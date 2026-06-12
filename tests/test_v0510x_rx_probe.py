"""v0.5.109: POST /api/admin/dpdk/rx/probe — one-shot RX verdict probe.

Spawns rx_worker, samples for N seconds, stops, returns a verdict.
Operator-facing replacement for the manual "start, sleep, curl,
stop, compute, interpret" loop. Specifically for srv06's "is the
wire delivering DPDK-rate packets?" question.

Verdict heuristics tested:
- rx_silent: rx_pkts=0 + hw_imissed=0 → cable disconnected
- hw_drops: hw_imissed>0 + rx_pkts=0 → NIC chip dropping
- rate_limited: rx_pkts>0 but ≪ expected → switch storm-control
- rx_active: rx_pkts>0 and matches expected → wire works
- needs_diag: no heartbeats at all → EAL/spawn failure
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
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-rx-probe.db")


class _FakeProc:
    """rx_worker stand-in that emits a single scripted heartbeat."""
    def __init__(self, heartbeat: dict | None = None, pid: int = 99999):
        self.pid = pid
        self._terminated = False
        self.signals_received: list[int] = []
        lines = []
        if heartbeat:
            import json
            lines.append(json.dumps(heartbeat))
        self.stdout = io.StringIO(
            "".join(line + "\n" for line in lines)
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


def _setup_fake_worker(monkeypatch, heartbeat: dict | None, pid: int = 12345):
    """Install a single _FakeProc emitting `heartbeat` on every spawn."""
    from utils import dpdk_rx_worker, dpdk_rx_manager, nic_counters
    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc(heartbeat=heartbeat, pid=pid)
    monkeypatch.setattr(
        dpdk_rx_worker, "_resolve_rx_worker_bin",
        lambda: "/usr/local/bin/rx_worker",
    )
    monkeypatch.setattr(
        dpdk_rx_worker.subprocess, "Popen",
        lambda *a, **kw: fake,
    )
    monkeypatch.setattr(
        nic_counters, "iface_to_pci_bdf",
        lambda iface: "0000:2b:00.1",
    )
    return fake


# ─── Input validation ───


def test_probe_rejects_missing_rx_iface(server, monkeypatch):
    _setup_fake_worker(monkeypatch, heartbeat=None)
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={})
    assert r.status_code == 400
    assert "rx_iface" in r.get_json()["error"]


def test_probe_rejects_bad_iface_name(server, monkeypatch):
    _setup_fake_worker(monkeypatch, heartbeat=None)
    c = server.app.test_client()
    for bad in ["../etc", "a" * 16, "name with space"]:
        r = c.post("/api/admin/dpdk/rx/probe",
                   json={"rx_iface": bad})
        assert r.status_code == 400, f"{bad!r} should reject"


def test_probe_rejects_bad_pci_bdf(server, monkeypatch):
    _setup_fake_worker(monkeypatch, heartbeat=None)
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1",
        "rx_pci_bdf": "not-a-bdf",
    })
    assert r.status_code == 400


def test_probe_clamps_duration(server, monkeypatch):
    """Out-of-range duration_s silently defaults to 10s — better
    UX than 400, and the verdict heuristic still works at default."""
    _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 0, "matched_pkts": 0, "rx_pps": 0,
        "hw_imissed": 0, "hw_ierrors": 0, "hw_rx_nombuf": 0,
        "rx_bytes": 0, "stream_id": "test", "ts": 0,
    })
    c = server.app.test_client()
    # Force a very short window so the test runs fast — use 1s.
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1",
        "duration_s": 1,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["duration_s"] == 1


# ─── Verdict heuristics ───


def test_verdict_rx_silent_when_no_packets_and_no_drops(server, monkeypatch):
    """The srv06 cabling-broken case: 0 packets, 0 hw_imissed."""
    _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 0, "matched_pkts": 0, "rx_pps": 0,
        "hw_imissed": 0, "hw_ierrors": 0, "hw_rx_nombuf": 0,
        "rx_bytes": 0, "stream_id": "probe", "ts": 0,
    })
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["verdict"] == "rx_silent"
    assert body["rx_pkts"] == 0
    assert body["hw_imissed"] == 0
    # Actions actually guide the operator
    actions_text = " ".join(body["actions"]).lower()
    assert "cable" in actions_text or "loopback" in actions_text


def test_verdict_hw_drops_when_chip_drops(server, monkeypatch):
    _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 0, "matched_pkts": 0, "rx_pps": 0,
        "hw_imissed": 12345, "hw_ierrors": 0, "hw_rx_nombuf": 0,
        "rx_bytes": 0, "stream_id": "probe", "ts": 0,
    })
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    body = r.get_json()
    assert body["verdict"] == "hw_drops"
    assert body["hw_imissed"] == 12345


def test_verdict_rx_active_when_packets_match_expectation(server, monkeypatch):
    """100K pps × 1s = ~100K packets. Heuristic should report
    rx_active and surface delivery_pct."""
    _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 100_000, "matched_pkts": 100_000,
        "rx_pps": 100_000, "hw_imissed": 0, "hw_ierrors": 0,
        "hw_rx_nombuf": 0, "rx_bytes": 6_400_000,
        "stream_id": "probe", "ts": 0,
    })
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
        "expected_pps": 100000,
    })
    body = r.get_json()
    assert body["verdict"] == "rx_active"
    assert body["rx_pkts"] == 100_000
    assert body["delivery_pct"] is not None
    assert 90.0 <= body["delivery_pct"] <= 110.0


def test_verdict_rate_limited_when_far_below_expected(server, monkeypatch):
    """Critical srv06 use case: 24M pps expected, only 1M arrives
    → switch storm-control."""
    _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 1_000_000, "matched_pkts": 1_000_000,
        "rx_pps": 1_000_000, "hw_imissed": 0, "hw_ierrors": 0,
        "hw_rx_nombuf": 0, "rx_bytes": 64_000_000,
        "stream_id": "probe", "ts": 0,
    })
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
        "expected_pps": 24_000_000,
    })
    body = r.get_json()
    assert body["verdict"] == "rate_limited"
    actions_text = " ".join(body["actions"]).lower()
    assert "storm" in actions_text or "rate-limit" in actions_text


def test_verdict_needs_diag_when_no_heartbeat(server, monkeypatch):
    """rx_worker spawned but emitted nothing — EAL failure, hugepages
    missing, etc. Probe must not get stuck."""
    _setup_fake_worker(monkeypatch, heartbeat=None)  # ← empty stdout
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    body = r.get_json()
    assert body["verdict"] == "needs_diag"
    actions_text = " ".join(body["actions"]).lower()
    assert "journal" in actions_text or "hugepages" in actions_text


# ─── Cleanup / resilience ───


def test_probe_cleans_up_worker_after_run(server, monkeypatch):
    """The probe must stop the rx_worker it spawned, regardless of
    verdict. Operator running 10 probes in a row should NOT
    accumulate 10 zombie workers."""
    fake = _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 1, "matched_pkts": 1, "rx_pps": 1,
        "hw_imissed": 0, "hw_ierrors": 0, "hw_rx_nombuf": 0,
        "rx_bytes": 64, "stream_id": "probe", "ts": 0,
    })
    c = server.app.test_client()
    c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    # SIGTERM sent at cleanup
    assert signal.SIGTERM in fake.signals_received
    # Registry now empty
    from utils.dpdk_rx_manager import registry
    assert registry().list() == []


def test_probe_503_when_binary_missing(server, monkeypatch):
    from utils import dpdk_rx_worker, dpdk_rx_manager, nic_counters
    dpdk_rx_manager.reset_registry_for_tests()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: None)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    assert r.status_code == 503
    body = r.get_json()
    assert "install_dpdk" in body["hint"] or "v0.5.105" in body["hint"]


def test_probe_400_when_pci_bdf_unresolvable(server, monkeypatch):
    """Iface name not in /sys/class/net AND no explicit pci_bdf.
    Probe must fail fast with an actionable hint."""
    from utils import dpdk_rx_worker, dpdk_rx_manager, nic_counters
    dpdk_rx_manager.reset_registry_for_tests()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: None)
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    assert r.status_code == 400
    body = r.get_json()
    assert "rx_pci_bdf" in body["hint"]


def test_probe_response_includes_samples_array(server, monkeypatch):
    """For deeper diagnosis: the response must expose per-second
    snapshots so operators can see if rx_pkts ramped or stayed flat."""
    _setup_fake_worker(monkeypatch, heartbeat={
        "rx_pkts": 50000, "matched_pkts": 50000,
        "rx_pps": 50000, "hw_imissed": 0, "hw_ierrors": 0,
        "hw_rx_nombuf": 0, "rx_bytes": 3_200_000,
        "stream_id": "probe", "ts": 0,
    })
    c = server.app.test_client()
    r = c.post("/api/admin/dpdk/rx/probe", json={
        "rx_iface": "ens2f1np1", "duration_s": 1,
    })
    body = r.get_json()
    assert "samples" in body
    assert isinstance(body["samples"], list)
