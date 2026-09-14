"""Upstream-router config hints (Juniper / Cisco IOS / Arista EOS).

Every netgen device peers with SOMETHING on the wire — usually the
lab's top-of-rack switch or a router farther upstream. That peer
needs a matching interface stanza (VLAN, IPs), a matching BGP
neighbor block, and matching OSPF / IS-IS interface enablement.
Getting the syntax right across three vendors is where operators
lose 30 minutes copy-pasting from documentation. This module
generates the paste-ready snippet from the device's own config.

Usage:

    from utils.upstream_hints import render_all
    snippets = render_all(device_data)
    # snippets = {"juniper": "...", "cisco": "...", "arista": "..."}

The generator is intentionally conservative:
- Upstream physical iface is a placeholder (ge-0/0/0 / Gi0/0 / Et1)
  because netgen has no way to know it. Operator edits before paste.
- BGP mode picks internal vs external from ASN comparison.
- OSPFv2/v3 use the device's own area-id, and hello/dead intervals
  match what the device was configured with (so the adjacency
  actually forms — mismatched intervals are the classic OSPF trap).
- IS-IS NET is derived from the device's system-id when set, or
  synthesized from the loopback when not.
- DHCP-client devices emit a dhcp-relay stanza (v0.5.318): a
  netgen DHCP-client sitting on VLAN N can't reach a netgen
  DHCP-server on a different subnet without a dhcp-relay agent
  on the L3 gateway. The server-IP field is a placeholder (the
  client-side dialog doesn't know its own server's address) and
  the client-VLAN SVI is inferred from the device's VLAN
  (irb.<vlan> / Vlan<vlan>). Suppressed for DHCP-server + non-
  DHCP devices.

Callers get vendor-neutral empty strings for protocols the device
doesn't have enabled — the section header just doesn't appear.
"""

from __future__ import annotations

import copy
import ipaddress
from typing import Any, Dict, List, Optional, Union


# --- Small extractors ------------------------------------------------------

def _first(*vals) -> str:
    """Return the first non-empty stringable value, or empty string."""
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _get(device_data: dict, *keys: str) -> str:
    """Pull the first non-empty value across a set of alternate keys —
    handles both display-form ("Device Name") and DB-form ("device_name")."""
    for k in keys:
        v = device_data.get(k)
        if v is None or v == "":
            continue
        return str(v).strip()
    return ""


def _mask_to_netmask(mask: str, default: str = "24") -> str:
    """'24' → '255.255.255.0', accepts int or str."""
    m = str(mask or default).strip() or default
    try:
        prefix = int(m)
        if not 0 <= prefix <= 32:
            prefix = 24
    except ValueError:
        prefix = 24
    net = ipaddress.IPv4Network(f"0.0.0.0/{prefix}")
    return str(net.netmask)


def _wildcard_from_prefix(ip: str, mask: str) -> str:
    """Cisco IOS wants an OSPF wildcard mask; derive from the /prefix."""
    m = str(mask or "24").strip() or "24"
    try:
        prefix = int(m)
        if not 0 <= prefix <= 32:
            prefix = 24
    except ValueError:
        prefix = 24
    net = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
    return str(net.hostmask)


def _network_cidr(ip: str, mask: str) -> str:
    """'192.168.0.2' + '24' → '192.168.0.0/24'."""
    try:
        net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
        return str(net)
    except (ipaddress.AddressValueError, ValueError):
        return f"{ip}/{mask}"


def _network_ipv6(ip: str, mask: str) -> str:
    try:
        net = ipaddress.IPv6Network(f"{ip}/{mask}", strict=False)
        return str(net)
    except (ipaddress.AddressValueError, ValueError):
        return f"{ip}/{mask}"


def _upstream_iface(vendor: str) -> str:
    """Placeholder upstream physical iface — operator edits before paste."""
    return {
        "juniper": "ge-0/0/0",
        "cisco":   "GigabitEthernet0/0",
        "arista":  "Ethernet1",
    }.get(vendor, "ge-0/0/0")


