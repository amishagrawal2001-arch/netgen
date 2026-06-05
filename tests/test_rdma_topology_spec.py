"""Tests for utils/rdma_topology.py — the v0.4.0 Topology Mode
spec + expansion + aggregation helpers.

Pure functions, no Qt, no network — testable headless. The dialog
(widgets/rdma_topology_dialog.py) and the REST plumbing are tested
separately; this file pins ONLY the math/structure of the
expand_pairs() and aggregate_stats() contract."""
from __future__ import annotations

import pytest

from utils.rdma_topology import (
    DEFAULT_BASE_LISTEN_PORT,
    RdmaPairPlan,
    RdmaTopologyEndpoint,
    RdmaTopologySpec,
    SHAPE_FAN_IN,
    SHAPE_FAN_OUT,
    SHAPE_MESH,
    SHAPE_PAIRWISE,
    SHAPE_SINGLE,
    aggregate_stats,
    client_start_payload,
    expand_pairs,
    server_start_payload,
    validate_spec,
)


# Small fixture helpers
def _ep(host: str, dev: str = "mlx5_0", port: int = 1, gid: int = 3,
        label: str = None) -> RdmaTopologyEndpoint:
    return RdmaTopologyEndpoint(
        tg_url=f"http://{host}:5050", device=dev, ib_port=port,
        gid_index=gid, label=label,
    )


def _spec(shape, servers, clients, test="send_bw", opts=None, base_port=None):
    kwargs = {
        "shape": shape,
        "server_endpoints": servers,
        "client_endpoints": clients,
        "test": test,
        "workload_opts": opts or {"msg_size": 65536, "qp_count": 1, "duration": 30},
    }
    if base_port is not None:
        kwargs["base_listen_port"] = base_port
    return RdmaTopologySpec(**kwargs)


# ─────────────────────────────────── validation ────────────────────────


def test_validate_rejects_unknown_shape():
    s = _spec("bogus", [_ep("srv01")], [_ep("srv02")])
    err = validate_spec(s)
    assert err is not None
    assert "bogus" in err
    assert "shape" in err.lower()


def test_validate_rejects_empty_endpoint_lists():
    s = _spec(SHAPE_MESH, [], [_ep("srv02")])
    assert validate_spec(s) is not None
    assert "server" in (validate_spec(s) or "").lower()
    s = _spec(SHAPE_MESH, [_ep("srv01")], [])
    assert validate_spec(s) is not None


def test_validate_rejects_empty_test():
    s = _spec(SHAPE_SINGLE, [_ep("srv01")], [_ep("srv02")], test="")
    assert validate_spec(s) is not None


def test_validate_single_requires_exactly_one_each():
    # 2 servers in single → invalid
    s = _spec(SHAPE_SINGLE, [_ep("srv01"), _ep("srv02")], [_ep("srv03")])
    assert validate_spec(s) is not None
    # 2 clients in single → invalid
    s = _spec(SHAPE_SINGLE, [_ep("srv01")], [_ep("srv02"), _ep("srv03")])
    assert validate_spec(s) is not None


def test_validate_fan_in_requires_one_server():
    s = _spec(SHAPE_FAN_IN, [_ep("srv01"), _ep("srv02")], [_ep("srv03")])
    err = validate_spec(s) or ""
    assert "fan_in" in err
    assert "1 server" in err or "exactly 1" in err


def test_validate_fan_out_requires_one_client():
    s = _spec(SHAPE_FAN_OUT, [_ep("srv01")], [_ep("srv02"), _ep("srv03")])
    err = validate_spec(s) or ""
    assert "fan_out" in err


def test_validate_pairwise_requires_equal_lengths():
    s = _spec(SHAPE_PAIRWISE,
              [_ep("srv01"), _ep("srv02")],
              [_ep("srv03")])
    err = validate_spec(s) or ""
    assert "pairwise" in err.lower() or "equal" in err.lower()


def test_validate_base_port_out_of_range():
    s = _spec(SHAPE_SINGLE, [_ep("srv01")], [_ep("srv02")], base_port=80)
    assert validate_spec(s) is not None
    s = _spec(SHAPE_SINGLE, [_ep("srv01")], [_ep("srv02")], base_port=70000)
    assert validate_spec(s) is not None


def test_validate_happy_path_returns_none():
    s = _spec(SHAPE_MESH,
              [_ep("srv01"), _ep("srv02")],
              [_ep("srv03"), _ep("srv04")])
    assert validate_spec(s) is None


# ─────────────────────────────────── expansion: shape correctness ─────


def test_expand_single_emits_one_pair():
    plans = expand_pairs(_spec(SHAPE_SINGLE, [_ep("srv01")], [_ep("srv02")]))
    assert len(plans) == 1
    assert plans[0].server.tg_url == "http://srv01:5050"
    assert plans[0].client.tg_url == "http://srv02:5050"
    assert plans[0].pair_index == 0


