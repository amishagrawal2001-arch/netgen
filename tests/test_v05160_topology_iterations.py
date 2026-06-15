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


def test_workload_grid_vertical_spacing_compacted():
    """Operator wanted "more compact" — back to 4 (the v0.5.156
    baseline) from the v0.5.159 bump to 8."""
    assert "tg.setVerticalSpacing(4)" in SRC


def test_stats_table_minimum_height_bumped():
    """160 → 360 so iterations × pairs fits without resizing."""
    assert "self._stats_table.setMinimumHeight(360)" in SRC
    # Old value gone.
    assert "self._stats_table.setMinimumHeight(160)" not in SRC


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
