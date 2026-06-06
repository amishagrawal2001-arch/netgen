"""Tests for the v0.4.0 RFC 2544 smart binary search + reachability
pre-flight.

Operator-reported scenario (svl-d-ai-srv04, 400G Mellanox NIC):
  - Started RFC 2544 with DPDK unchecked + frame_size=64
  - Scapy could send only ~80k packets in 60 seconds (~1.3 kpps)
    despite the binary search asking for 297M pps (50% of 595M
    line rate at 64B)
  - RX received 0 (peer unreachable)
  - Naive search would have taken 13+ iterations × 60 sec to
    converge to "0 pps no-drop" — an hour of wasted test time

v0.4.0 fixes:
  1. _rfc2544_decide_step detects tx_pps_actual << trying_pps and
     uses the actually-achieved rate as the new ceiling — converges
     in 1-2 iterations on the right diagnosis
  2. _rfc2544_check_reachable runs a best-effort ICMP ping BEFORE
     starting; the /api/rfc2544/start handler returns HTTP 409 with
     a warning if the dest IP doesn't respond, so the operator can
     either fix the setup or override with confirm_unreachable=true

These tests pin the math + the helper's API contract."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from utils.rfc2544 import (
    _check_reachable as _rfc2544_check_reachable,
    _decide_step as _rfc2544_decide_step,
)


# ─────────────────────────────────── _rfc2544_decide_step ─────────────


def test_decide_normal_no_loss_advances_lo():
    """Standard binary search step on a clean iteration: loss ≤ target
    sets last_good + lo to trying_pps; hi unchanged."""
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=14_880_000, rx=14_880_000,
        trying_pps=14_880_000,
        lo_pps=0, hi_pps=595_000_000, last_good=0,
        target_loss_pct=0.0,
        duration_s=60,
    )
    assert lo == 14_880_000
    assert hi == 595_000_000  # unchanged
    assert last_good == 14_880_000
    assert diag is None


def test_decide_normal_loss_lowers_hi():
    """Standard binary search step on a lossy iteration where TX was
    able to GENERATE the requested rate (no rate-limit): set hi to
    trying_pps and keep lo + last_good."""
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=600_000_000, rx=590_000_000,   # tx matched ask; small loss
        trying_pps=600_000_000,
        lo_pps=400_000_000, hi_pps=800_000_000, last_good=400_000_000,
        target_loss_pct=0.0,
        duration_s=1,                      # tx_actual = 600M pps
    )
    assert lo == 400_000_000  # unchanged
    assert hi == 600_000_000  # lowered to trying
    assert last_good == 400_000_000  # unchanged
    assert diag is None


def test_decide_tx_rate_limited_uses_actual_as_ceiling():
    """The v0.4.0 fix: when TX could only achieve a small fraction
    of the asked rate (Scapy ceiling, peer unreachable, etc.),
    use the actual achieved rate as the new ceiling. Converges
    in 1-2 iterations instead of 13."""
    # Operator's exact case: asked 297M, achieved 80k over 60s = 1333 pps
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=80_000, rx=0,
        trying_pps=297_619_047,
        lo_pps=0, hi_pps=595_238_095, last_good=0,
        target_loss_pct=0.0,
        duration_s=60,
    )
    # tx_pps_actual = 80000/60 ≈ 1333. That's WAY below 10% of 297M.
    # New ceiling = achieved rate = 1333.
    assert hi == 1333, (
        f"smart search should set hi to actual achieved rate; got {hi}"
    )
    assert lo == 0
    assert last_good == 0
    assert diag == "tx_rate_limited"


def test_decide_tx_rate_limit_threshold_is_10_percent():
    """The detection trigger is tx_actual < 10% of trying_pps.
    9% should trigger; 11% should not. This keeps the fast-path
    tight enough to not false-fire on small overshoot."""
    # 9% of 1M = 90k achieved over 1 sec → 90k pps achieved
    lo9, hi9, _, diag9 = _rfc2544_decide_step(
        tx=90_000, rx=0, trying_pps=1_000_000,
        lo_pps=0, hi_pps=2_000_000, last_good=0,
        target_loss_pct=0.0, duration_s=1,
    )
    assert diag9 == "tx_rate_limited"

    # 15% of 1M = 150k pps achieved → above threshold, no fast path
    lo15, hi15, _, diag15 = _rfc2544_decide_step(
        tx=150_000, rx=0, trying_pps=1_000_000,
        lo_pps=0, hi_pps=2_000_000, last_good=0,
        target_loss_pct=0.0, duration_s=1,
    )
    assert diag15 is None
    assert hi15 == 1_000_000  # normal step lowered hi to trying


def test_decide_triggers_even_with_partial_rx():
    """TX-rate-limit + partial RX is still a rate-limit case — TX
    achieved 1.3 kpps of asked 297M. The search should halve from
    the achieved rate, not from the asked rate. That's the right
    place to look for the no-drop rate."""
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=80_000, rx=40_000,                  # 50% loss, partial RX
        trying_pps=297_619_047,
        lo_pps=0, hi_pps=595_238_095, last_good=0,
        target_loss_pct=0.0,
        duration_s=60,
    )
    # tx_pps_actual = 1333. Fast-path triggers; new hi = 1333.
    # Next iter would try (0+1333)/2 = 666 pps which IS within
    # Scapy's envelope — search converges fast on the real
    # no-drop ceiling.
    assert hi == 1333
    assert diag == "tx_rate_limited"


def test_decide_does_not_trigger_when_no_loss():
    """If loss ≤ target_loss_pct, we don't care about TX shortfall —
    the result is "this rate works", advance lo."""
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=50_000, rx=50_000,                  # 0% loss
        trying_pps=297_619_047,
        lo_pps=0, hi_pps=595_238_095, last_good=0,
        target_loss_pct=0.0,
        duration_s=60,
    )
    assert lo == 297_619_047
    assert last_good == 297_619_047
    assert diag is None


def test_decide_clamps_new_hi_to_existing_hi():
    """Defensive: tx_actual shouldn't exceed previous hi, but if
    it somehow does (clock skew, double-counting), don't EXPAND
    the search range. Pick tx_actual strictly < 10% threshold so
    the fast path triggers + the clamp is exercised."""
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=9_000_000, rx=0,                    # tx_actual = 9M pps
        trying_pps=100_000_000,                # asked 100M (9% < 10% threshold)
        lo_pps=0, hi_pps=5_000_000,            # previous hi was 5M
        last_good=0,
        target_loss_pct=0.0,
        duration_s=1,
    )
    # 9M would expand hi above 5M, but we clamp to previous hi
    assert hi == 5_000_000
    assert diag == "tx_rate_limited"


def test_decide_zero_duration_no_divide_by_zero():
    """Defensive: caller shouldn't pass duration_s=0 but if it does,
    don't crash. Result should fall through to normal step."""
    lo, hi, last_good, diag = _rfc2544_decide_step(
        tx=1000, rx=0, trying_pps=10_000,
        lo_pps=0, hi_pps=100_000, last_good=0,
        target_loss_pct=0.0, duration_s=0,
    )
    # tx_pps_actual = 0 (division-by-zero guard). 0 < 10% of 10000 →
    # would trigger fast path, set hi to max(int(0), 1) = 1.
    assert hi == 1
    assert diag == "tx_rate_limited"


