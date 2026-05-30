"""Devices tab audit pinning tests (v0.2.85).

Standing up the full DevicesTab is heavy (8200+ lines, lots of
dependencies). Most of these tests are source-grep assertions — they
catch the specific shape we landed without needing the live widget.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


# ───────────────────────────────────── #1 duplicate BGP wrapper removed
def test_apply_bgp_configurations_defined_exactly_once():
    """v0.2.85 #1: the duplicate def at lines 3195-3203 of
    widgets/devices_tab.py was dead per Python's last-def-wins, but
    living in the source confused triage. Pin that exactly one
    apply_bgp_configurations remains."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    matches = re.findall(r"^\s*def apply_bgp_configurations\b", src,
                         flags=re.MULTILINE)
    assert len(matches) == 1, (
        f"expected exactly one apply_bgp_configurations def, got {len(matches)}"
    )


def test_start_and_stop_bgp_protocol_defined_exactly_once():
    """Same cleanup, same risk: dead defs at 3199-3203 were confusable."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    for name in ("start_bgp_protocol", "stop_bgp_protocol"):
        m = re.findall(rf"^\s*def {name}\b", src, flags=re.MULTILINE)
        assert len(m) == 1, f"expected exactly one {name}, got {len(m)}"


# ───────────────────────────────────── #2 DHCP apply kicks preflight
def test_dhcp_apply_pools_kicks_preflight_bar():
    """v0.2.85 #2: every other protocol's apply path calls
    kick_refresh; DHCP was the lone outlier. Pin the hook so a
    future refactor that removes it gets caught loudly."""
    src = (REPO / "utils" / "devices_tab_dhcp.py").read_text()
    # Find the apply_dhcp_pools method body.
    m = re.search(
        r"def apply_dhcp_pools\(self\).*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None, "apply_dhcp_pools not found"
    body = m.group(0)
    assert "kick_refresh" in body, (
        "apply_dhcp_pools must call kick_refresh after success — "
        "every other protocol's apply path does. The DUPLICATE_IPV4 "
        "finding can flip when a DHCP-assigned address collides."
    )


# ───────────────────────────────────── #3 Delete-key shortcut
def test_delete_key_shortcut_bound_to_devices_table():
    """v0.2.85 #3: QShortcut(Qt.Key_Delete) bound to self.devices_table
    (not the whole tab) with WidgetShortcut context, so inline-edit
    Delete key on a single character doesn't accidentally remove the
    row."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    # The literal pattern we landed.
    assert "_QShortcut(_QKeySequence(Qt.Key_Delete)," in src
    assert "self.devices_table)" in src
    # Verify it's scoped to the widget (so a global Delete doesn't fire).
    assert "_del_shortcut.setContext(Qt.WidgetShortcut)" in src
    # Verify it connects to the right slot.
    assert "_del_shortcut.activated.connect(self.remove_selected_device)" in src


# ─────────────────────────────────── #5 right-click context menu wiring
def test_devices_table_has_custom_context_menu_policy():
    """v0.2.85 #5: setContextMenuPolicy(Qt.CustomContextMenu) +
    customContextMenuRequested signal on devices_table so the operator
    can right-click a row instead of rolling to the toolbar."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    assert "self.devices_table.setContextMenuPolicy(Qt.CustomContextMenu)" in src
    assert (
        "self.devices_table.customContextMenuRequested.connect("
        in src
    )
    assert "self._on_devices_table_context_menu" in src


def test_context_menu_handler_offers_expected_actions():
    """The 4 actions (Apply / Copy / Paste / Delete) match the
    operator's mental model from the toolbar. Adding/removing items
    here without considering the toolbar parity is a smell, so pin
    them all."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    m = re.search(
        r"def _on_devices_table_context_menu\(self, pos\).*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None
    body = m.group(0)
    for label in ('"Apply selected"', '"Copy"', '"Paste"', '"Delete"'):
        assert label in body, f"context menu missing action {label}"


def test_context_menu_handler_selects_row_under_cursor():
    """If the operator right-clicks on an unselected row, the menu's
    actions act on whatever was previously selected — wrong target.
    Handler must select the row under the cursor first."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    m = re.search(
        r"def _on_devices_table_context_menu\(self, pos\).*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    body = m.group(0)
    assert "selectRow" in body
    assert "indexAt(pos)" in body


def test_context_menu_handler_disables_paste_without_clipboard():
    """Paste meaningful only when copied_device is non-empty. Pin the
    guard so a future refactor doesn't enable Paste unconditionally."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    m = re.search(
        r"def _on_devices_table_context_menu\(self, pos\).*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    body = m.group(0)
    assert "copied_device" in body
    assert "clipboard_has_device" in body
    assert "act_paste.setEnabled(clipboard_has_device)" in body


# ────────────────────────────────────────── kick_refresh coverage sweep
@pytest.mark.parametrize("apply_method,host_file", [
    ("apply_bgp_configurations", "widgets/devices_tab.py"),
    ("apply_ospf_configurations", "widgets/devices_tab.py"),
    ("apply_isis_configurations", "widgets/devices_tab.py"),
    ("apply_vxlan_configurations", "widgets/devices_tab.py"),
    ("apply_dhcp_pools", "utils/devices_tab_dhcp.py"),
])
def test_every_apply_path_kicks_preflight(apply_method, host_file):
    """v0.2.71/v0.2.74 wired kick_refresh into BGP/OSPF/IS-IS/VXLAN.
    v0.2.85 closed the DHCP gap. This is the consolidated regression
    suite — every protocol's apply MUST kick the bar. Failing this
    test means the bar will silently fall out of sync after that
    protocol's apply, which is the exact bug v0.2.71 set out to
    eliminate."""
    src = (REPO / host_file).read_text()
    m = re.search(
        rf"def {apply_method}\(self\).*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None, f"{apply_method} not found in {host_file}"
    # Some methods have multiple defs in different files; we want at
    # least the LAST one to call kick_refresh.
    bodies = re.findall(
        rf"def {apply_method}\(self\).*?(?=^\s*def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert any("kick_refresh" in b for b in bodies), (
        f"{apply_method} in {host_file} has no kick_refresh call — "
        f"the preflight bar will fall out of sync after this apply."
    )
