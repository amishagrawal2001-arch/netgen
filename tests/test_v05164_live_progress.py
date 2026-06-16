"""v0.5.164: live progress visualization in Blast + Topology.

Operator: "see the visualization during test running" — Live stats
was just spamming ~270 identical "running" lines with no progress
indicator. Added a QProgressBar + label strip between the action
row and the Live stats / Results panel.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOPO = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


def test_blast_has_progress_widget_and_update_helper():
    assert "self._progress_bar = QProgressBar()" in SRC_BLAST
    assert "self._progress_widget" in SRC_BLAST
    assert "def _update_progress_widget(" in SRC_BLAST
    # And it's wired into the running-poll site.
    assert "self._update_progress_widget(elapsed_secs)" in SRC_BLAST


def test_topology_has_progress_widget_and_update_helper():
    assert "self._progress_bar = QProgressBar()" in SRC_TOPO
    assert "self._progress_widget" in SRC_TOPO
    assert "def _update_progress_from_jobs(" in SRC_TOPO
    # And called from _on_job_resp on every running poll.
    assert "self._update_progress_from_jobs()" in SRC_TOPO


def test_both_dialogs_reset_progress_on_run_settle():
    """When the run finishes (or operator hits Stop) the
    progress widget should hide so the dialog returns to its
    idle state."""
    assert "def _reset_progress_widget(" in SRC_BLAST
    assert "def _reset_progress_widget(" in SRC_TOPO
    assert "self._reset_progress_widget()" in SRC_BLAST
    assert "self._reset_progress_widget()" in SRC_TOPO


def test_progress_widget_hidden_by_default():
    """Idle dialog should NOT show the progress bar until at
    least one running poll has come back."""
    assert "self._progress_widget.setVisible(False)" in SRC_BLAST
    assert "self._progress_widget.setVisible(False)" in SRC_TOPO


def test_progress_label_reports_elapsed_and_duration():
    """The label carries both numerator (elapsed) and denominator
    (duration) so operators can sanity-check perftest's Duration
    matches what they configured."""
    # Blast: f"{head} • {int(elapsed_secs)}s / {duration}s"
    assert "int(elapsed_secs)" in SRC_BLAST
    assert "{duration}s" in SRC_BLAST
    # Topology: f"{head} • {int(elapsed)}s / {duration}s"
    assert "int(elapsed)" in SRC_TOPO
    assert "{duration}s" in SRC_TOPO
