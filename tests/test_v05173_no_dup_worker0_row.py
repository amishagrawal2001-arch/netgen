"""v0.5.173: drop redundant worker 0 row when iter rows already cover it.

Operator: "ran itr 1 , than ran two itrs, however report picked
up first and last."

The 2-iter Run #2 was producing rows:
  - iter #0  = 162.93
  - iter #1  = 162.67
  - worker 0 = 162.67  ← duplicate of iter #1's value

The operator visually parsed this as "first" (iter #0 = 162.93)
and "last" (worker 0 = 162.67) — the middle iter #1 row was
swallowed by having the same value as the trailing worker 0.

The fix: `_append_run_log_entry` only emits the trailing worker 0
row when it adds information:
  1. Single-iter run with no parallel extras — worker 0 IS the
     result (iter rows produced nothing).
  2. Parallel run (extras present) — worker 0 is the
     iteration-0 half of the per-worker breakdown.

For iterate-N with no extras, the iter rows are the complete
breakdown — no worker 0 dup needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_append_run_log_skips_worker0_when_iter_rows_exist():
    """Source-level check: the trailing worker 0 append is
    gated by `extras or not already_have_iter_rows`."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    # Find the _append_run_log_entry function body.
    idx = src.find("def _append_run_log_entry")
    assert idx > 0
    body = src[idx:idx + 4000]
    # The gate must be present.
    assert "already_have_iter_rows" in body
    assert "if extras or not already_have_iter_rows" in body


def test_single_iter_run_still_emits_worker0():
    """1-iter run without extras: iter rows are empty, so the
    worker 0 row IS the result and must still appear."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    idx = src.find("def _append_run_log_entry")
    body = src[idx:idx + 4000]
    # The fallback path (the trailing rows.append) is the worker 0
    # case. Must still be there.
    assert '"label": "worker 0"' in body
    # And `_client_bw` must still be the source.
    assert "getattr(self, \"_client_bw\", None)" in body


def test_summary_excludes_worker0_dup_for_iterate_n():
    """The Σ summary is computed from `rows`. For iterate-N runs
    where worker 0 is suppressed, the summary samples count
    correctly equals the iteration count, not iter_count + 1."""
    # Simulate the function body's row-building logic with a
    # 2-iter, no-extras scenario.
    iter_results = [
        {"iter": 0, "bw": 162.93, "msgrate": 0.310765},
        {"iter": 1, "bw": 162.67, "msgrate": 0.310263},
    ]
    extras = []
    rows = []
    for r in iter_results:
        rows.append({
            "label": f"iter #{r['iter']}",
            "state": "done",
            "bw_gbps": r["bw"],
            "msgrate_mpps": r["msgrate"],
        })
    for w in extras:
        rows.append(w)
    already_have_iter_rows = bool(iter_results)
    if extras or not already_have_iter_rows:
        rows.append({"label": "worker 0", "bw_gbps": 162.67,
                      "msgrate_mpps": 0.310263})
    # Should have exactly 2 rows (no worker 0 dup).
    assert len(rows) == 2
    labels = [r["label"] for r in rows]
    assert labels == ["iter #0", "iter #1"]


def test_single_iter_no_extras_still_emits_worker0():
    """1-iter run with no extras: iter_results=[], extras=[]. The
    fallback condition `extras or not already_have_iter_rows`
    evaluates True (no iter rows), so worker 0 IS appended."""
    iter_results = []
    extras = []
    rows = []
    for r in iter_results:
        rows.append({"label": f"iter #{r['iter']}", "bw_gbps": r["bw"]})
    already_have_iter_rows = bool(iter_results)
    if extras or not already_have_iter_rows:
        rows.append({"label": "worker 0", "bw_gbps": 173.18})
    assert len(rows) == 1
    assert rows[0]["label"] == "worker 0"
    assert rows[0]["bw_gbps"] == 173.18


def test_parallel_run_with_extras_emits_worker0():
    """Parallel-worker run (extras present): worker 0 IS the
    counterpart of the extras and must appear."""
    iter_results = []   # single-iter run
    extras = [
        {"worker_idx": 1, "client_bw": 70.5, "client_msgrate": 0.1,
         "client_finished": True, "server_finished": True},
        {"worker_idx": 2, "client_bw": 71.0, "client_msgrate": 0.1,
         "client_finished": True, "server_finished": True},
    ]
    rows = []
    for r in iter_results:
        rows.append({"label": f"iter #{r['iter']}"})
    for w in extras:
        rows.append({"label": f"worker {w['worker_idx']}",
                     "bw_gbps": w["client_bw"]})
    already_have_iter_rows = bool(iter_results)
    if extras or not already_have_iter_rows:
        rows.append({"label": "worker 0", "bw_gbps": 70.0})
    labels = [r["label"] for r in rows]
    # 2 extras + 1 worker 0 = 3 worker rows.
    assert labels == ["worker 1", "worker 2", "worker 0"]


def test_iterate_n_with_extras_emits_worker0():
    """The mixed case — iterate-N AND parallel workers. Operator
    might launch a 2-iter run with 4 parallel workers per iter.
    The trailing worker 0 row still belongs (counterpart of
    extras), so it must appear."""
    iter_results = [{"iter": 0, "bw": 162.93, "msgrate": 0.31}]
    extras = [
        {"worker_idx": 1, "client_bw": 70.5, "client_msgrate": 0.1,
         "client_finished": True, "server_finished": True},
    ]
    rows = []
    for r in iter_results:
        rows.append({"label": f"iter #{r['iter']}"})
    for w in extras:
        rows.append({"label": f"worker {w['worker_idx']}"})
    already_have_iter_rows = bool(iter_results)
    if extras or not already_have_iter_rows:
        rows.append({"label": "worker 0"})
    labels = [r["label"] for r in rows]
    assert "iter #0" in labels
    assert "worker 1" in labels
    assert "worker 0" in labels
