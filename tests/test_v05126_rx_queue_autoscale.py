"""v0.5.126: auto-scale rx_worker queues + lcores based on target pps.

Observed on srv06: at 46 Mpps with 512B frames, single-queue
rx_worker hit `hw_imissed: 3,397,304,661` — 3.4 BILLION drops at
the NIC RX ring because one DPDK lcore can't consume that fast.

ConnectX-6 with 512B frames saturates around ~6-7 Mpps per
single lcore (measured: 6.4 Mpps under load). For higher
target rates, spread across multiple queues + lcores via RSS.

This file pins the auto-scale thresholds and the operator-
override path.

Buckets:
  * < 6 Mpps          → 1 queue,  lcores 0,1
  * 6 .. <18 Mpps     → 2 queues, lcores 0,1,2
  * 18 .. <30 Mpps    → 4 queues, lcores 0,1,2,3,4
  * >= 30 Mpps        → 8 queues, lcores 0,1,2,3,4,5,6,7,8
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _stream(target_pps, **overrides):
    """Stream config with target_pps surfaced where
    _resolve_target_pps() can find it."""
    s = {
        "engine": "dpdk",
        "rx_engine": "dpdk",
        "dpdk_enable": True,
        "frame_size": 512,
        "stream_pps_rate": str(target_pps),
        "interface": "ens2f1np1",
        "protocol_data": {
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
            "udp": {"udp_source_port": "222",
                    "udp_destination_port": "1111"},
            "vlan": {"vlan_id": "1"},
        },
        "VLAN": "Untagged",
    }
    s.update(overrides)
    return s


def _call_and_capture_kwargs(stream_data):
    """Invoke _maybe_start_dpdk_rx_for_stream with the registry
    .start() mocked, return the kwargs passed in. That's the
    cleanest way to pin rx_queues/lcores without running DPDK."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    captured = {}
    fake_reg = MagicMock()
    fake_reg.start.side_effect = lambda **kw: captured.update(kw) or {"pid": 1}
    fake_reg._handles = {}   # post-spawn liveness check sees nothing → no fallback

    with patch("utils.nic_counters.iface_to_pci_bdf",
               return_value="0000:2b:00.0"), \
         patch("utils.dpdk_rx_manager.registry",
               MagicMock(return_value=fake_reg)):
        _maybe_start_dpdk_rx_for_stream(
            stream_id="s1", rx_interface="ens2f0np0",
            stream_data=stream_data,
        )
    return captured


def test_low_pps_picks_single_queue():
    """100k pps → 1 queue. Matches the v0.5.125 working setup
    (operator ran at 100k pps with rx_queues=1 and saw no drops).
    Don't pay multi-queue setup cost for low-rate tests."""
    kw = _call_and_capture_kwargs(_stream(100_000))
    assert kw["rx_queues"] == 1
    assert kw["lcores"] == "0,1"


def test_six_mpps_threshold_picks_two_queues():
    """At exactly the single-queue saturation point (6 Mpps),
    bump to 2 queues so headroom is available."""
    kw = _call_and_capture_kwargs(_stream(6_000_000))
    assert kw["rx_queues"] == 2
    assert kw["lcores"] == "0,1,2"


def test_eighteen_mpps_picks_four_queues():
    kw = _call_and_capture_kwargs(_stream(18_000_000))
    assert kw["rx_queues"] == 4
    assert kw["lcores"] == "0,1,2,3,4"


def test_thirty_mpps_picks_eight_queues():
    """The srv06 v0.5.126 trip rate — 46 Mpps falls in this
    bucket. 8 queues × 6 Mpps each = 48 Mpps ceiling. Enough
    headroom for line-rate at 200G with 512B frames."""
    kw = _call_and_capture_kwargs(_stream(46_000_000))
    assert kw["rx_queues"] == 8
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8"


def test_explicit_operator_override_wins():
    """If the operator sets rx_queues in stream_data, that wins
    over the auto-scale. Lets advanced users tune."""
    kw = _call_and_capture_kwargs(_stream(46_000_000, rx_queues=2))
    assert kw["rx_queues"] == 2
    # Auto-derived lcores from rx_queues+1
    assert kw["lcores"] == "0,1,2"


def test_explicit_lcores_override_wins():
    """Operator can also set a specific lcore list."""
    s = _stream(100_000)
    s["rx_queues"] = 4
    s["rx_lcores"] = "10,11,12,13,14"
    kw = _call_and_capture_kwargs(s)
    assert kw["rx_queues"] == 4
    assert kw["lcores"] == "10,11,12,13,14"


def test_explicit_override_clamps_to_safe_range():
    """Defensive: garbage in rx_queues shouldn't crash; clamp."""
    kw = _call_and_capture_kwargs(_stream(100_000, rx_queues=99))
    assert kw["rx_queues"] == 16, (
        "rx_queues should clamp at 16 (DPDK MAX_RX_QUEUES "
        "in rx_worker.c)"
    )
    kw = _call_and_capture_kwargs(_stream(100_000, rx_queues=0))
    assert kw["rx_queues"] == 1
    kw = _call_and_capture_kwargs(_stream(100_000, rx_queues="bogus"))
    assert kw["rx_queues"] == 1, "Invalid value falls back to 1, not crash"


def test_zero_or_missing_pps_picks_max_queues():
    """v0.5.127: pps == 0 means Line Rate (or unset). Tx_worker
    fires at max chip rate (~25-46 Mpps on 200G ConnectX-6).
    Pre-v0.5.127 the 0 fell into the < 6 Mpps bucket → single
    queue → hw_imissed=1.1 BILLION at line rate. Now treat 0
    the same as the top bucket so Line Rate works out of the
    box."""
    s = _stream(0)
    del s["stream_pps_rate"]
    kw = _call_and_capture_kwargs(s)
    assert kw["rx_queues"] == 8
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8"


def test_explicit_zero_pps_also_picks_max_queues():
    """Same fix applies when stream_pps_rate is explicitly 0."""
    kw = _call_and_capture_kwargs(_stream(0))
    assert kw["rx_queues"] == 8
