"""v0.5.319 — Upstream Config Hint:
  (a) Cisco/Arista DHCP-relay parity with Juniper (more knobs so the
      operator sees the vendor-neutral concepts side-by-side).
  (b) Scale expansion: when the Increment section is on (count > 1),
      emit N per-device stanzas — one per netgen device the dialog
      is about to create — instead of just the base device.

Operator report on v0.5.318: Cisco/Arista relay stanzas had only 2
substantive lines (`interface Vlan<N>` + `ip helper-address`), less
than Juniper's 5, so parity felt thin. Also asked for the hint to
"automatically generate scale configs based on selected increment
parameters" so that a 100-device scale device doesn't produce 1
stanza.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


# ---------- (a) Cisco / Arista DHCP-relay parity beefup ----------


def _client_dev(vlan="10"):
    return {
        "device_name": "c1",
        "vlan": vlan,
        "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }


def test_cisco_relay_includes_service_dhcp():
    """v0.5.319 added `service dhcp` global so the operator sees
    the global switch explicitly. Pre-fix, IOS relay stanza was
    silent about it (relied on IOS default = on)."""
    out = u.render_cisco(_client_dev())
    assert "service dhcp" in out


def test_cisco_relay_includes_trust_all():
    """Parity with Junos `overrides allow-snooped-clients`."""
    out = u.render_cisco(_client_dev())
    assert "ip dhcp relay information trust-all" in out


def test_cisco_relay_still_emits_ip_helper_address():
    """The actual forwarding directive must still be there — the
    beefup added surrounding context, not replaced the core line."""
    out = u.render_cisco(_client_dev())
    assert "interface Vlan10" in out
    assert "ip helper-address" in out


def test_arista_relay_includes_relay_info_option():
    """Parity with the option-82 tagging that Junos does implicitly
    via the server-group construct."""
    out = u.render_arista(_client_dev())
    assert "ip dhcp relay information option" in out


def test_arista_relay_still_emits_ip_helper_address():
    out = u.render_arista(_client_dev())
    assert "interface Vlan10" in out
    assert "ip helper-address" in out


def test_juniper_relay_unchanged_by_v0319():
    """Juniper was already complete — must not regress."""
    out = u.render_juniper(_client_dev())
    for line in (
        "set forwarding-options dhcp-relay overrides allow-snooped-clients",
        "set forwarding-options dhcp-relay forward-only",
        "set forwarding-options dhcp-relay server-group DHCP-SERVERS",
        "set forwarding-options dhcp-relay active-server-group DHCP-SERVERS",
        "set forwarding-options dhcp-relay group CLIENTS interface irb.10",
    ):
        assert line in out


# ---------- (b) Scale expansion ----------


def _base_bgp():
    return {
        "device_name": "netgen-device",
        "vlan": "100",
        "ipv4_address": "10.0.0.2",
        "ipv4_mask": "24",
        "ipv4_gateway": "10.0.0.1",
        "mac_address": "00:11:22:33:44:01",
        "bgp_config": {
            "bgp_local_as": "65001",
            "bgp_remote_asn": "65000",
            "bgp_hold_time": "90",
            "bgp_keepalive": "30",
            "ipv4_enabled": True,
            "ipv6_enabled": False,
        },
    }


def test_expand_for_scale_count_one_returns_single_dict_wrapped():
    """Count == 1 → list of one. Simplifies caller code."""
    out = u.expand_for_scale(_base_bgp(), {"count": 1})
    assert len(out) == 1
    assert out[0]["device_name"] == "netgen-device"


def test_expand_for_scale_count_three_returns_three():
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3,
        "mac":  {"on": True, "byte_idx": 0},
        "ipv4": {"on": True, "octet_idx": 0},
    })
    assert len(out) == 3


def test_expand_for_scale_first_device_is_base_verbatim():
    """i=0 is the unmodified base — matches the netgen-side
    convention where iteration-0 == what the operator typed."""
    base = _base_bgp()
    out = u.expand_for_scale(base, {
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })
    assert out[0]["device_name"] == "netgen-device"
    assert out[0]["ipv4_address"] == "10.0.0.2"


def test_expand_ipv4_increments_last_octet():
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })
    assert out[0]["ipv4_address"] == "10.0.0.2"
    assert out[1]["ipv4_address"] == "10.0.0.3"
    assert out[2]["ipv4_address"] == "10.0.0.4"


def test_expand_ipv4_off_leaves_addresses_unchanged():
    """Increment must be gated on the per-field checkbox — not
    every scale run touches IPv4."""
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3,
        "ipv4": {"on": False, "octet_idx": 0},
        "mac":  {"on": True,  "byte_idx": 0},
    })
    for dev in out:
        assert dev["ipv4_address"] == "10.0.0.2"


def test_expand_mac_increments_last_byte():
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3, "mac": {"on": True, "byte_idx": 0},
    })
    assert out[0]["mac_address"] == "00:11:22:33:44:01"
    assert out[1]["mac_address"] == "00:11:22:33:44:02"
    assert out[2]["mac_address"] == "00:11:22:33:44:03"


def test_expand_mac_carries_across_byte_boundary():
    """Step > 255 into a single byte must overflow LEFT — same
    behavior as widgets/devices_tab._increment_mac."""
    base = _base_bgp()
    base["mac_address"] = "00:11:22:33:44:ff"
    out = u.expand_for_scale(base, {
        "count": 2, "mac": {"on": True, "byte_idx": 0},
    })
    assert out[1]["mac_address"] == "00:11:22:33:45:00"


def test_expand_vlan_increments_when_on():
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3, "vlan": {"on": True},
    })
    assert out[0]["vlan"] == "100"
    assert out[1]["vlan"] == "101"
    assert out[2]["vlan"] == "102"


def test_expand_device_name_suffix_for_i_gt_zero():
    """i=0 keeps the base name; i>=1 gets `-<i+1>` so the emitted
    stanzas don't collide on `peer:netgen-device`."""
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })
    assert out[0]["device_name"] == "netgen-device"
    assert out[1]["device_name"] == "netgen-device-2"
    assert out[2]["device_name"] == "netgen-device-3"


