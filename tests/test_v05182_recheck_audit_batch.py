"""v0.5.182: fix every gap caught in the recheck audit of v0.5.181.

Operator ran v0.5.181 on srv06 with Run-all + 16 parallel workers,
exported the report, and surfaced 12 distinct issues:

  NB-1   Run #3 (read_bw, Parallel=16) reported only worker 0.
         Root cause: NB-12 — params snapshot read spinner AFTER
         the spawn, so the spawn count and reported count differed.
  NB-2   Lat extras never captured lat_avg/min/max/p99. Only
         worker 0 had lat values → Σ averaged 1 sample even when
         15 extras reported.
  NB-3   Lat Σ row said "1 samples" when 15 of 16 workers reported.
         No workers_attempted dispatch.
  NB-4   Run-all carried BW-test settings (msg_size=65536, parallel
         =16, tx_depth=128) into lat tests. Operator's headline
         "41 µs send_lat" was loaded latency, not idle.
  NB-5   No warning at lat-test start when parallel>1.
  NB-6   Queue advance fired on worker-0-done. In-flight extras
         got killed when the next test's Start spawned. Staircase
         in operator's report: send_lat 1/16 done, write_lat 15/16,
         read_lat 16/16.
  NB-7   Lat Σ row showed only avg — no min/max.
  NB-8   Lat p99 column always `—`. Root cause: perftest's `-D`
         (duration) mode emits 4-column output WITHOUT p99. Switch
         to `-n` (iter count) for *_lat tests to get 9-column
         output.
  NB-9   MTU `—` in endpoint table despite ibv_devinfo capturing
         it. Root: device payload cached at dialog-open, never
         refreshed.
  NB-10  IPv4 `—` (preflight IPs not surfaced). Same root as NB-9.
  NB-11  Single-worker BW headline tail said "final" not
         "total across 1 worker" — inconsistent with the new
         total-across-N phrasing.
  NB-12  `params` dict in run-log entry read spinner at REPORT
         time, not Start. Mid-run spinner changes silently
         misrepresented what was actually used.
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
PERF_SRC = (REPO / "utils" / "rdma_perf.py").read_text()


# ───────────────────── NB-8: perftest -n for lat ─────────────────────


def test_lat_tests_prefer_iter_mode_over_duration():
    """NB-8: `_build_perftest_cmd` must prefer `-n` over `-D` for
    *_lat tests, because duration mode silently strips the
    9-column output that carries p99."""
    cmd_block_start = PERF_SRC.index("def _build_perftest_cmd")
    cmd_block_end = PERF_SRC.index("\ndef ", cmd_block_start + 10)
    body = PERF_SRC[cmd_block_start:cmd_block_end]
    assert "NB-8" in body
    assert "is_lat = test.endswith" in body
    assert 'cmd += ["-n", str(int(iterations) if iterations else' in body


# ───────────────────── NB-2: Blast extras lat ─────────────────────


def test_blast_extras_capture_lat_fields():
    """NB-2: `_on_extra_job_resp` must stash final_lat_avg_us,
    final_lat_min_us, final_lat_max_us, final_lat_p99_us on the
    worker dict, alongside the existing BW/MsgRate."""
    body_start = BLAST_SRC.index("def _on_extra_job_resp")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "{side}_lat_avg_us" in body
    assert "{side}_lat_min_us" in body
    assert "{side}_lat_max_us" in body
    assert "{side}_lat_p99_us" in body


def test_blast_extras_row_builder_forwards_lat():
    """NB-2: `_append_run_log_entry`'s extras row builder must
    forward per-extra lat fields so the Σ averages across every
    worker that reported."""
    body_start = BLAST_SRC.index("def _append_run_log_entry")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert 'w.get("client_lat_avg_us")' in body
    assert 'row["lat_avg_us"] = w.get("client_lat_avg_us")' in body


# ───────────────────── NB-3 + NB-7: lat Σ honesty ─────────────────────


def test_lat_summary_dispatches_on_workers_attempted():
    body_start = REPORT_SRC.index("def _render_lat_summary")
    body_end = REPORT_SRC.index("\ndef ", body_start + 10)
    body = REPORT_SRC[body_start:body_end]
    assert 'aggregation_mode' in body
    assert 'workers_attempted' in body
    assert 'pairs_attempted' in body


def test_lat_summary_renders_n_of_m_partial():
    from utils.rdma_report import _render_lat_summary
    s = {
        "samples": 1, "lat_avg_us": 41.86,
        "lat_min_us": 41.86, "lat_max_us": 41.86,
        "iters_sum": 191148,
        "aggregation_mode": "avg_iterations",
        "workers_attempted": 16,
    }
    html = _render_lat_summary(s)
    assert "1 of 16 workers" in html
    assert "avg 41.86" in html


def test_lat_summary_renders_min_max_when_spread():
    """NB-7: lat Σ must show min/max when present, not just avg."""
    from utils.rdma_report import _render_lat_summary
    s = {
        "samples": 15, "lat_avg_us": 41.7,
        "lat_min_us": 41.4, "lat_max_us": 42.1,
        "iters_sum": 2879531,
        "workers_attempted": 16,
    }
    html = _render_lat_summary(s)
    # Avg + min + max all surface.
    assert "41.70" in html or "41.7" in html
    assert "41.40" in html
    assert "42.10" in html
    # Partial reporting honesty.
    assert "15 of 16 workers" in html


def test_lat_summary_p99_column_rendered():
    """NB-8 followup: when p99 is in the summary, render it
    (not always `—`)."""
    from utils.rdma_report import _render_lat_summary
    s = {
        "samples": 1, "lat_avg_us": 41.86,
        "lat_p99_us": 42.50,
        "iters_sum": 191148,
    }
    html = _render_lat_summary(s)
    assert "42.50" in html


# ───────────────────── NB-4: Run-all auto-tune lat ─────────────────────


def test_blast_run_all_auto_tunes_lat():
    """NB-4: when Run-all crosses into a *_lat test, msg_size,
    parallel_workers, tx_depth must reset to idle-lat defaults."""
    body_start = BLAST_SRC.index("def _apply_test_type_defaults")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert '"msg_size": 2' in body
    assert '"parallel_workers": 1' in body
    assert '"tx_depth": 2' in body
    # Restored on BW.
    assert "target = baseline" in body
    # NB-5: status banner mentions the auto-tune.
    assert "Lat test — auto-tuned" in body


def test_topology_run_all_auto_tunes_lat():
    body_start = TOPO_SRC.index("def _apply_test_type_defaults")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert '"msg_size": 2' in body
    assert '"parallel_workers": 1' in body


def test_blast_populate_queue_captures_baseline():
    body_start = BLAST_SRC.index(
        "def _maybe_populate_run_all_queue")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_run_all_baseline_spins" in body
    assert "_apply_test_type_defaults(first)" in body


def test_blast_advance_re_tunes_for_next_test():
    body_start = BLAST_SRC.index(
        "def _start_next_test_in_queue")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_apply_test_type_defaults(next_test)" in body


# ───────────────────── NB-6: queue advance after extras done ─────────────────────


def test_blast_finalize_extracted_into_separate_method():
    """NB-6: _on_both_finished defers to _finalize_run when extras
    are still in flight, so the Run-all queue doesn't advance
    until every worker has reported."""
    assert "def _finalize_run" in BLAST_SRC
    # _on_both_finished checks all_extras_done before finalizing.
    obf_start = BLAST_SRC.index("def _on_both_finished")
    obf_end = BLAST_SRC.index("\n    def ", obf_start)
    body = BLAST_SRC[obf_start:obf_end]
    assert "all_extras_done" in body
    assert "_pending_finalize" in body


def test_blast_maybe_emit_total_fires_pending_finalize():
    body_start = BLAST_SRC.index("def _maybe_emit_total")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_pending_finalize" in body
    assert "self._finalize_run()" in body


def test_blast_finalize_idempotent():
    body_start = BLAST_SRC.index("def _finalize_run")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_run_finalised" in body


# ───────────────────── NB-9 + NB-10: device refresh ─────────────────────


def test_blast_refresh_device_payloads_method_exists():
    assert "def _refresh_device_payloads" in BLAST_SRC
    body_start = BLAST_SRC.index("def _refresh_device_payloads")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "/api/rdma/devices" in body
    assert "_on_devices_resp" in body


def test_blast_proceed_with_start_refreshes_payloads():
    body_start = BLAST_SRC.index("def _proceed_with_start")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "_refresh_device_payloads" in body


# ───────────────────── NB-11: single-worker headline ─────────────────────


def test_single_worker_bw_headline_says_total_across_1():
    from utils.rdma_report import _render_headline
    rows = [{"label": "worker 0", "state": "done",
             "bw_gbps": 171.32, "msgrate_mpps": 0.3268}]
    summary = {
        "samples": 1, "bw_avg_gbps": 171.32,
        "bw_min_gbps": 171.32, "bw_max_gbps": 171.32,
        "msgrate_avg_mpps": 0.3268,
        "aggregation_mode": "sum_workers",
        "workers_attempted": 1,
    }
    run = {
        "kind": "blast", "test": "send_bw",
        "params": {}, "rows": rows, "summary": summary,
        "endpoints": {},
    }
    html = _render_headline(run)
    assert "total across 1 worker" in html


def test_single_pair_topology_bw_headline_says_total_across_1_pair():
    from utils.rdma_report import _render_headline
    rows = [{"label": "#0.0", "state": "rc=0",
             "bw_gbps": 156.65, "msgrate_mpps": 0.2988}]
    summary = {
        "samples": 1, "bw_avg_gbps": 156.65,
        "msgrate_avg_mpps": 0.2988,
        "aggregation_mode": "sum_pairs",
        "pairs_attempted": 1,
    }
    run = {
        "kind": "topology", "test": "send_bw",
        "params": {}, "rows": rows, "summary": summary,
        "endpoints": {},
    }
    html = _render_headline(run)
    assert "total across 1 pair" in html


# ───────────────────── NB-12: params at Start ─────────────────────


def test_blast_proceed_snapshots_params():
    body_start = BLAST_SRC.index("def _proceed_with_start")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "self._iteration_params = {" in body
    # Spinner reads captured at Start time.
    assert "self._msg_size_spin.value()" in body
    assert "self._parallel_workers_spin.value()" in body


def test_blast_append_run_log_prefers_snapshot():
    body_start = BLAST_SRC.index("def _append_run_log_entry")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # The snapshot is preferred; the spinner re-read is the
    # defensive fallback.
    assert 'getattr(self, "_iteration_params"' in body


def test_topology_start_snapshots_params():
    body_start = TOPO_SRC.index(
        "def _proceed_with_topology_start")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "self._iteration_params = {" in body


def test_topology_append_run_log_prefers_snapshot():
    body_start = TOPO_SRC.index("def _append_run_log_entry")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert 'getattr(self, "_iteration_params"' in body


# ───────────────────── end-to-end ─────────────────────


def test_nb6_finalize_defers_when_extras_inflight_behavioral():
    """v0.5.182 review LOW-2: behavioral coverage of the NB-6 race.

    Scenario: worker 0 finishes BEFORE the parallel extras call their
    done callbacks. _on_both_finished should NOT call _finalize_run
    directly — it must set _pending_finalize and wait. Then once
    all extras report, _maybe_emit_total triggers _finalize_run."""
    from PyQt5.QtWidgets import QApplication
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    dlg = RdmaBlastFlowDialog(server_tg_url='http://x:5050')
    # Bootstrap minimal state.
    dlg._iteration_total = 1
    dlg._iteration_in_progress = True
    dlg._iteration_idx = 0
    dlg._iteration_results = []
    dlg._server_job_id = None
    dlg._client_job_id = None
    dlg._server_finished = True
    dlg._client_finished = True
    dlg._total_emitted = False
    dlg._finalised = False
    dlg._run_finalised = False
    dlg._pending_finalize = False
    dlg._remaining_tests = []
    # Simulate one in-flight extra.
    dlg._extra_workers = [{
        "worker_idx": 1,
        "server": "srv-jid", "client": "cli-jid",
        "server_finished": False, "client_finished": False,
    }]
    # Stub the heavy finalize methods so we can detect calls.
    finalize_calls = {"count": 0}

    def _stub_finalize():
        finalize_calls["count"] += 1
    dlg._finalize_run = _stub_finalize
    # Worker 0 done fires first.
    dlg._on_both_finished()
    assert finalize_calls["count"] == 0, (
        "NB-6 regression: _finalize_run fired before extras done"
    )
    assert dlg._pending_finalize is True, (
        "NB-6: _on_both_finished should set _pending_finalize"
    )
    # Now the extra calls done — _maybe_emit_total should pick up.
    dlg._extra_workers[0]["server_finished"] = True
    dlg._extra_workers[0]["client_finished"] = True
    dlg._extra_workers[0]["client_bw"] = 50.0
    dlg._extra_workers[0]["client_msgrate"] = 0.1
    dlg._client_bw = 50.0
    dlg._client_msgrate = 0.1
    dlg._maybe_emit_total()
    assert finalize_calls["count"] == 1, (
        "NB-6: _maybe_emit_total should fire _finalize_run when "
        "extras finish"
    )


def test_full_report_renders_partial_lat_workers_attempted():
    """End-to-end: a 1-of-16 send_lat run (operator's NB-2 scenario)
    must headline as "1 of 16 workers reported" not "final"."""
    from utils.rdma_report import build_html_report
    rows = [
        {"label": "worker 0", "state": "done",
         "lat_avg_us": 41.86, "iters": 191148},
    ]
    for w in range(1, 16):
        rows.append({
            "label": f"worker {w}", "state": "?",
            "lat_avg_us": None, "iters": None,
        })
    summary = {
        "samples": 1, "lat_avg_us": 41.86,
        "lat_min_us": 41.86, "lat_max_us": 41.86,
        "iters_sum": 191148,
        "workers_attempted": 16,
    }
    run = {
        "kind": "blast", "test": "send_lat",
        "started_at": "2026-06-17T23:32:15",
        "params": {"parallel_workers": 16, "msg_size": 65536},
        "endpoints": {}, "rows": rows, "summary": summary,
    }
    html = build_html_report(
        title="NB-2 partial-lat",
        runs=[run],
        generated_at="2026-06-17T23:33:00",
    )
    # Header surfaces partial reporting.
    assert "1 of 16 workers" in html
    # Σ row also surfaces it.
    assert "of 16 workers" in html
