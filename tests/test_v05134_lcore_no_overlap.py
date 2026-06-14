"""v0.5.134: rx_worker lcore picker excludes lcores held by other workers.

After v0.5.131-133, srv06 multi-stream still pinned both rx_workers
to the same lcore set `0,1,...,16`. Two DPDK processes sharing
physical CPUs means the Linux scheduler ping-pongs them — every
context switch reaches into the wrong PMD hot path. Operator saw
rx_rate collapse to 14 Mpps with imissed=5.8B even though tx was
firing at 21.87 Mpps.

v0.5.134 reads each registered rx_worker's `-l` arg from its cmd
list and excludes those lcores when picking for the new stream.
Two streams on different PCI BDFs (e.g. f0np0 + f1np1) get
disjoint lcore sets — each PMD owns its CPUs uncontested.

Fallback contract: when sysfs is unavailable OR the cpulist
minus the reserved set isn't large enough, fall back to
`range(needed)` — matches pre-v0.5.134 behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ----- _pick_rx_lcores with reserved set ---------------------------------

def test_pick_skips_reserved_cores():
    """Reserved set {0..7} + need 5 lcores: picker returns {8..12}
    (or equivalent) from the NUMA cpulist."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        result = _pick_rx_lcores(
            "0000:2b:00.0", queue_count=4,
            reserved={0, 1, 2, 3, 4, 5, 6, 7},
        )
    # Need 5 lcores; skip 0-7; first 5 from {8..15, 32..47} = 8,9,10,11,12
    assert result == "8,9,10,11,12"


def test_pick_skips_spans_numa_chunks():
    """When the reserved set eats into both contiguous + SMT-sibling
    ranges, picker continues across chunks."""
    from run_tgen_server import _pick_rx_lcores
    # NUMA cpulist: 0-15 + 32-47. Reserve all of 0-15 + half of 32-47.
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        result = _pick_rx_lcores(
            "0000:2b:00.0", queue_count=3,
            reserved=set(range(16)) | set(range(32, 40)),
        )
    # 0-15 + 32-39 reserved → first 4 from {40..47} = 40,41,42,43
    assert result == "40,41,42,43"


def test_pick_falls_back_when_not_enough_free_cores():
    """If excluding reserved leaves fewer cores than needed, fall
    back to range(needed). Better to overlap than to under-
    provision lcores."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=[0, 1, 2, 3]):
        # need 5 lcores (4 queues + 1 main), reserved={0,1,2}, only
        # {3} left, but need 5 → fall back
        result = _pick_rx_lcores(
            "0000:2b:00.0", queue_count=4,
            reserved={0, 1, 2},
        )
    assert result == "0,1,2,3,4"


def test_pick_no_reserved_set_unchanged():
    """v0.5.134 is a pure addition. None / empty reserved → same
    behavior as v0.5.132."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        a = _pick_rx_lcores("0000:2b:00.0", queue_count=8)
        b = _pick_rx_lcores("0000:2b:00.0", queue_count=8, reserved=None)
        c = _pick_rx_lcores("0000:2b:00.0", queue_count=8, reserved=set())
    assert a == b == c == "0,1,2,3,4,5,6,7,8"


def test_pick_fallback_path_ignores_reserved():
    """When NUMA info is absent, fallback is range(N) regardless of
    reserved (no notion of which lcore is which without sysfs)."""
    from run_tgen_server import _pick_rx_lcores
    with patch("run_tgen_server._numa_cpulist_for_pci", return_value=None):
        result = _pick_rx_lcores(
            "0000:ff:ff.7", queue_count=4,
            reserved={0, 1, 2, 3},  # ignored on fallback
        )
    assert result == "0,1,2,3,4"


# ----- _collect_used_lcores helper ---------------------------------------

def _fake_handle(lcores: str):
    """Build a mock RxHandle-like object exposing only `cmd`."""
    h = MagicMock()
    h.cmd = ["/usr/local/bin/rx_worker", "-l", lcores, "-n", "4",
             "-a", "0000:2b:00.0", "--", "--stream-id", "s",
             "--rx-queues", "8"]
    return h


def test_collect_empty_when_no_handles():
    """No streams running → no reserved lcores."""
    from run_tgen_server import _collect_used_lcores
    fake_reg = MagicMock()
    fake_reg._handles = {}
    with patch("utils.dpdk_rx_manager.registry", return_value=fake_reg):
        assert _collect_used_lcores() == set()


def test_collect_single_handle():
    """One stream up using lcores 0-8 → reserve = {0..8}."""
    from run_tgen_server import _collect_used_lcores
    fake_reg = MagicMock()
    fake_reg._handles = {"s1": _fake_handle("0,1,2,3,4,5,6,7,8")}
    with patch("utils.dpdk_rx_manager.registry", return_value=fake_reg):
        assert _collect_used_lcores() == set(range(9))