def test_expand_gateway_can_be_pinned_when_off():
    """Common lab pattern: all N devices share the same upstream
    gateway. gateway.on=False → gateway stays constant."""
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3,
        "ipv4":    {"on": True,  "octet_idx": 0},
        "gateway": {"on": False, "octet_idx": 0},
    })
    for dev in out:
        assert dev["ipv4_gateway"] == "10.0.0.1"


def test_expand_gateway_increments_when_on():
    out = u.expand_for_scale(_base_bgp(), {
        "count": 3,
        "gateway": {"on": True, "octet_idx": 0},
    })
    assert out[0]["ipv4_gateway"] == "10.0.0.1"
    assert out[1]["ipv4_gateway"] == "10.0.0.2"


def test_expand_deepcopies_bgp_config():
    """Increment must not mutate the shared bgp_config sub-dict.
    Pre-fix (if we forgot deepcopy) `out[1].bgp_config is out[0].
    bgp_config` — modifying one would smash the other."""
    base = _base_bgp()
    out = u.expand_for_scale(base, {
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })
    assert out[0]["bgp_config"] is not base["bgp_config"]
    assert out[1]["bgp_config"] is not out[0]["bgp_config"]


# ---------- (b') End-to-end render_all with list ----------


def test_render_all_accepts_list_form():
    devs = u.expand_for_scale(_base_bgp(), {
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })
    out = u.render_all(devs)
    assert set(out.keys()) == {"juniper", "cisco", "arista"}
    # Three distinct peer descriptions per vendor.
    assert out["juniper"].count('description "peer:netgen-device"') == 1
    assert out["juniper"].count('description "peer:netgen-device-2"') == 1
    assert out["juniper"].count('description "peer:netgen-device-3"') == 1