def _subif_name(vendor: str, vlan: str) -> str:
    """`ge-0/0/0.100` / `GigabitEthernet0/0.100` / `Ethernet1.100`."""
    return f"{_upstream_iface(vendor)}.{vlan}"


def _isis_net_from_loopback(loopback_ipv4: str, area: str = "49.0001") -> str:
    """Derive a plausible NET from the loopback IPv4.
    192.255.0.102 → 49.0001.1922.5500.0102.00 (nibble-packed)."""
    try:
        octets = str(loopback_ipv4).strip().split(".")
        if len(octets) != 4:
            return f"{area}.0000.0000.0001.00"
        packed = "".join(f"{int(o):03d}" for o in octets)  # 12 digits
        sysid = ".".join([packed[0:4], packed[4:8], packed[8:12]])
        return f"{area}.{sysid}.00"
    except ValueError:
        return f"{area}.0000.0000.0001.00"


# --- Renderers -------------------------------------------------------------

def render_juniper(device_data: dict) -> str:
    return _render(device_data, "juniper")


def render_cisco(device_data: dict) -> str:
    return _render(device_data, "cisco")


def render_arista(device_data: dict) -> str:
    return _render(device_data, "arista")


def render_all(device_data: Union[dict, List[dict]]) -> Dict[str, str]:
    """v0.5.319: accepts a single device_data dict (single-device
    mode, unchanged) OR a LIST of device_data dicts (scale mode).
    For scale, emits N per-device stanzas separated by a divider.

    Scale mode is how the Add Device dialog's "Increment" section
    surfaces here — with `increment_count=N` and the MAC/IPv4/IPv6/
    gateway/VLAN checkboxes ticked, the dialog now expands into a
    list of N snapshots (base + N-1 incremented copies) so the
    operator gets N upstream stanzas — one per netgen device —
    without having to open the dialog N times."""
    if isinstance(device_data, list):
        return _render_all_scale(device_data)
    return {
        "juniper": render_juniper(device_data),
        "cisco":   render_cisco(device_data),
        "arista":  render_arista(device_data),
    }


def _render_all_scale(devices: List[dict]) -> Dict[str, str]:
    if not devices:
        return {"juniper": "", "cisco": "", "arista": ""}
    dividers = {
        "juniper": "\n\n# " + "=" * 68 + "\n",
        "cisco":   "\n\n! " + "=" * 68 + "\n",
        "arista":  "\n\n! " + "=" * 68 + "\n",
    }
    out: Dict[str, List[str]] = {"juniper": [], "cisco": [], "arista": []}
    for dev in devices:
        out["juniper"].append(render_juniper(dev))
        out["cisco"].append(render_cisco(dev))
        out["arista"].append(render_arista(dev))
    return {v: dividers[v].join(out[v]) for v in out}


# --- Scale expansion --------------------------------------------------------
#
# v0.5.319 (audit upstream-hint-scale): the Add Device dialog's
# Increment section lets the operator generate N devices from one
# base (MAC/IPv4/IPv6/gateway/VLAN each incrementable in a chosen
# octet). Pre-fix, the upstream-hint dialog only saw the base
# device — a 100-device scale had ONE stanza, and the operator
# would have to manually reason about how to extend it. Now the
# dialog's snapshot builder calls `expand_for_scale` and hands the
# renderer a list of N per-device dicts, and `render_all` walks it.


