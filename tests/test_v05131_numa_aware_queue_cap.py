"""v0.5.131: line-rate rx_queue cap scales with NIC's NUMA node cores.

After v0.5.128 (count flags) + v0.5.130 (dialog norm) shipped, the
srv06 UDP stream still had a ~28% TX/RX gap: tx_rate=24 Mpps but
rx_rate=17 Mpps. Operator's 5-tuple cycling at count=128 was
spreading RSS across all 8 rx_queues evenly — the bottleneck had
moved to per-lcore RX throughput.

srv06 has 64 cores / 2 NUMA nodes / 16 cores per socket. The
rx_worker was capped at 8 queues + 9 lcores even though the
NIC's local NUMA node had 16 cores to spare.

v0.5.131 lifts the line-rate bucket cap from a hard 8 to
min(16, numa_cores - 2). Lean single-NUMA hosts and sysfs read
failures fall back to 8 (matches pre-fix behavior — no regression).

Tests cover:
- The NUMA-cores reader (sysfs parsing, error paths)
- The cap function (clamps low + high, fallback on missing sysfs)
- The end-to-end autoscaler at line rate uses the computed cap
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ----- _numa_cores_for_pci helper ----------------------------------------

def _patch_sysfs(numa_node, cpulist):
    """Patch both sysfs files _numa_cores_for_pci reads. Multiple
    `open()` calls in sequence: first returns numa_node, second
    returns cpulist."""
    files = [mock_open(read_data=str(numa_node)).return_value,
             mock_open(read_data=cpulist).return_value]
    m = mock_open()
    m.side_effect = files
    return patch("builtins.open", m)


def test_numa_reader_dense_range():
    """srv06 shape: 'Core(s) per socket: 16' → NUMA node 0 has
    16 cores: cpulist '0-15'. Expect 16."""
    from run_tgen_server import _numa_cores_for_pci
    with _patch_sysfs(0, "0-15"):
        assert _numa_cores_for_pci("0000:2b:00.0") == 16


def test_numa_reader_with_smt_pairs():
    """SMT-on hosts show paired ranges: '0-15,32-47' = 16 cores +
    their 16 SMT siblings = 32 logical CPUs. We use the logical
    count (DPDK lcores ARE logical CPUs)."""
    from run_tgen_server import _numa_cores_for_pci
    with _patch_sysfs(0, "0-15,32-47"):
        assert _numa_cores_for_pci("0000:2b:00.0") == 32


def test_numa_reader_single_socket_box_returns_none():
    """Hosts without NUMA topology have numa_node = -1. Return None
    so the cap falls back to the conservative default."""
    from run_tgen_server import _numa_cores_for_pci
    with _patch_sysfs(-1, "0-7"):
        assert _numa_cores_for_pci("0000:2b:00.0") is None


def test_numa_reader_missing_sysfs_returns_none():
    """Mac / CI runners have no /sys/bus/pci. OSError is caught and
    helper returns None — autoscaler falls back to cap=8."""
    from run_tgen_server import _numa_cores_for_pci
    # No patch — real sysfs absent on the test runner (Mac/CI).
    # The bdf is valid format; the path just doesn't exist.
    assert _numa_cores_for_pci("0000:ff:ff.7") is None


def test_numa_reader_handles_garbage_numa_node():
    """A non-numeric numa_node should trigger ValueError → None."""
    from run_tgen_server import _numa_cores_for_pci
    with _patch_sysfs("not-a-number", "0-15"):
        assert _numa_cores_for_pci("0000:2b:00.0") is None


def test_numa_reader_single_cpu_chunk():
    """cpulist can be a single CPU '4' (no range), or mixed."""
    from run_tgen_server import _numa_cores_for_pci
    with _patch_sysfs(0, "0,1,2,3"):
        assert _numa_cores_for_pci("0000:2b:00.0") == 4
    with _patch_sysfs(0, "0-3,8,10"):
        assert _numa_cores_for_pci("0000:2b:00.0") == 6


# ----- _line_rate_queue_cap function -------------------------------------

def test_cap_falls_back_to_eight_when_numa_unavailable():
    """No NUMA info → conservative cap (pre-v0.5.131 behavior)."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=None):
        assert _line_rate_queue_cap("0000:2b:00.0") == 8


def test_cap_clamps_to_eight_on_lean_hosts():
    """If NUMA has 4 cores, leaving 2 for system gives 2 — but cap
    is min 8 so the auto-scaler stays useful on small boxes."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=4):
        assert _line_rate_queue_cap("0000:2b:00.0") == 8


def test_cap_uses_numa_when_cores_available():
    """srv06: 16 cores on NIC NUMA → cap = 16 - 2 = 14."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        assert _line_rate_queue_cap("0000:2b:00.0") == 14


def test_cap_hard_caps_at_sixteen():
    """rx_worker.c's MAX_RX_QUEUES = 16. Even a 48-core NUMA caps
    at 16 — going higher would crash the worker."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=48):
        assert _line_rate_queue_cap("0000:2b:00.0") == 16


def test_cap_smt_doubled_uses_full_count():
    """SMT-on srv06 reports 32 logical CPUs on the NUMA node. We
    cap at 16 (rx_worker limit) but the floor is the full count
    so we don't waste lcores."""
    from run_tgen_server import _line_rate_queue_cap
    with patch("run_tgen_server._numa_cores_for_pci", return_value=32):
        assert _line_rate_queue_cap("0000:2b:00.0") == 16


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


def _call_and_capture(stream_data):
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


def test_line_rate_autoscale_uses_numa_cap():
    """End-to-end: pps=0 (Line Rate) on a 16-NUMA-core host →
    14 queues + 15 lcores (was 8 + 9 pre-v0.5.131)."""
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        kw = _call_and_capture(_stream(0))
    assert kw["rx_queues"] == 14
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14"


def test_line_rate_autoscale_falls_back_to_eight_no_numa():
    """End-to-end fallback path: no NUMA → cap 8 (matches v0.5.127)."""
    with patch("run_tgen_server._numa_cores_for_pci", return_value=None):
        kw = _call_and_capture(_stream(0))
    assert kw["rx_queues"] == 8
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8"


def test_line_rate_autoscale_caps_at_sixteen():
    """End-to-end hard ceiling at 16 even when NUMA is huge."""
    with patch("run_tgen_server._numa_cores_for_pci", return_value=64):
        kw = _call_and_capture(_stream(0))
    assert kw["rx_queues"] == 16
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16"


def test_lower_buckets_unchanged_by_v05131():
    """v0.5.131 only touched the pps==0/>=30M bucket. The mid-rate
    buckets (6M, 18M) still pick their pre-fix queue counts so
    operators with explicit lower target_pps don't see surprise
    lcore inflation."""
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        kw = _call_and_capture(_stream(6_000_000))
    assert kw["rx_queues"] == 2
    with patch("run_tgen_server._numa_cores_for_pci", return_value=16):
        kw = _call_and_capture(_stream(18_000_000))
    assert kw["rx_queues"] == 4
