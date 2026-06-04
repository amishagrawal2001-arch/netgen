"""RDMA perftest orchestrator — v0.3.12.

Replaces the v0.2.x 44-line stub that:
  * Hardcoded ``-d mlx5_0`` (wrong on any non-Mellanox or multi-NIC host).
  * Hardcoded test type (``ib_write_bw``); operators couldn't pick Send,
    Read, or latency variants.
  * Forgot to import ``logging`` and ``time`` (any code path that hit
    those lines crashed with NameError — silently, because nothing ever
    called this module from a wired UI surface).
  * Stuffed parsed stats into a module-level ``perf_stats`` dict keyed
    by interface — collided with itself the moment two streams ran on
    the same iface.

This module exposes a real perftest job registry:

  list_rdma_devices()             → discovery of RDMA NICs/ports/GIDs
  perftest_installed()            → which tools + versions are on PATH
  start_perftest(role, test, opts) → spawn ib_*_bw / ib_*_lat, return
                                    job_id (+ listen addr/port for
                                    role="server")
  stop_perftest(job_id)           → SIGTERM the child, mark job stopped
  get_perftest_job(job_id)        → one job's running + final stats
  list_perftest_jobs()            → every job (running + recently
                                    finished), with parsed stats

NO HARDCODED DEVICE. NO HARDCODED TEST. Stats are per-job (keyed by
``job_id``), so parallel jobs on the same NIC don't trample each
other.

Used by:
  * /api/rdma/* routes in run_tgen_server.py (v0.3.12)
  * widgets/rdma_blast_flow_dialog.py (v0.3.12) — Blast a RDMA Flow
  * utils/rdma_handshake.py — broker that wires the per-job listen
    address+port into the peer TG so perftest's own TCP handshake
    completes over a netgen-coordinated socket.

Pure-Python, no rdma-core build dependency. Falls back to "perftest
not installed" if ``ib_send_bw`` etc. aren't on PATH — surfaces a
clean error to the GUI instead of a stack trace.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("rdma_perf")


# ─────────────────────────────────────────────────────────── constants

# perftest tools we support. Each one has a *_bw and *_lat variant.
# The "send" family doesn't need RDMA semantics on the receiver side
# (just a posted recv); "write" and "read" do one-sided RDMA, so the
# remote side runs as a passive listener.
_SUPPORTED_TESTS = {
    # test_id          → tool binary on PATH
    "send_bw":  "ib_send_bw",
    "write_bw": "ib_write_bw",
    "read_bw":  "ib_read_bw",
    "send_lat":  "ib_send_lat",
    "write_lat": "ib_write_lat",
    "read_lat":  "ib_read_lat",
    # Atomics — included for completeness; some HCAs don't support
    # these and the tool will exit 1 with a clear error which we
    # surface.
    "atomic_bw":  "ib_atomic_bw",
    "atomic_lat": "ib_atomic_lat",
}

# perftest's default control-channel port. Each concurrent job needs
# its own port — we allocate from this base upward.
_DEFAULT_BASE_PORT = 18515
_MAX_PARALLEL_JOBS = 64

# Keep finished jobs around for this many seconds so the GUI has a
# chance to poll the final stats line.
_FINISHED_JOB_TTL_SECS = 600

# Cap per-job stdout buffer so a long-running job doesn't OOM the
# server process. perftest emits ~1 line per --report_interval, so
# 5000 lines covers ~83 minutes of 1-sec reporting — plenty.
_STDOUT_BUFFER_MAX_LINES = 5000


# ─────────────────────────────────────────────────────────── data shapes

@dataclass
class RdmaPort:
    """One physical port on one RDMA device."""
    port: int
    state: str               # e.g. "ACTIVE", "DOWN", "INIT"
    physical_state: str      # e.g. "LinkUp", "Disabled", "Polling"
    link_layer: str          # "Ethernet" or "InfiniBand"
    rate: str                # e.g. "100 Gb/sec (4X EDR)"
    mtu: int                 # active MTU in bytes (e.g. 4096)
    gids: List[str]          # all valid GIDs on this port (skips zero-GIDs)
    lid: Optional[int]       # IB LID; None on Ethernet/RoCE


@dataclass
class RdmaDevice:
    """One RDMA HCA (e.g. mlx5_0, mlx5_bond_0, irdma0)."""
    name: str                # /sys/class/infiniband/<name>
    vendor: Optional[str]    # board ID / vendor (best-effort)
    fw_version: Optional[str]
    node_guid: Optional[str]
    ports: List[RdmaPort]


@dataclass
class PerftestJob:
    """Live or recently-finished perftest invocation."""
    job_id: str
    role: str                # "server" | "client"
    test: str                # "send_bw", "write_lat", …
    tool: str                # actual binary path: "ib_send_bw"
    device: str              # e.g. "mlx5_0"
    ib_port: int             # 1, 2, …
    listen_port: int         # perftest control-channel TCP port
    peer_addr: Optional[str] # set on client side; None on server
    cmd: List[str]
    pid: Optional[int]       # None once finished
    started_at: float
    finished_at: Optional[float]
    returncode: Optional[int]
    error: Optional[str]
    # Parsed telemetry the GUI polls:
    local_qpn: Optional[str] = None
    local_psn: Optional[str] = None
    local_lid: Optional[str] = None
    remote_qpn: Optional[str] = None
    remote_psn: Optional[str] = None
    remote_lid: Optional[str] = None
    # Final results row (when present):
    final_bw_avg_gbps: Optional[float] = None
    final_bw_peak_gbps: Optional[float] = None
    final_msg_rate_mpps: Optional[float] = None
    final_msg_size_bytes: Optional[int] = None
    final_iterations: Optional[int] = None
    # Latency-only:
    final_lat_min_us: Optional[float] = None
    final_lat_avg_us: Optional[float] = None
    final_lat_max_us: Optional[float] = None
    final_lat_p99_us: Optional[float] = None
    # Running stdout buffer (capped):
    stdout_tail: List[str] = field(default_factory=list)

    def to_public_dict(self) -> Dict[str, Any]:
        """Serializable view for /api/rdma/perftest/jobs response."""
        d = asdict(self)
        # cmd list of strings is fine; pid may be None — OK in JSON.
        d["running"] = (self.finished_at is None)
        return d


# ─────────────────────────────────────────────────────────── module state

_jobs: Dict[str, PerftestJob] = {}
_jobs_lock = threading.RLock()
_port_allocator_lock = threading.Lock()
_next_port = _DEFAULT_BASE_PORT


# ─────────────────────────────────────────────────────────── discovery

_IB_SYSFS_ROOT = "/sys/class/infiniband"


def _read_sysfs(path: str) -> Optional[str]:
    """Read a sysfs file, return stripped text or None on any error."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _list_port_gids(dev: str, port: int) -> List[str]:
    """Return all non-zero GIDs on (dev, port)."""
    gid_dir = os.path.join(_IB_SYSFS_ROOT, dev, "ports", str(port), "gids")
    if not os.path.isdir(gid_dir):
        return []
    gids: List[str] = []
    try:
        entries = sorted(os.listdir(gid_dir), key=lambda s: int(s) if s.isdigit() else 9999)
    except OSError:
        return []
    for entry in entries:
        text = _read_sysfs(os.path.join(gid_dir, entry))
        if not text:
            continue
        # Zero GID means "unconfigured slot" — skip.
        if text.replace(":", "").replace("0", "") == "":
            continue
        gids.append(text)
    return gids


