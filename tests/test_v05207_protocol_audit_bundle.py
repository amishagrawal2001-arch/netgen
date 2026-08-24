"""v0.5.207: cross-protocol audit bundle — four fixes.

Audit on 2026-08-23 (see MEMORY.md audit-release cadence)
turned up four bugs adjacent to the v0.5.202/v0.5.203/v0.5.205
work:

  1. BGP delete nuked the whole device on any row-click
     (utils/devices_tab_bgp.py:prompt_delete_bgp).
  2. ISIS delete nuked both AFs when only one row was clicked
     (utils/devices_tab_isis.py:prompt_delete_isis).
  3. ISIS add dialog had no AF checkboxes; the table fallback
     defaulted BOTH AFs to True unconditionally so single-
     stack devices got phantom rows for the AF they couldn't
     run (widgets/add_isis_dialog.py + utils/devices_tab_isis
     .py update_isis_table).
  4. OSPF Apply's deferred reload disconnected `cellChanged`
     without re-wiring, so inline OSPF edits silently dropped
     after every Apply (utils/devices_tab_ospf.py around line
     2734 — same class as the v0.5.202 BGP bug).

Each fix mirrors an already-shipped, already-tested pattern.
Tests below lock in the fixed behavior with both live handler
runs (per-AF disable, per-neighbor removal) and source-level
guards (so a well-meaning refactor can't quietly regress).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05207_test_{os.getpid()}.db"),
)


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    return app


# ─────────────────────────────────────────────────────────────────────
# Fix 1: BGP delete per-neighbor scoping
# ─────────────────────────────────────────────────────────────────────

class _FakeItem:
    def __init__(self, text): self._t = text
    def text(self): return self._t
    def row(self): return 0


class _FakeBgpTable:
    def __init__(self, device, af, ip):
        self._device, self._af, self._ip = device, af, ip
    def selectedItems(self):
        return [_FakeItem(self._device)]
    def item(self, row, col):
        return {0: _FakeItem(self._device),
                2: _FakeItem(self._af),
                3: _FakeItem(self._ip)}.get(col)


def _make_bgp_handler(device_dict, af, ip):
    from utils.devices_tab_bgp import BGPHandler
    handler = BGPHandler.__new__(BGPHandler)
    parent = MagicMock()
    parent.bgp_table = _FakeBgpTable(device_dict["Device Name"], af, ip)
    parent.main_window.all_devices = {"iface1": [device_dict]}
    parent._find_device_by_name = MagicMock(return_value=device_dict)
    parent.update_bgp_table = MagicMock()
    parent.get_server_url = MagicMock(return_value="http://fake")
    handler.parent = parent
    return handler, parent


def _bgp_device_two_v4_peers():
    return {
        "Device Name": "dev1",
        "IPv4": "10.0.0.1",
        "protocols": ["BGP"],
        "bgp_config": {
            "bgp_neighbor_ipv4": "1.1.1.1,2.2.2.2",
            "bgp_neighbor_ipv6": "",
            "ipv4_enabled": True,
            "ipv6_enabled": False,
        },
        "device_id": 1,
    }


def test_bgp_delete_removes_single_neighbor_not_all(qapp):
    """The core fix — click delete on 2.2.2.2 must remove
    ONLY 2.2.2.2 and leave 1.1.1.1 alone."""
    from utils import devices_tab_bgp as m
    device = _bgp_device_two_v4_peers()
    handler, parent = _make_bgp_handler(device, "IPv4", "2.2.2.2")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_bgp()

    assert device["bgp_config"]["bgp_neighbor_ipv4"] == "1.1.1.1"
    assert "BGP" in device["protocols"]
    # Whole-device cleanup must NOT fire when other peers survive.
    post_mock.assert_not_called()


def test_bgp_delete_scopes_across_afs(qapp):
    """v6 row delete must not touch v4 peers."""
    from utils import devices_tab_bgp as m
    device = _bgp_device_two_v4_peers()
    device["bgp_config"]["bgp_neighbor_ipv6"] = "2001:db8::1,2001:db8::2"
    device["bgp_config"]["ipv6_enabled"] = True
    handler, parent = _make_bgp_handler(device, "IPv6", "2001:db8::2")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_bgp()

    assert device["bgp_config"]["bgp_neighbor_ipv4"] == "1.1.1.1,2.2.2.2"
    assert device["bgp_config"]["bgp_neighbor_ipv6"] == "2001:db8::1"
    post_mock.assert_not_called()


def test_bgp_delete_last_peer_fires_full_cleanup(qapp):
    """When removing the last peer would leave zero neighbors,
    it's a full-device removal — fire /api/bgp/cleanup like
    pre-fix."""
    from utils import devices_tab_bgp as m
    device = {
        "Device Name": "dev1",
        "IPv4": "10.0.0.1",
        "protocols": ["BGP"],
        "bgp_config": {
            "bgp_neighbor_ipv4": "1.1.1.1",
            "bgp_neighbor_ipv6": "",
            "ipv4_enabled": True,
            "ipv6_enabled": False,
        },
        "device_id": 42,
    }
    handler, parent = _make_bgp_handler(device, "IPv4", "1.1.1.1")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        post_mock.return_value.status_code = 200
        handler.prompt_delete_bgp()

    assert device["bgp_config"].get("_marked_for_removal") is True
    post_mock.assert_called_once()
    assert "/api/bgp/cleanup" in post_mock.call_args.args[0]


def test_bgp_delete_af_flag_flips_when_last_af_peer_removed(qapp):
    """When per-neighbor removal empties one AF's list but the
    OTHER AF still has peers, flip that AF's `*_enabled` flag
    so the table + apply pipeline treat it as retired."""
    from utils import devices_tab_bgp as m
    device = {
        "Device Name": "dev1",
        "protocols": ["BGP"],
        "bgp_config": {
            "bgp_neighbor_ipv4": "1.1.1.1",
            "bgp_neighbor_ipv6": "2001:db8::1",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
        },
        "device_id": 42,
    }
    handler, parent = _make_bgp_handler(device, "IPv4", "1.1.1.1")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post"):
        handler.prompt_delete_bgp()

    assert device["bgp_config"]["bgp_neighbor_ipv4"] == ""
    assert device["bgp_config"]["ipv4_enabled"] is False
    # v6 side untouched — the whole point of scoping.
    assert device["bgp_config"]["bgp_neighbor_ipv6"] == "2001:db8::1"
    assert device["bgp_config"]["ipv6_enabled"] is True


# ─────────────────────────────────────────────────────────────────────
# Fix 2: ISIS delete per-AF scoping
# ─────────────────────────────────────────────────────────────────────

class _FakeIsisTable:
    def __init__(self, device, af):
        self._device, self._af = device, af
    def selectedItems(self):
        return [_FakeItem(self._device)]
    def item(self, row, col):
        return {0: _FakeItem(self._device),
                2: _FakeItem(self._af)}.get(col)


def _make_isis_handler(device_dict, af):
    from utils.devices_tab_isis import ISISHandler
    handler = ISISHandler.__new__(ISISHandler)
    parent = MagicMock()
    parent.isis_table = _FakeIsisTable(device_dict["Device Name"], af)
    parent.main_window.all_devices = {"iface1": [device_dict]}
    parent._find_device_by_name = MagicMock(return_value=device_dict)
    parent.get_server_url = MagicMock(return_value="http://fake")
    handler.parent = parent
    handler.update_isis_table = MagicMock()
    return handler, parent


def _isis_device_both_afs():
    return {
        "Device Name": "dev1",
        "IPv4": "10.0.0.1",
        "IPv6": "2001:db8::1",
        "protocols": ["IS-IS"],
        "isis_config": {
            "area_id": "49.0001.0000.0000.0001.00",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
        },
        "device_id": 42,
    }


def test_isis_delete_ipv6_disables_only_ipv6_when_both_enabled(qapp):
    from utils import devices_tab_isis as m
    device = _isis_device_both_afs()
    handler, parent = _make_isis_handler(device, "IPv6")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_isis()

    assert device["isis_config"]["ipv4_enabled"] is True
    assert device["isis_config"]["ipv6_enabled"] is False
    assert "IS-IS" in device["protocols"]
    post_mock.assert_not_called()


def test_isis_delete_ipv4_disables_only_ipv4_when_both_enabled(qapp):
    from utils import devices_tab_isis as m
    device = _isis_device_both_afs()
    handler, parent = _make_isis_handler(device, "IPv4")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_isis()

    assert device["isis_config"]["ipv4_enabled"] is False
    assert device["isis_config"]["ipv6_enabled"] is True
    post_mock.assert_not_called()


def test_isis_delete_last_af_fires_full_cleanup(qapp):
    from utils import devices_tab_isis as m
    device = _isis_device_both_afs()
    device["isis_config"]["ipv6_enabled"] = False  # only v4 enabled
    handler, parent = _make_isis_handler(device, "IPv4")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        post_mock.return_value.status_code = 200
        handler.prompt_delete_isis()

    assert device["isis_config"].get("_marked_for_removal") is True
    post_mock.assert_called_once()
    assert "/api/isis/cleanup" in post_mock.call_args.args[0]


# ─────────────────────────────────────────────────────────────────────
# Fix 3: ISIS add dialog AF checkboxes
# ─────────────────────────────────────────────────────────────────────

def test_isis_add_dialog_emits_af_flags(qapp):
    from widgets.add_isis_dialog import AddIsisDialog
    dlg = AddIsisDialog(parent=None, device_name="d1")
    values = dlg.get_values()
    assert values["ipv4_enabled"] is True
    assert values["ipv6_enabled"] is True
    dlg.deleteLater()


def test_isis_add_dialog_ipv4_only(qapp):
    from widgets.add_isis_dialog import AddIsisDialog
    dlg = AddIsisDialog(parent=None, device_name="d1")
    dlg.enable_ipv6_checkbox.setChecked(False)
    values = dlg.get_values()
    assert values["ipv4_enabled"] is True
    assert values["ipv6_enabled"] is False
    dlg.deleteLater()


def test_isis_add_dialog_rejects_zero_afs(qapp):
    from widgets.add_isis_dialog import AddIsisDialog
    dlg = AddIsisDialog(parent=None, device_name="d1")
    dlg.enable_ipv4_checkbox.setChecked(False)
    dlg.enable_ipv6_checkbox.setChecked(False)
    with patch("widgets.add_isis_dialog.QMessageBox.warning"):
        assert dlg._validate() is False
    dlg.deleteLater()


def test_isis_edit_dialog_hides_af_group_and_omits_flags(qapp):
    from widgets.add_isis_dialog import AddIsisDialog
    dlg = AddIsisDialog(parent=None, device_name="d1", edit_mode=True,
                        isis_config={"area_id": "49.0001.0000.0000.0001.00",
                                     "ipv4_enabled": True,
                                     "ipv6_enabled": False})
    assert dlg._af_group.isHidden()
    values = dlg.get_values()
    assert "ipv4_enabled" not in values
    assert "ipv6_enabled" not in values
    dlg.deleteLater()


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-ins
# ─────────────────────────────────────────────────────────────────────

def test_source_bgp_delete_reads_neighbor_columns():
    src = (REPO / "utils" / "devices_tab_bgp.py").read_text()
    tail = src.split("def prompt_delete_bgp", 1)[1][:6000]
    assert re.search(r"item\(row,\s*2\)", tail), \
        "prompt_delete_bgp no longer reads column 2 (Neighbor Type)"
    assert re.search(r"item\(row,\s*3\)", tail), \
        "prompt_delete_bgp no longer reads column 3 (Neighbor IP)"
    assert "can_scope" in tail, \
        "per-neighbor scoping branch missing from prompt_delete_bgp"


def test_source_isis_delete_reads_af_column():
    src = (REPO / "utils" / "devices_tab_isis.py").read_text()
    tail = src.split("def prompt_delete_isis", 1)[1][:6000]
    assert re.search(r"item\(row,\s*2\)", tail), \
        "prompt_delete_isis no longer reads column 2 (Neighbor Type)"
    assert "both_enabled" in tail, \
        "per-AF disable branch missing from prompt_delete_isis"


def test_source_isis_add_get_values_emits_af_flags():
    src = (REPO / "widgets" / "add_isis_dialog.py").read_text()
    tail = src.split("def get_values", 1)[1][:800]
    assert "ipv4_enabled" in tail
    assert "ipv6_enabled" in tail


def test_source_ospf_apply_deferred_reload_rewires_cellchanged():
    """The v0.5.202-class bug: after Apply OSPF, inline edits
    silently dropped because the deferred-reload closure
    disconnected cellChanged and never reconnected it. Fix
    added a _refresh_and_rewire closure that reconnects both
    the DevicesTab stub AND the OSPFHandler edit-writeback
    handler after the refresh."""
    src = (REPO / "utils" / "devices_tab_ospf.py").read_text()
    # Anchor on the deferred-reload UI-blocking comment — that's
    # unique to the Apply-flow disconnect (refresh_ospf_table
    # doesn't defer via QTimer). The refresh_ospf_table path at
    # ~line 251 already re-wires and isn't the one we care about.
    idx = src.find("Defer table update to prevent UI blocking")
    assert idx >= 0, "apply-time deferred-reload marker moved"
    window = src[max(0, idx - 2500):idx + 500]
    assert "_refresh_and_rewire" in window, \
        "apply-time deferred reload no longer bundles a rewire closure"
    assert "cellChanged.connect(" in window, \
        "apply-time deferred reload no longer reconnects cellChanged"
    assert "self.on_ospf_table_cell_changed" in window, \
        "apply-time reconnect no longer wires the OSPFHandler write-back"
