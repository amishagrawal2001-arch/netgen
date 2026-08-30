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
- DHCP hints are deliberately not emitted — a DHCP-server device
  on netgen serves clients directly on the wire, so the upstream
  doesn't need special config beyond what BGP/OSPF already set up.

Callers get vendor-neutral empty strings for protocols the device
doesn't have enabled — the section header just doesn't appear.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, Optional


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


def render_all(device_data: dict) -> Dict[str, str]:
    return {
        "juniper": render_juniper(device_data),
        "cisco":   render_cisco(device_data),
        "arista":  render_arista(device_data),
    }


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
