"""utils/rdma_host_info.py — v0.5.155 host topology probe.

Reads CPU count, NUMA topology, and per-HCA NUMA node mappings
from sysfs. Used by the Blast dialog's "Parallel workers" feature
to:
  * Determine the spinbox cap (= total CPU count).
  * Auto-pick NUMA-local cores when the operator clicks "🚀 Max BW"
    — same NUMA as the chosen HCA so the worker's CPU, RAM, and
    PCIe path all align (avoids the cross-NUMA penalty from
    v0.5.131).

All reads are sysfs-only — no subprocess. Returns sensible
defaults on hosts where the sysfs layout is missing (containers,
macOS dev, etc) so the route never raises.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, FileNotFoundError):
        return None


def _parse_cpu_list(s: str) -> List[int]:
    """Parse a Linux cpu-list expression like '0-3,8,10-12' into
    a sorted list of ints."""
    out: List[int] = []
    if not s:
        return out
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                out.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return sorted(set(out))


def _cpu_count() -> int:
    """Total online CPUs on this host. Prefers sysfs (matches
    `numactl --hardware` and what taskset accepts); falls back to
    `os.cpu_count()` which on Linux is the same number."""
    raw = _read_file("/sys/devices/system/cpu/online")
    if raw:
        cpus = _parse_cpu_list(raw)
        if cpus:
            return len(cpus)
    return os.cpu_count() or 1


def _numa_nodes() -> List[Dict[str, Any]]:
    """Enumerate every NUMA node: {node_id, cpus: [...]}.

    Returns a single synthetic node {node_id: 0, cpus: [0..N-1]}
    when /sys/devices/system/node/ is absent — keeps callers
    working on non-NUMA containers.
    """
    out: List[Dict[str, Any]] = []
    try:
        entries = sorted(os.listdir("/sys/devices/system/node"))
    except (OSError, FileNotFoundError):
        entries = []
    for entry in entries:
        m = re.match(r"^node(\d+)$", entry)
        if not m:
            continue
        node_id = int(m.group(1))
        cpulist = _read_file(
            f"/sys/devices/system/node/{entry}/cpulist") or ""
        out.append({
            "node_id": node_id,
            "cpus": _parse_cpu_list(cpulist),
        })
    if not out:
        # No NUMA visible — synthesize a flat node 0.
        out.append({
            "node_id": 0,
            "cpus": list(range(_cpu_count())),
        })
    return out


def _hca_numa() -> Dict[str, int]:
    """Map every RDMA HCA name → NUMA node id. Reads
    /sys/class/infiniband/<hca>/device/numa_node. Returns -1
    when the HCA exists but its sysfs node entry is missing or
    reads -1 (kernel for "unknown")."""
    out: Dict[str, int] = {}
    try:
        hcas = sorted(os.listdir("/sys/class/infiniband"))
    except (OSError, FileNotFoundError):
        return out
    for hca in hcas:
        raw = _read_file(
            f"/sys/class/infiniband/{hca}/device/numa_node")
        try:
            out[hca] = int(raw) if raw is not None else -1
        except ValueError:
            out[hca] = -1
    return out


def host_info() -> Dict[str, Any]:
    """One-shot snapshot for `/api/rdma/host_info`.

    Shape:
      {
        "cpu_count": int,
        "numa_nodes": [{node_id: int, cpus: [int, ...]}, ...],
        "hca_numa":   {hca_name: node_id, ...}
      }
    """
    return {
        "cpu_count": _cpu_count(),
        "numa_nodes": _numa_nodes(),
        "hca_numa": _hca_numa(),
    }


def pick_workers_for_hca(
    hca: str,
    requested: Optional[int] = None,
    info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """v0.5.155 "🚀 Max BW" auto-picker.

    Returns:
      {
        "worker_count": int,
        "numa_pin": int | None,
        "cpus": [int, ...],       # per-worker CPU IDs (length =
                                  # worker_count)
        "reason": "..."           # human-readable rationale
      }

    Logic:
      1. Look up the HCA's NUMA node.
      2. If known + has CPUs → use those CPUs. worker_count =
         len(numa_cpus) capped at `requested` or 16 (a safe
         single-host upper bound; perftest scales linearly
         until you saturate the wire or PCIe).
      3. If unknown → use all CPUs, no numa_pin.
    """
    if info is None:
        info = host_info()
    hca_numa = info.get("hca_numa") or {}
    numa_node = hca_numa.get(hca)
    nodes = info.get("numa_nodes") or []

    chosen_cpus: List[int] = []
    chosen_numa: Optional[int] = None
    if numa_node is not None and numa_node >= 0:
        for n in nodes:
            if n.get("node_id") == numa_node:
                chosen_cpus = list(n.get("cpus") or [])
                chosen_numa = numa_node
                break
    if not chosen_cpus:
        # Fall back: every CPU, no NUMA pin (kernel decides).
        for n in nodes:
            chosen_cpus.extend(n.get("cpus") or [])
        chosen_cpus = sorted(set(chosen_cpus))

    cap = requested if requested is not None else 16
    cap = max(1, min(int(cap), 16, len(chosen_cpus) or 1))
    chosen_cpus = chosen_cpus[:cap]
    worker_count = len(chosen_cpus) or 1

    if chosen_numa is not None:
        reason = (
            f"{worker_count} worker(s) pinned to NUMA node "
            f"{chosen_numa} (HCA {hca}'s home node)."
        )
    elif numa_node == -1 or numa_node is None:
        reason = (
            f"HCA {hca}'s NUMA node is unknown; using "
            f"{worker_count} worker(s) on cores "
            f"{chosen_cpus[0]}..{chosen_cpus[-1]} without "
            f"NUMA pinning."
        )
    else:
        reason = (
            f"{worker_count} worker(s) on cores "
            f"{chosen_cpus[0]}..{chosen_cpus[-1]}."
        )

    return {
        "worker_count": worker_count,
        "numa_pin": chosen_numa,
        "cpus": chosen_cpus,
        "reason": reason,
    }