def test_collect_union_across_handles():
    """Multiple streams: union all their lcore sets."""
    from run_tgen_server import _collect_used_lcores
    fake_reg = MagicMock()
    fake_reg._handles = {
        "s1": _fake_handle("0,1,2,3"),
        "s2": _fake_handle("8,9,10,11"),
    }
    with patch("utils.dpdk_rx_manager.registry", return_value=fake_reg):
        assert _collect_used_lcores() == {0, 1, 2, 3, 8, 9, 10, 11}


def test_collect_handles_lookup_failure():
    """Defensive: when registry import fails, return empty set so
    the autoscaler treats this as a fresh allocation."""
    from run_tgen_server import _collect_used_lcores
    with patch("utils.dpdk_rx_manager.registry",
               side_effect=RuntimeError("no registry in test")):
        assert _collect_used_lcores() == set()


def test_collect_handles_malformed_cmd():
    """Handle without `cmd` attr or with garbage entries shouldn't
    crash — skip and continue."""
    from run_tgen_server import _collect_used_lcores
    weird = MagicMock()
    weird.cmd = None
    bogus = MagicMock()
    bogus.cmd = ["rx_worker", "-l", "not-numeric"]
    good = _fake_handle("4,5,6")
    fake_reg = MagicMock()
    fake_reg._handles = {"a": weird, "b": bogus, "c": good}
    with patch("utils.dpdk_rx_manager.registry", return_value=fake_reg):
        # Only the good handle contributes
        assert _collect_used_lcores() == {4, 5, 6}


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


def _capture_kwargs(stream_data, existing_handles=None):
    """Patch the registry to simulate `existing_handles` streams
    already running. Returns the kwargs the autoscaler would pass
    to rx_registry.start() for the new stream."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    existing_handles = existing_handles or {}
    captured = {}
    fake_reg = MagicMock()
    fake_reg.start.side_effect = lambda **kw: captured.update(kw) or {"pid": 1}
    fake_reg._handles = existing_handles
    with patch("utils.nic_counters.iface_to_pci_bdf",
               return_value="0000:2b:00.0"), \
         patch("utils.dpdk_rx_manager.registry",
               MagicMock(return_value=fake_reg)):
        _maybe_start_dpdk_rx_for_stream(
            stream_id="new_stream", rx_interface="ens2f0np0",
            stream_data=stream_data,
        )
    return captured


def test_e2e_second_stream_picks_disjoint_lcores():
    """srv06 reproduction: 1st stream took lcores 0..8 (the v0.5.133
    backoff gave it 8 queues). 2nd stream now starts. The picker
    must skip 0..8 → second stream uses 9..16 (still on NUMA
    node 0)."""
    existing = {"existing": _fake_handle("0,1,2,3,4,5,6,7,8")}
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), existing_handles=existing)
    # v0.5.133 backoff: 16 // (1 + 1) = 8 queues for new stream.
    # v0.5.134: skip lcores 0-8 → pick from {9..15, 32..47}.
    # Need 9 lcores (8 queues + 1 main) → "9,10,11,12,13,14,15,32,33".
    assert kw["rx_queues"] == 8
    assert kw["lcores"] == "9,10,11,12,13,14,15,32,33"
    # Confirm zero overlap with the existing stream's lcores
    new_set = set(int(x) for x in kw["lcores"].split(","))
    assert new_set & {0, 1, 2, 3, 4, 5, 6, 7, 8} == set(), (
        "v0.5.134 fix: no lcore overlap between concurrent rx_workers"
    )


def test_e2e_first_stream_unchanged():
    """No reserved lcores → behavior identical to v0.5.132/133.
    First stream gets the full cap from the start of the cpulist."""
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), existing_handles={})
    assert kw["rx_queues"] == 16
    assert kw["lcores"] == "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,32"


def test_e2e_third_stream_disjoint_from_both():
    """3 streams running. 1st: 0..7, 2nd: 8..15. 3rd starts. Picker
    must skip both reserved sets → 3rd lands on SMT siblings."""
    existing = {
        "s1": _fake_handle("0,1,2,3,4,5,6,7"),
        "s2": _fake_handle("8,9,10,11,12,13,14,15"),
    }
    with patch("run_tgen_server._numa_cpulist_for_pci",
               return_value=list(range(16)) + list(range(32, 48))):
        kw = _capture_kwargs(_stream(0), existing_handles=existing)
    # 3 existing handles → v0.5.133 cap = 16 // 3 = 5 → 6 lcores.
    # 0-15 reserved → pick from 32..47, first 6.
    new_set = set(int(x) for x in kw["lcores"].split(","))
    assert new_set & set(range(16)) == set()
    assert new_set <= set(range(32, 48))
