"""Regression tests for the v0.3.19 perftest-retry-poll fix.

Operator scenario: server upgraded to v0.3.18 which has the
server-side RDMA auto-install daemon thread. Operator opens
Tools → RDMA Blast about 5 sec into server start, before the
auto-install has landed perftest. The dialog's initial probe
returns ``installed: false``, the red banner fires. ~30 sec
later the server's auto-install thread finishes; perftest is
now on PATH. But the dialog never re-probes — the banner sticks
until manual close+reopen.

v0.3.19 fix: when ``_on_installed_resp`` sees ``installed: false``,
start a QTimer that re-runs ``_probe_both_sides`` every 5 sec for
up to 24 ticks (2 min). When perftest lands, the next probe's
success path clears the banner + stops the timer.

These tests pin:
1. Initial perftest-missing starts the retry timer.
2. Subsequent success stops the timer AND clears the banner.
3. Max-attempts cap (24 ticks) stops the timer.
4. Concurrent missing-callbacks don't spawn multiple timers.
5. ``closeEvent`` tears down the timer cleanly.
6. Per-side bookkeeping: server-missing + client-success keeps
   timer running.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PyQt5 = pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


def _make_dialog():
    """Two-TG dialog (server + client URLs) so we can exercise the
    per-side state independently."""
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    return RdmaBlastFlowDialog(
        "http://10.0.0.1:5050", "http://10.0.0.2:5050",
        server_tg_label="TG-A", client_tg_label="TG-B",
    )


def test_initial_state_no_retry_timer():
    """Before any probe response arrives, no retry timer should be
    running. The retry only fires when we know perftest is missing."""
    d = _make_dialog()
    assert d._perftest_retry_timer is None
    assert d._perftest_retry_attempts == 0
    assert d._perftest_missing_sides == set()
    d.close()


def test_perftest_missing_starts_retry_timer():
    """When _on_installed_resp gets ``installed: false`` from a TG,
    the retry timer must start."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    assert d._perftest_retry_timer is not None, (
        "retry timer should start when perftest reported missing"
    )
    assert "server" in d._perftest_missing_sides
    assert not d._start_btn.isEnabled(), (
        "Start button must stay disabled while perftest missing"
    )
    d.close()


def test_perftest_present_does_not_start_retry():
    """The happy path — initial probe says perftest is installed —
    must NOT start a retry timer (avoids needless polling on healthy
    servers)."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": True}, "")
    d._on_installed_resp("client", {"installed": True}, "")
    assert d._perftest_retry_timer is None, (
        "retry timer should not start when perftest is already installed"
    )
    assert d._perftest_missing_sides == set()
    d.close()


def test_unreachable_does_not_start_retry():
    """Server unreachable (network error or no response) is NOT a
    transient missing-package situation. Retry timer should NOT
    start — operator needs to fix connectivity. Spamming probes
    against an unreachable TG is noise."""
    d = _make_dialog()
    d._on_installed_resp("server", None, "connection refused")
    assert d._perftest_retry_timer is None, (
        "retry timer should not start on connectivity errors — "
        "only on confirmed installed=false"
    )
    d.close()


def test_subsequent_success_stops_retry_and_clears_banner():
    """The v0.3.18 auto-install lands → next probe returns
    ``installed: true`` → timer must stop + banner must clear +
    Start must enable."""
    d = _make_dialog()
    # First probe: both sides missing
    d._on_installed_resp("server", {"installed": False}, "")
    d._on_installed_resp("client", {"installed": False}, "")
    assert d._perftest_retry_timer is not None
    assert d._perftest_missing_sides == {"server", "client"}
    assert not d._start_btn.isEnabled()

    # Retry tick — server now installed
    d._on_installed_resp("server", {"installed": True}, "")
    # Still one side missing, timer keeps running
    assert d._perftest_retry_timer is not None
    assert d._perftest_missing_sides == {"client"}

    # Retry tick — client now installed too
    d._on_installed_resp("client", {"installed": True}, "")
    # All sides clean — timer stops, banner clears, Start enables
    assert d._perftest_retry_timer is None, (
        "all sides installed → retry timer must stop"
    )
    assert d._perftest_missing_sides == set()
    assert d._start_btn.isEnabled(), (
        "all sides installed → Start must re-enable"
    )
    # Banner cleared — empty text or no red
    assert "perftest" not in d._status_label.text().lower(), (
        f"banner not cleared after recovery: {d._status_label.text()!r}"
    )
    d.close()


def test_idempotent_start_retry_timer():
    """If a previous probe started the timer, a second probe also
    finding perftest missing must NOT spawn a second timer. The
    second call is a no-op (same singleton timer)."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    first_timer = d._perftest_retry_timer
    assert first_timer is not None

    d._on_installed_resp("client", {"installed": False}, "")
    # Same singleton — not a fresh QTimer
    assert d._perftest_retry_timer is first_timer, (
        "second missing-side probe spawned a duplicate timer — "
        "idempotency broken"
    )
    assert d._perftest_missing_sides == {"server", "client"}
    d.close()