def expand_for_scale(base: dict, increment_meta: dict) -> List[dict]:
    """Return `count` copies of `base`, with per-i increments
    applied to the fields the operator flagged.

    `increment_meta` shape:
      {
        "count": int (>= 1),
        "mac":        {"on": bool, "byte_idx": 0..5},
        "ipv4":       {"on": bool, "octet_idx": 0..3},
        "ipv6":       {"on": bool, "hextet_idx": 0..7},
        "gateway":    {"on": bool, "octet_idx": 0..3},
        "vlan":       {"on": bool},
      }

    Indexes match the dialog's *_combo.currentIndex() layout
    (0 = last octet/hextet/byte; 3 = 1st octet for IPv4, etc.)
    so this stays in lockstep with widgets/devices_tab._increment_ipv4.

    The device_name is appended `-i` for i >= 1 so the emitted
    stanzas don't all say `peer:netgen-device`.
    """
    count = int(increment_meta.get("count", 1) or 1)
    if count <= 1:
        return [copy.deepcopy(base)]

    out: List[dict] = []
    base_name = str(base.get("device_name") or "netgen-device")
    for i in range(count):
        dev = copy.deepcopy(base)
        if i == 0:
            out.append(dev)
            continue

        dev["device_name"] = f"{base_name}-{i + 1}"

        mac = (increment_meta.get("mac") or {})
        if mac.get("on") and dev.get("mac_address"):
            dev["mac_address"] = _incr_mac(dev["mac_address"], i, int(mac.get("byte_idx", 0)))

        ipv4 = (increment_meta.get("ipv4") or {})
        if ipv4.get("on") and dev.get("ipv4_address"):
            dev["ipv4_address"] = _incr_ipv4(dev["ipv4_address"], i, int(ipv4.get("octet_idx", 0)))

        gw = (increment_meta.get("gateway") or {})
        if gw.get("on") and dev.get("ipv4_gateway"):
            dev["ipv4_gateway"] = _incr_ipv4(dev["ipv4_gateway"], i, int(gw.get("octet_idx", 0)))

        ipv6 = (increment_meta.get("ipv6") or {})
        if ipv6.get("on") and dev.get("ipv6_address"):
            dev["ipv6_address"] = _incr_ipv6(dev["ipv6_address"], i, int(ipv6.get("hextet_idx", 0)))
        if ipv6.get("on") and dev.get("ipv6_gateway"):
            # v6 gateway rides the v6 hextet index — same as v4
            # gateway rides the v4 octet index in the widget.
            dev["ipv6_gateway"] = _incr_ipv6(dev["ipv6_gateway"], i, int(ipv6.get("hextet_idx", 0)))

        vlan = (increment_meta.get("vlan") or {})
        if vlan.get("on"):
            try:
                dev["vlan"] = str(int(str(dev.get("vlan") or "0").strip()) + i)
            except (ValueError, TypeError):
                pass

        out.append(dev)
    return out


def _incr_ipv4(addr: str, step: int, octet_idx: int) -> str:
    """Mirror of widgets/devices_tab._increment_ipv4. octet_idx
    0 = 4th (last) octet, 3 = 1st octet. Overflow ripples LEFT."""
    try:
        parts = list(map(int, str(addr).split(".")))
        if len(parts) != 4:
            return addr
        target = 3 - octet_idx
        parts[target] += step
        for j in range(3, -1, -1):
            while parts[j] > 255:
                parts[j] -= 256
                if j > 0:
                    parts[j - 1] += 1
        return ".".join(map(str, parts))
    except (ValueError, TypeError):
        return addr


def _incr_ipv6(addr: str, step: int, hextet_idx: int) -> str:
    """Mirror of widgets/devices_tab._increment_ipv6. hextet_idx
    0 = 8th (last) hextet, 7 = 1st hextet."""
    try:
        exploded = ipaddress.IPv6Address(str(addr)).exploded
        hextets = [int(h, 16) for h in exploded.split(":")]
        target = 7 - hextet_idx
        hextets[target] += step
        for j in range(7, -1, -1):
            while hextets[j] > 0xFFFF:
                hextets[j] -= 0x10000
                if j > 0:
                    hextets[j - 1] += 1
        packed = ":".join(f"{h:04x}" for h in hextets)
        # Re-collapse to canonical form for a compact display.
        return str(ipaddress.IPv6Address(packed))
    except (ValueError, ipaddress.AddressValueError):
        return addr


