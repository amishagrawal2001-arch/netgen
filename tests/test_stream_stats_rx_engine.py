"""v0.5.105 Phase B: /api/streams/stats folds rx_worker counters
into the active_streams response.

Operator-driven scenario:
  1. Operator configures a stream (DPDK TX, Scapy default RX)
  2. /api/streams/stats shows rx_count=0 (Scapy sniffer overwhelmed)
  3. Operator manually starts rx_worker via admin endpoint
  4. /api/streams/stats now shows the rx_worker's accurate count
     with rx_engine="dpdk"

Tests verify the fold is non-invasive: when no rx_worker is
registered the response shape is unchanged (rx_engine="scapy"
is the only addition, defaulted gracefully).
"""
from __future__ import annotations

import io
import os
import signal
import time
from pathlib import Path
from unittest import mock

import pytest


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-rx-engine.db")


class _FakeProc:
    def __init__(self, lines=None, pid=99):
        self.pid = pid
        self._terminated = False
        self.signals_received = []
        self.stdout = io.StringIO(
            "".join(line if line.endswith("\n") else line + "\n"
                    for line in (lines or []))
        )
        self.stderr = io.StringIO("")
    def poll(self): return 0 if self._terminated else None
    def send_signal(self, sig):
        self.signals_received.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            self._terminated = True
    def wait(self, timeout=None): return 0
    def kill(self): self.send_signal(signal.SIGKILL)


@pytest.fixture(scope="module")
def server():
    pytest.importorskip("flask")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_tgen_server", str(_SERVER),
    )
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    s.app.config["TESTING"] = True
    return s


@pytest.fixture
def client(server, monkeypatch):
    from utils import dpdk_rx_worker, dpdk_rx_manager
    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc(lines=[
        '{"ts":0,"stream_id":"udp-test","rx_pkts":120000000,'
        '"rx_bytes":7680000000,"rx_pps":24000000,"rx_bps":1536000000000,'
        '"matched_pkts":119999958,"matched_bytes":7679997312,'
        '"hw_imissed":42,"hw_ierrors":0,"hw_rx_nombuf":0}',
    ])
    monkeypatch.setattr(
        dpdk_rx_worker, "_resolve_rx_worker_bin",
        lambda: "/usr/local/bin/rx_worker",
    )
    monkeypatch.setattr(
        dpdk_rx_worker.subprocess, "Popen",
        lambda *a, **kw: fake,
    )
    return server.app.test_client(), server, fake


# ─── Without an rx_worker: shape is unchanged ───


def test_stats_default_rx_engine_is_scapy(client, monkeypatch):
    c, s, _f = client
    # Mock the stream DB so we get a deterministic active_streams
    fake_streams = [
        {
            "stream_id": "udp-test", "stream_name": "UDP",
            "interface": "ens2f0np0", "rx_interface": "ens2f1np1",
            "tx_count": 1000, "rx_count": 0,
            "tx_rate": 100.0, "rx_rate": 0.0,
            "status": "Running", "tg_id": 0,
            "stream_config": {},
        },
    ]
    monkeypatch.setattr(s.stream_db, "get_all_streams",
                        lambda **kw: fake_streams)
    r = c.get("/api/streams/stats?status=Running")
    assert r.status_code == 200
    streams = r.get_json()["active_streams"]
    assert len(streams) == 1
    assert streams[0]["rx_engine"] == "scapy"
    # rx_count unchanged when no rx_worker is registered
    assert streams[0]["rx_count"] == 0


# ─── With an rx_worker: counters get folded ───


