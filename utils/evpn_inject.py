"""EVPN Type-2 (MAC/IP) bulk injection for scaled testing.

Real EVPN routers advertise Type-2 routes from MAC entries they learned
in the data plane. For traffic-generator-style testing we want to skip
that and *manufacture* N synthetic MAC (or MAC+IP) entries so the
device's BGP speaker advertises them as Type-2 routes — letting one
chassis pretend to be a VTEP with hundreds or thousands of endpoints.

The injection path uses kernel networking primitives:

* ``bridge fdb append <mac> dev <vxlanN> master self static dst <vtep>``
  populates the bridge FDB / VXLAN MAC table that zebra reads.
* ``ip neigh add <ip> lladdr <mac> dev <iface> nud noarp`` populates the
  IP-to-MAC ARP table for the MAC+IP Type-2 sub-route. Omitting the IP
  yields a MAC-only Type-2 (still valid; used by switches).

FRR's zebra picks both up and BGP advertises them under the existing
EVPN address-family that ``configure_bgp_for_device`` already enables
when a device has VXLAN config.

This module exports only **pure helpers** + a high-level entry point
(`inject_type2` / `clear_type2`) that takes an injectable ``run``
callable so the subprocess layer can be mocked in tests. The Flask
route in ``run_tgen_server.py`` is a thin wrapper that calls
``inject_type2`` and serialises the result.
"""

from __future__ import annotations

import ipaddress
import subprocess
import threading
import uuid
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# In-process registry of active injections so the clear-route can find
# them by inject_id. Protected by a lock — the Flask routes can be
# called from multiple worker threads.
_INJECTIONS: Dict[str, dict] = {}
_INJ_LOCK = threading.Lock()


# ───────────────────────────────────────────── MAC / IP range helpers
def mac_to_int(mac: str) -> int:
    """Parse a colon/dash-separated MAC into a 48-bit integer.

    Raises ``ValueError`` on malformed input — the Flask route's
    400-response handler relies on that, so don't paper over it.
    """
    parts = mac.replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"bad MAC (need 6 octets): {mac!r}")
    try:
        ns = [int(p, 16) for p in parts]
    except ValueError:
        raise ValueError(f"bad MAC (non-hex octet): {mac!r}")
    for n in ns:
        if not 0 <= n <= 0xff:
            raise ValueError(f"bad MAC (octet out of range): {mac!r}")
    n = 0
    for p in ns:
        n = (n << 8) | p
    return n


def int_to_mac(n: int) -> str:
    """Format a 48-bit integer as ``aa:bb:cc:dd:ee:ff``. Wraps past
    2^48 since the kernel will reject anything wider anyway."""
    n &= (1 << 48) - 1
    h = f"{n:012x}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def generate_mac_range(base_mac: str, count: int) -> List[str]:
    """Return ``count`` consecutive MACs starting at ``base_mac``."""
    if count <= 0:
        return []
    start = mac_to_int(base_mac)
    return [int_to_mac(start + i) for i in range(count)]


def generate_ip_range(base_ip: str, count: int) -> List[str]:
    """Return ``count`` consecutive IPv4 addresses starting at
    ``base_ip``. Wraps via :class:`ipaddress.IPv4Address` arithmetic,
    so callers see clear errors if the range overflows."""
    if count <= 0:
        return []
    start = int(ipaddress.IPv4Address(base_ip))
    return [str(ipaddress.IPv4Address(start + i)) for i in range(count)]


# ─────────────────────────────────────────────── command-list builders
def build_inject_commands(
    iface: str,
    entries: Sequence[Tuple[str, Optional[str]]],
    remote_vtep_ip: Optional[str] = None,
    l3_iface: Optional[str] = None,
) -> List[List[str]]:
    """Build the kernel commands needed to add the given (MAC, IP) entries.

    Each entry is ``(mac, ip_or_None)``. Returns one argv list per
    kernel command, in the order they should be executed. The caller
    (or :func:`inject_type2`) decides how to run them — keeping this
    function pure makes the command shape trivially testable.

    * ``iface`` — the VXLAN interface (e.g. ``vxlan100``); bridge FDB
      entries are added here.
    * ``remote_vtep_ip`` — when set, the FDB entry carries
      ``dst <vtep>`` so the MAC is associated with that remote VTEP
      (BGP advertisement carries this as the Type-2 next-hop).
    * ``l3_iface`` — interface where IP→MAC neigh entries are added
      (typically the SVI / bridge for that VNI). Falls back to ``iface``.
    """
    cmds: List[List[str]] = []
    neigh_iface = l3_iface or iface
    for mac, ip in entries:
        fdb = ["bridge", "fdb", "append", mac, "dev", iface,
               "master", "self", "static"]
        if remote_vtep_ip:
            fdb.extend(["dst", remote_vtep_ip])
        cmds.append(fdb)
        if ip:
            cmds.append(["ip", "neigh", "add", ip, "lladdr", mac,
                         "dev", neigh_iface, "nud", "noarp"])
    return cmds


