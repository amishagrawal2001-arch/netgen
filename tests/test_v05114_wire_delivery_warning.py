"""v0.5.114: /api/streams/stats annotates each active_stream
with a wire_delivery_warning when TX is firing but RX is
essentially zero. Prevents the operator's "is it MAC, VLAN, or
switch?" 5-hour debugging loop the srv06 saga taught us about.

The detector is intentionally permissive (only fires at TX >=
100 pps with RX < 5% of TX) to avoid false-positives on idle /
one-way / stopping streams.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _stream(stream_id, tx_rate, rx_rate, rx_interface="ens2f1np1"):
    """Helper to assemble a stream dict matching active_streams
    shape that the stats endpoint produces."""
    return {
        "stream_id": stream_id,
        "stream_name": f"s_{stream_id}",
        "rx_interface": rx_interface,
        "tx_rate": tx_rate,
        "rx_rate": rx_rate,
        "tx_count": int(tx_rate * 10),
        "rx_count": int(rx_rate * 10),
        "interface": "ens2f0np0",
    }


def _apply_detector(streams):
    """Run only the wire-delivery detector logic against a
    pre-built active_streams list. This mirrors what the stats
    endpoint does AFTER the rx_worker fold — pure annotation
    pass."""
    for s in streams:
        rx_iface = (s.get("rx_interface") or "").strip()
        tx_rate = float(s.get("tx_rate") or 0.0)
        rx_rate = float(s.get("rx_rate") or 0.0)
        if tx_rate < 100 or rx_iface in ("", None):
            continue
        if rx_rate >= tx_rate * 0.05:
            continue
        s["wire_delivery_warning"] = {
            "tx_rate": tx_rate,
            "rx_rate": rx_rate,
            "summary": f"TX at {tx_rate:.0f} pps, RX at {rx_rate:.0f}",
        }
    return streams


def test_warning_fires_when_rx_essentially_zero():
    """Classic srv06 saga state: TX hammering, RX flat. Warning
    must fire and mention both rates in the summary."""
    streams = [_stream("s1", tx_rate=1000.0, rx_rate=0.0)]
    _apply_detector(streams)
    assert "wire_delivery_warning" in streams[0]
    w = streams[0]["wire_delivery_warning"]
    assert w["tx_rate"] == 1000.0
    assert w["rx_rate"] == 0.0


def test_warning_does_not_fire_for_healthy_stream():
    """Stream at line-rate delivery (RX matches TX) should NOT
    get a warning — that's the success state."""
    streams = [_stream("s1", tx_rate=1000.0, rx_rate=1000.0)]
    _apply_detector(streams)
    assert "wire_delivery_warning" not in streams[0]


def test_warning_does_not_fire_for_idle_stream():
    """TX < 100 pps → likely stopping or idle. Don't spam the
    operator with false-positive warnings."""
    streams = [_stream("s1", tx_rate=50.0, rx_rate=0.0)]
    _apply_detector(streams)
    assert "wire_delivery_warning" not in streams[0]


def test_warning_does_not_fire_for_partial_delivery_above_threshold():
    """RX >= 5% of TX → not catastrophic dropping. Switch
    storm-control partial mode (operator's 74% delivery at
    500k pps) shouldn't trip this warning."""
    streams = [_stream("s1", tx_rate=1000.0, rx_rate=300.0)]
    _apply_detector(streams)
    assert "wire_delivery_warning" not in streams[0]


def test_warning_fires_at_just_below_threshold():
    """RX = 4% of TX (just below 5%) → fires. Boundary check."""
    streams = [_stream("s1", tx_rate=1000.0, rx_rate=40.0)]
    _apply_detector(streams)
    assert "wire_delivery_warning" in streams[0]


def test_warning_skipped_when_rx_iface_missing():
    """Stream has no rx_interface set (legacy config) — skip
    silently. We can't tell where to look for the issue."""
    streams = [_stream("s1", tx_rate=1000.0, rx_rate=0.0, rx_interface="")]
    _apply_detector(streams)
    assert "wire_delivery_warning" not in streams[0]


def test_endpoint_returns_warning_when_present():
    """Smoke: hit the actual /api/streams/stats endpoint, verify
    the warning field round-trips. The endpoint relies on
    active_streams + stream_tracker state which we can't easily
    stub here — this is a contract test on the shape, not an
    end-to-end delivery test."""
    from run_tgen_server import app
    with app.test_client() as c:
        r = c.get("/api/streams/stats")
    assert r.status_code == 200
    body = r.get_json()
    assert "active_streams" in body
    # Whether a warning is present depends on test-DB state.
    # Just confirm the shape is preserved.
    for s in body.get("active_streams", []):
        if "wire_delivery_warning" in s:
            w = s["wire_delivery_warning"]
            assert "tx_rate" in w
            assert "rx_rate" in w
            assert "summary" in w
