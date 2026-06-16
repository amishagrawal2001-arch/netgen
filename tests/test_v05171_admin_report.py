"""v0.5.171: HTML report export for the admin portal.

Operator: "also allow user to generate report for admin portal
http://san-hp-srv06:5050/admin"

Three pieces tested:
  1. **Pure builder** `utils.admin_report.build_admin_report_html`
     takes a merged snapshot dict and returns a self-contained
     HTML document. Verified section-by-section.
  2. **Route** `GET /api/admin/report.html` returns
     Content-Type: text/html with a Content-Disposition
     attachment filename. Source-level (server module is too
     heavy to import here).
  3. **Admin page wiring** — Export Report button + handler are
     present in `_ADMIN_HTML`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.admin_report import build_admin_report_html


# ───── builder fixtures ──────────────────────────────────────────


SAMPLE_HEALTH = {
    "hostname": "san-hp-srv06",
    "netgen_server": {"port": 5050},
    "dpdk": {"installed": True, "version": "23.11.2"},
    "iommu": {"enabled": True,
              "cmdline_excerpt":
                  "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=pt"},
    "vfio": {"vfio_pci": True, "vfio_iommu_type1": True},
    "hugepages": {"by_node": [
        {"node": 0, "nr_hugepages": 1024,
         "free_hugepages": 1024, "size_kb": 2048},
        {"node": 1, "nr_hugepages": 1024,
         "free_hugepages": 1020, "size_kb": 2048},
    ]},
    "tx_worker": {"present": True,
                  "path": "/usr/local/bin/tx_worker"},
    "install_running": False,
    "tools_present": {
        "ip": True, "ethtool": True, "lldpcli": True,
        "lspci": True, "ibv_devinfo": True,
        "perftest": True, "dpdk_devbind": True,
    },
}


SAMPLE_INTERFACES = [
    {"name": "ens2f0np0", "driver": "mlx5_core",
     "mac": "5c:25:73:3f:30:56", "ipv4": "10.43.0.2/24",
     "speed": 200000, "mtu": 1500, "operstate": "up",
     "numa_node": 0,
     "pcie_gen": 4, "pcie_current_width": 16,
     "nic_model": "ConnectX-7"},
    {"name": "ens2f1np1", "driver": "mlx5_core",
     "mac": "5c:25:73:3f:30:57", "ipv4": "10.42.0.1/24",
     "speed": 200000, "mtu": 1500, "operstate": "up",
     "numa_node": 0,
     "pcie_gen": 4, "pcie_current_width": 16,
     "nic_model": "ConnectX-7"},
]


SAMPLE_RDMA = [
    {"name": "rocep43s0f0", "vendor": "MT_0000000838",
     "fw_version": "28.42.1000", "driver": "mlx5_core",
     "net_ifaces": ["ens2f0np0"], "numa_node": 0,
     "pcie_gen": 4, "pcie_current_width": 16,
     "netdev_ips": {"ens2f0np0": ["10.43.0.2/24"]},
     "ports": [{"port": 1, "state": "ACTIVE",
                "link_layer": "Ethernet",
                "rate": "200 Gb/sec (4X HDR)",
                "mtu": 4096,
                "gids": ["fe80:0000:0000:0000:9a03:9bff:fe21:c5a1"]}]},
]


SAMPLE_BIND_HISTORY = [
    {"timestamp": "2026-06-16T22:00:00Z",
     "pci": "0000:2b:00.0",
     "from_driver": "mlx5_core", "to_driver": "vfio-pci",
     "action": "bind"},
]


SAMPLE_ORPHANS = [
    {"pid": 3194868, "role": "tx",
     "stream_id": "3ede73ca-79a1-4d1e-adac-e1aa85662fed",
     "bdf": "0000:2b:00.0", "etime_seconds": 800,
     "cmdline": "/usr/local/bin/tx_worker -l 0,1,2 ..."},
]


def _build_full_snapshot():
    return {
        "health": SAMPLE_HEALTH,
        "interfaces": SAMPLE_INTERFACES,
        "rdma_devices": SAMPLE_RDMA,
        "bind_history": SAMPLE_BIND_HISTORY,
        "orphans": SAMPLE_ORPHANS,
    }


# ───── builder shape ────────────────────────────────────────────


def test_report_renders_complete_html_document():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(),
        generated_at="2026-06-16 22:00:00 UTC",
        server_version="0.5.171",
    )
    assert "<!DOCTYPE html>" in html
    assert "<title>Admin Portal" in html
    # Footer present so the operator knows it's self-contained.
    assert "Self-contained HTML" in html


def test_report_intro_shows_host_and_version():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(),
        generated_at="2026-06-16 22:00:00 UTC",
        server_version="0.5.171",
    )
    assert "san-hp-srv06" in html
    assert "0.5.171" in html
    assert "Generated:" in html


def test_report_works_with_empty_snapshot():
    """Defensive — the route may call this when the server is in
    a partial state (e.g. /api/rdma/devices returned []). Must
    still produce a valid document, not crash."""
    html = build_admin_report_html(
        snapshot={}, generated_at="2026-06-16 22:00:00 UTC",
    )
    assert "<!DOCTYPE html>" in html
    # Sections that handle empty data with a friendly placeholder.
    assert "No interface data" in html or "Network interfaces" in html


# ───── per-section content ──────────────────────────────────────


def test_health_section_shows_dpdk_iommu_vfio():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(),
        generated_at="x",
    )
    assert "DPDK installed" in html
    assert "23.11.2" in html
    assert "IOMMU enabled" in html
    assert "vfio-pci" in html
    assert "intel_iommu=on" in html


def test_health_section_pills_reflect_status():
    """Falsy values should render as MISSING (red pill); truthy as
    OK (green)."""
    snap = _build_full_snapshot()
    snap["health"] = {**SAMPLE_HEALTH,
                       "dpdk": {"installed": False, "version": None}}
    html = build_admin_report_html(snapshot=snap, generated_at="x")
    assert "MISSING" in html
    # Bin tx_worker stayed True — should still show OK.
    assert "OK" in html


def test_tools_section_lists_each_tool():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    for t in ("ip", "ethtool", "lldpcli", "lspci",
              "ibv_devinfo", "perftest", "dpdk_devbind"):
        assert t in html


def test_hugepages_section_renders_per_node_rows():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    assert "Hugepages" in html
    assert "node 0" in html
    assert "node 1" in html
    assert "1024" in html
    assert "2048 kB" in html


def test_interfaces_section_renders_pcie_and_numa():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    assert "ens2f0np0" in html
    assert "Gen4 x16" in html
    assert "10.43.0.2/24" in html
    assert "node 0" in html
    assert "ConnectX-7" in html
    assert "5c:25:73:3f:30:56" in html


def test_interface_link_state_uses_up_pill():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    # Two interfaces, both UP → two OK pills (at least).
    assert html.count("pill ok'>UP") >= 2


def test_rdma_section_renders_full_hca_row():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    assert "RDMA HCAs" in html
    assert "rocep43s0f0" in html
    assert "200 Gb/sec" in html
    # The "(4X HDR)" classifier must be stripped — same convention
    # as the RDMA session report.
    assert "(4X HDR)" not in html
    assert "ConnectX-7" in html
    assert "mlx5_core" in html
    assert "ACTIVE" in html


def test_bind_history_section_renders_recent():
    html = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    assert "bind history" in html.lower()
    assert "0000:2b:00.0" in html
    assert "vfio-pci" in html


def test_orphans_section_renders_when_present_and_skipped_when_empty():
    html_with = build_admin_report_html(
        snapshot=_build_full_snapshot(), generated_at="x")
    assert "Orphan workers" in html_with
    assert "3194868" in html_with
    # Empty list → section dropped entirely.
    snap_no = {**_build_full_snapshot(), "orphans": []}
    html_no = build_admin_report_html(
        snapshot=snap_no, generated_at="x")
    assert "Orphan workers" not in html_no


def test_html_escape_applied_to_hostname_and_paths():
    """Server-controlled fields (hostname, tx_worker path,
    cmdline excerpt) must still be HTML-escaped — a hostile or
    accidentally-set value can't break the layout."""
    snap = _build_full_snapshot()
    snap["health"] = {
        **SAMPLE_HEALTH,
        "hostname": "<script>alert(1)</script>",
        "tx_worker": {"present": True,
                       "path": "/usr/<bad>/tx_worker"},
    }
    html = build_admin_report_html(snapshot=snap, generated_at="x")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;bad&gt;" in html


