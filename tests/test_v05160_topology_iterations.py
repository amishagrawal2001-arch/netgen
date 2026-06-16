"""v0.5.160: Topology dialog UI polish + N-run iteration loop.

Operator: "UI polish is needed for RDMA topology test also,
Shared workload input section can be more compact, increase stats
section vertical size, add number of iterations to run in the
shared workload section so that when user start topology test it
iterate through and record the results in the per pair stats
section #0, #1.. etc."

Two distinct asks:
  1. UI polish — compact workload (vertical 8 → 4) + bigger stats
     table (160 → 360 min height).
  2. Iterations feature — new spinbox in Shared workload that
     drives N full topology runs. Each iteration appends one row
     per pair to the table labeled "#<iter>.<pair>"; after every
     iteration finishes, a Σ summary row shows avg/min/max BW
     across the (iter, pair) samples.

Local commit only — operator said "do not generate new rel till
asked".
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


# ───── #1: UI polish ────────────────────────────────────────────────────


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()


def test_workload_grid_vertical_spacing_compacted():
    """Operator wanted "more compact" — back to 4 (the v0.5.156
    baseline) from the v0.5.159 bump to 8."""
    assert "tg.setVerticalSpacing(4)" in SRC


def test_stats_table_minimum_height_bumped():
    """v0.5.160 bumped 160 → 360; the compaction pass landed on
    200 (enough for ~8 rows; the stretch=1 below claims any freed
    vertical room when there are more)."""
    assert "self._stats_table.setMinimumHeight(200)" in SRC
    # Old values gone.
    assert "self._stats_table.setMinimumHeight(160)" not in SRC
    assert "self._stats_table.setMinimumHeight(360)" not in SRC


def test_blast_test_params_vertical_spacing_reverted():
    """v0.5.159's bump to 8 added whitespace; v0.5.160 reverts to
    v0.5.152's 2."""
    assert "tg.setVerticalSpacing(2)" in SRC_BLAST
    assert "tg.setVerticalSpacing(8)" not in SRC_BLAST


def test_blast_button_heights_capped():
    """The fix for button-bearing rows being visibly taller than
    bare-spinbox rows: cap the buttons to 28 px (matches QSpinBox
    height on macOS)."""
    assert "self._qp_verify_btn.setMaximumHeight(28)" in SRC_BLAST
    assert "self._max_bw_btn.setMaximumHeight(28)" in SRC_BLAST


def test_topology_max_bw_button_height_capped():
    assert "self._max_bw_btn.setMaximumHeight(28)" in SRC


# ───── #3: Blast also gains the Iterations field + loop ─────────────────


def test_blast_iterations_spinbox_added():
    """Operator: "also don't see number of iterations input" —
    Blast now has its own Iterations spinbox in the Test
    parameters section."""
    assert "self._iterations_spin = QSpinBox()" in SRC_BLAST
    assert "self._iterations_spin.setRange(1, 1000)" in SRC_BLAST


def test_blast_proceed_with_start_sets_up_iteration_state():
    """First call (iteration 0) sets the loop state; subsequent
    iterations re-enter via `_start_next_iteration` and reuse the
    cached state."""
    body = _extract_method(SRC_BLAST, "_proceed_with_start")
    assert "_iteration_in_progress" in body
    assert "_iteration_total" in body
    assert "_iteration_results" in body
    assert "_iteration_stop_requested" in body


def test_blast_on_both_finished_advances_iteration():
    """At end of each run, capture the iteration's BW + either
    schedule the next iteration via QTimer.singleShot or emit
    the Σ summary."""
    body = _extract_method(SRC_BLAST, "_on_both_finished")
    assert "_iteration_idx" in body
    assert "_iteration_results.append" in body
    assert "QTimer.singleShot" in body
    assert "_start_next_iteration" in body
    assert "_emit_iteration_summary" in body


def test_blast_start_next_iteration_method_exists():
    assert "def _start_next_iteration(" in SRC_BLAST


def test_blast_emit_iteration_summary_renders_avg_min_max():
    body = _extract_method(SRC_BLAST, "_emit_iteration_summary")
    assert "min(bws)" in body
    assert "max(bws)" in body
    assert "Σ across" in body


