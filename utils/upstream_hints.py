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
    """v0.5.320 (audit upstream-hint-scale-dedupe): shared blocks
    that are IDENTICAL across all N devices (e.g. the DHCP-relay
    stanza when every device sits on the same client VLAN; the
    interface vlan-tagging + subunit lines when VLAN doesn't
    increment) are emitted ONCE at the top under a "Shared
    upstream config" banner. Only blocks that vary per-device
    (BGP neighbor with per-device IP, interface description with
    per-device name) get repeated per device.

    Pre-fix, every device's stanza was concatenated verbatim, so
    a 100-device scale on a shared VLAN emitted 100 identical
    DHCP-relay stanzas. Operator's srv06 lab: 2-device scale had
    the 5-line dhcp-relay block twice for no reason. This
    slimmed-down form is what actually gets pasted into a switch.
    """
    if not devices:
        return {"juniper": "", "cisco": "", "arista": ""}
    # v0.5.320: a 1-element list is degenerate scale — there is
    # nothing to share ACROSS devices when there is only one.
    # Emit the single device's plain rendering (no shared banner,
    # no divider) to keep the paste-body clean.
    if len(devices) == 1:
        return {
            "juniper": render_juniper(devices[0]) + "\n",
            "cisco":   render_cisco(devices[0]) + "\n",
            "arista":  render_arista(devices[0]) + "\n",
        }
    return {v: _dedupe_and_emit(devices, v) for v in ("juniper", "cisco", "arista")}


def _detect_scale_collisions(devices: List[dict]) -> List[dict]:
    """v0.5.322 (audit scale-collision-warning): fields that MUST
    be unique per netgen device (IPv4, MAC) are checked across
    the scale set; if all N devices share the same value, emit a
    collision-warning that gets rendered as a `!!! WARNING !!!`
    block at the top of the hint output.

    Trigger: operator sets count>N without ticking the per-field
    increment checkbox. Currently the dialog produces N devices
    with the same MAC + IPv4, which:

      * On the netgen box: `ip addr add` will EEXIST on the
        second device — the second apply silently fails.

      * On the upstream: BGP won't establish two sessions to the
        same peer IP (single-peer identity); OSPF/ISIS will
        adjacency-flap because of duplicate router-ids/system-ids.

    Returns a list of {field, label, value, why} dicts — one per
    field found to be shared-but-should-be-unique. Empty list
    when the scale set looks internally consistent.
    """
    if len(devices) < 2:
        return []
    warnings: List[dict] = []
    critical = [
        ("ipv4_address", "IPv4 address",
         "the switch/router can't establish separate BGP/OSPF/ISIS "
         "adjacencies to N peers with the same address; only one "
         "session survives"),
        ("ipv6_address", "IPv6 address",
         "same as IPv4 for the v6 side — a duplicate ipv6 makes "
         "OSPFv3 / IS-IS / BGP-IPv6 peers unresolvable"),
        ("mac_address", "MAC address",
         "two interfaces with the same MAC on the same L2 fight for "
         "frames — packet loss and adjacency flap"),
        ("loopback_ipv4", "Loopback IPv4",
         "OSPF router-id and BGP router-id both default to the "
         "loopback — duplicate router-id causes adjacency flap or "
         "route table churn"),
        ("loopback_ipv6", "Loopback IPv6",
         "same as v4 for the v6-only BGP/OSPFv3 case — the loopback "
         "seeds router-id/session-id"),
    ]
    for field, label, why in critical:
        values = [str(d.get(field) or "").strip() for d in devices]
        non_empty = [v for v in values if v]
        if non_empty and len(set(non_empty)) == 1 and len(non_empty) == len(devices):
            warnings.append({
                "field": field, "label": label,
                "value": non_empty[0], "why": why,
            })
    return warnings


