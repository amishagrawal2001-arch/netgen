"""v0.5.137: stream-stats Loss % uses rates when running.

Operator screenshot: UEC stream showed Loss = 99.39% despite
tx_rate = 23.71 Mpps and rx_rate = 20.57 Mpps. Real
instantaneous loss = (23.71 − 20.57) / 23.71 = 13.2%.

The 99.39% came from cumulative counts:
  (TX 3.2B − RX 19.5M) / TX 3.2B = 99.39%

That's arithmetically correct, but meaningless: TX had been
counting for ~135s while RX had been counting for ~0.95s. The
rx_worker was re-spawned (lcore reallocation post-v0.5.131/132/134)
and started its counter fresh while tx_worker kept its history.

v0.5.137 prefers rate-based loss when the stream is running and
both rates are observable. Falls back to cumulative when the
stream is stopped (rates are 0 but cumulative still summarizes
the session).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _compute_loss(tx_count, rx_count, tx_rate, rx_rate, flow_tracking=True):
    """Mirror the v0.5.137 loss-pct branch inline so the test
    pins the contract without dragging in Qt."""
    if isinstance(tx_count, int) and tx_count > 0:
        if tx_rate and tx_rate > 0 and rx_rate is not None:
            _diff = tx_rate - rx_rate
            return max(0.0, (_diff / tx_rate) * 100.0)
        elif isinstance(rx_count, int):
            return ((tx_count - rx_count) / tx_count * 100)
        else:
            return 100.0 if flow_tracking else None
    return None


# ----- the reported bug --------------------------------------------------

def test_srv06_uec_loss_uses_rate_not_cumulative():
    """The operator screenshot scenario: cumulative counts are
    badly out of sync because rx_worker re-spawned. Rate-based
    loss reflects the real ~13% loss, not 99.39%."""
    loss = _compute_loss(
        tx_count=3_218_807_168, rx_count=19_481_462,
        tx_rate=23_710_000, rx_rate=20_570_000,
    )
    assert 12.0 < loss < 14.0, (
        f"Real loss for tx=23.71M / rx=20.57M is ~13.2%. "
        f"Got: {loss:.2f}% — looks like the cumulative-count "
        f"path is still being used."
    )


def test_zero_loss_when_rates_equal():
    """Steady state, all packets accounted for. The cumulative
    counters being out of sync (e.g., rx counter started later)
    must NOT inflate this to a fake non-zero loss."""
    loss = _compute_loss(
        tx_count=1_000_000_000, rx_count=10_000_000,  # cumul gap
        tx_rate=20_000_000, rx_rate=20_000_000,
    )
    assert loss == 0.0


def test_100pct_loss_when_rx_rate_zero():
    """TX is firing, RX is dropping everything (e.g., link down
    mid-stream). Clean 100% reading."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=0,
        tx_rate=20_000_000, rx_rate=0,
    )
    assert loss == 100.0


def test_negative_loss_clamped_to_zero():
    """Sampling-window phase offset: occasionally rx_rate >
    tx_rate by a few percent (rx_worker polled a fuller window).
    Don't flash a confusing negative loss — clamp to 0."""
    loss = _compute_loss(
        tx_count=1_000_000_000, rx_count=999_990_000,
        tx_rate=20_000_000, rx_rate=20_010_000,  # rx > tx jitter
    )
    assert loss == 0.0


# ----- stopped / zero-rate fallback ---------------------------------------

def test_stopped_stream_falls_back_to_cumulative():
    """When the stream is stopped (both rates are 0), the
    cumulative count loss IS the right number — that's the
    session summary the operator wants on the "Status: Stopped"
    row."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=900_000,
        tx_rate=0, rx_rate=0,
    )
    assert abs(loss - 10.0) < 0.01


def test_tx_rate_zero_rx_rate_nonzero_falls_back():
    """Defensive: tx_rate=0 but rx_rate>0 (rare, but possible if
    we sampled mid-stop). Don't divide by zero — fall back to
    cumulative."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=900_000,
        tx_rate=0, rx_rate=100,
    )
    assert abs(loss - 10.0) < 0.01


def test_tx_count_zero_returns_none():
    """No basis for a loss measurement: TX hasn't fired any
    packets yet. Surface None so the renderer shows the muted
    "—" instead of false-positive green."""
    loss = _compute_loss(
        tx_count=0, rx_count=0,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


def test_rx_rate_none_falls_through():
    """When the server didn't report rx_rate at all (older API),
    fall back to cumulative."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=900_000,
        tx_rate=20_000_000, rx_rate=None,
    )
    assert abs(loss - 10.0) < 0.01


# ----- realistic cases ---------------------------------------------------

def test_small_loss():
    """Wire loss of about 2%."""
    loss = _compute_loss(
        tx_count=1_000_000_000, rx_count=980_000_000,
        tx_rate=20_000_000, rx_rate=19_600_000,
    )
    assert abs(loss - 2.0) < 0.01


def test_half_packets_lost():
    """50% wire loss (e.g., congested switch)."""
    loss = _compute_loss(
        tx_count=2_000_000, rx_count=1_000_000,
        tx_rate=20_000_000, rx_rate=10_000_000,
    )
    assert abs(loss - 50.0) < 0.01


def test_flow_tracking_off_no_rx_rate_returns_none():
    """When flow_tracking is off the server reports rx_count
    as None / not-int — and rx_rate is also None. With v0.5.137,
    rx_rate=None falls through to the cumulative branch, which
    sees rx_count is not int → returns None (flow_tracking off)."""
    loss = _compute_loss(
        tx_count=1_000_000, rx_count=None,
        tx_rate=20_000_000, rx_rate=None,
        flow_tracking=False,
    )
    assert loss is None
