"""v0.5.317 — Upstream DHCP relay-agent config hint on the Add
Device dialog when DHCP Client mode is selected.

Operator can't get a DHCP-client device to lease unless the L3
gateway between the client's VLAN and the netgen DHCP-server
device has a dhcp-relay agent forwarding client requests upstream.
Pre-fix, this requirement was invisible: the operator would set up
a Client-mode device, watch it fail, and have no idea what was
missing on the switch side. Ship a static hint pane inside the
DHCP config section that appears whenever Client mode is selected,
with a copyable Juniper example config the operator can adapt to
their topology (server IP + client-VLAN interface).
"""
from __future__ import annotations

from pathlib import Path

_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_device_dialog.py"


def _src() -> str:
    return _DIALOG.read_text()


def test_marker_present():
    assert "v0.5.317 (audit dhcp-client-upstream-relay-hint)" in _src()


def test_hint_widget_created():
    """A dedicated QLabel widget must exist so the tests can
    reference it by attribute and the mode-change handler can
    show/hide it."""
    src = _src()
    assert "self.dhcp_client_relay_hint = QLabel()" in src


def test_hint_added_to_dhcp_layout():
    """The hint must actually be added to the DHCP section layout —
    otherwise it exists but doesn't render."""
    src = _src()
    assert "dhcp_main_layout.addWidget(self.dhcp_client_relay_hint)" in src


def test_hint_hidden_by_default():
    """Constructor default must be hidden so DHCP-server /
    DHCP-disabled devices don't see it. The mode-change handler
    flips visibility."""
    src = _src()
    idx = src.index("self.dhcp_client_relay_hint = QLabel()")
    body = src[idx:idx + 3000]
    assert "self.dhcp_client_relay_hint.setVisible(False)" in body


def test_hint_visibility_bound_to_client_mode():
    """`_on_dhcp_mode_changed` must set visibility TRUE only when
    is_client AND dhcp_mode_combo is enabled (DHCP protocol
    itself is turned on). Server mode + DHCP-off both must
    suppress."""
    src = _src()
    idx = src.index("def _on_dhcp_mode_changed")
    # find next `def ` (function boundary)
    next_def = src.index("\n    def ", idx + 10)
    body = src[idx:next_def]
    assert "self.dhcp_client_relay_hint.setVisible" in body
    assert "is_client and self.dhcp_mode_combo.isEnabled()" in body


def test_hint_includes_junos_config_example():
    """The hint must include the four Junos set-form lines that
    the operator will copy. Line-form pattern: `set forwarding-
    options dhcp-relay ...`."""
    src = _src()
    marker = "v0.5.317 (audit dhcp-client-upstream-relay-hint)"
    hint_idx = src.index("self.dhcp_client_relay_hint.setText(", src.index(marker))
    body = src[hint_idx:hint_idx + 4000]
    # 4 lines that would together form the operator's srv06
    # reference config.
    assert "set forwarding-options dhcp-relay overrides allow-snooped-clients" in body
    assert "set forwarding-options dhcp-relay forward-only" in body
    assert "set forwarding-options dhcp-relay server-group DHCP-SERVERS" in body
    assert "set forwarding-options dhcp-relay active-server-group DHCP-SERVERS" in body
    assert "set forwarding-options dhcp-relay group CLIENTS interface" in body


def test_hint_explains_substitutions():
    """The example uses placeholder IPs / interface names — the
    hint must call out which ones the operator needs to swap for
    their topology (else they'd copy the sample verbatim and
    wonder why it doesn't work)."""
    src = _src()
    marker = "v0.5.317 (audit dhcp-client-upstream-relay-hint)"
    hint_idx = src.index("self.dhcp_client_relay_hint.setText(", src.index(marker))
    body = src[hint_idx:hint_idx + 4000]
    # DHCP-server device IP callout + client-VLAN SVI callout.
    assert "DHCP-server device" in body
    assert "SVI" in body or "irb" in body.lower()


def test_hint_calls_out_relay_vs_direct_attached():
    """Direct-attached (server + client on same L2) doesn't need
    the relay — the hint must SAY so, otherwise operators who
    happen to have a direct-attached setup will think they need
    to configure their switch and won't."""
    src = _src()
    marker = "v0.5.317 (audit dhcp-client-upstream-relay-hint)"
    hint_idx = src.index("self.dhcp_client_relay_hint.setText(", src.index(marker))
    body = src[hint_idx:hint_idx + 4000]
    assert "direct-attached" in body.lower()


def test_hint_text_is_selectable_by_mouse():
    """Copy-paste is the point — the operator will select the
    config lines and paste into their switch CLI. Without
    setTextInteractionFlags, clicking-and-dragging on a QLabel
    is a no-op."""
    src = _src()
    idx = src.index("self.dhcp_client_relay_hint = QLabel()")
    body = src[idx:idx + 3000]
    assert "setTextInteractionFlags" in body
    assert "TextSelectableByMouse" in body


def test_hint_uses_rich_text_format():
    """The `<pre>` block for the config example is markup, not
    plain text. Without setTextFormat(Qt.RichText), the label
    would render "<pre>" literally instead of formatting the
    code block."""
    src = _src()
    idx = src.index("self.dhcp_client_relay_hint = QLabel()")
    body = src[idx:idx + 3000]
    assert "setTextFormat(Qt.RichText)" in body


def test_dialog_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
