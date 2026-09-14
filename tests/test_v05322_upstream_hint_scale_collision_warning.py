"""v0.5.322 — collision warning in Upstream Config Hint when N
scale devices share fields that MUST be unique per device.

Operator report on v0.5.321: BGP scale of 2 emitted two BGP
neighbor blocks with the SAME peer IP (192.168.0.2 on both) —
the operator hadn't ticked the IPv4 increment checkbox. That's
not a hint bug per se, but the hint is what surfaces the problem
to the operator; showing them the paste-body silently would let
them apply an invalid config (BGP refuses two sessions to the
same peer; L2 breaks with duplicate MACs; OSPF/ISIS flap on
duplicate router-id).

Fix: `_detect_scale_collisions` walks the list of N devices and
checks the fields that MUST be unique per device (IPv4/IPv6
address, MAC, loopback v4/v6). When every device has the same
non-empty value in a field → emit a `!!! SCALE COLLISION
WARNING !!!` block at the very top of the hint output, naming
the specific field, the offending value, and the specific
checkbox in the dialog's Increment Options section to tick.
Protocol-agnostic — same warning covers BGP / OSPF / ISIS /
DHCP scale runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


def _bgp_no_incr(count=2):
    """Operator's exact repro: BGP iBGP, count=2, NO increment."""
    base = {
        "device_name": "netgen-device", "vlan": "10",
        "ipv4_address": "192.168.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "192.168.0.1",
        "mac_address": "02:00:00:00:00:01",
        "bgp_config": {
            "bgp_local_as": "65000", "bgp_remote_asn": "65000",
            "bgp_hold_time": "90", "bgp_keepalive": "30",
            "ipv4_enabled": True, "ipv6_enabled": False,
        },
    }
    return u.expand_for_scale(base, {"count": count})


def _bgp_with_incr(count=2):
    """Same shape but with IPv4 + MAC increment on — should NOT warn."""
    base = {
        "device_name": "netgen-device", "vlan": "10",
        "ipv4_address": "192.168.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "192.168.0.1",
        "mac_address": "02:00:00:00:00:01",
        "bgp_config": {
            "bgp_local_as": "65000", "bgp_remote_asn": "65000",
            "bgp_hold_time": "90", "bgp_keepalive": "30",
            "ipv4_enabled": True, "ipv6_enabled": False,
        },
    }
    return u.expand_for_scale(base, {
        "count": count,
        "ipv4": {"on": True, "octet_idx": 0},
        "mac":  {"on": True, "byte_idx": 0},
    })


# ---------- Detector unit tests ----------


def test_detector_returns_empty_for_single_device():
    """A single device can't collide with itself. len(devices) < 2
    returns []."""
    assert u._detect_scale_collisions([]) == []
    assert u._detect_scale_collisions([{"ipv4_address": "10.0.0.1"}]) == []


def test_detector_flags_shared_ipv4():
    warnings = u._detect_scale_collisions(_bgp_no_incr(count=2))
    fields = [w["field"] for w in warnings]
    assert "ipv4_address" in fields


def test_detector_flags_shared_mac():
    warnings = u._detect_scale_collisions(_bgp_no_incr(count=2))
    fields = [w["field"] for w in warnings]
    assert "mac_address" in fields


def test_detector_flags_shared_loopback_ipv4():
    """OSPF/ISIS router-id derives from loopback — duplicate is
    just as bad as duplicate iface IPs."""
    devs = [
        {"loopback_ipv4": "192.255.0.1", "ipv4_address": "10.0.0.2"},
        {"loopback_ipv4": "192.255.0.1", "ipv4_address": "10.0.0.3"},
    ]
    warnings = u._detect_scale_collisions(devs)
    fields = [w["field"] for w in warnings]
    assert "loopback_ipv4" in fields
    # But ipv4_address is UNIQUE across the two, so it must NOT warn.
    assert "ipv4_address" not in fields


def test_detector_no_warning_when_ipv4_incremented():
    """When the operator DOES tick IPv4 increment, per-device
    ipv4_address differs → no warning."""
    warnings = u._detect_scale_collisions(_bgp_with_incr(count=2))
    fields = [w["field"] for w in warnings]
    assert "ipv4_address" not in fields
    assert "mac_address" not in fields


def test_detector_ignores_empty_field():
    """If ipv4_address is empty on every device, that's fine —
    the operator didn't provide one, not a collision."""
    devs = [{"ipv4_address": ""}, {"ipv4_address": ""}]
    assert u._detect_scale_collisions(devs) == []


def test_detector_flags_only_when_all_are_identical():
    """If devices 1 and 2 have the same IPv4 but device 3 has a
    different one, that's still a collision between 1 and 2 —
    but the current logic requires ALL to share. This gives a
    conservative fewer-false-positives default. Verify:"""
    devs = [
        {"ipv4_address": "10.0.0.2"},
        {"ipv4_address": "10.0.0.2"},
        {"ipv4_address": "10.0.0.3"},
    ]
    warnings = u._detect_scale_collisions(devs)
    # 2 out of 3 share → don't warn (only when all share).
    fields = [w["field"] for w in warnings]
    assert "ipv4_address" not in fields


def test_detector_flags_ipv6_address():
    devs = [
        {"ipv6_address": "2001:db8::2"},
        {"ipv6_address": "2001:db8::2"},
    ]
    warnings = u._detect_scale_collisions(devs)
    fields = [w["field"] for w in warnings]
    assert "ipv6_address" in fields


