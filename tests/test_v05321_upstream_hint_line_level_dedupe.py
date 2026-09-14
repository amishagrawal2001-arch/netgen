"""v0.5.321 — line-level dedupe in scale-mode Upstream Hint.

Operator report on v0.5.320: BGP/OSPF/ISIS scale still emits
duplicate LINES within blocks — e.g. `set routing-options
autonomous-system 65000` appears N times because it's inside the
BGP block, and the BGP block varies per-device (the group name
carries the `-N` suffix). Same story for interface `vlan-tagging`,
`unit N vlan-id N`, `family inet address <gw>/<mask>` — all
shared but held inside a block that varies per-device (the
description line).

Fix (line-level dedupe):
  * When a block varies per-device but SHAPE matches across
    devices (same line count), split by newline and classify
    each line-position (K) as SHARED (identical text at position
    K on every device) or PER_DEVICE.
  * Hoist SHARED lines into the shared banner section.
  * Per-device sections carry only their unique lines.

Also required for line-level dedupe to reach the interface
stanza: `_render` used to join header + iface with a single
newline, gluing them into one block. Change to blank-line
separator so iface is its own block eligible for line-level
dedupe.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


def _bgp_scale(count=3):
    base = {
        "device_name": "bgp-scale", "vlan": "100",
        "ipv4_address": "10.0.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "10.0.0.1",
        "bgp_config": {
            "bgp_local_as": "65001", "bgp_remote_asn": "65000",
            "bgp_hold_time": "90", "bgp_keepalive": "30",
            "ipv4_enabled": True, "ipv6_enabled": False,
        },
    }
    return u.expand_for_scale(base, {
        "count": count, "ipv4": {"on": True, "octet_idx": 0},
    })


def _ospf_scale(count=3):
    base = {
        "device_name": "ospf-scale", "vlan": "200",
        "ipv4_address": "10.1.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "10.1.0.1",
        "ospf_config": {
            "area_id": "0.0.0.0",
            "hello_interval": "10", "dead_interval": "40",
            "ipv4_enabled": True, "ipv6_enabled": False,
            "p2p_ipv4": True,
        },
    }
    return u.expand_for_scale(base, {
        "count": count, "ipv4": {"on": True, "octet_idx": 0},
    })


# ---------- (a) Interface shared lines hoisted ----------


def test_iface_vlan_tagging_hoisted_when_vlan_shared():
    """v0.5.321 fix: `_render` now joins header + iface with a
    blank line so iface participates in line-level dedupe.
    `set interfaces ge-0/0/0 vlan-tagging` is identical across
    all N devices — must appear exactly once."""
    out = u.render_all(_bgp_scale())["juniper"]
    assert out.count("set interfaces ge-0/0/0 vlan-tagging") == 1


def test_iface_unit_vlan_id_hoisted_when_vlan_shared():
    out = u.render_all(_bgp_scale())["juniper"]
    # `unit 100 vlan-id 100` — identical across all devices.
    assert out.count("set interfaces ge-0/0/0 unit 100 vlan-id 100") == 1


def test_iface_family_inet_gateway_hoisted_when_gateway_shared():
    """`family inet address 10.0.0.1/24` — identical when gateway
    doesn't increment. Must be hoisted."""
    out = u.render_all(_bgp_scale())["juniper"]
    assert out.count("set interfaces ge-0/0/0 unit 100 family inet address 10.0.0.1/24") == 1


def test_iface_description_stays_per_device():
    """The description carries `peer:<name>` which varies per
    device (name has `-N` suffix). Must appear N times, not
    hoisted."""
    out = u.render_all(_bgp_scale())["juniper"]
    assert 'description "peer:bgp-scale"' in out
    assert 'description "peer:bgp-scale-2"' in out
    assert 'description "peer:bgp-scale-3"' in out


# ---------- (b) BGP shared lines hoisted ----------


def test_bgp_autonomous_system_hoisted_across_scale():
    """`set routing-options autonomous-system 65000` is identical
    across every BGP-scale device. Must appear ONCE."""
    out = u.render_all(_bgp_scale())["juniper"]
    assert out.count("set routing-options autonomous-system 65000") == 1


