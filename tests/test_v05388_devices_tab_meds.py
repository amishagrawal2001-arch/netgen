"""v0.5.388 — Device tab audit MEDs (5 items).

  D1  Multi-delete confirmation + row-shift safety (by device_id).
  D2  AI discovery Cancel wiring + worker leak fix.
  D3  Frozen dialog helpers rebuild on each open.
  D4  Chassis window saveState/restoreState via QSettings.
  D5  update_server_tree preserves scroll + expansion + selection.
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


def test_unified_dialog_ast_parses():
    ast.parse(_read("widgets/unified_add_device_dialog.py"))


def test_main_ast_parses():
    ast.parse(_read("traffic_client/main.py"))


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


# ─── D1: multi-delete confirmation + row-shift safety ───


def test_d1_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.388 (audit devices-tab D1)" in src


def test_d1_snapshots_selection_upfront():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def remove_selected_device(self)")
    _end = _idx + 6000
    body = src[_idx:_end]
    # Builds a tuple list up-front
    assert "_targets = []" in body
    assert "_targets.append((_did, device_name, device_info))" in body


def test_d1_confirm_lists_names_and_count():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def remove_selected_device(self)")
    _end = _idx + 6000
    body = src[_idx:_end]
    # Confirm dialog lists names
    assert "_name_lines" in body
    assert "Remove {len(_targets)} device(s)?" in body


def test_d1_row_relookup_by_device_id():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def remove_selected_device(self)")
    _end = src.index("\n    def prompt_manage_route_pools(", _idx)
    body = src[_idx:_end]
    assert "def _row_for_device_id(_did):" in body
    # removeRow uses re-looked-up index
    assert "_cur_row = _row_for_device_id(device_id)" in body
    assert "self.devices_table.removeRow(_cur_row)" in body


# ─── D2: AI discovery Cancel + leak ───


def test_d2_marker_present():
    src = _read("widgets/unified_add_device_dialog.py")
    assert "v0.5.388 (audit devices-tab D2)" in src


def test_d2_cancel_wired():
    src = _read("widgets/unified_add_device_dialog.py")
    assert "progress.canceled.connect(self._cancel_discovery_worker)" in src
    assert "def _cancel_discovery_worker(self):" in src


def test_d2_previous_worker_interrupted_before_new_start():
    src = _read("widgets/unified_add_device_dialog.py")
    _idx = src.index("def ai_discover_devices(self)") if "def ai_discover_devices" in src else -1
    if _idx == -1:
        # Locate the discovery block indirectly via marker
        _idx = src.index("v0.5.388 (audit devices-tab D2): if a previous discovery")
    body = src[_idx:_idx + 3500]
    assert "_prev_worker = getattr(self, \"discovery_worker\", None)" in body
    assert "_prev_worker.requestInterruption()" in body


def test_d2_progress_close_guarded():
    """Both on_discovery_complete + on_discovery_error must
    guard progress.close() against a destroyed dialog."""
    src = _read("widgets/unified_add_device_dialog.py")
    # Count try-wrapped close calls
    assert src.count("progress.close()") >= 2
    # Both sites now wrap in try
    assert "except (RuntimeError, Exception)" in src


# ─── D3: dialog helpers fresh per open ───


def test_d3_marker_present():
    src = _read("widgets/unified_add_device_dialog.py")
    assert "v0.5.388 (audit devices-tab D3)" in src


def test_d3_no_cached_short_circuit():
    """The `if not self.frr_dialog:` and `if not self.external_dialog:`
    short-circuits must be gone from the add_device body."""
    src = _read("widgets/unified_add_device_dialog.py")
    _idx = src.index("def add_device(self):")
    _end = _idx + 3500
    body = src[_idx:_end]
    assert "if not self.frr_dialog:" not in body
    assert "if not self.external_dialog:" not in body


def test_d3_calls_refresh_helper_when_present():
    src = _read("widgets/unified_add_device_dialog.py")
    _idx = src.index("def add_device(self):")
    _end = _idx + 3500
    body = src[_idx:_end]
    assert "_existing_devices_refresh" in body
    assert "if callable(_refresher):" in body


# ─── D4: window layout save/restore ───


def test_d4_marker_present():
    src = _read("traffic_client/main.py")
    assert "v0.5.388 (audit devices-tab D4)" in src


def test_d4_settings_helper_defined():
    src = _read("traffic_client/main.py")
    assert "def _layout_settings(self):" in src
    assert "def _restore_window_layout(self):" in src
    assert "def _save_window_layout(self):" in src


def test_d4_qsettings_backed():
    src = _read("traffic_client/main.py")
    # Uses QSettings with stable org/app so ~/.config/netgen path
    assert '_LAYOUT_SETTINGS_ORG = "netgen"' in src
    assert '_LAYOUT_SETTINGS_APP = "netgen-client"' in src
    # Restore uses restoreGeometry + restoreState
    assert "self.restoreGeometry(_geo)" in src
    assert "self.restoreState(_state)" in src
    # Save uses saveGeometry + saveState
    assert "self.saveGeometry()" in src
    assert "self.saveState()" in src


def test_d4_wired_into_showevent_and_closeevent():
    src = _read("traffic_client/main.py")
    # showEvent restores once
    _idx = src.index("def showEvent(self, event):")
    _body_s = src[_idx:_idx + 3000]
    assert "_restore_window_layout" in _body_s
    assert "_layout_restored" in _body_s
    # closeEvent saves
    _idx2 = src.index("def closeEvent(self, event):")
    _body_c = src[_idx2:_idx2 + 5000]
    assert "self._save_window_layout()" in _body_c


# ─── D5: server tree preserves state ───


def test_d5_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.388 (audit devices-tab D5)" in src


def test_d5_captures_scroll_expanded_and_selection():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def update_server_tree(self):")
    _end = _idx + 8000
    body = src[_idx:_end]
    # scroll capture
    assert "_scroll_pos = _vs.value()" in body
    # expanded set capture
    assert "_expanded_addrs = set()" in body
    assert "_it.isExpanded():" in body
    # selection capture
    assert "_sel_key = None" in body


def test_d5_restores_state_after_rebuild():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def update_server_tree(self):")
    _end = src.index("logger.debug(f\"Tree widget updated with", _idx)
    body = src[_idx:_end]
    # Restore scroll
    assert "_vs.setValue(_scroll_pos)" in body
    # Restore expansion
    assert "_it.setExpanded(True)" in body
    # Restore selection (server or interface variant)
    assert "self.server_tree.setCurrentItem(_it)" in body


def test_d5_auto_select_gated_to_first_populate_only():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def update_server_tree(self):")
    _end = src.index("logger.debug(f\"Tree widget updated with", _idx)
    body = src[_idx:_end]
    # First-populate flag gates the auto-select
    assert "_server_tree_populated_once" in body
    assert "_first_populate" in body
    # And skips when we already restored a prior selection
    assert "if _first_populate and not _restored_sel" in body


# ─── version guard ───


def test_pyproject_version_at_least_0588():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 388)


# ─── regression guards ───


def test_v0387_c1_existing_dhcp_intact():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C1)" in src


def test_v0387_c3_row_reresolve_intact():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C3)" in src


def test_v0387_c4_server_remove_confirm_intact():
    src = _read("traffic_client/menu_actions.py")
    assert "v0.5.387 (audit devices-tab C4)" in src


def test_v0387_c5_ghost_prune_intact():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C5)" in src


def test_v0372_c4_device_delete_server_first_intact():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.372 (audit device-delete-ui-first-server-later)" in src
