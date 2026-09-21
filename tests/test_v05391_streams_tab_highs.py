"""v0.5.391 — Streams tab audit HIGHs (5 items).

  G1  Drop duplicate AddStreamDialog.accept — restore cross-layer validators.
  G2  Edit-Stream name change actually persists (top-level + protocol_selection).
  G3  Warn on Add-Stream name-collision auto-suffix (parity with v0.5.389 E3).
  G4  remove_selected_stream deletes by stream_id, not name.
  G5  remove_selected_stream snapshots targets before confirm modal.
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


def test_stream_dialog_ast_parses():
    ast.parse(_read("widgets/stream_dialog.py"))


def test_stream_control_ast_parses():
    ast.parse(_read("traffic_client/stream_control.py"))


# ─── G1: duplicate accept ───


def test_g1_marker_present():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.391 (audit streams G1)" in src


def test_g1_only_one_accept_def_on_AddStreamDialog():
    """After the fix there must be exactly ONE
    `def accept(self)` at the top-level of the class body
    (indented with 4 spaces)."""
    src = _read("widgets/stream_dialog.py")
    _accept_defs = re.findall(r"^    def accept\(self\):", src, re.MULTILINE)
    assert len(_accept_defs) == 1, (
        f"Expected exactly 1 `def accept(self)` on AddStreamDialog; "
        f"got {len(_accept_defs)}"
    )


def test_g1_surviving_accept_calls_validate_cross_layer():
    """The surviving accept must call _validate_cross_layer so
    Random Min<Max, L2/L3 combos, and PCAP checks all fire."""
    src = _read("widgets/stream_dialog.py")
    _idx = src.rindex("def accept(self):")
    _end = src.index("\n    def ", _idx + 1)
    body = src[_idx:_end]
    assert "self._validate_cross_layer()" in body
    # Save Anyway path still exists
    assert "Save Anyway" in body


def test_g1_validate_cross_layer_helper_intact():
    """The _validate_cross_layer helper itself must NOT have
    been removed — future callers + tests depend on it."""
    src = _read("widgets/stream_dialog.py")
    assert "def _validate_cross_layer(self) -> list:" in src


# ─── G2: Edit-Stream name persistence ───


def test_g2_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.391 (audit streams G2)" in src


def test_g2_persists_edited_name_not_original():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def edit_selected_stream(self):")
    _end = src.index("def remove_selected_stream(self):", _idx)
    body = src[_idx:_end]
    # Uses edited["name"] preference
    assert 'edited.get("name")' in body
    # Writes to BOTH top-level and protocol_selection
    assert 'updated["name"] = _new_name' in body
    assert 'updated["protocol_selection"]["name"] = _new_name' in body
    # No more unconditional stream_name overwrite
    assert 'updated["protocol_selection"]["name"] = stream_name\n' not in body


def test_g2_replacement_loop_matches_by_stream_id():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def edit_selected_stream(self):")
    _end = src.index("def remove_selected_stream(self):", _idx)
    body = src[_idx:_end]
    assert "_orig_sid = original.get(\"stream_id\") if original else None" in body
    assert "if _orig_sid and s.get(\"stream_id\") == _orig_sid:" in body


# ─── G3: Add-Stream auto-suffix warn ───


def test_g3_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.391 (audit streams G3)" in src


def test_g3_tracks_collision_and_warns():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("v0.5.391 (audit streams G3)")
    body = src[_idx:_idx + 3500]
    assert "_was_collision" in body
    assert 'QMessageBox.information(' in body
    assert 'Stream name changed' in body


def test_g3_empty_name_stays_silent():
    """Empty-name auto-suffix is the sensible default (operator
    left the field blank) — must NOT trigger the warn."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("v0.5.391 (audit streams G3)")
    body = src[_idx:_idx + 3500]
    # _was_collision only fires when a name WAS supplied
    assert "(not _was_empty) and (_requested_name in existing_names)" in body


# ─── G4: delete-by-stream_id ───


def test_g4_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.391 (audit streams G4" in src


def test_g4_delete_by_stream_id_not_name():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream(self):")
    _end = _idx + 12000
    body = src[_idx:_end]
    # New shape: filter by stream_id when available
    assert 'if s.get("stream_id") != _sid' in body
    # Legacy name-match kept only as fallback
    assert "else:" in body  # the sid-missing branch
    assert 'if s.get("protocol_selection", {}).get("name") != stream_name' in body


def test_g4_auto_stop_timer_cancels_by_stream_id():
    """The _cancel_auto_stop_timer call must fire by stream_id
    (not by name-match), so we don't cancel a same-named
    sibling's timer."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream(self):")
    _end = _idx + 12000
    body = src[_idx:_end]
    assert "self._cancel_auto_stop_timer(_sid)" in body


# ─── G5: snapshot-before-modal ───


def test_g5_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.391 (audit streams G4 + G5)" in src or "v0.5.391 (audit streams G5)" in src


def test_g5_snapshot_before_confirm_modal():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream(self):")
    _end = _idx + 12000
    body = src[_idx:_end]
    # Snapshot list built up-front
    assert "_targets = []" in body
    assert "_targets.append((_sid, _sname, _port_key, _iface_text))" in body
    # Confirm modal fires AFTER the snapshot
    _snapshot_pos = body.find("_targets.append(")
    _confirm_pos = body.find("QMessageBox.question(")
    assert _snapshot_pos > 0 and _confirm_pos > 0
    assert _snapshot_pos < _confirm_pos, (
        "Snapshot must be built BEFORE the confirm modal"
    )


def test_g5_delete_loop_uses_snapshot_not_selected_rows():
    """The delete loop must iterate `_targets`, not
    `selected_rows` — the latter has stale row indices after
    the confirm modal."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream(self):")
    _end = _idx + 12000
    body = src[_idx:_end]
    # Post-confirm loop iterates the snapshot
    assert "for _sid, stream_name, port_key, _iface_text in _targets:" in body
    # No more `for row in selected_rows:` post-confirm
    _post_confirm = body[body.rindex("QMessageBox.question("):]
    assert "for row in selected_rows:" not in _post_confirm


# ─── version guard ───


def test_pyproject_version_at_least_0591():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 391)


# ─── regression guards ───


def test_v0389_e3_device_name_collision_warn_intact():
    """G3 mirrors v0.5.389 E3 (device-tab). E3 must still be
    in place — v0.5.391 didn't touch that path."""
    src = _read("widgets/devices_tab.py")
    assert "v0.5.389 (audit devices-tab E3)" in src


def test_v0388_d1_device_multi_delete_pattern_intact():
    """G5 mirrors v0.5.388 D1 (device-tab). D1 must still be
    in place — same shape."""
    src = _read("widgets/devices_tab.py")
    assert "v0.5.388 (audit devices-tab D1)" in src


def test_v0372_stream_delete_confirm_intact():
    """v0.5.372 added the delete-stream confirm dialog. G5's
    snapshot-before-modal must not have removed the confirm."""
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.372 (audit stream-delete-no-confirm)" in src


def test_v0374_stream_stats_numeric_sort_intact():
    """v0.5.374 E2 introduced NumericSortItem for stream stats.
    G-series didn't touch statistics_section — sanity check."""
    src = _read("traffic_client/statistics_section.py")
    assert "NumericSortItem" in src
