"""v0.5.114: bifurcated-Mellanox detector + smart rx_engine
default + /api/interfaces/<iface>/rx_engine_advice endpoint.

Pins the srv06 saga's hard-won lesson into the code: on
Mellanox bifurcated kernel-bound mode, rx_worker reliably
grabs the chip queue and dies, leaving the kernel blind. The
dialog should default rx_engine to Scapy on those NICs (and
warn the operator when they override).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, mock_open

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_bifurcated_detector_true_for_mellanox():
    """mlx5_core driver + infiniband devnode = bifurcated
    Mellanox, the srv06 case."""
    from utils.nic_counters import is_mellanox_bifurcated_kernel
    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch("os.readlink", return_value="../drivers/mlx5_core"), \
         patch("os.path.isdir", return_value=True):
        assert is_mellanox_bifurcated_kernel("ens2f0np0") is True


def test_bifurcated_detector_false_for_intel():
    """i40e / ixgbe / etc — not bifurcated Mellanox, no warning."""
    from utils.nic_counters import is_mellanox_bifurcated_kernel
    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch("os.readlink", return_value="../drivers/i40e"):
        assert is_mellanox_bifurcated_kernel("eth0") is False


def test_bifurcated_detector_false_when_no_pci_bdf():
    """vfio-bound or unplugged iface → no BDF → cannot detect →
    return False (don't false-positive)."""
    from utils.nic_counters import is_mellanox_bifurcated_kernel
    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value=None,
    ):
        assert is_mellanox_bifurcated_kernel("vfio-iface") is False


def test_bifurcated_detector_false_when_no_infiniband_devnode():
    """mlx5_core driver but no infiniband subdir — virtual
    function or legacy non-RDMA Mellanox. Not the chip family
    that triggers the bug."""
    from utils.nic_counters import is_mellanox_bifurcated_kernel
    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.0",
    ), patch("os.readlink", return_value="../drivers/mlx5_core"), \
         patch("os.path.isdir", return_value=False):
        assert is_mellanox_bifurcated_kernel("ens2f0np0") is False


def test_advice_endpoint_returns_scapy_on_bifurcated():
    """Endpoint contract: bifurcated NIC → recommended=scapy
    with bifurcated_mellanox=True and reason explaining why."""
    from run_tgen_server import app
    with patch(
        "utils.nic_counters.is_mellanox_bifurcated_kernel",
        return_value=True,
    ):
        with app.test_client() as c:
            r = c.get("/api/interfaces/ens2f0np0/rx_engine_advice")
    assert r.status_code == 200
    body = r.get_json()
    assert body["recommended"] == "scapy"
    assert body["bifurcated_mellanox"] is True
    assert "Mellanox" in body["reason"]


def test_advice_endpoint_returns_dpdk_on_normal_nic():
    """Standard non-Mellanox NIC → recommended=dpdk."""
    from run_tgen_server import app
    with patch(
        "utils.nic_counters.is_mellanox_bifurcated_kernel",
        return_value=False,
    ):
        with app.test_client() as c:
            r = c.get("/api/interfaces/eth0/rx_engine_advice")
    assert r.status_code == 200
    body = r.get_json()
    assert body["recommended"] == "dpdk"
    assert body["bifurcated_mellanox"] is False


def test_advice_endpoint_rejects_path_traversal():
    """Same iface-name validator as the MAC endpoint — must
    reject overlong / shell-meta / path-traversal names."""
    from run_tgen_server import app
    bad = ["../etc/passwd", "abcdefghijklmnop", "eth$0", ""]
    with app.test_client() as c:
        for name in bad:
            r = c.get(f"/api/interfaces/{name}/rx_engine_advice")
            assert r.status_code in (400, 404), (
                f"name {name!r} should be rejected, got "
                f"{r.status_code}"
            )


def test_dialog_defaults_rx_engine_to_recommended_on_add(qapp, monkeypatch):
    """v0.5.114: Add Stream (no saved rx_engine) → combo defaults
    to whatever the server recommends. Test path is normal NIC →
    recommends 'dpdk' → combo set to dpdk on dialog open."""
    from widgets.stream_dialog import AddStreamDialog

    def _fake_get(url, *a, **kw):
        class _R:
            ok = True
            def json(self): return {
                "interface": "eth0",
                "recommended": "dpdk",
                "reason": "Standard NIC",
                "bifurcated_mellanox": False,
            }
        return _R()
    monkeypatch.setattr("requests.get", _fake_get)

    d = AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: eth0",
        stream_data=None,
        server_interfaces=[{
            "tg_id": "0",
            "address": "http://1.1.1.1:5050",
            "online": True,
        }],
    )
    try:
        d.populate_stream_fields({})  # Add path — no saved data
        assert d.rx_engine_combo.currentData() == "dpdk"
    finally:
        d.deleteLater()


def test_dialog_respects_explicit_rx_engine_on_edit(qapp, monkeypatch):
    """Edit path: stream_data has explicit rx_engine="scapy" →
    combo MUST stay at scapy even if server recommends dpdk.
    Operator's choice wins."""
    from widgets.stream_dialog import AddStreamDialog

    def _fake_get(url, *a, **kw):
        class _R:
            ok = True
            def json(self): return {
                "recommended": "dpdk",
                "reason": "Standard NIC",
                "bifurcated_mellanox": False,
            }
        return _R()
    monkeypatch.setattr("requests.get", _fake_get)

    d = AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: eth0",
        stream_data={"rx_engine": "scapy"},
        server_interfaces=[{
            "tg_id": "0",
            "address": "http://1.1.1.1:5050",
            "online": True,
        }],
    )
    try:
        d.populate_stream_fields({"rx_engine": "scapy"})
        assert d.rx_engine_combo.currentData() == "scapy"
    finally:
        d.deleteLater()


def test_dialog_warning_chip_when_overriding_bifurcated_recommendation(
    qapp, monkeypatch,
):
    """Operator on Mellanox bifurcated NIC sets rx_engine=DPDK
    → server recommends scapy → red warning chip surfaces with
    the bifurcated-Mellanox reason."""
    from widgets.stream_dialog import AddStreamDialog

    def _fake_get(url, *a, **kw):
        class _R:
            ok = True
            def json(self): return {
                "recommended": "scapy",
                "reason": "Mellanox bifurcated...",
                "bifurcated_mellanox": True,
            }
        return _R()
    monkeypatch.setattr("requests.get", _fake_get)

    d = AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: ens2f0np0",
        stream_data=None,
        server_interfaces=[{
            "tg_id": "0",
            "address": "http://1.1.1.1:5050",
            "online": True,
        }],
    )
    d.show()
    try:
        # Manually override to DPDK (against recommendation)
        idx = d.rx_engine_combo.findData("dpdk")
        d.rx_engine_combo.setCurrentIndex(idx)
        d._refresh_rx_engine_advice()
        assert not d._rx_engine_advice_label.isHidden()
        text = d._rx_engine_advice_label.text()
        assert "Mellanox bifurcated" in text or "chip queue" in text
    finally:
        d.deleteLater()
