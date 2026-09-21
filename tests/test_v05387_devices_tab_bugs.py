"""v0.5.387 — Device tab audit HIGHs + MEDs (5 items).

  C1  prompt_edit_device UnboundLocalError dhcp_config → existing_dhcp.
  C2  _apply_device_status_row re-verify row by device_id.
  C3  prompt_edit_device re-resolves row after modal.
  C4  remove_selected_server confirmation prompt.
  C5  reload_devices_from_server prunes ghost devices.
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


def test_menu_actions_ast_parses():
    ast.parse(_read("traffic_client/menu_actions.py"))


# ─── C1: prompt_edit_device UnboundLocalError ───


def test_c1_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C1)" in src


def test_c1_uses_existing_dhcp_not_dhcp_config():
    """The relay_return_hop + pool_router pre-fills must reference
    `existing_dhcp`, NOT the not-yet-bound `dhcp_config`."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.387 (audit devices-tab C1)")
    body = src[_idx:_idx + 5000]
    # Both fields now source from existing_dhcp
    assert 'existing_dhcp.get("relay_return_hop")' in body
    assert 'existing_dhcp.get("pool_router")' in body
    # No `dhcp_config.get(` in these pre-fill lines
    _prefill_slice = body[:body.find("dhcp_pool_router_input") + 400]
    # dhcp_config appears LATER as a tuple element, not in prefill
    assert "dhcp_config.get(\"relay_return_hop\")" not in _prefill_slice
    assert "dhcp_config.get(\"pool_router\")" not in _prefill_slice


def test_c1_swallowing_handler_is_error_level():
    """The bare `except Exception` handler that used to silently
    hide the UnboundLocalError must now log at ERROR + include
    traceback so future pre-fill bugs surface loudly."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("Protocol pre-fill skipped")
    body = src[max(0, _idx - 400):_idx + 800]
    assert "logger.error(" in body
    assert "traceback.format_exc()" in body


# ─── C2: status-row wrong-row race ───


def test_c2_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C2)" in src


def test_c2_signal_carries_device_id():
    """row_data signal signature must include device_id so the
    receiver can verify the row hint."""
    src = _read("widgets/devices_tab.py")
    assert "row_data = pyqtSignal(int, str, dict)" in src


def test_c2_receiver_verifies_and_rescans():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def _apply_device_status_row(self, row, device_id, device_data):")
    body = src[_idx:_idx + 3500]
    # Compares expected id against the row hint
    assert "if _row_id != _expected_id:" in body
    # Rescans the table when mismatched
    assert "for _r in range(self.devices_table.rowCount()):" in body
    # Skips gracefully when device_id is gone
    assert "if _found_row is None:" in body


def test_c2_emit_includes_device_id():
    """The worker's emit call must pass 3 args now."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.387 (audit devices-tab C2): also emit")
    body = src[_idx:_idx + 3000]
    assert "self.row_data.emit(_row, str(_dev_id), r.json() or {})" in body


# ─── C3: modal row invalidation ───


def test_c3_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C3)" in src


def test_c3_reresolves_row_after_modal_accept():
    """Immediately after `if dialog.exec_() != dialog.Accepted:
    return`, the handler must re-locate the row by _self_id."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.387 (audit devices-tab C3)")
    body = src[_idx:_idx + 2500]
    assert "_resolved_row = None" in body
    assert "for _r in range(self.devices_table.rowCount()):" in body
    assert 'str(_it.data(_Qt.UserRole) or "") == str(_self_id):' in body
    # Aborts with warning if the device is gone
    assert "no longer in the table" in body


def test_c3_row_variable_reassigned():
    """After the re-resolve, `row` must be reassigned to
    `_resolved_row` before the setText loop uses it."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.387 (audit devices-tab C3)")
    body = src[_idx:_idx + 2500]
    assert "row = _resolved_row" in body


# ─── C4: server-remove confirmation ───


def test_c4_marker_present():
    src = _read("traffic_client/menu_actions.py")
    assert "v0.5.387 (audit devices-tab C4)" in src


def test_c4_confirmation_dialog_before_removal():
    src = _read("traffic_client/menu_actions.py")
    _idx = src.index("v0.5.387 (audit devices-tab C4)")
    body = src[_idx:_idx + 2500]
    # QMessageBox.question with Yes/No, No default
    assert "QMessageBox.question(" in body
    assert "QMessageBox.No," in body
    # Returns without action when user declines
    assert "if _confirm != QMessageBox.Yes:" in body
    # Warns clearly when only ports (no top-level) are selected
    assert "You selected one or more PORTS" in body


def test_c4_lists_chassis_addresses_in_prompt():
    src = _read("traffic_client/menu_actions.py")
    _idx = src.index("v0.5.387 (audit devices-tab C4)")
    body = src[_idx:_idx + 2500]
    # Enumerates addresses in the confirmation body
    assert "_addr_lines" in body
    assert "it.text(1)" in body


# ─── C5: ghost prune ───


def test_c5_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.387 (audit devices-tab C5)" in src


def test_c5_prune_uses_merged_seen_ids():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.387 (audit devices-tab C5)")
    body = src[_idx:_idx + 4000]
    assert "if _did in merged_seen_ids:" in body
    # Skips prune when merged_seen_ids is empty (safety rail 1)
    assert "and merged_seen_ids:" in body
    # Preserves _needs_apply rows (safety rail 2)
    assert 'if _dev.get("_needs_apply"):' in body


def test_c5_replaces_cache_in_place():
    """Must mutate main_window.all_devices in-place (not rebind)
    so other holders of the reference see the pruned list."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("v0.5.387 (audit devices-tab C5)")
    body = src[_idx:_idx + 4000]
    assert "self.main_window.all_devices[:] = _pruned" in body


# ─── version guard ───


def test_pyproject_version_at_least_0587():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 387)


# ─── regression guards ───


def test_v0386_b5_stream_index_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.386 (audit stats-B5)" in src


def test_v0385_a3_pool_in_use_guard_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.385 (audit BGP-A3)" in src


def test_v0377_f2_gateway_confirm_intact():
    src = _read("widgets/devices_tab.py")
    # v0.5.377 added the empty-gateway confirm gate; C1/C3 must
    # not have removed it.
    assert "v0.5.377" in src


def test_v0294_dhcp_lease_display_intact():
    """v0.5.294 added the DHCP-lease surface in _apply_device_status_row;
    C2 refactored the signal but must not have removed the lease writes."""
    src = _read("widgets/devices_tab.py")
    assert "v0.5.314 (audit dhcp-lease-lost-on-nav)" in src
