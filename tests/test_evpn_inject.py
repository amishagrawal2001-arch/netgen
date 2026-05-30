"""Tests for utils.evpn_inject — EVPN Type-2 bulk injection (v0.2.62).

Three layers:
  * pure helpers (MAC/IP range arithmetic, command-list builders) — no
    subprocess, deterministic;
  * `inject_type2` / `clear_type2` with a fake ``run`` so we can pin
    the kernel-command stream, the registry round-trip, and the
    partial-failure result shape — without touching the host;
  * a few rejection cases the Flask route relies on for 400s.
"""

from types import SimpleNamespace

import pytest

from utils import evpn_inject as ei


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test gets an empty in-process injection registry — otherwise
    list-injections and clear-by-id tests would see leftovers from a
    previous run."""
    ei._reset_registry_for_tests()
    yield
    ei._reset_registry_for_tests()


def _fake_run(seq, capture=None):
    """Return a ``run`` callable that yields ``CompletedProcess``-shaped
    results from ``seq`` in order. Each item is (returncode, stderr).
    ``capture`` (a list) records every argv it was called with."""
    it = iter(seq)
    capture = capture if capture is not None else []
    def run(argv):
        capture.append(list(argv))
        rc, stderr = next(it)
        return SimpleNamespace(returncode=rc, stderr=stderr)
    return run, capture


# ───────────────────────────────────────────── MAC / IP range helpers
def test_mac_to_int_and_back_round_trips():
    assert ei.mac_to_int("00:00:00:00:00:00") == 0
    assert ei.mac_to_int("ff:ff:ff:ff:ff:ff") == (1 << 48) - 1
    for mac in ("aa:bb:cc:dd:ee:01", "00:11:22:33:44:55", "fe-dc-ba-98-76-54"):
        n = ei.mac_to_int(mac)
        assert ei.int_to_mac(n) == mac.replace("-", ":").lower()


def test_mac_to_int_rejects_malformed():
    for bad in ("aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:ff:00", "zz:bb:cc:dd:ee:ff",
                "aa:bb:cc:dd:ee:1ff", "", ":::::"):
        with pytest.raises(ValueError):
            ei.mac_to_int(bad)


def test_generate_mac_range_returns_consecutive_macs():
    macs = ei.generate_mac_range("aa:bb:cc:00:00:fe", 4)
    assert macs == [
        "aa:bb:cc:00:00:fe",
        "aa:bb:cc:00:00:ff",
        "aa:bb:cc:00:01:00",   # carry crosses the byte boundary
        "aa:bb:cc:00:01:01",
    ]


def test_generate_mac_range_zero_count_yields_empty():
    assert ei.generate_mac_range("aa:bb:cc:dd:ee:01", 0) == []
    assert ei.generate_mac_range("aa:bb:cc:dd:ee:01", -3) == []


def test_generate_ip_range_returns_consecutive_ipv4s():
    ips = ei.generate_ip_range("10.0.0.254", 4)
    assert ips == ["10.0.0.254", "10.0.0.255", "10.0.1.0", "10.0.1.1"]


# ─────────────────────────────────────────── command-list builders
def test_build_inject_commands_mac_only_emits_only_bridge_fdb():
    """No IP → no `ip neigh add` should be issued for that entry."""
    cmds = ei.build_inject_commands(
        "vxlan100", [("aa:bb:cc:00:00:01", None)],
    )
    assert cmds == [
        ["bridge", "fdb", "append", "aa:bb:cc:00:00:01",
         "dev", "vxlan100", "master", "self", "static"],
    ]


def test_build_inject_commands_mac_plus_ip_emits_both():
    cmds = ei.build_inject_commands(
        "vxlan100", [("aa:bb:cc:00:00:01", "10.0.0.1")],
    )
    assert cmds == [
        ["bridge", "fdb", "append", "aa:bb:cc:00:00:01",
         "dev", "vxlan100", "master", "self", "static"],
        ["ip", "neigh", "add", "10.0.0.1", "lladdr", "aa:bb:cc:00:00:01",
         "dev", "vxlan100", "nud", "noarp"],
    ]


def test_build_inject_commands_attaches_remote_vtep_when_set():
    cmds = ei.build_inject_commands(
        "vxlan100",
        [("aa:bb:cc:00:00:01", None)],
        remote_vtep_ip="192.0.2.5",
    )
    assert cmds[0][-2:] == ["dst", "192.0.2.5"]


def test_build_inject_commands_uses_l3_iface_for_neigh():
    """ip-neigh entries must land on the SVI / bridge interface, not
    on the VXLAN interface itself."""
    cmds = ei.build_inject_commands(
        "vxlan100",
        [("aa:bb:cc:00:00:01", "10.0.0.1")],
        l3_iface="br100",
    )
    # The neigh command's `dev` arg must be the L3 iface.
    assert cmds[1][cmds[1].index("dev") + 1] == "br100"
    # The FDB command's `dev` arg stays the VXLAN iface.
    assert cmds[0][cmds[0].index("dev") + 1] == "vxlan100"


def test_build_clear_commands_is_inverse_order():
    """Clear order is neigh-then-fdb — neigh entry references the MAC
    that fdb holds, so neigh must go first per kernel ordering."""
    cmds = ei.build_clear_commands(
        "vxlan100", [("aa:bb:cc:00:00:01", "10.0.0.1")],
    )
    assert cmds == [
        ["ip", "neigh", "del", "10.0.0.1", "dev", "vxlan100"],
        ["bridge", "fdb", "del", "aa:bb:cc:00:00:01", "dev", "vxlan100"],
    ]


# ──────────────────────────────────────── inject_type2 / clear_type2
def test_inject_type2_runs_2N_commands_for_mac_plus_ip():
    """N entries with IPs → 2N kernel commands (1 fdb + 1 neigh each)."""
    run, captured = _fake_run([(0, "")] * 4)
    result = ei.inject_type2(
        "vxlan100", base_mac="aa:bb:cc:00:00:01",
        count=2, base_ip="10.0.0.1",
        remote_vtep_ip="192.0.2.5", run=run,
    )
    assert result["ok_count"] == 4
    assert result["failed_count"] == 0
    assert len(captured) == 4
    # Verify the MACs increment and the IPs increment in lockstep.
    macs = [c[3] for c in captured if c[0:2] == ["bridge", "fdb"]]
    ips  = [c[3] for c in captured if c[0:2] == ["ip", "neigh"]]
    assert macs == ["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"]
    assert ips  == ["10.0.0.1",          "10.0.0.2"]
    # And the inject got registered for later clear.
    assert any(i["inject_id"] == result["inject_id"]
               for i in ei.list_active_injections())


def test_inject_type2_mac_only_runs_N_commands():
    """No base_ip → only N bridge-fdb commands."""
    run, captured = _fake_run([(0, "")] * 3)
    result = ei.inject_type2(
        "vxlan100", base_mac="aa:bb:cc:00:00:01",
        count=3, run=run,
    )
    assert result["ok_count"] == 3
    assert len(captured) == 3
    assert all(c[0:2] == ["bridge", "fdb"] for c in captured)


def test_inject_type2_partial_failure_records_each_error():
    """Some commands fail (returncode != 0), others succeed. Result
    surfaces both counts AND the per-command failure details so the
    operator can see which MAC was bad."""
    run, _ = _fake_run([
        (0, ""),                          # mac 1 fdb OK
        (2, "RTNETLINK answers: File exists"),   # mac 1 neigh dup
        (0, ""),                          # mac 2 fdb OK
        (0, ""),                          # mac 2 neigh OK
    ])
    result = ei.inject_type2(
        "vxlan100", base_mac="aa:bb:cc:00:00:01",
        count=2, base_ip="10.0.0.1", run=run,
    )
    assert result["ok_count"] == 3
    assert result["failed_count"] == 1
    assert result["errors"][0]["returncode"] == 2
    assert "File exists" in result["errors"][0]["stderr"]


def test_inject_type2_subprocess_exception_recorded_not_raised():
    """If subprocess itself blows up (timeout, ENOENT for `bridge`),
    we don't propagate — we accumulate it as an error so other entries
    still get tried."""
    calls = []
    def boom(argv):
        calls.append(argv)
        raise FileNotFoundError("bridge: command not found")
    result = ei.inject_type2(
        "vxlan100", base_mac="aa:bb:cc:00:00:01",
        count=2, run=boom,
    )
    # 2 attempted, all failed via exception, none ok.
    assert result["ok_count"] == 0
    assert result["failed_count"] == 2
    assert all(e["returncode"] == -1 for e in result["errors"])
    assert all("FileNotFoundError" in e["stderr"] for e in result["errors"])


def test_inject_type2_rejects_zero_count():
    with pytest.raises(ValueError, match="count must be > 0"):
        ei.inject_type2("vxlan100", base_mac="aa:bb:cc:00:00:01",
                        count=0, run=lambda *a: None)


def test_clear_type2_drops_record_even_when_kernel_complains():
    """`ip neigh del`/`bridge fdb del` commonly return errors when the
    entry is already gone. Clear must STILL drop the in-process record
    so a follow-up inject can re-use the resources cleanly."""
    run, _ = _fake_run([(0, "")] * 4)   # for the initial inject
    inj = ei.inject_type2(
        "vxlan100", base_mac="aa:bb:cc:00:00:01",
        count=2, base_ip="10.0.0.1", run=run,
    )
    assert len(ei.list_active_injections()) == 1
    # Now clear with the kernel returning failure on every del.
    run2, _ = _fake_run([(2, "No such file or directory")] * 4)
    res = ei.clear_type2(inj["inject_id"], run=run2)
    assert res["failed_count"] == 4
    # Critically: the record is gone regardless of kernel errors.
    assert ei.list_active_injections() == []


def test_clear_type2_unknown_id_returns_warning_not_error():
    """Stale inject_id (server restart, double-clear) → warning, not
    500. The route should still return 200."""
    res = ei.clear_type2("not-a-real-id", run=lambda *a: None)
    assert res["ok_count"] == 0
    assert res["failed_count"] == 0
    assert "warning" in res


def test_list_active_injections_reflects_inject_and_clear():
    run, _ = _fake_run([(0, "")] * 6)
    a = ei.inject_type2("vxlan100", "aa:bb:cc:00:00:01", 1, run=run)
    b = ei.inject_type2("vxlan200", "aa:bb:cc:00:01:00", 2, run=run)
    items = ei.list_active_injections()
    assert {i["inject_id"] for i in items} == {a["inject_id"], b["inject_id"]}
    # Each item carries iface + count for the GUI table.
    ifaces = {i["iface"] for i in items}
    assert ifaces == {"vxlan100", "vxlan200"}
    ei.clear_type2(a["inject_id"], run=run)
    assert [i["inject_id"] for i in ei.list_active_injections()] == [b["inject_id"]]
