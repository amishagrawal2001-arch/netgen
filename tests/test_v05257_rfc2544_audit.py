"""v0.5.257 — RFC 2544 throughput audit: 7 correctness fixes.

Also folds in latency-1 (RFC 2544 calling non-existent
`LatencySampler.snapshot()` instead of `.stats()`).
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SERVER = (REPO / "run_tgen_server.py").read_text()


def _step_body():
    idx = SERVER.find("def _rfc2544_run_step(")
    end = SERVER.find("\ndef _rfc2544_thread(", idx + 1)
    return SERVER[idx:end if end > 0 else idx + 8000]


def _thread_body():
    idx = SERVER.find("def _rfc2544_thread(")
    end = SERVER.find("\n@app.route", idx + 1)
    return SERVER[idx:end if end > 0 else idx + 8000]


def _start_endpoint_body():
    idx = SERVER.find("def rfc2544_start():")
    end = SERVER.find("\n@app.route", idx + 1)
    return SERVER[idx:end if end > 0 else idx + 6000]


# --- RFC-2: resolution_pps clamped per frame size ------------------


def test_resolution_pps_clamped_to_one_percent_of_line_rate():
    body = _step_body()
    assert "audit RFC-2" in body
    assert "min(int(resolution_pps), max(1, hi_pps // 100))" in body


def test_resolution_clamp_logs_when_it_fires():
    body = _step_body()
    assert "clamped to" in body


# --- RFC-3: first iteration probes hi_pps --------------------------


def test_first_iteration_probes_full_line_rate():
    body = _step_body()
    assert "audit RFC-3" in body
    assert "first_probe = True" in body
    assert "if first_probe:" in body
    assert "trying_pps = hi_pps" in body


def test_bisect_still_runs_from_second_iteration():
    """After the first probe, subsequent iterations still bisect."""
    body = _step_body()
    # Both trying_pps formulations coexist — the first-probe branch
    # AND the bisect branch.
    assert "trying_pps = (lo_pps + hi_pps) // 2" in body


# --- RFC-1 + RFC-4: link_speed validation --------------------------


def test_link_speed_validates_positive_from_sysfs():
    body = _thread_body()
    assert "audit RFC-1" in body
    # No more silent fallback to 100 Gbps — check as a LIVE code
    # statement, not the comment prose. Only accept if the string
    # appears as an assignment on a line with 8-16 chars leading
    # whitespace (typical function-body indent) and no # before it.
    live_fallback = [
        line for line in body.splitlines()
        if re.match(r"^\s{8,16}link_mbps\s*=\s*100000\b", line)
    ]
    assert live_fallback == [], (
        f"live fallback assignment still present: {live_fallback!r}"
    )
    assert "if _v > 0:" in body


def test_link_speed_missing_raises_with_actionable_error():
    body = _thread_body()
    assert 'raise RuntimeError(' in body
    assert "link_speed_mbps" in body


# --- RFC-8: tx_rate_limited diagnosis cleared when rate found ------


def test_tx_rate_limited_cleared_when_last_good_positive():
    body = _step_body()
    assert "audit RFC-8" in body
    assert 'diagnosis == "tx_rate_limited" and last_good > 0' in body
    assert "diagnosis = None" in body


# --- RFC-7: empty frame_sizes rejected -----------------------------


def test_empty_frame_sizes_returns_400():
    body = _start_endpoint_body()
    assert "audit RFC-7" in body
    assert "frame_sizes must be a non-empty list" in body


# --- RFC-10: reachability skip only on real loopback ---------------


def test_reachability_guard_uses_explicit_same_iface_check():
    body = _start_endpoint_body()
    assert "audit RFC-10" in body
    assert "_same_iface_loopback" in body
    # Old broken guard is gone as a LIVE if-clause — comment prose
    # may still mention it (for context). Filter to real code
    # lines that could actually trip the old logic.
    live_broken = [
        line for line in body.splitlines()
        if 'data.get("rx_iface") or data.get("tx_iface")' in line
        and line.lstrip().startswith(("and ", "if "))
    ]
    assert live_broken == [], (
        f"old broken guard still live: {live_broken!r}"
    )


# --- latency-1: LatencySampler.stats() (not .snapshot()) ----------


def test_rfc2544_calls_latency_stats_not_snapshot():
    body = _thread_body()
    assert "audit latency-1" in body
    assert "latency = s.stats()" in body
    # Broken call is gone.
    assert "latency = s.snapshot()" not in body


# --- Metadata -------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 257)
