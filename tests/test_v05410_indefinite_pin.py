"""v0.5.410 — 15 s pin expired while server still said running.

Trace-driven fix. See CHANGELOG for the diagnostic log.

  AA1  pin lifetime is float('inf') across W1 / W4 / Y1 / Z1 gates.
  AA2  poll's server-confirmed-stopped branch clears the pin.
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


def test_stream_control_ast_parses():
    ast.parse(_read("traffic_client/stream_control.py"))


# ─── AA1: indefinite grace across all four pin gates ───


def test_aa1_marker_present():
    for _p in (
        "traffic_client/statistics_section.py",
        "traffic_client/server_section.py",
        "traffic_client/stream_control.py",
    ):
        assert "v0.5.410 (audit stream-AA1)" in _read(_p), _p


def test_aa1_statistics_poll_grace_is_infinite():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.410 (audit stream-AA1)")
    body = src[_idx:_idx + 1200]
    assert '_GRACE_S = float("inf")' in body
    assert "_pin_active = _pin_ts is not None" in body


def test_aa1_refresh_in_place_grace_is_infinite():
    src = _read("traffic_client/server_section.py")
    # W4 site (_refresh_stream_status_in_place uses _GRACE_S_RO).
    _idx = src.index("_GRACE_S_RO = float(\"inf\")")
    assert _idx > 0


def test_aa1_rebuild_grace_is_infinite():
    src = _read("traffic_client/server_section.py")
    # Y1 site (_do_update_stream_table uses _GRACE_S_REBUILD).
    _idx = src.index("_GRACE_S_REBUILD = float(\"inf\")")
    assert _idx > 0


def test_aa1_sink_no_more_15s_literal():
    """The Z1 sink pin gate must not compare against the 15.0
    grace literal any more — it should just check pin_ts is not None."""
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status")
    _end = src.index("# ---------- copy/paste", _idx)
    body = src[_idx:_end]
    # No more 15.0-second literal here.
    assert "< 15.0" not in body
    # The new form: pin_ts is not None → force red.
    assert "if _pin_ts is not None:" in body


# ─── AA2: server-confirmed-stopped hand-off clears the pin ───


def test_aa2_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.410 (audit stream-AA2)" in src


def test_aa2_hysteresis_success_branch_clears_pin():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.410 (audit stream-AA2)")
    body = src[_idx:_idx + 1500]
    # Only reached after U2 confirm-count threshold — hand-off signal.
    assert "self._clear_client_stop(sid_for_history)" in body


# ─── version guard ───


def test_pyproject_at_least_0610():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 410)


# ─── regression guards ───


def test_v0407_z1_sink_gate_intact():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.407 (audit stats-Z1)" in src


def test_v0405_w1_pin_helpers_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "def _pin_client_stop(self, stream_id):" in src


def test_v0404_u2_hysteresis_threshold_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "_STOPPED_CONFIRM_THRESHOLD = 3" in src


def test_v0393_j2_counter_advance_intact():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 8000]
    assert "if _counters_advanced:" in body


def test_v0409_diag_logging_intact():
    src = _read("traffic_client/stream_control.py")
    assert "[STATE-PAINT]" in src
