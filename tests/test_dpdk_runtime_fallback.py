"""Runtime DPDK fallback telemetry (v0.2.77).

When the launcher has to swap engines mid-flight (tx_worker rc=100
Broadcom ULP error, or any exception during handoff), the worker
calls ``stream_tracker.mark_runtime_engine`` to record the swap.
The stats endpoint then surfaces ``runtime_engine`` +
``runtime_fallback_reason`` so the GUI can show "Scapy ⚠ (was DPDK)"
in the Engine column with the reason in the tooltip.

These tests cover the tracker plumbing without spinning up a real
tx_worker. Pure-function — no Qt, no subprocess.
"""

import threading
import pytest

from multithreaded_traffic_gen import StreamTracker


def _seeded_tracker():
    tr = StreamTracker()
    tr.add_stream({
        "stream_id": "s1",
        "interface": "ens1f0",
        "stream_name": "test-stream",
        "stop_event": threading.Event(),
        "rx_thread": None,
        "rx_interface": "ens1f0",
        "flow_tracking_enabled": False,
        "future": None,
        "frame_size": 1024,
    })
    return tr


# ────────────────────────────────────────────── mark_runtime_engine
def test_mark_runtime_engine_records_both_fields():
    tr = _seeded_tracker()
    tr.mark_runtime_engine(
        "ens1f0", "s1",
        runtime_engine="scapy",
        fallback_reason="tx_worker rc=100",
    )
    stats = tr.get_stream_stats()
    assert len(stats) == 1
    item = stats[0]
    assert item["runtime_engine"] == "scapy"
    assert item["runtime_fallback_reason"] == "tx_worker rc=100"


def test_mark_runtime_engine_no_op_when_stream_unknown():
    """Mark for a stream that isn't tracked is silent — don't raise,
    don't add a phantom row."""
    tr = _seeded_tracker()
    tr.mark_runtime_engine(
        "wrong_iface", "wrong_sid",
        runtime_engine="scapy", fallback_reason="x",
    )
    stats = tr.get_stream_stats()
    assert len(stats) == 1
    # The legitimately-tracked stream stays clean.
    assert "runtime_engine" not in stats[0]
    assert "runtime_fallback_reason" not in stats[0]


def test_mark_runtime_engine_can_be_called_with_only_one_field():
    """Partial calls (e.g. only the reason, leaving engine unchanged)
    must work — useful for layering on top of an earlier mark."""
    tr = _seeded_tracker()
    tr.mark_runtime_engine("ens1f0", "s1", runtime_engine="scapy")
    tr.mark_runtime_engine(
        "ens1f0", "s1",
        fallback_reason="additional context appended later",
    )
    item = tr.get_stream_stats()[0]
    assert item["runtime_engine"] == "scapy"
    assert "additional context" in item["runtime_fallback_reason"]


# ─────────────────────────────────────── stats payload shape (v0.2.77)
def test_stats_omits_runtime_fields_when_never_marked():
    """Unmarked streams should NOT carry the new keys at all — keeps
    the legacy stats shape clean for clients that don't know about
    them."""
    tr = _seeded_tracker()
    item = tr.get_stream_stats()[0]
    assert "runtime_engine" not in item
    assert "runtime_fallback_reason" not in item


def test_stats_carries_runtime_fields_once_marked():
    tr = _seeded_tracker()
    tr.mark_runtime_engine(
        "ens1f0", "s1",
        runtime_engine="scapy",
        fallback_reason="DPDK handoff failed at runtime: link drop",
    )
    item = tr.get_stream_stats()[0]
    # Both surfaced verbatim — client renders the reason in the
    # Engine cell tooltip.
    assert item["runtime_engine"] == "scapy"
    assert "link drop" in item["runtime_fallback_reason"]
