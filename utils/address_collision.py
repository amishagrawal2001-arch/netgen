"""Duplicate address detection across devices.

Duplicate loopback IPs are catastrophic for OSPF: FRR derives the OSPF
router-id from the loopback IPv4, and two speakers on the same segment
with the same router-id cannot form adjacency (they read each other's
Hellos and interpret them as their own, staying in Init forever). BGP
survives router-id collisions because TCP + peer-IP + AS uniquely
identify a session, so the mode failure is OSPF v4/v6 down + BGP up
even though the underlying cause is a duplicate address.

This module is used from two places:

- run_tgen_server.py — the /api/device/apply gate returns HTTP 409
  when the incoming payload would collide with an existing device.
  Hard backstop even if the client is out of date.

- widgets/add_device_dialog.py — pre-fills the loopback fields with
  the next-available value on Add, and warns before Save if the user
  edits into a collision.

The interface IP / MAC fields are checked per (interface, vlan_id)
tuple since same L2 segment = same broadcast domain. The gateway is
never checked — multiple devices deliberately share one gateway.
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, Optional, Tuple


GLOBAL_FIELDS = ("loopback_ipv4", "loopback_ipv6")
L2_SCOPED_FIELDS = ("ipv4_address", "ipv6_address", "mac_address")


def _canonical(field: str, value: str) -> Optional[str]:
    """Normalize a value so "192.168.0.1" == "192.168.000.001" and
    MAC "AA:BB:cc:DD:EE:FF" == "aa:bb:cc:dd:ee:ff". Returns None if
    the value is empty or unparseable — callers treat None as "no
    value to compare"."""
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        if field in ("loopback_ipv4", "ipv4_address"):
            return str(ipaddress.IPv4Address(v))
        if field in ("loopback_ipv6", "ipv6_address"):
            return str(ipaddress.IPv6Address(v))
        if field == "mac_address":
            return v.lower()
    except (ipaddress.AddressValueError, ValueError):
        return None
    return v


def _same_l2_segment(dev: dict, interface: str, vlan_id) -> bool:
    """True when `dev` sits on the same L2 broadcast domain as
    (interface, vlan_id) — i.e. same base interface AND same VLAN tag.
    A device with interface="vlan200@ens2f0np0" is normalized to base
    "ens2f0np0" so the display-form doesn't mask a real collision."""
    peer_iface = str(dev.get("interface") or "").strip()
    if "@" in peer_iface:
        peer_iface = peer_iface.split("@", 1)[-1]
    if peer_iface != (interface or "").strip():
        return False
    peer_vlan = str(dev.get("vlan") or dev.get("vlan_id") or "0").strip() or "0"
    my_vlan = str(vlan_id or "0").strip() or "0"
    return peer_vlan == my_vlan


def find_conflict(
    field: str,
    value: str,
    devices: Iterable[dict],
    exclude_id: Optional[str] = None,
    interface: Optional[str] = None,
    vlan_id=None,
) -> Optional[Tuple[str, str]]:
    """Return (device_id, device_name) of the first existing device
    that already uses `value` for `field`, or None if no collision.

    For loopback_ipv4/ipv6 the scope is global. For ipv4_address,
    ipv6_address, mac_address the scope is per (interface, vlan_id) —
    callers must pass both; without them the L2-scoped check returns
    None (nothing to compare against).
    """
    canon = _canonical(field, value)
    if canon is None:
        return None
    l2_scoped = field in L2_SCOPED_FIELDS
    if l2_scoped and not interface:
        return None
    for dev in devices or ():
        if exclude_id and dev.get("device_id") == exclude_id:
            continue
        peer_val = _canonical(field, dev.get(field) or "")
        if peer_val != canon:
            continue
        if l2_scoped and not _same_l2_segment(dev, interface, vlan_id):
            continue
        return (dev.get("device_id") or "", dev.get("device_name") or "")
    return None


