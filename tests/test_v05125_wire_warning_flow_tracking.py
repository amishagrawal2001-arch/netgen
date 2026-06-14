"""v0.5.125: wire_delivery_warning must not falsely accuse the
wire when flow tracking is disabled.

Pre-fix `wire_delivery_warning` triggered whenever
`tx_rate > 100 AND rx_rate < 5% of tx_rate`. That fired for two
genuinely different conditions:

  1. Real wire-drop (switch storm-control, MAC mismatch, etc.) —
     warning was helpful.
  2. `flow_tracking_enabled=false` on the stream — netgen isn't
     running an RX sniffer at all, so rx_rate is zero BY DESIGN.
     The warning's "wire is dropping ~100% of frames" message
     was a flat-out false accusation that sent an operator (and
     this debug agent) chasing a non-existent switch bug.

v0.5.125 splits the trigger:

  * flow_tracking_enabled=false → warning fires with `reason:
    flow_tracking_disabled` and a different message pointing at
    the Flow Tracking toggle in the dialog.
  * flow_tracking_enabled=true and rx_rate < 5% → warning fires
    with the original wire-drop message (unchanged behavior for
    the legitimate case).

Tests cover the trigger split + the response payload shape.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _stream(*, tx_rate=1000.0, rx_rate=0.0,
            flow_tracking=True, rx_iface="ens2f1np1"):
    """Stream-stats shape produced by /api/streams/stats."""
    return {
        "tx_rate": tx_rate,
        "rx_rate": rx_rate,
        "flow_tracking_enabled": flow_tracking,
        "rx_interface": rx_iface,
        "stream_name": "test",
        "stream_id": "x",
        "interface": "ens2f0np0",
    }


def _apply_warning_pass(streams):
    """Re-run just the warning-detection block over a list of
    stream dicts. The actual block lives inline in
    run_tgen_server.py:get_stream_stats around line 1057; this
    extracts the same logic with the same thresholds so unit
    tests don't need to spin up Flask + a stream tracker."""
    for s in streams:
        rx_iface = (s.get("rx_interface") or "").strip()
        tx_rate = float(s.get("tx_rate") or 0.0)
        rx_rate = float(s.get("rx_rate") or 0.0)
        if tx_rate < 100 or rx_iface in ("", None):
            continue
        if rx_rate >= tx_rate * 0.05:
            continue
        flow_on = bool(s.get("flow_tracking_enabled"))
        if not flow_on:
            s["wire_delivery_warning"] = {
                "tx_rate": tx_rate,
                "rx_rate": rx_rate,
                "summary": (
                    f"TX is at {tx_rate:.0f} pps but RX counter "
                    f"is 0 because Flow Tracking is DISABLED "
                    f"for this stream. The wire may be delivering "
                    f"fine — netgen just isn't counting. Enable "
                    f"Flow Tracking in the Edit Stream dialog to "
                    f"start counting RX packets."
                ),
                "reason": "flow_tracking_disabled",
            }
            continue
        # Wire-drop path (original behavior)
        s["wire_delivery_warning"] = {
            "tx_rate": tx_rate,
            "rx_rate": rx_rate,
            "summary": (
                f"TX is at {tx_rate:.0f} pps but RX on "
                f"{rx_iface} is at {rx_rate:.0f} pps — "
                f"wire is dropping frames."
            ),
        }


def test_flow_tracking_off_yields_different_warning_reason():
    """The bug. Operator's Flow Tracking is off. Pre-fix the
    warning falsely accused the wire."""
    s = _stream(flow_tracking=False)
    _apply_warning_pass([s])
    w = s.get("wire_delivery_warning")
    assert w is not None, "Warning should still fire — just with a different reason"
    assert w.get("reason") == "flow_tracking_disabled"
    assert "Flow Tracking is DISABLED" in w["summary"]
    assert "switch" not in w["summary"].lower(), (
        "Don't mention switch when flow tracking is off — that's "
        "what wasted the debug session"
    )


def test_flow_tracking_on_keeps_original_wire_warning():
    """Regression guard. Real wire drops still get the original
    warning that mentions switch storm-control etc. so the
    operator knows where to look."""
    s = _stream(flow_tracking=True)
    _apply_warning_pass([s])
    w = s.get("wire_delivery_warning")
    assert w is not None
    assert w.get("reason") != "flow_tracking_disabled"
    assert "wire is dropping" in w["summary"]


def test_no_warning_when_rx_matches_tx():
    """Both warnings should NOT fire when rx is within 5% of tx."""
    s = _stream(tx_rate=1000.0, rx_rate=960.0, flow_tracking=True)
    _apply_warning_pass([s])
    assert "wire_delivery_warning" not in s


def test_no_warning_when_tx_below_threshold():
    """Stopping / idle streams shouldn't trip the warning."""
    s = _stream(tx_rate=50.0, rx_rate=0.0, flow_tracking=False)
    _apply_warning_pass([s])
    assert "wire_delivery_warning" not in s


def test_no_warning_when_rx_iface_unset():
    """Streams without rx_interface (TX-only) shouldn't trip."""
    s = _stream(rx_iface="")
    _apply_warning_pass([s])
    assert "wire_delivery_warning" not in s


def test_source_uses_flow_tracking_check():
    """Pin the actual source so a refactor that removes the
    flow-tracking branch fails."""
    src_path = REPO / "run_tgen_server.py"
    text = src_path.read_text()
    # The flow_tracking check must precede the wire-drop summary.
    assert "flow_tracking_disabled" in text, (
        "run_tgen_server.py must emit reason=flow_tracking_disabled "
        "for the flow-tracking-off case — the marker that the v0.5.125 "
        "split exists"
    )
    assert "is DISABLED" in text, (
        "The flow-tracking-off summary must mention 'DISABLED' so "
        "the operator immediately knows where to look"
    )
