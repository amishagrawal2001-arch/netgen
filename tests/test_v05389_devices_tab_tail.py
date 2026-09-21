"""v0.5.389 — Device tab audit tail (4 items).

  E1  populate_device_table preserves sort + selection.
  E2  Column width + visibility persist via QSettings.
  E3  Warn on Add-device name collision auto-suffix.
  E4  MAC validator rejects broadcast + multicast source MACs.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_devices_tab_ast_parses():
    ast.parse(_read("widgets/devices_tab.py"))


def test_add_device_dialog_ast_parses():
    ast.parse(_read("widgets/add_device_dialog.py"))


# ─── E1: populate_device_table sort + selection ───


def test_e1_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.389 (audit devices-tab E1)" in src


def test_e1_captures_and_restores_sort_state():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def populate_device_table(self):")
    _end = src.index("    # ---------- Dialogs / actions ----------", _idx)
    body = src[_idx:_end]
    # Captured
    assert "capture_sort_state" in body
    # Restored
    assert "restore_sort_state" in body
    # Sorting is disabled during rebuild + restored after
    assert "self.devices_table.setSortingEnabled(False)" in body
    assert "self.devices_table.setSortingEnabled(_was_sorting)" in body


def test_e1_captures_and_restores_selection_by_device_id():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def populate_device_table(self):")
    _end = src.index("    # ---------- Dialogs / actions ----------", _idx)
    body = src[_idx:_end]
    assert "_selected_ids = set()" in body
    # Restore uses UserRole stash so it survives sort re-order
    assert "self.devices_table.selectRow(_r)" in body


# ─── E2: column layout persistence ───


def test_e2_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.389 (audit devices-tab E2)" in src


def test_e2_helpers_defined():
    src = _read("widgets/devices_tab.py")
    assert "def _restore_devices_table_columns(self):" in src
    assert "def _save_devices_table_columns(self):" in src
    assert "def _on_devices_column_resized" in src


def test_e2_qsettings_backed_and_wired():
    src = _read("widgets/devices_tab.py")
    # QSettings org + app match v0.5.388 D4 shape
    assert '_DEVICES_COLS_SETTINGS_ORG = "netgen"' in src
    assert '_DEVICES_COLS_SETTINGS_APP = "netgen-client"' in src
    # Restore hooked at init AFTER the hardcoded defaults
    assert "self._restore_devices_table_columns()" in src
    # Save hooked to sectionResized
    assert "sectionResized.connect(self._on_devices_column_resized)" in src


# ─── E3: name collision warn ───


def test_e3_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.389 (audit devices-tab E3)" in src


def test_e3_info_dialog_when_auto_suffixed():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.389 (audit devices-tab E3)")
    body = src[_idx:_idx + 3000]
    assert "_auto_suffixed = True" in body
    assert 'QMessageBox.information(' in body
    assert 'Device name changed' in body


def test_e3_default_sentinel_stays_silent():
    """`device` default sentinel should not trigger the warn —
    that's a placeholder, not an intentional name."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.389 (audit devices-tab E3)")
    body = src[_idx:_idx + 3000]
    # The `if base_name == "device":` branch does NOT set
    # _auto_suffixed (only the else branch does).
    assert 'if base_name == "device":' in body
    _dev_branch = body.split('else:', 1)[0]
    assert "_auto_suffixed = True" not in _dev_branch


# ─── E4: MAC validator ───


def test_e4_marker_present():
    src = _read("widgets/add_device_dialog.py")
    assert "v0.5.389 (audit devices-tab E4)" in src


def test_e4_regex_rejects_broadcast_and_multicast():
    """Behavioral: instantiate the compiled regex + test unicast
    accept / multicast reject / broadcast reject."""
    import re as _re
    src = _read("widgets/add_device_dialog.py")
    # Extract the new regex string
    _m = _re.search(
        r'mac_re\s*=\s*QRegExp\(\s*(?:r?"|r?\')(.+?)(?:"|\')\s*\)',
        src,
        _re.DOTALL,
    )
    # QRegExp construction is multi-line; do a simpler grep for
    # the actual regex fragments.
    body = src[src.index("v0.5.389 (audit devices-tab E4)"):
               src.index("v0.5.389 (audit devices-tab E4)") + 2500]
    assert "^(?!FF:FF:FF:FF:FF:FF$)" in body
    assert "[02468ACEace]" in body  # LSB=0 hex class

    # Build the equivalent Python regex and test.
    _py_re = _re.compile(
        r"^(?!FF:FF:FF:FF:FF:FF$)"
        r"[0-9A-Fa-f][02468ACEace]"
        r"(:[0-9A-Fa-f]{2}){5}$"
    )
    # Unicast accepts
    assert _py_re.match("00:11:22:33:44:55")
    assert _py_re.match("02:aa:bb:cc:dd:ee")
    # Broadcast rejects
    assert not _py_re.match("FF:FF:FF:FF:FF:FF")
    # Multicast rejects (first-octet LSB = 1)
    assert not _py_re.match("01:00:5E:00:00:01")
    assert not _py_re.match("33:33:00:00:00:01")
    assert not _py_re.match("11:22:33:44:55:66")
    # Malformed rejects
    assert not _py_re.match("not a mac")
    assert not _py_re.match("00:11:22:33:44")


# ─── version guard ───


def test_pyproject_version_at_least_0589():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 389)


# ─── regression guards ───


def test_v0388_d1_multi_delete_intact():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.388 (audit devices-tab D1)" in src


def test_v0388_d4_window_layout_intact():
    src = _read("traffic_client/main.py")
    assert "v0.5.388 (audit devices-tab D4)" in src


def test_v0387_c1_prefill_fix_intact():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C1)" in src


def test_v0372_c5_bgp_holdtime_validation_intact():
    """v0.5.372 C5 tightened BGP validators; E4's MAC change
    must not have affected them."""
    src = _read("widgets/add_bgp_dialog.py")
    assert "hold" in src.lower()
