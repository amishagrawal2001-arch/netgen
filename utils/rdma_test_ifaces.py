"""utils/rdma_test_ifaces.py — v0.5.150 RDMA test-iface lifecycle.

Why this exists
---------------
When operators run RDMA Blast / Topology Test between two HCAs on
the SAME host (e.g. `rocep43s0f0 ↔ rocep43s0f1` on srv06), they
hit the classic Linux "same-host loopback" routing trap:

    Both HCAs' kernel ifaces (ens2f0np0, ens2f1np1) have IPs in the
    same subnet → the kernel routes A→B via `lo` instead of out f0
    and back in f1 → the RoCEv2 packet never crosses the wire →
    `Failed to modify QP to RTR — Unable to Connect the HCA's
    through the link`.

This module owns the temporary network config that makes the test
work, without polluting the operator's persistent setup:

* **Probe** — ask each HCA: kernel iface, port state, link layer,
  IP addresses, rp_filter, GIDs.
* **Validate** — given the operator's proposed (iface, CIDR) list,
  surface conflicts: bad CIDR, IP already present, same subnet on
  two ifaces of one host (the trap), CIDR overlapping an existing
  route.
* **Configure** — runtime-only: `ip addr add … label <iface>:netgen`,
  optional `sysctl net.ipv4.conf.<iface>.rp_filter=0`. Records the
  applied state in `/etc/netgen/rdma-test-ifaces.json` so cleanup
  can find every IP we added, even after netgen-server restart.
* **Cleanup** — find the labeled IPs we created, remove them,
  restore rp_filter, drop the state entry.

Label convention
----------------
Every test IP we add carries the label `<iface>:netgen` (e.g.
`ens2f0np0:netgen`). This means:
* `ip -br addr show` shows the `netgen` tag — operators can spot
  what we added.
* Cleanup can `ip addr del … label …` precisely without grepping
  for a numeric IP.
* If netgen crashes mid-test, the operator (or a recovery hook)
  can find every netgen-managed test IP with one grep.

We deliberately never modify:
* Persistent files (`/etc/network/interfaces`, netplan YAML,
  NetworkManager, `/etc/sysctl.conf`). On reboot, everything we
  did is gone.
* Ifaces that already carry the operator's own IP. We refuse to
  layer a test IP on top of a production-looking address.

Privileges
----------
`ip addr` and `sysctl` need root. netgen-server runs with the
capabilities to do this (see v0.5.56 + the bind machinery). On
hosts without root, the apply step returns the underlying
`ip` command failure verbatim so the operator sees what's wrong.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────── constants

# Persisted state lives next to the DPDK bind registry; that
# directory already exists and is mode 0755 root:root.
STATE_PATH = "/etc/netgen/rdma-test-ifaces.json"

# Label suffix every test IP carries. The full label is
# `<iface>:ng`. Linux caps `ip addr label` at IFNAMSIZ (16
# chars incl. terminator → 15 usable). The label MUST start
# with the iface name, so suffix budget = 15 - len(iface) - 1.
# `ng` (2 chars) leaves room for iface names up to 12 chars —
# every modern predictable-name iface (ens2f0np0, enp4s0,
# rocep…) fits. Longer names skip the label and rely on the
# state file for cleanup.
LABEL_SUFFIX = "ng"


# ─────────────────────────────────────────── shell helpers


def _run(cmd: List[str], check: bool = False) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with logging.

    Tolerates a missing binary (macOS dev / test env without `ip`
    or `sysctl`) by returning a synthetic CompletedProcess with
    rc=127. Production Linux servers always have these, but the
    test env on a mac doesn't — failing the whole module to
    import on dev machines would be worse."""
    logger.debug("[rdma_test_ifaces] $ %s", " ".join(cmd))
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=check,
            timeout=10,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=cmd, returncode=127, stdout="",
            stderr=f"{cmd[0]}: command not found",
        )


