"""v0.5.108 hot-fix regression:

Pre-fix bug: `_maybe_start_dpdk_rx_for_stream` assumed
stream_data["L3"] / ["L4"] / ["VLAN"] were dicts. They're string
FLAGS in the real payload ("IPv4", "UDP", "Untagged" / "Tagged"
respectively); the actual per-protocol config lives under
stream_data["protocol_data"]["ipv4"] / ["udp"] / ["vlan"].

Result: every operator on v0.5.105–v0.5.107 who started a DPDK
template stream got a 500 from /api/traffic/start the moment the
auto-lifecycle helper tried `stream_data.get("L3").get("dst_ip")`.

This test uses the EXACT payload shape captured from srv06's
journal so we never regress on the field-paths again. Any future
refactor that changes how protocol fields are nested will fail
this test instantly with a clear "you broke the production
payload contract" diagnostic.
"""
from __future__ import annotations

import io
import os
import signal
from pathlib import Path
from unittest import mock

import pytest


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-rx-payload.db")


# Captured from srv06 journal on 2026-06-12T21:03:57 — verbatim
# slice of the failing /api/traffic/start payload. The dialog
# always produces this exact shape via populate_stream_fields()
# + the template path. If this structure changes, the auto-
# lifecycle helper MUST adapt to match (or fall back to its
# match-anything defaults).
_SRV06_STREAM_PAYLOAD = {
    "name": "Stream_1",
    "enabled": True,
    "rx_port": "Same as TX Port",
    "engine": "dpdk",
    "rx_engine": "dpdk",
    "dpdk_enable": True,
    "frame_size": "1500",
    # Top-level flags — STRINGS, not dicts:
    "L1": "None",
    "VLAN": "Untagged",
    "L2": "None",
    "L3": "IPv4",
    "L4": "UDP",
    "stream_id": "16053077-9c26-4ba5-8897-e2d90fb2411d",
    "interface": "ens2f0np0",
    # Actual per-protocol config — nested:
    "protocol_data": {
        "mac": {
            "mac_destination_address": "02:00:00:00:00:02",
            "mac_source_address": "02:00:00:00:00:01",
        },
        "vlan": {
            "vlan_id": "1",
            "vlan_priority": "0",
            "vlan_cfi_dei": "0",
        },
        "ipv4": {
            "ipv4_source": "10.0.0.1",
            "ipv4_destination": "10.0.0.2",
            "ipv4_ttl": "64",
        },
        "udp": {
            "udp_source_port": "1234",
            "udp_destination_port": "4791",
        },
    },
    "stream_rate_type": "Line Rate",
}


# ─── Reproducer: the original crash must NOT recur ───


@pytest.fixture
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


def test_srv06_payload_does_not_crash_helper(server, monkeypatch):
    """The exact production payload that 500'd on srv06. After
    the v0.5.108 hot-fix the helper must complete without
    raising AttributeError on any of the string-flag fields."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()

    # Stub the manager-spawn so we just want to confirm no crash
    # in the field-extraction logic.
    monkeypatch.setattr(
        dpdk_rx_worker, "_resolve_rx_worker_bin",
        lambda: None,  # forces FileNotFoundError, which the
                       # helper catches as a warning, not raise
    )
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    # Must NOT raise. Pre-fix: AttributeError on .get() of string.
    server._maybe_start_dpdk_rx_for_stream(
        stream_id=_SRV06_STREAM_PAYLOAD["stream_id"],
        rx_interface="ens2f1np1",
        stream_data=dict(_SRV06_STREAM_PAYLOAD),
    )
    # Registry empty because rx_worker binary "missing" — that's
    # the controlled fallback path. The point: no exception escaped.
    assert dpdk_rx_manager.registry().list() == []


# ─── Field-extraction correctness ───


class _FakeProc:
    """Captures the cmd argv so we can assert the right fields
    were extracted from the nested protocol_data shape."""
    def __init__(self):
        self.pid = 11111
        self.signals_received = []
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self._terminated = False
    def poll(self): return 0 if self._terminated else None
    def send_signal(self, sig):
        self.signals_received.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            self._terminated = True
    def wait(self, timeout=None): return 0
    def kill(self): self.send_signal(signal.SIGKILL)


def test_helper_extracts_udp_ports_from_protocol_data(server, monkeypatch):
    """The captured payload has UDP src=1234, dst=4791 under
    protocol_data.udp.udp_destination_port. The helper must read
    THAT path, not stream_data["L4"]["dst_port"] (the old wrong
    path) which is a string 'UDP'."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    server._maybe_start_dpdk_rx_for_stream(
        stream_id=_SRV06_STREAM_PAYLOAD["stream_id"],
        rx_interface="ens2f1np1",
        stream_data=dict(_SRV06_STREAM_PAYLOAD),
    )

    reg = dpdk_rx_manager.registry()
    assert len(reg.list()) == 1, "rx_worker should have been spawned"
    # Reach into the handle to inspect the argv (registry.list()
    # intentionally doesn't expose cmd in the public response).
    sid = list(reg._handles.keys())[0]
    cmd = reg._handles[sid].cmd
    # UDP ports surfaced from protocol_data.udp:
    assert "--dst-port" in cmd
    dst_port = cmd[cmd.index("--dst-port") + 1]
    assert dst_port == "4791"
    assert "--src-port" in cmd
    src_port = cmd[cmd.index("--src-port") + 1]
    assert src_port == "1234"