def test_blast_stop_clicked_halts_iteration_loop():
    """Operator-driven Stop must terminate the loop, not just
    stop the current iteration's perftest jobs."""
    body = _extract_method(SRC_BLAST, "_on_stop_clicked")
    assert "_iteration_stop_requested = True" in body
    assert "_iteration_in_progress = False" in body


# ───── #2a: Iterations spinbox ──────────────────────────────────────────


def test_iterations_spinbox_added():
    assert "self._iterations_spin = QSpinBox()" in SRC
    assert "self._iterations_spin.setRange(1, 1000)" in SRC


def test_iterations_spinbox_placed_in_workload_grid():
    """Lives on row 4 next to Parallel workers."""
    assert 'tg.addWidget(QLabel("Iterations:"), 4, 2' in SRC
    assert "tg.addWidget(self._iterations_spin, 4, 3)" in SRC


# ───── #2b: iteration loop ──────────────────────────────────────────────


def test_proceed_with_start_sets_up_iteration_state():
    body = _extract_method(SRC, "_proceed_with_topology_start")
    assert "self._iterations_total" in body
    assert "self._iteration_idx = 0" in body
    assert "self._iteration_results" in body
    assert "self._stop_requested = False" in body
    # Hands off to _run_one_iteration instead of inlining the
    # per-pair start dispatch.
    assert "self._run_one_iteration()" in body


def test_run_one_iteration_method_exists():
    assert "def _run_one_iteration(" in SRC


def test_run_one_iteration_appends_rows_and_dispatches():
    body = _extract_method(SRC, "_run_one_iteration")
    # Fresh per-iteration job state.
    assert "self._pair_jobs = {" in body
    assert "self._latest_jobs = {}" in body
    # Capture the row offset before appending.
    assert "self._current_iter_base_row" in body
    assert "self._populate_stats_table_skeleton" in body
    # Then dispatch the per-pair perftest server starts.
    assert "/api/rdma/perftest/start" in body


def test_skeleton_appends_per_iteration_rows():
    body = _extract_method(SRC, "_populate_stats_table_skeleton")
    # Label uses iter.pair format for multi-iteration runs.
    assert "_iteration_idx" in body
    assert "_iterations_total" in body
    # Row index relative to base, not pair_index alone.
    assert "base + p.pair_index" in body


def test_update_pair_row_offsets_by_iteration_base():
    """Rows for iteration N start at _current_iter_base_row; old
    iterations' rows must not be overwritten."""
    body = _extract_method(SRC, "_update_pair_row")
    assert "_current_iter_base_row" in body
    assert "+ pair_index" in body


# ───── #2c: iteration completion + summary ──────────────────────────────


def test_on_job_resp_advances_iteration():
    """After all_pairs_done, snapshot + bump iteration index and
    re-enter _run_one_iteration (or emit summary if done)."""
    body = _extract_method(SRC, "_on_job_resp")
    assert "_snapshot_iteration_results" in body
    assert "self._iteration_idx += 1" in body
    assert "_run_one_iteration" in body
    assert "_append_summary_row" in body


def test_snapshot_method_captures_per_pair_stats():
    body = _extract_method(SRC, "_snapshot_iteration_results")
    assert "final_bw_avg_gbps" in body
    assert "final_msg_rate_mpps" in body
    assert "_iteration_results.append" in body


def test_summary_row_method_renders_avg_min_max():
    body = _extract_method(SRC, "_append_summary_row")
    assert "_iteration_results" in body
    assert "min(" in body
    assert "max(" in body
    assert "Σ" in body
    # Skip when only one iteration ran (would just duplicate the
    # lone row).
    assert "_iterations_total < 2" in body


def test_stop_clicked_sets_stop_flag():
    """Operator-driven Stop must halt the iteration loop, not just
    the current iteration's perftest jobs."""
    body = _extract_method(SRC, "_on_stop_clicked")
    assert "self._stop_requested = True" in body


# ───── helpers ──────────────────────────────────────────────────────────


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)