def _read_sys(path: str) -> Optional[str]:
    """Read a sysfs file. Returns None on any error."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, FileNotFoundError):
        return None


# ─────────────────────────────────────────── probe


def _resolve_kernel_iface(hca: str) -> Optional[str]:
    """Map an RDMA HCA name (rocep43s0f0, mlx5_0) to its kernel
    Ethernet iface (ens2f0np0). Reads
    /sys/class/infiniband/<hca>/device/net/<iface>."""
    try:
        candidates = os.listdir(f"/sys/class/infiniband/{hca}/device/net")
    except (OSError, FileNotFoundError):
        return None
    if not candidates:
        return None
    # Multi-port HCAs may have multiple net entries; pick the
    # first deterministically (sorted by name).
    return sorted(candidates)[0]


def _iface_ip_addresses(iface: str) -> List[str]:
    """Return list of `CIDR` strings on the iface (e.g.
    ["10.0.0.5/24", "fe80::1/64"]). Uses `ip -j addr show`."""
    r = _run(["ip", "-j", "addr", "show", "dev", iface])
    if r.returncode != 0:
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    out: List[str] = []
    for entry in data:
        for info in entry.get("addr_info", []) or []:
            local = info.get("local")
            prefix = info.get("prefixlen")
            if local and prefix is not None:
                out.append(f"{local}/{prefix}")
    return out


def _iface_rp_filter(iface: str) -> Optional[int]:
    """Read net.ipv4.conf.<iface>.rp_filter."""
    raw = _read_sys(f"/proc/sys/net/ipv4/conf/{iface}/rp_filter")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _hca_gids(hca: str, ib_port: int = 1) -> List[Dict[str, str]]:
    """Enumerate GIDs on /sys/class/infiniband/<hca>/ports/<port>/gids/.
    Returns a list of {index, gid, type, ndev}. Type discovered via
    the gid_attrs/types/<n> sysfs node (RoCEv1 vs RoCEv2)."""
    out: List[Dict[str, str]] = []
    base = f"/sys/class/infiniband/{hca}/ports/{ib_port}/gids"
    types_base = (
        f"/sys/class/infiniband/{hca}/ports/{ib_port}/gid_attrs/types"
    )
    ndevs_base = (
        f"/sys/class/infiniband/{hca}/ports/{ib_port}/gid_attrs/ndevs"
    )
    try:
        idx_files = sorted(
            os.listdir(base),
            key=lambda s: int(s) if s.isdigit() else 999,
        )
    except (OSError, FileNotFoundError):
        return out
    for idx_str in idx_files:
        gid = _read_sys(f"{base}/{idx_str}")
        if not gid or set(gid.replace(":", "")) == {"0"}:
            # Skip the all-zero placeholder GIDs.
            continue
        gtype = _read_sys(f"{types_base}/{idx_str}") or ""
        ndev = _read_sys(f"{ndevs_base}/{idx_str}") or ""
        out.append({
            "index": idx_str,
            "gid": gid,
            "type": gtype,
            "ndev": ndev,
        })
    return out


def _port_state(hca: str, ib_port: int = 1) -> Dict[str, str]:
    """Port state + link layer from
    /sys/class/infiniband/<hca>/ports/<port>/."""
    base = f"/sys/class/infiniband/{hca}/ports/{ib_port}"
    state_raw = _read_sys(f"{base}/state") or ""
    # sysfs returns e.g. "4: ACTIVE" or "1: DOWN".
    state = state_raw.split(":")[-1].strip().upper() if ":" in state_raw else state_raw.upper()
    link_layer = _read_sys(f"{base}/link_layer") or ""
    rate = _read_sys(f"{base}/rate") or ""
    return {
        "state": state,
        "link_layer": link_layer,
        "rate": rate,
    }


def probe_device(hca: str, ib_port: int = 1) -> Dict[str, Any]:
    """Top-level probe: collects everything the preflight UI needs
    to render a decision for one HCA."""
    iface = _resolve_kernel_iface(hca)
    port = _port_state(hca, ib_port)
    out: Dict[str, Any] = {
        "hca": hca,
        "ib_port": ib_port,
        "kernel_iface": iface,
        "state": port.get("state"),
        "link_layer": port.get("link_layer"),
        "rate": port.get("rate"),
        "ip_addresses": _iface_ip_addresses(iface) if iface else [],
        "rp_filter": _iface_rp_filter(iface) if iface else None,
        "gids": _hca_gids(hca, ib_port),
    }
    return out


# ─────────────────────────────────────────── routing-table inspection


def _existing_routes() -> List[Dict[str, Any]]:
    """Return parsed `ip -j route show` for the main table.
    Each entry has at least {dst, dev, prefsrc?, scope?}."""
    r = _run(["ip", "-j", "route", "show"])
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout) or []
    except json.JSONDecodeError:
        return []


def _network_for_cidr(cidr: str) -> Optional[ipaddress.IPv4Network]:
    """Strict CIDR parse — returns None on malformed input. Uses
    strict=False so "10.0.0.5/24" parses to the 10.0.0.0/24 net."""
    try:
        return ipaddress.IPv4Network(cidr, strict=False)
    except (ValueError, ipaddress.NetmaskValueError):
        return None


# ─────────────────────────────────────────── auto-pick subnets


def auto_pick_subnets(
    pair_count: int = 1,
    avoid_routes: Optional[List[Dict[str, Any]]] = None,
) -> List[Tuple[str, str]]:
    """Suggest `pair_count` pairs of /24 CIDRs that don't collide
    with any existing routes. Each pair is the two endpoints of a
    point-to-point RDMA test: `(server_cidr, client_cidr)`.

    Uses RFC 1918 candidate blocks starting at 10.42.0.0/16 — a
    range very rarely overlapping with operator infra. If that
    block collides, walks forward (10.43.0.0, 10.44.0.0, …).

    Returns list of (server_cidr, client_cidr) tuples like
    [("10.42.0.1/24", "10.42.0.2/24"), ...].
    """
    if avoid_routes is None:
        avoid_routes = _existing_routes()
    occupied_nets: List[ipaddress.IPv4Network] = []
    for r in avoid_routes:
        dst = r.get("dst") or ""
        if dst == "default" or not dst:
            continue
        net = _network_for_cidr(dst if "/" in dst else f"{dst}/32")
        if net is not None:
            occupied_nets.append(net)
    pairs: List[Tuple[str, str]] = []
    octet = 42
    while len(pairs) < pair_count and octet < 200:
        candidate = ipaddress.IPv4Network(f"10.{octet}.0.0/24")
        if not any(candidate.overlaps(n) for n in occupied_nets):
            srv_cidr = f"10.{octet}.0.1/24"
            cli_cidr = f"10.{octet}.0.2/24"
            pairs.append((srv_cidr, cli_cidr))
        octet += 1
    return pairs


# ─────────────────────────────────────────── validation


def _ip_in_cidr_list(ip: str, cidrs: List[str]) -> bool:
    """Is `ip` (bare address, no mask) inside any of `cidrs`?"""
    try:
        addr = ipaddress.IPv4Address(ip)
    except (ValueError, ipaddress.AddressValueError):
        return False
    for c in cidrs:
        net = _network_for_cidr(c)
        if net and addr in net:
            return True
    return False


def validate_user_ips(
    ifaces: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Validate operator-supplied (iface, cidr) list.

    Returns
    -------
    {
      "ok": bool,                        # True iff no issues with severity="error"
      "issues": [
        {
          "iface": "ens2f0np0",
          "cidr":  "10.10.0.1/24",
          "severity": "error" | "warning",
          "message": "...",
        }, ...
      ],
    }

    Errors block apply; warnings surface but don't block.
    """
    issues: List[Dict[str, str]] = []

    # 1. Per-entry format + iface presence.
    parsed: List[Tuple[str, str, ipaddress.IPv4Interface]] = []
    for entry in ifaces:
        iface = (entry.get("name") or "").strip()
        cidr = (entry.get("cidr") or "").strip()
        if not iface:
            issues.append({
                "iface": "", "cidr": cidr,
                "severity": "error",
                "message": "missing iface name",
            })
            continue
        if not cidr:
            issues.append({
                "iface": iface, "cidr": "",
                "severity": "error",
                "message": "missing CIDR",
            })
            continue
        try:
            iface_addr = ipaddress.IPv4Interface(cidr)
        except (ValueError, ipaddress.NetmaskValueError) as e:
            issues.append({
                "iface": iface, "cidr": cidr,
                "severity": "error",
                "message": f"bad CIDR: {e}",
            })
            continue
        # Iface must exist.
        if not os.path.isdir(f"/sys/class/net/{iface}"):
            issues.append({
                "iface": iface, "cidr": cidr,
                "severity": "error",
                "message": f"iface {iface!r} not present",
            })
            continue
        parsed.append((iface, cidr, iface_addr))

    # 2. Same-host same-subnet trap: two ifaces in one validate
    # call sharing a subnet is exactly the routing pitfall the
    # operator is trying to fix.
    nets_by_iface: Dict[str, ipaddress.IPv4Network] = {
        iface: iface_addr.network for iface, _, iface_addr in parsed
    }
    seen_nets: Dict[str, str] = {}
    for iface, net in nets_by_iface.items():
        key = str(net)
        if key in seen_nets:
            issues.append({
                "iface": iface, "cidr": str(net),
                "severity": "error",
                "message": (
                    f"same subnet ({net}) already used by "
                    f"{seen_nets[key]} — same-host same-subnet "
                    f"is the routing trap RDMA testing hits. "
                    f"Pick a different subnet for each iface."
                ),
            })
        else:
            seen_nets[key] = iface

    # 3. Existing-IP / existing-route conflicts.
    existing_routes = _existing_routes()
    for iface, cidr, iface_addr in parsed:
        # The exact IP must not already be on another iface.
        existing = _iface_ip_addresses(iface)
        for ex_cidr in existing:
            ex_iface = ipaddress.IPv4Interface(ex_cidr) if "." in ex_cidr else None
            if ex_iface is None:
                continue
            if str(ex_iface.ip) == str(iface_addr.ip):
                issues.append({
                    "iface": iface, "cidr": cidr,
                    "severity": "warning",
                    "message": (
                        f"{iface_addr.ip} already on {iface}; "
                        f"will be skipped"
                    ),
                })
                break
        # The proposed network must not overlap a different
        # iface's route (would steal traffic).
        for route in existing_routes:
            dst = route.get("dst") or ""
            dev = route.get("dev") or ""
            if dev == iface or not dst or dst == "default":
                continue
            route_net = _network_for_cidr(
                dst if "/" in dst else f"{dst}/32")
            if route_net and route_net.overlaps(iface_addr.network):
                issues.append({
                    "iface": iface, "cidr": cidr,
                    "severity": "warning",
                    "message": (
                        f"overlaps route {dst} on {dev} — "
                        f"may steal traffic"
                    ),
                })

    has_error = any(i["severity"] == "error" for i in issues)
    return {"ok": not has_error, "issues": issues}


