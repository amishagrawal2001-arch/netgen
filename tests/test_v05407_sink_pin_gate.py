"""v0.5.407 — Stop All still leaves rows green (v0.5.406 miss).

  Z1  update_stream_status sink checks the pin itself, forces red
      inside grace window regardless of caller's intent.
  Z2  All start-path green paints now clear the pin AND pass stream_id.
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


def test_stream_control_ast_parses():
    ast.parse(_read("traffic_client/stream_control.py"))


def test_stream_logic_ast_parses():
    ast.parse(_read("traffic_client/stream_logic.py"))


# ─── Z1: sink-level pin gate ───


def test_z1_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.407 (audit stats-Z1)" in src


def test_z1_sink_reads_pin_dict():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status")
    _end = src.index("# ---------- copy/paste", _idx)
    body = src[_idx:_end]
    assert '_pinned = getattr(self, "_client_stopped_streams", None)' in body


def test_z1_sink_forces_red_on_pin_hit():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status")
    _end = src.index("# ---------- copy/paste", _idx)
    body = src[_idx:_end]
    # Grace-window constant + color-force.
    assert "15.0" in body
    assert 'color = "red"' in body


def test_z1_sink_bails_when_color_is_red():
    """If caller already wants red, no need to consult the pin
    (and doing so pointlessly would still be a no-op)."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status")
    _end = src.index("# ---------- copy/paste", _idx)
    body = src[_idx:_end]
    assert 'if stream_id is not None and color != "red":' in body


def test_z1_sink_bails_when_stream_id_none():
    """Backwards-compat: legacy callers that don't pass stream_id
    are exempt (the pin is per-sid; without a sid there's nothing
    to check)."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status")
    _end = src.index("# ---------- copy/paste", _idx)
    body = src[_idx:_end]
    # The composite guard: stream_id is not None AND color != "red"
    assert "stream_id is not None" in body


# ─── Z2: parity clears on start-path ───


def test_z2_marker_present():
    src = _read("traffic_client/stream_logic.py")
    # Four Z2 sites in start-path branches originally; v0.5.413
    # CC2 replaced 2 of them (start_all success + fallback) with
    # unconditional pin-clears. Remaining 2 are in start_stream.
    assert src.count("v0.5.407 (audit stats-Z2)") >= 2


def test_z2_start_path_all_clear_pin_before_green():
    """Each green paint in start_stream / start_all_streams
    must clear the pin first — otherwise the Z1 sink refuses it."""
    src = _read("traffic_client/stream_logic.py")
    # v0.5.413 CC2 added 2 more unconditional pin-clears in
    # start_all_streams' success/fallback branches, so we now
    # expect ≥5 total: W3 one + Z2 two + CC2 two.
    assert src.count("self._clear_client_stop(") >= 5


# ─── version guard ───


def test_pyproject_at_least_0607():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 407)


# ─── regression guards ───


def test_v0406_y1_rebuild_pin_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.406 (audit stats-Y1)" in src
    assert "_pinned_rebuild" in src


def test_v0405_w1_pin_helpers_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "def _pin_client_stop(self, stream_id):" in src
    assert "def _clear_client_stop(self, stream_id):" in src


def test_v0405_w4_refresh_pin_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.405 (audit stats-W4)" in src


def test_v0404_u2_hysteresis_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "_STOPPED_CONFIRM_THRESHOLD = 3" in src


def test_v0393_j2_counter_advance_still_in_place():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 8000]
    assert "if _counters_advanced:" in body


def test_v0392_h1_row_reresolve_intact():
    """The H1 stream_id row-reresolve must survive the Z1 changes."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status")
    _end = src.index("# ---------- copy/paste", _idx)
    body = src[_idx:_end]
    assert "_it.data(Qt.UserRole) == stream_id" in body
    assert "_target_row = _r" in body
