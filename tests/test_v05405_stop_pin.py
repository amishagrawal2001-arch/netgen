"""v0.5.405 — Stop click leaves stream row stuck GREEN (user-reported).

  W1  _client_stopped_streams + _pin_client_stop / _clear_client_stop helpers.
  W2  All stop paths pin: _stop_stream_by_id, stop_stream, stop_all_streams.
  W3  start_stream clears the pin so restart repaints green immediately.
  W4  _refresh_stream_status_in_place forces red inside grace window.
  W5  Pin dict added to Q5-style TTL prune.
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


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


def test_stream_logic_ast_parses():
    ast.parse(_read("traffic_client/stream_logic.py"))


# ─── W1: helpers + grace window ───


def test_w1_helpers_defined():
    src = _read("traffic_client/statistics_section.py")
    assert "def _pin_client_stop(self, stream_id):" in src
    assert "def _clear_client_stop(self, stream_id):" in src
    # Grace window in force
    _idx = src.index("v0.5.405 (audit stats-W1)")
    body = src[_idx:_idx + 4000]
    assert "_GRACE_S = 15.0" in body


def test_w1_force_red_inside_grace_window():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.405 (audit stats-W1)")
    body = src[_idx:_idx + 4000]
    # Grace-window branch forces red and skips the rest
    assert "if _pin_active:" in body
    assert 'stream["status"] = "stopped"' in body
    assert 'self.update_stream_status(' in body


# ─── W2: pinning from stop paths ───


def test_w2_stop_stream_by_id_pins():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def _stop_stream_by_id(self")
    _end = src.index("def _schedule_stream_auto_stop", _idx) if "def _schedule_stream_auto_stop" in src[_idx:] else _idx + 3500
    body = src[_idx:_end]
    assert "self._pin_client_stop(stream_id)" in body


def test_w2_stop_stream_pins():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def stop_stream(self):")
    _end = src.index("def _begin_button_feedback", _idx)
    body = src[_idx:_end]
    assert "self._pin_client_stop(stream_id_from_table)" in body


def test_w2_stop_all_pins():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def stop_all_streams(self):")
    _end = src.index("def _apply_stream_body", _idx) if "_apply_stream_body" in src[_idx:] else _idx + 10000
    body = src[_idx:_end]
    assert "self._pin_client_stop(sid)" in body


# ─── W3: start clears ───


def test_w3_start_clears_pin():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def start_stream(self):")
    _end = src.index("def stop_stream(self):", _idx)
    body = src[_idx:_end]
    assert "self._clear_client_stop(sid)" in body


# ─── W4: in-place refresh forces red in grace window ───


def test_w4_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.405 (audit stats-W4)" in src


def test_w4_refresh_forces_red_inside_grace_window():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.405 (audit stats-W4)")
    body = src[_idx:_idx + 3500]
    # Reads pinned dict
    assert "_pinned_ro = getattr(self, \"_client_stopped_streams\", None)" in body
    assert "_GRACE_S_RO = 15.0" in body
    # Overrides color to red inside window
    assert '_pin_ts is not None and (_now_ro - _pin_ts) < _GRACE_S_RO' in body


# ─── W5: TTL prune includes pin dict ───


def test_w5_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.405 (audit stats-W5)" in src


def test_w5_pin_dict_pruned_alongside_other_caches():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.405 (audit stats-W5)")
    body = src[_idx:_idx + 2500]
    assert '_pinned_dict = getattr(self, "_client_stopped_streams", None)' in body
    assert "_pinned_dict.pop(_sid_e, None)" in body


# ─── version guard ───


def test_pyproject_at_least_0605():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 405)


# ─── regression guards ───


def test_v0404_u2_hysteresis_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "_stopped_confirm_count" in src


def test_v0404_path_a_aggregation_intact():
    src = _read("traffic_client/statistics_section.py")
    # Aggregated call after the per-server loop must be present.
    assert "self.update_per_stream_statistics(all_stream_stats)" in src


def test_v0393_j2_counter_advance_still_in_place():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 8000]
    assert "if _counters_advanced:" in body
    assert "trusting" in body.lower() and "counters" in body.lower()
