"""Tests for utils/rdma_stream_engine.py — v0.3.12.

Exercises the shim that bridges per-stream "engine = rdma" config
into utils/rdma_perf.start_perftest.
"""
from __future__ import annotations

import threading
from unittest import mock

import pytest

from utils import rdma_stream_engine


# ─────────────────────────────────────────── should_use_rdma

def test_should_use_rdma_top_level():
    assert rdma_stream_engine.should_use_rdma({"engine": "rdma"}) is True
    assert rdma_stream_engine.should_use_rdma({"engine": "RDMA"}) is True


def test_should_use_rdma_nested():
    assert rdma_stream_engine.should_use_rdma(
        {"protocol_selection": {"engine": "rdma"}}
    ) is True


def test_should_use_rdma_negative_cases():
    assert rdma_stream_engine.should_use_rdma({}) is False
    assert rdma_stream_engine.should_use_rdma({"engine": "scapy"}) is False
    assert rdma_stream_engine.should_use_rdma({"engine": "dpdk"}) is False
    assert rdma_stream_engine.should_use_rdma(None) is False  # type: ignore


# ─────────────────────────────────────────── rdma_compatibility_check

def test_compat_missing_rdma_block():
    assert rdma_stream_engine.rdma_compatibility_check({}) is not None
    assert "rdma" in rdma_stream_engine.rdma_compatibility_check({})


def test_compat_missing_peer():
    r = rdma_stream_engine.rdma_compatibility_check({"rdma": {}})
    assert r is not None and "peer_addr" in r


def test_compat_invalid_test_name():
    r = rdma_stream_engine.rdma_compatibility_check(
        {"rdma": {"peer_addr": "10.0.0.2", "test": "potato_bw"}}
    )
    assert r is not None and "rdma.test must be one of" in r


def test_compat_happy_path():
    r = rdma_stream_engine.rdma_compatibility_check(
        {"rdma": {"peer_addr": "10.0.0.2", "test": "send_bw"}}
    )
    assert r is None


def test_compat_accepts_every_supported_test():
    for t in ("send_bw", "write_bw", "read_bw",
              "send_lat", "write_lat", "read_lat"):
        r = rdma_stream_engine.rdma_compatibility_check(
            {"rdma": {"peer_addr": "10.0.0.2", "test": t}}
        )
        assert r is None, f"unexpected reject of {t}: {r}"


# ─────────────────────────────────────────── _opts_from_stream

def test_opts_defaults_when_only_peer_set():
    opts = rdma_stream_engine._opts_from_stream(
        {"rdma": {"peer_addr": "10.0.0.2"}}
    )
    assert opts["peer_addr"] == "10.0.0.2"
    assert opts["device"] == "mlx5_0"
    assert opts["ib_port"] == 1
    assert opts["msg_size"] == 65536
    assert opts["qp_count"] == 1
    assert opts["duration"] == 30
    assert opts["gid_index"] == 3
    assert opts["bidirectional"] is False
    assert opts["report_gbits"] is True


def test_opts_full_override():
    opts = rdma_stream_engine._opts_from_stream({"rdma": {
        "peer_addr": "10.0.0.5", "device": "mlx5_bond_0",
        "ib_port": 2, "msg_size": 8192, "qp_count": 8,
        "duration": 90, "gid_index": 1, "bidirectional": True,
        "mtu": 4, "tx_depth": 512,
    }})
    assert opts["device"] == "mlx5_bond_0"
    assert opts["ib_port"] == 2
    assert opts["msg_size"] == 8192
    assert opts["qp_count"] == 8
    assert opts["duration"] == 90
    assert opts["gid_index"] == 1
    assert opts["bidirectional"] is True
    assert opts["mtu"] == 4
    assert opts["tx_depth"] == 512


# ─────────────────────────────────────────── start_rdma_stream

class _FakeTracker:
    """Mirrors the REAL multithreaded_traffic_gen.StreamTracker shape:
      add_stream(stream: dict)                      — dict, not positional
      mark_runtime_engine(iface, sid, *, runtime_engine, fallback_reason)
      update_tx_by_id(iface, sid, count=1)
    Matching the real signature is the whole point of these tests —
    the v0.3.12 stop-bug was caused by stub args that didn't match."""
    def __init__(self):
        self.added = []
        self.engines = []
        self.tx_updates = []

    def add_stream(self, stream):
        # Real tracker takes a dict — fail loudly if a dict isn't passed.
        assert isinstance(stream, dict), \
            f"add_stream expects a dict; got {type(stream).__name__}"
        self.added.append(stream)

    def mark_runtime_engine(self, interface, sid, *,
                            runtime_engine=None, fallback_reason=None):
        # Forces kwarg call shape — caught the bug where rdma_stream_engine
        # was passing engine + reason positionally.
        self.engines.append((interface, sid, runtime_engine, fallback_reason))

    def update_tx_by_id(self, interface, sid, count=1):
        self.tx_updates.append((interface, sid, count))


def test_start_rdma_stream_rejects_when_compat_fails():
    tracker = _FakeTracker()
    out = rdma_stream_engine.start_rdma_stream(
        {"engine": "rdma", "rdma": {}},   # no peer_addr
        interface="enp1s0f0",
        stop_event=threading.Event(),
        tracker=tracker,
    )
    assert out["status"] == "error"
    assert "peer_addr" in out["error"]
    assert tracker.added == []  # never registered