def test_bgp_per_device_group_lines_appear_per_device():
    """The `NETGEN-<name> peer-as`, `hold-time`, `type external`,
    `neighbor <ip>` lines all carry the per-device group name
    suffix — must NOT be hoisted. Each appears N times, one per
    device."""
    out = u.render_all(_bgp_scale())["juniper"]
    for name in ("bgp-scale", "bgp-scale-2", "bgp-scale-3"):
        assert f"set protocols bgp group NETGEN-{name} type external" in out
        assert f"set protocols bgp group NETGEN-{name} peer-as 65001" in out
        assert f"set protocols bgp group NETGEN-{name} hold-time 90" in out


def test_bgp_neighbor_ip_incremented_per_device():
    out = u.render_all(_bgp_scale())["juniper"]
    assert "neighbor 10.0.0.2 description" in out
    assert "neighbor 10.0.0.3 description" in out
    assert "neighbor 10.0.0.4 description" in out


# ---------- (c) OSPF shared lines hoisted ----------


def test_ospf_interface_stanza_lines_all_shared():
    """OSPF stanza references `ge-0/0/0.200` on every device
    (VLAN doesn't increment). Every line inside the OSPF stanza
    is shared → the WHOLE stanza is block-level identical and
    gets emitted once."""
    out = u.render_all(_ospf_scale())["juniper"]
    for line in (
        "set protocols ospf area 0.0.0.0 interface ge-0/0/0.200 hello-interval 10",
        "set protocols ospf area 0.0.0.0 interface ge-0/0/0.200 dead-interval 40",
        "set protocols ospf area 0.0.0.0 interface ge-0/0/0.200 interface-type p2p",
    ):
        assert out.count(line) == 1


def test_ospf_scale_has_shared_banner():
    out = u.render_all(_ospf_scale())["juniper"]
    assert "Shared upstream config (applies to all 3 netgen devices)" in out


# ---------- (d) Line-level dedupe requires shape match ----------


def test_shape_mismatch_falls_back_to_per_device_whole_block():
    """If block shapes disagree across devices (different line
    count), line-level dedupe is unsafe — position K on device
    A might not be the same "kind" of line as position K on
    device B. Fall back to whole-block per-device."""
    # Manually construct two devices whose iface blocks have
    # different line counts: dev0 has ipv6_gateway (so v6 line
    # is present), dev1 doesn't.
    dev0 = {
        "device_name": "shape-a", "vlan": "50",
        "ipv4_gateway": "10.5.0.1", "ipv4_mask": "24",
        "ipv6_gateway": "2001:db8::1", "ipv6_mask": "64",
    }
    dev1 = {
        "device_name": "shape-b", "vlan": "50",
        "ipv4_gateway": "10.5.0.1", "ipv4_mask": "24",
        # No ipv6_gateway.
    }
    out = u.render_all([dev0, dev1])["juniper"]
    # The v6 line is on dev0 only — must still appear.
    assert "family inet6 address 2001:db8::1/64" in out
    # Nothing in the shared banner because shapes mismatch.
    # (The header banner is emitted only if there IS a shared
    # block; check that shared iface lines were NOT hoisted.)
    # Dev0's line "family inet address 10.5.0.1/24" still lives
    # per-device even though it's identical text — safety fallback.
    assert out.count("set interfaces ge-0/0/0 unit 50 family inet address 10.5.0.1/24") == 2


# ---------- (e) Cisco / Arista line-level dedupe ----------


def test_cisco_bgp_scale_dedupes_router_bgp_line():
    """Cisco emits `router bgp <asn>` — identical when local_asn
    is shared. Must be hoisted."""
    out = u.render_all(_bgp_scale())["cisco"]
    assert out.count("router bgp 65000") == 1


def test_arista_bgp_scale_dedupes_router_bgp_line():
    out = u.render_all(_bgp_scale())["arista"]
    assert out.count("router bgp 65000") == 1


# ---------- (f) _render header separator change ----------


def test_render_header_and_sections_separated_by_blank_line():
    """The regression-guard for the v0.5.321 fix in `_render`:
    header MUST be its own block. Sanity check by splitting a
    single-device render by `\\n\\n` — first element is header
    only (no iface lines), second is iface_stanza."""
    dev = {"device_name": "one", "vlan": "10", "ipv4_gateway": "10.0.0.1", "ipv4_mask": "24"}
    out = u.render_juniper(dev)
    blocks = [b for b in out.split("\n\n") if b.strip()]
    assert blocks[0].startswith("# Upstream config for netgen device 'one'")
    assert "set interfaces" not in blocks[0]  # header block has no iface lines
    assert "set interfaces ge-0/0/0" in blocks[1]  # iface_stanza is block 1


# ---------- AST parse ----------


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())
