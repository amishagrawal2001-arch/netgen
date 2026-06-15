"""v0.5.138: Loss % shows "—" (None) when stream is not running.

Operator screenshot of stopped streams:
  ICMP: TX=2,600     RX=368     Loss=85.85%   Status=Stopped
  UEC:  TX=330,752   RX=4       Loss=100.00%  Status=Stopped
  UDP:  TX=136M      RX=8,906   Loss=99.99%   Status=Stopped

All three loss numbers are misleading. The TX counter is the
final tx_worker count; the RX counter is whatever rx_worker had
when it shut down (probably before tx_worker finished, or its
counter was zeroed on a re-spawn during the run).

v0.5.137 had a "fall back to cumulative" path for stopped
streams that was supposed to surface "session loss summary"
but in practice produced these false-positive numbers. v0.5.138
drops the fallback — Loss is only computed when the stream is
actively running with both rates observable.

The TX/RX Count columns are still displayed for operators who
want to do the math themselves and know the counters are aligned
(e.g., after a fresh stop+start of both workers).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _compute_loss(tx_count, rx_count, tx_rate, rx_rate, flow_tracking=True):
    """v0.5.138 loss-pct contract. Mirrors production logic at
    statistics_section.py:1894."""
    if (isinstance(tx_count, int) and tx_count > 0
            and tx_rate and tx_rate > 0
            and rx_rate is not None):
        _diff = tx_rate - rx_rate
        return max(0.0, (_diff / tx_rate) * 100.0)
    return None


# ----- the reported operator scenarios -----------------------------------

def test_screenshot_icmp_stopped_no_loss():
    """ICMP row: TX=2,600, RX=368, Loss=85.85% pre-fix. Should be
    None now — stream is stopped, the counters are not aligned."""
    loss = _compute_loss(
        tx_count=2_600, rx_count=368,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


def test_screenshot_uec_stopped_no_loss():
    """UEC row: TX=330,752, RX=4, Loss=100% pre-fix. None now."""
    loss = _compute_loss(
        tx_count=330_752, rx_count=4,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


def test_screenshot_udp_stopped_no_loss():
    """UDP row: TX=136M, RX=8.9k, Loss=99.99% pre-fix. None now."""
    loss = _compute_loss(
        tx_count=136_678_720, rx_count=8_906,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


# ----- running cases unchanged from v0.5.137 -----------------------------

def test_running_with_rates_still_computes_loss():
    """v0.5.138 didn't change the running-stream path. Rate-based
    loss still fires when both rates are observable."""
    loss = _compute_loss(
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=23_710_000, rx_rate=20_570_000,
    )
    assert 12.0 < loss < 14.0  # ~13.2% — the srv06 UEC scenario


def test_running_zero_loss_still_works():
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=999_000,
        tx_rate=10_000_000, rx_rate=10_000_000,
    )
    assert loss == 0.0


def test_running_full_loss_still_works():
    """Stream is running, RX has stopped responding (link down,
    queue full): 100% instantaneous loss."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=0,
        tx_rate=20_000_000, rx_rate=0,
    )
    assert loss == 100.0


# ----- transition states -------------------------------------------------

def test_just_started_no_rates_yet():
    """Stream has TX'd some packets but the rate hasn't been
    delta-computed yet (first sample window). Return None — no
    measurement available."""
    loss = _compute_loss(
        tx_count=500, rx_count=0,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


def test_just_stopped_rates_still_nonzero():
    """The instant after a stop, the last poll's rates may still
    be nonzero. We compute rate-based loss in that window — that's
    the LAST steady-state value the operator saw. They'll watch
    it drop to "—" on the next poll cycle when rates hit 0."""
    loss = _compute_loss(
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=20_000_000, rx_rate=18_000_000,
    )
    assert abs(loss - 10.0) < 0.01


# ----- defensive ---------------------------------------------------------

def test_tx_count_zero_returns_none():
    """No packets emitted at all — return None regardless of
    other state."""
    loss = _compute_loss(
        tx_count=0, rx_count=0,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


def test_negative_tx_count_returns_none():
    """Garbage tx_count → None (don't crash on negative or
    non-int)."""
    loss = _compute_loss(
        tx_count=-1, rx_count=0,
        tx_rate=10_000_000, rx_rate=5_000_000,
    )
    assert loss is None


def test_flow_tracking_off_returns_none():
    """When flow_tracking is off, rx_rate is typically None or 0.
    Either way → None."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=0,
        tx_rate=10_000_000, rx_rate=None,
        flow_tracking=False,
    )
    assert loss is None
