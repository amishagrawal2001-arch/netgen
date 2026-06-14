"""v0.5.133: rx_queue cap divides by concurrent DPDK stream count.

After v0.5.131 bumped the line-rate cap from 8 → 16, srv06 saw
EAL `Cause: mbuf_pool` when starting a 2nd concurrent DPDK
stream. Each worker reserves its own mempool — 16 queues × 8K
mbufs × 2KB ≈ 256 MB per worker. 2 streams × (rx_worker +
tx_worker) = ~1 GB hugepages, exceeding srv06's budget.

v0.5.133 backs off the cap proportionally: with N already-running
DPDK streams, the new stream gets `base_cap // (N + 1)` queues.
Floor at 2 so the stream still benefits from RSS.

Fallback contract preserved:
- Single-stream operations get the full v0.5.131/132 cap
- Multi-stream operations get a fair share of the budget
- Stops the EAL mbuf_pool failure dead.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ----- _line_rate_queue_cap with active_dpdk_streams ----------------------

def test_cap_unchanged_when_no_active_streams():
    """active_dpdk_streams=0 → same cap as pre-v0.5.133 (single
    stream gets full budget)."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        assert _line_rate_queue_cap("0000:2b:00.0",
                                     active_dpdk_streams=0) == 14


def test_cap_divides_by_two_when_one_active():
    """The srv06 case: 1 stream already running, starting a 2nd.
    base=14 → 14/2 = 7 per stream. Total = 14, same as single
    stream. Prevents hugepage doubling."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        assert _line_rate_queue_cap("0000:2b:00.0",
                                     active_dpdk_streams=1) == 7


def test_cap_divides_further_with_more_streams():
    """3 already running, 4th coming → 14/4 = 3 queues each.
    Stays well above the floor of 2."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        assert _line_rate_queue_cap("0000:2b:00.0",
                                     active_dpdk_streams=3) == 3


def test_cap_floors_at_two_with_many_streams():
    """Defense in depth — even with 100 streams, give the new
    stream 2 queues so RSS still helps. (DPDK with 1 queue lands
    everything on lcore 0 → useless for multi-flow.)"""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        assert _line_rate_queue_cap("0000:2b:00.0",
                                     active_dpdk_streams=100) == 2


def test_cap_fallback_path_also_divides():
    """Lean host (no NUMA → base=8): same backoff applies."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=None):
        assert _line_rate_queue_cap("0000:ff:ff.7",
                                     active_dpdk_streams=0) == 8
        # 8 // 2 = 4 — half budget for the new stream when 1 is up
        assert _line_rate_queue_cap("0000:ff:ff.7",
                                     active_dpdk_streams=1) == 4
        # 8 // 5 = 1 → floor to 2
        assert _line_rate_queue_cap("0000:ff:ff.7",
                                     active_dpdk_streams=4) == 2


# ----- _count_active_dpdk_rx helper --------------------------------------

def test_count_returns_zero_on_lookup_failure():
    """Defensive: when the registry isn't importable (test scaffold
    contexts), helper returns 0 so the autoscaler treats this as
    the first stream — matches pre-v0.5.133 behavior."""
    from run_tgen_server import _count_active_dpdk_rx
    with patch("utils.dpdk_rx_manager.registry",
               side_effect=RuntimeError("no registry in test")):
        assert _count_active_dpdk_rx() == 0


def test_count_reads_registry_handles_map():
    """Happy path: count = len(registry()._handles). 3 entries →
    return 3."""
    from run_tgen_server import _count_active_dpdk_rx
    fake_reg = MagicMock()
    fake_reg._handles = {"s1": object(), "s2": object(), "s3": object()}
    with patch("utils.dpdk_rx_manager.registry", return_value=fake_reg):
        assert _count_active_dpdk_rx() == 3


def test_count_empty_handles_returns_zero():
    """No streams up → 0. The new stream gets the full cap."""
    from run_tgen_server import _count_active_dpdk_rx
    fake_reg = MagicMock()
    fake_reg._handles = {}
    with patch("utils.dpdk_rx_manager.registry", return_value=fake_reg):
        assert _count_active_dpdk_rx() == 0


# ----- End-to-end through the autoscaler ---------------------------------

def _stream(target_pps, **overrides):
    s = {
        "engine": "dpdk", "rx_engine": "dpdk", "dpdk_enable": True,
        "frame_size": 512, "stream_pps_rate": str(target_pps),
        "interface": "ens2f1np1",
        "protocol_data": {
            "ipv4": {"ipv4_source": "10.0.0.1", "ipv4_destination": "10.0.0.2"},
            "udp": {"udp_source_port": "222", "udp_destination_port": "1111"},
            "vlan": {"vlan_id": "1"},
        },
        "VLAN": "Untagged",
    }
    s.update(overrides)
    return s


def _capture_kwargs(stream_data, active_count=0):
    """Patch the registry so the autoscaler thinks `active_count`
    streams are already running. Returns the kwargs the autoscaler
    would have passed to rx_registry.start()."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    captured = {}
    fake_reg = MagicMock()
    fake_reg.start.side_effect = lambda **kw: captured.update(kw) or {"pid": 1}
    fake_reg._handles = {f"existing_{i}": object() for i in range(active_count)}
    with patch("utils.nic_counters.iface_to_pci_bdf",
               return_value="0000:2b:00.0"), \
         patch("utils.dpdk_rx_manager.registry",
               MagicMock(return_value=fake_reg)):
        _maybe_start_dpdk_rx_for_stream(
            stream_id="new_stream", rx_interface="ens2f0np0",
            stream_data=stream_data,
        )
    return captured


def test_e2e_first_stream_gets_full_cap():
    """Single-stream path is unchanged from v0.5.132. srv06's first
    line-rate stream still gets 16 queues on a 16-core NUMA node."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), active_count=0)
    assert kw["rx_queues"] == 16


def test_e2e_second_stream_halves_cap():
    """srv06 reproduction: 1 stream already up, 2nd starting. The
    new stream gets 16 // 2 = 8 queues. Combined mempool footprint
    matches single-stream era. No more EAL mbuf_pool error."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), active_count=1)
    assert kw["rx_queues"] == 8


def test_e2e_three_active_quarters_cap():
    """4th stream: 16 // 4 = 4 queues."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), active_count=3)
    assert kw["rx_queues"] == 4


def test_e2e_lcores_still_match_queue_count():
    """The lcore string length must track the reduced queue count.
    For 8 queues, expect 9 lcores from the NUMA cpulist (NOT 17,
    which was the pre-v0.5.133 single-stream value)."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), active_count=1)
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8"


def test_e2e_explicit_override_bypasses_backoff():
    """Operator override (`rx_queues` in stream_data) is the escape
    hatch. Backoff doesn't apply — the operator knows their box's
    hugepage budget."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0, rx_queues=12), active_count=5)
    assert kw["rx_queues"] == 12


def test_e2e_mid_rate_buckets_unaffected_by_backoff():
    """v0.5.133 only touches the line-rate bucket (the one that
    bumped to 16 in v0.5.131). The mid-rate buckets stay at their
    original 2/4 queues — they were never the source of hugepage
    pressure."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(18_000_000), active_count=5)
    assert kw["rx_queues"] == 4  # Still 4, not 4 // 6 = 0
