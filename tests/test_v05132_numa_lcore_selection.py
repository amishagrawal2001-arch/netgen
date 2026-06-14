"""v0.5.132: rx_worker lcores picked from NIC's NUMA cpulist.

Pre-fix the autoscaler returned `range(cap + 1)` for the lcore
list. On srv06 (dual-socket, NIC on NUMA node 0 with CPUs
`0-15, 32-47`), picking lcores 0..16 silently landed lcore 16 on
NUMA node 1 — one full QPI/UPI hop from the NIC's memory.
Per-queue throughput halved (2.2 → 1.1 Mpps/queue) when the cap
jumped from 8 to 16. TX/RX ratio improved 73% → 87% with
v0.5.131 but didn't reach line rate; the NUMA-cross was eating
the rest.

v0.5.132 picks lcores from the actual NIC NUMA node's cpulist
(via `/sys/devices/system/node/node<N>/cpulist`). srv06 with
NIC on node 0 now gets lcores `0,1,2,3,4,5,6,7,8,9,10,11,12,
13,14,15,32` — all on the right node.

Falls back to `range(N)` when sysfs is unavailable (Mac, CI,
single-socket boxes) — matches pre-v0.5.132 behavior on lean
hosts.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _patch_sysfs(numa_node, cpulist):
    """Patch the two sysfs files the NUMA helper reads."""
    files = [mock_open(read_data=str(numa_node)).return_value,
             mock_open(read_data=cpulist).return_value]
    m = mock_open()
    m.side_effect = files
    return patch("builtins.open", m)


# ----- _numa_cpulist_for_pci helper --------------------------------------

def test_cpulist_dense_range():
    """srv06 NUMA node 0 SMT-off shape: '0-15' → [0..15]."""
    from run_tgen_server import _numa_cpulist_for_pci
    with _patch_sysfs(0, "0-15"):
        assert _numa_cpulist_for_pci("0000:2b:00.0") == list(range(16))


def test_cpulist_smt_interleaved():
    """srv06 actual shape with SMT-on: '0-15,32-47' → first 16
    cores + their SMT siblings. The full 32-cpu list is returned
    sorted: [0,1,...,15,32,33,...,47]."""
    from run_tgen_server import _numa_cpulist_for_pci
    with _patch_sysfs(0, "0-15,32-47"):
        result = _numa_cpulist_for_pci("0000:2b:00.0")
        assert result == list(range(16)) + list(range(32, 48))


def test_cpulist_single_cpus():
    """Mixed format with single CPUs and ranges."""
    from run_tgen_server import _numa_cpulist_for_pci
    with _patch_sysfs(0, "0,1,2,3"):
        assert _numa_cpulist_for_pci("0000:2b:00.0") == [0, 1, 2, 3]
    with _patch_sysfs(0, "0-3,8,10"):
        assert _numa_cpulist_for_pci("0000:2b:00.0") == [0, 1, 2, 3, 8, 10]


def test_cpulist_returns_sorted():
    """Defensive: even if sysfs gave an unsorted list, we sort it
    so lcore picking is deterministic."""
    from run_tgen_server import _numa_cpulist_for_pci
    with _patch_sysfs(0, "32-47,0-15"):
        assert _numa_cpulist_for_pci("0000:2b:00.0") == \
            list(range(16)) + list(range(32, 48))


def test_cpulist_single_socket_returns_none():
    """numa_node = -1 → no NUMA → None."""
    from run_tgen_server import _numa_cpulist_for_pci
    with _patch_sysfs(-1, "0-31"):
        assert _numa_cpulist_for_pci("0000:2b:00.0") is None


def test_cpulist_missing_sysfs_returns_none():
    """Mac / CI — no /sys/bus/pci → OSError → None."""
    from run_tgen_server import _numa_cpulist_for_pci
    assert _numa_cpulist_for_pci("0000:ff:ff.7") is None


def test_numa_cores_count_consistent_with_cpulist():
    """The count helper must agree with the cpulist length so the
    cap calc and the lcore picker can't disagree."""
    from run_tgen_server import _numa_cores_for_pci, _numa_cpulist_for_pci
    with _patch_sysfs(0, "0-15,32-47"):
        assert _numa_cores_for_pci("0000:2b:00.0") == 32
    with _patch_sysfs(0, "0-15,32-47"):
        assert len(_numa_cpulist_for_pci("0000:2b:00.0")) == 32


# ----- _pick_rx_lcores function ------------------------------------------

