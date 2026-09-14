"""v0.5.325 — three-way audit fix:
  (1) DHCP v4-server vs v6-server templates verified distinct;
  (2) ROCEv2 + VXLAN upstream-hint stanzas added (per-vendor);
  (3) MAC address from Add-Device dialog now actually applied to
      the Linux interface at `/api/device/apply` time.

Operator report (2026-09-14): three issues on top of the scale
work:
  1. dhcp ipv4 server and dhcp ipv6 server template is using
     same config;
  2. no config suggestion for ROCEv2 and VXLAN;
  3. user configured MAC address is not being applied on the
     device interface for vlan and non vlan.

Issue 1 is verified false — the templates ARE distinct at
source (different subnets, different field values). Test pins
that.

Issue 2 is real — pre-fix `utils/upstream_hints._render` had
no rocev2/vxlan branch and the dialog snapshot omitted their
config dicts. Fixed.

Issue 3 is real and functional — `/api/device/apply` extracted
neither `data.get("mac")` nor `data.get("mac_address")`, so the
kernel-auto-assigned MAC stayed put no matter what the operator
typed in the dialog. Fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _dialog_src():
    return (_REPO / "widgets" / "add_device_dialog.py").read_text()


def _templates_src():
    return (_REPO / "utils" / "device_templates.py").read_text()


# ==================================================================
# Issue #1 — DHCPv4 vs DHCPv6 server templates ARE distinct
# ==================================================================


def _template_block(src: str, key: str) -> str:
    """Extract the body of a specific template (from its `key=...`
    line up to the next `_Template(` block start), so an over-wide
    slice doesn't include the neighboring template."""
    start = src.index(f'key="{key}",')
    end = src.find("_Template(", start + 1)
    return src[start:end] if end > 0 else src[start:]


def test_dhcpv4_server_template_has_ipv4_pool_only():
    body = _template_block(_templates_src(), "dhcp_server")
    # v4 pool + subnet + v4 sub-toggle on
    assert '"dhcp_pool_start_input": "172.16.30.10"' in body
    assert '"dhcp_pool_end_input": "172.16.30.200"' in body
    assert '"ipv4_checkbox": True' in body
    # And NOT the v6 fields
    assert 'dhcp6_pool_start_input' not in body
    assert '"ipv6_input"' not in body


def test_dhcpv6_server_template_has_ipv6_pool_only():
    body = _template_block(_templates_src(), "dhcp_server_ipv6")
    # v6 pool + subnet + v6 sub-toggle on
    assert '"dhcp6_pool_start_input": "2001:db8:30::100"' in body
    assert '"dhcp6_pool_end_input": "2001:db8:30::1ff"' in body
    assert '"ipv6_checkbox": True' in body
    assert '"dhcp_ipv6_enabled_checkbox": True' in body
    # And NOT the v4 pool fields
    assert 'dhcp_pool_start_input' not in body
    assert '"ipv4_input"' not in body


def test_dhcpv4_and_dhcpv6_render_distinct_hint_output():
    """End-to-end: the upstream hint MUST render distinct output
    for the two templates. v4 emits `family inet` on the
    interface; v6 emits `family inet6`."""
    v4 = {"device_name": "v4srv", "vlan": "10",
          "ipv4_address": "172.16.30.1", "ipv4_mask": "24",
          "ipv4_gateway": "172.16.30.1",
          "dhcp_mode": "server", "dhcp_config": {"mode": "server"}}
    v6 = {"device_name": "v6srv", "vlan": "10",
          "ipv6_address": "2001:db8:30::1", "ipv6_mask": "64",
          "ipv6_gateway": "2001:db8:30::1",
          "dhcp_mode": "server", "dhcp_config": {"mode": "server"}}
    v4_out = u.render_juniper(v4)
    v6_out = u.render_juniper(v6)
    assert v4_out != v6_out
    assert "family inet " in v4_out or "family inet\n" in v4_out or "family inet " in v4_out.replace("inet6", "___")
    assert "family inet6" in v6_out


# ==================================================================
# Issue #2 — ROCEv2 + VXLAN upstream-hint stanzas
# ==================================================================