def test_expand_fan_in_emits_one_pair_per_client():
    # 1 server, 3 clients → 3 pairs
    plans = expand_pairs(_spec(
        SHAPE_FAN_IN,
        [_ep("srv01")],
        [_ep("srv02"), _ep("srv03"), _ep("srv04")],
    ))
    assert len(plans) == 3
    # All share the same server
    assert all(p.server.tg_url == "http://srv01:5050" for p in plans)
    # Clients enumerate
    client_hosts = [p.client.tg_url for p in plans]
    assert client_hosts == [
        "http://srv02:5050", "http://srv03:5050", "http://srv04:5050",
    ]


def test_expand_fan_out_emits_one_pair_per_server():
    plans = expand_pairs(_spec(
        SHAPE_FAN_OUT,
        [_ep("srv01"), _ep("srv02"), _ep("srv03")],
        [_ep("srv04")],
    ))
    assert len(plans) == 3
    assert all(p.client.tg_url == "http://srv04:5050" for p in plans)


def test_expand_mesh_emits_cross_product():
    # 2 × 3 = 6 pairs
    plans = expand_pairs(_spec(
        SHAPE_MESH,
        [_ep("a"), _ep("b")],
        [_ep("x"), _ep("y"), _ep("z")],
    ))
    assert len(plans) == 6
    # Verify all combinations appear exactly once
    pair_set = {
        (p.server.tg_url, p.client.tg_url) for p in plans
    }
    expected = {
        ("http://a:5050", "http://x:5050"),
        ("http://a:5050", "http://y:5050"),
        ("http://a:5050", "http://z:5050"),
        ("http://b:5050", "http://x:5050"),
        ("http://b:5050", "http://y:5050"),
        ("http://b:5050", "http://z:5050"),
    }
    assert pair_set == expected


def test_expand_pairwise_emits_parallel_pairs():
    plans = expand_pairs(_spec(
        SHAPE_PAIRWISE,
        [_ep("s1"), _ep("s2"), _ep("s3")],
        [_ep("c1"), _ep("c2"), _ep("c3")],
    ))
    assert len(plans) == 3
    # Index-aligned
    assert (plans[0].server.tg_url, plans[0].client.tg_url) == \
           ("http://s1:5050", "http://c1:5050")
    assert (plans[1].server.tg_url, plans[1].client.tg_url) == \
           ("http://s2:5050", "http://c2:5050")
    assert (plans[2].server.tg_url, plans[2].client.tg_url) == \
           ("http://s3:5050", "http://c3:5050")


# ─────────────────────────────────── expansion: invariants ─────────────


def test_expand_assigns_unique_handshake_ids():
    """Every pair needs a distinct handshake_id so the broker can
    match them. UUID v4 collisions are astronomically unlikely, but
    pin the property anyway — a refactor could accidentally reuse
    one id across pairs."""
    plans = expand_pairs(_spec(
        SHAPE_MESH,
        [_ep("a"), _ep("b"), _ep("c")],
        [_ep("x"), _ep("y"), _ep("z")],
    ))
    ids = [p.handshake_id for p in plans]
    assert len(ids) == len(set(ids)), (
        f"duplicate handshake_ids: {[i for i in ids if ids.count(i) > 1]}"
    )


def test_expand_assigns_unique_listen_ports():
    """Same server endpoint participating in K pairs (FAN_IN) needs K
    DIFFERENT listen_ports — the same TG host can't bind two perftests
    to one port. Allocating by pair_index (base + index) guarantees
    uniqueness across the whole topology."""
    plans = expand_pairs(_spec(
        SHAPE_FAN_IN,
        [_ep("srv01")],
        [_ep("c1"), _ep("c2"), _ep("c3"), _ep("c4"), _ep("c5")],
    ))
    ports = [p.listen_port for p in plans]
    assert len(ports) == len(set(ports)), f"duplicate listen_ports: {ports}"
    # And they should be contiguous from the base
    assert sorted(ports) == [
        DEFAULT_BASE_LISTEN_PORT + i for i in range(5)
    ]


def test_expand_pair_indexes_are_contiguous():
    plans = expand_pairs(_spec(
        SHAPE_MESH, [_ep("a"), _ep("b")], [_ep("x"), _ep("y")],
    ))
    assert [p.pair_index for p in plans] == [0, 1, 2, 3]


def test_expand_honours_custom_base_port():
    plans = expand_pairs(_spec(
        SHAPE_FAN_IN, [_ep("a")], [_ep("x"), _ep("y")],
        base_port=22000,
    ))
    assert plans[0].listen_port == 22000
    assert plans[1].listen_port == 22001


def test_expand_raises_on_invalid_spec():
    """validate_spec() returns a message; expand_pairs() must raise
    ValueError so callers can't accidentally proceed with a broken
    spec."""
    bad = _spec(SHAPE_SINGLE,
                [_ep("a"), _ep("b")], [_ep("c")])  # too many servers
    with pytest.raises(ValueError):
        expand_pairs(bad)


# ─────────────────────────────────── REST payload builders ─────────────


