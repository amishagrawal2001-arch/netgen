"""v0.5.179: Topology dialog lat reporting parity with Blast.

Operator hit this on srv06 in v0.5.178: every Topology `*_lat`
run came back rc=0 (perftest succeeded) but the exported HTML
report showed "—" for every result cell because the row builder
in `_append_run_log_entry` only carried `bw_gbps` / `msgrate_mpps`
— no `lat_avg_us` etc. The shared `rdma_report` renderer's
`has_lat = any(... "lat_avg_us" ...)` dispatch therefore fell
through to the BW columns for a lat test, which is exactly the
broken render the operator screenshotted.

Same class of bug v0.5.176 fixed for the Blast dialog; the
Topology dialog was never updated.

These tests pin the three sites:
  1. `_snapshot_iteration_results` captures lat fields
  2. `_append_run_log_entry` forwards lat fields into rows
     + builds a lat-shaped summary
  3. `_render_results_card` dispatches to `_render_lat_results_card`
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_report import build_html_report


SRC = (REPO / "widgets"
       / "rdma_topology_dialog.py").read_text()


# ───────── source-grep guards ─────────


def test_snapshot_captures_lat_fields():
    """The snapshot must stash lat_avg/min/max/p99 alongside
    bw/msgrate. Pre-fix only bw/msgrate were captured."""
    body_start = SRC.index("def _snapshot_iteration_results")
    body_end = SRC.index("\n    def ", body_start)
    body = SRC[body_start:body_end]
    for field in ("lat_avg_us", "lat_min_us",
                  "lat_max_us", "lat_p99_us"):
        assert field in body, (
            f"_snapshot_iteration_results dropped {field}")
    assert "final_lat_avg_us" in body  # source-of-truth key


def test_append_run_log_forwards_lat_to_rows():
    """The row builder must include lat_avg_us conditionally so
    the report's has_lat dispatch flips to the lat columns."""
    body_start = SRC.index("def _append_run_log_entry")
    body_end = SRC.index("\n    # ─", body_start)
    body = SRC[body_start:body_end]
    # Conditional add — only when lat_avg_us is present (mirrors
    # Blast's pattern). The literal string is the heart of the
    # fix; if anyone removes it, lat dispatch breaks again.
    assert 'r.get("lat_avg_us") is not None' in body
    assert 'row["lat_avg_us"]' in body


def test_append_run_log_builds_lat_summary_when_lats_present():
    """A `*_lat` run should produce a summary dict with
    lat_avg_us / lat_min_us / lat_max_us, NOT bw_avg_gbps."""
    body_start = SRC.index("def _append_run_log_entry")
    body_end = SRC.index("\n    # ─", body_start)
    body = SRC[body_start:body_end]
    # Lat summary branch precedes the BW summary branch — must
    # be `if lats: ... elif bws: ...`.
    lat_branch = body.index("if lats:")
    bw_branch = body.index("elif bws:")
    assert lat_branch < bw_branch
    # The summary dict for lats carries lat_*_us keys.
    summary_block = body[lat_branch:bw_branch]
    for key in ("lat_avg_us", "lat_min_us", "lat_max_us",
                "samples"):
        assert key in summary_block


def test_render_results_card_dispatches_to_lat_renderer():
    """The card renderer must check for `lat_avg_us` and call
    `_render_lat_results_card`."""
    body_start = SRC.index("def _render_results_card")
    body_end = SRC.index("def _render_lat_results_card", body_start)
    body = SRC[body_start:body_end]
    assert "is_lat_run" in body
    assert "_render_lat_results_card" in body


def test_lat_results_card_renderer_exists():
    assert "def _render_lat_results_card" in SRC


# ───────── end-to-end shape test (no Qt — pure data) ─────────


def test_full_topology_lat_report_renders_lat_columns():
    """Feed a topology-style run dict with lat fields through
    `build_html_report` and confirm the rendered HTML carries
    the lat header columns (Lat avg / Lat p99), NOT BW."""
    rows = [
        {"label": "#0.0", "state": "rc=0",
         "lat_avg_us": 5.54, "lat_min_us": 4.10,
         "lat_max_us": 12.0, "lat_p99_us": 8.1, "iters": 1577611},
    ]
    summary = {
        "samples": 1, "lat_avg_us": 5.54, "lat_min_us": 4.10,
        "lat_max_us": 12.0,
    }
    run = {
        "kind": "topology",
        "test": "send_lat",
        "started_at": "2026-06-17T09:43:39",
        "params": {"shape": "single", "iterations": 1,
                   "duration_s": 30, "msg_size": 65536},
        "endpoints": {"pairs": [
            {"idx": 0, "server": "srv06.mlx5_0",
             "client": "srv06.mlx5_3"}]},
        "rows": rows,
        "summary": summary,
    }
    html = build_html_report(
        title="Topology lat parity",
        runs=[run],
        generated_at="2026-06-17T09:45:00",
    )
    # The lat columns must appear (proves has_lat dispatch fired).
    assert ">Lat avg (µs)<" in html
    assert ">Lat p99 (µs)<" in html
    # The bw columns must NOT appear.
    assert ">BW avg (Gbps)<" not in html
    assert ">MsgRate (Mpps)<" not in html
    # The 5.54 µs value made it into the rendered table.
    assert ">5.54<" in html


def test_topology_bw_report_unchanged():
    """A `*_bw` topology run must still render BW columns —
    the lat fix must not regress the BW path."""
    rows = [
        {"label": "#0.0", "state": "rc=0",
         "bw_gbps": 171.25, "msgrate_mpps": 0.3266},
    ]
    summary = {
        "samples": 1, "bw_avg_gbps": 171.25,
        "bw_min_gbps": 171.25, "bw_max_gbps": 171.25,
        "msgrate_avg_mpps": 0.3266,
    }
    run = {
        "kind": "topology",
        "test": "send_bw",
        "started_at": "2026-06-17T09:43:00",
        "params": {"shape": "single"},
        "endpoints": {"pairs": [
            {"idx": 0, "server": "srv06.mlx5_0",
             "client": "srv06.mlx5_3"}]},
        "rows": rows,
        "summary": summary,
    }
    html = build_html_report(
        title="Topology BW parity",
        runs=[run],
        generated_at="2026-06-17T09:45:00",
    )
    assert ">BW avg (Gbps)<" in html
    assert ">MsgRate (Mpps)<" in html
    assert ">Lat avg (µs)<" not in html
    assert ">171.25<" in html
