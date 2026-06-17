"""v0.5.172: three operator-reported fixes.

  1. **Clear Stats doesn't reset Packets Lost / Loss %.** The
     button taring was missing phy_tx / phy_rx, so the loss
     formula (lost / pair_tx × 100) used cumulative-since-boot
     PHY counters. Lost showed millions while Loss % rounded to
     0.00% against the trillions-since-boot denominator.
  2. **Blast report shows two runs when only one was run.**
     `_on_both_finished` lacked an idempotency guard; once the
     poll timer was stopped, Qt-queued poll callbacks kept
     entering the function and re-appending to `_run_log`.
  3. **Endpoint table overflows the run-card.** 16 columns
     exceeded the 1180px max-width on most viewports — needs
     horizontal scroll.

Plus a small audit-driven addition: MT_0000000225 → ConnectX-6
mapping (operator's lab box rendered the model as `—`).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ───── Fix 1: Clear Stats baselines PHY counters ────────────────


def test_clear_cached_statistics_snapshots_phy_counters():
    """The baseline dict must include phy_tx / phy_rx so the loss
    renderer can subtract them on the next poll."""
    src = (REPO / "traffic_client" / "statistics_section.py").read_text()
    clear_idx = src.find("def clear_cached_statistics")
    assert clear_idx > 0
    # Look at the snapshot block — should include phy_tx + phy_rx.
    block = src[clear_idx:clear_idx + 3000]
    assert '"phy_tx"' in block, "phy_tx not snapshotted in iface_baselines"
    assert '"phy_rx"' in block, "phy_rx not snapshotted in iface_baselines"


def test_loss_renderer_subtracts_phy_baselines():
    """The Lost / Loss% column populator must subtract the
    baseline before passing values to compute_iface_pair_loss.
    Without this, Clear Stats has no effect on those columns."""
    src = (REPO / "traffic_client" / "statistics_section.py").read_text()
    # The renderer block lives around the "Packets Lost — v0.5.144"
    # comment.
    idx = src.find("Packets Lost — v0.5.144")
    assert idx > 0
    body = src[idx:idx + 4000]
    # The new code references _iface_baselines for both own
    # and peer phy subtraction.
    assert "_iface_baselines" in body
    # And uses max(0, raw - baseline) to clamp negative diffs
    # (when the operator restarts the server, raw counter
    # resets but our baseline persists — must not show a huge
    # negative-then-wrap number).
    assert "max(0," in body


def test_clear_stats_loss_compute_after_tare_returns_zero():
    """Functional test of the math: when raw == baseline (i.e.,
    Clear was just pressed), the subtracted phy_tx/rx is zero
    → has_traffic is False → Lost should be 0 and Loss should
    render as em-dash."""
    from traffic_client.statistics_section import compute_iface_pair_loss
    # Pure-function check: zero-zero pair → (0, 0.0).
    lost, pct = compute_iface_pair_loss(0, 0, 0, 0)
    assert lost == 0
    assert pct == 0.0
    # Realistic post-tare: 1000 sent, 950 received → 50 lost, 5%.
    lost, pct = compute_iface_pair_loss(1000, 950, 0, 0)
    assert lost == 50
    assert abs(pct - 5.0) < 1e-9


# ───── Fix 2: idempotent _on_both_finished ──────────────────────


def test_on_both_finished_has_idempotency_guard():
    """Second + subsequent entries into _on_both_finished must
    short-circuit so the run-log entry is only appended once."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    idx = src.find("def _on_both_finished")
    assert idx > 0
    body = src[idx:idx + 1500]
    # The guard reads + sets a `_finalised` flag at the very top.
    assert "_finalised" in body
    assert 'getattr(self, "_finalised", False)' in body
    assert "return" in body
    assert "self._finalised = True" in body


def test_proceed_with_start_resets_finalised_flag():
    """Each new Start must clear the guard so the next
    _on_both_finished can fire exactly once."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    idx = src.find("def _proceed_with_start")
    assert idx > 0
    body = src[idx:idx + 6000]
    assert "self._finalised = False" in body


# ───── Fix 3: endpoint table horizontal scroll ─────────────────


def test_endpoint_table_wrapped_in_scroll_container():
    """16-column endpoint table needs overflow-x:auto so it pans
    inside the run-card instead of spilling past the right
    border."""
    src = (REPO / "utils" / "rdma_report.py").read_text()
    assert "endpoint-scroll" in src
    # CSS rule must set overflow-x: auto.
    css_idx = src.find(".endpoint-scroll")
    assert css_idx > 0
    css_body = src[css_idx:css_idx + 400]
    assert "overflow-x: auto" in css_body
    assert "min-width: max-content" in css_body or \
        "min-width:max-content" in css_body


def test_endpoint_table_html_wrapped_at_render_time():
    """Verify the HTML output contains the wrapper div around
    the endpoints table."""
    from utils.rdma_report import build_html_report
    SAMPLE_DEVICE = {
        "name": "rocep43s0f0", "vendor": "MT_0000000838",
        "fw_version": "28.42.1000", "driver": "mlx5_core",
        "net_ifaces": ["ens2f0np0"],
        "ports": [{
            "port": 1, "state": "ACTIVE",
            "link_layer": "Ethernet",
            "rate": "200 Gb/sec (4X HDR)", "mtu": 4096,
            "gids": ["fe80:0000:0000:0000:9a03:9bff:fe21:c5a1"],
        }],
    }
    run = {
        "kind": "blast", "started_at": "x", "test": "ib_send_bw",
        "params": {"msg_size": 65536},
        "endpoints": {"server": "TG 0 a", "client": "TG 0 b"},
        "endpoint_details": [
            {"side": "server", "tg": "TG 0",
             "hca": "rocep43s0f0", "device": SAMPLE_DEVICE},
            {"side": "client", "tg": "TG 0",
             "hca": "rocep43s0f1", "device": SAMPLE_DEVICE},
        ],
        "rows": [{"label": "worker 0", "state": "done",
                  "bw_gbps": 172.22, "msgrate_mpps": 0.3285}],
        "summary": {"samples": 1, "bw_avg_gbps": 172.22},
    }
    html = build_html_report(
        title="x", runs=[run], generated_at="x")
    # Find the endpoint table and check the wrapper precedes it.
    table_idx = html.find("table class='endpoints'")
    assert table_idx > 0
    above = html[max(0, table_idx - 200):table_idx]
    assert "endpoint-scroll" in above


# ───── Audit add: MT_0000000225 board_id ───────────────────────


def test_mt_0000000225_maps_to_connectx_6():
    """Operator's lab HCA had board_id MT_0000000225 (FW 20.40.x)
    and the Model column rendered as `—`. Add it to the map so
    the report shows the friendly name."""
    from utils.rdma_report import _resolve_nic_model
    assert _resolve_nic_model("MT_0000000225") == "ConnectX-6"


# ───── Regression: existing v0.5.170 mappings still work ───────


def test_existing_board_id_mappings_unchanged():
    from utils.rdma_report import _resolve_nic_model
    assert _resolve_nic_model("MT_0000000838") == "ConnectX-7"
    assert _resolve_nic_model("MT_0000001019") == "ConnectX-8"
    assert _resolve_nic_model("MT_xxxxxxxxxx") == "—"
