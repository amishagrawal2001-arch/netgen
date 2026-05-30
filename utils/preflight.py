"""Pre-Apply device-config sanity checks.

Operators today click "Apply" and then debug failures after the fact —
BGP without a remote ASN, VXLAN missing a remote VTEP, two devices
fighting over the same IPv4, loopback unset when BGP needs a router-id.
The codebase has comments scattered around these exact bad-config
shapes (e.g. ``utils/bgp.py:1473`` logs ``"VXLAN config found but
missing required fields"``); this module surfaces them BEFORE the
Apply round-trip so the user sees a clear error / warning list.

Pure-function design: each ``check_*`` takes a device dict (and, for
cross-device checks, a list of all devices in the deployment) and
returns ``List[Finding]``. ``check_all_devices`` aggregates everything
into one report. The Flask route is a 5-line wrapper around
``check_all_devices``; the GUI integration lands in a follow-up slice.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


# Severity vocabulary kept small + stable so the GUI can render a
# consistent icon set for each level.
LEVEL_ERROR   = "error"
LEVEL_WARNING = "warning"


def _finding(level: str, code: str, message: str,
             device_name: Optional[str] = None,
             interface: Optional[str] = None) -> Dict[str, Any]:
    """One record in a preflight report.

    `code` is a short stable identifier (used by the GUI for filtering
    / tests for matching) — `message` is the human-readable text.
    `device_name` + `interface` are echoed so a single aggregated
    report can be sorted / grouped.
    """
    out: Dict[str, Any] = {"level": level, "code": code, "message": message}
    if device_name is not None:
        out["device_name"] = device_name
    if interface is not None:
        out["interface"] = interface
    return out


def _strip_cidr(ip: str) -> str:
    """``10.0.0.1/24`` → ``10.0.0.1``; tolerates already-bare addresses
    and falsy input."""
    if not ip:
        return ""
    return ip.split("/", 1)[0].strip()


def _device_label(device: Dict[str, Any]) -> str:
    """Human-readable identifier for a device — used in messages."""
    return (device.get("Device Name") or device.get("device_name")
            or device.get("device_id") or "(unnamed)")


def _interface_label(device: Dict[str, Any]) -> Optional[str]:
    return device.get("Interface") or device.get("interface")


# ──────────────────────────────────────────────────── single-device checks
def check_bgp_has_remote_asn(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """BGP enabled but bgp_config.bgp_remote_asn is missing → no session
    will ever form. Error-level (Apply will succeed but BGP stays
    Active forever)."""
    if "BGP" not in (device.get("protocols") or []):
        return []
    bgp = device.get("bgp_config") or {}
    if not bgp.get("bgp_remote_asn"):
        return [_finding(
            LEVEL_ERROR, "BGP_NO_REMOTE_ASN",
            "BGP is enabled but bgp_remote_asn is empty — no neighbour "
            "session will form.",
            device_name=_device_label(device),
            interface=_interface_label(device),
        )]
    return []


def check_bgp_has_loopback(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """BGP without a loopback IPv4 → FRR's router-id falls back to a
    transient interface address which makes routes unstable. Warning-
    level (it'll come up, just brittle)."""
    if "BGP" not in (device.get("protocols") or []):
        return []
    loopback = (device.get("Loopback IPv4") or
                device.get("loopback_ipv4") or "")
    if not _strip_cidr(loopback):
        return [_finding(
            LEVEL_WARNING, "BGP_NO_LOOPBACK",
            "BGP is enabled but Loopback IPv4 is empty — the router-id "
            "will be derived from a transient interface address.",
            device_name=_device_label(device),
            interface=_interface_label(device),
        )]
    return []


def check_vxlan_has_required_fields(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``utils/bgp.py:1473`` (the EVPN auto-config code) logs this same
    bad shape when it falls through. Catch it earlier so the operator
    sees a clear warning before Apply runs."""
    vxlan = device.get("vxlan_config")
    if not vxlan:
        return []
    # Accept either the flat {"vni": ..., "local_ip": ...} or the
    # tunnels-list {"tunnels": [...]} shape — both appear in the wild.
    if isinstance(vxlan, dict) and "tunnels" in vxlan:
        tunnels = vxlan.get("tunnels") or []
    elif isinstance(vxlan, dict) and vxlan.get("vni"):
        tunnels = [vxlan]
    else:
        tunnels = []
    if not tunnels:
        return [_finding(
            LEVEL_WARNING, "VXLAN_EMPTY",
            "vxlan_config present but contains no usable tunnels.",
            device_name=_device_label(device),
            interface=_interface_label(device),
        )]
    findings = []
    for t in tunnels:
        vni = t.get("vni")
        local = t.get("local_ip")
        remote = t.get("remote_ip")
        missing = [k for k, v in
                   (("vni", vni), ("local_ip", local), ("remote_ip", remote))
                   if not v]
        if missing:
            findings.append(_finding(
                LEVEL_ERROR, "VXLAN_MISSING_FIELDS",
                f"VXLAN tunnel is missing required field(s): "
                f"{', '.join(missing)} — FRR EVPN will skip it.",
                device_name=_device_label(device),
                interface=_interface_label(device),
            ))
    return findings


def check_isis_has_area(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """IS-IS without ``area_id`` → ``periodic_isis_status_check``
    silently auto-stops monitoring (see ``utils/devices_tab_isis.py``
    has_real_isis_config). Surface it explicitly."""
    if "IS-IS" not in (device.get("protocols") or []):
        return []
    isis = (device.get("isis_config") or
            device.get("is_is_config") or {})
    if not isinstance(isis, dict) or not isis.get("area_id"):
        return [_finding(
            LEVEL_WARNING, "ISIS_NO_AREA",
            "IS-IS is enabled but isis_config.area_id is empty — the "
            "monitor will auto-stop and no adjacency forms.",
            device_name=_device_label(device),
            interface=_interface_label(device),
        )]
    return []


def check_ospf_has_area(device: Dict[str, Any]) -> List[Dict[str, Any]]:
    """OSPF without an area_id usually fails silently — FRR rejects the
    config and the adjacency never forms."""
    if "OSPF" not in (device.get("protocols") or []):
        return []
    ospf = device.get("ospf_config") or {}
    if not isinstance(ospf, dict) or not ospf.get("area_id"):
        return [_finding(
            LEVEL_WARNING, "OSPF_NO_AREA",
            "OSPF is enabled but ospf_config.area_id is empty.",
            device_name=_device_label(device),
            interface=_interface_label(device),
        )]
    return []


# ──────────────────────────────────────────────────── cross-device checks
def check_duplicate_ipv4(device: Dict[str, Any],
                         other_devices: Iterable[Dict[str, Any]]
                         ) -> List[Dict[str, Any]]:
    """Two devices on the same broadcast domain configured with the
    same IPv4 → forwarding is wedged and ARP flaps. Cross-device check
    — passes ``device`` PLUS all other devices in the deployment."""
    my_ip = _strip_cidr(device.get("IPv4") or device.get("ipv4") or "")
    if not my_ip:
        return []
    me_label = _device_label(device)
    collisions = []
    for od in other_devices:
        if od is device:
            continue
        if _device_label(od) == me_label:
            # Same device record passed twice — defensive.
            continue
        other_ip = _strip_cidr(od.get("IPv4") or od.get("ipv4") or "")
        if other_ip and other_ip == my_ip:
            collisions.append(_device_label(od))
    if not collisions:
        return []
    return [_finding(
        LEVEL_ERROR, "DUPLICATE_IPV4",
        f"IPv4 {my_ip} is also configured on: {', '.join(sorted(collisions))} "
        f"— this will wedge forwarding and flap ARP.",
        device_name=me_label,
        interface=_interface_label(device),
    )]


# ─────────────────────────────────────────────────────────── aggregator
# Order = the order findings will appear in the report; single-device
# checks first so the cross-device section reads as "and also".
_SINGLE_DEVICE_CHECKS = (
    check_bgp_has_remote_asn,
    check_bgp_has_loopback,
    check_vxlan_has_required_fields,
    check_ospf_has_area,
    check_isis_has_area,
)

_CROSS_DEVICE_CHECKS = (
    check_duplicate_ipv4,
)


def check_all_devices(devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run every check against every device and return an aggregated
    report ready for ``jsonify``.

    Result shape:
      ``{"summary": {"error": n, "warning": n, "ok": n_devices_clean},
         "findings": [<finding>, ...],
         "by_device": {device_name: [<finding>, ...]}}``

    The summary lets the GUI render a quick badge ("3 errors, 2 warnings");
    `findings` is the flat list for a flat panel; `by_device` is for
    grouping in a table.
    """
    findings: List[Dict[str, Any]] = []
    for d in devices:
        for chk in _SINGLE_DEVICE_CHECKS:
            findings.extend(chk(d))
        for chk in _CROSS_DEVICE_CHECKS:
            findings.extend(chk(d, devices))

    by_device: Dict[str, List[Dict[str, Any]]] = {}
    for f in findings:
        by_device.setdefault(f.get("device_name", "(unknown)"), []).append(f)

    errors   = sum(1 for f in findings if f["level"] == LEVEL_ERROR)
    warnings = sum(1 for f in findings if f["level"] == LEVEL_WARNING)
    clean_devices = sum(1 for d in devices
                        if _device_label(d) not in by_device)

    return {
        "summary": {
            "error":   errors,
            "warning": warnings,
            "ok":      clean_devices,
            "total":   len(devices),
        },
        "findings":  findings,
        "by_device": by_device,
    }
