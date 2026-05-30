"""Tests for the RFC 2544 dialog (v0.2.59).

Covers the bits we can test without spinning up a real server:
  * Param payload assembly — `capture_latency` flag round-trips, plus
    all the existing fields stay where they were.
  * Results-row population from a `/api/rfc2544/progress` payload — both
    the new latency columns AND the legacy (no-latency) payload from a
    pre-0.2.59 server render correctly.
  * `build_rfc2544_html_report` — pure function, easy to exercise on
    canned payloads; assert the structural pieces (params block, results
    table, summary footer) and the graceful "no results" / missing
    latency cases.
"""

from unittest.mock import MagicMock

import pytest
from PyQt5.QtWidgets import QApplication

from widgets.rfc2544_dialog import (RFC2544_FRAME_SIZES, Rfc2544Dialog,
                                    build_rfc2544_html_report)


# ────────────────────────────────────────────────────── dialog construction
def test_dialog_constructs_with_expected_columns(qapp):
    dlg = Rfc2544Dialog(server_url="http://1.1.1.1")
    # 0.2.59 added 3 latency columns → 5 + 3 = 8 total.
    assert dlg.results_table.columnCount() == 8
    assert dlg.results_table.rowCount() == len(RFC2544_FRAME_SIZES)
    # Capture-latency checkbox exists and defaults off so the §26.1
    # throughput test stays bit-identical to pre-0.2.59 runs.
    assert dlg.latency_checkbox.isChecked() is False
    # HTML export button exists and is disabled until a test finishes.
    assert dlg.export_html_btn.isEnabled() is False


def test_current_params_includes_capture_latency(qapp):
    """The /api/rfc2544/start payload must carry the new flag so the
    server knows whether to enable timestamps + snapshot latency."""
    dlg = Rfc2544Dialog(server_url="http://1.1.1.1")
    dlg.latency_checkbox.setChecked(True)
    params = dlg._current_params_for_report()
    assert params["capture_latency"] is True
    # And the existing fields still flow.
    for k in ("tx_iface", "mac_src", "mac_dst", "ip_src", "ip_dst",
              "duration_per_step", "target_loss_pct", "resolution_pps",
              "dpdk_enable"):
        assert k in params


# ─────────────────────────────────────────── progress-row population (poll)
def _fake_progress_response(progress):
    """Wrap a progress list in the shape /api/rfc2544/progress returns."""
    m = MagicMock()
    m.json.return_value = {"running": False, "progress": progress}
    return m


def test_poll_populates_latency_columns_when_present(qapp, monkeypatch):
    """A 0.2.59 server returns a `latency` dict per entry. The dialog
    must put p50/p95/p99 into cols 5/6/7."""
    import widgets.rfc2544_dialog as mod
    progress = [{
        "frame_size": 64,
        "max_no_drop_pps": 14_000_000,
        "max_no_drop_gbps": 9.4,
        "pct_of_line_rate": 94.0,
        "attempts": [{}, {}, {}],
        "latency": {"p50_us": 12.3, "p95_us": 45.6, "p99_us": 99.9},
    }]
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_progress_response(progress))

    dlg = Rfc2544Dialog(server_url="http://1.1.1.1")
    dlg._poll_progress()
    row = RFC2544_FRAME_SIZES.index(64)
    assert dlg.results_table.item(row, 1).text() == "14,000,000"
    assert dlg.results_table.item(row, 2).text() == "9.40"
    assert dlg.results_table.item(row, 5).text() == "12.3"
    assert dlg.results_table.item(row, 6).text() == "45.6"
    assert dlg.results_table.item(row, 7).text() == "99.9"


def test_poll_gracefully_handles_old_server_without_latency(qapp, monkeypatch):
    """A pre-0.2.59 server returns NO `latency` field. The latency
    columns must show "—" rather than crashing or showing 'None'."""
    import widgets.rfc2544_dialog as mod
    progress = [{
        "frame_size": 128,
        "max_no_drop_pps": 8_500_000,
        "max_no_drop_gbps": 8.9,
        "pct_of_line_rate": 89.0,
        "attempts": [{}, {}],
        # No "latency" key at all — old server.
    }]
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_progress_response(progress))

    dlg = Rfc2544Dialog(server_url="http://1.1.1.1")
    dlg._poll_progress()
    row = RFC2544_FRAME_SIZES.index(128)
    for col in (5, 6, 7):
        assert dlg.results_table.item(row, col).text() == "—"


