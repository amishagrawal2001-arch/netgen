"""v0.5.205: Add OSPF now emits per-AF flags; Delete OSPF is
row-scoped to the selected AF.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: added OSPF to a
single device that had both v4 and v6 addresses. The OSPF table
showed two rows — v4 (Full/Backup, real neighbor) and v6 (No
Neighbors, never actually configured on the router). Deleting
the v6 row also removed the v4 row, because
`prompt_delete_ospf` ignored the row's AF column and always
fired `/api/ospf/cleanup` for the whole device.

Root causes:
 1. `AddOspfDialog.get_values()` didn't return
    `ipv4_enabled`/`ipv6_enabled`. The OSPF table's fallback in
    `update_ospf_table` inferred BOTH AFs when the device had
    both a v4 and a v6 address.
 2. `prompt_delete_ospf` read only column 0 (device name),
    ignored column 3 (Neighbor Type), and always tore down the
    whole device's OSPF.

Fixes locked in below by source-level checks so a well-meaning
refactor can't silently regress them.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05205_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# AddOspfDialog: get_values emits ipv4_enabled / ipv6_enabled
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_add_dialog_default_enables_both_afs(qapp):
    from widgets.add_ospf_dialog import AddOspfDialog
    dlg = AddOspfDialog(parent=None, device_name="d1")
    values = dlg.get_values()
    assert values["ipv4_enabled"] is True
    assert values["ipv6_enabled"] is True
    dlg.deleteLater()


def test_add_dialog_ipv4_only(qapp):
    from widgets.add_ospf_dialog import AddOspfDialog
    dlg = AddOspfDialog(parent=None, device_name="d1")
    dlg.enable_ipv6_checkbox.setChecked(False)
    values = dlg.get_values()
    assert values["ipv4_enabled"] is True
    assert values["ipv6_enabled"] is False
    dlg.deleteLater()


def test_add_dialog_ipv6_only(qapp):
    from widgets.add_ospf_dialog import AddOspfDialog
    dlg = AddOspfDialog(parent=None, device_name="d1")
    dlg.enable_ipv4_checkbox.setChecked(False)
    values = dlg.get_values()
    assert values["ipv4_enabled"] is False
    assert values["ipv6_enabled"] is True
    dlg.deleteLater()


def test_add_dialog_rejects_zero_afs(qapp):
    """Reject at validate time; otherwise Add produces an OSPF
    config with no address family — the row never renders and
    Apply becomes a silent no-op."""
    from widgets.add_ospf_dialog import AddOspfDialog
    dlg = AddOspfDialog(parent=None, device_name="d1")
    dlg.enable_ipv4_checkbox.setChecked(False)
    dlg.enable_ipv6_checkbox.setChecked(False)
    with patch("widgets.add_ospf_dialog.QMessageBox.warning"):
        assert dlg._validate() is False
    dlg.deleteLater()


def test_edit_dialog_hides_af_group_and_omits_flags(qapp):
    """Edit mode is scoped to one AF's fields via the row's AF
    column — emitting `ipv4_enabled`/`ipv6_enabled` from the
    default-True checkboxes would clobber the caller's stored
    flags via the merge in `_update_device_protocol`."""
    from widgets.add_ospf_dialog import AddOspfDialog
    dlg = AddOspfDialog(parent=None, device_name="d1",
                        ospf_config={"area_id": "0.0.0.0",
                                     "ipv4_enabled": True,
                                     "ipv6_enabled": False})
    assert dlg._af_group.isHidden()
    values = dlg.get_values()
    assert "ipv4_enabled" not in values
    assert "ipv6_enabled" not in values
    dlg.deleteLater()


# ─────────────────────────────────────────────────────────────────────
# prompt_delete_ospf: per-AF disable when both enabled, full
# removal only for the last AF
# ─────────────────────────────────────────────────────────────────────

class _FakeItem:
    def __init__(self, text):
        self._t = text
    def text(self):
        return self._t
    def row(self):
        return 0


class _FakeTable:
    def __init__(self, device_name, af_text):
        self._device = device_name
        self._af = af_text
    def selectedItems(self):
        return [_FakeItem(self._device)]
    def item(self, row, col):
        if col == 0:
            return _FakeItem(self._device)
        if col == 3:
            return _FakeItem(self._af)
        return None


def _make_handler(device_dict, af_text):
    """Wire up the minimum stubs OSPFHandler needs to run
    prompt_delete_ospf against a fake table + fake device."""
    from utils.devices_tab_ospf import OSPFHandler
    handler = OSPFHandler.__new__(OSPFHandler)
    parent = MagicMock()
    parent.ospf_table = _FakeTable(device_dict["Device Name"], af_text)
    parent.main_window.all_devices = {"iface1": [device_dict]}
    parent.get_server_url = MagicMock(return_value="http://fake")
    handler.parent = parent
    handler.update_ospf_table = MagicMock()
    return handler, parent


def _device_both_afs():
    return {
        "Device Name": "device1",
        "IPv4": "10.0.0.1",
        "IPv6": "2001:db8::1",
        "protocols": ["OSPF"],
        "ospf_config": {
            "area_id": "0.0.0.0",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
        },
        "device_id": 42,
    }


def test_delete_ipv6_row_disables_only_ipv6_when_both_enabled(qapp):
    """The critical fix — deleting the v6 row must not remove
    the v4 side."""
    from utils import devices_tab_ospf as m
    device = _device_both_afs()
    handler, parent = _make_handler(device, "IPv6")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_ospf()

    assert device["ospf_config"]["ipv4_enabled"] is True
    assert device["ospf_config"]["ipv6_enabled"] is False
    # OSPF must stay in `protocols` — the v4 side is still live.
    assert "OSPF" in device["protocols"]
    # Whole-device cleanup would drop the v4 peer too — never fire
    # it on a per-AF disable.
    post_mock.assert_not_called()


def test_delete_ipv4_row_disables_only_ipv4_when_both_enabled(qapp):
    from utils import devices_tab_ospf as m
    device = _device_both_afs()
    handler, parent = _make_handler(device, "IPv4")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_ospf()

    assert device["ospf_config"]["ipv4_enabled"] is False
    assert device["ospf_config"]["ipv6_enabled"] is True
    assert "OSPF" in device["protocols"]
    post_mock.assert_not_called()


def test_delete_last_af_fires_full_cleanup(qapp):
    """When only one AF is enabled, deleting that row is the
    last-AF case — fall through to the full-removal path so the
    server-side OSPF is actually torn down."""
    from utils import devices_tab_ospf as m
    device = _device_both_afs()
    device["ospf_config"]["ipv6_enabled"] = False  # only v4 left

    handler, parent = _make_handler(device, "IPv4")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.Yes), \
         patch.object(m.QMessageBox, "information"), \
         patch.object(m.requests, "post") as post_mock:
        post_mock.return_value.status_code = 200
        handler.prompt_delete_ospf()

    # OSPF removed from protocols + marked for removal
    assert "OSPF" not in device["protocols"]
    assert device["ospf_config"].get("_marked_for_removal") is True
    # Full-device cleanup was fired
    post_mock.assert_called_once()
    args, kwargs = post_mock.call_args
    assert "/api/ospf/cleanup" in args[0]
    assert kwargs["json"] == {"device_id": 42}


def test_delete_cancel_leaves_config_intact(qapp):
    from utils import devices_tab_ospf as m
    device = _device_both_afs()
    handler, parent = _make_handler(device, "IPv6")

    with patch.object(m.QMessageBox, "question",
                      return_value=m.QMessageBox.No), \
         patch.object(m.QMessageBox, "information") as info, \
         patch.object(m.requests, "post") as post_mock:
        handler.prompt_delete_ospf()

    assert device["ospf_config"]["ipv6_enabled"] is True
    assert device["ospf_config"]["ipv4_enabled"] is True
    post_mock.assert_not_called()
    info.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-ins — cheap regression guards
# ─────────────────────────────────────────────────────────────────────

def test_source_get_values_emits_af_flags_in_add_mode():
    """Guard against a refactor that drops the AF emission and
    quietly re-exposes the original bug."""
    src = (REPO / "widgets" / "add_ospf_dialog.py").read_text()
    tail = src.split("def get_values", 1)[1][:800]
    assert "ipv4_enabled" in tail, "get_values no longer emits ipv4_enabled"
    assert "ipv6_enabled" in tail, "get_values no longer emits ipv6_enabled"


def test_source_delete_reads_af_column():
    """Guard against a refactor that stops looking at column 3
    (Neighbor Type) — that's what re-enables the original
    'delete v6 nukes v4' bug."""
    src = (REPO / "utils" / "devices_tab_ospf.py").read_text()
    tail = src.split("def prompt_delete_ospf", 1)[1][:6000]
    assert re.search(r"item\(row,\s*3\)", tail), \
        "prompt_delete_ospf no longer reads column 3 for the AF"
    assert "both_enabled" in tail, \
        "per-AF disable branch missing from prompt_delete_ospf"
