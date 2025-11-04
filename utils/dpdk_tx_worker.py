# utils/dpdk_tx_worker.py
# DPDK tx_worker launcher + stream sync for OSTG.
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import logging
from typing import Optional, Dict, Any

LOG = logging.getLogger("dpdk_tx_worker")

# ------------------------- Public API -------------------------

def _resolve_stream_name(stream_data, interface, stream_id):
    ps = (stream_data.get("protocol_selection") or {}) or {}
    for k in ("name", "stream_name", "display_name", "title"):
        v = stream_data.get(k)
        if v:
            return str(v)
    for k in ("name", "stream_name"):
        v = ps.get(k)
        if v:
            return str(v)
    port = stream_data.get("port") or interface
    l4 = (stream_data.get("L4") or ps.get("L4") or "Any")
    sid = (str(stream_id) or "")[:8]
    return f"{port} / {l4} [{sid}]"


def should_use_dpdk(stream_data: Dict[str, Any]) -> bool:
    """
    Decide whether this stream should be driven by the DPDK backend.
    Convention: engine == 'dpdk' OR any of: dpdk_enable, dpdk, use_dpdk (truthy / 'true'/'1'/etc).
    Supports flags under protocol_selection as well.
    """
    if not isinstance(stream_data, dict):
        return False

    ps = stream_data.get("protocol_selection", {}) or {}
    engine = str(stream_data.get("engine") or ps.get("engine") or "").strip().lower()
    if engine == "dpdk":
        return True

    for key in ("dpdk_enable", "dpdk", "use_dpdk"):
        v = stream_data.get(key)
        if v is None:
            v = ps.get(key)
        if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
            return True
        if bool(v):
            return True
    return False


