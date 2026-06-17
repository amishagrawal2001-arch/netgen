"""v0.5.181 followup: SUM-vs-AVG aggregation + Max-BW visibility.

Operator's srv06 report headlined `avg 10.28 Gbps` for a
16-parallel-worker send_bw run — wildly misleading. Real wire
throughput was ~113 Gbps (11 workers × 10.28). Two related
gaps:

  1. Σ summary aggregated workers with AVG (correct for
     iterations, wrong for workers). Now SUM-aware via
     `aggregation_mode` in the summary dict.
  2. Max-BW button picked the same CPUs as the linear fallback
     on srv06 (single-socket, NUMA 0 cores = 0..15), so operator
     reported "no difference." Now the picked cpus + numa pin
     are LOGGED to the status panel for every Start so the
     equivalence is visible, not invisible.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_report import (
    _fmt_summary_cell,
    _render_bw_summary,
    _render_headline,
    build_html_report,
)


BLAST_SRC = (REPO / "widgets"
             / "rdma_blast_flow_dialog.py").read_text()
TOPO_SRC = (REPO / "widgets"
            / "rdma_topology_dialog.py").read_text()


# ───────── #1: aggregation mode → SUM vs AVG ─────────


def test_summary_cell_prefix_total_for_sum_modes():
    cell = _fmt_summary_cell(
        "164.45", "10.26", "10.29",
        unit_after_max="", prefix="total")
    assert cell.startswith("total 164.45")
    assert "(min 10.26, max 10.29)" in cell


def test_summary_cell_prefix_avg_default():
    cell = _fmt_summary_cell(
        "10.28", "10.26", "10.29", unit_after_max="")
    assert cell.startswith("avg 10.28")


def test_bw_summary_renders_total_when_sum_workers():
    s = {
        "samples": 11, "bw_avg_gbps": 113.08,
        "bw_min_gbps": 10.26, "bw_max_gbps": 10.29,
        "msgrate_avg_mpps": 0.2156,
        "msgrate_min_mpps": 0.0196,
        "msgrate_max_mpps": 0.0196,
        "iters_sum": 2156432,
        "aggregation_mode": "sum_workers",
        "workers_attempted": 16,
    }
    html = _render_bw_summary(s)
    # Total, not avg.
    assert "total 113.08" in html
    # Workers attempted vs reported.
    assert "11 of 16 workers" in html
    # Iters sum carried through.
    assert ">2156432<" in html


def test_bw_summary_renders_avg_default():
    s = {
        "samples": 10, "bw_avg_gbps": 165.0,
        "bw_min_gbps": 164.0, "bw_max_gbps": 166.0,
        "msgrate_avg_mpps": 0.3,
        "iters_sum": 30000000,
    }
    html = _render_bw_summary(s)
    assert "avg 165.00" in html
    assert "total" not in html


def test_headline_says_total_for_sum_workers():
    rows = [
        {"label": "worker 0", "state": "done",
         "bw_gbps": 10.28, "msgrate_mpps": 0.0196},
    ] * 11
    summary = {
        "samples": 11, "bw_avg_gbps": 113.08,
        "bw_min_gbps": 10.26, "bw_max_gbps": 10.29,
        "msgrate_avg_mpps": 0.2156,
        "msgrate_min_mpps": 0.0196,
        "msgrate_max_mpps": 0.0196,
        "aggregation_mode": "sum_workers",
        "workers_attempted": 16,
    }
    run = {
        "kind": "blast", "test": "send_bw",
        "params": {}, "rows": rows, "summary": summary,
        "endpoints": {},
    }
    html = _render_headline(run)
    # 113.08 not 10.28
    assert "113.08" in html
    # "total across 11 of 16 workers"
    assert "total across 11 of 16 workers" in html


def test_headline_says_average_for_avg_iterations_mode():
    rows = [
        {"label": "iter #0", "state": "done",
         "bw_gbps": 170.0, "msgrate_mpps": 0.3},
        {"label": "iter #1", "state": "done",
         "bw_gbps": 168.0, "msgrate_mpps": 0.3},
    ]
    summary = {
        "samples": 2, "bw_avg_gbps": 169.0,
        "bw_min_gbps": 168.0, "bw_max_gbps": 170.0,
        "msgrate_avg_mpps": 0.3,
        "aggregation_mode": "avg_iterations",
    }
    run = {
        "kind": "blast", "test": "send_bw",
        "params": {}, "rows": rows, "summary": summary,
        "endpoints": {},
    }
    html = _render_headline(run)
    assert "average across 2 samples" in html
    assert "total across" not in html


# ───────── #2: producer detects worker mode ─────────


def test_blast_summary_producer_marks_worker_mode():
    """Blast's _append_run_log_entry must label the summary's
    aggregation_mode based on whether rows are worker- or
    iter-labeled."""
    body_start = BLAST_SRC.index("def _append_run_log_entry")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "worker_rows" in body
    assert "iter_rows" in body
    assert '"aggregation_mode": "sum_workers"' in body
    assert '"aggregation_mode": "avg_iterations"' in body
    assert '"workers_attempted"' in body


def test_topology_summary_producer_marks_pair_mode():
    body_start = TOPO_SRC.index("def _append_run_log_entry")
    # next def at same indent
    body_end = TOPO_SRC.index("\n    # ─", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "is_parallel_pairs" in body
    assert '"aggregation_mode": "sum_pairs"' in body
    assert '"aggregation_mode": "avg_iterations"' in body


# ───────── #3: Max-BW visibility ─────────


def test_blast_max_bw_handler_logs_picked_cpus():
    body_start = BLAST_SRC.index("def _on_max_bw_clicked")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # The status update mentions picked cpus + numa.
    assert "max-bw" in body or "picked workers" in body
    assert "_fmt_cpu_list" in body


def test_blast_extras_path_always_logs_cpus():
    """Even when Max BW was NOT clicked, the workers' actual
    cpu pins + numa are logged when extras spawn — that's how
    operator sees `linear cpus=[0-15]` equivalence to Max BW."""
    body_start = BLAST_SRC.index("def _start_extra_workers")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # The unconditional log (not gated on fallback_reason).
    assert "spawning" in body and "workers" in body
    assert "_fmt_cpu_list(cpus)" in body
    assert "numa_pin" in body


def test_topology_extras_path_logs_pin():
    body_start = TOPO_SRC.index("def _start_pair_extra_workers")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "_fmt_cpu_list(cpus)" in body


def test_cpu_list_helper_collapses_ranges():
    """Sanity check the range-collapse formatter both dialogs
    use to render `cpus=[0-15]` instead of `cpus=[0,1,2,...,15]`."""
    from PyQt5.QtWidgets import QApplication
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    dlg = RdmaBlastFlowDialog(server_tg_url='http://x:5050')
    assert dlg._fmt_cpu_list([0, 1, 2, 3]) == "[0-3]"
    assert dlg._fmt_cpu_list([0, 1, 2, 5, 6, 7]) == "[0-2,5-7]"
    assert dlg._fmt_cpu_list([0]) == "[0]"
    assert dlg._fmt_cpu_list([]) == "[]"
    assert dlg._fmt_cpu_list([15, 1, 0, 2]) == "[0-2,15]"


# ───────── end-to-end ─────────


def test_full_report_with_worker_mode_renders_total():
    """End-to-end: a 16-worker run with 11 reporting renders
    `total 113 Gbps · 11 of 16 workers` not `avg 10`."""
    rows = []
    for w in range(11):
        rows.append({
            "label": f"worker {w}", "state": "done",
            "bw_gbps": 10.28, "msgrate_mpps": 0.0196,
        })
    for w in range(11, 16):
        rows.append({
            "label": f"worker {w}", "state": "?",
            "bw_gbps": None, "msgrate_mpps": None,
        })
    summary = {
        "samples": 11, "bw_avg_gbps": 113.08,
        "bw_min_gbps": 10.28, "bw_max_gbps": 10.28,
        "msgrate_avg_mpps": 0.2156,
        "msgrate_min_mpps": 0.0196,
        "msgrate_max_mpps": 0.0196,
        "aggregation_mode": "sum_workers",
        "workers_attempted": 16,
    }
    run = {
        "kind": "blast", "test": "send_bw",
        "started_at": "2026-06-17T14:05:00",
        "params": {"parallel_workers": 16, "msg_size": 65536},
        "endpoints": {"server": "x", "client": "y"},
        "rows": rows, "summary": summary,
    }
    html = build_html_report(
        title="Aggregation parity",
        runs=[run],
        generated_at="2026-06-17T14:10:00",
    )
    # The misleading 10.28 must NOT be the headline number.
    assert "<span class='num'>113.08</span>" in html
    # And the tail explains "total across 11 of 16 workers".
    assert "total across 11 of 16 workers" in html
    # Σ row shows total + "11 of 16 workers".
    assert "total 113.08" in html
    assert "11 of 16 workers" in html
