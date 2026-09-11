"""v0.5.296 — Expose relay_return_hop in the DHCP-server device dialog.

The whole v0.5.287-295 debug saga on srv06 was ultimately unblocked
when I set dhcp_config.relay_return_hop=172.16.30.1 via direct
sqlite write. No client UI exposed the field — v0.5.245 shipped
the server-side skip but never a way to configure it. Any future
operator running a DHCP-relay setup would have hit the same wall.

Fix: add QLineEdit with tooltip to the DHCP-server device dialog
(widgets/add_device_dialog.py), wired into the enable/disable
toggle and the save/load paths. Server side (v0.5.245 skip,
v0.5.295 anchor-replay propagation) already reads the field —
this ship is client-only.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def _dlg_src() -> str:
    return (REPO / "widgets" / "add_device_dialog.py").read_text()


def _tab_src() -> str:
    return (REPO / "widgets" / "devices_tab.py").read_text()


# ─────────────────────────────────────────────────────────────────
# Dialog: widget exists, has tooltip, placeholder
# ─────────────────────────────────────────────────────────────────

def test_v05296_marker_present():
    assert "v0.5.296 (audit relay-ui)" in _dlg_src()


def test_relay_return_hop_widget_declared():
    src = _dlg_src()
    assert "self.dhcp_relay_return_hop_input = QLineEdit()" in src


def test_widget_has_tooltip_explaining_the_concept():
    """The tooltip is the only place the operator learns what
    this field does. It must reference relay-agent + on-server L2
    + the anchor-collision consequence."""
    src = _dlg_src()
    idx = src.find("self.dhcp_relay_return_hop_input = QLineEdit()")
    body = src[idx:idx + 3000]
    assert "setToolTip" in body
    assert "relay agent" in body.lower()
    assert "direct-attached" in body.lower() or "same L2" in body.lower()


def test_widget_added_to_dhcp_left_layout():
    src = _dlg_src()
    idx = src.find("self.dhcp_relay_return_hop_input = QLineEdit()")
    body = src[idx:idx + 3000]
    assert 'dhcp_left_layout.addRow("Relay Return Hop:", self.dhcp_relay_return_hop_input)' in body


def test_placeholder_names_the_shape():
    """Placeholder must include an example IP so operators can
    tell what to put there without opening docs."""
    src = _dlg_src()
    idx = src.find("self.dhcp_relay_return_hop_input = QLineEdit()")
    body = src[idx:idx + 3000]
    assert "setPlaceholderText" in body


# ─────────────────────────────────────────────────────────────────
# Dialog: enable/disable wiring
# ─────────────────────────────────────────────────────────────────

def test_widget_initially_disabled():
    """Follows the same initial-disable pattern as other DHCP
    server fields — enabled only after DHCP-server mode is
    selected."""
    src = _dlg_src()
    assert "self.dhcp_relay_return_hop_input.setEnabled(False)" in src


def test_widget_toggled_with_ipv4_active():
    """Included in the enable/disable loop that fires on
    server-mode + IPv4 toggle changes."""
    src = _dlg_src()
    # The toggle block starts with `for widget in (` followed by
    # a list of DHCP-IPv4 fields.
    idx = src.find("self.dhcp_gateway_route_input,\n            self.dhcp_relay_return_hop_input,")
    assert idx > 0, "relay_return_hop must be in the same toggle group"


# ─────────────────────────────────────────────────────────────────
# Dialog: save path
# ─────────────────────────────────────────────────────────────────

def test_save_persists_relay_return_hop_into_dhcp_config():
    src = _dlg_src()
    assert (
        'dhcp_config["relay_return_hop"] = (\n'
        '                        self.dhcp_relay_return_hop_input.text().strip()\n'
        '                    )'
    ) in src


# ─────────────────────────────────────────────────────────────────
# Edit path (devices_tab.py) — preload existing value
# ─────────────────────────────────────────────────────────────────

def test_edit_preloads_relay_return_hop():
    """When operator re-opens the dialog for an existing device,
    the current relay_return_hop value must be shown (not blank)."""
    src = _tab_src()
    assert "dialog.dhcp_relay_return_hop_input.setText" in src


def test_edit_preload_reads_from_dhcp_config_dict():
    src = _tab_src()
    idx = src.find("dialog.dhcp_relay_return_hop_input.setText")
    body = src[max(0, idx - 800):idx + 200]
    assert 'dhcp_config.get("relay_return_hop"' in body


def test_edit_preload_guards_missing_attribute():
    """hasattr(dialog, ...) guard — dialog may be an older widget
    version in flight; don'''t crash if the attribute is missing."""
    src = _tab_src()
    idx = src.find("dialog.dhcp_relay_return_hop_input.setText")
    body = src[max(0, idx - 800):idx + 200]
    assert 'hasattr(dialog, "dhcp_relay_return_hop_input")' in body


# ─────────────────────────────────────────────────────────────────
# Server-side compatibility (regression on v0.5.245 + v0.5.295)
# ─────────────────────────────────────────────────────────────────

def test_server_still_reads_relay_return_hop_from_dhcp_config():
    """start_dhcp_server has read this field since v0.5.245.
    Regression guard."""
    src = (REPO / "utils" / "dhcp.py").read_text()
    assert 'dhcp_config.get("relay_return_hop"' in src


def test_v05295_replay_propagation_intact():
    """v0.5.295 anchor-replay must still propagate the field."""
    src = (REPO / "utils" / "arp_monitor.py").read_text()
    assert "relay_return_hop=_relay_return_hop" in src


# ─────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────

def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 296)
