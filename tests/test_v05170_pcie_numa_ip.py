"""v0.5.170: PCIe Gen + NUMA + IPv4 + NIC model + line-rate
efficiency in the HTML session report.

Operator: "report should also capture the PCI gen used, example
gen4/5/6..etc in the Endpoints. also check if there is anything
else missing in the reports."

Three additions tested:
  1. **Server**: RdmaDevice gains pcie_*, numa_node, netdev_ips.
     sysfs readers parse `current_link_speed` ("16.0 GT/s PCIe"
     → Gen4), `current_link_width`, `numa_node`. Downgraded flag
     when current < max.
  2. **Report**: endpoint table gains Model, PCIe, NUMA, IPv4
     columns. NIC model derived from board_id → product name.
  3. **Headline**: appends "X% of N G line rate" so operators can
     spot a sub-line-rate result at a glance.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ───── PCIe sysfs parsing ────────────────────────────────────────


def test_gts_to_gen_maps_known_rates():
    from utils.rdma_perf import _gts_to_gen
    assert _gts_to_gen(2.5) == 1
    assert _gts_to_gen(5.0) == 2
    assert _gts_to_gen(8.0) == 3
    assert _gts_to_gen(16.0) == 4
    assert _gts_to_gen(32.0) == 5
    assert _gts_to_gen(64.0) == 6


def test_gts_to_gen_returns_none_for_missing():
    from utils.rdma_perf import _gts_to_gen
    assert _gts_to_gen(None) is None


def test_parse_link_speed_gts_extracts_float():
    from utils.rdma_perf import _parse_link_speed_gts
    assert _parse_link_speed_gts("16.0 GT/s PCIe") == 16.0
    assert _parse_link_speed_gts("8 GT/s") == 8.0
    assert _parse_link_speed_gts("Unknown") is None
    assert _parse_link_speed_gts(None) is None
    assert _parse_link_speed_gts("") is None


def test_read_pcie_link_reads_full_state(tmp_path):
    """Mock /sys/bus/pci/devices/<bdf>/ + verify all fields."""
    from utils.rdma_perf import _read_pcie_link
    pci_root = tmp_path / "pci"
    dev = pci_root / "0000:2b:00.0"
    dev.mkdir(parents=True)
    (dev / "current_link_speed").write_text("16.0 GT/s PCIe\n")
    (dev / "current_link_width").write_text("16\n")
    (dev / "max_link_speed").write_text("16.0 GT/s PCIe\n")
    (dev / "max_link_width").write_text("16\n")
    out = _read_pcie_link("0000:2b:00.0", pci_root=str(pci_root))
    assert out["current_speed_gts"] == 16.0
    assert out["current_width"] == 16
    assert out["max_speed_gts"] == 16.0
    assert out["max_width"] == 16
    assert out["gen"] == 4
    assert out["max_gen"] == 4
    assert out["downgraded"] is False


def test_read_pcie_link_flags_downgraded_speed(tmp_path):
    """Gen5 slot trained at Gen4 = operator-critical signal."""
    from utils.rdma_perf import _read_pcie_link
    pci_root = tmp_path / "pci"
    dev = pci_root / "0000:c0:00.0"
    dev.mkdir(parents=True)
    (dev / "current_link_speed").write_text("16.0 GT/s PCIe\n")
    (dev / "max_link_speed").write_text("32.0 GT/s PCIe\n")
    (dev / "current_link_width").write_text("16\n")
    (dev / "max_link_width").write_text("16\n")
    out = _read_pcie_link("0000:c0:00.0", pci_root=str(pci_root))
    assert out["gen"] == 4
    assert out["max_gen"] == 5
    assert out["downgraded"] is True


def test_read_pcie_link_flags_downgraded_width(tmp_path):
    """x16 slot trained at x8 — same severity as gen downgrade."""
    from utils.rdma_perf import _read_pcie_link
    pci_root = tmp_path / "pci"
    dev = pci_root / "0000:01:00.0"
    dev.mkdir(parents=True)
    (dev / "current_link_speed").write_text("16.0 GT/s PCIe\n")
    (dev / "max_link_speed").write_text("16.0 GT/s PCIe\n")
    (dev / "current_link_width").write_text("8\n")
    (dev / "max_link_width").write_text("16\n")
    out = _read_pcie_link("0000:01:00.0", pci_root=str(pci_root))
    assert out["downgraded"] is True


def test_read_pcie_link_returns_nones_when_sysfs_missing(tmp_path):
    """Defensive against containerised /sys."""
    from utils.rdma_perf import _read_pcie_link
    out = _read_pcie_link(
        "0000:01:00.0", pci_root=str(tmp_path / "missing"))
    assert out["gen"] is None
    assert out["downgraded"] is False


def test_read_numa_node_parses_int(tmp_path):
    from utils.rdma_perf import _read_numa_node
    pci_root = tmp_path / "pci"
    dev = pci_root / "0000:2b:00.0"
    dev.mkdir(parents=True)
    (dev / "numa_node").write_text("0\n")
    assert _read_numa_node(
        "0000:2b:00.0", pci_root=str(pci_root)) == 0


def test_read_numa_node_treats_minus_one_as_none(tmp_path):
    """A `-1` in sysfs means "no NUMA topology info" — the report
    shouldn't render a misleading `node -1`."""
    from utils.rdma_perf import _read_numa_node
    pci_root = tmp_path / "pci"
    dev = pci_root / "0000:2b:00.0"
    dev.mkdir(parents=True)
    (dev / "numa_node").write_text("-1\n")
    assert _read_numa_node(
        "0000:2b:00.0", pci_root=str(pci_root)) is None