def test_stats_folds_rx_worker_counters_when_registered(client, monkeypatch):
    c, s, _f = client
    fake_streams = [
        {
            "stream_id": "udp-test", "stream_name": "UDP",
            "interface": "ens2f0np0", "rx_interface": "ens2f1np1",
            "tx_count": 120_000_000, "rx_count": 0,
            "tx_rate": 24_000_000.0, "rx_rate": 0.0,
            "status": "Running", "tg_id": 0,
            "stream_config": {},
        },
    ]
    monkeypatch.setattr(s.stream_db, "get_all_streams",
                        lambda **kw: fake_streams)

    # Start an rx_worker for this stream
    start_resp = c.post("/api/admin/dpdk/rx/start",
                        json={"stream_id": "udp-test",
                              "pci_bdf": "0000:2b:00.1"})
    assert start_resp.status_code == 200
    # Wait for the reader to absorb the scripted heartbeat
    from utils.dpdk_rx_manager import registry as _rxreg
    for _ in range(50):
        if _rxreg().latest_for("udp-test"):
            break
        time.sleep(0.01)

    # Now /api/streams/stats should reflect the worker's counters
    r = c.get("/api/streams/stats?status=Running")
    assert r.status_code == 200
    streams = r.get_json()["active_streams"]
    assert streams[0]["rx_engine"] == "dpdk"
    # matched_pkts > prior rx_count → fold took effect
    assert streams[0]["rx_count"] == 119_999_958
    # rx_rate adopted from rx_pps
    assert streams[0]["rx_rate"] == 24_000_000.0
    # Hardware drop counters surfaced for diagnostics
    detail = streams[0]["rx_engine_detail"]
    assert detail["hw_imissed"] == 42

    # Cleanup
    c.post("/api/admin/dpdk/rx/stop",
           json={"stream_id": "udp-test"})


def test_stats_only_affects_streams_with_registered_workers(client, monkeypatch):
    """Multi-stream case: one has rx_worker, the other doesn't.
    Only the registered one gets folded."""
    c, s, _f = client
    fake_streams = [
        {"stream_id": "with-worker",
         "stream_name": "A", "interface": "eth0",
         "tx_count": 100, "rx_count": 50,
         "tx_rate": 10.0, "rx_rate": 5.0,
         "status": "Running", "stream_config": {}},
        {"stream_id": "without-worker",
         "stream_name": "B", "interface": "eth0",
         "tx_count": 200, "rx_count": 99,
         "tx_rate": 20.0, "rx_rate": 9.9,
         "status": "Running", "stream_config": {}},
    ]
    monkeypatch.setattr(s.stream_db, "get_all_streams",
                        lambda **kw: fake_streams)
    # Start a worker only for the first stream
    c.post("/api/admin/dpdk/rx/start",
           json={"stream_id": "with-worker",
                 "pci_bdf": "0000:2b:00.1"})
    from utils.dpdk_rx_manager import registry as _rxreg
    for _ in range(50):
        if _rxreg().latest_for("with-worker"):
            break
        time.sleep(0.01)
    r = c.get("/api/streams/stats?status=Running")
    streams = r.get_json()["active_streams"]
    by_id = {x["stream_id"]: x for x in streams}
    assert by_id["with-worker"]["rx_engine"] == "dpdk"
    assert by_id["without-worker"]["rx_engine"] == "scapy"
    # without-worker keeps original Scapy rx_count
    assert by_id["without-worker"]["rx_count"] == 99


def test_stats_fold_failure_does_not_break_response(client, monkeypatch):
    """If the rx_manager import fails or crashes, the stats endpoint
    must still return active_streams in the old shape — don't 500."""
    c, s, _f = client
    fake_streams = [
        {"stream_id": "s", "stream_name": "S", "interface": "eth0",
         "tx_count": 1, "rx_count": 1, "tx_rate": 1.0, "rx_rate": 1.0,
         "status": "Running", "stream_config": {}},
    ]
    monkeypatch.setattr(s.stream_db, "get_all_streams",
                        lambda **kw: fake_streams)
    # Sabotage the registry: latest_for raises
    from utils.dpdk_rx_manager import RxRegistry, registry
    import utils.dpdk_rx_manager as mgr
    bad = RxRegistry()
    bad.latest_for = lambda sid: (_ for _ in ()).throw(
        RuntimeError("simulated"),
    )
    monkeypatch.setattr(mgr, "_REGISTRY", bad)
    r = c.get("/api/streams/stats?status=Running")
    assert r.status_code == 200
    streams = r.get_json()["active_streams"]
    assert streams[0]["stream_id"] == "s"
    # rx_count is the Scapy original because the fold bailed
    assert streams[0]["rx_count"] == 1
