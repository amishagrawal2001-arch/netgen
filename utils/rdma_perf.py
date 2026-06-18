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
    # v0.3.15: device-wide capability ceilings from ibv_devinfo. None
    # when ibv_devinfo isn't installed, fails (e.g. perms in a
    # container), or doesn't print the field. Surfaced in
    # /api/rdma/devices so the GUI can show real HCA limits in the
    # RDMA Devices viewer + clamp QP-count spinboxes intelligently.
    # Values are RAW caps reported by libibverbs — actual usable
    # numbers are typically smaller (memory, CPU bottlenecks hit
    # first).
    max_qp: Optional[int] = None        # device-wide QP ceiling
    max_qp_wr: Optional[int] = None     # per-QP send/recv WR depth
    max_cq: Optional[int] = None        # device-wide CQ ceiling
    max_cqe: Optional[int] = None       # per-CQ entry ceiling
    max_mr: Optional[int] = None        # device-wide MR ceiling
    max_pd: Optional[int] = None        # device-wide PD ceiling
    max_sge: Optional[int] = None       # per-WR scatter/gather ceiling
    # v0.3.16+: kernel netdev names attached to this RDMA device.
    # Resolved by walking /sys/class/infiniband/<name>/device/net/.
    # Operator-critical: without these the RDMA Devices view shows
    # only abstract `mlx5_N` IDs, leaving the operator to manually
    # cross-reference with `ip link` to know which port carries
    # their test traffic. List rather than scalar because some HCAs
    # (bonded mlx5_bond_*, or dual-port HCAs with separate netdevs)
    # expose multiple netdevs. Empty list when the symlink dir is
    # missing (kernel without netdev binding, or containerised
    # /sys mount with /device stripped).
    net_ifaces: List[str] = field(default_factory=list)
    # v0.5.167: kernel driver name (e.g. "mlx5_core", "irdma",
    # "bnxt_re"). Resolved by readlink of
    # `/sys/class/infiniband/<name>/device/driver`. None when the
    # symlink is missing (containerised /sys with /device stripped,
    # or built-in driver without a bus tie-in). Surfaced in the
    # HTML session report so operators can tell at a glance which
    # driver stack ran the test.
    driver: Optional[str] = None
    # v0.5.170: PCIe link state. `current_*` is what the HCA
    # actually trained to; `max_*` is what the slot can do.
    # `downgraded=True` when the trained link is below the slot's
    # max — operator-critical signal (a Gen5 ConnectX-7 stuck at
    # Gen4 x16 still works but tops out at half its theoretical
    # bandwidth). All values are best-effort; missing sysfs nodes
    # leave them None, and the report renders a `—`.
    pcie_current_speed_gts: Optional[float] = None
    pcie_current_width: Optional[int] = None
    pcie_max_speed_gts: Optional[float] = None
    pcie_max_width: Optional[int] = None
    pcie_gen: Optional[int] = None           # derived: 1/2/3/4/5/6
    pcie_max_gen: Optional[int] = None
    pcie_downgraded: bool = False
    # v0.5.170: NUMA node the HCA sits on. Lets the report flag
    # cross-NUMA test placements (worker on node 0, HCA on node 1
    # is a known perf cliff). -1 in /sys means "no NUMA info"; we
    # surface that as None.
    numa_node: Optional[int] = None
    # v0.5.170: IPv4 / IPv6 addresses on each netdev bound to this
    # HCA. Operators read IPs not GIDs when cross-referencing with
    # `ip addr`; surfacing them in the report cuts a manual SSH.
    # Shape: {iface_name: ["10.42.0.1/24", "fe80::5e25:73ff:fe3f:3056/64"]}
    netdev_ips: Dict[str, List[str]] = field(default_factory=dict)


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
    # v0.5.177: Spirent/Ixia-style latency-vs-size sweep. One entry
    # per message size when perftest is run with `-a -n N`. Each
    # entry: {bytes, iters, lat_min_us, lat_max_us, lat_typ_us,
    # lat_avg_us, lat_stdev_us, lat_p99_us, lat_p999_us}. None
    # values are preserved as None rather than 0.0 so the report
    # renders `—` for unreported columns.
    final_lat_sweep: Optional[List[Dict[str, Any]]] = None
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


def _list_net_ifaces(dev: str) -> List[str]:
    """Return the kernel netdev names attached to an RDMA device.

    Walks ``/sys/class/infiniband/<dev>/device/net/`` — each entry
    in that directory is a symlink (or virtual directory) named
    after a netdev that this HCA exposes. Mellanox single-port
    HCAs typically have ONE entry (e.g. ``mlx5_0`` → ``enp4s0f0np0``);
    bonded HCAs (``mlx5_bond_0``) expose the bond's netdev; some
    dual-port configurations expose two.

    Returns [] on:
      * containerised /sys mount that strips /device/net
      * kernel without the netdev binding for this HCA
      * permission denied on the directory walk
    The caller treats empty as "unknown" and renders accordingly
    (operator can still pick the device, just won't see the
    netdev name in the label).
    """
    net_dir = os.path.join(_IB_SYSFS_ROOT, dev, "device", "net")
    if not os.path.isdir(net_dir):
        return []
    try:
        names = sorted(os.listdir(net_dir))
    except OSError as exc:
        logger.debug(f"[rdma] _list_net_ifaces({dev}): {exc}")
        return []
    # Filter out hidden entries (none expected here, but defensive).
    return [n for n in names if not n.startswith(".")]


