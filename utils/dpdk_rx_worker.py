"""DPDK RX worker launcher + stdout-tail counter integration.

Solves the srv06 problem (Jun 12 2026): kernel netdev RX overflow at
24M pps. tx_worker pushes line-rate, the wire delivers, but the
kernel's `netdev_max_backlog` overflows in microseconds and Scapy's
AF_PACKET sniffer never sees a single packet.

DPDK RX captures frames straight off the PMD's RX queues — zero
kernel involvement, hardware-accurate, line-rate-capable. Works for
any TX source: Scapy, DPDK, external host. The PMD doesn't care.

Mirrors utils/dpdk_tx_worker.py's structure:
  - `_resolve_rx_worker_bin()` — same search order as tx
  - `start_rx_worker(...)` → returns RxHandle (process + line reader)
  - `stop_rx_worker(handle)` → SIGTERM + drain + final summary
  - Latest counters available via `handle.latest()` — non-blocking

Worker stdout is one JSON line per second + a `{"final":true,...}`
on exit. The launcher's background thread parses each line into the
handle's `_latest` dict. Callers read `handle.latest()` to surface
counters via the existing /api/streams/stats endpoint with no schema
change — DPDK RX just becomes another stats source.

For srv06 specifically: TX side stays as today (tx_worker on
ens2f0np0, vfio-bound). RX side gains rx_worker on ens2f1np1 —
which on Mellanox can run in BIFURCATED mode (kernel netdev stays
present + DPDK PMD attaches simultaneously). On Intel/Broadcom the
RX iface must be vfio-bound; tcpdump won't work on it while netgen
has it, same trade-off DPDK TX makes today.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

LOG = logging.getLogger(__name__)

_LATEST_LOCK = threading.Lock()


def _resolve_rx_worker_bin() -> Optional[str]:
    """Same priority chain as _resolve_tx_worker_bin (see
    utils/dpdk_tx_worker.py): env override → /usr/local/bin →
    install dirs → wheel-shipped. Returns None if nothing found —
    callers should fall back to Scapy with an explanatory warning."""
    p = os.environ.get("RX_WORKER_BIN")
    if p and os.path.exists(p):
        LOG.debug("[dpdk-rx] RX_WORKER_BIN=%s", p)
        return os.path.abspath(p)

    cand = "/usr/local/bin/rx_worker"
    if os.path.exists(cand):
        LOG.debug("[dpdk-rx] using /usr/local/bin/rx_worker")
        return cand

    for install_dir in ("/opt/netgen", "/opt/OSTG", "/opt/netgen-server"):
        cand = os.path.join(install_dir, "resources", "dpdk",
                            "rx_worker", "build", "rx_worker")
        if os.path.exists(cand):
            LOG.debug("[dpdk-rx] using install-dir rx_worker: %s", cand)
            return os.path.abspath(cand)

    # Wheel-shipped fallback — likely stale ABI but better than
    # nothing on a CI host or fresh install before install_dpdk.sh.
    try:
        from importlib.resources import files as _res_files
        rp = _res_files("resources.dpdk.rx_worker") / "build" / "rx_worker"
        if rp and os.path.exists(rp.as_posix()):
            LOG.debug("[dpdk-rx] wheel fallback %s (may have stale ABI)",
                      rp.as_posix())
            return rp.as_posix()
    except Exception:
        pass

    LOG.debug("[dpdk-rx] no rx_worker binary found anywhere")
    return None


@dataclass
class RxHandle:
    """Live handle to a running rx_worker child."""
    stream_id: str
    proc: subprocess.Popen
    cmd: list[str]
    started_at: float
    # v0.5.255 (audit RX-3): track the process-group id + the
    # systemd scope unit so Stop / is_running can reach the REAL
    # worker even when systemd-run --no-block has reparented it
    # into a scope under PID 1. Pre-fix, `proc.pid` was the
    # systemd-run wrapper's PID — which exits within milliseconds
    # of registering the scope — so SIGTERM to it was a no-op,
    # `is_running()` returned False, and the real rx_worker kept
    # running as an untracked orphan.
    pgid: Optional[int] = None
    unit: Optional[str] = None  # sanitised systemd scope unit name
    # Updated by the stdout-reader thread on every heartbeat line.
    # Always read via .latest() (which copies under the lock).
    _latest: dict = field(default_factory=dict)
    _final: Optional[dict] = None
    _reader: Optional[threading.Thread] = None
    # v0.5.118: rolling tail of stderr lines from the worker. Read
    # via .stderr_tail(). Bounded to STDERR_TAIL_LINES; older
    # lines drop off. The drainer thread is essential — without
    # it, a worker that emits more than ~64 KB of EAL spew blocks
    # on write to its stderr pipe and deadlocks. Even when the
    # pipe doesn't fill, the lines are what tells us WHY rx_worker
    # died (rte_eth_dev_configure failure, mempool exhaustion,
    # hugepage allocation error, etc.) — pre-v0.5.118 these died
    # invisibly into a pipe that nothing read.
    _stderr_lines: list = field(default_factory=list)
    _stderr_reader: Optional[threading.Thread] = None

    def latest(self) -> dict:
        """Non-blocking snapshot of the most recent counter heartbeat
        from the worker. Returns empty dict if no heartbeat yet."""
        with _LATEST_LOCK:
            return dict(self._latest)

    def final(self) -> Optional[dict]:
        """The `{"final":true,...}` line emitted on exit. None until
        the worker has stopped + emitted it."""
        with _LATEST_LOCK:
            return dict(self._final) if self._final else None

    def is_running(self) -> bool:
        """v0.5.255 (audit RX-3, cascade RX-6): check whether the
        REAL worker is alive, not the (already-exited) systemd-run
        wrapper. Priority: scope active → PID in scope alive →
        proc.poll() as last resort."""
        # 1. Preferred: ask systemd whether the scope is still up.
        # This is the ground truth when systemd-run is in use.
        if self.unit:
            try:
                r = subprocess.run(
                    ["systemctl", "is-active", "--quiet", f"{self.unit}.scope"],
                    capture_output=True, timeout=3,
                )
                if r.returncode == 0:
                    return True
                # rc=3 (inactive) — fall through to heartbeat check;
                # the scope may already be cleaned up while the real
                # worker is still draining its final counters.
            except Exception:
                pass
        # 2. Fresh heartbeat within the last 3 s means the worker
        # is still emitting → alive. Belt-and-braces for the case
        # where systemctl is missing or slow.
        with _LATEST_LOCK:
            hb = self._latest.get("ts") or self._latest.get("uptime_s")
        if hb is not None:
            try:
                if time.monotonic() - self.started_at < 3.0:
                    # Too early to trust the heartbeat gate.
                    pass
                else:
                    # Any heartbeat at all within recent memory is
                    # a positive signal — the reader thread only
                    # writes to _latest when the worker emits a line.
                    return self._final is None and (
                        self.proc.poll() is None or bool(self._latest)
                    )
            except Exception:
                pass
        # 3. Last resort — poll the (wrapper) PID. Correct when
        # systemd-run wasn't used (naked Popen fallback).
        return self.proc.poll() is None

    def stderr_tail(self, n: int = 50) -> list:
        """Last n lines of stderr from the worker. Useful when the
        worker died without emitting a final summary — the death
        cause typically lives in the last few stderr lines."""
        with _LATEST_LOCK:
            return list(self._stderr_lines[-n:])


# v0.5.118: stderr capture cap. The DPDK EAL spew at startup is
# bounded (~40 lines on a stock mlx5 init); 200 covers that plus
# any runtime warnings + a final death message.
STDERR_TAIL_LINES = 200


def _stderr_reader(handle: RxHandle) -> None:
    """v0.5.118: drain the worker's stderr pipe into a bounded
    in-memory ring so it (a) doesn't block on write when the pipe
    fills and (b) is queryable post-mortem via handle.stderr_tail().

    Pre-fix the rx_worker's stderr was captured to PIPE but never
    read. EAL spew (~40 lines on mlx5 init) fits in the 64 KB
    pipe so most workers stayed alive — but workers that emitted
    a stack-trace or repeated warnings deadlocked silently. And
    even when they didn't deadlock, post-mortem diagnostics
    required journalctl on the host because the captured stderr
    was discarded on process exit.
    """
    try:
        for raw in handle.proc.stderr:
            line = raw.rstrip("\n")
            if not line:
                continue
            with _LATEST_LOCK:
                handle._stderr_lines.append(line)
                if len(handle._stderr_lines) > STDERR_TAIL_LINES:
                    # Drop oldest lines — preserve the LAST N which
                    # is what diagnostics actually need.
                    del handle._stderr_lines[:-STDERR_TAIL_LINES]
            # Log at INFO so the operator can see it streaming in
            # netgen-server's journalctl without enabling debug.
            LOG.info("[dpdk-rx %s stderr] %s",
                     handle.stream_id, line)
    except Exception as exc:
        LOG.warning("[dpdk-rx %s] stderr reader crashed: %s",
                    handle.stream_id, exc)


def _stdout_reader(handle: RxHandle) -> None:
    """Tail the worker's stdout; parse one JSON line at a time and
    stash into the handle's _latest / _final fields. Runs in its own
    thread (daemon) until the worker exits.

    Resilient to non-JSON lines (rare; worker stderrs everything that
    isn't JSON, but EAL spew can still slip out on a bad day)."""
    try:
        for raw in handle.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            if line[0] != "{":
                LOG.debug("[dpdk-rx %s] non-json: %s",
                          handle.stream_id, line[:120])
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                LOG.debug("[dpdk-rx %s] parse: %s | line=%s",
                          handle.stream_id, exc, line[:120])
                continue
            with _LATEST_LOCK:
                if d.get("final"):
                    handle._final = d
                else:
                    handle._latest = d
    except Exception as exc:
        LOG.warning("[dpdk-rx %s] reader thread crashed: %s",
                    handle.stream_id, exc)


def start_rx_worker(
    *,
    stream_id: str,
    pci_bdf: str,
    lcores: str = "0,1,2,3",
    file_prefix: Optional[str] = None,
    rx_queues: int = 1,
    vlan: Optional[int] = None,
    dst_port: Optional[int] = None,
    src_port: Optional[int] = None,
    src_ip: Optional[str] = None,
    dst_ip: Optional[str] = None,
    duration_s: int = 0,
    extra_eal_args: Optional[list[str]] = None,
) -> RxHandle:
    """Spawn rx_worker as a subprocess capturing stdout. Returns a
    RxHandle the caller polls for counter updates.

    pci_bdf: the RX port's PCI BDF (e.g. 0000:2b:00.1). Required.
    lcores: comma-separated lcore list. Must include enough cores
        for main + rx_queues workers.
    file_prefix: DPDK multi-instance prefix. Defaults to
        f"rxw_{stream_id}". Mandatory if tx_worker is also running
        on the same host — different prefixes give different
        /var/run/dpdk dirs and avoid collisions.
    rx_queues: how many RX queues to set up. Multi-queue spreads
        work across cores via RSS for >40Gbps line rates.
    vlan / dst_port / src_port / src_ip / dst_ip: optional filter
        spec — if any are set, only matched_pkts grows. rx_pkts
        always counts every frame the port receives (useful for
        validating "did the wire deliver?").
    duration_s: 0 = run until SIGTERM; >0 = self-stop after N sec.
    """
    binary = _resolve_rx_worker_bin()
    if not binary:
        raise FileNotFoundError(
            "rx_worker binary not found — re-run install_dpdk.sh, "
            "or wait for v0.5.105's netgen-upgrade to rebuild it"
        )
    if not pci_bdf:
        raise ValueError("pci_bdf is required for DPDK RX")

    # v0.5.255 (audit RX-4): coerce port 0 → None so `--dst-port 0`
    # / `--src-port 0` never reaches the C worker. Pre-fix
    # rx_worker.c's `packet_matches` treated port=0 as "match only
    # port 0", so every real UDP frame was dropped from
    # `matched_pkts` while `rx_pkts` climbed — same v0.5.129
    # footgun the RX autoscaler already fixes via
    # `_port_filter_or_none`. Belt-and-braces here so BOTH entry
    # points (`_maybe_start_dpdk_rx_for_stream` AND the manual
    # `/api/admin/dpdk/rx/start` endpoint) are safe.
    if dst_port is not None and int(dst_port) <= 0:
        LOG.debug("[dpdk-rx] dropping dst_port=%s (0 = no-filter)", dst_port)
        dst_port = None
    if src_port is not None and int(src_port) <= 0:
        LOG.debug("[dpdk-rx] dropping src_port=%s (0 = no-filter)", src_port)
        src_port = None

    if file_prefix is None:
        # Same naming convention as tx_worker for log greppability.
        # v0.5.255 (audit RX-5): append a short random suffix so two
        # streams whose ids share the first 8 chars (or a fast
        # crash-and-relaunch inside the same millisecond) can never
        # collide on --file-prefix. Pre-fix the ms-timestamp was
        # the only uniqueness guarantee below the 8-char slice.
        import secrets as _secrets
        file_prefix = (
            f"rxw_{stream_id[:8]}_{os.getpid()}_"
            f"{int(time.time()*1000)}_{_secrets.token_hex(3)}"
        )

    eal = [binary,
           "-l", lcores,
           "-n", "4",
           "--file-prefix", file_prefix,
           "-a", pci_bdf]
    if extra_eal_args:
        eal.extend(extra_eal_args)

    app = ["--", "--stream-id", stream_id, "--rx-queues", str(rx_queues)]
    if vlan is not None:
        app.extend(["--vlan", str(int(vlan))])
    if dst_port is not None:
        app.extend(["--dst-port", str(int(dst_port))])
    if src_port is not None:
        app.extend(["--src-port", str(int(src_port))])
    if src_ip:
        app.extend(["--src-ip", str(src_ip)])
    if dst_ip:
        app.extend(["--dst-ip", str(dst_ip)])
    if duration_s > 0:
        app.extend(["--duration", str(int(duration_s))])

    cmd = eal + app
    # v0.5.169: wrap in systemd-run --scope for cgroup lifecycle
    # tracking (see utils/dpdk_tx_worker.py for the rationale).
    # v0.5.255 (audit RX-3): remember the unit name so stop_rx_worker
    # can `systemctl stop netgen-rx-<sid>.scope` — the ONLY
    # reliable way to kill the real worker when systemd-run
    # --no-block has reparented it under PID 1.
    _unit_name: Optional[str] = None
    try:
        from utils import systemd_scope
        cmd = systemd_scope.build_systemd_run_prefix(
            role="rx", stream_id=stream_id) + cmd
        if systemd_scope.has_systemd_run():
            _unit_name = systemd_scope.sanitise_unit_name("rx", stream_id)
    except Exception as _e:
        LOG.debug("[dpdk-rx] systemd_scope import failed: %s", _e)
    LOG.info("[dpdk-rx] exec: %s", " ".join(shlex.quote(a) for a in cmd))

    # v0.5.255 (audit RX-1 + RX-2): build a child env with the DPDK
    # library search path resolved + the mempool-ops default the
    # tx launcher has always set. Pre-fix, rx inherited only
    # `os.environ` and died with `librte_ethdev.so.XX` load errors
    # on rebuilt-DPDK hosts (srv06 after install_dpdk.sh). Same
    # helper the tx side uses since v0.5.253.
    child_env = os.environ.copy()
    try:
        from utils.dpdk_tx_worker import resolve_dpdk_ld_library_path
        _merged_ld = resolve_dpdk_ld_library_path(
            child_env.get("LD_LIBRARY_PATH", "")
        )
        if _merged_ld:
            child_env["LD_LIBRARY_PATH"] = _merged_ld
    except Exception as _e:
        LOG.debug("[dpdk-rx] LD_LIBRARY_PATH discovery skipped: %s", _e)
    child_env.setdefault("RTE_DISABLE_MEMPOOL_OPS", "1")

    # text=True so we get str lines, not bytes — matches tx_worker
    # pattern. bufsize=1 → line-buffered so we get heartbeats with
    # 1-second granularity, not after some big stdout buffer fills.
    # v0.5.255 (audit RX-3): start_new_session=True so we can
    # killpg() the group on the non-systemd fallback path.
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=child_env,
        start_new_session=True,
    )
    _pgid: Optional[int] = None
    try:
        _pgid = os.getpgid(proc.pid)
    except Exception:
        _pgid = None
    handle = RxHandle(
        stream_id=stream_id, proc=proc, cmd=cmd,
        started_at=time.monotonic(),
        pgid=_pgid, unit=_unit_name,
    )
    t = threading.Thread(
        target=_stdout_reader, args=(handle,),
        daemon=True, name=f"rx-reader-{stream_id[:8]}",
    )
    handle._reader = t
    t.start()
    # v0.5.118: drain stderr in parallel. Without this thread the
    # worker either deadlocks on a full pipe OR exits with stderr
    # lost into the void. With it, the lines are queryable
    # post-mortem via handle.stderr_tail() and live-streamed to
    # netgen-server's log so journalctl shows them.
    et = threading.Thread(
        target=_stderr_reader, args=(handle,),
        daemon=True, name=f"rx-errs-{stream_id[:8]}",
    )
    handle._stderr_reader = et
    et.start()
    return handle