# ─────────────────────────────────── _rfc2544_check_reachable ─────────


def test_reachable_when_ping_succeeds():
    """Successful ping (rc=0) → reachable=True, empty message."""
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake):
        ok, msg = _rfc2544_check_reachable("10.0.0.2", "eth0")
    assert ok is True
    assert msg == ""


def test_unreachable_when_ping_fails():
    """ping rc != 0 → reachable=False with a helpful message."""
    fake = MagicMock(returncode=1, stdout="", stderr="ping: Host unreachable")
    with patch("subprocess.run", return_value=fake):
        ok, msg = _rfc2544_check_reachable("10.0.0.2", "eth0")
    assert ok is False
    assert "10.0.0.2" in msg
    assert "eth0" in msg


def test_reachable_when_ping_missing():
    """ping binary not on PATH → don't block the test on
    infrastructure noise (some minimal containers strip ping)."""
    with patch("subprocess.run", side_effect=FileNotFoundError("ping")):
        ok, msg = _rfc2544_check_reachable("10.0.0.2", "eth0")
    assert ok is True
    assert "FileNotFoundError" in msg or "ping skipped" in msg


def test_reachable_with_empty_dst_returns_true():
    """No ip_dst → nothing to probe → don't block."""
    ok, msg = _rfc2544_check_reachable("", "eth0")
    assert ok is True


def test_ping_command_uses_interface_binding():
    """When tx_iface is set, ping must use -I to bind to that
    interface, otherwise the probe goes via the default route
    (which may not even be the NIC under test)."""
    captured = []
    fake = MagicMock(returncode=0, stdout="", stderr="")
    def _capture(cmd, *args, **kwargs):
        captured.append(cmd)
        return fake
    with patch("subprocess.run", side_effect=_capture):
        _rfc2544_check_reachable("10.0.0.2", "enp181s0f0np0")
    assert captured, "subprocess.run was not called"
    cmd = captured[0]
    assert "-I" in cmd
    iface_idx = cmd.index("-I") + 1
    assert cmd[iface_idx] == "enp181s0f0np0"
    # And the dest IP is the last arg
    assert cmd[-1] == "10.0.0.2"


def test_ping_works_without_interface_binding():
    """When tx_iface is empty, fall back to default-route probe."""
    captured = []
    fake = MagicMock(returncode=0, stdout="", stderr="")
    def _capture(cmd, *args, **kwargs):
        captured.append(cmd)
        return fake
    with patch("subprocess.run", side_effect=_capture):
        _rfc2544_check_reachable("10.0.0.2", "")
    assert captured
    assert "-I" not in captured[0]
    assert captured[0][-1] == "10.0.0.2"