def test_poll_handles_latency_with_some_missing_percentiles(qapp, monkeypatch):
    """Sampler with no samples returns None for percentile fields.
    Missing-percentile cells must render '—' individually — not the
    whole row."""
    import widgets.rfc2544_dialog as mod
    progress = [{
        "frame_size": 256,
        "max_no_drop_pps": 4_500_000,
        "max_no_drop_gbps": 9.7,
        "pct_of_line_rate": 97.0,
        "attempts": [{}],
        "latency": {"p50_us": 20.1, "p95_us": None, "p99_us": 80.0},
    }]
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_progress_response(progress))

    dlg = Rfc2544Dialog(server_url="http://1.1.1.1")
    dlg._poll_progress()
    row = RFC2544_FRAME_SIZES.index(256)
    assert dlg.results_table.item(row, 5).text() == "20.1"
    assert dlg.results_table.item(row, 6).text() == "—"
    assert dlg.results_table.item(row, 7).text() == "80.0"


# ────────────────────────────────────── build_rfc2544_html_report (pure fn)
def _sample_params():
    return {
        "tx_iface": "enp1s0", "rx_iface": "enp1s1",
        "mac_src": "aa:bb:cc:dd:ee:01", "mac_dst": "aa:bb:cc:dd:ee:02",
        "ip_src": "10.0.0.1", "ip_dst": "10.0.0.2",
        "duration_per_step": 10, "target_loss_pct": 0.0,
        "resolution_pps": 100_000,
        "dpdk_enable": True, "capture_latency": True,
    }


def test_html_report_contains_params_and_data_rows():
    rows = [
        {"frame_size": 64,  "max_no_drop_pps": 14_000_000,
         "max_no_drop_gbps": 9.4, "pct_of_line_rate": 94.0,
         "attempts": [{}, {}, {}],
         "latency": {"p50_us": 12.3, "p95_us": 45.6, "p99_us": 99.9}},
        {"frame_size": 1518, "max_no_drop_pps": 812_000,
         "max_no_drop_gbps": 9.9, "pct_of_line_rate": 99.0,
         "attempts": [{}],
         "latency": {"p50_us": 8.2, "p95_us": 15.0, "p99_us": 22.5}},
    ]
    html = build_rfc2544_html_report(_sample_params(), rows,
                                     server_url="http://1.1.1.1")
    # Structural pieces present.
    assert "<title>RFC 2544 Throughput Test Report</title>" in html
    assert "Test parameters" in html
    assert "Results" in html
    # Params surface.
    assert "enp1s0" in html and "enp1s1" in html
    assert "aa:bb:cc:dd:ee:01" in html
    # Rows surface.
    assert "14,000,000" in html
    assert "812,000" in html
    # Latency columns rendered with the new units.
    assert "Lat p50 (µs)" in html
    assert "12.3" in html and "45.6" in html and "99.9" in html
    # Summary footer picks the best Gbps.
    assert "9.9 Gbps" in html
    # Self-contained — no external resources.
    assert "<link rel=" not in html
    assert "<script" not in html


def test_html_report_missing_latency_renders_em_dash():
    """When latency wasn't captured, the cells render — and the report
    is still well-formed."""
    rows = [{"frame_size": 64, "max_no_drop_pps": 1_000_000,
             "max_no_drop_gbps": 0.7, "pct_of_line_rate": 7.0,
             "attempts": [{}]}]  # no latency key
    html = build_rfc2544_html_report(_sample_params(), rows)
    assert "<td>—</td>" in html
    assert "<table class='results'>" in html


def test_export_default_filenames_are_timestamped(qapp, monkeypatch):
    """Both CSV and HTML exports should pre-populate the Save dialog
    with a YYYY-MM-DD_HH-MM-SS filename so re-exports don't silently
    collide and the operator can hit Save without typing. Pinned in
    v0.2.74; the CSV path was missing it before."""
    import re
    from PyQt5 import QtWidgets

    dlg = Rfc2544Dialog(server_url="http://1.1.1.1")

    seen = {}
    def _spy(*a, **k):
        # QFileDialog.getSaveFileName(parent, caption, default_name, filter)
        # — defensively read by both positional and keyword.
        if len(a) >= 3:
            seen["default"] = a[2]
        # Return ("", "") to cancel and skip the rest of the export.
        return ("", "")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(_spy))

    dlg._on_export_csv()
    assert "default" in seen
    assert re.match(r"rfc2544_results_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.csv",
                    seen["default"]), f"got: {seen['default']!r}"

    seen.clear()
    dlg._on_export_html()
    assert re.match(r"rfc2544_report_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.html",
                    seen["default"]), f"got: {seen['default']!r}"


def test_html_report_no_results_shows_message():
    """An empty progress list (test cancelled / hadn't started) renders
    a visible error line instead of a malformed empty table."""
    html = build_rfc2544_html_report(_sample_params(), [])
    assert "no results" in html.lower()
    assert "<table class='results'>" not in html