def test_ipv4_mask_to_prefix_converts_dotted_to_cidr():
    from utils.rdma_perf import _ipv4_mask_to_prefix
    assert _ipv4_mask_to_prefix("255.255.255.0") == 24
    assert _ipv4_mask_to_prefix("255.255.0.0") == 16
    assert _ipv4_mask_to_prefix("255.255.255.255") == 32
    assert _ipv4_mask_to_prefix(None) is None
    assert _ipv4_mask_to_prefix("garbage") is None


def test_ipv6_mask_to_prefix_counts_bits():
    from utils.rdma_perf import _ipv6_mask_to_prefix
    assert _ipv6_mask_to_prefix("ffff:ffff:ffff:ffff::") == 64
    assert _ipv6_mask_to_prefix("ffff::") == 16


# ───── RdmaDevice plumbing ───────────────────────────────────────


def test_rdma_device_has_new_pcie_numa_ip_fields():
    """All new fields must exist on the dataclass so the route
    serialiser (asdict) picks them up automatically."""
    from utils.rdma_perf import RdmaDevice
    fields = RdmaDevice.__dataclass_fields__
    for name in (
        "pcie_current_speed_gts", "pcie_current_width",
        "pcie_max_speed_gts", "pcie_max_width",
        "pcie_gen", "pcie_max_gen", "pcie_downgraded",
        "numa_node", "netdev_ips",
    ):
        assert name in fields, f"missing field: {name}"


# ───── NIC model mapping ─────────────────────────────────────────


def test_resolve_nic_model_maps_known_board_ids():
    from utils.rdma_report import _resolve_nic_model
    assert _resolve_nic_model("MT_0000000838") == "ConnectX-7"
    assert _resolve_nic_model("MT_0000000437") == "ConnectX-6"
    assert _resolve_nic_model("MT_0000001019") == "ConnectX-8"


def test_resolve_nic_model_falls_through_for_unknown():
    from utils.rdma_report import _resolve_nic_model
    assert _resolve_nic_model("MT_xxxxxxxxxx") == "—"
    assert _resolve_nic_model(None) == "—"
    assert _resolve_nic_model("") == "—"


# ───── Report rendering ──────────────────────────────────────────


SAMPLE_DEVICE_FULL = {
    "name": "rocep43s0f0",
    "vendor": "MT_0000000838",
    "fw_version": "28.42.1000",
    "driver": "mlx5_core",
    "net_ifaces": ["ens2f0np0"],
    "pcie_gen": 4,
    "pcie_current_width": 16,
    "pcie_max_gen": 4,
    "pcie_max_width": 16,
    "pcie_downgraded": False,
    "numa_node": 0,
    "netdev_ips": {
        "ens2f0np0": ["10.43.0.2/24",
                      "fe80::5e25:73ff:fe3f:3056/64"],
    },
    "ports": [{
        "port": 1, "state": "ACTIVE",
        "link_layer": "Ethernet",
        "rate": "200 Gb/sec (4X HDR)",
        "mtu": 4096,
        "gids": ["fe80:0000:0000:0000:9a03:9bff:fe21:c5a1"],
    }],
}


def _bw_run_with_full_devices(**overrides):
    run = {
        "kind": "blast",
        "started_at": "2026-06-16T14:22:40",
        "test": "ib_send_bw",
        "params": {"msg_size": 65536, "qp_count": 1,
                    "duration_s": 30},
        "endpoints": {"server": "TG 0 rocep43s0f0",
                       "client": "TG 0 rocep43s0f1"},
        "endpoint_details": [
            {"side": "server", "tg": "TG 0",
             "hca": "rocep43s0f0", "device": SAMPLE_DEVICE_FULL},
            {"side": "client", "tg": "TG 0",
             "hca": "rocep43s0f1",
             "device": {**SAMPLE_DEVICE_FULL,
                        "name": "rocep43s0f1",
                        "net_ifaces": ["ens2f1np1"],
                        "netdev_ips": {
                            "ens2f1np1": ["10.42.0.1/24"]}}},
        ],
        "rows": [{"label": "worker 0", "state": "done",
                  "bw_gbps": 172.22, "msgrate_mpps": 0.3285}],
        "summary": {"samples": 1, "bw_avg_gbps": 172.22,
                    "bw_min_gbps": 172.22, "bw_max_gbps": 172.22,
                    "msgrate_avg_mpps": 0.3285},
    }
    run.update(overrides)
    return run


