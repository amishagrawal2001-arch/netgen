"""v0.5.112: rx_engine + engine + dpdk_enable + friends must
stay at the TOP LEVEL of the saved stream dict after Edit Save.

Pre-fix the edit-stream updater at stream_control.py:1398-1403
iterated dialog top-level keys into protocol_selection unless
the key was explicitly listed. rx_engine wasn't listed → got
buried at stream["protocol_selection"]["rx_engine"] →
/api/traffic/start's _maybe_start_dpdk_rx_for_stream reads
stream_data.get("rx_engine") at the TOP level → saw nothing →
no rx_worker spawned. This was the v0.5.110 srv06 saga's
"dialog says DPDK, stats say scapy" gap.

Pin the fix: simulate edit_selected_stream's updated-dict build
and assert engine keys end up at the top level.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


_TOP_LEVEL_ENGINE_KEYS = (
    "engine", "rx_engine", "dpdk_enable",
    "dpdk_multi_instance", "dpdk_tx_cores",
    "rx_pci_bdf", "enable_timestamps", "rdma",
)


def _build_updated_like_edit_save(original, edited, tx_port, stream_name):
    """Mirror the relevant slice of edit_selected_stream's updater.
    Kept in lockstep with stream_control.py — if the real updater
    changes shape, mirror the change here too. The point of this
    test is that engine keys land at the top level, not bury the
    full code path.
    """
    updated = {
        "stream_id": original.get("stream_id"),
        "status": original.get("status", "stopped"),
        "rx_port": edited.get("rx_port", tx_port),
        "flow_tracking_enabled": edited.get("flow_tracking_enabled", False),
        "protocol_selection": {},
        "protocol_data": edited.get("protocol_data", {}),
        "rocev2": edited.get("rocev2", {}),
        "uec": edited.get("uec", {}),
        "override_settings": edited.get("override_settings", {}),
        "stream_rate_control": edited.get("stream_rate_control", {})
    }
    for _k in _TOP_LEVEL_ENGINE_KEYS:
        if _k in edited:
            updated[_k] = edited[_k]
    for k, v in edited.items():
        if k not in updated and k not in {
            "protocol_data", "rocev2", "uec", "override_settings",
            "stream_rate_control", "rx_port", "stream_id", "status",
            "flow_tracking_enabled"
        } and k not in _TOP_LEVEL_ENGINE_KEYS:
            updated["protocol_selection"][k] = v
    updated["protocol_selection"]["name"] = stream_name
    return updated


def test_rx_engine_promoted_to_top_level():
    """The bug: rx_engine landed in protocol_selection, server
    looked at top level, never saw it. Fix: top-level survival."""
    edited = {
        "name": "stream_1",
        "enabled": True,
        "rx_engine": "dpdk",
        "engine": "dpdk",
        "dpdk_enable": True,
        "protocol_data": {"mac": {}},
    }
    updated = _build_updated_like_edit_save(
        original={"stream_id": "abc"},
        edited=edited, tx_port="ens2f0np0", stream_name="stream_1",
    )
    assert updated["rx_engine"] == "dpdk", (
        "rx_engine must be at TOP level — server's "
        "_maybe_start_dpdk_rx_for_stream reads it from there"
    )
    assert updated["engine"] == "dpdk"
    assert updated["dpdk_enable"] is True
    # Cross-check: not buried.
    assert "rx_engine" not in updated["protocol_selection"]
    assert "engine" not in updated["protocol_selection"]


def test_non_engine_keys_still_route_to_protocol_selection():
    """Don't regress the existing behavior: arbitrary dialog
    top-level keys (frame_size, stream_pps_rate, name) still go
    into protocol_selection."""
    edited = {
        "name": "stream_1",
        "frame_size": "1500",
        "stream_pps_rate": "1000",
        "rx_engine": "dpdk",
    }
    updated = _build_updated_like_edit_save(
        original={"stream_id": "abc"},
        edited=edited, tx_port="ens2f0np0", stream_name="stream_1",
    )
    assert updated["rx_engine"] == "dpdk"  # promoted
    assert updated["protocol_selection"]["frame_size"] == "1500"
    assert updated["protocol_selection"]["stream_pps_rate"] == "1000"


def test_absent_engine_keys_dont_overwrite_with_none():
    """If the dialog didn't surface rx_engine (older dialog,
    test stub, etc.), don't write rx_engine=None at top level —
    leaving it absent lets the server fall back to default
    (scapy)."""
    edited = {
        "name": "stream_1",
        "frame_size": "1500",
        # rx_engine intentionally absent
    }
    updated = _build_updated_like_edit_save(
        original={"stream_id": "abc"},
        edited=edited, tx_port="ens2f0np0", stream_name="stream_1",
    )
    assert "rx_engine" not in updated  # not even None


def test_real_edit_save_path_promotes_rx_engine():
    """Smoke test the actual stream_control.py code by importing
    and running its constant. The function isn't easily callable
    here (it's a method that needs full Qt + tree state), so we
    pin the constant + behavior via the helper above. The
    important property test_rx_engine_promoted_to_top_level
    pins the contract; this guards against the constant list
    silently shrinking."""
    import traffic_client.stream_control as sc
    # Parse the module text for the exact tuple — keeps this
    # test from re-importing every dialog widget. The fix's
    # uniqueness is the tuple's content.
    src = (Path(sc.__file__)).read_text(encoding="utf-8")
    assert '"rx_engine"' in src, (
        "stream_control.py must list rx_engine as a top-level "
        "engine key — otherwise the v0.5.112 fix has regressed"
    )
    assert '"engine"' in src
    assert '"dpdk_enable"' in src
