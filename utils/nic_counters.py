"""Cross-binding RX/TX counter reader.

Problem this solves
-------------------
When a NIC is bound to vfio-pci for DPDK, the kernel netdev
disappears:

  $ ip link show ens2f0np0
  Device "ens2f0np0" does not exist.
  $ ethtool -S ens2f0np0
  netlink error: No such device

This is the srv06 case (Jun 11 2026): TX was working at the
PMD level but the operator had no way to confirm packets
were leaving the wire — every kernel-level diag tool was
blind.

Three observation paths exist; this module exposes a unified
"give me counters for iface X" API that picks the right one:

1. Kernel /sys/class/net/<iface>/statistics/
   - Works when iface is kernel-bound (mlx5_core, i40e, etc.)
   - Universal across NIC vendors
   - Disappears when iface is vfio-bound

2. Mellanox InfiniBand sysfs counters at
   /sys/class/infiniband/mlx5_N/ports/1/counters/
   - Works EVEN when the iface is vfio-bound
   - ConnectX-4 and later (the bifurcated PMD path)
   - Hardware-level counters; same chip exposes them to both
     the kernel and the InfiniBand stack
   - Resolved from PCI BDF (which the iface name doesn't have
     when vfio-bound) via /sys/bus/pci/devices/<bdf>/infiniband/

3. DPDK telemetry socket
   - At /var/run/dpdk/<file-prefix>/dpdk_telemetry.v2
   - Only works while a DPDK app (tx_worker) has the port open
   - Per-port `ipackets`/`opackets`/`ierrors`/`oerrors`/`rx_nombuf`
   - Source of truth for what the PMD ACTUALLY did vs what
     the wire shows
   - Future: wire this in once we agree on the socket naming
     convention with tx_worker

For srv06 (ConnectX-6 Mellanox) the sysfs Mellanox path is
the answer. For Intel/Broadcom DPDK ports there's no
equivalent — the only path is DPDK telemetry; sysfs paths 1
& 2 return None.

Returned shape — same for all paths:

    {
        "source": "kernel" | "mellanox-sysfs" | "dpdk-telemetry" | None,
        "iface": "ens2f0np0",
        "pci_bdf": "0000:2b:00.0" | None,
        "binding": "kernel" | "vfio-pci" | "unknown",
        "ts_ns": <int from time.monotonic_ns>,
        "rx_packets": int | None,
        "tx_packets": int | None,
        "rx_bytes": int | None,
        "tx_bytes": int | None,
        "rx_errors": int | None,
        "tx_errors": int | None,
        "rx_dropped": int | None,
        "tx_dropped": int | None,
        "extra": { ... raw counter dict, source-specific ... },
        "warnings": ["..."],  # operator-visible (e.g. "iface vfio-bound,
                              #   using Mellanox sysfs")
    }

Two samples + a delta gives you pps. The HTTP endpoint
returns ONE snapshot; the client polls and diffs.

Privacy: no MACs, IPs, or anything operator-identifiable are
read from counters — just integers. Safe to log and surface
in the admin UI without redaction.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger(__name__)


# ─── PCI ↔ iface ↔ infiniband resolution ───
#
# When an iface is kernel-bound:
#   /sys/class/net/<iface>/device → ../../bus/pci/devices/<bdf>
# When it's vfio-bound:
#   the iface name is gone, but the PCI device still has:
#   /sys/bus/pci/devices/<bdf>/infiniband/mlx5_N → ...
#   /sys/bus/pci/devices/<bdf>/net/<iface>      → only if kernel-bound
#
# So we resolve in both directions: iface → PCI for kernel
# state, and PCI → infiniband for the Mellanox sysfs path
# (which survives vfio bind).


def iface_to_pci_bdf(iface: str) -> Optional[str]:
    """Map iface name → PCI BDF (e.g. 0000:2b:00.0).

    Only works if the iface is currently kernel-bound. For
    vfio-bound interfaces, you need to remember the BDF from
    BEFORE the bind happened (or pass it explicitly into
    read_counters).
    """
    sym = f"/sys/class/net/{iface}/device"
    try:
        target = os.readlink(sym)
    except (OSError, FileNotFoundError):
        return None
    # Target is a relative path like "../../bus/pci/devices/0000:2b:00.0"
    bdf = os.path.basename(target)
    # Sanity check: BDF format is DDDD:BB:DD.F
    if len(bdf.split(":")) == 3 and "." in bdf:
        return bdf
    return None


def pci_bdf_to_infiniband(pci_bdf: str) -> Optional[str]:
    """Map PCI BDF → InfiniBand device name (e.g. mlx5_2).

    Works regardless of whether the iface is kernel-bound or
    vfio-bound — the InfiniBand link is at the PCI level."""
    ib_dir = f"/sys/bus/pci/devices/{pci_bdf}/infiniband"
    try:
        names = os.listdir(ib_dir)
    except (OSError, FileNotFoundError):
        return None
    # Take the first (single-port Mellanox = one device;
    # for dual-port cards there's typically still one IB
    # device per PF).
    return names[0] if names else None


def pci_bdf_binding(pci_bdf: str) -> str:
    """Return "kernel" | "vfio-pci" | "unknown" — what driver
    is currently bound to this PCI BDF."""
    drv_link = f"/sys/bus/pci/devices/{pci_bdf}/driver"
    try:
        target = os.readlink(drv_link)
    except (OSError, FileNotFoundError):
        return "unknown"
    drv_name = os.path.basename(target)
    if drv_name == "vfio-pci":
        return "vfio-pci"
    if drv_name in ("mlx5_core", "i40e", "ice", "ixgbe", "bnxt_en", "igb"):
        return "kernel"
    # Some less-common kernel drivers; treat as kernel anyway
    # (they expose /sys/class/net).
    return "kernel"


def is_mellanox_bifurcated_kernel(iface: str) -> bool:
    """v0.5.114: detect the srv06-class trap: a Mellanox iface
    bound to mlx5_core (kernel) but where DPDK PMD CAN claim the
    chip's RX queue via bifurcated mode.

    On this configuration, setting rx_engine="dpdk" reliably
    breaks RX counting:
      1. rx_worker grabs the chip's default RX queue via DPDK PMD
      2. Frames arriving at the chip get routed to rx_worker's
         DPDK queue → kernel netdev's queue gets nothing → kernel
         rx counter stops incrementing
      3. rx_worker dies (startup race we haven't root-caused)
      4. Chip flow steering still points at the dead worker's
         queue. Kernel stays blind even though chip is receiving
      5. Stream stats reads from rx_worker registry → reports 0
         RX forever, even at line-rate ingress

    This is documented in project_srv06_rx_worker_blindness. The
    proper fix is rte_flow rules in rx_worker.c (deferred to a
    future release). The pragmatic workaround is to default
    rx_engine to "scapy" on these NICs.

    Detection rule: driver = mlx5_core AND infiniband device
    exists (mlx5 / ConnectX series). Other vendors are not
    affected.
    """
    pci_bdf = iface_to_pci_bdf(iface)
    if not pci_bdf:
        return False
    drv_link = f"/sys/bus/pci/devices/{pci_bdf}/driver"
    try:
        target = os.readlink(drv_link)
        drv_name = os.path.basename(target)
    except (OSError, FileNotFoundError):
        return False
    if drv_name != "mlx5_core":
        return False
    # InfiniBand devnode existing under the PCI BDF confirms
    # Mellanox ConnectX-class — that's the chip family where
    # bifurcated PMD steals the RX queue from the kernel.
    ib_dir = f"/sys/bus/pci/devices/{pci_bdf}/infiniband"
    return os.path.isdir(ib_dir)


# ─── Counter readers — one per source path ───


def _read_int(path: str) -> Optional[int]:
    """Read a file expected to contain a single integer; return
    None on any failure (file missing, perm denied, parse
    error). Counters at /sys are 64-bit unsigned in decimal."""
    try:
        with open(path, "r") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError, FileNotFoundError):
        return None


def read_kernel_counters(iface: str) -> Optional[dict]:
    """Read /sys/class/net/<iface>/statistics/* — universal
    across NIC vendors. Returns None if iface doesn't exist
    in /sys/class/net (likely vfio-bound or unplugged).

    All values are uint64 cumulative since boot/iface up; the
    caller computes deltas across two samples."""
    base = f"/sys/class/net/{iface}/statistics"
    if not os.path.isdir(base):
        return None
    fields = (
        "rx_packets", "tx_packets",
        "rx_bytes", "tx_bytes",
        "rx_errors", "tx_errors",
        "rx_dropped", "tx_dropped",
        "rx_crc_errors", "rx_frame_errors", "rx_fifo_errors",
        "tx_carrier_errors", "tx_aborted_errors", "tx_fifo_errors",
        "multicast", "collisions",
    )
    out: dict[str, Any] = {}
    for f in fields:
        v = _read_int(f"{base}/{f}")
        if v is not None:
            out[f] = v
    return out if out else None


# Mapping from Mellanox InfiniBand sysfs counter names to our
# unified key set. The IB stack speaks "rcv/xmit", the kernel
# stack speaks "rx/tx"; map for the operator's sanity.
_MLX_RCV_TO_RX = {
    "port_rcv_packets":  "rx_packets",
    "port_rcv_data":     "rx_bytes",    # In OCTETS of 4-byte units
                                        # on older HW! Modern (CX-4+) is bytes.
    "port_xmit_packets": "tx_packets",
    "port_xmit_data":    "tx_bytes",
    "port_rcv_errors":   "rx_errors",
    "port_xmit_discards": "tx_dropped",
}


def read_mellanox_sysfs_counters(
    mlx_name: str, port: int = 1,
) -> Optional[dict]:
    """Read /sys/class/infiniband/<mlx_name>/ports/<port>/counters/*

    This path survives vfio-pci binding — the InfiniBand stack
    talks to the same chip the PMD is driving. Source of truth
    for "did packets actually reach the wire" on Mellanox NICs."""
    base = f"/sys/class/infiniband/{mlx_name}/ports/{port}/counters"
    if not os.path.isdir(base):
        return None
    out: dict[str, Any] = {}
    raw: dict[str, int] = {}
    try:
        names = os.listdir(base)
    except OSError:
        return None
    for n in names:
        v = _read_int(f"{base}/{n}")
        if v is None:
            continue
        raw[n] = v
        if n in _MLX_RCV_TO_RX:
            out[_MLX_RCV_TO_RX[n]] = v
    # Always include the raw counter dict for completeness —
    # operators occasionally want symbol_error, link_downed,
    # etc. that aren't in our unified set.
    if not out and not raw:
        return None
    out["_raw"] = raw
    return out


# ─── Top-level unified read ───


def read_counters(
    iface: str,
    *,
    pci_bdf: Optional[str] = None,
) -> dict:
    """Return a unified counter snapshot.

    iface: the iface name. May or may not exist in
        /sys/class/net (vfio-bound disappears).
    pci_bdf: optional PCI BDF. If iface is vfio-bound the
        caller should pass this — it's the only way to find
        the Mellanox InfiniBand sysfs path.

    Returns a dict shaped like the module docstring. Always
    succeeds — if no source is available, returns the dict
    with source=None and a 'warnings' entry explaining.
    """
    warnings: list[str] = []
    resolved_bdf = pci_bdf

    if resolved_bdf is None:
        resolved_bdf = iface_to_pci_bdf(iface)
        if resolved_bdf is None:
            warnings.append(
                f"could not resolve PCI BDF for {iface}; iface may "
                f"be vfio-bound — pass pci_bdf explicitly"
            )

    binding = pci_bdf_binding(resolved_bdf) if resolved_bdf else "unknown"

    snapshot: dict[str, Any] = {
        "source": None,
        "iface": iface,
        "pci_bdf": resolved_bdf,
        "binding": binding,
        "ts_ns": time.monotonic_ns(),
        "rx_packets": None, "tx_packets": None,
        "rx_bytes": None,   "tx_bytes": None,
        "rx_errors": None,  "tx_errors": None,
        "rx_dropped": None, "tx_dropped": None,
        "extra": {},
        "warnings": warnings,
    }

    # Path 1: kernel statistics. Works only when kernel-bound.
    kc = read_kernel_counters(iface)
    if kc is not None:
        snapshot["source"] = "kernel"
        for k in ("rx_packets", "tx_packets", "rx_bytes", "tx_bytes",
                  "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"):
            if k in kc:
                snapshot[k] = kc[k]
        snapshot["extra"]["kernel"] = kc
        return snapshot

    # Path 2: Mellanox InfiniBand sysfs. Works for vfio-bound
    # Mellanox NICs.
    if resolved_bdf:
        mlx_name = pci_bdf_to_infiniband(resolved_bdf)
        if mlx_name:
            mc = read_mellanox_sysfs_counters(mlx_name)
            if mc:
                snapshot["source"] = "mellanox-sysfs"
                snapshot["extra"]["infiniband_device"] = mlx_name
                for k in ("rx_packets", "tx_packets", "rx_bytes",
                          "tx_bytes", "rx_errors", "tx_dropped"):
                    if k in mc:
                        snapshot[k] = mc[k]
                snapshot["extra"]["mellanox_raw"] = mc.get("_raw", {})
                if binding == "vfio-pci":
                    warnings.append(
                        f"iface {iface} is vfio-bound; counters "
                        f"come from Mellanox InfiniBand sysfs "
                        f"({mlx_name}) — kernel netdev path is "
                        f"unavailable while DPDK has the port"
                    )
                return snapshot

    # No source worked. Return the empty shape with a clear
    # warning so the operator knows we couldn't read anything.
    if not warnings:
        warnings.append(
            f"no counter source available for {iface} — neither "
            f"kernel /sys/class/net nor Mellanox InfiniBand "
            f"sysfs returned data"
        )
    return snapshot


def compute_pps(
    prev: dict, current: dict,
) -> dict:
    """Compute packets-per-second + bps deltas between two
    snapshots from read_counters(). Skips any field that was
    None in either sample.

    Returns:
        {
            "rx_pps": float | None,
            "tx_pps": float | None,
            "rx_bps": float | None,
            "tx_bps": float | None,
            "elapsed_s": float,
            "deltas": { "rx_packets": ..., ... },
        }
    """
    elapsed_ns = current.get("ts_ns", 0) - prev.get("ts_ns", 0)
    if elapsed_ns <= 0:
        return {
            "rx_pps": None, "tx_pps": None,
            "rx_bps": None, "tx_bps": None,
            "elapsed_s": 0.0, "deltas": {},
        }
    elapsed_s = elapsed_ns / 1e9
    out: dict[str, Any] = {"elapsed_s": elapsed_s, "deltas": {}}
    for field, dst_key in (
        ("rx_packets", "rx_pps"),
        ("tx_packets", "tx_pps"),
        ("rx_bytes",   "rx_bps"),
        ("tx_bytes",   "tx_bps"),
    ):
        a, b = prev.get(field), current.get(field)
        if a is None or b is None:
            out[dst_key] = None
            continue
        delta = b - a
        if delta < 0:
            # Counter wrap (rare on 64-bit) or iface reset
            # mid-sample. Don't compute a bogus negative rate.
            out[dst_key] = None
            continue
        out["deltas"][field] = delta
        # Bytes → bits for bps semantics
        scale = 8.0 if dst_key.endswith("_bps") else 1.0
        out[dst_key] = (delta * scale) / elapsed_s
    return out