def test_server_payload_includes_required_fields():
    plan = expand_pairs(_spec(
        SHAPE_SINGLE, [_ep("srv01", "mlx5_0", port=1, gid=3)],
        [_ep("srv02")],
    ))[0]
    body = server_start_payload(
        plan, "send_bw",
        {"msg_size": 65536, "qp_count": 1, "duration": 30, "mtu": 5},
    )
    assert body["role"] == "server"
    assert body["test"] == "send_bw"
    assert body["device"] == "mlx5_0"
    assert body["ib_port"] == 1
    assert body["gid_index"] == 3
    assert body["handshake_id"] == plan.handshake_id
    assert body["listen_port"] == plan.listen_port
    # Workload opts flow through verbatim
    assert body["msg_size"] == 65536
    assert body["qp_count"] == 1
    assert body["duration"] == 30
    assert body["mtu"] == 5


def test_client_payload_carries_peer_addr():
    plan = expand_pairs(_spec(
        SHAPE_SINGLE, [_ep("srv01")], [_ep("srv02")],
    ))[0]
    body = client_start_payload(
        plan, "send_bw",
        {"msg_size": 65536},
        peer_addr="10.0.0.1",
    )
    assert body["role"] == "client"
    assert body["peer_addr"] == "10.0.0.1"
    assert body["handshake_id"] == plan.handshake_id


# ─────────────────────────────────── aggregation ───────────────────────


def test_aggregate_empty_returns_zeroed_dict():
    out = aggregate_stats([])
    assert out["pair_count"] == 0
    assert out["pairs_running"] == 0
    assert out["pairs_done"] == 0
    assert out["total_bw_avg_gbps"] is None
    assert out["total_msg_rate_mpps"] is None


def test_aggregate_bw_sums_across_pairs():
    """Per-pair BW totals roll up to a TOTAL row by summation —
    that's the natural meaning for a multi-pair stress test."""
    jobs = [
        {"running": False, "finished_at": 100.0,
         "final_bw_avg_gbps": 100.0, "final_msg_rate_mpps": 0.5},
        {"running": False, "finished_at": 100.0,
         "final_bw_avg_gbps": 200.0, "final_msg_rate_mpps": 1.0},
        {"running": False, "finished_at": 100.0,
         "final_bw_avg_gbps": 50.5, "final_msg_rate_mpps": 0.25},
    ]
    out = aggregate_stats(jobs)
    assert out["pair_count"] == 3
    assert out["pairs_done"] == 3
    assert out["total_bw_avg_gbps"] == 350.5
    assert out["total_msg_rate_mpps"] == 1.75
    assert out["pairs_with_data"] == 3


def test_aggregate_running_pairs_counted_separately():
    jobs = [
        {"running": True, "finished_at": None,
         "final_bw_avg_gbps": None, "final_msg_rate_mpps": None},
        {"running": False, "finished_at": 100.0,
         "final_bw_avg_gbps": 100.0, "final_msg_rate_mpps": 0.5},
    ]
    out = aggregate_stats(jobs)
    assert out["pairs_running"] == 1
    assert out["pairs_done"] == 1
    assert out["total_bw_avg_gbps"] == 100.0  # only the done pair contributed


def test_aggregate_latency_uses_weighted_mean():
    """Latency aggregates as iteration-weighted mean — a 10-iter
    pair shouldn't drag the average around as much as a 10K-iter
    one. Using final_iterations as the weight gives the right
    semantics."""
    jobs = [
        {"running": False, "finished_at": 100.0,
         "final_lat_avg_us": 1.0, "final_iterations": 10},
        {"running": False, "finished_at": 100.0,
         "final_lat_avg_us": 5.0, "final_iterations": 10},
    ]
    out = aggregate_stats(jobs, is_lat=True)
    # Equal weights → straight mean = 3.0
    assert out["weighted_lat_avg_us"] == 3.0

    # Asymmetric weights — the 100-iter pair dominates
    jobs2 = [
        {"running": False, "finished_at": 100.0,
         "final_lat_avg_us": 1.0, "final_iterations": 1000},
        {"running": False, "finished_at": 100.0,
         "final_lat_avg_us": 100.0, "final_iterations": 10},
    ]
    out2 = aggregate_stats(jobs2, is_lat=True)
    # (1000*1 + 10*100) / 1010 ≈ 1.98 — much closer to 1 than 100
    assert 1.9 <= out2["weighted_lat_avg_us"] <= 2.1


def test_aggregate_surfaces_first_error_only():
    """If multiple pairs error, surface the FIRST one — the operator
    can drill into the per-pair grid for the rest."""
    jobs = [
        {"running": False, "error": "first error msg"},
        {"running": False, "error": "second error msg"},
    ]
    out = aggregate_stats(jobs)
    assert out["any_error"] == "first error msg"


def test_aggregate_ignores_non_dict_entries():
    """The poll path may surface None on connectivity error; the
    aggregator must skip them rather than crash."""
    jobs = [
        None,
        {"running": False, "finished_at": 100.0,
         "final_bw_avg_gbps": 50.0, "final_msg_rate_mpps": 0.5},
        "not a dict",
    ]
    out = aggregate_stats(jobs)
    # Only the valid dict contributes
    assert out["pair_count"] == 3  # length is what was passed
    assert out["pairs_done"] == 1
    assert out["total_bw_avg_gbps"] == 50.0
