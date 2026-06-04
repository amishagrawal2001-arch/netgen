"""Smoke tests for widgets/rdma_blast_flow_dialog.py + the engine
combo refactor in widgets/stream_dialog.py — v0.3.12."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PyQt5 = pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


def test_rdma_blast_flow_dialog_constructs():
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    d = RdmaBlastFlowDialog(
        "http://10.0.0.1:5050", "http://10.0.0.2:5050",
        server_tg_label="TG-A", client_tg_label="TG-B",
    )
    assert d.windowTitle() == "Blast a RDMA Flow"
    # All test types listed
    test_ids = [d._test_combo.itemData(i) for i in range(d._test_combo.count())]
    assert "send_bw" in test_ids
    assert "write_bw" in test_ids
    assert "read_bw" in test_ids
    assert "send_lat" in test_ids
    assert "write_lat" in test_ids
    assert "read_lat" in test_ids
    # Common opts defaults
    opts = d._common_opts()
    assert opts["msg_size"] == 65536
    assert opts["qp_count"] == 1
    assert opts["duration"] == 30
    assert opts["report_gbits"] is True
    d.close()


def test_rdma_blast_flow_dialog_loopback_mode():
    """Constructing without a client URL → loopback (both URLs equal)."""
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    d = RdmaBlastFlowDialog("http://10.0.0.1:5050")
    assert d._server_tg_url == d._client_tg_url
    d.close()


def test_engine_combo_default_is_scapy():
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(parent=None, stream_data={"stream_id": "x"},
                        server_interfaces=[])
    assert hasattr(d, "engine_combo")
    assert d.engine_combo.currentData() == "scapy"
    out = d.get_stream_details()
    assert out["engine"] == "scapy"
    assert out["dpdk_enable"] is False
    assert out["rdma"] is None
    d.close()


def test_engine_combo_dpdk_syncs_checkbox():
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(parent=None, stream_data={"stream_id": "x"},
                        server_interfaces=[])
    idx = d.engine_combo.findData("dpdk")
    d.engine_combo.setCurrentIndex(idx)
    assert d.dpdk_enable_checkbox.isChecked() is True
    out = d.get_stream_details()
    assert out["engine"] == "dpdk"
    assert out["dpdk_enable"] is True
    d.close()


def test_engine_combo_rdma_disables_checkbox():
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(parent=None, stream_data={"stream_id": "x"},
                        server_interfaces=[])
    idx = d.engine_combo.findData("rdma")
    d.engine_combo.setCurrentIndex(idx)
    assert d.dpdk_enable_checkbox.isEnabled() is False
    # Setting peer + tweaking params lands in save shape.
    d.rdma_peer_field.setText("10.0.0.99")
    d.rdma_msg_size_spin.setValue(8192)
    d.rdma_duration_spin.setValue(45)
    out = d.get_stream_details()
    assert out["engine"] == "rdma"
    assert out["dpdk_enable"] is False
    assert out["rdma"]["peer_addr"] == "10.0.0.99"
    assert out["rdma"]["msg_size"] == 8192
    assert out["rdma"]["duration"] == 45
    d.close()


def test_dpdk_checkbox_toggle_propagates_to_combo():
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(parent=None, stream_data={"stream_id": "x"},
                        server_interfaces=[])
    # Operator ticks checkbox directly — combo should follow.
    d.dpdk_enable_checkbox.setChecked(True)
    assert d.engine_combo.currentData() == "dpdk"
    d.dpdk_enable_checkbox.setChecked(False)
    assert d.engine_combo.currentData() == "scapy"
    d.close()


def test_legacy_dpdk_enable_resolves_to_dpdk_engine():
    """Pre-v0.3.12 saves only had dpdk_enable. Load must resolve to
    engine=dpdk so the combo + save shape stays coherent."""
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(parent=None, stream_data={"stream_id": "x"},
                        server_interfaces=[])
    d.populate_stream_fields({"dpdk_enable": True})
    assert d.engine_combo.currentData() == "dpdk"
    d.close()


def test_engine_save_load_round_trip_rdma():
    """Save an RDMA stream, feed back to a fresh dialog, verify state
    survives intact."""
    from widgets.stream_dialog import AddStreamDialog
    d1 = AddStreamDialog(parent=None, stream_data={"stream_id": "x"},
                         server_interfaces=[])
    idx = d1.engine_combo.findData("rdma")
    d1.engine_combo.setCurrentIndex(idx)
    d1.rdma_peer_field.setText("10.0.0.50")
    d1.rdma_test_combo.setCurrentIndex(d1.rdma_test_combo.findData("write_bw"))
    d1.rdma_msg_size_spin.setValue(16384)
    d1.rdma_qp_count_spin.setValue(4)
    d1.rdma_duration_spin.setValue(60)
    d1.rdma_gid_index_spin.setValue(1)
    d1.rdma_bidir_check.setChecked(True)
    saved = d1.get_stream_details()

    d2 = AddStreamDialog(parent=None, stream_data={"stream_id": "y"},
                         server_interfaces=[])
    d2.populate_stream_fields(saved)
    assert d2.engine_combo.currentData() == "rdma"
    assert d2.rdma_peer_field.text() == "10.0.0.50"
    assert d2.rdma_test_combo.currentData() == "write_bw"
    assert d2.rdma_msg_size_spin.value() == 16384
    assert d2.rdma_qp_count_spin.value() == 4
    assert d2.rdma_duration_spin.value() == 60
    assert d2.rdma_gid_index_spin.value() == 1
    assert d2.rdma_bidir_check.isChecked() is True
    d1.close()
    d2.close()


def test_menu_action_class_loads_into_main_window():
    """The RDMA mixin should land on TrafficGeneratorClient."""
    from traffic_client.main import TrafficGeneratorClient
    assert hasattr(TrafficGeneratorClient, "show_rdma_blast_flow_dialog")
    assert hasattr(TrafficGeneratorClient, "show_rdma_devices_dialog")
    assert hasattr(TrafficGeneratorClient, "show_rdma_jobs_dialog")