def test_pick_lcores_uses_numa_when_available():
    """srv06: NIC on node 0, cpulist '0-15,32-47'. For 16 queues
    we need 17 lcores. Picker returns first 17 from the NUMA
    cpulist — NOT range(17) which would have included lcore 16
    (on the WRONG NUMA node)."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        result = _pick_rx_lcores("0000:2b:00.0", queue_count=16)
    expected = ",".join(str(i) for i in range(16)) + ",32"
    assert result == expected, (
        f"For 16 queues the picker must select 17 lcores all on "
        f"the NIC's NUMA node. Got: {result!r}"
    )


def test_pick_lcores_falls_back_to_range_when_no_numa():
    """No NUMA info → range(needed). Matches pre-v0.5.132 / lean
    host behavior — no regression."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci", return_value=None):
        assert _pick_rx_lcores("0000:2b:00.0", queue_count=8) == \
            "0,1,2,3,4,5,6,7,8"


def test_pick_lcores_falls_back_when_numa_too_small():
    """If the NIC's NUMA node has FEWER cores than we need, fall
    back to range(needed). Better to span NUMA than to under-
    provision lcores (we'd hit pps loss for sure with too few)."""
    from run_tgen_server import _pick_rx_lcores
    # NUMA has 4 cores, we need 9 lcores for 8 queues
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=[0, 1, 2, 3]):
        result = _pick_rx_lcores("0000:2b:00.0", queue_count=8)
    assert result == "0,1,2,3,4,5,6,7,8"


def test_pick_lcores_small_request_fits_first_chunk():
    """For 2 queues we need 3 lcores. Picker should grab the first
    3 from the NUMA cpulist (which are the contiguous cores at
    the low end of the node)."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        assert _pick_rx_lcores("0000:2b:00.0", queue_count=2) == "0,1,2"


def test_pick_lcores_node1_nic_uses_high_cpus():
    """Symmetric check: if the NIC is on NUMA node 1, we'd pick
    lcores from '16-31,48-63'. Don't hardcode an assumption about
    node 0."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16, 32)) + list(range(48, 64))):
        result = _pick_rx_lcores("0000:5e:00.0", queue_count=16)
    # First 17 from [16..31] + [48..63]: 16..31 then 48
    expected = ",".join(str(i) for i in range(16, 32)) + ",48"
    assert result == expected


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


def _capture_kwargs(stream_data):
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    captured = {}
    fake_reg = MagicMock()
    fake_reg.start.side_effect = lambda **kw: captured.update(kw) or {"pid": 1}
    fake_reg._handles = {}
    with patch("utils.nic_counters.iface_to_pci_bdf",
               return_value="0000:2b:00.0"), \
         patch("utils.dpdk_rx_manager.registry",
               MagicMock(return_value=fake_reg)):
        _maybe_start_dpdk_rx_for_stream(
            stream_id="s1", rx_interface="ens2f0np0",
            stream_data=stream_data,
        )
    return captured


def test_line_rate_e2e_picks_node0_lcores_on_srv06_shape():
    """The actual srv06 case at line rate: 16 queues, lcores must
    all be on NUMA node 0 (no lcore 16, which is on node 1).
    Pre-fix saw lcores 0..16 with lcore 16 on the wrong node →
    50% per-queue throughput drop."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0))
    assert kw["rx_queues"] == 16
    expected = ",".join(str(i) for i in range(16)) + ",32"
    assert kw["lcores"] == expected, (
        f"v0.5.132: lcores must come from NIC's NUMA cpulist, "
        f"not range(N). Got: {kw['lcores']!r}"
    )
    # Critical assertion: every selected lcore is on node 0.
    node0 = set(range(16)) | set(range(32, 48))
    for lc in kw["lcores"].split(","):
        assert int(lc) in node0, (
            f"lcore {lc} crosses NUMA boundaries — v0.5.132 fix "
            f"didn't reach the autoscaler"
        )


def test_line_rate_e2e_fallback_unchanged():
    """Mac / CI test runners: no NUMA → fallback to range(cap+1).
    Confirm the v0.5.131 → v0.5.132 transition didn't change
    behavior on hosts without sysfs."""
    with patch("run_tgen_server._numa_cpulist_for_pci", return_value=None):
        kw = _capture_kwargs(_stream(0))
    assert kw["rx_queues"] == 8
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8"


def test_explicit_override_unaffected_by_v05132():
    """v0.5.132 only touches the line-rate auto-scale branch.
    Explicit operator overrides still get the old `range(rxq+1)`
    behavior — not regressed, but also not yet NUMA-aware
    (deliberate: operators with explicit lcores know best)."""
    kw = _capture_kwargs(_stream(0, rx_queues=4))
    assert kw["rx_queues"] == 4
    assert kw["lcores"] == "0,1,2,3,4"
