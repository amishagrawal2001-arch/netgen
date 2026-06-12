"""v0.5.105 final phase: auto-lifecycle + UI surface.

Tests cover:
  - _maybe_start_dpdk_rx_for_stream: skips when rx_engine!=dpdk,
    starts via manager when rx_engine=dpdk, defaults to scapy when
    field missing
  - _maybe_stop_dpdk_rx_for_stream: idempotent
  - rx_pci_bdf override path (operator-supplied for vfio-bound RX)
  - PCI-BDF resolution failure path (cannot resolve from iface,
    no override) → logs but doesn't raise
  - Manager FileNotFoundError surfaces as warning, not exception
  - stream_dialog widget has rx_engine_combo wired
  - Admin console drawer HTML has Start rx_worker button
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
_STREAM_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "stream_dialog.py"
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-rx-auto.db")


class _FakeProc:
    def __init__(self, pid=33333):
        self.pid = pid
        self._terminated = False
        self.signals_received = []
        self.stdout = io.StringIO("")
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


@pytest.fixture(autouse=True)
def reset_registry():
    from utils import dpdk_rx_manager
    dpdk_rx_manager.reset_registry_for_tests()
    yield
    dpdk_rx_manager.reset_registry_for_tests()


# ─── _maybe_start_dpdk_rx_for_stream ───


def test_helper_skips_when_rx_engine_unset(server, monkeypatch):
    """No rx_engine field → Scapy default → no rx_worker spawn."""
    from utils.dpdk_rx_manager import registry
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="s", rx_interface="eth0",
        stream_data={},
    )
    assert registry().list() == []


def test_helper_skips_when_rx_engine_scapy(server):
    from utils.dpdk_rx_manager import registry
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="s", rx_interface="eth0",
        stream_data={"rx_engine": "scapy"},
    )
    assert registry().list() == []


def test_helper_spawns_when_rx_engine_dpdk(server, monkeypatch):
    """Happy path: rx_engine=dpdk + resolvable PCI BDF →
    rx_worker spawn registered."""
    from utils import dpdk_rx_worker, nic_counters
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="udp-test", rx_interface="ens2f1np1",
        stream_data={
            "rx_engine": "dpdk",
            "L4": {"dst_port": 4791},
            "L3": {"dst_ip": "10.0.0.2"},
            "VLAN": {"vlan_id": 100},
        },
    )
    from utils.dpdk_rx_manager import registry
    workers = registry().list()
    assert len(workers) == 1
    assert workers[0]["stream_id"] == "udp-test"


def test_helper_uses_explicit_rx_pci_bdf_override(server, monkeypatch):
    """When the RX iface is vfio-bound, iface_to_pci_bdf returns
    None. Operator workaround: pass rx_pci_bdf in stream_data."""
    from utils import dpdk_rx_worker, nic_counters
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: None)
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="s2", rx_interface="ens2f1np1",
        stream_data={
            "rx_engine": "dpdk",
            "rx_pci_bdf": "0000:2B:00.1",
        },
    )
    from utils.dpdk_rx_manager import registry
    workers = registry().list()
    assert len(workers) == 1


def test_helper_skips_when_pci_bdf_unresolvable(server, monkeypatch):
    """No override + can't resolve from iface → log warning,
    don't spawn, don't raise. Stream's TX continues."""
    from utils import nic_counters
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: None)
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="s3", rx_interface="unknown-iface",
        stream_data={"rx_engine": "dpdk"},
    )
    from utils.dpdk_rx_manager import registry
    assert registry().list() == []


def test_helper_swallows_missing_binary(server, monkeypatch):
    """rx_worker binary not installed → log warning, continue.
    Stream's TX path is independent of RX engine."""
    from utils import dpdk_rx_worker, nic_counters
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: None)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")
    # Must not raise
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="s4", rx_interface="ens2f1np1",
        stream_data={"rx_engine": "dpdk"},
    )
    from utils.dpdk_rx_manager import registry
    assert registry().list() == []


# ─── _maybe_stop_dpdk_rx_for_stream ───


def test_stop_helper_is_idempotent_for_unknown(server):
    """No raise when stopping a stream that was never started.
    Stream stop path can call this unconditionally for every
    stop entry."""
    server._maybe_stop_dpdk_rx_for_stream(stream_id="never-started")


def test_stop_helper_terminates_running_worker(server, monkeypatch):
    from utils import dpdk_rx_worker, nic_counters
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")
    server._maybe_start_dpdk_rx_for_stream(
        stream_id="s5", rx_interface="ens2f1np1",
        stream_data={"rx_engine": "dpdk"},
    )
    server._maybe_stop_dpdk_rx_for_stream(stream_id="s5")
    assert signal.SIGTERM in fake.signals_received


# ─── Stream dialog wiring ───


def test_stream_dialog_has_rx_engine_combo():
    """The dialog source must declare an rx_engine combo so
    the operator can pick Scapy vs DPDK per stream."""
    src = _STREAM_DIALOG.read_text()
    assert "rx_engine_combo" in src
    # Both options present
    assert 'addItem("Scapy (kernel sniffer)"' in src
    assert 'addItem("DPDK (rx_worker)"' in src


def test_stream_dialog_writes_rx_engine_into_payload():
    """The save-side must serialize rx_engine so the server can
    read it. (Without this the combo is decorative — the spawn
    helper never sees the choice.)"""
    src = _STREAM_DIALOG.read_text()
    assert '"rx_engine":' in src
    # Default falls through to scapy if combo missing
    assert "scapy" in src


def test_stream_dialog_restores_rx_engine_on_load():
    """When opening an existing stream, the dialog should reflect
    the saved rx_engine instead of always defaulting to Scapy."""
    src = _STREAM_DIALOG.read_text()
    assert 'rx_engine_combo.findData' in src or 'rx_engine_combo.setCurrentIndex' in src


# ─── Admin console drawer button ───


def test_admin_drawer_has_rx_worker_buttons(server):
    """The Live counters tile in the iface drawer must include
    a Start rx_worker button — the third deliverable."""
    src = Path(_SERVER).read_text()
    # Buttons rendered in the drawer HTML
    assert "Start rx_worker" in src
    # JS wires them to the endpoints
    assert "wireRxWorkerButtons" in src
    assert "/api/admin/dpdk/rx/start" in src
    assert "/api/admin/dpdk/rx/stop" in src


def test_admin_drawer_resolves_pci_bdf_from_counters(server):
    """The Start handler must resolve iface→PCI BDF via the
    counters endpoint (works for both kernel + vfio bindings).
    Hardcoding from iface name would fail for vfio-bound RX."""
    src = Path(_SERVER).read_text()
    # The JS handler reads pci_bdf from the counters response
    # before posting to /start
    import re
    handler = re.search(
        r"function wireRxWorkerButtons[\s\S]+?(?=\n    [a-z]+\s*\()",
        src,
    )
    assert handler, "wireRxWorkerButtons not found"
    body = handler.group(0)
    assert "/counters" in body
    assert "pci_bdf" in body
