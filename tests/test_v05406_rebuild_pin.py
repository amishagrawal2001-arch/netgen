"""v0.5.406 — structural rebuild bypassed the W1/W4 pin.

  Y1  _do_update_stream_table now consults _client_stopped_streams
      and forces red inside the 15 s grace window.
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


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


# ─── Y1 markers + implementation ───


def test_y1_marker_present():
    src = _read("traffic_client/server_section.py")
    # Two Y1 comment sites — one at the hoist, one at the per-stream branch.
    assert src.count("v0.5.406 (audit stats-Y1)") >= 2


def test_y1_pinned_dict_hoisted_before_port_loop():
    src = _read("traffic_client/server_section.py")
    _idx_hoist = src.index("v0.5.406 (audit stats-Y1)")
    body = src[_idx_hoist:_idx_hoist + 1500]
    # The hoist body reads the dict and stashes a monotonic timestamp.
    assert '_pinned_rebuild = getattr(self, "_client_stopped_streams", None) or {}' in body
    assert "_GRACE_S_REBUILD = 15.0" in body
    assert "_now_rebuild = _time_rebuild.monotonic()" in body


def test_y1_per_stream_forces_red_inside_grace():
    src = _read("traffic_client/server_section.py")
    # Second Y1 site — check the color-decision branch.
    _first = src.index("v0.5.406 (audit stats-Y1)")
    _second = src.index("v0.5.406 (audit stats-Y1)", _first + 1)
    body = src[_second:_second + 1500]
    # Reads sid off the stream dict and the pin timestamp.
    assert '_sid_for_pin = stream.get("stream_id")' in body
    assert "_pin_ts_r = _pinned_rebuild.get(_sid_for_pin)" in body
    # Grace-window check + forces red BEFORE the running check.
    assert '_now_rebuild - _pin_ts_r) < _GRACE_S_REBUILD' in body
    assert 'dot_color, status_label = "red", "Stopped"' in body


def test_y1_forces_red_before_running_check():
    """The pin branch must be first — a rebuild where the pin
    fires AND status='running' has to paint red, not green."""
    src = _read("traffic_client/server_section.py")
    _first = src.index("v0.5.406 (audit stats-Y1)")
    _second = src.index("v0.5.406 (audit stats-Y1)", _first + 1)
    body = src[_second:_second + 1500]
    _pin_pos = body.index("_pin_ts_r is not None")
    _running_pos = body.index('elif status == "running"')
    assert _pin_pos < _running_pos, (
        "The pin branch must precede the 'running' branch or a "
        "racing _paint_green will still win."
    )


# ─── version guard ───


def test_pyproject_at_least_0606():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 406)


# ─── regression guards ───


def test_v0405_w1_pin_helpers_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "def _pin_client_stop(self, stream_id):" in src
    assert "def _clear_client_stop(self, stream_id):" in src


def test_v0405_w4_refresh_pin_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.405 (audit stats-W4)" in src
    assert '_pinned_ro = getattr(self, "_client_stopped_streams", None)' in src


def test_v0404_u2_hysteresis_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "_stopped_confirm_count" in src
    assert "_STOPPED_CONFIRM_THRESHOLD = 3" in src


def test_v0393_j2_counter_advance_still_in_place():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 8000]
    assert "if _counters_advanced:" in body
