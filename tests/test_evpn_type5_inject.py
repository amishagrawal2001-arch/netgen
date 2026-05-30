"""Tests for EVPN Type-5 (IP prefix) bulk injection (v0.2.66).

Companion to ``tests/test_evpn_inject.py`` (which covers Type-2). Same
three layers:
  * pure helpers (prefix range, command-list builders);
  * inject / clear with a fake ``run`` so we pin the kernel-command
    stream + the partial-failure result shape;
  * cross-kind safety — clear_type2 must refuse a type-5 record and
    vice versa (the registry now mixes both).
"""

from types import SimpleNamespace

import pytest

from utils import evpn_inject as ei


@pytest.fixture(autouse=True)
def _clean_registry():
    ei._reset_registry_for_tests()
    yield
    ei._reset_registry_for_tests()


def _fake_run(seq, capture=None):
    it = iter(seq)
    capture = capture if capture is not None else []
    def run(argv):
        capture.append(list(argv))
        rc, stderr = next(it)
        return SimpleNamespace(returncode=rc, stderr=stderr)
    return run, capture


# ─────────────────────────────────────── generate_prefix_range_v4
def test_prefix_range_aligned_consecutive_24s():
    prefixes = ei.generate_prefix_range_v4("10.100.0.0", 24, 4)
    assert prefixes == [
        "10.100.0.0/24",
        "10.100.1.0/24",
        "10.100.2.0/24",
        "10.100.3.0/24",
    ]


def test_prefix_range_crosses_octet_boundary():
    """100 /24s starting at 10.100.0.0 must walk to 10.100.99.0."""
    prefixes = ei.generate_prefix_range_v4("10.100.0.0", 24, 100)
    assert prefixes[0]  == "10.100.0.0/24"
    assert prefixes[99] == "10.100.99.0/24"


def test_prefix_range_works_for_28():
    """Each /28 has 16 addresses; 5 of them at 10.0.0.0 → 10.0.0.{0,16,32,48,64}/28."""
    prefixes = ei.generate_prefix_range_v4("10.0.0.0", 28, 5)
    assert prefixes == [
        "10.0.0.0/28", "10.0.0.16/28",
        "10.0.0.32/28", "10.0.0.48/28", "10.0.0.64/28",
    ]


def test_prefix_range_zero_count_yields_empty():
    assert ei.generate_prefix_range_v4("10.0.0.0", 24, 0) == []


def test_prefix_range_misaligned_base_raises():
    """A /24 starting at 10.0.0.5 is malformed — the kernel would
    accept it (mask off the host bits) but it's almost certainly a
    user mistake. Refuse up front so the operator sees what's wrong."""
    with pytest.raises(ValueError, match="not aligned"):
        ei.generate_prefix_range_v4("10.0.0.5", 24, 3)


def test_prefix_range_out_of_range_prefix_len_raises():
    with pytest.raises(ValueError, match="prefix_len must be 1..32"):
        ei.generate_prefix_range_v4("10.0.0.0", 0, 1)
    with pytest.raises(ValueError, match="prefix_len must be 1..32"):
        ei.generate_prefix_range_v4("10.0.0.0", 33, 1)


# ─────────────────────────────────────── command-list builders
def test_build_route_inject_minimal():
    cmds = ei.build_route_inject_commands(
        ["10.0.0.0/24", "10.0.1.0/24"], dev="eth0",
    )
    assert cmds == [
        ["ip", "route", "add", "10.0.0.0/24", "dev", "eth0"],
        ["ip", "route", "add", "10.0.1.0/24", "dev", "eth0"],
    ]


def test_build_route_inject_with_gateway_and_vrf():
    cmds = ei.build_route_inject_commands(
        ["10.0.0.0/24"], dev="eth0",
        gateway="192.168.1.1", vrf_table=1001,
    )
    assert cmds == [["ip", "route", "add", "10.0.0.0/24",
                    "via", "192.168.1.1", "dev", "eth0",
                    "table", "1001"]]


def test_build_route_clear_omits_via_and_keeps_table():
    """Delete matches by prefix + table; via is intentionally absent
    (kernel rejects `del … via …` when the route was added without
    explicit nexthop)."""
    cmds = ei.build_route_clear_commands(
        ["10.0.0.0/24"], dev="eth0", vrf_table=1001,
    )
    assert cmds == [["ip", "route", "del", "10.0.0.0/24",
                    "dev", "eth0", "table", "1001"]]


def test_build_route_clear_omits_dev_when_none():
    cmds = ei.build_route_clear_commands(["10.0.0.0/24"], vrf_table=1001)
    assert cmds == [["ip", "route", "del", "10.0.0.0/24",
                    "table", "1001"]]