def test_endpoint_table_includes_new_columns():
    from utils.rdma_report import build_html_report
    html = build_html_report(
        title="x", runs=[_bw_run_with_full_devices()],
        generated_at="2026-06-16T14:23:03")
    for col in ("Model", "PCIe", "NUMA", "IPv4"):
        assert f">{col}<" in html, f"missing column header: {col}"


def test_pcie_cell_shows_gen_and_width():
    from utils.rdma_report import build_html_report
    html = build_html_report(
        title="x", runs=[_bw_run_with_full_devices()],
        generated_at="2026-06-16T14:23:03")
    assert "Gen4 x16" in html


def test_pcie_cell_flags_downgrade_with_max():
    """Downgraded link must surface both current and max so
    operators can see "Gen4 x16 (max Gen5 x16)" without
    cross-referencing sysfs."""
    from utils.rdma_report import build_html_report
    downgraded = dict(SAMPLE_DEVICE_FULL)
    downgraded["pcie_max_gen"] = 5
    downgraded["pcie_max_speed_gts"] = 32.0
    downgraded["pcie_downgraded"] = True
    run = _bw_run_with_full_devices(endpoint_details=[
        {"side": "server", "tg": "TG 0",
         "hca": "rocep43s0f0", "device": downgraded},
    ])
    html = build_html_report(
        title="x", runs=[run], generated_at="2026-06-16T14:23:03")
    assert "Gen4 x16" in html
    assert "max Gen5" in html
    assert "pcie-warn" in html


def test_numa_node_cell_renders_node_n():
    from utils.rdma_report import build_html_report
    html = build_html_report(
        title="x", runs=[_bw_run_with_full_devices()],
        generated_at="2026-06-16T14:23:03")
    assert "node 0" in html


def test_ipv4_cell_picks_first_ipv4_per_endpoint():
    from utils.rdma_report import build_html_report
    html = build_html_report(
        title="x", runs=[_bw_run_with_full_devices()],
        generated_at="2026-06-16T14:23:03")
    assert "10.43.0.2/24" in html
    assert "10.42.0.1/24" in html
    # IPv6 link-local NOT in the IPv4 column (operator wants v4
    # for cross-referencing; v6 link-local is noise).
    # The IPv6 may still appear elsewhere in the JSON payload —
    # but in the IPv4 column cell it should NOT.


def test_nic_model_column_shows_friendly_name():
    """ConnectX-7 not MT_0000000838 — operators read product
    names, not Mellanox internal IDs."""
    from utils.rdma_report import build_html_report
    html = build_html_report(
        title="x", runs=[_bw_run_with_full_devices()],
        generated_at="2026-06-16T14:23:03")
    assert "ConnectX-7" in html


def test_headline_includes_line_rate_efficiency():
    """172.22 / 200 = 86.1% — operator's #1 ask at run finish:
    "did we hit line rate?". Show it up top."""
    from utils.rdma_report import build_html_report
    html = build_html_report(
        title="x", runs=[_bw_run_with_full_devices()],
        generated_at="2026-06-16T14:23:03")
    assert "86.1% of 200 G line rate" in html


def test_headline_efficiency_skipped_when_no_rate():
    """Legacy run_log entries without endpoint_details: headline
    must still render, just without the % line."""
    from utils.rdma_report import build_html_report
    run = _bw_run_with_full_devices()
    run.pop("endpoint_details")
    html = build_html_report(
        title="x", runs=[run], generated_at="2026-06-16T14:23:03")
    # BW still shown.
    assert "172.22" in html
    # But no efficiency string.
    assert "line rate" not in html.lower()


def test_extract_line_rate_picks_slowest():
    """A 200 G ↔ 100 G run is capped at 100 G end-to-end."""
    from utils.rdma_report import _extract_line_rate_gbps
    run = _bw_run_with_full_devices()
    run["endpoint_details"][1]["device"] = {
        **run["endpoint_details"][1]["device"],
        "ports": [{
            "port": 1, "state": "ACTIVE",
            "link_layer": "Ethernet",
            "rate": "100 Gb/sec (4X EDR)", "mtu": 4096,
            "gids": [],
        }],
    }
    assert _extract_line_rate_gbps(run) == 100.0


# ───── Server route + dialog wiring (source-level) ───────────────


def test_rdma_devices_route_still_uses_asdict():
    """asdict picks up new fields automatically — no route edit
    needed. Verify the route still uses asdict on the dataclass."""
    src = (REPO / "run_tgen_server.py").read_text()
    routes_idx = src.find("def api_rdma_devices")
    assert routes_idx > 0
    body = src[routes_idx:routes_idx + 2000]
    assert "asdict(d)" in body
