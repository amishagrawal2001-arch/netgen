"""v0.5.393 — Status shows red while traffic flowing (3 fixes).

  J1  Server /api/streams/stats keeps Running when counters non-zero
      even if stream_tracker index is missing the sid.
  J2  Client statistics_section trusts advancing counters over the
      server's "stopped" label — override paints green + logs drift.
  J3  Client statistics_section passes stream_id= to update_stream_status
      so v0.5.392 H1's row re-resolution kicks in.
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


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


# ─── J1: server counter-trust ───


def test_j1_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.393 (audit streams J1)" in src


def test_j1_counters_override_tracker_miss():
    src = _read("run_tgen_server.py")
    # Scan the verifier block
    _idx = src.index("v0.5.393 (audit streams J1)")
    body = src[_idx:_idx + 3000]
    # Reads both tx and rx counts as ints from the stream dict
    assert 'int(stream.get("tx_count", 0)' in body
    assert 'int(stream.get("rx_count", 0)' in body
    # Guards downgrade with counter check
    assert "if tx_count_now > 0 or rx_count_now > 0:" in body
    # Keeps Running on non-zero counters
    assert 'actual_status = "Running"' in body
    # Logs the drift
    assert "tracker drift" in body.lower()


def test_j1_still_downgrades_on_zero_counters():
    """Belt-and-suspenders: the ORIGINAL downgrade path must still
    fire when both counters are zero (real dead stream)."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.393 (audit streams J1)")
    body = src[_idx:_idx + 3000]
    # Original correcting-to-Stopped log line is preserved in the else branch
    assert "correcting to 'Stopped'" in body
    # Rates zeroed in that branch
    assert "tx_rate = 0.0" in body


# ─── J2: client counter override ───


def test_j2_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.393 (audit streams J2" in src


def test_j2_history_dict_tracked():
    src = _read("traffic_client/statistics_section.py")
    # per-sid (tx, rx) history via getattr lazy dict
    assert "_stream_counter_history" in src
    assert 'getattr(self, "_stream_counter_history", None)' in src
    assert "prev[sid_for_history] = (_tx_now, _rx_now)" in src


def test_j2_advance_triggers_green_override():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.393 (audit streams J2")
    body = src[_idx:_idx + 5000]
    # The stopped branch checks the override
    assert 'server_status == "stopped":' in body
    assert "if _counters_advanced:" in body
    # Under override we paint GREEN not red
    _stopped_idx = body.index('elif server_status == "stopped":')
    _absent_idx = body.index(', "green"', _stopped_idx)
    assert _absent_idx > _stopped_idx  # green paint exists inside stopped-branch
    # Warns on drift so root-cause stays traceable
    assert "trusting" in body.lower() and "counters" in body.lower()


def test_j2_advance_check_also_in_absent_branch():
    """When sid isn't in stat_map at all, one absent poll should NOT
    flip a running stream to red if counters just advanced."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.393 (audit streams J2")
    # Widen slice so it reaches into the trailing paint call.
    body = src[_idx:_idx + 8000]
    _else_marker = body.index("Stream not in stats at all")
    _tail = body[_else_marker:]
    # Bounded by the next top-level def or the return of the outer method
    _next_def = _tail.find("\n    def ")
    if _next_def != -1:
        _tail = _tail[:_next_def]
    assert "if _counters_advanced:" in _tail
    # Green paint present in that tail block
    assert ', "green"' in _tail


# ─── J3: stream_id= wiring ───


def test_j3_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.393 (audit streams" in src and "J3" in src


def test_j3_all_paint_calls_pass_stream_id():
    """Every update_stream_status call inside the counter-history block
    must pass stream_id= so H1's row re-resolution kicks in."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.393 (audit streams J2")
    _end = _idx + 8000
    body = src[_idx:_end]
    # Trim at next top-level def to avoid picking up unrelated later calls
    _next = body.find("\n    def ", 200)
    if _next != -1:
        body = body[:_next]
    paint_calls = re.findall(r"self\.update_stream_status\(row,\s*\"[^\"]+\"([^)]*)\)", body)
    assert len(paint_calls) >= 4, f"Expected ≥4 paint calls in J2/J3 block; got {len(paint_calls)}"
    for _tail in paint_calls:
        assert "stream_id=" in _tail, (
            f"paint call missing stream_id= kwarg: "
            f"self.update_stream_status(row, ...{_tail})"
        )


# ─── version guard ───


def test_pyproject_at_least_0593():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 393)


# ─── regression guards ───


def test_v0392_h1_update_stream_status_stream_id_intact():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.392 (audit streams H1)" in src


def test_v0392_h5_status_pushed_prune_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.392 (audit streams H5)" in src