# ─────────────────────────────────────── inject_type5 / clear_type5
def test_inject_type5_runs_one_command_per_prefix():
    run, captured = _fake_run([(0, "")] * 3)
    result = ei.inject_type5(
        dev="eth0", base_prefix="10.100.0.0", prefix_len=24,
        count=3, run=run,
    )
    assert result["ok_count"] == 3
    assert result["failed_count"] == 0
    assert result["count"] == 3
    assert len(captured) == 3
    # Every command is an `ip route add`.
    assert all(c[:3] == ["ip", "route", "add"] for c in captured)
    # Result lists the actual prefixes (useful for GUI / scripts).
    assert result["prefixes"] == [
        "10.100.0.0/24", "10.100.1.0/24", "10.100.2.0/24",
    ]


def test_inject_type5_gateway_and_vrf_threaded_through():
    run, captured = _fake_run([(0, "")] * 1)
    ei.inject_type5(
        dev="eth0", base_prefix="10.100.0.0", prefix_len=24, count=1,
        gateway="192.168.1.1", vrf_table=1001, run=run,
    )
    assert captured[0] == [
        "ip", "route", "add", "10.100.0.0/24",
        "via", "192.168.1.1", "dev", "eth0", "table", "1001",
    ]


def test_inject_type5_partial_failure():
    run, _ = _fake_run([
        (0, ""),                              # prefix 1 OK
        (2, "RTNETLINK: File exists"),       # prefix 2 dup
        (0, ""),                              # prefix 3 OK
    ])
    result = ei.inject_type5(
        dev="eth0", base_prefix="10.100.0.0", prefix_len=24,
        count=3, run=run,
    )
    assert result["ok_count"] == 2
    assert result["failed_count"] == 1
    assert "File exists" in result["errors"][0]["stderr"]


def test_inject_type5_rejects_zero_count():
    with pytest.raises(ValueError, match="count must be > 0"):
        ei.inject_type5("eth0", "10.0.0.0", 24, 0, run=lambda *a: None)


def test_clear_type5_drops_record_even_when_kernel_complains():
    run, _ = _fake_run([(0, "")] * 2)
    inj = ei.inject_type5("eth0", "10.0.0.0", 24, 2, run=run)
    assert len(ei.list_active_injections()) == 1
    # Now clear with every `del` failing — record must still get dropped.
    run2, _ = _fake_run([(2, "RTNETLINK: No such process")] * 2)
    res = ei.clear_type5(inj["inject_id"], run=run2)
    assert res["failed_count"] == 2
    assert ei.list_active_injections() == []


def test_clear_type5_unknown_id_returns_warning_not_error():
    res = ei.clear_type5("not-a-real-id", run=lambda *a: None)
    assert "warning" in res


# ─────────────────────────────────── cross-kind safety + listing
def test_list_active_injections_includes_kind_for_both():
    run, _ = _fake_run([(0, "")] * 3)
    t2 = ei.inject_type2("vxlan100", "aa:bb:cc:00:00:01", 1, run=run)
    t5 = ei.inject_type5("eth0", "10.100.0.0", 24, 2, run=run)
    items = {i["inject_id"]: i for i in ei.list_active_injections()}
    assert items[t2["inject_id"]]["kind"] == "type2"
    assert items[t5["inject_id"]]["kind"] == "type5"
    # Cross-kind alias: type-5's `iface` column carries `dev` so the
    # existing v0.2.63 GUI table renders it cleanly.
    assert items[t5["inject_id"]]["iface"] == "eth0"
    assert items[t5["inject_id"]]["count"] == 2


def test_clear_type2_refuses_type5_record_and_leaves_it_registered():
    """A type-5 record passed to /api/evpn/type2/clear must NOT be
    deleted — the wrong cleaner would build wrong commands and leak
    kernel state. Put the record back and return a warning so the
    caller can route to /api/evpn/type5/clear."""
    run, _ = _fake_run([(0, "")] * 1)
    t5 = ei.inject_type5("eth0", "10.100.0.0", 24, 1, run=run)
    res = ei.clear_type2(t5["inject_id"], run=lambda *a: None)
    assert "warning" in res
    assert "type5" in res["warning"]
    # And the record is still there for the right cleaner to find.
    assert any(i["inject_id"] == t5["inject_id"]
               for i in ei.list_active_injections())


def test_clear_type5_refuses_type2_record_and_leaves_it_registered():
    """The mirror test — protects against a Type-2 record being eaten
    by /api/evpn/type5/clear by mistake."""
    run, _ = _fake_run([(0, "")] * 1)
    t2 = ei.inject_type2("vxlan100", "aa:bb:cc:00:00:01", 1, run=run)
    res = ei.clear_type5(t2["inject_id"], run=lambda *a: None)
    assert "warning" in res
    assert "type2" in res["warning"]
    assert any(i["inject_id"] == t2["inject_id"]
               for i in ei.list_active_injections())