def run_stream(
    stream_data: Dict[str, Any],
    interface: str,
    stop_event,
    tracker,
    *,
    dpdk_corelist: Optional[str] = None,
    tx_worker_bin: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> int:
    """
    Launch the DPDK tx_worker for this stream and keep StreamTracker in sync.
    Returns the tx_worker exit code (0 on success).

    Required tracker methods:
      - update_tx_by_id(interface, stream_id)
      - get_tx_count_by_id(interface, stream_id)
      - find_stream_by_id(interface, stream_id)     [not required here but commonly present]
      - remove_stream_by_id(interface, stream_id)   [not required here but commonly present]

    stop_event: threading.Event-like with .is_set()
    """
    # Ensure a truly unique stream_id (never derive from name)
    sid = stream_data.get("stream_id")
    if not sid or not str(sid).strip():
        sid = _uuid()
        # Persist back so the rest of the pipeline sees the same id
        try:
            stream_data["stream_id"] = sid
        except Exception:
            pass
    stream_id = str(sid)

    stream_name = _resolve_stream_name(stream_data, interface, stream_id)
    LOG.info("DPDK backend starting for stream '%s' (id=%s) on %s", stream_name, stream_id, interface)

    fields = _resolve_l2_l3_l4(stream_data)
    missing = [k for k in ("src_mac", "dst_mac", "src_ip", "dst_ip") if not fields.get(k)]
    if missing:
        LOG.error("[dpdk] missing required fields: %s", missing)
        return 2

    # Pick device & NUMA
    bdf = _iface_to_bdf(interface)
    numa = _bdf_numa_node(bdf) if bdf else 0
    corelist = dpdk_corelist or str(stream_data.get("dpdk_corelist") or _pick_corelist_on_node(numa))

    # Resolve tx_worker binary (env → packaged → relative fallbacks)
    bin_path = tx_worker_bin or _resolve_tx_worker_bin()
    if not bin_path or not os.path.exists(bin_path):
        LOG.error("[dpdk] tx_worker binary not found at %s", bin_path)
        return 3
    try:
        os.chmod(bin_path, 0o755)
    except Exception:
        pass

    pps = _resolve_target_pps(stream_data)
    vlan_id = fields["vlan_id"]
    frame_size = int(fields["frame_size"] or 64)
    no_udp_csum = bool(stream_data.get("no_udp_csum", False))
    mem_channels = str(stream_data.get("dpdk_mem_channels") or os.environ.get("DPDK_MEM_CHANNELS") or "4")

    # duration
    duration_seconds = _resolve_duration_seconds(stream_data)

    # Warn if not UDP (worker is UDP-only)
    l4 = (stream_data.get("L4") or stream_data.get("protocol_selection", {}).get("L4") or "").strip().upper()
    if l4 and l4 != "UDP":
        LOG.warning("[dpdk] tx_worker is UDP-only (requested L4=%s) — proceeding with UDP.", l4)

    # Unique EAL file-prefix to avoid collisions across concurrent workers
    file_prefix = _file_prefix(stream_id, interface)
    LOG.debug("[dpdk] using file-prefix: %s", file_prefix)

    # Build command
    cmd = [bin_path, "-l", str(corelist), "-n", mem_channels, "--file-prefix", file_prefix]
    if bdf:
        # On mlx5 you should *not* bind to vfio; passing -a <BDF> with kernel driver is fine.
        cmd += ["-a", bdf]
    cmd += ["--",
            "--src-mac", fields["src_mac"], "--dst-mac", fields["dst_mac"],
            "--src-ip", str(fields["src_ip"]), "--dst-ip", str(fields["dst_ip"]),
            "--src-port", str(fields["udp_sport"]), "--dst-port", str(fields["udp_dport"]),
            "--size", str(frame_size), "--pps", str(pps),
            "--stream-id", stream_id]
    if vlan_id is not None:
        cmd += ["--vlan", str(vlan_id)]
    if no_udp_csum:
        cmd += ["--no-udp-csum"]
    if duration_seconds is not None:
        cmd += ["--duration", str(duration_seconds)]
    if "burst" in stream_data:
        try:
            b = int(stream_data["burst"])
            if b > 0:
                cmd += ["--burst", str(b)]
        except Exception:
            pass

    # Environment (allow caller to inject EAL/PMD paths etc.)
    child_env = os.environ.copy()
    if env:
        child_env.update(env)

    # Helpful defaults if user didn’t set them
    child_env.setdefault("RTE_DISABLE_MEMPOOL_OPS", "1")   # avoids missing shared objs on some hosts
    # You may add library path hints here if you ship PMDs:
    # child_env.setdefault("LD_LIBRARY_PATH", "/usr/local/lib:/usr/lib")

    LOG.info("[dpdk] exec: %s", shlex.join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=child_env,
    )

    # Parse "STAT ..." lines to sync StreamTracker
    stat_re = re.compile(r"^STAT(?:_FINAL)?\s+.*?\bstream=(\S+)\s+tx=(\d+)\s+drop=(\d+)")

    try:
        for line in proc.stdout:  # type: ignore[arg-type]
            if line is None:
                break
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("EAL:"):
                LOG.debug(line)
                continue
            m = stat_re.search(line)
            if m:
                tx_abs = int(m.group(2))
                # Convert absolute to deltas for StreamTracker
                current = _safe_get_tx(tracker, interface, stream_id)
                delta = max(tx_abs - current, 0)
                if delta:
                    for _ in range(delta):
                        tracker.update_tx_by_id(interface, stream_id)
                LOG.debug("[dpdk] %s tx=%s (delta=%s)", stream_id, tx_abs, delta)

            if stop_event.is_set():
                break
    except Exception as e:  # defensive
        LOG.warning("[dpdk] stdout reader error: %s", e)
    finally:
        # Try to shutdown cleanly
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    rc = proc.returncode if proc.returncode is not None else 0
    LOG.info("[dpdk] stream '%s' (id=%s) finished, rc=%s", stream_name, stream_id, rc)
    return rc


# ------------------------- Internals -------------------------

def _uuid() -> str:
    import uuid as _uuid_mod
    return str(_uuid_mod.uuid4())

def _safe_get_tx(tracker, interface: str, stream_id: str) -> int:
    try:
        return int(tracker.get_tx_count_by_id(interface, stream_id))
    except Exception:
        return 0

def _resolve_duration_seconds(stream_data: Dict[str, Any]) -> Optional[int]:
    # Honor tx_duration first (new style)
    txd = stream_data.get("tx_duration", {}) or {}
    if str(txd.get("mode") or "").strip().lower() == "seconds":
        try:
            return int(txd.get("seconds") or 0)
        except Exception:
            return 0

    # Fall back to stream_rate_control / legacy fields
    rc = stream_data.get("stream_rate_control", {}) or {}
    mode = rc.get("stream_duration_mode") or stream_data.get("stream_duration_mode")
    if str(mode or "").strip().lower() == "seconds":
        try:
            return int(rc.get("stream_duration_seconds", stream_data.get("stream_duration_seconds", 10)) or 10)
        except Exception:
            return 10
    return None

def _resolve_tx_worker_bin() -> str:
    """
    Search order:
      1) $TX_WORKER_BIN if file exists
      2) importlib.resources: resources.dpdk.tx_worker/build/tx_worker (installed package)
      3) path relative to this file: ../resources/dpdk/tx_worker/build/tx_worker
      4) CWD fallback: ./resources/dpdk/tx_worker/build/tx_worker
      5) Legacy local tree: ./tx_worker/build/tx_worker
    """
    # 1) explicit env override
    p = os.environ.get("TX_WORKER_BIN")
    if p and os.path.exists(p):
        LOG.debug("[dpdk] TX_WORKER_BIN=%s", p)
        return os.path.abspath(p)

    # 2) packaged resource
    try:
        # Python 3.9+: importlib.resources.files
        try:
            from importlib.resources import files as _res_files
            pkg = "resources.dpdk.tx_worker"
            rp = _res_files(pkg) / "build" / "tx_worker"
            if rp and os.path.exists(rp.as_posix()):
                return os.path.abspath(rp.as_posix())
        except Exception:
            # Back-compat: importlib.resources.path
            from importlib import resources as _res
            with _res.path("resources.dpdk.tx_worker", "build") as _build_dir:
                cand = os.path.join(str(_build_dir), "tx_worker")
                if os.path.exists(cand):
                    return os.path.abspath(cand)
    except Exception as e:
        LOG.debug("[dpdk] importlib.resources lookup failed: %s", e)

    # 3) relative to this file (site-packages layout has ../resources/…)
    here = os.path.dirname(__file__)
    cand = os.path.abspath(os.path.join(here, "..", "resources", "dpdk", "tx_worker", "build", "tx_worker"))
    if os.path.exists(cand):
        return cand

    # 4) cwd fallback
    cand = os.path.abspath(os.path.join(os.getcwd(), "resources", "dpdk", "tx_worker", "build", "tx_worker"))
    if os.path.exists(cand):
        return cand

    # 5) legacy local tree
    cand = os.path.abspath(os.path.join(os.getcwd(), "tx_worker", "build", "tx_worker"))
    if os.path.exists(cand):
        return cand

    # give a helpful path anyway
    return os.path.abspath(os.path.join(os.getcwd(), "resources", "dpdk", "tx_worker", "build", "tx_worker"))

def _iface_to_bdf(iface: str) -> Optional[str]:
    """Turn 'enp13s0f0np0' into '0000:0d:00.0' via sysfs."""
    try:
        dev = os.path.realpath(f"/sys/class/net/{iface}/device")
        bdf = os.path.basename(dev)
        return bdf if ":" in bdf else None
    except Exception:
        return None

def _bdf_numa_node(bdf: str) -> int:
    try:
        p = f"/sys/bus/pci/devices/{bdf}/numa_node"
        if os.path.exists(p):
            v = int(open(p).read().strip())
            return v if v >= 0 else 0
    except Exception:
        pass
    return 0

def _pick_corelist_on_node(numa_node: int) -> str:
    """
    Quick default: prefer two cores on the requested NUMA node if discoverable.
    Fallback to '0,2' (common on HT systems).
    """
    try:
        out = subprocess.check_output(["lscpu", "-e=CPU,NODE"], text=True)
        cores = []
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) != 2:
                continue
            cpu, node = parts
            if str(node) == str(numa_node):
                cores.append(int(cpu))
        if len(cores) >= 2:
            return f"{cores[0]},{cores[1]}"
    except Exception:
        pass
    return "0,2"