def find_iface_vlan_conflict(
    interface: str,
    vlan_id,
    devices: Iterable[dict],
    exclude_id: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """Return (device_id, device_name) of an existing device already
    bound to the same (interface, vlan_id) tuple, or None.

    Two devices sharing the same physical NIC MUST use different
    VLAN tags — each ends up on its own vlan<N> subinterface which
    can then be moved into its own Linux VRF for protocol isolation.
    Two devices on the exact same (interface, vlan) would share the
    same L2/L3 segment and collide on TCP/179 (BGP), OSPF raw
    sockets, and ISIS PF_PACKET binds. The server already gates on
    this at run_tgen_server.py:4265-4301 (HTTP 409); this helper is
    the client-side companion so the operator sees the error before
    the round-trip. Both sides normalize the `vlanNN@base` display
    form to bare `base` before comparing, so cached devices with
    the display-form interface don't mask a real collision.
    """
    iface_norm = _normalize_base_iface(interface)
    if not iface_norm:
        return None
    vlan_norm = str(vlan_id or "0").strip() or "0"
    for dev in devices or ():
        if exclude_id and dev.get("device_id") == exclude_id:
            continue
        peer_iface = _normalize_base_iface(dev.get("interface") or "")
        if peer_iface != iface_norm:
            continue
        peer_vlan = str(
            dev.get("vlan") or dev.get("vlan_id") or "0"
        ).strip() or "0"
        if peer_vlan != vlan_norm:
            continue
        return (dev.get("device_id") or "", dev.get("device_name") or "")
    return None


def _normalize_base_iface(iface: str) -> str:
    """Strip UI display prefixes ("TG 0 - Port: X") and vlan-alias
    prefixes ("vlanNN@X") down to the bare kernel interface name."""
    if not iface:
        return ""
    s = str(iface).strip().strip('"').rstrip(",")
    if " - " in s:
        s = s.split(" - ", 1)[-1].strip()
    if ":" in s:
        s = s.rsplit(":", 1)[-1].strip()
    if "@" in s:
        s = s.split("@", 1)[-1].strip()
    parts = s.split()
    return parts[-1] if parts else ""


def _used_ipv4(devices: Iterable[dict], field: str) -> set:
    used = set()
    for dev in devices or ():
        v = dev.get(field)
        if not v:
            continue
        try:
            used.add(int(ipaddress.IPv4Address(str(v).strip())))
        except (ipaddress.AddressValueError, ValueError):
            pass
    return used


def _used_ipv6(devices: Iterable[dict], field: str) -> set:
    used = set()
    for dev in devices or ():
        v = dev.get(field)
        if not v:
            continue
        try:
            used.add(int(ipaddress.IPv6Address(str(v).strip())))
        except (ipaddress.AddressValueError, ValueError):
            pass
    return used


def next_available_loopback_ipv4(
    devices: Iterable[dict], start: str = "192.255.0.1"
) -> str:
    """Return the lowest unused loopback IPv4 at or above `start`.

    - No existing devices → return `start` verbatim.
    - Some existing devices → return max(used) + 1, skipping .0 and
      .255 host octets (both are reserved for network/broadcast in
      the classic sense and confuse some kernels' route validation).

    Never returns a value already in `devices`; if the counter
    overflows 255.255.255.254 we fall back to `start` rather than
    generating an invalid address (the operator will see the collision
    on Save and can pick their own value).
    """
    used = _used_ipv4(devices, "loopback_ipv4")
    if not used:
        return start
    try:
        start_int = int(ipaddress.IPv4Address(start.strip()))
    except (ipaddress.AddressValueError, ValueError):
        start_int = int(ipaddress.IPv4Address("192.255.0.1"))
    candidate = max(max(used) + 1, start_int)
    max_ipv4 = (1 << 32) - 1
    while candidate <= max_ipv4:
        if (candidate & 0xff) in (0, 255):
            candidate += 1
            continue
        if candidate in used:
            candidate += 1
            continue
        return str(ipaddress.IPv4Address(candidate))
    return start


def next_available_loopback_ipv6(
    devices: Iterable[dict], start: str = "2001:ff00::1"
) -> str:
    """Return the lowest unused loopback IPv6 at or above `start`.

    IPv6 has no equivalent of the .0/.255 host-octet oddness, so the
    walk is a plain max+1.
    """
    used = _used_ipv6(devices, "loopback_ipv6")
    if not used:
        return start
    try:
        start_int = int(ipaddress.IPv6Address(start.strip()))
    except (ipaddress.AddressValueError, ValueError):
        start_int = int(ipaddress.IPv6Address("2001:ff00::1"))
    candidate = max(max(used) + 1, start_int)
    max_ipv6 = (1 << 128) - 1
    while candidate <= max_ipv6:
        if candidate in used:
            candidate += 1
            continue
        return str(ipaddress.IPv6Address(candidate))
    return start