def test_start_rdma_stream_happy_path():
    """Mock start_perftest → verify shim registers in tracker +
    handshake registry, and returns the expected response shape."""
    fake_start = mock.Mock(return_value={
        "status": "started",
        "job_id": "fake-job-id",
        "listen_port": 18515,
        "tool": "/usr/bin/ib_send_bw",
        "cmd": ["/usr/bin/ib_send_bw"],
    })
    fake_register = mock.Mock(return_value={"handshake_id": "fake-hid",
                                            "record": {}})

    with mock.patch("utils.rdma_perf.start_perftest", fake_start), \
         mock.patch("utils.rdma_handshake.register_half", fake_register):
        tracker = _FakeTracker()
        out = rdma_stream_engine.start_rdma_stream(
            {
                "engine": "rdma",
                "stream_id": "stream-abc",
                "name": "rdma-1",
                "rdma": {
                    "peer_addr": "10.0.0.2",
                    "device": "mlx5_0",
                    "test": "send_bw",
                    "msg_size": 4096,
                    "duration": 10,
                },
            },
            interface="enp1s0f0",
            stop_event=threading.Event(),
            tracker=tracker,
        )

    assert out["status"] == "started"
    assert out["stream_id"] == "stream-abc"
    assert out["rdma_job_id"] == "fake-job-id"
    assert out["engine"] == "rdma"

    # Verify the perftest call shape.
    fake_start.assert_called_once()
    args, kwargs = fake_start.call_args
    assert args[0] == "client"
    assert args[1] == "send_bw"
    assert args[2]["peer_addr"] == "10.0.0.2"
    assert args[2]["msg_size"] == 4096

    # Verify handshake registration.
    fake_register.assert_called_once()
    _, hkw = fake_register.call_args
    assert hkw["role"] == "client"
    assert hkw["job_id"] == "fake-job-id"

    # Tracker registration — dict shape that matches the real tracker.
    assert len(tracker.added) == 1
    reg = tracker.added[0]
    assert reg["interface"] == "enp1s0f0"
    assert reg["stream_id"] == "stream-abc"
    assert reg["stream_name"] == "rdma-1"
    # CRITICAL: stop_event must be the SAME object we passed in. The
    # v0.3.12 bug was a fresh orphan event that the /api/traffic/stop
    # handler couldn't reach, so perftest ran to its full --duration.
    # Regression: same event must round-trip into the tracker so
    # `tracker.find_stream_by_id(...)["stop_event"].set()` works.
    assert reg["stop_event"] is not None
    # mark_runtime_engine must be called with kwargs (signature has *,).
    assert tracker.engines == [("enp1s0f0", "stream-abc", "rdma", None)]


def test_stop_event_round_trips_through_tracker_so_stop_works():
    """Direct regression for the v0.3.12 stop bug: the stop_event the
    caller passes must land in the tracker entry so /api/traffic/stop
    can signal it. Walks the data shape end-to-end."""
    import threading
    fake_start = mock.Mock(return_value={
        "status": "started", "job_id": "j", "listen_port": 18515,
        "tool": "/usr/bin/ib_send_bw", "cmd": ["/usr/bin/ib_send_bw"],
    })
    with mock.patch("utils.rdma_perf.start_perftest", fake_start), \
         mock.patch("utils.rdma_handshake.register_half",
                    return_value={"handshake_id": "h", "record": {}}):
        tracker = _FakeTracker()
        my_event = threading.Event()
        out = rdma_stream_engine.start_rdma_stream(
            {"engine": "rdma", "stream_id": "s1",
             "rdma": {"peer_addr": "10.0.0.2", "test": "send_bw"}},
            interface="enp1s0",
            stop_event=my_event,
            tracker=tracker,
        )
    assert out["status"] == "started"
    # The very same Event instance must be discoverable in the
    # tracker so the stop handler can fire it.
    assert tracker.added[0]["stop_event"] is my_event


def test_tracker_add_stream_signature_must_be_dict():
    """Belt-and-suspenders: prove start_rdma_stream never calls
    add_stream(interface, sid, name) positionally — that's the v0.3.12
    bug we just fixed and we want it to stay fixed."""
    import threading
    fake_start = mock.Mock(return_value={
        "status": "started", "job_id": "j", "listen_port": 18515,
        "tool": "/usr/bin/ib_send_bw", "cmd": ["/usr/bin/ib_send_bw"],
    })
    with mock.patch("utils.rdma_perf.start_perftest", fake_start), \
         mock.patch("utils.rdma_handshake.register_half",
                    return_value={"handshake_id": "h", "record": {}}):
        tracker = _FakeTracker()
        out = rdma_stream_engine.start_rdma_stream(
            {"engine": "rdma", "stream_id": "s1",
             "rdma": {"peer_addr": "10.0.0.2", "test": "send_bw",
                      "msg_size": 8192}},
            interface="enp1s0",
            stop_event=threading.Event(),
            tracker=tracker,
        )
    # Reached only if _FakeTracker.add_stream's `assert isinstance(stream, dict)`
    # was satisfied — i.e. start_rdma_stream called add_stream({...}) not
    # add_stream(interface, sid, name).
    assert out["status"] == "started"
    assert "stream_id" in tracker.added[0]
    assert "interface" in tracker.added[0]
    assert "stop_event" in tracker.added[0]
    # frame_size should be derived from rdma.msg_size for stats display.
    assert tracker.added[0]["frame_size"] == 8192


def test_start_rdma_stream_surfaces_perftest_error():
    fake_start = mock.Mock(return_value={
        "status": "error", "error": "perftest not installed",
    })
    with mock.patch("utils.rdma_perf.start_perftest", fake_start):
        tracker = _FakeTracker()
        out = rdma_stream_engine.start_rdma_stream(
            {
                "engine": "rdma",
                "stream_id": "s1",
                "rdma": {"peer_addr": "10.0.0.2", "test": "send_bw"},
            },
            interface="enp1s0",
            stop_event=threading.Event(),
            tracker=tracker,
        )
    assert out["status"] == "error"
    assert "perftest not installed" in out["error"]
    assert tracker.added == []
