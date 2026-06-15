"""v0.5.140: stream-stats Loss % latches the final value on stop.

Operator referenced Spirent's behavior. In Spirent panels, when
a stream stops the Loss % column FREEZES on the last observed
value rather than blanking. Operator wants the same: see how
the test ended without racing to read it before the cell wipes.

Behavior contract (Spirent-like):
- Running with rates: rate-based loss (unchanged from v0.5.137)
- Stopped after running: show the last rate-based loss observed
- Never ran (warmup): None / "—"
- Restart (counter reset): drop the latch, recompute on next sample
- Clear Stats: purge latch alongside baselines

The latch is keyed by stream_id and lives in
`self._latched_loss_pct`.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class _MockSection:
    """Standalone re-implementation of the v0.5.140 loss-latch
    logic. Mirrors the production code in
    `statistics_section.py:1907+`. Tests pin the contract here so
    we don't need Qt at import time."""

    def __init__(self):
        self._latched_loss_pct: dict[str, float] = {}
        self._stream_baselines: dict[str, dict] = {}

    def compute_loss(
        self, stream_id, raw_tx, tx_count, rx_count,
        tx_rate, rx_rate,
    ):
        latched = self._latched_loss_pct
        prev_tx = self._stream_baselines.setdefault(
            stream_id, {}).get("_last_tx_for_latch")
        if (prev_tx is not None and isinstance(raw_tx, int)
                and raw_tx < prev_tx):
            latched.pop(stream_id, None)
        self._stream_baselines.setdefault(stream_id, {})[
            "_last_tx_for_latch"] = raw_tx

        if (isinstance(tx_count, int) and tx_count > 0
                and tx_rate and tx_rate > 0
                and rx_rate is not None):
            diff = tx_rate - rx_rate
            loss_pct = max(0.0, (diff / tx_rate) * 100.0)
            latched[stream_id] = loss_pct
            return loss_pct
        if stream_id in latched:
            return latched[stream_id]
        return None


# ----- the Spirent contract ----------------------------------------------

def test_running_computes_and_caches_loss():
    """While the stream is running, loss is rate-based AND the
    latch dict captures the value for later replay."""
    sec = _MockSection()
    loss = sec.compute_loss(
        stream_id="uec", raw_tx=1_000_000_000,
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=23_710_000, rx_rate=20_570_000,
    )
    assert 12.0 < loss < 14.0  # ~13.2%
    assert sec._latched_loss_pct["uec"] == loss


def test_stop_latches_final_value():
    """Spirent contract: after the stream stops (rates=0), the
    Loss % cell shows the LAST observed rate-based loss instead
    of dropping to None. Operator can read the final test result
    without racing."""
    sec = _MockSection()
    # First poll: running with 13.2% loss → latched
    sec.compute_loss(
        stream_id="uec", raw_tx=1_000_000_000,
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=23_710_000, rx_rate=20_570_000,
    )
    # Second poll: stream stopped, rates went to 0
    loss = sec.compute_loss(
        stream_id="uec", raw_tx=1_000_000_000,
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=0, rx_rate=0,
    )
    # Latched value still surfaces
    assert 12.0 < loss < 14.0


def test_never_ran_returns_none():
    """Stream that never had a running sample (e.g., warmup window
    or stream that failed to start): None, not a stale latch from
    another stream."""
    sec = _MockSection()
    loss = sec.compute_loss(
        stream_id="never_ran", raw_tx=500,
        tx_count=500, rx_count=0,
        tx_rate=0, rx_rate=0,
    )
    assert loss is None


def test_restart_drops_latch_and_recomputes():
    """When tx_count drops below the previous sample (counter
    reset = stream restart), the latch is dropped. The next
    running sample populates a fresh value."""
    sec = _MockSection()
    # Session 1: 13.2% loss latched
    sec.compute_loss(
        stream_id="uec", raw_tx=1_000_000_000,
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=23_710_000, rx_rate=20_570_000,
    )
    # Stream stopped (latch holds 13.2%)
    loss_stopped = sec.compute_loss(
        stream_id="uec", raw_tx=1_000_000_000,
        tx_count=1_000_000_000, rx_count=900_000_000,
        tx_rate=0, rx_rate=0,
    )
    assert 12.0 < loss_stopped < 14.0

    # Operator restarts. Counter resets to lower value.
    # No new rates yet — latch should drop, and since no new
    # running sample, the cell goes None.
    loss_restart_no_rates = sec.compute_loss(
        stream_id="uec", raw_tx=100,  # < 1_000_000_000 → reset
        tx_count=100, rx_count=0,
        tx_rate=0, rx_rate=0,
    )
    assert loss_restart_no_rates is None

    # First poll into the new session — fresh loss computed
    loss_new_session = sec.compute_loss(
        stream_id="uec", raw_tx=10_000,
        tx_count=10_000, rx_count=8_000,
        tx_rate=10_000_000, rx_rate=5_000_000,
    )
    assert abs(loss_new_session - 50.0) < 0.01


