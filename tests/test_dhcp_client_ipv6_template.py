"""DHCPv6 client template — mirror of the DHCPv4 client template
for v6-only client scenarios.

Operator ask post-v0.5.303 (which shipped the DHCPv6 server
template): also add the client-side v6 template so a lab can
stress-test a DHCPv6 server with matching DHCPv6 clients without
the "click 4 sub-toggles + swap two checkboxes" prelude every
time.

start_dhcp_client (utils/dhcp.py) reads dhcp_config.ipv4_enabled
and dhcp_config.ipv6_enabled independently — v4=off, v6=on means
"spawn only dhcp6c (or dhclient -6 fallback), skip dhclient -4".
Widget defaults are v4=on/v6=off, so a template needs to flip
both sub-toggles.
"""
from __future__ import annotations

from pathlib import Path

from utils import device_templates


_DEVICE_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_device_dialog.py"


def _dialog_has_widget(name: str) -> bool:
    return f"self.{name}" in _DEVICE_DIALOG.read_text()


def test_template_registered():
    t = device_templates.get_template("dhcp_client_ipv6")
    assert t is not None, "dhcp_client_ipv6 missing from _TEMPLATES"
    assert t.key == "dhcp_client_ipv6"
    titles = {row["key"]: row["title"] for row in device_templates.list_templates()}
    assert "dhcp_client_ipv6" in titles
    assert "DHCPv6" in titles["dhcp_client_ipv6"] or "IPv6" in titles["dhcp_client_ipv6"]


def test_template_enables_dhcp_protocol_only():
    """No BGP/OSPF/ISIS/VXLAN — just DHCP. Same shape as the v4
    sibling dhcp_client."""
    t = device_templates.get_template("dhcp_client_ipv6")
    assert t.protocols == ["DHCP"]


def test_template_widget_names_all_exist_on_dialog():
    """v0.4.7-shape audit: any widget name that doesn't exist on
    AddDeviceDialog gets silently dropped by apply_to_dialog."""
    t = device_templates.get_template("dhcp_client_ipv6")
    for field_name in t.fields:
        assert _dialog_has_widget(field_name), (
            f"dhcp_client_ipv6.fields[{field_name!r}] doesn't match "
            f"any `self.<name>` in AddDeviceDialog — apply_to_dialog "
            f"will silently drop this field."
        )


def test_template_client_mode_v6_only_sub_toggles():
    """DHCP-family sub-toggles: v4 OFF, v6 ON. This is what
    start_dhcp_client reads to decide which daemons to spawn.
    Widget defaults are v4=on/v6=off, so BOTH must be flipped
    by the template — the v4 sibling dhcp_client relies on the
    defaults and doesn't flip either."""
    t = device_templates.get_template("dhcp_client_ipv6")
    assert t.fields["dhcp_ipv4_enabled_checkbox"] is False
    assert t.fields["dhcp_ipv6_enabled_checkbox"] is True
    assert t.fields["dhcp_mode_combo"] == "Client"


def test_no_static_ipv6_address_configured():
    """DHCP client mode — address comes from the lease, no static
    ipv6_input value should be set (would just be overwritten by
    the client-mode branch of _on_dhcp_mode_changed that clears
    ipv6_input anyway). Same as the v4 sibling."""
    t = device_templates.get_template("dhcp_client_ipv6")
    assert "ipv6_input" not in t.fields
    assert "ipv6_gateway_input" not in t.fields
    assert "ipv4_input" not in t.fields


def test_template_pairs_with_dhcp_server_ipv6_by_vlan():
    """Both dhcp_server_ipv6 and dhcp_client_ipv6 default to
    vlan_input='10' so operators one-clicking the pair land them
    on the same L2 segment by default."""
    server_t = device_templates.get_template("dhcp_server_ipv6")
    client_t = device_templates.get_template("dhcp_client_ipv6")
    assert server_t.fields["vlan_input"] == client_t.fields["vlan_input"]


def test_template_applies_via_headless_dialog():
    """End-to-end: apply the template to a headless AddDeviceDialog
    and confirm the sub-toggles reach the right state + get_values()
    emits the right dhcp_config."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.add_device_dialog import AddDeviceDialog

    dlg = AddDeviceDialog()
    device_templates.apply_to_dialog(dlg, "dhcp_client_ipv6")

    # Sub-toggles must be v4=off, v6=on (widget defaults are
    # v4=on/v6=off, so both must actually flip).
    assert dlg.dhcp_ipv4_enabled_checkbox.isChecked() is False
    assert dlg.dhcp_ipv6_enabled_checkbox.isChecked() is True
    assert dlg.dhcp_mode_combo.currentText() == "Client"
    assert dlg.dhcp_enable_checkbox.isChecked() is True

    # get_values must emit dhcp_config with ipv4_enabled=False and
    # ipv6_enabled=True so start_dhcp_client (utils/dhcp.py) spawns
    # only the v6 daemon.
    dlg.iface_input.setText("vlan40")
    dlg.mac_input.setText("aa:bb:cc:dd:ee:ff")
    vals = dlg.get_values()
    dhcp_config = vals[20]
    assert dhcp_config["mode"] == "client"
    assert dhcp_config["ipv4_enabled"] is False
    assert dhcp_config["ipv6_enabled"] is True