def build_clear_commands(
    iface: str,
    entries: Sequence[Tuple[str, Optional[str]]],
    l3_iface: Optional[str] = None,
) -> List[List[str]]:
    """Inverse of :func:`build_inject_commands`. Removes the neigh
    entry first (depends on the FDB target still being there), then
    the FDB entry."""
    cmds: List[List[str]] = []
    neigh_iface = l3_iface or iface
    for mac, ip in entries:
        if ip:
            cmds.append(["ip", "neigh", "del", ip, "dev", neigh_iface])
        cmds.append(["bridge", "fdb", "del", mac, "dev", iface])
    return cmds


# ───────────────────────────────────────────────── high-level entry pts
def _default_run(argv: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=5)


def inject_type2(
    iface: str,
    base_mac: str,
    count: int,
    base_ip: Optional[str] = None,
    remote_vtep_ip: Optional[str] = None,
    l3_iface: Optional[str] = None,
    run: Callable[[List[str]], subprocess.CompletedProcess] = _default_run,
) -> dict:
    """Inject ``count`` synthetic Type-2 entries.

    Returns a result dict with:
      * ``inject_id``       — opaque token to pass to :func:`clear_type2`
      * ``iface``           — echoes the VXLAN interface
      * ``count``           — entries attempted
      * ``ok_count`` /
        ``failed_count``    — counts after running every command
      * ``entries``         — full list of (mac, ip) tuples (so the GUI
                               can preview / report)
      * ``errors``          — list of ``{cmd, returncode, stderr}`` for
                               each command that failed

    Even if some commands fail (e.g. duplicate FDB entry), the inject
    is still registered with whatever did land — clear_type2 will
    attempt removal of every entry and ignore "not found" errors.
    """
    if count <= 0:
        raise ValueError("count must be > 0")
    macs = generate_mac_range(base_mac, count)
    ips = generate_ip_range(base_ip, count) if base_ip else [None] * count
    entries: List[Tuple[str, Optional[str]]] = list(zip(macs, ips))
    cmds = build_inject_commands(iface, entries, remote_vtep_ip, l3_iface)

    errors = []
    ok = 0
    for argv in cmds:
        try:
            res = run(argv)
            if res.returncode != 0:
                errors.append({
                    "cmd": argv,
                    "returncode": res.returncode,
                    "stderr": (res.stderr or "")[:500],
                })
            else:
                ok += 1
        except Exception as exc:
            errors.append({
                "cmd": argv,
                "returncode": -1,
                "stderr": f"{type(exc).__name__}: {exc}"[:500],
            })

    inject_id = str(uuid.uuid4())
    record = {
        "iface": iface,
        "l3_iface": l3_iface,
        "remote_vtep_ip": remote_vtep_ip,
        "entries": entries,
    }
    with _INJ_LOCK:
        _INJECTIONS[inject_id] = record
    return {
        "inject_id": inject_id,
        "iface": iface,
        "count": count,
        "ok_count": ok,
        "failed_count": len(errors),
        "entries": [{"mac": m, "ip": i} for (m, i) in entries],
        "errors": errors,
    }


def clear_type2(
    inject_id: str,
    run: Callable[[List[str]], subprocess.CompletedProcess] = _default_run,
) -> dict:
    """Remove every entry from a previous :func:`inject_type2` call.

    Returns ``{inject_id, ok_count, failed_count, errors}``. "Entry
    not found" errors from the kernel are common (cleanup is
    best-effort) so they're surfaced in ``errors`` but the route
    succeeds — the in-process record is dropped either way."""
    with _INJ_LOCK:
        rec = _INJECTIONS.pop(inject_id, None)
    if rec is None:
        return {"inject_id": inject_id, "ok_count": 0,
                "failed_count": 0, "errors": [],
                "warning": "unknown inject_id (already cleared or "
                           "server restarted)"}
    cmds = build_clear_commands(rec["iface"], rec["entries"], rec.get("l3_iface"))
    errors = []
    ok = 0
    for argv in cmds:
        try:
            res = run(argv)
            if res.returncode != 0:
                errors.append({"cmd": argv,
                               "returncode": res.returncode,
                               "stderr": (res.stderr or "")[:500]})
            else:
                ok += 1
        except Exception as exc:
            errors.append({"cmd": argv, "returncode": -1,
                           "stderr": f"{type(exc).__name__}: {exc}"[:500]})
    return {"inject_id": inject_id, "ok_count": ok,
            "failed_count": len(errors), "errors": errors}


def list_active_injections() -> List[dict]:
    """Lightweight snapshot of currently-registered injections —
    powers a future /api/evpn/type2/list route + the GUI table."""
    with _INJ_LOCK:
        return [
            {"inject_id": iid, "iface": rec["iface"],
             "l3_iface": rec.get("l3_iface"),
             "remote_vtep_ip": rec.get("remote_vtep_ip"),
             "count": len(rec["entries"])}
            for iid, rec in _INJECTIONS.items()
        ]


def _reset_registry_for_tests():
    """Clear the in-process registry. Test-only — never call from
    production code; an explicit clear_type2() is the right path."""
    with _INJ_LOCK:
        _INJECTIONS.clear()