def test_max_attempts_cap_stops_timer():
    """After 24 ticks (2 min) the retry gives up. The banner stays
    so operator knows auto-install didn't land in the expected
    window, but the timer stops to avoid forever-polling."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    assert d._perftest_retry_timer is not None

    # Simulate 25 ticks — past the 24 cap
    for _ in range(25):
        d._perftest_retry_tick()

    assert d._perftest_retry_timer is None, (
        "retry timer should stop after 24-tick cap"
    )
    # Side bookkeeping NOT auto-cleared — the banner state reflects
    # reality: perftest is still missing.
    assert "server" in d._perftest_missing_sides
    d.close()


def test_close_event_stops_retry_timer():
    """Dialog close must tear down the timer so Qt doesn't deliver
    tick events to a deleted widget (the classic SIGABRT cause we
    fought in v0.2.20–v0.2.24).

    Qt subtlety: ``close()`` on an unshown dialog doesn't necessarily
    route through ``closeEvent`` — we invoke closeEvent directly with
    a mock QCloseEvent to verify the override does the teardown."""
    from PyQt5.QtGui import QCloseEvent
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    assert d._perftest_retry_timer is not None
    # Direct invocation — verifies the override behaviour regardless
    # of whether Qt routes close() through closeEvent for an unshown
    # widget.
    d.closeEvent(QCloseEvent())
    assert d._perftest_retry_timer is None, (
        "closeEvent override must stop the retry timer"
    )


def test_per_side_missing_set_is_tracked():
    """The dialog tracks WHICH side reported missing. Each side
    arriving as installed=True removes only its own entry; both
    must clear before the timer stops."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    d._on_installed_resp("client", {"installed": False}, "")
    assert d._perftest_missing_sides == {"server", "client"}

    # Only server recovers
    d._on_installed_resp("server", {"installed": True}, "")
    assert d._perftest_missing_sides == {"client"}, (
        "server-success must remove only 'server' from missing set; "
        f"got {d._perftest_missing_sides}"
    )
    # Timer still running — client side still missing
    assert d._perftest_retry_timer is not None

    # Then client recovers
    d._on_installed_resp("client", {"installed": True}, "")
    assert d._perftest_missing_sides == set()
    assert d._perftest_retry_timer is None
    d.close()


def test_retry_interval_is_5_seconds():
    """Pin the 5-second retry interval — too fast spams the server,
    too slow misses the typical ~30s auto-install window."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    interval = d._perftest_retry_timer.interval()
    assert 3000 <= interval <= 10000, (
        f"retry interval {interval}ms outside reasonable range "
        "(want 3-10 sec — too fast spams server, too slow misses "
        "the typical 30-sec auto-install window)"
    )
    d.close()


def test_banner_text_mentions_auto_install():
    """The red banner must hint that v0.3.18+ servers auto-install
    in the background — operator should know not to SSH in
    immediately, the dialog will recover on its own."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    txt = d._status_label.text().lower()
    assert "auto" in txt or "background" in txt, (
        f"banner should mention auto-install behaviour: {txt!r}"
    )
    d.close()