def _incr_mac(mac: str, step: int, byte_idx: int) -> str:
    """Mirror of widgets/devices_tab._increment_mac. byte_idx
    0 = 6th (last) byte, 5 = 1st byte."""
    try:
        parts = [int(b, 16) for b in str(mac).split(":")]
        if len(parts) != 6:
            return mac
        target = 5 - byte_idx
        parts[target] += step
        for j in range(5, -1, -1):
            while parts[j] > 255:
                parts[j] -= 256
                if j > 0:
                    parts[j - 1] += 1
        return ":".join(f"{b:02x}" for b in parts)
    except (ValueError, TypeError):
        return mac


def _render(device_data: dict, vendor: str) -> str:
    device_name = _get(device_data, "device_name", "Device Name") or "netgen-device"
    vlan        = _get(device_data, "vlan", "VLAN", "vlan_id") or "0"
    ipv4        = _get(device_data, "ipv4_address", "IPv4", "ipv4")
    ipv4_mask   = _get(device_data, "ipv4_mask", "IPv4 Mask") or "24"
    ipv4_gw     = _get(device_data, "ipv4_gateway", "IPv4 Gateway", "Gateway")
    ipv6        = _get(device_data, "ipv6_address", "IPv6", "ipv6")
    ipv6_mask   = _get(device_data, "ipv6_mask", "IPv6 Mask") or "64"
    ipv6_gw     = _get(device_data, "ipv6_gateway", "IPv6 Gateway")
    loopback_v4 = _get(device_data, "loopback_ipv4", "Loopback IPv4")

    bgp_config  = device_data.get("bgp_config") or {}
    ospf_config = device_data.get("ospf_config") or {}
    isis_config = device_data.get("isis_config") or {}
    dhcp_config = device_data.get("dhcp_config") or {}

    sections = []
    sections.append(_iface_stanza(
        vendor, device_name, vlan, ipv4, ipv4_mask, ipv4_gw, ipv6, ipv6_mask, ipv6_gw,
        isis_enabled=bool(isis_config),
    ))

    if bgp_config:
        bgp = _bgp_stanza(vendor, device_name, ipv4, ipv6, bgp_config, ipv4_gw)
        if bgp:
            sections.append(bgp)

    if ospf_config:
        ospf = _ospf_stanza(vendor, vlan, ipv4, ipv4_mask, ipv6, ospf_config)
        if ospf:
            sections.append(ospf)

    if isis_config:
        isis = _isis_stanza(vendor, vlan, loopback_v4, isis_config)
        if isis:
            sections.append(isis)

    # v0.5.318 (audit dhcp-client-upstream-relay-hint): a netgen
    # DHCP-client device needs the L3 gateway (this upstream) to
    # forward its DHCP requests to the netgen DHCP-server device
    # via dhcp-relay. Emit for CLIENT mode only. Server-mode +
    # non-DHCP devices don't need this and get no stanza.
    _dhcp_mode = (
        str(dhcp_config.get("mode") or dhcp_config.get("dhcp_mode") or "").strip().lower()
        or str(device_data.get("dhcp_mode") or "").strip().lower()
    )
    if _dhcp_mode == "client":
        relay = _dhcp_relay_stanza(vendor, vlan, dhcp_config)
        if relay:
            sections.append(relay)

    header = _header(vendor, device_name, vlan, ipv4, ipv6)
    return header + "\n" + "\n\n".join(s for s in sections if s) + "\n"


def _header(vendor: str, name: str, vlan: str, ipv4: str, ipv6: str) -> str:
    marker = {"juniper": "#", "cisco": "!", "arista": "!"}.get(vendor, "#")
    bits = [f"{marker} Upstream config for netgen device '{name}'"]
    detail = []
    if ipv4:
        detail.append(f"peer IPv4 {ipv4}")
    if ipv6:
        detail.append(f"peer IPv6 {ipv6}")
    if vlan and vlan != "0":
        detail.append(f"VLAN {vlan}")
    if detail:
        bits.append(f"{marker} " + ", ".join(detail))
    bits.append(f"{marker} Replace the placeholder iface ({_upstream_iface(vendor)}) with your actual uplink.")
    return "\n".join(bits)


