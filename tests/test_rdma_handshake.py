"""Tests for utils/rdma_handshake.py — v0.3.12."""
from __future__ import annotations

import time

import pytest

from utils import rdma_handshake


@pytest.fixture(autouse=True)
def _reset():
    rdma_handshake.reset_for_tests()
    yield
    rdma_handshake.reset_for_tests()


def test_new_handshake_id_is_uuid_shape():
    hid = rdma_handshake.new_handshake_id()
    assert len(hid) == 36
    assert hid.count("-") == 4


def test_register_half_creates_record():
    hid = rdma_handshake.new_handshake_id()
    out = rdma_handshake.register_half(
        hid, role="server", job_id="job-A",
        test="send_bw", device="mlx5_0", ib_port=1,
        listen_port=18515, listen_addr="10.0.0.1",
    )
    assert out["handshake_id"] == hid
    assert len(out["record"]["halves"]) == 1
    assert out["record"]["halves"][0]["role"] == "server"


def test_register_half_auto_allocates_id_when_none():
    out = rdma_handshake.register_half(
        None, role="server", job_id="solo",
        test="write_bw", device="mlx5_0", ib_port=1,
        listen_port=18516,
    )
    assert len(out["handshake_id"]) == 36


def test_register_replaces_existing_half_with_same_role():
    """Operator restarted the server side — new job_id should
    REPLACE the old half, not accumulate."""
    hid = rdma_handshake.new_handshake_id()
    rdma_handshake.register_half(hid, role="server", job_id="job-A",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515)
    rdma_handshake.register_half(hid, role="server", job_id="job-A2",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515)
    rec = rdma_handshake.get_handshake(hid)
    assert len(rec["halves"]) == 1
    assert rec["halves"][0]["job_id"] == "job-A2"


def test_two_halves_coexist_under_one_handshake():
    """Loopback or normal client+server pair — both halves persist."""
    hid = rdma_handshake.new_handshake_id()
    rdma_handshake.register_half(hid, role="server", job_id="srv",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515,
                                 listen_addr="10.0.0.1")
    rdma_handshake.register_half(hid, role="client", job_id="cli",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515,
                                 peer_addr="10.0.0.1")
    rec = rdma_handshake.get_handshake(hid)
    assert len(rec["halves"]) == 2
    roles = {h["role"] for h in rec["halves"]}
    assert roles == {"server", "client"}


def test_find_job_handshake_reverse_lookup():
    hid = rdma_handshake.new_handshake_id()
    rdma_handshake.register_half(hid, role="client", job_id="my-job",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515,
                                 peer_addr="10.0.0.1")
    assert rdma_handshake.find_job_handshake("my-job") == hid
    assert rdma_handshake.find_job_handshake("not-a-job") is None


def test_list_handshakes_returns_all():
    hids = [rdma_handshake.new_handshake_id() for _ in range(3)]
    for i, hid in enumerate(hids):
        rdma_handshake.register_half(hid, role="server", job_id=f"j{i}",
                                     test="send_bw", device="mlx5_0",
                                     ib_port=1, listen_port=18515 + i)
    out = rdma_handshake.list_handshakes()
    assert len(out) == 3


def test_forget_handshake():
    hid = rdma_handshake.new_handshake_id()
    rdma_handshake.register_half(hid, role="server", job_id="j",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515)
    assert rdma_handshake.forget_handshake(hid) is True
    assert rdma_handshake.get_handshake(hid) is None
    # Idempotent
    assert rdma_handshake.forget_handshake(hid) is False


def test_gc_drops_expired_records(monkeypatch):
    monkeypatch.setattr(rdma_handshake, "_HANDSHAKE_TTL_SECS", 0.5)
    hid = rdma_handshake.new_handshake_id()
    rdma_handshake.register_half(hid, role="server", job_id="j",
                                 test="send_bw", device="mlx5_0",
                                 ib_port=1, listen_port=18515)
    time.sleep(0.7)
    # Trigger GC by listing
    out = rdma_handshake.list_handshakes()
    assert all(r["handshake_id"] != hid for r in out)
