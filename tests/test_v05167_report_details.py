"""v0.5.167: enriched HTML session report.

Operator: "also add more details in the report, example NIC type,
BW, driver,... etc. and also improve the visibility of report."

Three asks:

  1. **More detail** — per-endpoint NIC type, driver, link rate,
     MTU, FW, GID. Sourced from the cached /api/rdma/devices
     payload (attached to each run entry as `endpoint_details`).
  2. **Better visibility** — paired-row param table (the old grid
     was interleaving labels and values), zebra-stripe tables,
     left-stripe on the run card, key-result callout.
  3. **Server driver field** — new `driver` field on RdmaDevice
     populated from /sys/class/infiniband/X/device/driver. Flows
     through the devices route via asdict so the GUI gets it for
     free.

Tests cover all three.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_report import build_html_report


SAMPLE_DEVICE = {
    "name": "rocep43s0f0",
    "vendor": "MT_0000000838",
    "fw_version": "28.42.1000",
    "driver": "mlx5_core",
    "net_ifaces": ["ens2f0np0"],
    "ports": [{
        "port": 1,
        "state": "ACTIVE",
        "link_layer": "Ethernet",
        "rate": "200 Gb/sec (4X HDR)",
        "mtu": 4096,
        "gids": ["fe80:0000:0000:0000:9a03:9bff:fe21:c5a1"],
    }],
}


def _build_blast_run_with_details() -> dict:
    return {
        "kind": "blast",
        "started_at": "2026-06-16T14:22:40",
        "test": "ib_send_bw",
        "params": {
            "msg_size": 65536, "qp_count": 1, "duration_s": 30,
            "bidirectional": False, "cpu_util": False,
            "parallel_workers": 1, "iterations": 1,
        },
        "endpoints": {
            "server": "TG 0 rocep43s0f0",
            "client": "TG 0 rocep43s0f1",
        },
        "endpoint_details": [
            {"side": "server", "tg": "TG 0", "hca": "rocep43s0f0",
             "device": SAMPLE_DEVICE},
            {"side": "client", "tg": "TG 0", "hca": "rocep43s0f1",
             "device": {**SAMPLE_DEVICE, "name": "rocep43s0f1",
                        "net_ifaces": ["ens2f0np1"]}},
        ],
        "rows": [
            {"label": "worker 0", "state": "done",
             "bw_gbps": 172.22, "msgrate_mpps": 0.3285},
        ],
        "summary": {
            "samples": 1, "bw_avg_gbps": 172.22,
            "bw_min_gbps": 172.22, "bw_max_gbps": 172.22,
            "msgrate_avg_mpps": 0.3285,
        },
    }


# ───── Endpoint details (the "more detail" ask) ──────────────────────


def test_report_surfaces_endpoint_table_when_details_present():
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    assert "class='endpoints'" in html
    # Column headers — operator scans these first.
    for col in ("HCA", "Link", "Rate", "MTU", "Driver", "FW", "GID"):
        assert col in html
    # Cell values from the sample device.
    assert "rocep43s0f0" in html
    assert "Ethernet" in html
    assert "200 Gb/sec" in html
    assert "4096 B" in html
    assert "ens2f0np0" in html
    assert "mlx5_core" in html
    assert "28.42.1000" in html
    assert "MT_0000000838" in html


def test_report_link_rate_classifier_stripped():
    """The /sys/class/infiniband rate field is verbose ('200 Gb/sec
    (4X HDR)'); the report should drop the trailing class so the
    cell fits in the table column."""
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    # "(4X HDR)" must be stripped — visual clutter in a busy table.
    assert "(4X HDR)" not in html


def test_report_renders_active_state_badge():
    """Operators glance at state — color-coded badge is the
    intended affordance."""
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    assert "badge-state up" in html
    assert "ACTIVE" in html


def test_report_falls_back_when_no_endpoint_details():
    """Back-compat: older run_log entries (pre-v0.5.167) have no
    `endpoint_details` key. The renderer must still produce a
    sensible Endpoints block."""
    legacy = _build_blast_run_with_details()
    legacy.pop("endpoint_details")
    html = build_html_report(
        title="x", runs=[legacy],
        generated_at="2026-06-16T14:23:03",
    )
    # Legacy server/client strings still surface.
    assert "TG 0 rocep43s0f0" in html
    # No endpoint table when details missing.
    assert "class='endpoints'" not in html


# ───── Visibility pass (param table + headline) ──────────────────────


def test_params_render_as_paired_row_table_not_grid():
    """The old <dl class='params'> with a grid auto-fill template
    interleaved labels and values into independent cells. The new
    output uses <table class='params'> with one row per pair."""
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    assert "table class='params'" in html
    # Old grid markup must NOT leak back in.
    assert "dl class='params'" not in html
    # Each pair is its own row; verify by counting <tr> in params.
    params_block = html.split("class='params'", 1)[1].split("</table>", 1)[0]
    assert params_block.count("<tr>") >= 5


def test_params_use_human_labels_and_units():
    """Operator-facing labels, not raw keys. bool → yes/no.
    Duration → with 's' unit. Msg size → with 'B' unit."""
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    assert "Message size" in html
    assert "65536 B" in html
    assert "Duration" in html
    assert "30 s" in html
    assert "Bidirectional" in html
    # bool False → "no" not "False"
    assert ">no<" in html or ">no " in html or "no</td>" in html


def test_report_renders_headline_callout():
    """Per-run key-result callout — operators scan this first."""
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    assert "class='headline'" in html
    # Big number visible.
    assert "172.22" in html
    assert "Gbps" in html
    assert "Mpps" in html


def test_report_run_card_has_left_stripe_styling():
    """Visual divider per run — operators get a clear hierarchy
    in a multi-run report."""
    html = build_html_report(
        title="x", runs=[_build_blast_run_with_details(),
                          _build_blast_run_with_details()],
        generated_at="2026-06-16T14:23:03",
    )
    assert "border-left: 4px solid var(--accent)" in html
    # Both runs render.
    assert "Run #1" in html
    assert "Run #2" in html


def test_html_escape_applied_to_endpoint_device_fields():
    """Even though device payloads come from the server, the
    builder still escapes everything so a hostile payload (or
    accidentally-pasted HTML in a board_id) can't break the
    report layout."""
    run = _build_blast_run_with_details()
    run["endpoint_details"][0]["device"]["vendor"] = "<script>x</script>"
    html = build_html_report(
        title="x", runs=[run],
        generated_at="2026-06-16T14:23:03",
    )
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# ───── Server-side driver field ──────────────────────────────────────


def test_rdma_device_has_driver_field():
    """v0.5.167: RdmaDevice.driver — read from
    /sys/class/infiniband/X/device/driver symlink."""
    from utils.rdma_perf import RdmaDevice
    fields = RdmaDevice.__dataclass_fields__
    assert "driver" in fields


def test_read_driver_name_returns_none_on_missing_symlink(tmp_path):
    """Defensive against containerized /sys with /device stripped.
    Reading a non-existent symlink must NOT raise — it should
    cleanly return None so list_rdma_devices keeps working."""
    from utils import rdma_perf
    # Swap the sysfs root to a dir without a 'device/driver' symlink.
    fake = tmp_path / "ib"
    (fake / "fakehca").mkdir(parents=True)
    orig = rdma_perf._IB_SYSFS_ROOT
    rdma_perf._IB_SYSFS_ROOT = str(fake)
    try:
        assert rdma_perf._read_driver_name("fakehca") is None
    finally:
        rdma_perf._IB_SYSFS_ROOT = orig


def test_read_driver_name_returns_basename(tmp_path):
    """When the symlink IS present, returns the basename of the
    target (e.g. 'mlx5_core' for /sys/bus/pci/drivers/mlx5_core)."""
    import os
    from utils import rdma_perf
    fake = tmp_path / "ib"
    dev_dir = fake / "fakehca" / "device"
    dev_dir.mkdir(parents=True)
    driver_target = tmp_path / "drivers" / "mlx5_core"
    driver_target.mkdir(parents=True)
    os.symlink(str(driver_target), str(dev_dir / "driver"))
    orig = rdma_perf._IB_SYSFS_ROOT
    rdma_perf._IB_SYSFS_ROOT = str(fake)
    try:
        assert rdma_perf._read_driver_name("fakehca") == "mlx5_core"
    finally:
        rdma_perf._IB_SYSFS_ROOT = orig


# ───── Dialog wiring ─────────────────────────────────────────────────


def test_blast_dialog_caches_device_payloads():
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    assert "self._device_payloads" in src
    # Wired into _on_devices_resp so the cache is fresh each probe.
    assert "self._device_payloads[side]" in src
    # And consumed by the run-log builder.
    assert "endpoint_details" in src


def test_topology_dialog_prefetches_endpoint_devices():
    src = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()
    assert "_endpoint_device_cache" in src
    assert "_prefetch_endpoint_devices" in src
    assert "endpoint_details" in src
