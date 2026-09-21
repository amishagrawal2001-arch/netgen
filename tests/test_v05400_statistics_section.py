"""v0.5.400 — statistics_section.py client-side stats poll audit.

  Q1  Multi-TG per-signal update_per_stream_statistics → aggregated in _on_poll_finished.
  Q2  Chart pushes zeros on no-fresh-data tick (was drawing stale non-zero line).
  Q3  format_rate returns "—" on parse fail / None (was "0.00 pps" lie).
  Q4  Disconnect previous worker's slots before rebinding to avoid stale finished.
  Q5  _stream_counter_history TTL prune folded into existing sweep.
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


def _strip_comments(src: str) -> str:
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


# ─── Q1: multi-TG aggregation ───


def test_q1_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q1)" in src


def test_q1_per_signal_call_removed():
    """_on_poll_stream_stats_fetched must NOT call
    update_per_stream_statistics anymore — aggregated call lives in
    _on_poll_finished."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _on_poll_stream_stats_fetched")
    _end = src.index("def _on_poll_fetch_error", _idx)
    body = _strip_comments(src[_idx:_end])
    assert "self.update_per_stream_statistics(" not in body, (
        "per-signal update_per_stream_statistics call still exists "
        "in _on_poll_stream_stats_fetched — Q1 regression"
    )


def test_q1_aggregated_call_in_poll_finished():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _on_poll_finished")
    _end = src.index("def update_per_stream_statistics", _idx)
    body = src[_idx:_end]
    # The aggregated call uses `_filtered` (already ghost-scrubbed)
    assert "self.update_per_stream_statistics(_filtered)" in body


# ─── Q2: chart zeros on no data ───


def test_q2_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q2)" in src


def test_q2_pushes_zeroed_stats_shape():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.400 (audit stats-Q2)")
    body = src[_idx:_idx + 2500]
    # Builds zeroed copy in the {iface: {send_bps: 0}} shape
    assert '_copy["send_bps"] = 0.0' in body
    assert "self._push_chart_sample(_zeroed_stats)" in body


# ─── Q3: format_rate returns "—" on parse fail ───


def test_q3_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q3)" in src


def test_q3_format_rate_returns_dash_on_none_and_parse_fail():
    """The runtime format_rate defined inside
    update_stream_statistics_table must return '—' on None and on
    parse failure."""
    src = _read("traffic_client/statistics_section.py")
    # Grab the second format_rate (the Q3-fixed one, inside
    # update_stream_statistics_table).
    _first = src.index("def format_rate(")
    _second = src.index("def format_rate(", _first + 10)
    _end = src.index("all_streams = []", _second)
    body = src[_second:_end]
    # Distinguishes None → "—"
    assert 'if rate_val is None:' in body
    assert 'return "—"' in body
    # Retains genuine 0.0 = "0.00 pps"
    assert 'return "0.00 pps"' in body
    # Parse-fail path returns "—"
    _except_count = body.count('return "—"')
    assert _except_count >= 2, (
        f"expected ≥2 `return \"—\"` sites (None + parse-fail); "
        f"got {_except_count}"
    )


# ─── Q4: disconnect old worker's slots ───


def test_q4_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q4)" in src


def test_q4_disconnect_before_rebind():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.400 (audit stats-Q4)")
    body = src[_idx:_idx + 2500]
    # Fetches prev worker before assigning new one
    assert '_prev_worker = getattr(self, "_stats_worker", None)' in body
    # Disconnects the 4 signal bindings
    for _sig in (
        '"interfaces_fetched"',
        '"stream_stats_fetched"',
        '"fetch_error"',
        '"finished"',
    ):
        assert _sig in body, f"Q4 missing disconnect for {_sig}"
    # Uses try/except to survive already-disconnected / dead C++ obj
    assert "except (TypeError, RuntimeError):" in body


# ─── Q5: counter_history TTL prune ───


def test_q5_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q5)" in src


def test_q5_counter_history_folded_into_ttl_sweep():
    src = _read("traffic_client/statistics_section.py")
    # The prune block references _counter_hist_dict alongside
    # _baselines_dict / _latched_dict.
    _idx = src.index("v0.5.400 (audit stats-Q5)")
    body = src[_idx:_idx + 2500]
    assert "_counter_hist_dict = getattr(self, \"_stream_counter_history\", None)" in body
    # Included in the _need_prune trigger
    assert "len(_counter_hist_dict) > _STREAM_CACHE_SOFT_CAP" in body
    # Evicted alongside the other caches
    assert "_counter_hist_dict.pop(_sid_e, None)" in body


# ─── version guard ───


def test_pyproject_at_least_0600():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 400)


# ─── regression guards ───


def test_v0399_p2_status_clear_intact():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P2)" in src


def test_v0398_o1_iso_cutoff_intact():
    src = _read("utils/stream_database.py")
    assert "timedelta(days=days)" in src


def test_v0393_j2_counter_advance_override_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.393 (audit streams J2" in src


def test_v0382_w4_backoff_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "_should_poll_server" in src
