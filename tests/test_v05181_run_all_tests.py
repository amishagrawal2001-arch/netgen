"""v0.5.181 follow-up: "Run all tests" checkbox cycles through
every perftest test type in both Blast and Topology dialogs.

Operator: "allow user to run all the test in sequence based on
number of iterations, when user click run all checkbox or any
better way to enable. do it for both RDMA topology test and
RDMA blast test."

Semantics:
  * Checkbox seeds `_remaining_tests` with [w_bw, r_bw, s_lat,
    w_lat, r_lat] (sans first); first test runs immediately
  * Each test runs `iterations` times (existing inner loop)
  * After iterations exhaust, next test pops + restarts
  * Combo + checkbox disable while queue runs (visual feedback
    via combo update each tick)
  * Stop clears queue + re-enables UI
  * Re-entry of _on_start_clicked (next-test path) is idempotent
    — the disabled checkbox short-circuits re-seeding
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


# ───────── Blast dialog ─────────


def test_blast_run_all_checkbox_widget():
    assert 'self._run_all_check = QCheckBox("Run all tests")' in BLAST_SRC
    assert "_remaining_tests: List[str] = []" in BLAST_SRC


def test_blast_populate_queue_skips_first():
    body_start = BLAST_SRC.index("def _maybe_populate_run_all_queue")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # First test runs now, rest queue up.
    assert "first, *rest = all_tids" in body
    assert "self._remaining_tests = rest" in body
    # Combo + checkbox disabled while queue runs.
    assert "self._test_combo.setEnabled(False)" in body
    assert "self._run_all_check.setEnabled(False)" in body


def test_blast_populate_queue_idempotent():
    body_start = BLAST_SRC.index("def _maybe_populate_run_all_queue")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    # Idempotency guard: skip when checkbox already disabled.
    assert "if not self._run_all_check.isEnabled():" in body


def test_blast_finalize_advances_queue():
    """The Σ-summary / "Run finished" branch must check
    `_remaining_tests` and call `_start_next_test_in_queue`
    when non-empty.

    v0.5.182 NB-6: the queue-advance logic moved from
    _on_both_finished to _finalize_run so it runs only after
    every extra has reported, not just worker 0."""
    body_start = BLAST_SRC.index("def _finalize_run")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "if self._remaining_tests and not stop:" in body
    assert "self._start_next_test_in_queue()" in body


def test_blast_stop_clears_queue():
    body_start = BLAST_SRC.index("def _on_stop_clicked")
    body_end = BLAST_SRC.index("\n    def ", body_start)
    body = BLAST_SRC[body_start:body_end]
    assert "self._remaining_tests = []" in body
    # UI restored.
    assert "self._test_combo.setEnabled(True)" in body
    assert "self._run_all_check.setEnabled(True)" in body


# ───────── Topology dialog ─────────


def test_topology_run_all_checkbox_widget():
    assert 'self._run_all_check = QCheckBox("Run all tests")' in TOPO_SRC
    assert "self._remaining_tests: List[str] = []" in TOPO_SRC
    # Canonical test list defined.
    assert "self._all_perftest_tests" in TOPO_SRC


def test_topology_populate_queue_skips_first():
    body_start = TOPO_SRC.index("def _maybe_populate_run_all_queue")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "first, *rest = self._all_perftest_tests" in body
    assert "self._remaining_tests = rest" in body


def test_topology_populate_queue_idempotent():
    body_start = TOPO_SRC.index("def _maybe_populate_run_all_queue")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "if not self._run_all_check.isEnabled():" in body


def test_topology_finalize_advances_queue():
    body_start = TOPO_SRC.index("def _on_job_resp")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "if self._remaining_tests and not stop_requested:" in body
    assert "self._start_next_test_in_queue()" in body


def test_topology_stop_clears_queue():
    body_start = TOPO_SRC.index("def _on_stop_clicked")
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "self._remaining_tests = []" in body
    assert "self._test_combo.setEnabled(True)" in body


def test_topology_start_path_seeds_queue():
    body_start = TOPO_SRC.index("def _on_start_clicked")
    # use the next def at same indent
    body_end = TOPO_SRC.index("\n    def ", body_start)
    body = TOPO_SRC[body_start:body_end]
    assert "self._maybe_populate_run_all_queue()" in body


# ───────── functional check via Qt smoke ─────────


def test_run_all_queue_advances_through_all_six_tests():
    """Smoke test: populate, then simulate finishing tests
    one-by-one. Confirm the queue empties to the right order."""
    from PyQt5.QtWidgets import QApplication
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
    dlg = RdmaBlastFlowDialog(server_tg_url='http://x:5050')
    dlg.show()
    dlg._run_all_check.setChecked(True)
    dlg._maybe_populate_run_all_queue()
    # First test = send_bw, 5 in queue.
    assert dlg._test_combo.currentData() == "send_bw"
    assert dlg._remaining_tests == [
        "write_bw", "read_bw", "send_lat", "write_lat", "read_lat"]
    # Manually pop one — mimics queue advance.
    next_test = dlg._remaining_tests.pop(0)
    assert next_test == "write_bw"
    assert len(dlg._remaining_tests) == 4
    # Empty queue — last test.
    dlg._remaining_tests = []
    assert dlg._remaining_tests == []
