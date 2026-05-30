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
        "kind": "type2",
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
    # 0.2.66: the registry now mixes kinds (type2 + type5). Defensively
    # refuse to clear a type-5 record with the type-2 cleaner — would
    # build the wrong commands and leak the kernel state. Put it back
    # so /api/evpn/type5/clear can pick it up.
    if rec.get("kind") not in (None, "type2"):
        with _INJ_LOCK:
            _INJECTIONS[inject_id] = rec
        return {"inject_id": inject_id, "ok_count": 0,
                "failed_count": 0, "errors": [],
                "warning": f"inject_id is a {rec['kind']} record — call "
                           "the matching /api/evpn/{kind}/clear instead"}
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
    powers the /api/evpn/type2/list route + the GUI table.

    Kind-aware as of 0.2.66: each entry carries ``kind`` (``"type2"``
    or ``"type5"``) plus the protocol-specific summary fields. Old
    type-2-only callers can keep keying off ``iface`` / ``count``
    unchanged.
    """
    out = []
    with _INJ_LOCK:
        for iid, rec in _INJECTIONS.items():
            kind = rec.get("kind", "type2")
            if kind == "type5":
                out.append({
                    "inject_id": iid,
                    "kind": "type5",
                    "vrf_table": rec.get("vrf_table"),
                    "dev": rec.get("dev"),
                    "gateway": rec.get("gateway"),
                    "count": len(rec.get("prefixes") or []),
                    # Cross-kind convenience aliases so a single GUI
                    # column can render either: iface ≈ dev, l3_iface
                    # n/a. Keeps the v0.2.63 EVPN dialog table from
                    # breaking when type-5 rows appear.
                    "iface": rec.get("dev"),
                    "l3_iface": None,
                    "remote_vtep_ip": None,
                })
            else:
                out.append({
                    "inject_id": iid,
                    "kind": "type2",
                    "iface": rec.get("iface"),
                    "l3_iface": rec.get("l3_iface"),
                    "remote_vtep_ip": rec.get("remote_vtep_ip"),
                    "count": len(rec.get("entries") or []),
                })
    return out


def _reset_registry_for_tests():
    """Clear the in-process registry. Test-only — never call from
    production code; an explicit clear_type{2,5}() is the right path."""
    with _INJ_LOCK:
        _INJECTIONS.clear()


# ──────────────────────────────────────────────────────── Type-5 (0.2.66)
# Type-5 = IP Prefix route. A VTEP (or a router with FRR's
# `address-family l2vpn evpn` + `advertise ipv4 unicast`) advertises a
# routed prefix into the EVPN address-family — common in EVPN-VXLAN
# fabrics for inter-VRF routing. The kernel-side injection path is to
# add the prefix to a VRF's routing table (so FRR/zebra picks it up
# and BGP advertises). This module just builds + runs the `ip route
# add/del` commands; the FRR-side `advertise ipv4 unicast` config is
# assumed to already be in place.


def generate_prefix_range_v4(base_prefix: str, prefix_len: int,
                             count: int) -> List[str]:
    """Return ``count`` consecutive IPv4 prefixes, each ``/prefix_len``,
    starting at ``base_prefix`` (e.g. ``"10.100.0.0"``).

    Each successive prefix is one host-block away — i.e. the network
    address advances by ``2**(32-prefix_len)``. Useful for scaled EVPN
    Type-5 tests: a hundred ``/24``s starting at ``10.100.0.0`` ->
    ``10.100.0.0/24``, ``10.100.1.0/24``, …, ``10.100.99.0/24``.
    """
    if count <= 0:
        return []
    if not 1 <= int(prefix_len) <= 32:
        raise ValueError(f"prefix_len must be 1..32, got {prefix_len!r}")
    step = 1 << (32 - int(prefix_len))
    start = int(ipaddress.IPv4Address(base_prefix))
    if start % step != 0:
        raise ValueError(
            f"base_prefix {base_prefix!r} is not aligned to /{prefix_len} "
            f"boundary (off by {start % step} addresses)"
        )
    return [
        f"{ipaddress.IPv4Address(start + i * step)}/{prefix_len}"
        for i in range(count)
    ]


def build_route_inject_commands(
    prefixes: Sequence[str],
    dev: str,
    gateway: Optional[str] = None,
    vrf_table: Optional[int] = None,
) -> List[List[str]]:
    """One ``ip route add`` per prefix.

    * ``gateway`` (when set) becomes ``via <gateway>`` — common when
      the prefix sits behind a remote next-hop; omit for directly-
      attached prefixes.
    * ``vrf_table`` (when set) appends ``table <id>`` — required when
      the FRR VRF maps to a kernel routing table other than ``main``.
    """
    cmds: List[List[str]] = []
    for pfx in prefixes:
        argv = ["ip", "route", "add", pfx]
        if gateway:
            argv.extend(["via", gateway])
        argv.extend(["dev", dev])
        if vrf_table is not None:
            argv.extend(["table", str(int(vrf_table))])
        cmds.append(argv)
    return cmds


def build_route_clear_commands(
    prefixes: Sequence[str],
    dev: Optional[str] = None,
    vrf_table: Optional[int] = None,
) -> List[List[str]]:
    """One ``ip route del`` per prefix. ``dev`` is optional on delete —
    the kernel matches by prefix + table; ``via`` is intentionally NOT
    included (kernel matches without it). ``vrf_table`` (when set)
    appends ``table <id>`` so the right table's route is removed."""
    cmds: List[List[str]] = []
    for pfx in prefixes:
        argv = ["ip", "route", "del", pfx]
        if dev:
            argv.extend(["dev", dev])
        if vrf_table is not None:
            argv.extend(["table", str(int(vrf_table))])
        cmds.append(argv)
    return cmds


