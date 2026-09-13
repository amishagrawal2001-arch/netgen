"""v0.5.306 — defensive drop of unwanted IPv4 on IPv6-only DHCP-server device.

Operator on srv06 2026-09-13 selected the DHCPv6 server template
(added in v0.5.303). The dialog correctly showed IPv4 checkbox
unchecked, dhcp_ipv4_enabled unchecked, IPv4 fields grayed. Headless
PyQt simulation of `apply_to_dialog(dlg, 'dhcp_server_ipv6')` +
`get_values()` proved the tuple's `ipv4` position is `''` and
`dhcp_config.ipv4_enabled == False`.

Yet 192.168.0.2/24 (the ipv4_input widget's DEFAULT text) still
landed on the vlan sub-interface after Apply. Static-analysis
couldn't find the code path that re-injected the widget default
between Save and /api/device/apply.

v0.5.306 defensive drop: when /api/device/apply receives a payload
whose `dhcp_config` declares `ipv6_enabled=True, ipv4_enabled=False,
mode="server"` AND the payload also carries a non-empty `ipv4`
(or `loopback_ipv4`), drop the IPv4 fields before configuring and
log a WARN naming the received values. Closes the leak at the
last mile and gives the postmortem a bread-crumb naming what the
client actually sent.

Legitimate "DHCPv6 pool + static IPv4 management IP on the same
iface" setups are rare enough to justify the drop as default —
operators wanting both can apply the static v4 as a separate
static-config device on the same parent NIC.

Source-level checks only; the actual `ip addr add` runtime lives
on srv06.
"""
from __future__ import annotations

from pathlib import Path

_SERVER_PY = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER_PY.read_text()


def test_drop_marker_present():
    src = _src()
    assert "v0.5.306 (audit v6-only-dhcp-server-leak)" in src


def test_drop_only_fires_for_v6_only_server_mode():
    src = _src()
    idx = src.index("v0.5.306 (audit v6-only-dhcp-server-leak)")
    body = src[idx:idx + 4000]
    # All three conditions must be checked (not just two-of-three):
    #   ipv6_enabled=True
    #   ipv4_enabled=False
    #   mode="server"
    # Otherwise a dual-stack DHCP server or a DHCP client would
    # get its ipv4 wrongly dropped.
    assert 'dhcp_config.get("ipv6_enabled") is True' in body
    assert 'not _dc_v4_on' in body
    assert '_dc_mode == "server"' in body


def test_drop_zeros_the_ipv4_and_loopback_fields():
    src = _src()
    idx = src.index("v0.5.306 (audit v6-only-dhcp-server-leak)")
    body = src[idx:idx + 4000]
    # Post-drop the local variables that downstream `ip addr add`
    # blocks read from must all be empty — otherwise the drop is
    # cosmetic (log fires but IPv4 still gets configured).
    assert 'ipv4 = ""' in body
    assert 'ipv4_gateway = ""' in body
    assert 'loopback_ipv4 = ""' in body


def test_drop_fires_when_ipv4_or_loopback_v4_are_nonempty():
    src = _src()
    idx = src.index("v0.5.306 (audit v6-only-dhcp-server-leak)")
    body = src[idx:idx + 4000]
    # Guard is `if _v6_only_server and (ipv4 or loopback_ipv4)`.
    # Both possible payloads matter — pre-fix the leak was ipv4,
    # but loopback_ipv4 could also carry a stray widget default.
    assert "if _v6_only_server and (ipv4 or loopback_ipv4):" in body


def test_drop_logs_received_values_for_postmortem():
    src = _src()
    idx = src.index("v0.5.306 (audit v6-only-dhcp-server-leak)")
    body = src[idx:idx + 4000]
    # The WARN log must name the exact received values so the
    # client-side leak source can be identified.
    assert "[DEVICE APPLY WARN v0.5.306]" in body
    for expected in ("ipv4=%r", "ipv4_mask=%r", "ipv4_gateway=%r", "loopback_ipv4=%r"):
        assert expected in body


def test_drop_fail_closed_on_dhcp_config_exception():
    """If dhcp_config.get() raises (e.g. dhcp_config isn't a dict),
    `_v6_only_server` defaults to False and nothing gets dropped.
    Fail-open on exception so a corrupt DHCP config can't take
    down a DHCPv4-only or client device by accident."""
    src = _src()
    idx = src.index("v0.5.306 (audit v6-only-dhcp-server-leak)")
    body = src[idx:idx + 4000]
    assert "except Exception:" in body
    assert "_v6_only_server = False" in body


def test_drop_placed_before_vxlan_config_parsing():
    """The drop must run BEFORE the `ip addr add` block (line ~4846)
    but AFTER dhcp_config has been parsed. Anchor via the surrounding
    landmarks."""
    src = _src()
    drop_idx = src.index("v0.5.306 (audit v6-only-dhcp-server-leak)")
    dhcp_parse_idx = src.index('dhcp_config = json.loads(dhcp_config_raw)')
    ipv4_configure_idx = src.index("Step 4: Configure IPv4 address")
    assert dhcp_parse_idx < drop_idx < ipv4_configure_idx, (
        "v0.5.306 drop must sit between dhcp_config parsing and "
        "the ip-addr-add block that would otherwise put 192.168.0.2 "
        "on the interface."
    )


def test_server_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