def _format_scale_collision_banner(warnings: List[dict], marker: str, count: int) -> str:
    """Render the collision warnings as a comment-marker block for
    the given vendor (`#` for Junos, `!` for Cisco/Arista)."""
    if not warnings:
        return ""
    lines = [
        f"{marker} !!! SCALE COLLISION WARNING !!!",
        f"{marker} All {count} netgen devices share values that MUST be unique per device.",
        f"{marker} Fix in the Add Device dialog: tick the relevant checkbox under",
        f"{marker} 'Increment Options' so netgen assigns a unique per-device value.",
        f"{marker}",
    ]
    for w in warnings:
        # Map field-name to dialog checkbox label so the operator
        # knows exactly what to click.
        checkbox = {
            "ipv4_address":  "IPv4",
            "ipv6_address":  "IPv6",
            "mac_address":   "MAC",
            "loopback_ipv4": "Loopback",
            "loopback_ipv6": "Loopback",
        }.get(w["field"], w["field"])
        lines.append(
            f"{marker} - All devices have the same {w['label']} '{w['value']}' "
            f"→ tick the '{checkbox}' checkbox."
        )
        lines.append(f"{marker}   Reason: {w['why']}.")
    return "\n".join(lines)


def _dedupe_and_emit(devices: List[dict], vendor: str) -> str:
    """v0.5.321: two-tier dedupe.

    * BLOCK-level (v0.5.320): a whole `\\n\\n`-separated stanza that
      is byte-identical across every device is emitted once under
      the shared banner. Works for the DHCP-relay stanza when all
      N devices share a client VLAN.

    * LINE-level (v0.5.321): when a stanza varies per-device but
      LINES within it are identical (e.g. `set routing-options
      autonomous-system 65000` is the same on every BGP device
      even though `set protocols bgp group NETGEN-<name> ...`
      lines differ because the group name carries the `-N` suffix,
      or the interface `family inet address <gw>/<mask>` line is
      the same on every device when gateway doesn't increment),
      split the block by newline, classify each line-position as
      SHARED or PER_DEVICE, and hoist SHARED lines into the shared
      section while keeping PER_DEVICE lines per-device.

    Both tiers preserve line ORDER — line at position K in a
    block is compared to the SAME position K on every other
    device, so semantic order within a stanza survives the
    dedupe. Line-level dedupe requires matching shape across
    devices (same block-line-count); if shapes disagree, fall
    back to whole-block per-device.
    """
    marker = {"juniper": "#", "cisco": "!", "arista": "!"}[vendor]

    per_device_blocks: List[List[str]] = []
    for dev in devices:
        rendered = _render(dev, vendor).rstrip("\n")
        blocks = [b.strip("\n") for b in rendered.split("\n\n") if b.strip()]
        per_device_blocks.append(blocks)

    max_i = max((len(b) for b in per_device_blocks), default=0)

    # Per-block-index bucket that either goes into the shared
    # section (as a full block or as a slice of shared lines) OR
    # into each device's per-device section.
    shared_blocks: List[str] = []
    per_device_blocks_out: List[List[str]] = [[] for _ in devices]

    for i in range(max_i):
        texts = [b[i] if i < len(b) else "" for b in per_device_blocks]

        # Index 0 is the header — device_name is in the comment,
        # so it's always per-device regardless of text-equality.
        if i == 0:
            for j, t in enumerate(texts):
                if t:
                    per_device_blocks_out[j].append(t)
            continue

        # Block-level dedupe: whole stanza identical across all →
        # one shared copy, no per-device carbon.
        if all(t == texts[0] and t != "" for t in texts):
            shared_blocks.append(texts[0])
            continue

        # Line-level dedupe: split each device's copy of this
        # block into lines. If shapes match (same line count on
        # every device), classify per-position.
        line_sets = [t.split("\n") if t else [] for t in texts]
        shapes = {len(ls) for ls in line_sets if ls}
        if len(shapes) == 1 and shapes != {0}:
            nlines = next(iter(shapes))
            shared_positions: List[int] = []
            for k in range(nlines):
                lines_at_k = [ls[k] for ls in line_sets if ls]
                if all(t == lines_at_k[0] for t in lines_at_k):
                    shared_positions.append(k)

            if shared_positions:
                # Slice out the shared line-positions as one shared
                # block (preserves original ordering). Per-device
                # gets the remaining lines only.
                shared_lines = [line_sets[0][k] for k in shared_positions]
                shared_blocks.append("\n".join(shared_lines))
                for j, ls in enumerate(line_sets):
                    if not ls:
                        continue
                    remaining = [ls[k] for k in range(nlines) if k not in set(shared_positions)]
                    if remaining:
                        per_device_blocks_out[j].append("\n".join(remaining))
                continue

        # Shape mismatch OR no shared lines: whole block stays
        # per-device.
        for j, t in enumerate(texts):
            if t:
                per_device_blocks_out[j].append(t)

    chunks: List[str] = []
    banner_shared = (
        f"{marker} === Shared upstream config "
        f"(applies to all {len(devices)} netgen devices) ==="
    )
    banner_per_dev = f"{marker} " + "=" * 68

    # v0.5.322: collision warning is the FIRST thing the operator
    # sees. Emit before the shared banner so it can't be missed.
    warnings = _detect_scale_collisions(devices)
    if warnings:
        chunks.append(_format_scale_collision_banner(warnings, marker, len(devices)))

    if shared_blocks:
        chunks.append(banner_shared)
        chunks.extend(shared_blocks)

    for j, blocks in enumerate(per_device_blocks_out):
        if not blocks:
            continue
        if chunks:
            chunks.append(banner_per_dev)
        chunks.extend(blocks)

    return "\n\n".join(chunks) + "\n"


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

        # v0.5.324 (audit scale-auto-increment): loopback IPs
        # weren't incremented pre-fix, even when the dialog's
        # Loopback checkbox was ticked. Result: N scaled devices
        # got the same loopback → same OSPF/BGP router-id → the
        # SCALE COLLISION WARNING fired but nothing here fixed it.
        # Same octet/hextet semantics as the primary IPv4/IPv6.
        loopback = (increment_meta.get("loopback") or {})
        if loopback.get("on") and dev.get("loopback_ipv4"):
            dev["loopback_ipv4"] = _incr_ipv4(
                dev["loopback_ipv4"], i, int(loopback.get("ipv4_octet_idx", 0)),
            )
        if loopback.get("on") and dev.get("loopback_ipv6"):
            dev["loopback_ipv6"] = _incr_ipv6(
                dev["loopback_ipv6"], i, int(loopback.get("ipv6_hextet_idx", 0)),
            )

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

    bgp_config    = device_data.get("bgp_config") or {}
    ospf_config   = device_data.get("ospf_config") or {}
    isis_config   = device_data.get("isis_config") or {}
    dhcp_config   = device_data.get("dhcp_config") or {}
    # v0.5.325: operator asked "no config suggestion for ROCEv2 and
    # VXLAN". Both are switch-side data-plane features netgen just
    # produces packets for; the upstream needs matching config
    # (DSCP/PFC classifier for RoCE, VNI + VTEP peer for VXLAN)
    # or the packets get dropped / delivered wrong.
    rocev2_config = device_data.get("rocev2_config") or {}
    vxlan_config  = device_data.get("vxlan_config") or {}

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

    # v0.5.325: RoCEv2 + VXLAN stanzas. Both are switch-side
    # dependencies of what netgen puts on the wire — the operator
    # gets the packets to leave the box but the upstream must be
    # configured to carry/classify them correctly.
    if rocev2_config:
        rocev2 = _rocev2_stanza(vendor, vlan, rocev2_config)
        if rocev2:
            sections.append(rocev2)

    if vxlan_config:
        vxlan = _vxlan_stanza(vendor, vlan, vxlan_config)
        if vxlan:
            sections.append(vxlan)

    header = _header(vendor, device_name, vlan, ipv4, ipv6)
    # v0.5.321: header + sections joined with blank-line separator
    # so the header is its own block for the dedupe pass in
    # `_dedupe_and_emit`. Pre-fix, header was joined to iface_stanza
    # with a single "\n" — the two stuck together as block 0,
    # forcing the iface_stanza to inherit the header's always-
    # per-device classification. That kept identical iface lines
    # (vlan-tagging, unit N vlan-id N, family inet address <gw>)
    # duplicated per-device even after the v0.5.321 line-level
    # dedupe landed.
    return "\n\n".join([header, *(s for s in sections if s)]) + "\n"


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


