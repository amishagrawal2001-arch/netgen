"""v0.5.415 — Stream Statistics panel audit fixes (5 HIGH + 3 MED).

  DD1  stats-table status column consults client-stop pin
  DD2  restore Q3 None-vs-0.0 distinction
  DD3  ThroughputChart uses monotonic()
  DD4  resizeColumnsToContents runs once per structural change
  DD5  update_per_stream_statistics honors _refresh_paused
  DD6  chip ticks on empty-response polls
  DD7  amber-when-stale watchdog implemented
  DD11 dedupe _stopped_confirm_count across dual-worker paths
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


def test_stats_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


# ─── DD1: stats-table status column consults pin ───


def test_dd1_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.415 (audit stats-DD1)" in src


def test_dd1_stats_table_reads_pin_dict():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.415 (audit stats-DD1)")
    body = src[_idx:_idx + 2500]
    assert '_pinned_status = getattr(self, "_client_stopped_streams", None) or {}' in body
    assert '_sid_status and _sid_status in _pinned_status' in body
    assert 'status = "stopped"' in body


# ─── DD2: None vs 0.0 distinction ───


def test_dd2_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert src.count("v0.5.415 (audit stats-DD2)") >= 2


def test_dd2_none_uses_muted_display():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.415 (audit stats-DD2)")
    body = src[_idx:_idx + 2500]
    # None → "—", 0.0 → "0.00 pps" — no more collapsed OR.
    assert "if tx_rate is None:" in body
    assert 'tx_rate_display = "—"' in body
    assert "elif tx_rate == 0.0:" in body


# ─── DD3: ThroughputChart uses monotonic ───


def test_dd3_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.415 (audit stats-DD3)" in src


def test_dd3_chart_add_sample_uses_monotonic():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def add_sample(self, iface_to_bps, ts=None):")
    _end = src.index("def clear_samples", _idx)
    body = src[_idx:_end]
    assert "ts = _time.monotonic()" in body
    assert "ts = _time.time()" not in body


# ─── DD4: resize runs once per structural change ───


def test_dd4_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.415 (audit stats-DD4)" in src


def test_dd4_resize_gated_on_col_count():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.415 (audit stats-DD4)")
    body = src[_idx:_idx + 1500]
    assert "_stream_stats_autosized" in body
    assert "_stream_stats_last_col_count" in body
    # The unconditional call is gone; guarded call fires only when
    # not-yet-autosized or column count changed.
    assert "if not _autosized or _now_col_count != _prev_col_count:" in body


# ─── DD5: pause gate ───


def test_dd5_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.415 (audit stats-DD5)" in src


def test_dd5_pause_gate_short_circuits():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def update_per_stream_statistics(self, stream_stats):")
    _end = src.index("def ", _idx + 100)
    body = src[_idx:_end]
    assert 'if getattr(self, "_refresh_paused", False):' in body
    assert "return" in body


# ─── DD6: chip ticks on empty polls ───


def test_dd6_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.415 (audit stats-DD6)" in src


def test_dd6_empty_response_still_stamps_chip():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.415 (audit stats-DD6)")
    body = src[_idx:_idx + 1200]
    assert "self._update_last_refresh_chip()" in body
    assert "return" in body


# ─── DD7: amber-when-stale watchdog ───


def test_dd7_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert src.count("v0.5.415 (audit stats-DD7)") >= 2


def test_dd7_watchdog_method_defined():
    src = _read("traffic_client/statistics_section.py")
    assert "def _check_refresh_chip_staleness(self):" in src


def test_dd7_watchdog_timer_armed_lazily():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _update_last_refresh_chip")
    _end = src.index("def _check_refresh_chip_staleness", _idx)
    body = src[_idx:_end]
    # Timer is armed inside _update_last_refresh_chip
    assert "self._refresh_stale_timer" in body
    assert "self._last_refresh_monotonic = _time.monotonic()" in body


def test_dd7_watchdog_flips_amber_and_back():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _check_refresh_chip_staleness")
    _end = src.index("def export_statistics_csv", _idx)
    body = src[_idx:_end]
    # Amber colour is #b45309
    assert "#b45309" in body
    # 5-second staleness threshold constant
    assert "_REFRESH_STALE_S = 5.0" in body
    # Pause skip
    assert 'if getattr(self, "_refresh_paused", False):' in body


# ─── DD11: dedupe hysteresis bumps ───


def test_dd11_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.415 (audit stats-DD11)" in src


def test_dd11_min_bump_interval_enforced():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.415 (audit stats-DD11)")
    body = src[_idx:_idx + 2500]
    assert "_MIN_BUMP_S = 1.5" in body
    assert "_stopped_bump_ts" in body
    # Same-cycle duplicate short-circuits without bumping.
    assert "(_now_bump - _prev_bump) < _MIN_BUMP_S" in body


# ─── version guard ───


def test_pyproject_at_least_0615():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 415)


# ─── regression guards ───


def test_v0410_indefinite_pin_intact():
    src = _read("traffic_client/statistics_section.py")
    assert '_GRACE_S = float("inf")' in src


def test_v0404_u2_hysteresis_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "_STOPPED_CONFIRM_THRESHOLD = 3" in src


def test_v0405_w1_pin_helpers_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "def _pin_client_stop(self, stream_id):" in src


def test_v0393_j2_counter_advance_intact():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 8000]
    assert "if _counters_advanced:" in body
