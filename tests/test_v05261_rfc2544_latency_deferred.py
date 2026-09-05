"""v0.5.261 — RFC 2544 (RFC-5/6/9) + latency-8 deferred fixes."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SERVER = (REPO / "run_tgen_server.py").read_text()


def _step_body():
    idx = SERVER.find("def _rfc2544_run_step(")
    end = SERVER.find("\ndef _rfc2544_thread(", idx + 1)
    return SERVER[idx:end if end > 0 else idx + 12000]


def _start_traffic_body():
    idx = SERVER.find("def start_traffic():")
    # Very large function — take a generous window then trim.
    end = SERVER.find("\n@app.route", idx + 1)
    return SERVER[idx:end if end > 0 else idx + 20000]


# --- RFC-5: cancel-during-sleep skips decide_step -----------------


def test_cancel_mid_sleep_tracked_and_skips_decide_step():
    body = _step_body()
    assert "audit RFC-5" in body
    assert "cancelled_mid_sleep = False" in body
    assert "cancelled_mid_sleep = True" in body
    # `if cancelled_mid_sleep:` guard placed BEFORE the decide_step call.
    _cancel_guard = body.find("if cancelled_mid_sleep:")
    _decide_call = body.find("lo_pps, hi_pps, last_good, step_diag = _rfc2544_decide_step(")
    assert _cancel_guard > 0 and _decide_call > 0
    assert _cancel_guard < _decide_call
    # Sets diagnosis to "cancelled" and breaks.
    assert 'diagnosis = "cancelled"' in body


# --- RFC-6: two-consecutive-stable stats read ---------------------


def test_stats_read_waits_for_two_stable_samples():
    body = _step_body()
    assert "audit RFC-6" in body
    # New helper + polling loop.
    assert "def _read_stats_once():" in body
    assert "_wait_deadline = _t.monotonic() + 3.0" in body
    # Stability tolerance is 0.1% (< 0.001).
    assert "< 0.001" in body


def test_stats_read_replaces_bare_half_second_sleep():
    body = _step_body()
    # The old `_t.sleep(0.5)` right before stats read is gone (only
    # 0.5s sleep is inside the duration wait loop).
    live_bare_flush_sleeps = [
        line for line in body.splitlines()
        if line.lstrip() == "_t.sleep(0.5)"
    ]
    # There is still ONE `_t.sleep(0.5)` inside the duration-wait
    # loop — that's expected. But not TWO (which would mean the old
    # flush-window sleep is still present).
    assert len(live_bare_flush_sleeps) <= 1


# --- RFC-9: measure actual TX window -----------------------------


def test_actual_tx_window_measured():
    body = _step_body()
    assert "audit RFC-9" in body
    assert "tx_window_start = _t.monotonic()" in body
    assert "tx_window_end = _t.monotonic()" in body
    assert "actual_tx_window_s = max(0.001, tx_window_end - tx_window_start)" in body


def test_decide_step_uses_actual_tx_window():
    body = _step_body()
    # The decide_step call passes actual_tx_window_s, not duration_s.
    assert "duration_s=actual_tx_window_s" in body
    # And no live call uses the old `duration_s=duration_s`.
    live_old = [
        line for line in body.splitlines()
        if "duration_s=duration_s," in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"decide_step still called with nominal duration: {live_old!r}"


# --- latency-8: Scapy TX + enable_timestamps mismatch surfaced ---


def test_scapy_latency_mismatch_detected_and_surfaced():
    body = _start_traffic_body()
    assert "audit latency-8" in body
    assert "_latency_scapy_gap" in body
    assert '"latency_capture_ignored"' in body
    assert '"latency_capture_reason"' in body


def test_latency_mismatch_message_names_dpdk_requirement():
    body = _start_traffic_body()
    # The user-visible message must name the fix action.
    idx = body.find("NLAT timestamp emission requires the DPDK")
    assert idx > 0
    reason = body[idx:idx + 400]
    assert "Scapy" in reason
    assert "Enable DPDK" in reason or "enable DPDK" in reason


def test_latency_mismatch_logs_warning_on_start():
    body = _start_traffic_body()
    # WARNING log so operators grepping journalctl see the class.
    assert "[LATENCY]" in body
    assert "sampler will decode 0 NLAT frames" in body


def test_ts_requested_checks_all_three_flag_locations():
    body = _start_traffic_body()
    idx = body.find("audit latency-8")
    section = body[idx:idx + 1500]
    for flag in ('"enable_timestamps"', '"latency_enabled"',
                 'protocol_selection'):
        assert flag in section, f"missing check for {flag!r}"


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 261)