def _iface_stanza(
    vendor: str, name: str, vlan: str,
    ipv4: str, ipv4_mask: str, ipv4_gw: str,
    ipv6: str, ipv6_mask: str, ipv6_gw: str,
    isis_enabled: bool,
) -> str:
    if vendor == "juniper":
        lines = [
            f"set interfaces {_upstream_iface(vendor)} vlan-tagging",
            f"set interfaces {_upstream_iface(vendor)} unit {vlan} vlan-id {vlan}",
            f"set interfaces {_upstream_iface(vendor)} unit {vlan} description \"peer:{name}\"",
        ]
        if ipv4_gw and ipv4_mask:
            lines.append(f"set interfaces {_upstream_iface(vendor)} unit {vlan} family inet address {ipv4_gw}/{ipv4_mask}")
        if ipv6_gw and ipv6_mask:
            lines.append(f"set interfaces {_upstream_iface(vendor)} unit {vlan} family inet6 address {ipv6_gw}/{ipv6_mask}")
        if isis_enabled:
            lines.append(f"set interfaces {_upstream_iface(vendor)} unit {vlan} family iso")
        return "\n".join(lines)

    if vendor == "cisco":
        sub = _subif_name(vendor, vlan)
        lines = [
            f"interface {sub}",
            f" description peer:{name}",
            f" encapsulation dot1Q {vlan}",
        ]
        if ipv4_gw and ipv4_mask:
            lines.append(f" ip address {ipv4_gw} {_mask_to_netmask(ipv4_mask)}")
        if ipv6_gw and ipv6_mask:
            lines.append(f" ipv6 address {ipv6_gw}/{ipv6_mask}")
        if isis_enabled:
            lines.append(" ip router isis")
            lines.append(" ipv6 router isis") if ipv6 else None
            lines = [ln for ln in lines if ln is not None]
        lines.append("!")
        return "\n".join(lines)

    if vendor == "arista":
        sub = _subif_name(vendor, vlan)
        lines = [
            f"interface {sub}",
            f"   description peer:{name}",
            f"   encapsulation dot1q vlan {vlan}",
        ]
        if ipv4_gw and ipv4_mask:
            lines.append(f"   ip address {ipv4_gw}/{ipv4_mask}")
        if ipv6_gw and ipv6_mask:
            lines.append(f"   ipv6 address {ipv6_gw}/{ipv6_mask}")
        if isis_enabled:
            lines.append("   isis enable ISIS-1")
        lines.append("!")
        return "\n".join(lines)

    return ""