def test_etime_text_formats_seconds_minutes_hours():
    from utils.admin_report import _etime_txt
    assert _etime_txt(45) == "45s"
    assert _etime_txt(125) == "2m5s"
    assert _etime_txt(7265) == "2h1m"
    assert _etime_txt(None) == "?"


# ───── route + admin page wiring (source-level) ─────────────────


def test_report_route_registered():
    src = (REPO / "run_tgen_server.py").read_text()
    assert "/api/admin/report.html" in src
    assert "def admin_report_html" in src
    assert "from utils.admin_report import build_admin_report_html" in src


def test_report_route_serves_attachment_with_html_mimetype():
    """Operator should see a file download, not an in-tab render —
    same UX as the diag bundle. Verify the Content-Disposition
    header is set."""
    src = (REPO / "run_tgen_server.py").read_text()
    route_idx = src.find("def admin_report_html")
    assert route_idx > 0
    body = src[route_idx:route_idx + 4000]
    assert 'mimetype="text/html"' in body
    assert "Content-Disposition" in body
    assert "attachment" in body


def test_report_route_fetches_expected_apis():
    """The route must consume health + interfaces + rdma_devices +
    bind_history + orphans so the rendered report matches what
    the operator sees in /admin."""
    src = (REPO / "run_tgen_server.py").read_text()
    route_idx = src.find("def admin_report_html")
    body = src[route_idx:route_idx + 4000]
    assert "/api/admin/health" in body
    assert "/api/interfaces" in body
    assert "/api/rdma/devices" in body
    assert "/api/admin/bind_history" in body
    assert "/api/streams/orphans" in body


def test_admin_page_has_export_report_button():
    src = (REPO / "run_tgen_server.py").read_text()
    # Button is in the diagnostics card next to the existing
    # Export Diagnostics button.
    assert 'id="btn-export-report"' in src
    assert "📄 Export Report" in src


def test_admin_page_has_export_report_click_handler():
    src = (REPO / "run_tgen_server.py").read_text()
    assert "btn-export-report" in src
    handler_idx = src.find("$('btn-export-report').addEventListener")
    assert handler_idx > 0
    handler_body = src[handler_idx:handler_idx + 2000]
    assert "/api/admin/report.html" in handler_body
    # Must use the standard download-blob pattern so the operator
    # actually gets a file in their Downloads dir.
    assert "URL.createObjectURL" in handler_body
    assert "a.download" in handler_body