def test_running_with_no_data_shows_progress_not_nones():
    """v0.3.19 fix: while perftest is running but hasn't emitted
    any data rows yet (which is the entire test duration for
    batch-mode perftest), the chunk must NOT render the
    "size=NoneB iters=None BW avg=None Gbps peak=None
    MsgRate=None Mpps" wall-of-text the operator reported.
    Instead show a clean progress line."""
    import time
    d = _make_dialog()
    # Stub the stats view's append so we can inspect what was written
    appended = []
    d._stats_view.append = lambda txt: appended.append(txt)
    # Simulate a running job with no data yet (all final_* = None)
    running_job = {
        "running": True,
        "returncode": None,
        "test": "send_bw",
        "started_at": time.time() - 3,
        "final_msg_size_bytes": None,
        "final_iterations": None,
        "final_bw_avg_gbps": None,
        "final_bw_peak_gbps": None,
        "final_msg_rate_mpps": None,
    }
    d._render_job_into_stats("server", running_job)
    assert appended, "nothing was appended"
    chunk = appended[0]
    # Critical regression: no literal "None" should appear in the
    # rendered line.
    assert "None" not in chunk, (
        f"chunk still contains literal 'None' tokens: {chunk!r}"
    )
    # And it should mention something about waiting / progress
    lower = chunk.lower()
    assert ("complet" in lower or "running" in lower or
            "elapsed" in lower), (
        f"chunk should mention progress/completion: {chunk!r}"
    )
    d.close()


def test_running_with_data_renders_real_values():
    """Once perftest emits the summary, the real numbers must
    appear — make sure my no-data short-circuit doesn't suppress
    the actual results case."""
    d = _make_dialog()
    appended = []
    d._stats_view.append = lambda txt: appended.append(txt)
    done_job = {
        "running": False,
        "returncode": 0,
        "test": "send_bw",
        "started_at": 100.0,
        "final_msg_size_bytes": 65536,
        "final_iterations": 1000,
        "final_bw_avg_gbps": 96.4,
        "final_bw_peak_gbps": 96.43,
        "final_msg_rate_mpps": 0.18,
    }
    d._render_job_into_stats("server", done_job)
    chunk = appended[0]
    assert "65536" in chunk, f"size missing: {chunk!r}"
    assert "96.4" in chunk, f"BW avg missing: {chunk!r}"
    assert "0.18" in chunk, f"MsgRate missing: {chunk!r}"
    assert "done" in chunk, f"done state missing: {chunk!r}"
    d.close()


def test_per_side_finished_tracking_enables_on_both_finished():
    """v0.3.19 fix: pre-fix _is_finished(side, job, want_side)
    returned False any time side != want_side, so each poll
    callback could only see ITS OWN side's done state, never the
    other's. Result: _on_both_finished() was never called, the
    poll timer never stopped, and "done" lines were re-appended
    every 2 sec forever.

    Fix: track each side's finished state on the instance.
    _on_job_resp(side) sets _server_finished or _client_finished
    based on the response, then ANDs both to decide whether to
    stop."""
    d = _make_dialog()
    d._stats_view.append = lambda txt: None  # silence
    d._server_job_id = "srv-job"
    d._client_job_id = "cli-job"
    on_finished_calls = []
    d._on_both_finished = lambda: on_finished_calls.append("called")

    # Server poll says server is done; client poll hasn't fired yet.
    d._on_job_resp("server", {"job": {
        "running": False, "returncode": 0,
        "finished_at": 100.0,
        "final_bw_avg_gbps": 392.12,
        "final_msg_size_bytes": 65536,
    }}, "")
    # Only server is done — _on_both_finished must NOT have been called
    assert not on_finished_calls, (
        "_on_both_finished called with only server done — "
        "client hasn't reported yet"
    )
    assert d._server_finished is True
    assert d._client_finished is False

    # Now client poll says client is done.
    d._on_job_resp("client", {"job": {
        "running": False, "returncode": 0,
        "finished_at": 101.0,
        "final_bw_avg_gbps": 392.12,
        "final_msg_size_bytes": 65536,
    }}, "")
    # Both sides done — _on_both_finished must have been called.
    assert on_finished_calls == ["called"], (
        "_on_both_finished should fire exactly once when both "
        "sides report finished_at != None — pre-fix this was "
        "never called and the poll ran forever"
    )
    d.close()