def test_render_all_list_separates_by_divider():
    """A visual divider between per-device stanzas so the operator
    can scan the boundary at a glance."""
    devs = u.expand_for_scale(_base_bgp(), {
        "count": 2, "ipv4": {"on": True, "octet_idx": 0},
    })
    out = u.render_all(devs)
    assert "====" in out["juniper"]
    assert "====" in out["cisco"]


def test_render_all_dict_form_unchanged():
    """Backward compat — pre-v0.5.319 callers pass a single dict."""
    out = u.render_all(_base_bgp())
    assert set(out.keys()) == {"juniper", "cisco", "arista"}
    assert 'peer:netgen-device' in out["juniper"]


def test_render_all_bgp_neighbors_incremented_per_device():
    """The BGP neighbor line MUST reflect the per-i incremented
    IPv4, not the base IPv4. Otherwise the upstream stanza has
    N identical BGP neighbors — useless."""
    devs = u.expand_for_scale(_base_bgp(), {
        "count": 3, "ipv4": {"on": True, "octet_idx": 0},
    })
    out = u.render_all(devs)
    # Each device's stanza names its own IP as the neighbor.
    assert "neighbor 10.0.0.2" in out["juniper"]
    assert "neighbor 10.0.0.3" in out["juniper"]
    assert "neighbor 10.0.0.4" in out["juniper"]
    # And Cisco form.
    assert "neighbor 10.0.0.2 remote-as 65001" in out["cisco"]
    assert "neighbor 10.0.0.3 remote-as 65001" in out["cisco"]
    assert "neighbor 10.0.0.4 remote-as 65001" in out["cisco"]


# ---------- Dialog snapshot integration ----------


def _dialog_src():
    return (_REPO / "widgets" / "add_device_dialog.py").read_text()


def test_dialog_snapshot_calls_expand_for_scale():
    src = _dialog_src()
    assert "from utils.upstream_hints import expand_for_scale" in src
    assert "expand_for_scale(data, increment_meta)" in src


def test_dialog_snapshot_reads_increment_count():
    src = _dialog_src()
    idx = src.index("v0.5.319 (audit upstream-hint-scale)")
    body = src[idx:idx + 5000]
    assert "self.increment_count.value()" in body


def test_dialog_snapshot_gates_scale_on_count_gt_one():
    """count == 1 → single-dict form (backward compat with the
    v0.5.318 render_all path)."""
    src = _dialog_src()
    idx = src.index("v0.5.319 (audit upstream-hint-scale)")
    body = src[idx:idx + 5000]
    assert "if _incr_count > 1:" in body


def test_dialog_snapshot_carries_all_five_increment_axes():
    """MAC + IPv4 + IPv6 + gateway + VLAN — mirrors the dialog's
    five increment checkboxes (line ~513-519)."""
    src = _dialog_src()
    idx = src.index("v0.5.319 (audit upstream-hint-scale)")
    body = src[idx:idx + 5000]
    for axis in ('"mac":', '"ipv4":', '"ipv6":', '"gateway":', '"vlan":'):
        assert axis in body


# ---------- Upstream hint dialog title reflects count ----------


def test_dialog_title_shows_x_N_for_scale():
    """Window title `Upstream Router Config Hint — <name> × N`
    when the caller passes a list, so the operator visually
    knows this covers N devices."""
    src = (_REPO / "widgets" / "upstream_hint_dialog.py").read_text()
    assert "isinstance(device_data, list)" in src
    assert 'f"Upstream Router Config Hint — {name} × {len(device_data)}"' in src


# ---------- AST parse ----------


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())


def test_dialog_ast_parses():
    import ast
    ast.parse((_REPO / "widgets" / "add_device_dialog.py").read_text())


def test_upstream_hint_dialog_ast_parses():
    import ast
    ast.parse((_REPO / "widgets" / "upstream_hint_dialog.py").read_text())