def _rocev2_device():
    return {
        "device_name": "r1", "vlan": "10",
        "ipv4_address": "10.0.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "10.0.0.1",
        "rocev2_config": {
            "rocev2_priority": "3",
            "rocev2_dscp": "26",
            "rocev2_udp_port": "4791",
        },
    }


def _vxlan_device():
    return {
        "device_name": "v1", "vlan": "10",
        "ipv4_address": "10.0.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "10.0.0.1",
        "vxlan_config": {
            "vni": "10100",
            "local_ip": "192.255.1.1",
            "remote_peers": ["192.255.1.2", "192.255.1.3"],
            "udp_port": "4789",
            "vlan_id": "100",
        },
    }


def test_rocev2_juniper_stanza_emitted():
    out = u.render_juniper(_rocev2_device())
    assert "RoCEv2" in out
    assert "class-of-service classifiers ieee-802.1" in out
    # Priority 3 must appear in the classifier code-point.
    assert "code-points 003" in out
    # PFC on the priority.
    assert "congestion-notification-profile PFC-ROCE" in out


def test_rocev2_cisco_stanza_emitted():
    out = u.render_cisco(_rocev2_device())
    assert "RoCEv2" in out
    assert "class-map type qos" in out
    assert "match cos 3" in out
    assert "match dscp 26" in out
    assert "priority-flow-control mode on" in out


def test_rocev2_arista_stanza_emitted():
    out = u.render_arista(_rocev2_device())
    assert "RoCEv2" in out
    assert "priority-flow-control on" in out
    assert "priority-flow-control priority 3 no-drop" in out


def test_rocev2_stanza_carries_udp_port_annotation():
    """The comment must name UDP/<port> so operator knows which
    port the classifier applies to."""
    out = u.render_juniper(_rocev2_device())
    assert "UDP/4791" in out


def test_no_rocev2_stanza_when_config_absent():
    """A non-RoCEv2 device must NOT get a RoCEv2 stanza."""
    dev = {"device_name": "plain", "vlan": "10", "ipv4_address": "10.0.0.2"}
    out = u.render_juniper(dev)
    assert "RoCEv2" not in out
    assert "ROCE-CLASSIFIER" not in out


def test_vxlan_juniper_stanza_emitted():
    out = u.render_juniper(_vxlan_device())
    assert "VXLAN" in out
    assert "vni 10100" in out
    assert "vlan-id 100" in out
    # Both peers should appear as comments
    assert "192.255.1.2" in out
    assert "192.255.1.3" in out


def test_vxlan_cisco_stanza_has_nve_and_peers():
    out = u.render_cisco(_vxlan_device())
    assert "interface nve1" in out
    assert "member vni 10100" in out
    assert "peer-ip 192.255.1.2" in out
    assert "peer-ip 192.255.1.3" in out


def test_vxlan_arista_stanza_has_vxlan1_and_flood_list():
    out = u.render_arista(_vxlan_device())
    assert "interface Vxlan1" in out
    assert "vxlan vlan 100 vni 10100" in out
    assert "192.255.1.2" in out and "192.255.1.3" in out


def test_vxlan_stanza_carries_udp_port_annotation():
    out = u.render_juniper(_vxlan_device())
    assert "VXLAN UDP port: 4789" in out


def test_no_vxlan_stanza_when_config_absent():
    dev = {"device_name": "plain", "vlan": "10", "ipv4_address": "10.0.0.2"}
    out = u.render_juniper(dev)
    assert "VXLAN" not in out
    assert "vni" not in out.lower()


def test_vxlan_with_no_peers_shows_placeholder():
    """If the operator didn't fill in remote peers, hint must
    show a `<remote-VTEP-IP>` placeholder so they know to fill
    something in — not just silently omit peers."""
    dev = {
        "device_name": "v1", "vlan": "10",
        "vxlan_config": {"vni": "10100", "vlan_id": "100"},
    }
    out = u.render_juniper(dev)
    assert "<remote-VTEP-IP>" in out


# ==================================================================
# Issue #2 (cont.) — dialog snapshot wires rocev2 + vxlan config
# ==================================================================