def _read_driver_name(dev: str) -> Optional[str]:
    """v0.5.167: return the kernel driver bound to an RDMA HCA.

    `/sys/class/infiniband/<dev>/device/driver` is a symlink whose
    basename is the driver module name (mlx5_core, irdma, bnxt_re,
    qedr, hns_roce, …). Surfaced in the session report so operators
    can tell the driver stack at a glance.

    Returns None when the symlink is missing — happens in containers
    with a stripped /device subtree, and for the handful of HCAs that
    expose to /sys/class/infiniband without a /sys/bus tie-in.
    """
    link = os.path.join(_IB_SYSFS_ROOT, dev, "device", "driver")
    try:
        target = os.readlink(link)
    except OSError:
        return None
    name = os.path.basename(target.rstrip("/"))
    return name or None


# ───── v0.5.170 PCIe link + NUMA + netdev IP readers ─────────────


_PCI_SYSFS_ROOT = "/sys/bus/pci/devices"

# v0.5.170: PCIe encoded-bitrate → generation map. The kernel
# emits speeds like "16.0 GT/s PCIe" — we parse the float and
# look it up. Rounding tolerates the half-step values some BIOS
# vendors emit (e.g. "5.0 GT/s" + "8.0 GT/s" can be reported as
# "5.0 GT/s PCIe" or just "5 GT/s").
_PCIE_GEN_TABLE = (
    (2.5, 1),
    (5.0, 2),
    (8.0, 3),
    (16.0, 4),
    (32.0, 5),
    (64.0, 6),
)


def _gts_to_gen(gts: Optional[float]) -> Optional[int]:
    """Map a GT/s float to its PCIe generation number. Returns
    None when the input is missing or doesn't map to a known gen
    (future-proofing — Gen7 = 128 GT/s isn't shipping yet)."""
    if gts is None:
        return None
    # Pick the highest gen whose nominal rate is <= the reported
    # rate + a 10% tolerance. PCIe rates double per gen so the
    # tolerance never causes collision.
    best: Optional[int] = None
    for rate, gen in _PCIE_GEN_TABLE:
        if gts + 0.5 >= rate:
            best = gen
    return best


def _parse_link_speed_gts(raw: Optional[str]) -> Optional[float]:
    """Extract the GT/s number from the kernel's link-speed string.

    Examples:
      "16.0 GT/s PCIe" → 16.0
      "8 GT/s"         → 8.0
      "Unknown"        → None
    """
    if not raw:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*GT/s", raw, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _read_pcie_link(
    bdf: str,
    *,
    pci_root: str = _PCI_SYSFS_ROOT,
) -> Dict[str, Any]:
    """v0.5.170: read PCIe link state from sysfs.

    Returns a dict with current/max speed+width+gen and a
    `downgraded` flag. All keys may be None if sysfs doesn't
    expose them (containerised /sys with /bus/pci stripped, or
    virtualised devices where the kernel can't see the slot).

    The `downgraded` flag is critical operator signal: a Gen5
    ConnectX-7 stuck at Gen4 x16 still trains and works, but
    runs at half its theoretical BW. The report shows this with
    a red badge so the operator doesn't waste time wondering why
    they didn't hit line rate."""
    out: Dict[str, Any] = {
        "current_speed_gts": None,
        "current_width": None,
        "max_speed_gts": None,
        "max_width": None,
        "gen": None,
        "max_gen": None,
        "downgraded": False,
    }
    base = os.path.join(pci_root, bdf)
    if not os.path.isdir(base):
        return out
    out["current_speed_gts"] = _parse_link_speed_gts(
        _read_sysfs(os.path.join(base, "current_link_speed")))
    out["max_speed_gts"] = _parse_link_speed_gts(
        _read_sysfs(os.path.join(base, "max_link_speed")))
    cw = _read_sysfs(os.path.join(base, "current_link_width"))
    mw = _read_sysfs(os.path.join(base, "max_link_width"))
    try:
        out["current_width"] = int(cw) if cw else None
    except ValueError:
        out["current_width"] = None
    try:
        out["max_width"] = int(mw) if mw else None
    except ValueError:
        out["max_width"] = None
    out["gen"] = _gts_to_gen(out["current_speed_gts"])
    out["max_gen"] = _gts_to_gen(out["max_speed_gts"])
    # Downgraded when EITHER speed OR width is below the cap.
    # Both must be known to claim downgrade — unknown values
    # default to False rather than crying wolf.
    if (out["gen"] is not None and out["max_gen"] is not None
            and out["gen"] < out["max_gen"]):
        out["downgraded"] = True
    if (out["current_width"] is not None
            and out["max_width"] is not None
            and out["current_width"] < out["max_width"]):
        out["downgraded"] = True
    return out


