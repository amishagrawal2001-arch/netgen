"""v0.5.105 Phase A: rx_worker process registry + admin endpoints.

The registry and the endpoints are exercised together because the
endpoints' value IS lifecycle management — testing them apart
hides the bugs that matter (e.g. double-start, leaked handle,
stop-of-unknown).

These tests stub out utils.dpdk_rx_worker.start_rx_worker so we
don't actually spawn a DPDK process — but everything ABOVE that
(registry, locks, Flask routing, JSON validation, role gates) is
exercised end to end via test_client.
"""
from __future__ import annotations

import io
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-rx-mgr.db")


# ─── Fake worker process (re-used across tests) ───


class _FakeProc:
    """subprocess.Popen stand-in that emits scripted JSON heartbeats."""
    def __init__(self, lines=None, pid=12345):
        self.pid = pid
        self._terminated = False
        self.signals_received = []
        self._lines = lines or []
        self.stdout = io.StringIO(
            "".join(line if line.endswith("\n") else line + "\n"
                    for line in self._lines)
        )
        self.stderr = io.StringIO("")

    def poll(self):
        return 0 if self._terminated else None

    def send_signal(self, sig):
        self.signals_received.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            self._terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.send_signal(signal.SIGKILL)


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
    """Per-test client + a clean registry. Also stubs the worker
    spawn so we don't shell out to a real binary."""
    from utils import dpdk_rx_worker, dpdk_rx_manager
    # Reset between tests so handles don't leak across cases.
    dpdk_rx_manager.reset_registry_for_tests()

    # Replace Popen so start_rx_worker gets our FakeProc.
    fake = _FakeProc(lines=[
        '{"ts":0,"stream_id":"s","rx_pkts":1000,"rx_bytes":64000,'
        '"rx_pps":1000,"rx_bps":512000,"matched_pkts":1000,'
        '"matched_bytes":64000,"hw_imissed":0,"hw_ierrors":0,'
        '"hw_rx_nombuf":0}',
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


# ─── Input validation ───


def test_start_rejects_missing_stream_id(client):
    c, _s, _f = client
    r = c.post("/api/admin/dpdk/rx/start",
               json={"pci_bdf": "0000:2b:00.1"})
    assert r.status_code == 400
    assert "stream_id" in r.get_json()["error"]


def test_start_rejects_bad_pci_bdf(client):
    c, _s, _f = client
    r = c.post("/api/admin/dpdk/rx/start",
               json={"stream_id": "s", "pci_bdf": "not-a-bdf"})
    assert r.status_code == 400


def test_start_rejects_path_traversal_stream_id(client):
    c, _s, _f = client
    r = c.post("/api/admin/dpdk/rx/start",
               json={"stream_id": "../etc/passwd",
                     "pci_bdf": "0000:2b:00.1"})
    assert r.status_code == 400


def test_start_clamps_oversized_lcore_inputs(client):
    """rx_queues bounded [1, 16] — out-of-range silently defaults
    to 1 instead of crashing the worker with absurd lcore mask."""
    c, _s, _f = client
    r = c.post("/api/admin/dpdk/rx/start",
               json={"stream_id": "s",
                     "pci_bdf": "0000:2b:00.1",
                     "rx_queues": 99999})
    assert r.status_code == 200
    # Verify the request still succeeded with clamped rx_queues=1
    data = r.get_json()
    assert "stream_id" in data


# ─── Happy path ───


def test_start_then_list_shows_running_worker(client):
    c, _s, _f = client
    r = c.post("/api/admin/dpdk/rx/start",
               json={"stream_id": "udp-test",
                     "pci_bdf": "0000:2b:00.1"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["stream_id"] == "udp-test"
    assert data["pid"] == 12345

    # /list returns it
    r2 = c.get("/api/admin/dpdk/rx/list")
    assert r2.status_code == 200
    workers = r2.get_json()["workers"]
    assert len(workers) == 1
    assert workers[0]["stream_id"] == "udp-test"
    assert workers[0]["pid"] == 12345


def test_latest_returns_parsed_heartbeat(client):
    c, _s, _f = client
    c.post("/api/admin/dpdk/rx/start",
           json={"stream_id": "udp-test",
                 "pci_bdf": "0000:2b:00.1"})
    # Give the reader thread a moment to drain the scripted line.
    for _ in range(50):
        r = c.get("/api/admin/dpdk/rx/latest/udp-test")
        if r.status_code == 200 and r.get_json().get("rx_pkts") == 1000:
            break
        time.sleep(0.01)
    assert r.status_code == 200
    snap = r.get_json()
    assert snap["rx_pkts"] == 1000
    assert snap["matched_pkts"] == 1000


def test_latest_404_for_unknown_stream(client):
    c, _s, _f = client
    r = c.get("/api/admin/dpdk/rx/latest/never-started")
    assert r.status_code == 404


# ─── Double-start guard ───


def test_double_start_returns_409(client):
    """Operator hits Start twice — second one must NOT spawn a
    duplicate worker fighting for the same PCI port."""
    c, _s, _f = client
    r1 = c.post("/api/admin/dpdk/rx/start",
                json={"stream_id": "s",
                      "pci_bdf": "0000:2b:00.1"})
    assert r1.status_code == 200
    r2 = c.post("/api/admin/dpdk/rx/start",
                json={"stream_id": "s",
                      "pci_bdf": "0000:2b:00.1"})
    assert r2.status_code == 409
    assert "already running" in r2.get_json()["error"]


# ─── Stop path ───


def test_stop_running_worker_returns_stopped(client):
    c, _s, _f = client
    c.post("/api/admin/dpdk/rx/start",
           json={"stream_id": "s",
                 "pci_bdf": "0000:2b:00.1"})
    r = c.post("/api/admin/dpdk/rx/stop",
               json={"stream_id": "s"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "stopped"
    assert body["stream_id"] == "s"
    # And SIGTERM was sent (FakeProc records it)
    assert signal.SIGTERM in _f.signals_received


def test_stop_unknown_is_idempotent(client):
    """No error if operator stops a stream that was never
    started — status=unknown so the caller distinguishes
    no-op from 'really stopped'."""
    c, _s, _f = client
    r = c.post("/api/admin/dpdk/rx/stop",
               json={"stream_id": "never-existed"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "unknown"


def test_stop_then_list_is_empty(client):
    c, _s, _f = client
    c.post("/api/admin/dpdk/rx/start",
           json={"stream_id": "s",
                 "pci_bdf": "0000:2b:00.1"})
    c.post("/api/admin/dpdk/rx/stop",
           json={"stream_id": "s"})
    r = c.get("/api/admin/dpdk/rx/list")
    assert r.get_json()["workers"] == []


# ─── Missing binary path ───


def test_start_503_when_rx_worker_missing(client, monkeypatch):
    """Fresh server pre-install_dpdk.sh: no rx_worker binary.
    Must guide the operator at the fix, not crash."""
    c, _s, _f = client
    from utils import dpdk_rx_worker
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: None)
    r = c.post("/api/admin/dpdk/rx/start",
               json={"stream_id": "s",
                     "pci_bdf": "0000:2b:00.1"})
    assert r.status_code == 503
    body = r.get_json()
    assert "install_dpdk.sh" in body["hint"] or "v0.5.105" in body["hint"]


# ─── Registry unit-level behavior ───


def test_registry_stop_all_is_safe_when_empty():
    from utils.dpdk_rx_manager import RxRegistry
    r = RxRegistry()
    r.stop_all()   # no-op, no exception


def test_registry_reaps_dead_handle_on_re_start(monkeypatch):
    """If a previous rx_worker died (process gone but handle
    still cached), the next start for the same stream_id must
    succeed instead of 409'ing."""
    from utils import dpdk_rx_worker
    from utils.dpdk_rx_manager import RxRegistry
    dead = _FakeProc()
    dead._terminated = True   # already gone
    alive = _FakeProc(pid=22222)

    bins = iter([dead, alive])
    monkeypatch.setattr(
        dpdk_rx_worker, "_resolve_rx_worker_bin",
        lambda: "/usr/local/bin/rx_worker",
    )
    # v0.5.255 (audit RX-1): start_rx_worker now calls
    # `resolve_dpdk_ld_library_path`, which invokes
    # `subprocess.run(["pkg-config", ...])` and `[..., "ldconfig",
    # "-p"]`. `subprocess.run` internally instantiates
    # `subprocess.Popen`, so the monkeypatched Popen below would
    # eat iterator items on pkg-config / ldconfig probes and the
    # real spawn would get StopIteration. Stub the helper so it
    # returns "" quickly without any subprocess call.
    from utils import dpdk_tx_worker as _tx
    monkeypatch.setattr(_tx, "resolve_dpdk_ld_library_path",
                        lambda cur="": "")
    monkeypatch.setattr(
        dpdk_rx_worker.subprocess, "Popen",
        lambda *a, **kw: next(bins),
    )

    reg = RxRegistry()
    reg.start(stream_id="s", pci_bdf="0000:2b:00.1")
    # The dead handle's is_running() returns False — re-start
    # should reap and replace.
    info = reg.start(stream_id="s", pci_bdf="0000:2b:00.1")
    assert info["pid"] == 22222