def _first_from(*pairs, default=None):
    for d, key in pairs:
        if isinstance(d, dict) and key in d and d[key] not in (None, ""):
            return d[key]
    return default

def _resolve_l2_l3_l4(stream_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract MACs, IPs, UDP ports, VLAN, frame size from stream_data.
    Prefers protocol_data.* with fallbacks to top-level.
    """
    pd = stream_data.get("protocol_data", {}) or {}
    mac_pd = pd.get("mac", {}) or {}
    ipv4_pd = pd.get("ipv4", {}) or {}
    udp_pd = pd.get("udp", {}) or {}
    vlan_pd = pd.get("vlan", {}) or {}

    src_mac = _first_from((mac_pd, "mac_source_address"),
                          (stream_data, "mac_source_address"),
                          (stream_data, "mac_src"), (stream_data, "mac_source"))
    dst_mac = _first_from((mac_pd, "mac_destination_address"),
                          (stream_data, "mac_destination_address"),
                          (stream_data, "mac_dst"), (stream_data, "mac_destination"))

    src_ip = _first_from((ipv4_pd, "ipv4_source"), (stream_data, "src_ip"))
    dst_ip = _first_from((ipv4_pd, "ipv4_destination"), (stream_data, "dst_ip"))

    def _pick_port(*vals, default=None):
        for v in vals:
            if v is None:
                continue
            s = str(v).strip()
            if s == "":
                continue
            try:
                x = int(s, 10)
                if x > 0:
                    return x
            except Exception:
                pass
        return default

    # Defaults if unset/zero: sport=1234, dport=4791 (good match with RoCEv2/UDP)
    udp_sport = _pick_port(udp_pd.get("udp_source_port"),
                           stream_data.get("udp_source_port"),
                           stream_data.get("udp_sport"),
                           default=1234)
    udp_dport = _pick_port(udp_pd.get("udp_destination_port"),
                           stream_data.get("udp_destination_port"),
                           stream_data.get("udp_dport"),
                           default=4791)

    vlan_id = _first_from((vlan_pd, "vlan_id"), (stream_data, "vlan_id"))
    try:
        vlan_id = int(vlan_id) if vlan_id not in (None, "", "0") else None
    except Exception:
        vlan_id = None

    try:
        frame_size = int(stream_data.get("frame_size", 64))
    except Exception:
        frame_size = 64

    return dict(
        src_mac=src_mac, dst_mac=dst_mac,
        src_ip=src_ip,   dst_ip=dst_ip,
        udp_sport=udp_sport, udp_dport=udp_dport,
        vlan_id=vlan_id, frame_size=frame_size,
    )

def _resolve_target_pps(stream_data: Dict[str, Any]) -> int:
    """
    Convert your rate selection into a numeric PPS for tx_worker (0 = flood).
    Supports:
      - Packets Per Second (PPS)
      - Bit Rate (bps/Mbps)
      - Load (%)
      - "Line Rate" => 0
      Reads from stream_rate_control, top-level fields, protocol_selection, and tx_rate.
    """
    rc = stream_data.get("stream_rate_control", {}) or {}
    ps = stream_data.get("protocol_selection", {}) or {}
    tr = stream_data.get("tx_rate", {}) or {}

    def pick_num(*vals, default=0):
        for v in vals:
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            try:
                return int(float(s))
            except Exception:
                pass
        return default

    rtype = (rc.get("stream_rate_type")
             or stream_data.get("stream_rate_type")
             or ps.get("stream_rate_type")
             or tr.get("mode")
             or "Packets Per Second (PPS)")
    rtype_l = str(rtype).strip().lower()
    if rtype_l == "bit rate":
        rtype_l = "bit rate (mbps)"

    if rtype_l in ("line rate", "line-rate", "linerate", "line"):
        return 0  # flood (worker decides)

    if rtype_l in ("packets per second (pps)", "pps"):
        return pick_num(rc.get("stream_pps_rate"),
                        stream_data.get("stream_pps_rate"),
                        ps.get("stream_pps_rate"),
                        tr.get("pps"),
                        default=0)

    if rtype_l in ("bit rate (mbps)", "mbps", "bitrate"):
        br_mbps = pick_num(rc.get("stream_bit_rate"),
                           stream_data.get("stream_bit_rate"),
                           ps.get("stream_bit_rate"),
                           tr.get("mbps"),
                           default=0)
        frame_size = pick_num(stream_data.get("frame_size"), ps.get("frame_size"), default=64)
        bps = br_mbps * 1_000_000
        bytes_per_packet = max(frame_size + 20, 1)
        return int(bps / (bytes_per_packet * 8))

    if rtype_l in ("bit rate (bps)", "bps"):
        br_bps = pick_num(rc.get("stream_bit_rate"),
                          stream_data.get("stream_bit_rate"),
                          ps.get("stream_bit_rate"),
                          tr.get("bps"),
                          default=0)
        frame_size = pick_num(stream_data.get("frame_size"), ps.get("frame_size"), default=64)
        bytes_per_packet = max(frame_size + 20, 1)
        return int(br_bps / (bytes_per_packet * 8))

    if rtype_l in ("load (%)", "load", "percent"):
        load = pick_num(rc.get("stream_load_percentage"),
                        stream_data.get("stream_load_percentage"),
                        ps.get("stream_load_percentage"),
                        tr.get("percent"),
                        default=0)
        # naive 1GbE baseline: 100% ≈ 1.25 Mpps
        return int((1_250_000 * load) / 100)

    return 0

def _file_prefix(stream_id: str, iface: str) -> str:
    """
    Produce a unique, sanitized EAL file-prefix for each worker to avoid
    collisions in hugepage-backed shared mem regions across processes.
    """
    import time, re, os
    try:
        import uuid as _uuid_mod
        short = str(_uuid_mod.UUID(str(stream_id))).split("-")[0]
    except Exception:
        short = re.sub(r"[^a-zA-Z0-9]+", "", str(stream_id))[:8] or "x"
    ifx = re.sub(r"[^A-Za-z0-9_]+", "_", str(iface))
    return f"txw_{short}_{ifx}_{os.getpid()}_{int(time.time()*1000)}"

# (Optional helpers if you later choose to convert "line" to numeric PPS)
def _read_link_speed_mbps(iface: str) -> int:
    try:
        p = f"/sys/class/net/{iface}/speed"
        if os.path.exists(p):
            v = int(open(p).read().strip())
            if v > 0:
                return v
    except Exception:
        pass
    return 0

def _compute_line_pps(iface: str, frame_size_bytes: int) -> int:
    speed = _read_link_speed_mbps(iface)  # e.g., 100000 for 100G
    if speed <= 0:
        return 1_000_000  # safe fallback
    l1_bytes = max(64, int(frame_size_bytes)) + 20  # preamble+IFG approx
    return max(1, int((speed * 1_000_000) // (l1_bytes * 8)))