def inject_type5(
    dev: str,
    base_prefix: str,
    prefix_len: int,
    count: int,
    gateway: Optional[str] = None,
    vrf_table: Optional[int] = None,
    run: Callable[[List[str]], subprocess.CompletedProcess] = _default_run,
) -> dict:
    """Inject ``count`` consecutive IP prefixes as kernel routes.

    Counterpart of :func:`inject_type2` for EVPN Type-5. Returns the
    same-shaped result dict (``inject_id``, ``ok_count``, ``failed_count``,
    ``errors``, plus the generated ``prefixes`` list and the request
    fields echoed back).

    All validation lives in :func:`generate_prefix_range_v4` —
    misaligned ``base_prefix`` or out-of-range ``prefix_len`` raise
    ``ValueError`` (the Flask route returns 400).
    """
    if count <= 0:
        raise ValueError("count must be > 0")
    prefixes = generate_prefix_range_v4(base_prefix, prefix_len, count)
    cmds = build_route_inject_commands(prefixes, dev, gateway, vrf_table)

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
        "kind": "type5",
        "dev": dev,
        "gateway": gateway,
        "vrf_table": vrf_table,
        "prefixes": prefixes,
    }
    with _INJ_LOCK:
        _INJECTIONS[inject_id] = record
    return {
        "inject_id": inject_id,
        "kind": "type5",
        "dev": dev,
        "count": count,
        "ok_count": ok,
        "failed_count": len(errors),
        "prefixes": list(prefixes),
        "errors": errors,
    }


def clear_type5(
    inject_id: str,
    run: Callable[[List[str]], subprocess.CompletedProcess] = _default_run,
) -> dict:
    """Remove every prefix from a previous :func:`inject_type5` call.

    Same best-effort contract as :func:`clear_type2`: kernel "no such
    route" errors are surfaced under ``errors`` but the call succeeds
    and the in-process record is dropped. Refuses to clear a type-2
    record (puts it back) so the caller can route through the right
    cleaner."""
    with _INJ_LOCK:
        rec = _INJECTIONS.pop(inject_id, None)
    if rec is None:
        return {"inject_id": inject_id, "ok_count": 0,
                "failed_count": 0, "errors": [],
                "warning": "unknown inject_id (already cleared or "
                           "server restarted)"}
    if rec.get("kind") != "type5":
        with _INJ_LOCK:
            _INJECTIONS[inject_id] = rec
        return {"inject_id": inject_id, "ok_count": 0,
                "failed_count": 0, "errors": [],
                "warning": f"inject_id is a {rec.get('kind', 'type2')} "
                           "record — call /api/evpn/type2/clear instead"}
    cmds = build_route_clear_commands(
        rec.get("prefixes") or [],
        dev=rec.get("dev"),
        vrf_table=rec.get("vrf_table"),
    )
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