def _rocev2_stanza(vendor: str, vlan: str, rocev2_config: dict) -> str:
    """v0.5.325 (audit rocev2-vxlan-upstream-hint):

    RoCEv2 packets are UDP/4791 tagged with a Priority Code Point
    (PCP) at L2 and a DSCP at L3. Without a matching classifier
    on the upstream switch, the fabric ignores the priority marks
    and drops RoCE frames under any congestion — RDMA writes hang
    with completion-queue timeouts. The switch also has to enable
    PFC (Priority Flow Control) on the classifier'd priority so
    the RoCE flow gets lossless treatment.

    Netgen knows:
      * rocev2_priority — the 802.1p PCP bit netgen sets on TX
      * rocev2_dscp — the DSCP netgen sets on TX
      * rocev2_udp_port — the RoCEv2 UDP port (usually 4791)

    We emit a MINIMAL classifier + PFC-enable snippet the
    operator adapts. Real production fabrics usually already
    have RoCE-CoS templates the operator should reference
    instead.
    """
    prio = _first(
        rocev2_config.get("rocev2_priority"),
        rocev2_config.get("priority"), "3",
    )
    dscp = _first(
        rocev2_config.get("rocev2_dscp"),
        rocev2_config.get("dscp"), "26",
    )
    udp = _first(
        rocev2_config.get("rocev2_udp_port"),
        rocev2_config.get("udp_port"), "4791",
    )
    upstream = _upstream_iface(vendor)

    if vendor == "juniper":
        return "\n".join([
            "# --- RoCEv2: classify + enable PFC on this priority so",
            "# --- RDMA writes get lossless treatment across the fabric.",
            f"# Netgen sends RoCEv2 on UDP/{udp}, 802.1p priority={prio}, DSCP={dscp}.",
            f"set class-of-service classifiers ieee-802.1 ROCE-CLASSIFIER forwarding-class no-loss loss-priority low code-points {prio:0>3}",
            f"set class-of-service interfaces {upstream} unit {vlan} classifiers ieee-802.1 ROCE-CLASSIFIER",
            "set class-of-service forwarding-classes class no-loss queue-num 3 pfc-priority 3",
            f"set class-of-service interfaces {upstream} congestion-notification-profile PFC-ROCE",
            f"set class-of-service congestion-notification-profile PFC-ROCE input ieee-802.1 code-point {prio:0>3} pfc",
        ])

    if vendor == "cisco":
        return "\n".join([
            "! --- RoCEv2: classify + enable PFC on this priority so",
            "! --- RDMA writes get lossless treatment across the fabric.",
            f"! Netgen sends RoCEv2 on UDP/{udp}, 802.1p priority={prio}, DSCP={dscp}.",
            "class-map type qos match-all ROCE-CLASS",
            f" match cos {prio}",
            f" match dscp {dscp}",
            "!",
            "policy-map type qos ROCE-POLICY",
            " class ROCE-CLASS",
            "  set qos-group 3",
            "!",
            "class-map type network-qos ROCE-NQ",
            " match qos-group 3",
            "!",
            "policy-map type network-qos ROCE-NQ-POLICY",
            " class type network-qos ROCE-NQ",
            "  pause pfc-cos 3",
            "!",
            f"interface {upstream}",
            " service-policy type qos input ROCE-POLICY",
            " priority-flow-control mode on",
            "!",
        ])

    if vendor == "arista":
        return "\n".join([
            "! --- RoCEv2: classify + enable PFC on this priority so",
            "! --- RDMA writes get lossless treatment across the fabric.",
            f"! Netgen sends RoCEv2 on UDP/{udp}, 802.1p priority={prio}, DSCP={dscp}.",
            "qos map cos 3 to traffic-class 3",
            f"qos map dscp {dscp} to traffic-class 3",
            f"interface {upstream}",
            "   priority-flow-control on",
            "   priority-flow-control priority 3 no-drop",
            "!",
        ])

    return ""