def _bgp_stanza(vendor: str, name: str, ipv4: str, ipv6: str,
                bgp_config: dict, ipv4_gw: str) -> str:
    local_asn  = _first(bgp_config.get("bgp_local_as"), bgp_config.get("bgp_asn"), "65000")
    remote_asn = _first(bgp_config.get("bgp_remote_asn"), local_asn)
    hold       = _first(bgp_config.get("bgp_hold_time"), "90")
    keepalive  = _first(bgp_config.get("bgp_keepalive"), "30")
    ipv4_en    = bool(bgp_config.get("ipv4_enabled", True))
    ipv6_en    = bool(bgp_config.get("ipv6_enabled", False))
    # On the upstream we're the remote — so from the upstream's
    # perspective, our local_asn is the peer's remote-as.
    peer_asn = local_asn

    if vendor == "juniper":
        group_type = "internal" if local_asn == remote_asn else "external"
        lines = [
            f"set routing-options autonomous-system {remote_asn}",
            f"set protocols bgp group NETGEN-{name} type {group_type}",
            f"set protocols bgp group NETGEN-{name} peer-as {peer_asn}",
            f"set protocols bgp group NETGEN-{name} hold-time {hold}",
        ]
        if ipv4 and ipv4_en:
            lines.append(f"set protocols bgp group NETGEN-{name} neighbor {ipv4} description \"{name} v4\"")
        if ipv6 and ipv6_en:
            lines.append(f"set protocols bgp group NETGEN-{name} neighbor {ipv6} description \"{name} v6\"")
            lines.append(f"set protocols bgp group NETGEN-{name} neighbor {ipv6} family inet6 unicast")
        return "\n".join(lines)

    if vendor == "cisco":
        lines = [f"router bgp {remote_asn}"]
        if ipv4 and ipv4_en:
            lines.append(f" neighbor {ipv4} remote-as {peer_asn}")
            lines.append(f" neighbor {ipv4} description peer:{name}")
            lines.append(f" neighbor {ipv4} timers {keepalive} {hold}")
        if ipv6 and ipv6_en:
            lines.append(f" neighbor {ipv6} remote-as {peer_asn}")
            lines.append(f" neighbor {ipv6} description peer:{name}")
            lines.append(f" address-family ipv6 unicast")
            lines.append(f"  neighbor {ipv6} activate")
            lines.append(f" exit-address-family")
        lines.append("!")
        return "\n".join(lines)

    if vendor == "arista":
        lines = [f"router bgp {remote_asn}"]
        if ipv4 and ipv4_en:
            lines.append(f"   neighbor {ipv4} remote-as {peer_asn}")
            lines.append(f"   neighbor {ipv4} description peer:{name}")
            lines.append(f"   neighbor {ipv4} timers {keepalive} {hold}")
        if ipv6 and ipv6_en:
            lines.append(f"   neighbor {ipv6} remote-as {peer_asn}")
            lines.append(f"   neighbor {ipv6} description peer:{name}")
            lines.append(f"   address-family ipv6")
            lines.append(f"      neighbor {ipv6} activate")
        lines.append("!")
        return "\n".join(lines)

    return ""


def _ospf_stanza(vendor: str, vlan: str, ipv4: str, ipv4_mask: str,
                 ipv6: str, ospf_config: dict) -> str:
    area_v4 = _first(
        ospf_config.get("area_id_ipv4"),
        ospf_config.get("area_id"),
        "0.0.0.0",
    )
    area_v6 = _first(
        ospf_config.get("area_id_ipv6"),
        ospf_config.get("area_id"),
        "0.0.0.0",
    )
    hello = _first(ospf_config.get("hello_interval"), "10")
    dead  = _first(ospf_config.get("dead_interval"), "40")
    ipv4_en = bool(ospf_config.get("ipv4_enabled", True))
    ipv6_en = bool(ospf_config.get("ipv6_enabled", False))
    p2p_v4 = bool(ospf_config.get("p2p_ipv4", ospf_config.get("p2p", False)))
    p2p_v6 = bool(ospf_config.get("p2p_ipv6", ospf_config.get("p2p", False)))

    if vendor == "juniper":
        sub = _subif_name(vendor, vlan)
        lines = []
        if ipv4_en:
            lines += [
                f"set protocols ospf area {area_v4} interface {sub} hello-interval {hello}",
                f"set protocols ospf area {area_v4} interface {sub} dead-interval {dead}",
            ]
            if p2p_v4:
                lines.append(f"set protocols ospf area {area_v4} interface {sub} interface-type p2p")
        if ipv6_en:
            lines += [
                f"set protocols ospf3 area {area_v6} interface {sub} hello-interval {hello}",
                f"set protocols ospf3 area {area_v6} interface {sub} dead-interval {dead}",
            ]
            if p2p_v6:
                lines.append(f"set protocols ospf3 area {area_v6} interface {sub} interface-type p2p")
        return "\n".join(lines)

    if vendor == "cisco":
        sub = _subif_name(vendor, vlan)
        lines = []
        if ipv4_en and ipv4 and ipv4_mask:
            lines += [
                f"router ospf 1",
                f" network {ipv4} {_wildcard_from_prefix(ipv4, ipv4_mask)} area {area_v4}",
                "!",
                f"interface {sub}",
                f" ip ospf hello-interval {hello}",
                f" ip ospf dead-interval {dead}",
            ]
            if p2p_v4:
                lines.append(f" ip ospf network point-to-point")
            lines.append("!")
        if ipv6_en:
            lines += [
                f"ipv6 router ospf 1",
                f"!",
                f"interface {sub}",
                f" ipv6 ospf 1 area {area_v6}",
                f" ipv6 ospf hello-interval {hello}",
                f" ipv6 ospf dead-interval {dead}",
            ]
            if p2p_v6:
                lines.append(f" ipv6 ospf network point-to-point")
            lines.append("!")
        return "\n".join(lines)

    if vendor == "arista":
        sub = _subif_name(vendor, vlan)
        lines = []
        if ipv4_en and ipv4 and ipv4_mask:
            lines += [
                f"router ospf 1",
                f"   network {_network_cidr(ipv4, ipv4_mask)} area {area_v4}",
                "!",
                f"interface {sub}",
                f"   ip ospf hello-interval {hello}",
                f"   ip ospf dead-interval {dead}",
            ]
            if p2p_v4:
                lines.append(f"   ip ospf network point-to-point")
            lines.append("!")
        if ipv6_en:
            lines += [
                f"ipv6 router ospf 1",
                f"!",
                f"interface {sub}",
                f"   ipv6 ospf 1 area {area_v6}",
                f"   ipv6 ospf hello-interval {hello}",
                f"   ipv6 ospf dead-interval {dead}",
            ]
            if p2p_v6:
                lines.append(f"   ipv6 ospf network point-to-point")
            lines.append("!")
        return "\n".join(lines)

    return ""