def _read_numa_node(
    bdf: str,
    *,
    pci_root: str = _PCI_SYSFS_ROOT,
) -> Optional[int]:
    """Return the NUMA node the PCI device sits on. -1 in sysfs
    (no NUMA topology, or single-socket box) becomes None — the
    report renders that as `—`."""
    raw = _read_sysfs(os.path.join(pci_root, bdf, "numa_node"))
    if not raw:
        return None
    try:
        node = int(raw)
        return node if node >= 0 else None
    except ValueError:
        return None


def _read_iface_ips(ifaces: List[str]) -> Dict[str, List[str]]:
    """Return `{iface: [ip/prefix, ...]}` for each iface bound to
    an HCA. IPv4 + IPv6 in the same list — operators want both.

    Uses psutil if available (deterministic, no subprocess).
    Falls back to an empty dict if psutil isn't there or the
    iface isn't in the host's net stack. Never raises."""
    out: Dict[str, List[str]] = {}
    if not ifaces:
        return out
    try:
        import psutil
        import socket
    except ImportError:
        return out
    try:
        all_addrs = psutil.net_if_addrs()
    except Exception as exc:
        logger.debug(f"[rdma] psutil.net_if_addrs failed: {exc}")
        return out
    for iface in ifaces:
        addrs = all_addrs.get(iface, [])
        ip_list: List[str] = []
        for a in addrs:
            fam = getattr(a, "family", None)
            ip = getattr(a, "address", None)
            mask = getattr(a, "netmask", None)
            if fam == socket.AF_INET and ip:
                # IPv4 mask is dotted; convert to prefix length.
                prefix = _ipv4_mask_to_prefix(mask)
                ip_list.append(
                    f"{ip}/{prefix}" if prefix is not None else ip)
            elif fam == socket.AF_INET6 and ip:
                # Strip the scope-id suffix (`%iface`) — operators
                # don't need it.
                ip6 = ip.split("%", 1)[0]
                prefix = _ipv6_mask_to_prefix(mask)
                ip_list.append(
                    f"{ip6}/{prefix}" if prefix is not None else ip6)
        if ip_list:
            out[iface] = ip_list
    return out


def _ipv4_mask_to_prefix(mask: Optional[str]) -> Optional[int]:
    """Dotted IPv4 netmask → prefix length. Returns None on
    malformed input (None / empty / non-dotted)."""
    if not mask or "." not in mask:
        return None
    try:
        octets = [int(o) for o in mask.split(".")]
        if len(octets) != 4:
            return None
        bits = 0
        for o in octets:
            if not 0 <= o <= 255:
                return None
            bits += bin(o).count("1")
        return bits
    except ValueError:
        return None


def _ipv6_mask_to_prefix(mask: Optional[str]) -> Optional[int]:
    """psutil emits IPv6 netmask as `ffff:ffff:ffff:ffff::` —
    count the 1-bits across the 8 hextets. None on bad input."""
    if not mask or ":" not in mask:
        return None
    try:
        bits = 0
        for hx in mask.split(":"):
            if not hx:
                continue
            v = int(hx, 16)
            if not 0 <= v <= 0xFFFF:
                return None
            bits += bin(v).count("1")
        return bits
    except ValueError:
        return None


def _resolve_bdf_for_hca(dev: str) -> Optional[str]:
    """Resolve the canonical PCI BDF for an HCA name by reading
    `/sys/class/infiniband/<dev>/device` (symlink whose target's
    basename is the BDF, e.g. `0000:2b:00.0`).

    Used to plumb sysfs/pci lookups (link speed, NUMA, ifaddrs)
    from the HCA-side enumerator into the PCI-side helpers."""
    link = os.path.join(_IB_SYSFS_ROOT, dev, "device")
    try:
        target = os.readlink(link)
    except OSError:
        return None
    bdf = os.path.basename(target.rstrip("/"))
    if not re.fullmatch(
            r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]",
            bdf, re.IGNORECASE):
        return None
    return bdf.lower()


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


# IB MTU enum (RFC 7146 §2.4 / IBA spec §3.5.3 / verbs <infiniband/verbs.h>):
#   1 → 256 B   2 → 512 B   3 → 1024 B   4 → 2048 B   5 → 4096 B
# Mellanox + most other vendors expose /sys/class/infiniband/<dev>/ports/<n>/
# active_mtu as JUST the enum digit ("3\n") on modern kernels (5.x+). Older
# kernels and some out-of-tree drivers wrote "3: 1024" or even bare "1024".
# v0.3.13: handle all three formats; the v0.3.12 regex \d{3,5} required ≥3
# digits so single-digit "3" returned 0 — the in-app RDMA Devices viewer
# showed "0 B" MTU on every Mellanox NIC.
_IB_MTU_ENUM_BYTES: Dict[int, int] = {1: 256, 2: 512, 3: 1024, 4: 2048, 5: 4096}