def test_detector_flags_loopback_ipv6():
    devs = [
        {"loopback_ipv6": "2001:db8:ffff::1"},
        {"loopback_ipv6": "2001:db8:ffff::1"},
    ]
    warnings = u._detect_scale_collisions(devs)
    fields = [w["field"] for w in warnings]
    assert "loopback_ipv6" in fields


# ---------- End-to-end render integration ----------


def test_render_emits_collision_banner_when_scale_shares_ipv4():
    """The warning must appear at the TOP of the render — first
    thing the operator sees when opening the Upstream Hint dialog."""
    out = u.render_all(_bgp_no_incr(count=2))["juniper"]
    assert "!!! SCALE COLLISION WARNING !!!" in out
    # Must be at the very top (before the Shared banner).
    warn_idx = out.index("SCALE COLLISION WARNING")
    shared_idx = out.index("Shared upstream config")
    assert warn_idx < shared_idx


def test_render_names_offending_field_and_value():
    """The warning line must name the SPECIFIC field and the
    SPECIFIC offending value so the operator can jump straight
    to the dialog field and fix it."""
    out = u.render_all(_bgp_no_incr(count=2))["juniper"]
    assert "IPv4 address '192.168.0.2'" in out
    assert "MAC address '02:00:00:00:00:01'" in out


def test_render_names_checkbox_to_tick():
    """Actionable — the warning must tell the operator EXACTLY
    which checkbox in the Increment Options section to tick."""
    out = u.render_all(_bgp_no_incr(count=2))["juniper"]
    assert "tick the 'IPv4' checkbox" in out
    assert "tick the 'MAC' checkbox" in out


def test_render_no_warning_when_scale_is_properly_incremented():
    """The whole point — a correctly-configured scale run must
    NOT emit a warning."""
    out = u.render_all(_bgp_with_incr(count=2))["juniper"]
    assert "SCALE COLLISION WARNING" not in out


def test_render_no_warning_for_single_device():
    """A count=1 (or single-dict) render must not carry the
    warning banner — nothing collides with itself."""
    out = u.render_all(_bgp_no_incr(count=1))["juniper"]
    assert "SCALE COLLISION WARNING" not in out
    # Dict form too.
    out = u.render_all({"device_name": "solo", "vlan": "10", "ipv4_address": "10.0.0.2"})["juniper"]
    assert "SCALE COLLISION WARNING" not in out


def test_render_warning_uses_correct_comment_marker_per_vendor():
    """Junos uses `#`, Cisco/Arista use `!`. The warning banner
    must use the vendor's marker so it commented out (not parsed
    as an unknown command)."""
    outs = u.render_all(_bgp_no_incr(count=2))
    for line in outs["juniper"].splitlines():
        if line and "SCALE COLLISION WARNING" in line:
            assert line.startswith("# ")
    for vendor in ("cisco", "arista"):
        for line in outs[vendor].splitlines():
            if line and "SCALE COLLISION WARNING" in line:
                assert line.startswith("! ")


def test_render_covers_all_three_vendors():
    outs = u.render_all(_bgp_no_incr(count=2))
    for vendor in ("juniper", "cisco", "arista"):
        assert "SCALE COLLISION WARNING" in outs[vendor]


def test_render_ospf_scale_no_incr_also_warns():
    """Operator explicitly said 'similar problem with other config
    templates'. OSPF scale without increment must produce the
    same warning (protocol-agnostic detection)."""
    base = {
        "device_name": "ospf-scale", "vlan": "200",
        "ipv4_address": "10.1.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "10.1.0.1",
        "loopback_ipv4": "192.255.0.1",
        "ospf_config": {
            "area_id": "0.0.0.0", "hello_interval": "10",
            "dead_interval": "40", "ipv4_enabled": True,
            "ipv6_enabled": False,
        },
    }
    devs = u.expand_for_scale(base, {"count": 2})
    out = u.render_all(devs)["juniper"]
    assert "SCALE COLLISION WARNING" in out
    assert "IPv4 address '10.1.0.2'" in out
    assert "Loopback IPv4 '192.255.0.1'" in out


def test_render_isis_scale_no_incr_also_warns():
    base = {
        "device_name": "isis-scale", "vlan": "300",
        "ipv4_address": "10.2.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "10.2.0.1",
        "loopback_ipv4": "192.255.0.10",
        "isis_config": {
            "isis_area": "CORE",
            "isis_net": "49.0001.1922.5500.0010.00",
            "isis_level": "level-2-only",
        },
    }
    devs = u.expand_for_scale(base, {"count": 2})
    out = u.render_all(devs)["juniper"]
    assert "SCALE COLLISION WARNING" in out


def test_render_dhcp_scale_no_incr_also_warns():
    """DHCP client scale — same duplicate-MAC/IPv4 problem."""
    base = {
        "device_name": "dhcp-client", "vlan": "10",
        "mac_address": "02:00:00:00:00:aa",
        "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    devs = u.expand_for_scale(base, {"count": 2})
    out = u.render_all(devs)["juniper"]
    assert "SCALE COLLISION WARNING" in out
    assert "MAC address '02:00:00:00:00:aa'" in out


# ---------- Warning banner explains "why" ----------


def test_warning_reason_line_present():
    """Each collision line must be followed by a `Reason:` line
    explaining WHY the collision matters (adjacency won't form,
    packets fight, etc.) so the operator understands the impact."""
    out = u.render_all(_bgp_no_incr(count=2))["juniper"]
    assert "Reason:" in out


# ---------- AST parse ----------


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())