def stop_rx_worker(handle: RxHandle, timeout_s: float = 5.0) -> dict:
    """Stop the worker cleanly. Returns the final summary dict (or
    empty if the worker died before emitting one).

    v0.5.255 (audit RX-3): reworked Stop sequence because
    ``systemd-run --scope --no-block`` reparents the real worker
    into a scope under PID 1 — ``handle.proc.pid`` was the
    (already-exited) systemd-run wrapper, so the pre-fix
    ``handle.proc.send_signal(SIGTERM)`` targeted a dead PID and
    the real rx_worker kept running as an orphan.

    New sequence:
      1. If we have a systemd scope name, ``systemctl stop
         netgen-rx-<sid>.scope`` — kernel-guaranteed and reaches
         the real worker regardless of PID reparenting.
      2. Else (no systemd, naked Popen fallback) ``killpg(pgid,
         SIGTERM)`` on the process group we created with
         ``start_new_session=True``.
      3. Wait up to ``timeout_s`` for the worker to drain +
         emit its ``{"final":true,...}`` line.
      4. SIGKILL escalation via the same channel as #1 or #2.
    """
    # 1. Preferred stop path — the systemd scope.
    stopped_via_scope = False
    if handle.unit:
        try:
            from utils import systemd_scope
            stopped_via_scope = systemd_scope.stop_scope_for_stream(
                role="rx", stream_id=handle.stream_id,
            )
            if stopped_via_scope:
                LOG.info(
                    "[dpdk-rx %s] stopped via systemctl stop %s.scope",
                    handle.stream_id, handle.unit,
                )
        except Exception as _e:
            LOG.debug(
                "[dpdk-rx %s] scope stop failed: %s", handle.stream_id, _e,
            )

    # 2. Fallback (or belt-and-braces): signal the process group.
    # killpg reaches the wrapper's session even after the wrapper
    # exited, and — importantly — reaches the real worker on the
    # non-systemd path where it inherits our session.
    if not stopped_via_scope and handle.proc.poll() is None:
        try:
            if handle.pgid is not None:
                os.killpg(handle.pgid, signal.SIGTERM)
            else:
                handle.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as _e:
            LOG.debug(
                "[dpdk-rx %s] killpg SIGTERM: %s", handle.stream_id, _e,
            )

    # 3. Wait for the worker to exit + drain its final line.
    try:
        handle.proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        LOG.warning(
            "[dpdk-rx %s] wrapper still alive after %.1fs — escalating "
            "to SIGKILL",
            handle.stream_id, timeout_s,
        )
        # 4. SIGKILL escalation.
        try:
            if handle.pgid is not None:
                os.killpg(handle.pgid, signal.SIGKILL)
            else:
                handle.proc.kill()
        except Exception:
            try:
                handle.proc.kill()
            except Exception:
                pass
        try:
            handle.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    # Reader thread holds onto stdout until proc closes the FD; join
    # briefly to let it parse the final line if any.
    if handle._reader and handle._reader.is_alive():
        handle._reader.join(timeout=2)

    return handle.final() or {}
