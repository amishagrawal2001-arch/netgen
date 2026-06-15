"""v0.5.142: stream-stats render handles cold start (no _stream_baselines).

Operator hit:

    AttributeError: 'TrafficGeneratorClient' object has no attribute
                    '_stream_baselines'
    at statistics_section.py:1989

The v0.5.140 loss-latch code directly accessed
`self._stream_baselines.setdefault(...)`. That dict is created
by the Clear Stats handler in `main.py`, NOT by the section's
constructor. On a cold-start client (operator opens GUI for the
first time and the poll fires before Clear Stats), the attribute
doesn't exist → AttributeError → GUI aborts.

v0.5.142 makes the latch's counter-reset bookkeeping lazily init
the dict if it's missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SRC = (REPO / "traffic_client" / "statistics_section.py").read_text()


# ----- the production fix exists -----------------------------------------

def test_latch_block_uses_lazy_baselines():
    """The latch's counter-reset bookkeeping must NOT directly
    access `self._stream_baselines.setdefault(...)` — that's what
    crashed the cold-start client. It must use getattr-with-init
    instead."""
    # The old line that crashed:
    direct = re.search(
        r'_prev_tx = self\._stream_baselines\.setdefault\(',
        SRC,
    )
    assert direct is None, (
        "Direct `self._stream_baselines.setdefault(...)` access "
        "still in the latch block — cold-start clients will crash. "
        "Use a getattr-then-init pattern."
    )


def test_lazy_init_is_present():
    """The v0.5.142 fix: getattr(self, '_stream_baselines', None)
    + create if missing."""
    assert (
        'getattr(self, "_stream_baselines", None)' in SRC
        or "getattr(self, '_stream_baselines', None)" in SRC
    ), "v0.5.142 lazy-init not found"


# ----- behavioral test on an inline replica ------------------------------

class _Section:
    """Minimal stand-in for the StatisticsSection class. Holds only
    the attrs the latch block reads/writes so we can exercise the
    cold-start path without dragging Qt into the test."""

    def __init__(self):
        # NOTE: deliberately NO _stream_baselines and NO
        # _latched_loss_pct — exactly the cold-start state.
        pass

    def latch_block(self, stream_id, raw_tx, tx_count, tx_rate, rx_rate):
        """Mirror the production block in statistics_section.py
        update_stream_statistics_table around line 1983."""
        _latched = getattr(self, "_latched_loss_pct", None)
        if _latched is None:
            self._latched_loss_pct = {}
            _latched = self._latched_loss_pct

        _baselines = getattr(self, "_stream_baselines", None)
        if not isinstance(_baselines, dict):
            self._stream_baselines = {}
            _baselines = self._stream_baselines

        _prev_tx = _baselines.setdefault(
            stream_id, {}).get("_last_tx_for_latch")
        if (stream_id and _prev_tx is not None
                and isinstance(raw_tx, int) and raw_tx < _prev_tx):
            _latched.pop(stream_id, None)
        if stream_id:
            _baselines.setdefault(stream_id, {})[
                "_last_tx_for_latch"] = raw_tx

        if (isinstance(tx_count, int) and tx_count > 0
                and tx_rate and tx_rate > 0
                and rx_rate is not None):
            diff = tx_rate - rx_rate
            loss = max(0.0, (diff / tx_rate) * 100.0)
            _latched[stream_id] = loss
            return loss
        if stream_id in _latched:
            return _latched[stream_id]
        return None


def test_cold_start_first_poll_no_crash():
    """The exact operator scenario: cold-start client, first poll
    fires update_stream_statistics_table → must NOT raise
    AttributeError on _stream_baselines."""
    sec = _Section()  # bare — neither dict pre-created
    # Should not raise.
    loss = sec.latch_block(
        stream_id="uec",
        raw_tx=1_000_000,
        tx_count=1_000_000,
        tx_rate=20_000_000,
        rx_rate=18_000_000,
    )
    # 10% loss
    assert abs(loss - 10.0) < 0.01
    # Both dicts now exist after the first call.
    assert isinstance(sec._stream_baselines, dict)
    assert isinstance(sec._latched_loss_pct, dict)


def test_cold_start_then_subsequent_polls():
    """After the first poll lazily creates the dicts, subsequent
    polls reuse them — no re-creation."""
    sec = _Section()
    sec.latch_block(
        stream_id="uec", raw_tx=1_000_000,
        tx_count=1_000_000, tx_rate=20_000_000, rx_rate=18_000_000,
    )
    first_baselines = sec._stream_baselines
    sec.latch_block(
        stream_id="uec", raw_tx=2_000_000,
        tx_count=2_000_000, tx_rate=20_000_000, rx_rate=18_000_000,
    )
    assert sec._stream_baselines is first_baselines


def test_cold_start_then_latch_on_stop():
    """The Spirent latch behavior still works on a cold-start
    client: first poll captures running loss, second poll
    (stopped, rates=0) replays the latch."""
    sec = _Section()
    sec.latch_block(
        stream_id="uec", raw_tx=1_000_000,
        tx_count=1_000_000, tx_rate=20_000_000, rx_rate=18_000_000,
    )
    stopped = sec.latch_block(
        stream_id="uec", raw_tx=1_000_000,
        tx_count=1_000_000, tx_rate=0, rx_rate=0,
    )
    assert abs(stopped - 10.0) < 0.01


def test_non_dict_baseline_attribute_handled():
    """Defensive: if some test/scaffold sets `_stream_baselines`
    to a non-dict (None, list, etc.), the lazy init must
    overwrite it rather than crash."""
    sec = _Section()
    sec._stream_baselines = None  # weird but legal — let's not crash
    loss = sec.latch_block(
        stream_id="uec", raw_tx=1_000_000,
        tx_count=1_000_000, tx_rate=20_000_000, rx_rate=18_000_000,
    )
    assert abs(loss - 10.0) < 0.01
    assert isinstance(sec._stream_baselines, dict)
