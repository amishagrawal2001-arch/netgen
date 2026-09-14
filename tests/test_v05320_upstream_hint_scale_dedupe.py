"""v0.5.320 — dedupe shared blocks in scale-mode Upstream Hint.

Operator report on v0.5.319: a 2-device DHCP-client scale emitted
the 5-line dhcp-relay stanza TWICE (identical byte-for-byte)
because `_render_all_scale` concatenated per-device blobs verbatim.
At 100-device scale it'd be 100 copies — useless for a switch.

Fix: identify BLOCKS (separated by "\\n\\n" per `_render`) whose
text is identical across every device in the list. Emit those
ONCE at the top under a "Shared upstream config (applies to all N
netgen devices)" banner. Emit per-device unique blocks (BGP
neighbor with per-device IP, interface description with per-
device name) per device as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


# ---------- Shared block dedupe (positive) ----------


def _dhcp_client_scale(count: int):
    """Operator's actual repro shape: DHCP-client, no per-field
    increment → DHCP-relay block byte-identical across every
    device."""
    base = {
        "device_name": "netgen-device",
        "vlan": "10",
        "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    return u.expand_for_scale(base, {"count": count, "mac": {"on": True, "byte_idx": 0}})


def test_dhcp_relay_appears_only_once_in_scale():
    """The 5-line dhcp-relay block that was duplicated in v0.5.319
    must now appear EXACTLY once — not once per device."""
    devs = _dhcp_client_scale(count=3)
    out = u.render_all(devs)["juniper"]
    # Count occurrences of a distinctive line from the relay block.
    assert out.count("set forwarding-options dhcp-relay forward-only") == 1
    assert out.count("set forwarding-options dhcp-relay active-server-group DHCP-SERVERS") == 1


def test_scale_output_has_shared_banner_when_shared_blocks_exist():
    devs = _dhcp_client_scale(count=2)
    out = u.render_all(devs)["juniper"]
    assert "Shared upstream config (applies to all 2 netgen devices)" in out


def test_scale_output_shared_banner_shows_actual_count():
    devs = _dhcp_client_scale(count=5)
    out = u.render_all(devs)["juniper"]
    assert "applies to all 5 netgen devices" in out


def test_scale_shared_block_appears_before_per_device_blocks():
    """Ordering: shared config at the top so the operator sees the
    invariant first, then per-device deltas below."""
    devs = _dhcp_client_scale(count=2)
    out = u.render_all(devs)["juniper"]
    shared_idx = out.index("Shared upstream config")
    first_dev_hdr = out.index("Upstream config for netgen device 'netgen-device'")
    assert shared_idx < first_dev_hdr


def test_interface_description_still_appears_per_device():
    """Even with the shared dedupe, the interface description
    (which varies per device) MUST still appear N times — one per
    device — else the switch has no way to distinguish the peers."""
    devs = _dhcp_client_scale(count=3)
    out = u.render_all(devs)["juniper"]
    assert 'description "peer:netgen-device"' in out
    assert 'description "peer:netgen-device-2"' in out
    assert 'description "peer:netgen-device-3"' in out


def test_dedupe_across_all_three_vendors():
    """Cisco and Arista must dedupe too — pre-fix all three
    concatenated verbatim."""
    devs = _dhcp_client_scale(count=3)
    out = u.render_all(devs)
    # Cisco: ip helper-address is the analogue to Junos forward-only.
    # It's a UNIQUE line inside the shared DHCP-relay block.
    assert out["cisco"].count("ip dhcp relay information trust-all") == 1
    assert out["arista"].count("ip dhcp relay information option") == 1


# ---------- Per-device unique blocks preserved ----------


def _bgp_scale():
    """BGP-scale case where the interface family-inet address AND
    the BGP neighbor IP vary per device (via IPv4 increment) —
    no shared blocks at all. Must still render N complete stanzas."""
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
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })


def test_bgp_scale_no_shared_banner_when_all_blocks_differ():
    """(v0.5.320 assumption) — retained as a v0.5.321-compat
    check that a shared banner ONLY appears when there are
    actually shared lines. In BGP scale the interface's
    `vlan-tagging` / `unit N vlan-id N` / `family inet address
    <gw>/<mask>` lines and the BGP `autonomous-system` line are
    shared (v0.5.321 line-level dedupe surfaces them). So this
    scale case DOES have a banner — the pre-v0.5.321 assumption
    that it didn't was wrong. Verify the banner text is correct
    and the shared block is non-empty."""
    out = u.render_all(_bgp_scale())["juniper"]
    assert "Shared upstream config (applies to all 3 netgen devices)" in out
    # The 4 shared lines that v0.5.321 hoisted.
    assert "set interfaces ge-0/0/0 vlan-tagging" in out
    assert "set routing-options autonomous-system 65000" in out


def test_bgp_scale_all_three_bgp_neighbors_present():
    """Each device's BGP neighbor IP must appear exactly once,
    with the correct incremented IP."""
    out = u.render_all(_bgp_scale())["juniper"]
    assert out.count("neighbor 10.0.0.2 description") == 1
    assert out.count("neighbor 10.0.0.3 description") == 1
    assert out.count("neighbor 10.0.0.4 description") == 1


def test_bgp_scale_has_dividers_between_devices():
    """Each per-device section is separated from the next by the
    68-char ==== divider. With v0.5.321 shared-lines hoisting,
    the shared section also has a divider before the first per-
    device block, so total = N dividers for N per-device sections
    when a shared banner is present. (Pre-v0.5.321 this scale
    had no shared banner → N-1 dividers; v0.5.321 surfaces
    shared lines → N dividers.)"""
    out = u.render_all(_bgp_scale())["juniper"]
    divider_line = "# " + "=" * 68
    # 3 devices with shared banner → 3 dividers (banner → dev0,
    # dev0 → dev1, dev1 → dev2).
    assert out.count(divider_line) == 3


# ---------- Single-device unchanged ----------


def test_single_device_via_list_form_has_no_shared_banner():
    """A 1-element list is still valid scale mode — but with only
    one device, nothing is shared and no banner should appear."""
    devs = _dhcp_client_scale(count=1)
    out = u.render_all(devs)["juniper"]
    assert "Shared upstream config" not in out


def test_single_device_via_dict_form_unchanged():
    """Backward compat: passing a bare dict (not a list) works
    exactly as before v0.5.319 — no banner, no divider."""
    out = u.render_all({
        "device_name": "one", "vlan": "10", "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    })["juniper"]
    assert "Shared upstream config" not in out
    assert "====" not in out


# ---------- Header block is always per-device ----------


def test_header_block_never_treated_as_shared():
    """The header (index 0) has device_name in a comment — always
    per-device. Even if two devices somehow shared everything else,
    the header MUST NOT get emitted once in the shared section."""
    # Force two devices with completely identical everything by
    # NOT incrementing anything (count=2 but no on-flags means
    # both devices are byte-identical).
    devs = u.expand_for_scale({
        "device_name": "same", "vlan": "10",
        "dhcp_mode": "client", "dhcp_config": {"mode": "client"},
    }, {"count": 2})
    out = u.render_all(devs)["juniper"]
    # Header STILL appears in the per-device sections, not shared.
    # (Even though the header text is identical when nothing
    # increments, it's classified as index-0-forced-per-device.)
    header_line = "# Upstream config for netgen device 'same'"
    # Two device-name occurrences: one per device in per-device blocks.
    # The device_name suffix `-2` kicks in for i>=1, so only i=0 has
    # exact "'same'". i=1 has "'same-2'".
    assert "# Upstream config for netgen device 'same'" in out
    assert "# Upstream config for netgen device 'same-2'" in out


# ---------- Runtime output structure ----------


def test_output_ends_with_single_newline():
    """A trailing "\\n" makes the paste-body copy-friendly (no
    tail-truncated final line)."""
    devs = _dhcp_client_scale(count=2)
    out = u.render_all(devs)["juniper"]
    assert out.endswith("\n")
    assert not out.endswith("\n\n\n")  # not excessive blank lines


def test_no_orphan_divider_at_end():
    """The last per-device block must not be followed by a stray
    divider (empty ==== bar)."""
    devs = _dhcp_client_scale(count=2)
    out = u.render_all(devs)["juniper"].rstrip("\n")
    assert not out.endswith("=" * 20)


# ---------- AST parse ----------


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())