def _dhcp_relay_stanza(vendor: str, vlan: str, dhcp_config: dict) -> str:
    """v0.5.318 (audit dhcp-client-upstream-relay-hint):
    upstream L3-gateway config that forwards a netgen DHCP-client's
    requests to a remote netgen DHCP-server device.

    Two values the operator MUST substitute after paste:

      * DHCP-server device IP — the client-side dialog doesn't
        know its own server's address; render as a placeholder
        (`<DHCP-SERVER-IP>`) with a comment. Some deployments
        pin it in `dhcp_config.upstream_server_hint`; use that
        when present.
      * Client-VLAN SVI — inferred from the device's VLAN
        (`irb.<vlan>` / `interface Vlan<vlan>`). No substitution
        needed when the operator's naming matches netgen's guess.
    """
    server_ip = _first(
        dhcp_config.get("upstream_server_hint"),
        # Legacy shape: some dhcp_config dumps have relay_server_ip
        # from an earlier design; honor it if present.
        dhcp_config.get("relay_server_ip"),
    ) or "<DHCP-SERVER-IP>"

    svi_note = ""
    if not vlan or vlan == "0":
        # No VLAN means we can't name the SVI — leave it as an
        # explicit placeholder so the operator picks it.
        svi_juniper = "irb.<vlan>"
        svi_cisco = "Vlan<vlan>"
        svi_arista = "Vlan<vlan>"
        svi_note = " (edit: netgen device has no VLAN set)"
    else:
        svi_juniper = f"irb.{vlan}"
        svi_cisco = f"Vlan{vlan}"
        svi_arista = f"Vlan{vlan}"

    if vendor == "juniper":
        lines = [
            "# --- DHCP relay: forward this client's DISCOVER upstream to the netgen DHCP-server device.",
            f"# Substitute {server_ip if server_ip == '<DHCP-SERVER-IP>' else '(hinted: ' + server_ip + ')'} with the DHCP-server device's IPv4 address if not already correct.",
            f"# Client-VLAN SVI: {svi_juniper}{svi_note}",
            "set forwarding-options dhcp-relay overrides allow-snooped-clients",
            "set forwarding-options dhcp-relay forward-only",
            f"set forwarding-options dhcp-relay server-group DHCP-SERVERS {server_ip}",
            "set forwarding-options dhcp-relay active-server-group DHCP-SERVERS",
            f"set forwarding-options dhcp-relay group CLIENTS interface {svi_juniper}",
        ]
        return "\n".join(lines)

    if vendor == "cisco":
        # v0.5.319: parity with Juniper — include the global service
        # + trust-info equivalents so the operator sees the full
        # relay picture, not just `ip helper-address`. IOS defaults
        # `service dhcp` to ON; we still emit it explicitly so the
        # operator can verify. `ip dhcp relay information trust-all`
        # is the closest analogue to Juniper's
        # `overrides allow-snooped-clients` — accepts giaddr-relayed
        # DHCP requests that carry option-82 info from a downstream
        # relay (matters in multi-hop lab setups).
        lines = [
            "! --- DHCP relay: forward this client's DISCOVER upstream to the netgen DHCP-server device.",
            f"! Substitute {server_ip if server_ip == '<DHCP-SERVER-IP>' else '(hinted: ' + server_ip + ')'} with the DHCP-server device's IPv4 address if not already correct.",
            f"! Client-VLAN SVI: interface {svi_cisco}{svi_note}",
            "! Global DHCP relay service (usually already on by default in IOS):",
            "service dhcp",
            "! Trust relayed DHCP info from downstream (mirror of Junos allow-snooped-clients):",
            "ip dhcp relay information trust-all",
            "! Client-facing SVI: forward this VLAN's DHCP requests to the server device:",
            f"interface {svi_cisco}",
            f" ip helper-address {server_ip}",
            "!",
        ]
        return "\n".join(lines)

    if vendor == "arista":
        # v0.5.319: parity with Juniper. Arista EOS uses
        # `ip helper-address` on the SVI (same as IOS) but also has
        # `ip dhcp relay information option` for option-82 tagging
        # and a `dhcp relay always-on` mode; include both so the
        # operator sees the equivalent knobs to Juniper's
        # forwarding-options block.
        lines = [
            "! --- DHCP relay: forward this client's DISCOVER upstream to the netgen DHCP-server device.",
            f"! Substitute {server_ip if server_ip == '<DHCP-SERVER-IP>' else '(hinted: ' + server_ip + ')'} with the DHCP-server device's IPv4 address if not already correct.",
            f"! Client-VLAN SVI: interface {svi_arista}{svi_note}",
            "! Tag relayed DHCP requests with option-82 so the server can identify the source VLAN:",
            "ip dhcp relay information option",
            "! Client-facing SVI: forward this VLAN's DHCP requests to the server device:",
            f"interface {svi_arista}",
            f"   ip helper-address {server_ip}",
            "!",
        ]
        return "\n".join(lines)

    return ""