def test_helper_extracts_ipv4_from_protocol_data(server, monkeypatch):
    """src_ip / dst_ip come from protocol_data.ipv4.ipv4_source /
    ipv4_destination — NOT the top-level "L3" string."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    server._maybe_start_dpdk_rx_for_stream(
        stream_id=_SRV06_STREAM_PAYLOAD["stream_id"],
        rx_interface="ens2f1np1",
        stream_data=dict(_SRV06_STREAM_PAYLOAD),
    )

    reg = dpdk_rx_manager.registry()
    sid = list(reg._handles.keys())[0]
    cmd = reg._handles[sid].cmd
    assert "--src-ip" in cmd
    assert cmd[cmd.index("--src-ip") + 1] == "10.0.0.1"
    assert "--dst-ip" in cmd
    assert cmd[cmd.index("--dst-ip") + 1] == "10.0.0.2"


def test_untagged_vlan_does_not_apply_vlan_filter(server, monkeypatch):
    """The payload has 'VLAN': 'Untagged' top-level but
    protocol_data.vlan.vlan_id=1 (the default field value). The
    helper must respect the Untagged flag — applying VLAN filter
    1 would make rx_worker miss every frame on the wire."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    server._maybe_start_dpdk_rx_for_stream(
        stream_id=_SRV06_STREAM_PAYLOAD["stream_id"],
        rx_interface="ens2f1np1",
        stream_data=dict(_SRV06_STREAM_PAYLOAD),
    )
    reg = dpdk_rx_manager.registry()
    sid = list(reg._handles.keys())[0]
    cmd = reg._handles[sid].cmd
    assert "--vlan" not in cmd, (
        "Untagged stream must NOT pass --vlan to rx_worker — "
        "operator's stream has no Dot1Q tag on the wire, so a "
        "VLAN filter would drop every match"
    )


def test_tagged_vlan_passes_vlan_id_filter(server, monkeypatch):
    """When the stream IS tagged, the helper picks up the
    protocol_data.vlan.vlan_id value."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    payload = dict(_SRV06_STREAM_PAYLOAD)
    payload["VLAN"] = "Tagged"
    payload["protocol_data"] = dict(_SRV06_STREAM_PAYLOAD["protocol_data"])
    payload["protocol_data"]["vlan"] = {"vlan_id": "100"}

    server._maybe_start_dpdk_rx_for_stream(
        stream_id="taggedtest", rx_interface="ens2f1np1",
        stream_data=payload,
    )
    reg = dpdk_rx_manager.registry()
    sid = list(reg._handles.keys())[0]
    cmd = reg._handles[sid].cmd
    assert "--vlan" in cmd
    assert cmd[cmd.index("--vlan") + 1] == "100"


# ─── Defensive: malformed payloads don't 500 ───


def test_missing_protocol_data_does_not_crash(server, monkeypatch):
    """Hand-crafted API call without protocol_data — helper must
    still complete (with no filter args)."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    server._maybe_start_dpdk_rx_for_stream(
        stream_id="x", rx_interface="ens2f1np1",
        stream_data={"rx_engine": "dpdk"},  # ← bare minimum
    )
    workers = dpdk_rx_manager.registry().list()
    assert len(workers) == 1


def test_string_protocol_data_does_not_crash(server, monkeypatch):
    """Pathological case: protocol_data is a string (e.g., from
    a broken legacy schema). Defensive coercion must catch it."""
    from utils import dpdk_rx_manager, dpdk_rx_worker, nic_counters

    dpdk_rx_manager.reset_registry_for_tests()
    fake = _FakeProc()
    monkeypatch.setattr(dpdk_rx_worker, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    monkeypatch.setattr(dpdk_rx_worker.subprocess, "Popen",
                        lambda *a, **kw: fake)
    monkeypatch.setattr(nic_counters, "iface_to_pci_bdf",
                        lambda iface: "0000:2b:00.1")

    server._maybe_start_dpdk_rx_for_stream(
        stream_id="y", rx_interface="ens2f1np1",
        stream_data={"rx_engine": "dpdk",
                     "protocol_data": "this should be a dict"},
    )
    # Must NOT raise; worker spawned with empty filter
    assert len(dpdk_rx_manager.registry().list()) == 1