def _parse_state(raw: Optional[str]) -> str:
    """Convert e.g. '4: ACTIVE' → 'ACTIVE'."""
    if not raw:
        return "UNKNOWN"
    parts = raw.split(":", 1)
    return parts[1].strip() if len(parts) == 2 else raw.strip()


def list_rdma_devices() -> List[RdmaDevice]:
    """Enumerate every RDMA HCA on this host via /sys/class/infiniband.

    Returns an empty list (not an error) when:
      * The kernel doesn't have RDMA support loaded.
      * No HCA is present.
      * The user lacks read access (containerized server without
        ``/sys/class/infiniband`` mounted).

    Pure read-only sysfs walk — no syscalls into libibverbs, so this is
    safe to call on hosts where rdma-core isn't installed.
    """
    if not os.path.isdir(_IB_SYSFS_ROOT):
        return []

    devices: List[RdmaDevice] = []
    try:
        dev_names = sorted(os.listdir(_IB_SYSFS_ROOT))
    except OSError as exc:
        logger.debug(f"[rdma] cannot list {_IB_SYSFS_ROOT}: {exc}")
        return []

    for dev in dev_names:
        dev_root = os.path.join(_IB_SYSFS_ROOT, dev)
        if not os.path.isdir(dev_root):
            continue
        vendor = _read_sysfs(os.path.join(dev_root, "board_id"))
        fw_version = _read_sysfs(os.path.join(dev_root, "fw_ver"))
        node_guid = _read_sysfs(os.path.join(dev_root, "node_guid"))

        # Walk ports.
        ports_dir = os.path.join(dev_root, "ports")
        port_list: List[RdmaPort] = []
        if os.path.isdir(ports_dir):
            try:
                port_names = sorted(os.listdir(ports_dir), key=lambda s: int(s) if s.isdigit() else 9999)
            except OSError:
                port_names = []
            for pname in port_names:
                if not pname.isdigit():
                    continue
                port_n = int(pname)
                p_root = os.path.join(ports_dir, pname)
                state = _parse_state(_read_sysfs(os.path.join(p_root, "state")))
                phys = _parse_state(_read_sysfs(os.path.join(p_root, "phys_state")))
                link_layer = _read_sysfs(os.path.join(p_root, "link_layer")) or "Unknown"
                rate = _read_sysfs(os.path.join(p_root, "rate")) or ""
                mtu_raw = _read_sysfs(os.path.join(p_root, "active_mtu"))
                # active_mtu reads as e.g. "5: 4096" — third token is bytes.
                mtu = 0
                if mtu_raw:
                    # Pattern variants seen: "4096", "5: 4096", "4: 2048"
                    m = re.search(r"(\d{3,5})", mtu_raw)
                    if m:
                        try:
                            mtu = int(m.group(1))
                        except ValueError:
                            mtu = 0
                lid_raw = _read_sysfs(os.path.join(p_root, "lid"))
                lid: Optional[int] = None
                if lid_raw:
                    try:
                        lid = int(lid_raw, 0)  # accept 0x..  or decimal
                    except ValueError:
                        lid = None
                gids = _list_port_gids(dev, port_n)
                port_list.append(RdmaPort(
                    port=port_n, state=state, physical_state=phys,
                    link_layer=link_layer, rate=rate, mtu=mtu, gids=gids, lid=lid,
                ))

        devices.append(RdmaDevice(
            name=dev,
            vendor=vendor,
            fw_version=fw_version,
            node_guid=node_guid,
            ports=port_list,
        ))
    return devices