def test_snapshot_emits_rocev2_config_when_enabled():
    src = _dialog_src()
    idx = src.index("v0.5.325 (audit rocev2-vxlan-upstream-hint)")
    body = src[idx:idx + 3000]
    assert 'checked("rocev2_enable_checkbox")' in body
    assert 'data["rocev2_config"]' in body
    assert "rocev2_priority_input" in body
    assert "rocev2_dscp_input" in body
    assert "rocev2_udp_port_input" in body


def test_snapshot_emits_vxlan_config_when_enabled():
    src = _dialog_src()
    # Second v0.5.325 marker for the VXLAN block
    marker = "v0.5.325: emit vxlan_config"
    assert marker in src
    idx = src.index(marker)
    body = src[idx:idx + 3000]
    assert 'checked("vxlan_enable_checkbox")' in body
    assert 'data["vxlan_config"]' in body
    assert "vxlan_vni_input" in body
    assert "vxlan_local_ip_input" in body
    assert "vxlan_remote_input" in body


# ==================================================================
# Issue #3 — MAC address is now applied to the interface
# ==================================================================


def test_server_extracts_mac_from_apply_payload():
    """Pre-fix the /api/device/apply handler destructured every
    field EXCEPT mac. Client sent it (widgets/devices_tab.py:
    ~10861 `"mac": device_info.get("MAC Address")`) → server
    ignored it. Regression guard: MAC extraction MUST live in
    the apply handler."""
    src = _server_src()
    # Extraction line — accepts both mac and mac_address keys.
    assert 'mac_address = (data.get("mac") or data.get("mac_address")' in src


def test_server_runs_ip_link_set_address_when_mac_provided():
    """The extraction alone doesn't help — the value must
    reach an `ip link set <iface> address <mac>` call."""
    src = _server_src()
    # Search for the apply-block marker (not the extraction one —
    # they share the "v0.5.325 (audit mac-not-applied)" comment;
    # `Step 1b —` uniquely names the apply site).
    idx = src.index("Step 1b — apply the")
    body = src[idx:idx + 4000]
    assert '"ip", "link", "set"' in body
    assert '"address"' in body
    assert "mac_address.lower()" in body


def test_server_validates_mac_format_before_setting():
    """Reject non-xx:xx:xx:xx:xx:xx strings before subprocess
    so the operator sees WHY it failed instead of a cryptic
    kernel errno."""
    src = _server_src()
    idx = src.index("Step 1b — apply the")
    body = src[idx:idx + 4000]
    assert '[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}' in body


def test_server_sets_mac_before_interface_bringup():
    """Kernel refuses `ip link set address` on an UP interface
    (RTNETLINK EBUSY). The MAC step MUST run BEFORE the Step 3
    bring-up. If a future refactor moves the MAC step after
    bring-up, this test breaks."""
    src = _server_src()
    mac_idx = src.index("Step 1b — apply the")
    # Find the Step 3 bring-up line index.
    step3_idx = src.index('# Step 3: Bring up interface', mac_idx)
    assert mac_idx < step3_idx, (
        "v0.5.325 MAC-set MUST run BEFORE Step 3 bring-up "
        "(kernel refuses `ip link set address` on UP iface)"
    )


def test_server_mac_step_reports_result_field():
    """Success/failure of the MAC step must be reported back in
    the `result` dict so the client can show a warning if
    result['mac_configured'] is False."""
    src = _server_src()
    idx = src.index("Step 1b — apply the")
    body = src[idx:idx + 4000]
    assert 'result["mac_configured"] = True' in body
    assert 'result["mac_configured"] = False' in body


def test_server_mac_step_empty_mac_is_a_noop():
    """If operator didn't provide a MAC (empty string), the MAC
    step MUST be a no-op — kernel-auto MAC stays put, same as
    pre-v0.5.325 behavior. Guard on `if mac_address:` at the
    start of the block."""
    src = _server_src()
    idx = src.index("Step 1b — apply the")
    body = src[idx:idx + 4000]
    assert "if mac_address:" in body


# ==================================================================
# AST parse (v0.5.300 lesson)
# ==================================================================


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())


def test_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())


def test_server_ast_parses():
    import ast
    ast.parse(_server_src())