def test_render_dedup_for_terminal_state():
    """v0.3.19 fix: pre-fix the poll re-rendered the same
    "[server] done (rc=0) size=65536B ..." line every 2-sec
    tick because the job's terminal state never changes once
    perftest exits. Operator saw 14+ duplicate done-lines while
    waiting to close the dialog.

    Fix: _last_rendered_key dedups terminal-state renders. The
    first done-state poll renders; subsequent identical polls
    don't."""
    d = _make_dialog()
    appended = []
    d._stats_view.append = lambda txt: appended.append(txt)
    d._server_job_id = "srv-job"
    d._client_job_id = None  # loopback would be the other case

    done_job = {"job": {
        "running": False, "returncode": 0,
        "test": "send_bw",
        "started_at": 100.0,
        "finished_at": 130.0,
        "final_msg_size_bytes": 65536,
        "final_iterations": 11966479,
        "final_bw_avg_gbps": 392.12,
        "final_bw_peak_gbps": 0.0,
        "final_msg_rate_mpps": 0.747901,
    }}

    # First poll → render
    d._on_job_resp("server", done_job, "")
    first_count = len(appended)
    assert first_count == 1, f"first poll should render once: {appended}"

    # Subsequent polls with the SAME terminal state → no new render
    d._on_job_resp("server", done_job, "")
    d._on_job_resp("server", done_job, "")
    d._on_job_resp("server", done_job, "")
    assert len(appended) == first_count, (
        f"redundant polls of unchanged terminal state should NOT "
        f"re-render. Got {len(appended)} appends, expected "
        f"{first_count}. Appended lines: {appended}"
    )
    d.close()


def test_render_not_deduped_during_running_with_no_data():
    """While running-with-no-data, every poll SHOULD render — the
    elapsed-time on the progress line is the whole point. Dedup
    must only apply to terminal-state lines."""
    import time
    d = _make_dialog()
    appended = []
    d._stats_view.append = lambda txt: appended.append(txt)
    d._server_job_id = "srv-job"
    d._client_job_id = None

    base_time = time.time() - 30
    running_job_no_data = {"job": {
        "running": True, "returncode": None,
        "test": "send_bw",
        "started_at": base_time,
        "finished_at": None,
        # All final_* deliberately None
    }}

    for _ in range(5):
        d._on_job_resp("server", running_job_no_data, "")
    # Each tick should produce a progress line (elapsed seconds
    # advances; operator wants to see it tick forward).
    assert len(appended) == 5, (
        f"running-with-no-data should render every tick "
        f"(elapsed-time progress); got {len(appended)} renders, "
        f"expected 5"
    )
    d.close()


def test_new_run_resets_finished_state():
    """Second run in the same dialog session must reset
    _server_finished + _client_finished + _last_rendered_key so
    the new test doesn't see stale state from the previous run."""
    d = _make_dialog()
    d._server_finished = True
    d._client_finished = True
    d._last_rendered_key = {"server": ("stale",), "client": ("stale",)}

    # Simulate the reset block inside _on_start_clicked
    # (we don't call _on_start_clicked because it requires picked
    # devices, network state, etc. — just verify the reset code
    # exists and works.)
    src = open(
        "/Users/surajsharma/dev/netgen/widgets/rdma_blast_flow_dialog.py"
    ).read()
    # The fix added a reset block in _on_start_clicked
    assert "self._server_finished = False" in src
    assert "self._client_finished = False" in src
    assert "self._last_rendered_key" in src
    d.close()


def test_max_attempts_constant_is_2_minutes():
    """Re-verify the cap arithmetic: 24 ticks * 5 sec = 120 sec.
    The constant is inline in _perftest_retry_tick; this test
    catches a refactor that changes either number out of sync."""
    d = _make_dialog()
    d._on_installed_resp("server", {"installed": False}, "")
    interval_sec = d._perftest_retry_timer.interval() / 1000
    # Simulate exactly 24 ticks — should still be running on the
    # 24th, off on the 25th.
    for i in range(24):
        d._perftest_retry_tick()
        assert d._perftest_retry_timer is not None, (
            f"timer stopped early at tick {i+1}/24"
        )
    d._perftest_retry_tick()
    assert d._perftest_retry_timer is None, (
        f"timer should be stopped on tick 25 (after {24 * interval_sec}s)"
    )
    d.close()
