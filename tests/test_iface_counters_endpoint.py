"""v0.5.104: /api/admin/iface/<iface>/counters endpoint integration.

Pure endpoint-level test — mocks utils.nic_counters.read_counters
so we exercise the route + validation logic, not the sysfs
plumbing (covered by test_nic_counters.py).
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    pytest.importorskip("flask")
    tmp = tmp_path_factory.mktemp("netgen_db")
    os.environ["NETGEN_DB_PATH"] = str(tmp / "device.db")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_tgen_server", str(_SERVER),
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server.app.config["TESTING"] = True
    return server.app.test_client(), server


def test_counters_endpoint_returns_snapshot_shape(client, monkeypatch):
    c, _ = client
    fake_snap = {
        "source": "kernel",
        "iface": "ens2f1np1",
        "pci_bdf": "0000:2b:00.1",
        "binding": "kernel",
        "ts_ns": 1234567890,
        "rx_packets": 4_900_000, "tx_packets": 0,
        "rx_bytes": 313_600_000, "tx_bytes": 0,
        "rx_errors": 0, "tx_errors": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "extra": {"kernel": {"rx_packets": 4_900_000}},
        "warnings": [],
    }
    with mock.patch("utils.nic_counters.read_counters",
                    return_value=fake_snap):
        r = c.get("/api/admin/iface/ens2f1np1/counters")
    assert r.status_code == 200
    body = r.get_json()
    # Required keys present
    for k in ("source", "iface", "pci_bdf", "binding", "ts_ns",
              "rx_packets", "tx_packets", "warnings"):
        assert k in body
    assert body["rx_packets"] == 4_900_000


def test_counters_endpoint_rejects_invalid_iface_name(client):
    c, _ = client
    # Path traversal attempt + too long + special chars
    for bad in [
        "../etc",
        "a" * 16,
        "x;y",
        "name with space",
    ]:
        r = c.get(f"/api/admin/iface/{bad}/counters")
        # Either 400 (caught by our regex) or 404 (Flask routing)
        assert r.status_code in (400, 404), (
            f"iface={bad!r} returned {r.status_code}, expected 400/404"
        )


def test_counters_endpoint_validates_pci_bdf_query_param(client, monkeypatch):
    c, _ = client
    # Valid BDF format → accepted (mock the reader)
    fake_snap = {"source": None, "iface": "ens2f0np0",
                 "pci_bdf": "0000:2b:00.0", "binding": "vfio-pci",
                 "ts_ns": 0, "rx_packets": None, "tx_packets": None,
                 "rx_bytes": None, "tx_bytes": None,
                 "rx_errors": None, "tx_errors": None,
                 "rx_dropped": None, "tx_dropped": None,
                 "extra": {}, "warnings": []}
    with mock.patch("utils.nic_counters.read_counters",
                    return_value=fake_snap):
        r = c.get("/api/admin/iface/ens2f0np0/counters",
                  query_string={"pci_bdf": "0000:2b:00.0"})
    assert r.status_code == 200
    # Invalid BDF format → 400
    r = c.get("/api/admin/iface/ens2f0np0/counters",
              query_string={"pci_bdf": "not-a-bdf"})
    assert r.status_code == 400


def test_counters_endpoint_propagates_warnings(client, monkeypatch):
    """The vfio-bound case carries an actionable warning the
    UI surfaces. Endpoint must pass it through verbatim."""
    c, _ = client
    fake_snap = {
        "source": "mellanox-sysfs",
        "iface": "ens2f0np0",
        "pci_bdf": "0000:2b:00.0",
        "binding": "vfio-pci",
        "ts_ns": 999,
        "rx_packets": 0, "tx_packets": 5_000_000,
        "rx_bytes": 0, "tx_bytes": 320_000_000,
        "rx_errors": None, "tx_errors": None,
        "rx_dropped": None, "tx_dropped": 0,
        "extra": {"infiniband_device": "mlx5_2"},
        "warnings": [
            "iface ens2f0np0 is vfio-bound; counters come from "
            "Mellanox InfiniBand sysfs (mlx5_2) — kernel netdev "
            "path is unavailable while DPDK has the port",
        ],
    }
    with mock.patch("utils.nic_counters.read_counters",
                    return_value=fake_snap):
        r = c.get("/api/admin/iface/ens2f0np0/counters")
    body = r.get_json()
    assert any("vfio-bound" in w for w in body["warnings"])
    assert any("mlx5_2" in w for w in body["warnings"])
    assert body["source"] == "mellanox-sysfs"


def test_counters_endpoint_handles_reader_exception(client):
    """If the reader crashes (sysfs disappears mid-read, perm
    denied), endpoint returns 500 with the exception class —
    not a stack trace."""
    c, _ = client
    with mock.patch("utils.nic_counters.read_counters",
                    side_effect=RuntimeError("simulated")):
        r = c.get("/api/admin/iface/ens2f1np1/counters")
    assert r.status_code == 500
    body = r.get_json()
    assert "error" in body