def _parse_active_mtu(raw: Optional[str]) -> int:
    """Return MTU in bytes (e.g. 4096), or 0 if unparseable / missing.

    Accepts:
      "3"          → 1024  (modern kernel: bare IB MTU enum)
      "3: 1024"    → 1024  (older kernel: enum + bytes)
      "1024"       → 1024  (driver wrote bytes directly)
      "4096[B]"    → 4096  (perftest-style decoration; defensive)
      ""/None      → 0
    """
    if not raw:
        return 0
    txt = raw.strip()
    # Strip any "[B]"/"bytes" suffix some tools add.
    txt = re.sub(r"\s*\[?[Bb](ytes)?\]?\s*$", "", txt).strip()
    # If colon-separated "enum: bytes", prefer the bytes after the colon.
    if ":" in txt:
        right = txt.split(":", 1)[1].strip()
        if right.isdigit():
            n = int(right)
            return n if n >= 256 else _IB_MTU_ENUM_BYTES.get(n, 0)
    # Bare numeric. Single digit 1–5 → IB MTU enum; otherwise treat as bytes.
    if txt.isdigit():
        n = int(txt)
        if n in _IB_MTU_ENUM_BYTES:
            return _IB_MTU_ENUM_BYTES[n]
        return n if n >= 256 else 0
    # Embedded digits — pull the largest plausible bytes value (last fallback).
    candidates = [int(m) for m in re.findall(r"\d+", txt)]
    big = [c for c in candidates if c >= 256]
    if big:
        return max(big)
    if candidates:
        return _IB_MTU_ENUM_BYTES.get(candidates[0], 0)
    return 0


# Fields we extract from ibv_devinfo's verbose output. Each maps to a
# Python int after stripping the value side. Hex values (max_mr_size,
# page_size_cap) are intentionally NOT parsed — they're bitfields, not
# counts, and the GUI doesn't display them.
_IBV_DEVINFO_INT_FIELDS = (
    "max_qp", "max_qp_wr", "max_cq", "max_cqe",
    "max_mr", "max_pd", "max_sge",
)


