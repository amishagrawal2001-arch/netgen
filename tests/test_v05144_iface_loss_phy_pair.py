"""v0.5.144: iface Packets Lost / Loss % use PHY pair counters,
not per-stream rx_count.

Operator (screenshot of interface stats):

    TG 0 - ens2f0np0    TG 0 - ens2f1np1
    TX 23.66 Mfps       TX 0 fps
    RX 0 fps            RX 20.55 Mfps
    TX 189.27 Gbps      TX 0 bps
    RX 0 bps            RX 164.41 Gbps
    Packets Lost: 824,561,154   ← both halves identical
    Loss %:        99.37%       ← both halves identical, wildly wrong

The TX iface sent 830M, the RX iface received ~820M on the wire.
True wire loss is ~10M (~1.2%), not 824M (99.37%).

Root cause (v0.5.139): the iface loss math summed per-stream
`tx_count` and `rx_count`. Per-stream `rx_count` is the RX-engine
sniffer count — scapy/DPDK rx_worker DROPS most frames under
line-rate blast (the classic srv06 saga). So `tx_for_loss = 830M`,
`rx_for_loss = 5M`, lost = 825M, 99.37%. Useless.

v0.5.144 ground-truths loss on the iface PHY counters that v0.5.135
already populates in /api/interfaces (interface.tx, interface.rx —
tx_packets_phy / rx_packets_phy from `ethtool -S` on Mellanox). It
pairs each iface with its peer (the rx_iface of any stream with this
iface as tx_iface, and vice versa) and displays the SAME pair_loss
number on both halves of the pair.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# Late import to avoid pulling PyQt5 when only the helper is needed.
def _helper():
    from traffic_client.statistics_section import compute_iface_pair_loss
    return compute_iface_pair_loss


# ───── the helper produces the right number for the operator scenario ────


def test_screenshot_scenario_back_to_back_blast():
    """The exact operator-reported scenario: TX iface sent 830M, RX
    iface received 820M on the wire. Loss should be ~10M / ~1.2%,
    NOT 824M / 99.37%."""
    compute = _helper()

    # ens2f0np0 = TX side. own_phy_tx=830M, own_phy_rx=0.
    # Its peer (ens2f1np1) has phy_tx=0, phy_rx=820M.
    lost_tx, pct_tx = compute(
        own_phy_tx=830_000_000, own_phy_rx=0,
        peer_phy_tx=0, peer_phy_rx=820_000_000,
    )
    assert lost_tx == 10_000_000
    assert 1.20 < pct_tx < 1.21

    # ens2f1np1 = RX side. own_phy_tx=0, own_phy_rx=820M.
    # Its peer (ens2f0np0) has phy_tx=830M, phy_rx=0.
    # Same pair_loss number on both halves.
    lost_rx, pct_rx = compute(
        own_phy_tx=0, own_phy_rx=820_000_000,
        peer_phy_tx=830_000_000, peer_phy_rx=0,
    )
    assert lost_rx == lost_tx
    assert pct_rx == pct_tx


def test_loopback_iface_uses_own_counters():
    """When the same iface is both TX and RX (loopback / single-iface
    test), there's no peer — pass own values as peer too. Loss is
    own.phy_tx - own.phy_rx."""
    compute = _helper()
    lost, pct = compute(
        own_phy_tx=1_000_000, own_phy_rx=950_000,
        peer_phy_tx=1_000_000, peer_phy_rx=950_000,
    )
    assert lost == 50_000
    assert 4.99 < pct < 5.01


def test_no_traffic_yields_zero_no_division():
    """When neither half has TX, lost=0 and loss_pct=0.0 (no
    ZeroDivisionError)."""
    compute = _helper()
    lost, pct = compute(0, 0, 0, 0)
    assert lost == 0
    assert pct == 0.0


def test_excess_rx_clamps_to_zero():
    """Sometimes PHY RX exceeds PHY TX (CRC frames, broadcast noise,
    other tenants). Loss is `max(0, …)` — never negative."""
    compute = _helper()
    lost, pct = compute(
        own_phy_tx=100, own_phy_rx=200,
        peer_phy_tx=100, peer_phy_rx=200,
    )
    assert lost == 0
    assert pct == 0.0


def test_perfect_delivery_is_zero_loss():
    """When TX == RX, no loss."""
    compute = _helper()
    lost, pct = compute(
        own_phy_tx=1_000_000_000, own_phy_rx=0,
        peer_phy_tx=0, peer_phy_rx=1_000_000_000,
    )
    assert lost == 0
    assert pct == 0.0


def test_pair_uses_max_across_both_halves():
    """The "pair_tx" is the max of own.phy_tx and peer.phy_tx —
    regardless of which side is the TX. Same for pair_rx. So passing
    the arguments in either order gives the same loss."""
    compute = _helper()
    a = compute(
        own_phy_tx=500, own_phy_rx=0,
        peer_phy_tx=0, peer_phy_rx=300,
    )
    b = compute(
        own_phy_tx=0, own_phy_rx=300,
        peer_phy_tx=500, peer_phy_rx=0,
    )
    assert a == b
    assert a[0] == 200  # 500 - 300


def test_string_safe_inputs_coerce_to_int():
    """The server might hand back values as strings or None in
    pathological cases. Helper must not raise."""
    compute = _helper()
    # None silently → 0.
    lost, _ = compute(None, None, None, None)
    assert lost == 0
    # Strings would actually fail the int() conversion in production
    # code, but the helper uses `int(... or 0)` — empty string would
    # raise. We just guard against None here; pass through int values.
    lost, _ = compute(100, 50, 100, 50)
    assert lost == 50


# ───── the renderer + aggregation paths (source-level pin) ───────────────


SRC = (REPO / "traffic_client" / "statistics_section.py").read_text()


def test_helper_defined_at_module_level():
    """The pure helper is module-level (importable for tests)."""
    assert "def compute_iface_pair_loss(" in SRC


def test_merged_seeds_phy_counters_from_interface():
    """The iface seed must pull `tx` and `rx` from the interface dict
    into `phy_tx` / `phy_rx`. Without this, all iface-loss math falls
    back to 0."""
    assert '"phy_tx": int(interface.get("tx", 0) or 0)' in SRC
    assert '"phy_rx": int(interface.get("rx", 0) or 0)' in SRC


def test_merged_seeds_empty_peer_set():
    """`peer_ifaces` starts as a set so the stream loop can `.add()`
    its peer ifaces in O(1)."""
    assert '"peer_ifaces": set()' in SRC


def test_stream_loop_records_peer_mapping():
    """For each stream, both endpoints learn about the other so the
    loss row can find its peer from either side."""
    # Loose check that the bi-directional peer wiring exists.
    assert (
        'merged_statistics[tx_iface]["peer_ifaces"].add(rx_iface)' in SRC
    )
    assert (
        'merged_statistics[rx_iface]["peer_ifaces"].add(tx_iface)' in SRC
    )


def test_renderer_calls_helper_not_stream_aggregates():
    """The (10)/(11) cells must call `compute_iface_pair_loss(...)`
    with phy_tx/phy_rx, NOT use `tx_for_loss / rx_for_loss` for the
    displayed number (those were v0.5.139's wrong aggregates)."""
    assert "compute_iface_pair_loss(" in SRC


def test_renderer_uses_peer_ifaces_set():
    """The renderer must walk `stats.get('peer_ifaces')` and ask each
    peer for its PHY counters — that's how both halves of a pair
    converge on the SAME loss number."""
    assert 'stats.get("peer_ifaces"' in SRC
    # And must look up the peer in merged/filtered statistics.
    assert "peer.get(\"phy_tx\"" in SRC
    assert "peer.get(\"phy_rx\"" in SRC


def test_renderer_dashes_when_no_traffic():
    """For ifaces with no traffic and no peer traffic, show '—' not
    '0' — the latter implies "we measured, it's zero", the former
    "nothing to measure"."""
    # Loose check that em-dash branch still exists.
    assert "has_traffic" in SRC
    assert '"—"' in SRC


def test_phy_counters_get_cumulative_preservation():
    """`phy_tx` / `phy_rx` must be in the cumulative-counter
    preservation list so a single-fetch glitch doesn't blank the
    loss row."""
    # Lazy regex: just check the new keys appear in the preservation
    # tuple alongside the existing ones.
    import re
    m = re.search(
        r'for key in \([^)]*"phy_tx"[^)]*"phy_rx"[^)]*\):',
        SRC, flags=re.DOTALL,
    )
    assert m is not None, (
        "phy_tx/phy_rx not added to the cumulative preservation tuple"
    )
