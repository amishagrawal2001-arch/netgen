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


# ─────────────────────────────────── v0.4.0 Scapy pre-flight warning


def _make_dialog_for_preflight():
    """Construct dialog with a fake server_url + populated MAC/IP
    fields so _on_start passes the MAC/IP validation gate and
    reaches the Scapy pre-flight check."""
    app = QApplication.instance() or QApplication([])
    dlg = Rfc2544Dialog(server_url="http://10.0.0.1:5050")
    dlg.tx_iface_field.setText("enp181s0f0np0")
    dlg.mac_src_field.setText("02:00:00:00:00:01")
    dlg.mac_dst_field.setText("02:00:00:00:00:02")
    dlg.ip_src_field.setText("10.0.0.1")
    dlg.ip_dst_field.setText("10.0.0.2")
    return dlg


def test_preflight_warns_when_scapy_selected(monkeypatch):
    """Scapy + RFC2544_FRAME_SIZES (includes 64B) → warning fires.
    Operator clicking No must abort _on_start before any POST."""
    dlg = _make_dialog_for_preflight()
    dlg.dpdk_checkbox.setChecked(False)

    posted = []
    monkeypatch.setattr("requests.post",
                        lambda *a, **kw: posted.append((a, kw)) or MagicMock())

    # Simulate operator clicking "No" on the warning
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **kw: QMessageBox.No)

    dlg._on_start()
    assert posted == [], (
        "operator declined the Scapy warning but POST was sent anyway — "
        "pre-flight check didn't gate the start"
    )


def test_preflight_does_not_warn_when_dpdk_enabled(monkeypatch):
    """DPDK selected → no warning even with 64B frames. DPDK can
    actually reach the rates the test probes."""
    dlg = _make_dialog_for_preflight()
    dlg.dpdk_checkbox.setChecked(True)

    warned = []
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **kw: warned.append(a) or QMessageBox.No)
    # Stub requests.post so we don't hit the network
    monkeypatch.setattr("requests.post",
                        lambda *a, **kw: MagicMock(json=lambda: {"status":"started"}))

    dlg._on_start()
    # The warning() helper is used for OTHER cases (e.g. server errors),
    # but our pre-flight Scapy warning should NOT fire when DPDK is on.
    for call_args in warned:
        title = call_args[1] if len(call_args) > 1 else ""
        assert "Scapy" not in str(title), (
            f"Scapy-specific pre-flight warning fired despite DPDK enabled: "
            f"title={title!r}"
        )


def test_preflight_continue_anyway_proceeds_to_post(monkeypatch):
    """Operator clicking Yes on the Scapy warning means 'I know the
    risks, run it anyway' → _on_start must proceed to POST."""
    dlg = _make_dialog_for_preflight()
    dlg.dpdk_checkbox.setChecked(False)

    posted = []
    fake_resp = MagicMock()
    fake_resp.json = lambda: {"status": "started"}
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **kw: posted.append((a, kw)) or fake_resp,
    )

    # Simulate operator clicking Yes
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **kw: QMessageBox.Yes)

    dlg._on_start()
    assert len(posted) == 1, (
        "operator clicked Yes ('continue anyway') but POST was not "
        "issued — Yes path should proceed to start the test"
    )
    # And dpdk_enable=False made it into the payload
    posted_body = posted[0][1].get("json", {})
    assert posted_body.get("dpdk_enable") is False


# ─────────────────────────────────── v0.4.0 live in-flight row updates


def test_in_flight_row_renders_attempts_during_search():
    """v0.4.0 fix: during the binary search for a frame size, the
    client used to leave that row as dashes for 13+ minutes until
    the whole search converged. Now the client surfaces
    current_step.{iteration_count, last_attempt} as
    'trying X pps' / 'loss Y%' / 'iter N' so the operator sees
    visible progress every poll tick instead of staring at a row
    of '—' for 13 minutes."""
    from unittest.mock import patch
    app = QApplication.instance() or QApplication([])
    d = Rfc2544Dialog(server_url="http://10.0.0.1:5050")

    fake = MagicMock()
    fake.json = lambda: {
        "running": True,
        "progress": [],   # nothing completed yet
        "current_step": {
            "frame_size": 64,
            "trying_pps": 297_619_047,
            "phase": "testing 297,619,047 pps for 60s",
            "iteration_count": 3,
            "last_attempt": {
                "pps": 74_404_761, "tx": 80_000, "rx": 0,
                "loss_pct": 100.0,
            },
        },
    }
    with patch("requests.get", return_value=fake):
        d._poll_progress()

    row = RFC2544_FRAME_SIZES.index(64)
    assert "trying" in d.results_table.item(row, 1).text()
    assert "74,404,761" in d.results_table.item(row, 1).text()
    assert "loss" in d.results_table.item(row, 3).text()
    assert "100" in d.results_table.item(row, 3).text()
    assert d.results_table.item(row, 4).text() == "iter 3"
    # Rows for frame sizes not yet started must NOT have the
    # "trying" hint contaminating them.
    other_row = RFC2544_FRAME_SIZES.index(128)
    assert d.results_table.item(other_row, 1).text() == "—"


def test_in_flight_row_resets_when_search_moves_on():
    """After the binary search moves from 64B to 128B, the 64B row
    might still have a leftover 'trying X' label if the next
    progress payload doesn't include 64B in the completed list AND
    current_step has moved on. The poll loop should clear those
    leftover labels."""
    from unittest.mock import patch
    app = QApplication.instance() or QApplication([])
    d = Rfc2544Dialog(server_url="http://10.0.0.1:5050")

    # First poll: 64B in flight
    fake1 = MagicMock()
    fake1.json = lambda: {
        "running": True, "progress": [],
        "current_step": {
            "frame_size": 64, "trying_pps": 10_000_000,
            "iteration_count": 2, "phase": "...",
            "last_attempt": {"pps": 10_000_000, "loss_pct": 50.0},
        },
    }
    with patch("requests.get", return_value=fake1):
        d._poll_progress()
    row64 = RFC2544_FRAME_SIZES.index(64)
    assert "trying" in d.results_table.item(row64, 1).text()

    # Second poll: 64B completed, 128B in flight, but 64B is in
    # progress[] so it should render the FINAL values, not "trying".
    fake2 = MagicMock()
    fake2.json = lambda: {
        "running": True,
        "progress": [{
            "frame_size": 64, "max_no_drop_pps": 5_000_000,
            "max_no_drop_gbps": 2.56, "pct_of_line_rate": 0.84,
            "attempts": [{}, {}, {}],
        }],
        "current_step": {
            "frame_size": 128, "trying_pps": 5_000_000,
            "iteration_count": 1, "phase": "...",
            "last_attempt": {"pps": 5_000_000, "loss_pct": 0.0},
        },
    }
    with patch("requests.get", return_value=fake2):
        d._poll_progress()
    # 64B now shows real numbers, not "trying" anymore
    assert "trying" not in d.results_table.item(row64, 1).text()
    assert "5,000,000" in d.results_table.item(row64, 1).text()
    # 128B now shows the in-flight hint
    row128 = RFC2544_FRAME_SIZES.index(128)
    assert "trying" in d.results_table.item(row128, 1).text()