def _vxlan_stanza(vendor: str, vlan: str, vxlan_config: dict) -> str:
    """v0.5.325 (audit rocev2-vxlan-upstream-hint):

    Netgen's VXLAN device puts UDP/4789-encapped frames on the
    wire with a given VNI, source VTEP IP, and one or more
    remote VTEP peers. For the packets to actually reach a peer,
    the upstream switch (acting as a VTEP or transiting VTEP
    traffic) needs:

      * A matching VNI-to-VLAN mapping (so the switch decaps
        into the right VLAN when a remote VTEP sends to it).
      * Its own loopback advertised as source-interface.
      * A static peer list for head-end replication (or an EVPN
        control plane, out of scope for this hint).
      * The VXLAN UDP port (defaults to 4789).

    Netgen provides:
      * vni — the VXLAN Network Identifier
      * local_ip — netgen device's VTEP source
      * remote_peers / remote_endpoints — list of remote VTEPs
      * udp_port — usually 4789
      * vlan_id — the tenant VLAN behind this VNI
    """
    vni = _first(
        vxlan_config.get("vni"), "10010",
    )
    local_ip = _first(
        vxlan_config.get("local_ip"), "<netgen-VTEP-IP>",
    )
    udp_port = _first(
        vxlan_config.get("udp_port"), "4789",
    )
    tenant_vlan = _first(
        vxlan_config.get("vlan_id"), vlan, "10",
    )
    remote_raw = (
        vxlan_config.get("remote_peers")
        or vxlan_config.get("remote_endpoints")
        or []
    )
    if isinstance(remote_raw, str):
        remotes = [r.strip() for r in remote_raw.replace(";", ",").split(",") if r.strip()]
    elif isinstance(remote_raw, (list, tuple, set)):
        remotes = [str(r).strip() for r in remote_raw if str(r).strip()]
    else:
        remotes = []
    if not remotes:
        remotes = ["<remote-VTEP-IP>"]

    if vendor == "juniper":
        lines = [
            "# --- VXLAN: decap incoming frames from netgen's VTEP into the",
            "# --- matching tenant VLAN, and set up head-end replication",
            "# --- back to the netgen VTEP so BUM traffic can complete.",
            f"# Netgen VTEP source: {local_ip}, VXLAN UDP port: {udp_port},",
            f"# VNI {vni} maps to VLAN {tenant_vlan}.",
            f"set protocols evpn extended-vni-list {vni}",
            f"set vlans TENANT-{tenant_vlan} vlan-id {tenant_vlan}",
            f"set vlans TENANT-{tenant_vlan} vxlan vni {vni}",
        ]
        for r in remotes:
            lines.append(f"# Static VTEP peer (netgen side): {r}")
        lines.append("# Point your switch loopback + BGP EVPN toward this peer for full mesh.")
        return "\n".join(lines)

    if vendor == "cisco":
        lines = [
            "! --- VXLAN: decap incoming frames from netgen's VTEP into the",
            "! --- matching tenant VLAN, and set up head-end replication",
            "! --- back to the netgen VTEP so BUM traffic can complete.",
            f"! Netgen VTEP source: {local_ip}, VXLAN UDP port: {udp_port},",
            f"! VNI {vni} maps to VLAN {tenant_vlan}.",
            "feature vn-segment-vlan-based",
            "feature nv overlay",
            f"vlan {tenant_vlan}",
            f"  vn-segment {vni}",
            "!",
            "interface nve1",
            "  no shutdown",
            "  source-interface loopback0",
            f"  member vni {vni}",
            "    ingress-replication protocol static",
        ]
        for r in remotes:
            lines.append(f"      peer-ip {r}")
        lines.append("!")
        return "\n".join(lines)

    if vendor == "arista":
        lines = [
            "! --- VXLAN: decap incoming frames from netgen's VTEP into the",
            "! --- matching tenant VLAN, and set up head-end replication",
            "! --- back to the netgen VTEP so BUM traffic can complete.",
            f"! Netgen VTEP source: {local_ip}, VXLAN UDP port: {udp_port},",
            f"! VNI {vni} maps to VLAN {tenant_vlan}.",
            f"vlan {tenant_vlan}",
            "!",
            "interface Vxlan1",
            "   vxlan source-interface Loopback0",
            f"   vxlan udp-port {udp_port}",
            f"   vxlan vlan {tenant_vlan} vni {vni}",
            f"   vxlan vlan {tenant_vlan} flood vtep " + " ".join(remotes),
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
