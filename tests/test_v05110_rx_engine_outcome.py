"""v0.5.110: rx_worker spawn outcome must be surfaced in the
start response so the operator sees fallback reasons in the UI
instead of having to tail server logs.

`_maybe_start_dpdk_rx_for_stream` returns a structured dict —
{requested, actual, reason, pid} — and `/api/traffic/start`
folds it into each started_streams entry as rx_engine_requested,
rx_engine_actual, rx_engine_fallback_reason. The client renders
a "DPDK RX fallback" dialog when actual != requested.

Tests:
  • Scapy stream → requested=False, actual=scapy, reason=None
  • DPDK stream + pci_bdf lookup fails → actual=scapy + reason
  • DPDK stream + binary missing → actual=scapy + reason
  • DPDK stream + spawn succeeds → actual=dpdk + pid

We mock the manager registry start() so the tests don't need
the rx_worker binary or a real PCI BDF on the host.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_scapy_stream_returns_no_op_outcome():
    """rx_engine != 'dpdk' → no spawn attempted, deterministic
    no-op outcome dict."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    result = _maybe_start_dpdk_rx_for_stream(
        stream_id="s1",
        rx_interface="eth0",
        stream_data={"rx_engine": "scapy"},
    )
    assert result == {
        "requested": False,
        "actual": "scapy",
        "reason": None,
        "pid": None,
    }


def test_dpdk_stream_with_unresolvable_pci_bdf_falls_back():
    """The srv06 case: rx_engine='dpdk' but iface_to_pci_bdf
    returns None (kernel-bound iface that doesn't expose its
    device symlink, OR iface that's already vfio-bound — both
    leave us without a BDF unless the operator passes
    rx_pci_bdf explicitly). Outcome must explain WHY."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    with patch("utils.nic_counters.iface_to_pci_bdf", return_value=None):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s1",
            rx_interface="ens2f1np1",
            stream_data={"rx_engine": "dpdk"},
        )
    assert result["requested"] is True
    assert result["actual"] == "scapy"
    assert "pci_bdf" in result["reason"].lower()
    assert "ens2f1np1" in result["reason"]


def test_dpdk_stream_with_missing_binary_falls_back():
    """rx_worker binary not on $PATH → FileNotFoundError →
    fallback with a hint pointing at install_dpdk.sh."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    fake_registry = MagicMock()
    fake_registry.start.side_effect = FileNotFoundError(
        "/usr/local/bin/rx_worker"
    )
    fake_registry_factory = MagicMock(return_value=fake_registry)

    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch(
        "utils.dpdk_rx_manager.registry",
        fake_registry_factory,
    ):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s1",
            rx_interface="eth0",
            stream_data={"rx_engine": "dpdk"},
        )
    assert result["requested"] is True
    assert result["actual"] == "scapy"
    assert "install_dpdk" in result["reason"].lower()
    assert result["pid"] is None


def test_dpdk_stream_spawn_succeeds_returns_pid():
    """Happy path: rx_worker spawned, pid returned, actual=dpdk."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    fake_registry = MagicMock()
    fake_registry.start.return_value = {"pid": 12345}
    fake_registry_factory = MagicMock(return_value=fake_registry)

    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch(
        "utils.dpdk_rx_manager.registry",
        fake_registry_factory,
    ):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s1",
            rx_interface="eth0",
            stream_data={"rx_engine": "dpdk"},
        )
    assert result["requested"] is True
    assert result["actual"] == "dpdk"
    assert result["pid"] == 12345
    assert result["reason"] is None


def test_dpdk_stream_uses_operator_supplied_pci_bdf_override():
    """When the iface is already vfio-bound, iface_to_pci_bdf
    returns None. The operator can pass rx_pci_bdf in stream
    config; the helper should honor it without re-running the
    sysfs lookup (the lookup would just fail again)."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    fake_registry = MagicMock()
    fake_registry.start.return_value = {"pid": 99}
    fake_registry_factory = MagicMock(return_value=fake_registry)

    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value=None,
    ) as iface_lookup, patch(
        "utils.dpdk_rx_manager.registry",
        fake_registry_factory,
    ):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s1",
            rx_interface="eth0",
            stream_data={
                "rx_engine": "dpdk",
                "rx_pci_bdf": "0000:af:00.1",
            },
        )
    assert result["actual"] == "dpdk"
    assert result["pid"] == 99
    iface_lookup.assert_not_called()  # operator override wins


def test_dpdk_stream_already_running_treated_as_success():
    """Operator clicks Start twice on the same stream → registry
    raises ValueError("already running"). That's idempotent, not
    a fallback; outcome should still be actual=dpdk."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    fake_registry = MagicMock()
    fake_registry.start.side_effect = ValueError(
        "rx_worker already running for stream s1"
    )
    fake_registry_factory = MagicMock(return_value=fake_registry)

    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch(
        "utils.dpdk_rx_manager.registry",
        fake_registry_factory,
    ):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s1",
            rx_interface="eth0",
            stream_data={"rx_engine": "dpdk"},
        )
    assert result["requested"] is True
    assert result["actual"] == "dpdk"
    assert "already running" in result["reason"]


def test_helper_never_raises_into_caller():
    """The start_traffic loop calls this best-effort — it MUST
    NOT raise, even on completely malformed stream_data.
    Pre-fix v0.5.108 the helper raised AttributeError on
    stream_data["L3"] being a string instead of a dict and
    /api/traffic/start returned 500. Defensive contract:
    return a fallback outcome dict, never raise."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream

    # Junk values across every spot the helper reads.
    weird_payloads = [
        {"rx_engine": "dpdk", "protocol_data": "not-a-dict"},
        {"rx_engine": "dpdk", "protocol_data": {"ipv4": "str"}},
        {"rx_engine": "dpdk", "VLAN": ["tagged"]},  # list, not str
    ]
    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch("utils.dpdk_rx_manager.registry") as reg:
        reg.return_value.start.return_value = {"pid": 1}
        for p in weird_payloads:
            result = _maybe_start_dpdk_rx_for_stream(
                stream_id="s1", rx_interface="eth0", stream_data=p,
            )
            assert isinstance(result, dict)
            assert "actual" in result
