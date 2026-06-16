"""v0.5.165: post-run results summary card in Blast + Topology.

Operator: "post run complete visualizaiton, this is also not good."
After a run finishes the dialog should not just dump raw rc=0/BW
lines at the bottom of Live stats. Both dialogs now grow a green
pill card with the headline BW + MsgRate so operators see the
result without scrolling through the per-pair grid.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOPO = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


def test_both_dialogs_have_results_card_widget():
    assert "self._results_card = QLabel(" in SRC_BLAST
    assert "self._results_card = QLabel(" in SRC_TOPO


def test_both_dialogs_have_render_helper():
    assert "def _render_results_card(" in SRC_BLAST
    assert "def _render_results_card(" in SRC_TOPO


def test_both_dialogs_hide_card_by_default():
    """The card is for completed runs only — must be hidden until
    a run actually settles."""
    assert "self._results_card.setVisible(False)" in SRC_BLAST
    assert "self._results_card.setVisible(False)" in SRC_TOPO


def test_both_dialogs_render_card_on_completion():
    """Render hook must be called from the all-done branch."""
    assert "self._render_results_card()" in SRC_BLAST
    assert "self._render_results_card()" in SRC_TOPO


def test_card_uses_run_log_last_entry():
    """The render helper reads the just-appended _run_log entry —
    must not synthesize fresh state, must consume the same data
    the Export Report button uses."""
    assert "_run_log" in SRC_BLAST
    assert "_run_log" in SRC_TOPO


def test_card_html_escapes_test_label():
    """The 'test' label comes from operator-controlled combo data —
    must be HTML-escaped to keep the card injection-safe."""
    assert "escape(run.get('test')" in SRC_BLAST
    assert "escape(run.get('test')" in SRC_TOPO


def test_card_carries_big_number_styling():
    """Operator should see a headline-style BW figure (28px) +
    MsgRate (18px) — that's the whole point of the card."""
    for src in (SRC_BLAST, SRC_TOPO):
        assert "font-size:28px" in src
        assert "font-size:18px" in src


def test_topology_card_includes_pair_and_shape_context():
    """Topology runs aren't just one perftest — the card has to
    surface how many pairs ran and which shape was selected."""
    assert "shape" in SRC_TOPO
    assert "pair" in SRC_TOPO
    # 'shape' label is escaped too — operator controls the combo.
    assert "escape(str(params.get('shape')" in SRC_TOPO


def test_card_hidden_on_new_start():
    """Starting a fresh run must clear the previous run's card so
    operators don't confuse old + new results."""
    # Blast hides on Start in _proceed_with_start.
    blast_proceed_idx = SRC_BLAST.find("def _proceed_with_start")
    assert blast_proceed_idx > 0
    blast_proceed_body = SRC_BLAST[blast_proceed_idx:blast_proceed_idx + 4000]
    assert "self._results_card.setVisible(False)" in blast_proceed_body
    # Topology hides on Start in _proceed_with_topology_start.
    topo_proceed_idx = SRC_TOPO.find("def _proceed_with_topology_start")
    assert topo_proceed_idx > 0
    topo_proceed_body = SRC_TOPO[topo_proceed_idx:topo_proceed_idx + 4000]
    assert "self._results_card.setVisible(False)" in topo_proceed_body


def test_topology_uses_html_escape_import():
    """Topology dialog must import escape — render helper depends on it."""
    assert "from html import escape" in SRC_TOPO