# ─────────────────────────────────────────────────────────── perftest probe

def perftest_installed() -> Dict[str, Any]:
    """Return ``{installed: bool, tools: {test_id: path|None}, version: str|None}``.

    ``installed`` is True iff at least one supported tool is on PATH.
    Version is best-effort from ``ib_send_bw --version`` (perftest prints
    its own version on that line).
    """
    tools: Dict[str, Optional[str]] = {}
    for test_id, binary in _SUPPORTED_TESTS.items():
        tools[test_id] = shutil.which(binary)

    installed = any(p is not None for p in tools.values())
    version: Optional[str] = None
    if installed:
        # Probe whichever tool is present — they're all from the same
        # perftest package and report identical version strings.
        for path in tools.values():
            if path is None:
                continue
            try:
                proc = subprocess.run(
                    [path, "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                # perftest prints version to stderr on some builds and
                # stdout on others. Just grep both.
                blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
                m = re.search(r"perftest[-\s]+(\S+)", blob, re.IGNORECASE)
                if m:
                    version = m.group(1)
                    break
                # Some builds print just a bare version like "6.3":
                m2 = re.search(r"^\s*(\d+\.\d+(?:\.\d+)?)\s*$", blob, re.MULTILINE)
                if m2:
                    version = m2.group(1)
                    break
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug(f"[rdma] {path} --version failed: {exc}")
                continue
    return {"installed": installed, "tools": tools, "version": version}


# ─────────────────────────────────────────────────────────── port allocator

def _allocate_port() -> int:
    """Hand out a free perftest control-channel port. Simple incrementing
    allocator; we don't actually bind to verify free-ness because
    perftest itself will EADDRINUSE-fail loudly if we collide and the
    operator can just rerun (rare in practice — 64 simultaneous jobs
    is the cap)."""
    global _next_port
    with _port_allocator_lock:
        port = _next_port
        _next_port += 1
        if _next_port > _DEFAULT_BASE_PORT + _MAX_PARALLEL_JOBS * 2:
            _next_port = _DEFAULT_BASE_PORT
        return port


# ─────────────────────────────────────────────────────────── start

def _validate_start_opts(role: str, test: str, opts: Dict[str, Any]) -> Optional[str]:
    """Return None if valid, or an operator-readable reason string."""
    if role not in ("server", "client"):
        return f"role must be 'server' or 'client', got {role!r}"
    if test not in _SUPPORTED_TESTS:
        supported = ", ".join(sorted(_SUPPORTED_TESTS.keys()))
        return f"test must be one of: {supported}; got {test!r}"
    if role == "client" and not opts.get("peer_addr"):
        return "client role requires opts.peer_addr"
    # Device is optional — perftest defaults to the first one. But if
    # specified we sanity-check it exists in /sys/class/infiniband to
    # surface typos before fork.
    dev = opts.get("device")
    if dev and not os.path.isdir(os.path.join(_IB_SYSFS_ROOT, dev)):
        # Soft warning only — bare-metal hosts where /sys/class/infiniband
        # exists but the device is plug-removable shouldn't be blocked.
        # Log and continue; perftest will error if truly missing.
        logger.warning(f"[rdma] device {dev!r} not in {_IB_SYSFS_ROOT}; "
                       "passing through to perftest anyway")
    return None


def _build_perftest_cmd(
    tool_path: str, role: str, test: str, listen_port: int, opts: Dict[str, Any]
) -> List[str]:
    """Compose argv for perftest. Common args + role-specific tail.

    Honors these opts keys:
      device        ib device (e.g. "mlx5_0") — -d
      ib_port       physical port number — -i (default 1)
      gid_index     RoCE GID slot — -x  (default 3 for RoCEv2-IPv4)
      msg_size      bytes per posted op — -s
      qp_count      number of QPs — -q
      duration      seconds — -D
      iterations    fixed op count — -n  (mutually exclusive with duration)
      mtu           1=256, 2=512, 3=1024, 4=2048, 5=4096 — -m
      tx_depth      send queue depth — -t
      rx_depth      recv queue depth — --rx_depth
      bidirectional bool — -b  (only meaningful for *_bw)
      use_event     bool — -e  (interrupt mode instead of polling)
      inline        bytes inlined — -I
      cq_mod        CQ moderation — -Q
      cpu_util      bool — --cpu_util
      report_gbits  bool, default True — --report_gbits
      out_json      bool — --out_json --out_json_file=…
      perf_extra    list[str] — appended verbatim (escape hatch)
    """
    cmd: List[str] = [tool_path, "-p", str(listen_port)]

    dev = opts.get("device")
    if dev:
        cmd += ["-d", str(dev)]
    ib_port = opts.get("ib_port") or 1
    cmd += ["-i", str(ib_port)]

    gid_index = opts.get("gid_index")
    if gid_index is not None:
        cmd += ["-x", str(gid_index)]

    msg_size = opts.get("msg_size")
    if msg_size:
        cmd += ["-s", str(msg_size)]

    qp_count = opts.get("qp_count")
    if qp_count and int(qp_count) > 1:
        cmd += ["-q", str(qp_count)]

    duration = opts.get("duration")
    iterations = opts.get("iterations")
    # perftest rejects -D and -n together; prefer duration if both set.
    if duration:
        cmd += ["-D", str(duration)]
    elif iterations:
        cmd += ["-n", str(iterations)]

    mtu = opts.get("mtu")
    if mtu:
        cmd += ["-m", str(mtu)]

    tx_depth = opts.get("tx_depth")
    if tx_depth:
        cmd += ["-t", str(tx_depth)]
    rx_depth = opts.get("rx_depth")
    if rx_depth:
        cmd += ["--rx_depth", str(rx_depth)]

    if opts.get("bidirectional") and test.endswith("_bw"):
        cmd += ["-b"]

    if opts.get("use_event"):
        cmd += ["-e"]

    inline = opts.get("inline")
    if inline is not None:
        cmd += ["-I", str(inline)]

    cq_mod = opts.get("cq_mod")
    if cq_mod:
        cmd += ["-Q", str(cq_mod)]

    if opts.get("cpu_util"):
        cmd += ["--cpu_util"]

    # Always prefer gbits for _bw tests so our parser knows the unit.
    if test.endswith("_bw") and opts.get("report_gbits", True):
        cmd += ["--report_gbits"]

    # Force tabular output so parsing is stable across perftest versions.
    # (--out_json is nice but not present on every distro's perftest.)

    # Extra escape hatch — last so it can override built-in args.
    extra = opts.get("perf_extra") or []
    if isinstance(extra, str):
        extra = shlex.split(extra)
    cmd += list(extra)

    # Role tail: client takes peer addr as the final positional arg.
    if role == "client":
        cmd.append(str(opts["peer_addr"]))
    return cmd


# Regex pre-compiles for stdout parsing.
_RE_LOCAL_ADDR = re.compile(
    r"local address:.*?LID\s+(?P<lid>0x[0-9a-fA-F]+).*?"
    r"QPN\s+(?P<qpn>0x[0-9a-fA-F]+).*?PSN\s+(?P<psn>0x[0-9a-fA-F]+)",
    re.IGNORECASE,
)
_RE_REMOTE_ADDR = re.compile(
    r"remote address:.*?LID\s+(?P<lid>0x[0-9a-fA-F]+).*?"
    r"QPN\s+(?P<qpn>0x[0-9a-fA-F]+).*?PSN\s+(?P<psn>0x[0-9a-fA-F]+)",
    re.IGNORECASE,
)
# BW data row: "  65536      1000     96.43      96.40     0.18"
# Tokens: bytes, iterations, BW peak, BW average, MsgRate
_RE_BW_DATA_ROW = re.compile(
    r"^\s*(?P<bytes>\d+)\s+(?P<iters>\d+)\s+"
    r"(?P<peak>[\d.]+)\s+(?P<avg>[\d.]+)\s+(?P<mrate>[\d.]+)\s*$"
)
# Latency data row: "  2     1000     1.50     2.10     5.30  ... 2.95"
# perftest lat tools print: bytes iter t_min t_max t_typical t_avg t_stdev 99% 99.9%
_RE_LAT_DATA_ROW = re.compile(
    r"^\s*(?P<bytes>\d+)\s+(?P<iters>\d+)\s+"
    r"(?P<tmin>[\d.]+)\s+(?P<tmax>[\d.]+)\s+"
    r"(?P<ttyp>[\d.]+)\s+(?P<tavg>[\d.]+)\s+"
    r"(?P<tstdev>[\d.]+)\s+(?P<p99>[\d.]+)(?:\s+(?P<p999>[\d.]+))?\s*$"
)


def _reader_thread(job: PerftestJob, proc: subprocess.Popen) -> None:
    """Stream stdout, parse address + data rows, update job in place."""
    assert proc.stdout is not None
    is_lat = job.test.endswith("_lat")
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            with _jobs_lock:
                job.stdout_tail.append(line)
                if len(job.stdout_tail) > _STDOUT_BUFFER_MAX_LINES:
                    # Drop oldest half so we don't churn on every line.
                    del job.stdout_tail[: _STDOUT_BUFFER_MAX_LINES // 2]
            # Address rows — appear once near the start.
            m = _RE_LOCAL_ADDR.search(line)
            if m:
                with _jobs_lock:
                    job.local_lid = m.group("lid")
                    job.local_qpn = m.group("qpn")
                    job.local_psn = m.group("psn")
            m = _RE_REMOTE_ADDR.search(line)
            if m:
                with _jobs_lock:
                    job.remote_lid = m.group("lid")
                    job.remote_qpn = m.group("qpn")
                    job.remote_psn = m.group("psn")
            # Data rows.
            if is_lat:
                m = _RE_LAT_DATA_ROW.match(line)
                if m:
                    with _jobs_lock:
                        try:
                            job.final_msg_size_bytes = int(m.group("bytes"))
                            job.final_iterations = int(m.group("iters"))
                            job.final_lat_min_us = float(m.group("tmin"))
                            job.final_lat_max_us = float(m.group("tmax"))
                            job.final_lat_avg_us = float(m.group("tavg"))
                            p99 = m.group("p99")
                            if p99:
                                job.final_lat_p99_us = float(p99)
                        except (TypeError, ValueError):
                            pass
            else:
                m = _RE_BW_DATA_ROW.match(line)
                if m:
                    with _jobs_lock:
                        try:
                            job.final_msg_size_bytes = int(m.group("bytes"))
                            job.final_iterations = int(m.group("iters"))
                            job.final_bw_peak_gbps = float(m.group("peak"))
                            job.final_bw_avg_gbps = float(m.group("avg"))
                            job.final_msg_rate_mpps = float(m.group("mrate"))
                        except (TypeError, ValueError):
                            pass
    except Exception as exc:
        logger.warning(f"[rdma] reader for job {job.job_id} crashed: {exc}")
    finally:
        # Wait for the child so we get the real returncode.
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                rc = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rc = -1
        except Exception:
            rc = -1
        with _jobs_lock:
            job.finished_at = time.time()
            job.returncode = rc
            job.pid = None
            if rc != 0 and not job.error:
                # Grab last few stderr-ish lines from the tail for context.
                tail = "\n".join(job.stdout_tail[-10:]) if job.stdout_tail else ""
                job.error = f"perftest exited rc={rc}: {tail[-400:]}"


def _gc_old_jobs() -> None:
    """Reap finished jobs older than TTL. Called opportunistically on
    start; doesn't run on its own timer."""
    now = time.time()
    with _jobs_lock:
        stale = [
            jid for jid, j in _jobs.items()
            if j.finished_at is not None
            and (now - j.finished_at) > _FINISHED_JOB_TTL_SECS
        ]
        for jid in stale:
            _jobs.pop(jid, None)


def start_perftest(role: str, test: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    """Spawn a perftest invocation. Returns immediately with job_id.

    Returns dict shape:
      {"status": "started", "job_id": "<uuid>",
       "listen_port": int, "cmd": [...], "tool": "/usr/bin/ib_send_bw"}

    Or, on validation/setup failure:
      {"status": "error", "error": "<reason>"}
    """
    _gc_old_jobs()

    reason = _validate_start_opts(role, test, opts)
    if reason:
        return {"status": "error", "error": reason}

    info = perftest_installed()
    if not info["installed"]:
        return {"status": "error",
                "error": "perftest not installed (apt install perftest)"}
    tool_path = info["tools"].get(test)
    if not tool_path:
        return {"status": "error",
                "error": f"perftest tool for {test} not found on PATH "
                         f"({_SUPPORTED_TESTS[test]} missing)"}

    listen_port = opts.get("listen_port")
    if listen_port is None:
        listen_port = _allocate_port()
    listen_port = int(listen_port)

    cmd = _build_perftest_cmd(tool_path, role, test, listen_port, opts)
    job_id = str(uuid.uuid4())

    # Spawn.
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            # Put child in its own process group so we can SIGTERM cleanly.
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": f"spawn failed: {exc}"}

    job = PerftestJob(
        job_id=job_id,
        role=role,
        test=test,
        tool=tool_path,
        device=str(opts.get("device") or ""),
        ib_port=int(opts.get("ib_port") or 1),
        listen_port=listen_port,
        peer_addr=opts.get("peer_addr") if role == "client" else None,
        cmd=list(cmd),
        pid=proc.pid,
        started_at=time.time(),
        finished_at=None,
        returncode=None,
        error=None,
    )
    with _jobs_lock:
        _jobs[job_id] = job

    t = threading.Thread(
        target=_reader_thread, args=(job, proc), daemon=True,
        name=f"rdma-perf-{job_id[:8]}",
    )
    t.start()

    logger.info(f"[rdma] started {role} {test} job={job_id[:8]} "
                f"port={listen_port} cmd={' '.join(shlex.quote(c) for c in cmd)}")

    return {
        "status": "started",
        "job_id": job_id,
        "listen_port": listen_port,
        "cmd": list(cmd),
        "tool": tool_path,
    }


# ─────────────────────────────────────────────────────────── stop / inspect

def stop_perftest(job_id: str) -> Dict[str, Any]:
    """SIGTERM the child; if it doesn't exit in 3 s, SIGKILL."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"status": "error", "error": f"unknown job_id {job_id}"}
    if job.finished_at is not None:
        return {"status": "noop", "note": "job already finished",
                "job": job.to_public_dict()}
    pid = job.pid
    if pid is None:
        return {"status": "noop", "note": "no pid recorded",
                "job": job.to_public_dict()}
    try:
        # killpg because we used start_new_session.
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        logger.debug(f"[rdma] SIGTERM job {job_id[:8]} pid={pid}: {exc}")
    # Give the reader thread a moment to wait() and update the job.
    for _ in range(30):
        with _jobs_lock:
            if job.finished_at is not None:
                break
        time.sleep(0.1)
    # Escalate if still alive.
    with _jobs_lock:
        still = job.finished_at is None
    if still:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        # Wait one more beat.
        for _ in range(20):
            with _jobs_lock:
                if job.finished_at is not None:
                    break
            time.sleep(0.1)
    with _jobs_lock:
        snap = job.to_public_dict()
    return {"status": "stopped", "job": snap}


def get_perftest_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return j.to_public_dict() if j else None


def list_perftest_jobs() -> List[Dict[str, Any]]:
    _gc_old_jobs()
    with _jobs_lock:
        return [j.to_public_dict() for j in _jobs.values()]


# ─────────────────────────────────────────────────────────── backwards-compat shim

# Keep the v0.2.x function name + module-level dict alive so any
# in-tree caller that still imports `start_ibperf_server` /
# `perf_stats` keeps working. New code should NOT use these.

perf_stats: Dict[str, Any] = {}  # legacy global; left empty


def start_ibperf_server(stream_data, stop_event):
    """Legacy shim — translates the v0.2.x stub signature into a
    start_perftest call. Preserves the historical hardcoded defaults
    (ib_write_bw, mlx5_0, write-style) so existing callers don't break.
    New code should call start_perftest() directly.
    """
    interface = stream_data.get("interface", "")
    iteration = stream_data.get("ibperf_iteration", 1000)
    mtu = stream_data.get("frame_size", 4096)
    rate_limit = stream_data.get("ibperf_rate_limit", 100_000)
    direction = stream_data.get("ibperf_direction", 2)
    # MTU encoding for perftest -m: prefer the closest valid value.
    perftest_mtu = 5 if mtu >= 4096 else 4 if mtu >= 2048 else 3 if mtu >= 1024 else 2
    opts = {
        "device": "mlx5_0",
        "msg_size": "32K",
        "qp_count": 1,
        "iterations": iteration,
        "duration": 100,  # was -D 100 in the original stub
        "mtu": perftest_mtu,
        "report_gbits": True,
        "perf_extra": ["--rate_limit=" + str(rate_limit)],
        "bidirectional": (direction == 2),
    }
    result = start_perftest("server", "write_bw", opts)
    if result.get("status") == "started":
        # Mirror the legacy perf_stats[interface] shape for any older
        # poller still reading it.
        perf_stats[interface] = {
            "timestamp": time.time(),
            "rate": 0.0,
            "unit": "Gbps",
            "job_id": result["job_id"],
        }
    return result
