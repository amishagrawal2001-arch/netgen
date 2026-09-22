"""v0.5.408 — stream-button audit fixes: 7 HIGH + 3 MEDIUM."""
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


def test_stream_logic_ast_parses():
    ast.parse(_read("traffic_client/stream_logic.py"))


def test_stream_control_ast_parses():
    ast.parse(_read("traffic_client/stream_control.py"))


# ─── H1: Stop All surfaces HTTP failure ───


def test_h1_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.408 (audit stream-H1)" in src


def test_h1_stop_all_collects_and_shows_errors():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def stop_all_streams")
    _end = src.index("def _any_running", _idx)
    body = src[_idx:_end]
    assert "_stop_all_errors: list[str] = []" in body
    # Both HTTP-fail and exception branches append.
    assert "_stop_all_errors.append(err_msg)" in body
    # End-of-function dialog.
    assert "Stop All — some servers failed" in body


# ─── H2: Apply restores status on failure + surfaces dialog ───


def test_h2_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert src.count("v0.5.408 (audit stream-H2)") >= 3


def test_h2_apply_stream_body_collects_errors():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def _apply_stream_body")
    _end = src.index("def send_inline_update_to_server", _idx)
    body = src[_idx:_end]
    assert 'self._apply_errors: list[str] = []' in body
    assert "self._apply_errors.append(_err)" in body
    # End dialog.
    assert "Apply — some operations failed" in body


def test_h2_stop_branch_restores_running_on_failure():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def _apply_stream_body")
    _end = src.index("def send_inline_update_to_server", _idx)
    body = src[_idx:_end]
    # After a failed /stop we restore status="running" so we
    # don't lie about a stream that may still be alive.
    assert '_s_apply["status"] = "running"' in body


# ─── H3: Apply cancels + reschedules auto-stop timers on restart ───


def test_h3_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.408 (audit stream-H3)" in src


def test_h3_restart_success_cancels_and_reschedules_timer():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("v0.5.408 (audit stream-H3)")
    body = src[_idx:_idx + 2000]
    assert "self._cancel_auto_stop_timer(_sid_r)" in body
    assert "self._schedule_stream_auto_stop(" in body


# ─── H4: Delete Stream stops running + honest confirm text ───


def test_h4_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert src.count("v0.5.408 (audit stream-H4)") >= 2


def test_h4_delete_batches_stop_before_local_remove():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream")
    _end = src.index("def _get_stream_by_port_and_name", _idx) if "_get_stream_by_port_and_name" in src[_idx:] else _idx + 20000
    body = src[_idx:_end]
    assert "_running_targets" in body
    assert 'self._post_traffic_async(' in body
    assert '"stop"' in body


def test_h4_confirm_text_no_longer_lies():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream")
    _end = src.index("def _get_stream_by_port_and_name", _idx) if "_get_stream_by_port_and_name" in src[_idx:] else _idx + 20000
    body = src[_idx:_end]
    # The old lie is gone.
    assert "removes the stream from BOTH the desktop client " not in body
    # The new text notes the running-count.
    assert "currently RUNNING and will be stopped on the server" in body


# ─── H5 + H6 + M13: Start All partial-parse + error dialog ───


def test_h5_h6_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.408 (audit stream-H5)" in src
    assert "v0.5.408 (audit stream-H6" in src


def test_h5_start_all_parses_partial_success_on_non_ok():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def start_all_streams")
    _end = src.index("def apply_stream", _idx)
    body = src[_idx:_end]
    assert "_started_partial" in body
    assert 'resp.json() or {}' in body


def test_m13_start_all_error_dialog():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def start_all_streams")
    _end = src.index("def apply_stream", _idx)
    body = src[_idx:_end]
    assert "_start_all_errors" in body
    assert "Start All — some servers failed" in body


# ─── H7: red paints pass stream_id= ───


def test_h7_marker_count_at_least_three():
    src = _read("traffic_client/stream_logic.py")
    # 3 sites patched: start_stream cancel branch, start_stream
    # exception branch, start_all_streams exception branch.
    assert src.count("v0.5.408 (audit stream-H7)") >= 3


def test_h7_no_more_bare_red_paints_in_starts():
    """The four documented sites now all pass stream_id="""
    src = _read("traffic_client/stream_logic.py")
    # start_all exception branch uses _sid_h6.
    assert 'self.update_stream_status(r, "red", stream_id=_sid_h6)' in src
    # start_stream cancel branch (H7 comment) uses sid.
    assert 'self.update_stream_status(r, "red", stream_id=sid)' in src


# ─── M8: enabled checkbox matches by stream_id ───


def test_m8_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.408 (audit stream-M8)" in src


def test_m8_enabled_checkbox_uses_stream_id():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("v0.5.408 (audit stream-M8)")
    body = src[_idx:_idx + 2500]
    assert "_target_stream" in body
    assert '_s.get("stream_id") == stream_id' in body
    # Duplicate-name refusal.
    assert "Refusing to toggle" in body


# ─── M11: stop paths cancel timers ───


def test_m11_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert src.count("v0.5.408 (audit stream-M11)") >= 3


def test_m11_stop_stream_cancels_timer():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def stop_stream(self):")
    _end = src.index("def _begin_button_feedback", _idx)
    body = src[_idx:_end]
    assert "self._cancel_auto_stop_timer(stream_id_from_table)" in body


def test_m11_stop_all_cancels_timer():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def stop_all_streams")
    _end = src.index("def _any_running", _idx)
    body = src[_idx:_end]
    assert "self._cancel_auto_stop_timer(sid)" in body


def test_m11_apply_stop_branch_cancels_timer():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def _apply_stream_body")
    _end = src.index("def send_inline_update_to_server", _idx)
    body = src[_idx:_end]
    assert "self._cancel_auto_stop_timer(_sid_apply)" in body


# ─── version guard ───


def test_pyproject_at_least_0608():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 408)


# ─── regression guards ───


def test_v0407_sink_pin_gate_intact():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.407 (audit stats-Z1)" in src


def test_v0406_y1_rebuild_pin_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.406 (audit stats-Y1)" in src


def test_v0405_w1_pin_helpers_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "def _pin_client_stop(self, stream_id):" in src


def test_v0391_g4_delete_by_stream_id_intact():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def remove_selected_stream")
    _end = src.index("def _get_stream_by_port_and_name", _idx) if "_get_stream_by_port_and_name" in src[_idx:] else _idx + 20000
    body = src[_idx:_end]
    assert 's.get("stream_id") != _sid' in body


def test_v0394_k5_stop_by_id_no_red_on_fail_intact():
    src = _read("traffic_client/stream_logic.py")
    # _stop_stream_by_id K5 pattern preserved.
    _idx = src.index("def _stop_stream_by_id")
    _end = src.index("def _schedule_stream_auto_stop", _idx)
    body = src[_idx:_end]
    # K5 comment tag still present.
    assert "K5" in body or "must not paint red" in body.lower()
