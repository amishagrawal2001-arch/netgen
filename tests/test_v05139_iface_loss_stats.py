"""v0.5.139: Interface stats panel gains Packets Lost + Loss % rows.

Per the operator's request: the stream-stats Loss % column went
None-on-stopped (v0.5.138). They still want the loss number
visible after stop — but in the Interface stats panel instead,
where it makes sense as a cumulative session summary.

The new rows aggregate per stream into the iface's totals:
- `tx_for_loss`: Σ tx_count of streams associated with this iface
- `rx_for_loss`: Σ rx_count of the same streams (flow_tracking on)
- Lost = `tx_for_loss − rx_for_loss`
- Loss % = `lost / tx_for_loss × 100`

Both numbers are cumulative so they persist after stream stop
until Clear Stats is clicked. Baseline-subtracted so Clear Stats
resets cleanly.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "traffic_client" / "statistics_section.py").read_text()


# ----- table layout has the new rows -------------------------------------

def test_packets_lost_row_in_table_headers():
    """Both row-label literals (init at line ~449 and rebuild at
    line ~1621) must include the new rows."""
    matches = re.findall(
        r'"Status",\s*"Sent Frames",\s*"Received Frames",.*?"Errors",\s*"Packets Lost",\s*"Loss %"',
        SRC, flags=re.DOTALL,
    )
    assert len(matches) >= 2, (
        f"Expected 'Packets Lost' and 'Loss %' in both row-label "
        f"lists. Found {len(matches)} matches."
    )


def test_row_count_bumped_to_twelve():
    """setRowCount(10) → setRowCount(12) for the 2 new rows."""
    assert "setRowCount(12)" in SRC, (
        "iface stats table must size to 12 rows after v0.5.139"
    )


# ----- aggregation initializes the new fields ----------------------------

def test_merged_statistics_initializes_loss_counters():
    """merged_statistics[iface] must include tx_for_loss and
    rx_for_loss initialized to 0."""
    assert '"tx_for_loss": 0' in SRC
    assert '"rx_for_loss": 0' in SRC


def test_tx_iface_block_aggregates_loss():
    """When processing a stream's TX side, both tx_for_loss and
    rx_for_loss must be added to merged_statistics[tx_iface]."""
    # Find the TX aggregation block (line ~1141)
    tx_block = re.search(
        r'# TX aggregation.*?# v0\.4\.6: DO NOT add',
        SRC, flags=re.DOTALL,
    )
    assert tx_block is not None, "couldn't locate TX aggregation block"
    body = tx_block.group(0)
    assert 'merged_statistics[tx_iface]["tx_for_loss"]' in body
    assert 'merged_statistics[tx_iface]["rx_for_loss"]' in body


def test_rx_iface_block_aggregates_loss_too():
    """RX iface column should show the SAME loss number as the TX
    iface column (back-to-back pair perspective). Both lines must
    exist somewhere in the source, and the v0.5.139 marker comment
    must accompany them."""
    assert 'merged_statistics[rx_iface]["tx_for_loss"] += tx' in SRC, (
        "RX-iface block must add the stream's tx_count to the iface's "
        "tx_for_loss tally"
    )
    assert 'merged_statistics[rx_iface]["rx_for_loss"] += rx' in SRC, (
        "RX-iface block must add the stream's rx_count to the iface's "
        "rx_for_loss tally"
    )
    assert "v0.5.139: same loss totals on the RX-iface column" in SRC


# ----- the render math --------------------------------------------------

def _render_loss(tx_for_loss, rx_for_loss):
    """Mirror the renderer's row-10 / row-11 logic."""
    adj_tx = max(0, int(tx_for_loss))
    adj_rx = max(0, int(rx_for_loss))
    lost = max(0, adj_tx - adj_rx)
    if adj_tx > 0:
        loss_pct = lost / adj_tx * 100.0
        return lost, loss_pct
    return 0, None


def test_render_zero_traffic_returns_none_loss():
    """Iface with no TX activity (e.g., a pure-RX iface that's only
    a peer): Loss % is None ("—" in the cell), lost = 0."""
    lost, loss = _render_loss(tx_for_loss=0, rx_for_loss=0)
    assert lost == 0
    assert loss is None


def test_render_no_loss_when_rx_matches_tx():
    """Perfect run: 1M TX, 1M RX → lost=0, Loss=0%."""
    lost, loss = _render_loss(tx_for_loss=1_000_000, rx_for_loss=1_000_000)
    assert lost == 0
    assert loss == 0.0


def test_render_partial_loss():
    """100k packets lost out of 1M: 10% loss."""
    lost, loss = _render_loss(tx_for_loss=1_000_000, rx_for_loss=900_000)
    assert lost == 100_000
    assert abs(loss - 10.0) < 0.01


def test_render_full_loss():
    """All packets lost (e.g., link broken mid-run)."""
    lost, loss = _render_loss(tx_for_loss=1_000_000, rx_for_loss=0)
    assert lost == 1_000_000
    assert loss == 100.0


def test_render_rx_greater_than_tx_clamps_to_zero():
    """RX > TX (counter sync glitch, or RX iface in another role):
    clamp lost to 0 instead of showing a negative count."""
    lost, loss = _render_loss(tx_for_loss=1_000_000, rx_for_loss=1_010_000)
    assert lost == 0
    assert loss == 0.0


def test_render_after_stream_stop_persists():
    """The whole point: tx_for_loss and rx_for_loss are cumulative
    counts. They persist after stream stop. The renderer reads them
    as-is and still produces a meaningful Loss %."""
    # Stream stopped; final counts captured
    lost, loss = _render_loss(tx_for_loss=10_000_000, rx_for_loss=9_500_000)
    assert lost == 500_000
    assert abs(loss - 5.0) < 0.01
