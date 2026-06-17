"""v0.5.180 re-audit lat-pipeline parity tests.

The v0.5.178 audit caught state / probe / CIDR / validation bugs
but missed every lat-pipeline gap because it didn't trace data
flow from `PerftestJob.final_*` → poll → render. Operator's
follow-up audit request caught:

  H-RE-1 _update_pair_row only wrote BW + MsgRate → live grid
         shows `—` during entire lat run
  H-RE-2 _append_summary_row early-returned when bws + mrs empty
         → no Σ row for multi-iter lat runs
  H-RE-3 utils/rdma_report._render_headline always rendered BW
         headline → green callout says `— Gbps | — Mpps` for lat
  H-RE-4 stats table column headers static "BW Gbps / MsgRate
         Mpps" → misleading even after cell values fixed
  M-RE-2 line-rate efficiency calc not gated on _bw — safe
         today (bw is None on lat) but a stale value would
         render % nonsense
  L-RE-1 probe error text never surfaced to operator

These tests pin each fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_report import (
    build_html_report,
    _render_headline,
    _render_lat_headline,
)


SRC = (REPO / "widgets"
       / "rdma_topology_dialog.py").read_text()


# ───────── H-RE-1: live grid lat dispatch ─────────


def test_update_pair_row_sniffs_for_lat_data():
    body_start = SRC.index("def _update_pair_row")
    body_end = SRC.index("\n    def ", body_start)
    body = SRC[body_start:body_end]
    # The data-sniff guard distinguishes lat from BW.
    assert 'primary.get("final_lat_avg_us")' in body
    assert 'primary.get("final_lat_p99_us")' in body
    # Both branches present — lat writes µs, BW writes Gbps.
    assert "if is_lat:" in body
    # Calls the header refresh helper.
    assert "_refresh_stats_column_headers" in body


def test_update_pair_row_header_helper_relabels_to_lat():
    body_start = SRC.index("def _refresh_stats_column_headers")
    body_end = SRC.index("\n    def ", body_start)
    body = SRC[body_start:body_end]
    assert '"Lat avg (µs)"' in body
    assert '"Lat p99 (µs)"' in body
    assert '"BW Gbps"' in body
    assert '"MsgRate Mpps"' in body
    # No-op when unchanged (cheap to call every poll).
    assert "if current is not None and current.text() == labels[4]:" in body


# ───────── H-RE-2: Σ summary row aggregates lat ─────────


def test_append_summary_row_aggregates_lat_when_present():
    body_start = SRC.index("def _append_summary_row")
    body_end = SRC.index("\n    def ", body_start)
    body = SRC[body_start:body_end]
    # Pre-fix returned early when bws + mrs both empty.
    # Post-fix the early return also checks lat_avgs.
    assert "lat_avgs" in body
    assert "lat_p99s" in body
    assert "if not bws and not mrs and not lat_avgs:" in body
    # The lat branch writes "avg=X.XX min=Y.YY max=Z.ZZ" to col 4.
    assert "if lat_avgs:" in body


# ───────── H-RE-3: report headline lat dispatch ─────────


def test_render_headline_dispatches_to_lat_for_lat_runs():
    """Headline for a lat run carries µs units, not Gbps."""
    rows = [
        {"label": "#0.0", "state": "rc=0",
         "lat_avg_us": 5.54, "lat_p99_us": 8.1, "iters": 1577611},
    ]
    summary = {"samples": 1, "lat_avg_us": 5.54}
    run = {
        "kind": "topology", "test": "send_lat",
        "params": {}, "rows": rows, "summary": summary,
        "endpoints": {"pairs": []},
    }
    html = _render_headline(run)
    assert "µs avg" in html
    assert "µs p99" in html
    assert "Gbps" not in html
    assert "Mpps" not in html
    assert "5.54" in html
    assert "8.10" in html


def test_render_headline_still_renders_bw_for_bw_runs():
    """Non-regression: BW runs still get the BW headline."""
    rows = [
        {"label": "#0.0", "state": "rc=0",
         "bw_gbps": 171.25, "msgrate_mpps": 0.3266},
    ]
    summary = {"samples": 1, "bw_avg_gbps": 171.25,
               "msgrate_avg_mpps": 0.3266}
    run = {
        "kind": "topology", "test": "send_bw",
        "params": {}, "rows": rows, "summary": summary,
        "endpoints": {"pairs": []},
    }
    html = _render_headline(run)
    assert "Gbps" in html
    assert "Mpps" in html
    assert "µs" not in html
    assert "171.25" in html


def test_render_lat_headline_skips_line_rate_calc():
    """M-RE-2: line-rate efficiency is meaningless for lat. The
    `% of N G line rate` extras must NOT appear in lat
    headlines."""
    rows = [{"lat_avg_us": 5.54, "lat_p99_us": 8.1}]
    summary = {"samples": 5, "lat_avg_us": 5.54,
               "lat_min_us": 4.10, "lat_max_us": 12.0}
    # Endpoint with line_rate to make sure the calc would have
    # fired in the BW branch.
    run = {
        "test": "send_lat", "params": {}, "rows": rows,
        "summary": summary,
        "endpoints": {"pairs": [{"idx": 0,
                                 "server": "x", "client": "y"}]},
        "endpoint_details": [{"side": "server",
                              "device": {"ports": [{"rate": "200 Gb/sec"}]}}],
    }
    html = _render_lat_headline(run, summary, rows)
    assert "line rate" not in html
    assert "200 G" not in html
    # But min/max spread DOES appear (samples > 1).
    assert "min 4.10" in html
    assert "max 12.00" in html


# ───────── end-to-end ─────────


def test_full_topology_lat_report_carries_lat_headline_and_columns():
    rows = [
        {"label": "#0.0", "state": "rc=0",
         "lat_avg_us": 5.54, "lat_min_us": 4.10,
         "lat_max_us": 12.0, "lat_p99_us": 8.1, "iters": 1577611},
    ]
    summary = {"samples": 1, "lat_avg_us": 5.54,
               "lat_min_us": 4.10, "lat_max_us": 12.0}
    run = {
        "kind": "topology", "test": "send_lat",
        "started_at": "2026-06-17T09:43:39",
        "params": {"shape": "single"},
        "endpoints": {"pairs": [{"idx": 0,
                                 "server": "srv06.mlx5_0",
                                 "client": "srv06.mlx5_3"}]},
        "rows": rows, "summary": summary,
    }
    html = build_html_report(
        title="Topology lat re-audit",
        runs=[run],
        generated_at="2026-06-17T09:45:00",
    )
    # Headline: µs not Gbps
    assert "µs avg" in html
    assert "µs p99" in html
    # Per-row table: Lat avg / Lat p99 columns (not BW)
    assert ">Lat avg (µs)<" in html
    assert ">Lat p99 (µs)<" in html
    assert ">BW avg (Gbps)<" not in html
    # The values landed
    assert ">5.54<" in html
    # No "X% of N G line rate" pollution
    assert "line rate" not in html


# ───────── L-RE-1: probe error surfacing ─────────


def test_topology_probe_complete_surfaces_all_errored():
    body_start = SRC.index("def _topology_on_probe_complete")
    body_end = SRC.index("\n    def ", body_start)
    body = SRC[body_start:body_end]
    # All probes errored → status message mentions it.
    assert "probe(s) errored" in body
    # Code still proceeds (doesn't disable Start). The phrase
    # "Proceeding to start" wraps in the f-string so check the
    # anchor word that's on a single source line.
    assert "Proceeding to start" in body
    assert "perftest will give the definitive" in body