def _parse_ibv_devinfo(blob: str) -> Dict[str, Optional[int]]:
    """Pure-string parser for ibv_devinfo -v output.

    Returns a dict with the keys in _IBV_DEVINFO_INT_FIELDS; missing
    fields land as None. Tolerant of formatting variation:
      * Field name and value separated by any whitespace
      * Value may have trailing "(decoration)" — strip and parse the
        leading integer
      * Hex values (max_mr_size, page_size_cap) are intentionally
        NOT in the field list — they're bitfields, not counts
    """
    out: Dict[str, Optional[int]] = {k: None for k in _IBV_DEVINFO_INT_FIELDS}
    if not blob:
        return out
    for line in blob.splitlines():
        m = re.match(r"\s*(\w+)\s*:\s*(\S+)", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if key not in _IBV_DEVINFO_INT_FIELDS:
            continue
        # Tolerate "262144" and "262144(...)" forms; reject hex.
        if val.startswith("0x"):
            continue
        try:
            out[key] = int(val.split("(")[0])
        except (ValueError, TypeError):
            continue
    return out


def _query_ibv_devinfo(device_name: str) -> Dict[str, Optional[int]]:
    """Run ``ibv_devinfo -v -d <device>`` and parse out the integer
    capability fields. Graceful Nones on every failure mode so
    list_rdma_devices() can't crash on a missing binary or perms
    error.

    Failure modes handled:
      * ibv_devinfo binary missing on PATH → all-None
      * Permission denied (containerised, no rdma group) → all-None
      * Timeout (rare; ibv_devinfo can hang on a broken HCA) → all-None
      * Non-zero exit with diagnostic output → parse what we can
    """
    empty = {k: None for k in _IBV_DEVINFO_INT_FIELDS}
    if not device_name:
        return empty
    if shutil.which("ibv_devinfo") is None:
        logger.debug("[rdma] ibv_devinfo not on PATH; max_qp etc. unavailable")
        return empty
    try:
        proc = subprocess.run(
            ["ibv_devinfo", "-v", "-d", device_name],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug(f"[rdma] ibv_devinfo -d {device_name} failed: {exc}")
        return empty
    # Even on rc != 0, parse what's in stdout — some failures still
    # print partial output (e.g. one port down with another up).
    return _parse_ibv_devinfo(proc.stdout or "")


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
                # active_mtu format varies by kernel + driver. v0.3.13:
                # delegate to _parse_active_mtu which handles all three
                # forms (bare enum "3", "3: 1024", or raw bytes "1024").
                # v0.3.12 had a regex \d{3,5} here that returned 0 for
                # the modern bare-enum format Mellanox uses on 5.x+.
                mtu = _parse_active_mtu(mtu_raw)
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

        # v0.3.15: query HCA capability ceilings via ibv_devinfo.
        # Subprocess cost is ~30 ms per device; serial probe of N
        # devices = ~N×30 ms. Acceptable for HCA counts in the
        # 1–16 range typical of single hosts. If a future operator
        # has 64+ HCAs and this is too slow, parallelise via a
        # ThreadPoolExecutor in this loop.
        caps = _query_ibv_devinfo(dev)
        # v0.3.16+: surface kernel netdev names. Operator-critical
        # for correlating mlx5_N with `ip link` / IP config when
        # picking a device for Blast a RDMA Flow.
        net_ifaces = _list_net_ifaces(dev)

        devices.append(RdmaDevice(
            name=dev,
            vendor=vendor,
            fw_version=fw_version,
            node_guid=node_guid,
            ports=port_list,
            max_qp=caps.get("max_qp"),
            max_qp_wr=caps.get("max_qp_wr"),
            max_cq=caps.get("max_cq"),
            max_cqe=caps.get("max_cqe"),
            max_mr=caps.get("max_mr"),
            max_pd=caps.get("max_pd"),
            max_sge=caps.get("max_sge"),
            net_ifaces=net_ifaces,
            driver=_read_driver_name(dev),
            # v0.5.170: PCIe / NUMA / netdev IPs. Resolved via the
            # BDF symlink — bail gracefully when sysfs is partial.
            **_collect_pcie_numa_ips(dev, net_ifaces),
        ))
    return devices


def _collect_pcie_numa_ips(
    dev: str, net_ifaces: List[str],
) -> Dict[str, Any]:
    """v0.5.170: bundle the new PCIe/NUMA/netdev_ips reads so the
    list_rdma_devices call site stays readable."""
    bdf = _resolve_bdf_for_hca(dev)
    if not bdf:
        return {"netdev_ips": _read_iface_ips(net_ifaces)}
    pcie = _read_pcie_link(bdf)
    return {
        "pcie_current_speed_gts": pcie["current_speed_gts"],
        "pcie_current_width": pcie["current_width"],
        "pcie_max_speed_gts": pcie["max_speed_gts"],
        "pcie_max_width": pcie["max_width"],
        "pcie_gen": pcie["gen"],
        "pcie_max_gen": pcie["max_gen"],
        "pcie_downgraded": pcie["downgraded"],
        "numa_node": _read_numa_node(bdf),
        "netdev_ips": _read_iface_ips(net_ifaces),
    }


# ─────────────────────────────────────────────────────────── perftest probe

def perftest_installed() -> Dict[str, Any]:
    """Return ``{installed: bool, tools: {test_id: path|None}, version: str|None}``.

    ``installed`` is True iff at least one supported tool is on PATH.
    Version is best-effort — perftest's own ``--version`` flag returns
    no useful string on the builds shipped by current Debian/Ubuntu/
    Mellanox (the run banner shows test name but not version), so we
    fall back through several probes in order of cost.
    """
    tools: Dict[str, Optional[str]] = {}
    for test_id, binary in _SUPPORTED_TESTS.items():
        tools[test_id] = shutil.which(binary)

    installed = any(p is not None for p in tools.values())
    version: Optional[str] = None if not installed else _probe_perftest_version(tools)
    return {"installed": installed, "tools": tools, "version": version}


def _probe_perftest_version(tools: Dict[str, Optional[str]]) -> Optional[str]:
    """Try several version-discovery paths in order — return the first hit.

    Stop at the first probe that yields a non-empty version string.
    Logs at debug level for each miss so the misbehaving probe can be
    diagnosed without spamming WARNING noise on healthy hosts.

    Probe order (cheapest first):
      1. ``<tool> --version`` stdout/stderr (works on older perftest)
      2. ``<tool> -V`` (some forks)
      3. ``dpkg -s perftest`` (Debian/Ubuntu; ~30 ms; very reliable)
      4. ``rpm -q perftest`` (RHEL/Fedora; ~30 ms; very reliable)
      5. ``apk info -e perftest`` (Alpine; ~30 ms)

    Returns None if every probe fails — the GUI then renders just
    "perftest installed" without a version qualifier rather than
    erroring out.
    """
    # 1 + 2: ask perftest itself.
    for flag in ("--version", "-V"):
        for path in tools.values():
            if path is None:
                continue
            try:
                proc = subprocess.run(
                    [path, flag],
                    capture_output=True, text=True, timeout=5,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug(f"[rdma] {path} {flag} failed: {exc}")
                continue
            blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
            v = _extract_version_from_blob(blob)
            if v:
                return v
        # Only try a few tools per flag — they all share the perftest
        # package, so the first one that produces output decides.

    # 3: dpkg -s perftest
    try:
        proc = subprocess.run(
            ["dpkg", "-s", "perftest"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            m = re.search(r"^Version:\s*(\S+)", proc.stdout, re.MULTILINE)
            if m:
                return m.group(1)
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        logger.debug(f"[rdma] dpkg -s perftest failed: {exc}")

    # 4: rpm -q perftest
    try:
        proc = subprocess.run(
            ["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", "perftest"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        logger.debug(f"[rdma] rpm -q perftest failed: {exc}")

    # 5: apk info -e perftest
    try:
        proc = subprocess.run(
            ["apk", "info", "-v", "perftest"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            m = re.search(r"^perftest-(\S+)", proc.stdout, re.MULTILINE)
            if m:
                return m.group(1)
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        logger.debug(f"[rdma] apk info perftest failed: {exc}")

    return None


def _extract_version_from_blob(blob: str) -> Optional[str]:
    """Pure-string helper — pull a version number out of perftest's
    version-flag output if one is present. Factored out so the dpkg/
    rpm/apk fallbacks can stay independent of perftest's own output
    format. Used by _probe_perftest_version."""
    if not blob:
        return None
    # "perftest 6.2-1", "perftest-6.2", "Perftest version 6.10"
    m = re.search(
        r"perftest[-\s]+(?:version\s+)?v?(\d+(?:\.\d+)+(?:[-.]\S+)?)",
        blob, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Bare "6.10" or "6.2-1" alone on a line (some forks)
    m = re.search(r"^\s*v?(\d+\.\d+(?:[-.]\S+)?)\s*$", blob, re.MULTILINE)
    if m:
        return m.group(1)
    return None


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

    # v0.5.177: Spirent/Ixia-style message-size sweep. When the
    # operator picks "Sweep message sizes", we let perftest cycle
    # through every power-of-2 size (2 B → 8 MB) and emit one row
    # per size. -a forces sweep mode; we MUST also switch from
    # -D (duration) to -n (iterations per size) because perftest
    # uses -n's count as the per-size sample budget. Suppressing
    # -s here is deliberate — when -a is on, -s is ignored and a
    # user-set -s would just clutter the cmdline.
    sweep_sizes = opts.get("sweep_sizes") is True

    msg_size = opts.get("msg_size")
    if msg_size and not sweep_sizes:
        cmd += ["-s", str(msg_size)]

    # Multiple QPs are a perftest *_bw flag only — `ib_send_lat`,
    # `ib_write_lat`, `ib_read_lat` reject `-q N` with
    # "Multiple QPs only available on bw tests" and exit rc=1
    # before any data row. Pre-fix, the dialog's qp_count spinbox
    # value rode through to lat tests too, blowing up any sweep
    # the operator started after a multi-QP BW run had bumped
    # the spinbox > 1. Gate `-q` on test.endswith("_bw") so the
    # lat tests silently get whatever single-QP setup perftest
    # uses by default.
    qp_count = opts.get("qp_count")
    if (qp_count and int(qp_count) > 1
            and test.endswith("_bw")):
        cmd += ["-q", str(qp_count)]

    duration = opts.get("duration")
    iterations = opts.get("iterations")
    is_lat = test.endswith("_lat")
    if sweep_sizes:
        cmd += ["-a"]
        # iterations_per_size has Spirent-style semantics: "this
        # many ping-pongs at each size". Defaults to 5000 — enough
        # to stabilise t_avg / p99 without dragging the whole sweep
        # past 30 s on a healthy HCA.
        per_size = int(opts.get("iterations_per_size") or 5000)
        cmd += ["-n", str(per_size)]
    elif is_lat:
        # v0.5.182 NB-8: lat tests must use iterations mode (-n)
        # to get perftest's 9-column output (min / max / t_typical
        # / stdev / 99% percentile). Duration mode (-D) shrinks
        # the output to 4 columns (bytes / iters / t_avg / tps),
        # silently dropping p99. Operator hit this on srv06's
        # send_lat / write_lat / read_lat: p99 always rendered
        # as `—` in the report.
        cmd += ["-n", str(int(iterations) if iterations else 10000)]
    elif duration:
        # perftest rejects -D and -n together; prefer duration.
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

    # v0.5.74 (audit F3): strict bool. Pre-fix
    # `bidirectional: "false"` (truthy string) enabled `-b`. Same
    # class as v0.5.68 C2/C3 — the helper is duplicated here
    # rather than imported from run_tgen_server to keep utils
    # standalone.
    def _opt_true(v):
        return v is True

    if _opt_true(opts.get("bidirectional")) and test.endswith("_bw"):
        cmd += ["-b"]

    if _opt_true(opts.get("use_event")):
        cmd += ["-e"]

    inline = opts.get("inline")
    if inline is not None:
        cmd += ["-I", str(inline)]

    cq_mod = opts.get("cq_mod")
    if cq_mod:
        cmd += ["-Q", str(cq_mod)]

    if _opt_true(opts.get("cpu_util")):
        cmd += ["--cpu_util"]

    # Always prefer gbits for _bw tests so our parser knows the unit.
    # v0.5.74 (audit F3): default-True via `is True` check —
    # operator can disable by explicitly passing `false` (literal
    # Python `False`, not the truthy string).
    _rg = opts.get("report_gbits", True)
    if test.endswith("_bw") and (_rg is True or _rg is None):
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
# v0.5.162: with `--cpu_util` perftest appends a 6th column
# (CPU_util[%]). The pre-fix regex was anchored to 5 columns, so
# enabling CPU util made every BW run report None across the
# board (rc=0 but no parsed values). Make the CPU util column
# optional.
_RE_BW_DATA_ROW = re.compile(
    # v0.5.176: loosen the peak column to also accept the textual
    # placeholders some perftest builds emit for ib_read_bw —
    # specifically `N/A` (or a bare `-`) when the tool can't
    # compute peak for one-sided operations. Pre-fix, the strict
    # `[\d.]+` peak regex rejected the entire data row, leaving
    # final_bw_avg_gbps as None and the report showing all `—`
    # cells for read_bw runs.
    r"^\s*(?P<bytes>\d+)\s+(?P<iters>\d+)\s+"
    r"(?P<peak>[\d.]+|N/A|-)\s+(?P<avg>[\d.]+)\s+(?P<mrate>[\d.]+)"
    r"(?:\s+(?P<cpu_util>[\d.]+))?\s*$"
)
# Latency data row: "  2     1000     1.50     2.10     5.30  ... 2.95"
# perftest lat tools print: bytes iter t_min t_max t_typical t_avg t_stdev 99% 99.9%
# v0.5.162: same `--cpu_util` trailing-column tolerance as BW.
_RE_LAT_DATA_ROW = re.compile(
    r"^\s*(?P<bytes>\d+)\s+(?P<iters>\d+)\s+"
    r"(?P<tmin>[\d.]+)\s+(?P<tmax>[\d.]+)\s+"
    r"(?P<ttyp>[\d.]+)\s+(?P<tavg>[\d.]+)\s+"
    r"(?P<tstdev>[\d.]+)\s+(?P<p99>[\d.]+)(?:\s+(?P<p999>[\d.]+))?"
    r"(?:\s+(?P<cpu_util>[\d.]+))?\s*$"
)

# v0.5.177: abbreviated 4-column lat row that perftest emits in
# DURATION mode (`-D N`). The Blast dialog always uses -D, so the
# 9-column regex above never matched a real run — operator hit
# this on srv06 with `final_lat_*` staying None across every
# send_lat / write_lat / read_lat run.
#
# Format observed on srv06 (perftest from Ubuntu 22.04 repo):
#   #bytes        #iterations       t_avg[usec]    tps average
#   2             1577611            1.90           262864.28
#
# Just bytes / iters / t_avg / tps — no min, max, p99 etc.
# When matched, only `final_lat_avg_us` populates; min/max/p99
# stay None and the report renders them as `—`.
_RE_LAT_DATA_ROW_DURATION = re.compile(
    r"^\s*(?P<bytes>\d+)\s+(?P<iters>\d+)\s+"
    r"(?P<tavg>[\d.]+)\s+(?P<tps>[\d.]+)"
    r"(?:\s+(?P<cpu_util>[\d.]+))?\s*$"
)

# v0.5.146: perftest dumps a config block before the test starts
# ("CQ Moderation : 1", "Mtu : 1024[B]", "Link type : Ethernet",
# "CPU freq : 2394[MHz]", "GID index : N", "Connection type : RC",
# etc). When perftest fails to even start a transfer, these lines
# ARE the last 10 in stdout — and the previous error builder
# (`tail = stdout_tail[-10:]`) surfaced them as the diagnostic.
# Operator screenshot showed exactly this red wall of header text.
#
# This regex matches "<Title-Case Words> : <value>" lines, which is
# the precise shape perftest uses for the config dump. We also
# tag a few specific words that always appear ONLY in the header
# (Mtu/Link type/CPU freq/GID index/CQ Moderation) — the structural
# match alone would also strip lines like "remote address: ..."
# which are legitimate data, so we anchor on the bracket-or-keyword
# heuristic too.
_RE_PERFTEST_HEADER_LINE = re.compile(
    # Structural match for perftest's config-dump banner shape:
    # 1-5 short tokens (letters / digits / .*_/-), then a colon,
    # then a value. Header titles are short and verb-free; this
    # pattern fires on "Mtu : 1024[B]", "PCIe relax order: ON",
    # "ibv_wr* API : ON", "Data ex. method : Ethernet", etc.
    #
    # The negative anchor (_RE_PERFTEST_ERROR_HINT) below keeps
    # legitimate error lines that happen to look structurally
    # similar (e.g. "Status : Connection refused").
    r"^\s*[A-Za-z][A-Za-z0-9_./*-]*"
    r"(?:\s+[A-Za-z][A-Za-z0-9_./*-]*){0,4}"
    r"\s*:\s+\S",
)

# Negative anchor — lines that LOOK structurally like headers
# but actually carry the real diagnostic. perftest's failure
# messages reliably include one of these words; preserve them
# even if they superficially match a "Word : Value" shape.
_RE_PERFTEST_ERROR_HINT = re.compile(
    r"\b(?:error|fail|failed|couldn't|cannot|can't|unable|"
    r"refused|denied|invalid|no such|not found|timed? ?out|"
    r"closed|reset by peer|broken pipe|address (?:in use|already)|"
    r"resource temporarily unavailable)\b",
    re.IGNORECASE,
)


def _filter_perftest_noise(lines: List[str]) -> List[str]:
    """Drop perftest's config-dump 'Title : value' header lines so
    the rc!=0 error tail surfaces real diagnostics, not the banner.

    Lines containing operator-actionable hints
    (error/fail/couldn't/timeout/...) are always kept, even when
    they superficially look like header lines.
    """
    kept: List[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if _RE_PERFTEST_ERROR_HINT.search(line):
            kept.append(line)
            continue
        if _RE_PERFTEST_HEADER_LINE.match(line):
            continue
        kept.append(line)
    return kept


def _format_rc_error(rc: int, stdout_tail: List[str]) -> str:
    """Build the operator-visible `perftest exited rc=N: ...`
    message. v0.5.146: filter out the config-dump noise first; if
    nothing actionable remains, say so explicitly so the operator
    knows to look at the full log instead of squinting at header
    text."""
    filtered = _filter_perftest_noise(stdout_tail[-30:] if stdout_tail else [])
    if not filtered:
        return (
            f"perftest exited rc={rc} with no diagnostic on stdout/stderr "
            f"— check the full job log via "
            f"/api/rdma/perftest/job/<id>. Common causes: PFC/ECN "
            f"mismatch, wrong GID index, RoCEv2 disabled on the NIC, "
            f"or peer firewall blocking the perftest control TCP "
            f"port."
        )
    tail = "\n".join(filtered[-6:])
    return f"perftest exited rc={rc}: {tail[-400:]}"


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
                            # v0.5.177: In sweep mode (`-a -n N`)
                            # perftest emits one 9-col row per size.
                            # Accumulate each into final_lat_sweep so
                            # the GUI / report can draw the lat-vs-
                            # size curve. The final_lat_* scalars
                            # above keep getting overwritten with the
                            # last row (largest size) — backward
                            # compatible with the headline card and
                            # also a sensible default since 8 MB
                            # latency is what RDMA workloads actually
                            # care about.
                            if job.final_lat_sweep is None:
                                job.final_lat_sweep = []
                            p999 = m.group("p999")
                            job.final_lat_sweep.append({
                                "bytes": int(m.group("bytes")),
                                "iters": int(m.group("iters")),
                                "lat_min_us": float(m.group("tmin")),
                                "lat_max_us": float(m.group("tmax")),
                                "lat_typ_us": float(m.group("ttyp")),
                                "lat_avg_us": float(m.group("tavg")),
                                "lat_stdev_us": float(m.group("tstdev")),
                                "lat_p99_us": (float(p99)
                                               if p99 else None),
                                "lat_p999_us": (float(p999)
                                                if p999 else None),
                            })
                        except (TypeError, ValueError):
                            pass
                else:
                    # v0.5.177: fall back to the 4-column duration-mode
                    # format. perftest emits only bytes/iters/t_avg/tps
                    # when invoked with -D N (which the Blast dialog
                    # always does). Without this branch, every
                    # send_lat / read_lat / write_lat run from the GUI
                    # left every final_lat_* field as None.
                    m = _RE_LAT_DATA_ROW_DURATION.match(line)
                    if m:
                        with _jobs_lock:
                            try:
                                job.final_msg_size_bytes = int(
                                    m.group("bytes"))
                                job.final_iterations = int(
                                    m.group("iters"))
                                job.final_lat_avg_us = float(
                                    m.group("tavg"))
                                # min/max/p99 stay None — perftest
                                # didn't emit them in duration mode.
                            except (TypeError, ValueError):
                                pass
            else:
                m = _RE_BW_DATA_ROW.match(line)
                if m:
                    with _jobs_lock:
                        try:
                            job.final_msg_size_bytes = int(m.group("bytes"))
                            job.final_iterations = int(m.group("iters"))
                            # v0.5.176: peak column may be 'N/A' / '-'
                            # for ib_read_bw on some perftest builds —
                            # treat that as None rather than crashing
                            # the whole match (which would wipe avg +
                            # msgrate too).
                            peak_raw = m.group("peak")
                            try:
                                job.final_bw_peak_gbps = float(peak_raw)
                            except (TypeError, ValueError):
                                job.final_bw_peak_gbps = None
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
                # v0.5.146: surface the real failure reason. Was:
                # `tail = stdout_tail[-10:]` which on fast-failing
                # perftest is the config-dump banner ("Mtu : 1024
                # [B] Link type : Ethernet CPU freq : 2394 [MHz] …"
                # — exactly what the operator screenshot showed).
                job.error = _format_rc_error(rc, job.stdout_tail)


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

    # v0.5.155: CPU + NUMA pinning for parallel-worker BW scaling.
    # Layer 2 (cpu_pin) wraps perftest in `taskset -c <N>`.
    # Layer 3 (numa_pin) additionally wraps in
    # `numactl --cpunodebind=<N> --membind=<N>` so the worker's
    # CPU, RAM, AND its HCA's NUMA node all align — eliminates the
    # cross-NUMA penalty from v0.5.131. The two prefixes compose
    # left-to-right: numactl → taskset → perftest.
    cpu_pin = opts.get("cpu_pin")
    numa_pin = opts.get("numa_pin")
    if numa_pin is not None:
        try:
            cmd = [
                "numactl",
                f"--cpunodebind={int(numa_pin)}",
                f"--membind={int(numa_pin)}",
                "--",
            ] + cmd
        except (TypeError, ValueError):
            pass
    if cpu_pin is not None:
        try:
            cmd = ["taskset", "-c", str(int(cpu_pin))] + cmd
        except (TypeError, ValueError):
            pass

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
