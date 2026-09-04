"""v0.5.168: detect + reap orphan tx_worker / rx_worker processes.

Operator hit this on srv06: a DPDK Blast stream was started in a
prior GUI session and the operator's GUI showed all three streams
as STOPPED, but on the server the tx_worker (897% CPU) and
rx_worker (798% CPU) were still alive — pinned to the same HCA
that the operator was trying to run an RDMA perftest against.
The orphan ate ~17 cores on NUMA 0 + competed for PCIe on the
target BDF, dropping the RDMA BW from 171 Gbps to 68.59 Gbps.

This module is pure-function — no Flask, no Qt, no requests. The
REST routes import + call these; the GUI consumes the routes via
the existing async helpers.

How orphans happen:
  * Stop call never reaches the worker (operator closed dialog,
    GUI lost track of stream_id, prior session disconnected).
  * Stop's backstop `pkill --stream-id <uuid>` matched 0 procs
    silently (cmdline encoding mismatch, race).
  * Launcher thread died early without running its finally-reap.
  * ostg-server restart while a stream was running — the worker
    process survives because it's an independent /usr/local/bin
    invocation; tracker is empty after restart.

Detection: walk /proc/*/cmdline, identify tx_worker/rx_worker by
the binary basename, parse `--stream-id <uuid>` + `-a <BDF>` +
`--file-prefix <prefix>` from the args, cross-reference the
stream_id against the active tracker. Anything not in the
tracker = orphan.

Reaper: SIGTERM, wait `term_wait_secs`, then SIGKILL anything
still alive. Idempotent — re-reaping a dead PID is a no-op.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


# v0.5.168: the binaries that may be left as orphans. Match on
# basename — operators occasionally rename or symlink, but the
# canonical /usr/local/bin installs from `make install` always
# resolve to these names.
_WORKER_BINS = ("tx_worker", "rx_worker")


# Pre-compiled cmdline parsers. The DPDK EAL args are positional
# but `--stream-id`, `-a`, and `--file-prefix` are always present
# in the netgen launcher's cmdline. See run_tgen_server.py around
# the `tx_worker` spawn (and the rx_worker mirror).
_RE_STREAM_ID = re.compile(
    # v0.5.253 (audit DPDK-7): accept any non-space token, not just
    # strict UUID form. Pre-fix, a tx_worker started with a non-UUID
    # --stream-id (integration test slug, hand-invoked debug run,
    # API-direct caller) had stream_id=None, and `find_orphans`
    # short-circuits `not w.stream_id` → the legitimate worker got
    # classified as an orphan and Stop-All SIGKILLed it.
    r"--stream[-_]id[\s=]+(\S+)",
)
_RE_PCI_BDF = re.compile(
    # Accept BOTH the canonical domain-prefixed form
    # `0000:2b:00.0` (what the netgen launcher passes) AND the
    # unpadded form `2b:00.0` (occasionally seen in hand-crafted
    # DPDK invocations). `_normalise_bdf` canonicalises both to
    # the padded form afterwards.
    r"(?:^|[\s])-a[\s=]+([0-9a-f]{2,4}(?::[0-9a-f]{2,4})?"
    r"[:.][0-9a-f]{2}\.[0-7])",
    re.IGNORECASE,
)
_RE_FILE_PREFIX = re.compile(
    r"--file[-_]prefix[\s=]+(\S+)"
)


@dataclass
class DpdkWorker:
    """One running tx_worker or rx_worker process.

    `stream_id` and `bdf` are pulled from the cmdline; either may
    be None if the operator launched a worker manually with a
    non-standard arg layout (rare). The `etime_seconds` is the
    process's elapsed wall-clock — useful when the operator wants
    to know "how long has this been eating my HCA"."""
    pid: int
    role: str                  # "tx" | "rx"
    stream_id: Optional[str]   # parsed from --stream-id
    bdf: Optional[str]         # parsed from -a (PCI BDF)
    file_prefix: Optional[str] # parsed from --file-prefix
    etime_seconds: Optional[int]
    cmdline: str               # full /proc/<pid>/cmdline (NULs → spaces)

    def to_dict(self) -> Dict:
        return asdict(self)


def find_dpdk_workers(
    *,
    proc_root: str = "/proc",
) -> List[DpdkWorker]:
    """Enumerate every live tx_worker / rx_worker process on this
    host. Returns [] when /proc isn't readable (containers without
    procfs, or non-Linux). `proc_root` is overridable for testing.

    Implementation note: we walk `/proc/<pid>/cmdline` rather than
    shelling out to `ps` because:
      * No subprocess cost — this gets polled every 5s by the GUI.
      * `ps` cmdline truncation differs across distros; /proc is
        the source of truth.
      * No PATH dependency.
    """
    if not os.path.isdir(proc_root):
        return []
    workers: List[DpdkWorker] = []
    try:
        entries = os.listdir(proc_root)
    except OSError as exc:
        logger.debug(f"[orphans] cannot listdir {proc_root}: {exc}")
        return []
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        cmdline_raw = _read_cmdline(proc_root, pid)
        if not cmdline_raw:
            continue
        role = _classify_worker(cmdline_raw)
        if not role:
            continue
        cmdline = cmdline_raw.replace("\x00", " ").strip()
        workers.append(DpdkWorker(
            pid=pid,
            role=role,
            stream_id=_extract_first(_RE_STREAM_ID, cmdline),
            bdf=_normalise_bdf(_extract_first(_RE_PCI_BDF, cmdline)),
            file_prefix=_extract_first(_RE_FILE_PREFIX, cmdline),
            etime_seconds=_read_etime_seconds(proc_root, pid),
            cmdline=cmdline,
        ))
    workers.sort(key=lambda w: (w.bdf or "", w.role, w.pid))
    return workers


def find_orphans(
    known_stream_ids: Iterable[str],
    *,
    proc_root: str = "/proc",
) -> List[DpdkWorker]:
    """Return workers whose stream_id is NOT in the active tracker.

    Workers without a parseable stream_id are conservatively
    treated as orphans — if the launcher didn't tag them with a
    UUID, the tracker can't possibly know about them, so they're
    by definition untracked. The operator can choose whether to
    reap them via the confirm dialog."""
    known = {s for s in known_stream_ids if s}
    return [w for w in find_dpdk_workers(proc_root=proc_root)
            if not w.stream_id or w.stream_id not in known]


def find_orphans_for_bdf(
    bdf: str,
    known_stream_ids: Iterable[str],
    *,
    proc_root: str = "/proc",
) -> List[DpdkWorker]:
    """Pre-flight collision check — return orphan workers bound to
    the given PCI BDF. Used by the Start dialog to refuse a start
    against a device that's already being hammered by an orphan.

    BDF comparison is case-insensitive; padded form (`0000:2b:00.0`)
    is the only form the kernel emits."""
    target = _normalise_bdf(bdf)
    if not target:
        return []
    return [w for w in find_orphans(known_stream_ids, proc_root=proc_root)
            if w.bdf and w.bdf.lower() == target.lower()]


def reap_workers(
    pids: Iterable[int],
    *,
    term_wait_secs: float = 1.0,
) -> Dict[str, List[int]]:
    """SIGTERM the given PIDs, wait `term_wait_secs`, then SIGKILL
    anything still alive. Returns `{terminated, killed, failed}`
    so the caller can report a precise outcome.

    Idempotent — already-dead PIDs land in `terminated` with no
    error. `failed` only contains PIDs we couldn't signal at all
    (EPERM or other OSError). The escalation to SIGKILL after
    `term_wait_secs` is non-negotiable: DPDK workers are tight
    poll loops in C, they don't handle SIGTERM gracefully in all
    versions, and leaving them alive defeats the whole feature.
    """
    pids = sorted(set(int(p) for p in pids))
    terminated: List[int] = []
    failed: List[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append(pid)
        except ProcessLookupError:
            terminated.append(pid)  # Already dead — counts as success.
        except OSError as exc:
            logger.warning(f"[orphans] SIGTERM pid={pid} failed: {exc}")
            failed.append(pid)
    if not terminated:
        return {"terminated": [], "killed": [], "failed": failed}
    # Give SIGTERM a brief window. Anything still alive gets KILL.
    if term_wait_secs > 0:
        time.sleep(term_wait_secs)
    killed: List[int] = []
    for pid in terminated:
        if _proc_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass  # Died between the check and the kill — fine.
            except OSError as exc:
                logger.warning(f"[orphans] SIGKILL pid={pid} failed: {exc}")
                failed.append(pid)
    return {
        "terminated": [p for p in terminated if p not in failed],
        "killed": killed,
        "failed": failed,
    }


# ───── internals ─────────────────────────────────────────────────────


def _read_cmdline(proc_root: str, pid: int) -> Optional[str]:
    path = os.path.join(proc_root, str(pid), "cmdline")
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return None


def _read_etime_seconds(proc_root: str, pid: int) -> Optional[int]:
    """Parse `/proc/<pid>/stat` for the process start time + read
    `/proc/uptime` to compute wall-clock etime. Pure /proc — no
    subprocess. Returns None when sysfs is unavailable (containers,
    non-Linux test runs).

    The 22nd field of `/proc/<pid>/stat` is starttime in clock
    ticks since boot. (man 5 proc -> `(22) starttime`.) The field
    after `comm` may contain spaces — split on the LAST `)` first
    to skip the comm field, then split the remainder."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), "r") as fh:
            stat = fh.read()
        with open(os.path.join(proc_root, "uptime"), "r") as fh:
            uptime_s = float(fh.read().split()[0])
    except (OSError, ValueError):
        return None
    rparen = stat.rfind(")")
    if rparen < 0:
        return None
    fields = stat[rparen + 1:].split()
    # Field 22 (starttime) is at index 22 - 3 = 19 after the comm
    # field (fields 1-2 are pid + comm, stripped above).
    try:
        starttime_ticks = int(fields[19])
    except (IndexError, ValueError):
        return None
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    if hz <= 0:
        hz = 100
    started_at = starttime_ticks / hz
    etime = uptime_s - started_at
    return max(0, int(etime))


def _classify_worker(cmdline: str) -> Optional[str]:
    """Return 'tx' / 'rx' if the cmdline launched a tx_worker /
    rx_worker, else None. Matches against the binary basename in
    the first arg (argv[0]) which is NUL-separated from the rest.
    """
    first_arg = cmdline.split("\x00", 1)[0]
    base = os.path.basename(first_arg)
    if base == "tx_worker":
        return "tx"
    if base == "rx_worker":
        return "rx"
    return None


def _extract_first(pattern: re.Pattern, s: str) -> Optional[str]:
    m = pattern.search(s)
    return m.group(1) if m else None


def _normalise_bdf(bdf: Optional[str]) -> Optional[str]:
    """Canonical form: lowercase, full domain. /sys emits this
    form; some DPDK invocations omit the domain (`2b:00.0`)."""
    if not bdf:
        return None
    bdf = bdf.lower().strip()
    if re.fullmatch(r"[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", bdf):
        return f"0000:{bdf}"
    return bdf


def _proc_alive(pid: int) -> bool:
    """signal 0 = existence check; raises OSError if the process
    is gone (or if we lack permission, which we treat as alive
    since we can't tell)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