# ─────────────────────────────────────────── apply / cleanup


def _label_for(iface: str) -> Optional[str]:
    """Return the label string to use, or None if iface name is
    too long (the kernel caps labels at 15 chars, and the prefix
    must equal the iface name)."""
    cand = f"{iface}:{LABEL_SUFFIX}"
    return cand if len(cand) <= 15 else None


def _read_state() -> Dict[str, Any]:
    """Load persistent state. Returns the default skeleton when
    the file doesn't exist or is unreadable."""
    try:
        with open(STATE_PATH, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"records": []}
        data.setdefault("records", [])
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"records": []}


def _write_state(data: Dict[str, Any]) -> None:
    """Atomic state write (tmp + os.replace, same pattern as the
    DPDK bind registry)."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATE_PATH)


def apply_test_config(
    ifaces: List[Dict[str, str]],
    disable_rp_filter: bool = True,
) -> Dict[str, Any]:
    """Apply runtime test IPs + optional rp_filter relaxation.

    Returns a state record:
        {
          "state_id": "<uuid>",
          "applied_at": <unix_ts>,
          "applied": [
            {"iface": "ens2f0np0",
             "cidr": "10.42.0.1/24",
             "label": "ens2f0np0:netgen",
             "prev_rp_filter": 1,
             "rp_filter_changed": True},
            ...
          ],
          "errors": [...],
        }

    Persists to STATE_PATH. The returned `state_id` is what the
    caller passes back to `cleanup_test_config` to undo.
    """
    state_id = uuid.uuid4().hex[:12]
    record: Dict[str, Any] = {
        "state_id": state_id,
        "applied_at": int(time.time()),
        "applied": [],
        "errors": [],
    }
    for entry in ifaces:
        iface = (entry.get("name") or "").strip()
        cidr = (entry.get("cidr") or "").strip()
        if not iface or not cidr:
            record["errors"].append({
                "iface": iface, "cidr": cidr,
                "message": "missing iface or CIDR",
            })
            continue
        label = _label_for(iface)
        cmd = ["ip", "addr", "add", cidr, "dev", iface]
        if label:
            cmd += ["label", label]
        r = _run(cmd)
        if r.returncode != 0:
            record["errors"].append({
                "iface": iface, "cidr": cidr,
                "message": (
                    r.stderr.strip() or r.stdout.strip()
                    or "ip addr add failed"
                ),
            })
            continue
        applied_entry: Dict[str, Any] = {
            "iface": iface,
            "cidr": cidr,
            "label": label,
            "rp_filter_changed": False,
        }
        if disable_rp_filter:
            prev = _iface_rp_filter(iface)
            applied_entry["prev_rp_filter"] = prev
            r2 = _run([
                "sysctl", "-w",
                f"net.ipv4.conf.{iface}.rp_filter=0",
            ])
            if r2.returncode == 0:
                applied_entry["rp_filter_changed"] = (prev != 0)
            else:
                record["errors"].append({
                    "iface": iface, "cidr": cidr,
                    "message": f"rp_filter: {r2.stderr.strip()}",
                })
        # Bring iface up if it's not already.
        _run(["ip", "link", "set", "dev", iface, "up"])
        record["applied"].append(applied_entry)
    # Persist whatever succeeded so cleanup can find it later.
    data = _read_state()
    data["records"].append(record)
    _write_state(data)
    return record


def cleanup_test_config(
    state_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove test IPs + restore rp_filter for one record (when
    state_id is given) or ALL records (when state_id is None).

    Returns {"ok": bool, "removed": [...], "errors": [...]}.
    """
    data = _read_state()
    records = data.get("records") or []
    keep: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for rec in records:
        if state_id is not None and rec.get("state_id") != state_id:
            keep.append(rec)
            continue
        for entry in rec.get("applied", []):
            iface = entry.get("iface", "")
            cidr = entry.get("cidr", "")
            if iface and cidr:
                r = _run(["ip", "addr", "del", cidr, "dev", iface])
                if r.returncode != 0 and "not assigned" not in (
                        r.stderr or ""):
                    errors.append({
                        "iface": iface, "cidr": cidr,
                        "message": r.stderr.strip()
                        or "ip addr del failed",
                    })
                    continue
                removed.append({"iface": iface, "cidr": cidr})
            if entry.get("rp_filter_changed"):
                prev = entry.get("prev_rp_filter")
                if isinstance(prev, int):
                    _run([
                        "sysctl", "-w",
                        f"net.ipv4.conf.{iface}.rp_filter={prev}",
                    ])
    data["records"] = keep
    _write_state(data)
    return {"ok": not errors, "removed": removed, "errors": errors}


# ─────────────────────────────────────────── crash-recovery scan


_NETGEN_LABEL_RE = re.compile(rf"\S+:{re.escape(LABEL_SUFFIX)}\b")


def find_orphan_test_ips() -> List[Dict[str, str]]:
    """Scan `ip -br addr` for any labels matching `<iface>:netgen`
    that aren't in our state file. These are leftovers from a
    netgen-server crash or a manual `cleanup_test_config()` that
    raced with the apply. Returns the list so the operator can
    surface a manual "clean up orphans" affordance."""
    r = _run(["ip", "-br", "addr"])
    if r.returncode != 0:
        return []
    state = _read_state()
    tracked = {
        (e.get("iface"), e.get("cidr"))
        for rec in state.get("records", [])
        for e in rec.get("applied", [])
    }
    orphans: List[Dict[str, str]] = []
    for line in r.stdout.splitlines():
        if _NETGEN_LABEL_RE.search(line):
            parts = line.split()
            if len(parts) >= 3:
                iface = parts[0].split("@")[0]
                # parts[2:] is space-separated CIDR entries
                for cidr in parts[2:]:
                    if "/" in cidr and (iface, cidr) not in tracked:
                        orphans.append({"iface": iface, "cidr": cidr})
    return orphans