def _isis_stanza(vendor: str, vlan: str, loopback_ipv4: str,
                 isis_config: dict) -> str:
    level_raw = _first(isis_config.get("isis_level"), isis_config.get("level"), "level-2-only")
    if level_raw.startswith("level-"):
        level_short = level_raw.replace("-only", "")
    else:
        level_short = "level-2"
    net = _first(
        isis_config.get("isis_net"),
        isis_config.get("net"),
        _isis_net_from_loopback(loopback_ipv4),
    )
    area = _first(isis_config.get("isis_area"), isis_config.get("area"), "CORE")

    if vendor == "juniper":
        sub = _subif_name(vendor, vlan)
        return "\n".join([
            f"set protocols isis interface {sub} {level_short} enable",
            f"set protocols isis interface {sub} point-to-point",
            f"set protocols isis net {net}",
        ])

    if vendor == "cisco":
        sub = _subif_name(vendor, vlan)
        return "\n".join([
            f"router isis {area}",
            f" net {net}",
            f" is-type {level_short}",
            "!",
            f"interface {sub}",
            f" ip router isis {area}",
            f" isis network point-to-point",
            "!",
        ])

    if vendor == "arista":
        sub = _subif_name(vendor, vlan)
        return "\n".join([
            f"router isis {area}",
            f"   net {net}",
            f"   is-type {level_short}",
            "!",
            f"interface {sub}",
            f"   isis enable {area}",
            f"   isis network point-to-point",
            "!",
        ])

    return ""