def test_different_streams_have_independent_latches():
    """Two streams running concurrently: each latches its own
    loss. One stopping doesn't affect the other."""
    sec = _MockSection()
    sec.compute_loss(
        stream_id="udp", raw_tx=1_000_000,
        tx_count=1_000_000, rx_count=950_000,
        tx_rate=20_000_000, rx_rate=19_000_000,  # 5% loss
    )
    sec.compute_loss(
        stream_id="uec", raw_tx=2_000_000,
        tx_count=2_000_000, rx_count=1_500_000,
        tx_rate=23_710_000, rx_rate=11_855_000,  # 50% loss
    )
    # UDP stops
    udp_stopped = sec.compute_loss(
        stream_id="udp", raw_tx=1_000_000,
        tx_count=1_000_000, rx_count=950_000,
        tx_rate=0, rx_rate=0,
    )
    # UEC still running
    uec_running = sec.compute_loss(
        stream_id="uec", raw_tx=2_500_000,
        tx_count=2_500_000, rx_count=2_000_000,
        tx_rate=23_710_000, rx_rate=11_855_000,
    )
    assert abs(udp_stopped - 5.0) < 0.01
    assert abs(uec_running - 50.0) < 0.01


def test_rate_jitter_latches_new_value():
    """Running stream: the latch tracks the MOST RECENT
    rate-based loss. So mid-test jitter (loss spiking) latches
    that spike. After stop, that's what the operator sees."""
    sec = _MockSection()
    # 5% steady loss
    sec.compute_loss(
        stream_id="uec", raw_tx=1_000_000,
        tx_count=1_000_000, rx_count=950_000,
        tx_rate=20_000_000, rx_rate=19_000_000,
    )
    # Loss spikes to 50% just before stop
    sec.compute_loss(
        stream_id="uec", raw_tx=2_000_000,
        tx_count=2_000_000, rx_count=1_500_000,
        tx_rate=20_000_000, rx_rate=10_000_000,
    )
    # Stop
    final = sec.compute_loss(
        stream_id="uec", raw_tx=2_000_000,
        tx_count=2_000_000, rx_count=1_500_000,
        tx_rate=0, rx_rate=0,
    )
    # Latch reflects the most recent spike
    assert abs(final - 50.0) < 0.01


def test_screenshot_running_uec():
    """Sanity: the srv06 UEC scenario from earlier screenshots
    still computes ~13.2% when running."""
    sec = _MockSection()
    loss = sec.compute_loss(
        stream_id="uec", raw_tx=3_218_807_168,
        tx_count=3_218_807_168, rx_count=19_481_462,
        tx_rate=23_710_000, rx_rate=20_570_000,
    )
    assert 12.0 < loss < 14.0


def test_zero_loss_latches_zero():
    """A perfect run (0% loss) still latches 0% on stop —
    operator sees the test ended cleanly."""
    sec = _MockSection()
    sec.compute_loss(
        stream_id="udp", raw_tx=1_000_000,
        tx_count=1_000_000, rx_count=1_000_000,
        tx_rate=20_000_000, rx_rate=20_000_000,
    )
    stopped = sec.compute_loss(
        stream_id="udp", raw_tx=1_000_000,
        tx_count=1_000_000, rx_count=1_000_000,
        tx_rate=0, rx_rate=0,
    )
    assert stopped == 0.0


def test_clear_stats_purges_latch_dict():
    """Operator clicks Clear Stats → _latched_loss_pct should be
    in the purge list so stale latches don't bleed across runs.
    Verify the production source includes _latched_loss_pct in the
    Clear Stats clearing loop."""
    src = (REPO / "traffic_client" / "statistics_section.py").read_text()
    # The clearing loop iterates over a tuple of cache attr names.
    # _latched_loss_pct must appear inside that tuple.
    assert "_latched_loss_pct" in src
    # And specifically inside the Clear Stats purge loop —
    # check it appears in the same multi-line tuple as _stream_baselines.
    assert '"_stream_baselines", "_latched_loss_pct"' in src
