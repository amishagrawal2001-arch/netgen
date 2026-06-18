"""v0.5.181 gap-recheck batch: B-1/B-2/B-3 + P-1 + G-1/G-4.

After the v0.5.181 release-candidate audit caught:

  B-1  Sweep checkbox unticked when Run-all advanced from a
       *_bw test to a *_lat test — the auto-untick fired on the
       intermediate BW combo change, losing the operator's intent.
  B-2  Stop during the 1.5 s inter-test pause didn't cancel the
       QTimer.singleShot — the next test fired anyway.
  B-3  Blast `iters_sum` always summed to zero because no row in
       Blast's _append_run_log_entry carried `iters`. Report's
       Σ Iters column always showed `—`.
  P-1  Topology Max-BW button printed the worker math but NOT the
       picked cpus + numa pin (sibling-parity gap with Blast).
  G-1  Run-all progress text was "5 more in queue" — opaque vs
       "Test 2/6 done".
  G-4  Topology summary had no `pairs_attempted` analog to Blast's
       `workers_attempted` — "N of M pairs reported" couldn't
       render for multi-pair runs with partial data.

All six fix in v0.5.181-rc batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


BLAST_SRC = (REPO / "widgets"
             / "rdma_blast_flow_dialog.py").read_text()
TOPO_SRC = (REPO / "widgets"
            / "rdma_topology_dialog.py").read_text()
REPORT_SRC = (REPO / "utils" / "rdma_report.py").read_text()


# ───────────────────── B-1: sweep preservation ─────────────────────


def test_blast_sweep_visibility_skips_untick_during_run_all():
    body_start = BLAST_SRC.index("def _refresh_sweep_visibility")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # The B-1 guard: don't auto-untick when the queue is running.
    assert "run_all_active" in body
    assert "not self._run_all_check.isEnabled()" in body


def test_topology_sweep_visibility_skips_untick_during_run_all():
    body_start = TOPO_SRC.index("def _refresh_sweep_visibility")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "run_all_active" in body
    assert "not self._run_all_check.isEnabled()" in body


def test_blast_sweep_preserved_under_run_all_smoke():
    """Functional smoke: simulate Run-all pumping the combo
    from a *_lat (sweep checked) → *_bw → *_lat. Sweep must
    still be checked at the end."""
    from PyQt5.QtWidgets import QApplication
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    dlg = RdmaBlastFlowDialog(server_tg_url='http://x:5050')
    # Start on send_lat, tick sweep.
    for i in range(dlg._test_combo.count()):
        if dlg._test_combo.itemData(i) == "send_lat":
            dlg._test_combo.setCurrentIndex(i)
            break
    dlg._sweep_sizes_check.setChecked(True)
    assert dlg._sweep_sizes_check.isChecked()
    # Simulate Run-all queue active.
    dlg._run_all_check.setChecked(True)
    dlg._maybe_populate_run_all_queue()
    # Combo pumps through write_bw at some point. Trigger it.
    for i in range(dlg._test_combo.count()):
        if dlg._test_combo.itemData(i) == "write_bw":
            dlg._test_combo.blockSignals(True)
            dlg._test_combo.setCurrentIndex(i)
            dlg._test_combo.blockSignals(False)
            dlg._refresh_sweep_visibility()
            break
    # Sweep should STILL be checked (Run-all is active).
    assert dlg._sweep_sizes_check.isChecked(), \
        "B-1 regression: sweep was unticked during Run-all"


# ───────────────────── B-2: Stop cancels QTimer ─────────────────────


def test_blast_next_in_queue_tracks_pending_timer():
    body_start = BLAST_SRC.index(
        "def _start_next_test_in_queue")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # Tracked QTimer instead of fire-and-forget singleShot.
    assert "_run_all_pending_timer" in body
    assert "setSingleShot(True)" in body
    assert "start(1500)" in body


def test_blast_stop_cancels_pending_timer():
    body_start = BLAST_SRC.index("def _on_stop_clicked")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_run_all_pending_timer" in body
    assert "pending.stop()" in body


def test_topology_next_in_queue_tracks_pending_timer():
    body_start = TOPO_SRC.index(
        "def _start_next_test_in_queue")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "_run_all_pending_timer" in body
    assert "setSingleShot(True)" in body
    assert "start(1500)" in body


def test_topology_stop_cancels_pending_timer():
    body_start = TOPO_SRC.index("def _on_stop_clicked")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "_run_all_pending_timer" in body
    assert "pending.stop()" in body


# ───────────────────── B-3: Blast iters threading ─────────────────────


def test_blast_client_resp_captures_iters():
    body_start = BLAST_SRC.index("def _on_job_resp")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "self._client_iters = job.get" in body


def test_blast_extra_resp_captures_iters():
    body_start = BLAST_SRC.index("def _on_extra_job_resp")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert 'w.setdefault(f"{side}_iters"' in body


def test_blast_iter_results_carries_iters():
    body_start = BLAST_SRC.index("def _on_both_finished")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert '"iters": getattr(self, "_client_iters"' in body


def test_blast_row_builders_forward_iters():
    body_start = BLAST_SRC.index("def _append_run_log_entry")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # All three row sources (iter / extras / w0).
    assert 'row["iters"] = r.get("iters")' in body
    assert 'row["iters"] = w.get("client_iters")' in body
    assert 'w0_row["iters"] = w0_iters' in body


def test_blast_proceed_resets_client_iters():
    # The fresh-start reset block, alongside _client_bw = None.
    assert "self._client_iters = None" in BLAST_SRC


# ───────────────────── P-1: Topology Max BW visibility ─────────────────────


def test_topology_max_bw_logs_picked_cpus_at_click_time():
    body_start = TOPO_SRC.index("def _on_max_bw_clicked")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    # P-1: same surface area as Blast — cpus + numa pin in the
    # click-time status message.
    assert "_fmt_cpu_list" in body
    assert "numa=" in body


# ───────────────────── G-1: Run-all progress text ─────────────────────


def test_blast_run_all_progress_uses_n_of_m():
    # v0.5.182 NB-6: the Run-all advance logic was extracted from
    # _on_both_finished into _finalize_run, which now hosts the
    # progress-text + queue-advance code. Look there instead.
    body_start = BLAST_SRC.index("def _finalize_run")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_run_all_total" in body
    assert "Test {current}/{total} done" in body


def test_blast_populate_queue_records_total():
    body_start = BLAST_SRC.index(
        "def _maybe_populate_run_all_queue")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "self._run_all_total = len(all_tids)" in body


def test_topology_run_all_progress_uses_n_of_m():
    # Topology's progress lives in _on_job_resp's _all_pairs_done
    # arm. Source-grep on the unique substring.
    assert "Test {current}/{total} done" in TOPO_SRC
    assert ("self._run_all_total = len(self._all_perftest_tests)"
            in TOPO_SRC)


# ───────────────────── G-4: pairs_attempted parity ─────────────────────


def test_topology_summary_carries_pairs_attempted():
    body_start = TOPO_SRC.index("def _append_run_log_entry")
    body_end = TOPO_SRC.index("\n    # ─", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "pairs_attempted" in body
    assert 'summary["pairs_attempted"] = pairs_attempted' in body


def test_report_headline_uses_pairs_attempted_for_sum_pairs():
    # Dispatch in _render_headline picks pairs_attempted when
    # aggregation_mode == sum_pairs.
    body_start = REPORT_SRC.index(
        "v0.5.181 G-4: pairs_attempted is the sibling")
    body_end = body_start + 500
    body = REPORT_SRC[body_start:body_end]
    assert 'summary.get("pairs_attempted")' in body


def test_report_bw_summary_uses_pairs_attempted_for_sum_pairs():
    # _render_bw_summary's Samples cell selects unit by mode.
    body_start = REPORT_SRC.index(
        "v0.5.181 G-4: sum_pairs mode (Topology)")
    body_end = body_start + 500
    body = REPORT_SRC[body_start:body_end]
    assert 's.get("pairs_attempted")' in body
    assert 'unit = "pairs"' in body


def test_full_report_topology_sum_pairs_renders_n_of_m_pairs():
    """End-to-end: a 4-pair Topology run with 3 reporting must
    render `total X · 3 of 4 pairs`, mirroring Blast's worker
    behaviour."""
    from utils.rdma_report import build_html_report
    rows = []
    for p in range(3):
        rows.append({
            "label": f"#0.{p}",
            "state": "rc=0",
            "bw_gbps": 50.0,
            "msgrate_mpps": 0.10,
            "iters": 1000000,
        })
    rows.append({
        "label": "#0.3",
        "state": "FAILED",
        "bw_gbps": None,
        "msgrate_mpps": None,
    })
    summary = {
        "samples": 3, "bw_avg_gbps": 150.0,
        "bw_min_gbps": 50.0, "bw_max_gbps": 50.0,
        "msgrate_avg_mpps": 0.30,
        "msgrate_min_mpps": 0.10,
        "msgrate_max_mpps": 0.10,
        "iters_sum": 3000000,
        "aggregation_mode": "sum_pairs",
        "pairs_attempted": 4,
    }
    run = {
        "kind": "topology", "test": "send_bw",
        "started_at": "2026-06-17T15:00:00",
        "params": {"shape": "mesh"},
        "endpoints": {},
        "rows": rows, "summary": summary,
    }
    html = build_html_report(
        title="pairs_attempted parity",
        runs=[run],
        generated_at="2026-06-17T15:05:00",
    )
    # Headline shows "total across 3 of 4 pairs"
    assert "total across 3 of 4 pairs" in html
    # Σ row Samples cell shows "3 of 4 pairs"
    assert "3 of 4 pairs" in html
    # BW total
    assert ">150.00<" in html or "total 150.00" in html
