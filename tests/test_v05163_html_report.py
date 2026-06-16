"""v0.5.163: HTML report export for Blast + Topology dialogs.

Operator: "also allow user to generate report for this test both
in via blast test and topology test". Confirmed format = HTML,
scope = all runs since the dialog opened.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_report import build_html_report


# ───── builder ──────────────────────────────────────────────────────────


def test_empty_runs_renders_friendly_placeholder():
    html = build_html_report(
        title="Blast a RDMA Flow — Session Report",
        runs=[],
        generated_at="2026-06-16T05:50:00",
    )
    assert "<!DOCTYPE html>" in html
    assert "<title>Blast a RDMA Flow" in html
    assert "No runs recorded yet" in html
    assert "Generated:" in html


def test_bw_run_renders_summary_and_rows():
    runs = [{
        "kind": "blast",
        "started_at": "2026-06-16T05:42:00",
        "test": "ib_send_bw",
        "params": {"msg_size": 65536, "qp_count": 1, "duration_s": 30},
        "endpoints": {"server": "tg0 mlx5_0", "client": "tg0 mlx5_0"},
        "rows": [
            {"label": "iter #0", "state": "done",
             "bw_gbps": 171.21, "msgrate_mpps": 0.326553},
            {"label": "iter #1", "state": "done",
             "bw_gbps": 171.56, "msgrate_mpps": 0.327224},
        ],
        "summary": {
            "samples": 2,
            "bw_avg_gbps": 171.38,
            "bw_min_gbps": 171.21,
            "bw_max_gbps": 171.56,
            "msgrate_avg_mpps": 0.3269,
        },
    }]
    html = build_html_report(
        title="Blast a RDMA Flow — Session Report",
        runs=runs,
        generated_at="2026-06-16T05:50:00",
    )
    # Per-run shape.
    assert "Run #1" in html
    assert "ib_send_bw" in html
    # Params rendered (v0.5.167 swapped raw keys for human labels —
    # operators read "Message size" not "msg_size" in the report).
    assert "Message size" in html
    assert "65536" in html
    # Rows + numeric BW.
    assert "171.21" in html
    assert "171.56" in html
    # Σ summary row formatted.
    assert "Σ" in html
    assert "avg 171.38" in html


def test_lat_run_uses_lat_columns():
    runs = [{
        "kind": "topology", "started_at": "2026-06-16T05:42:00",
        "test": "ib_send_lat",
        "params": {"msg_size": 2}, "endpoints": {"pairs": []},
        "rows": [
            {"label": "#0.0", "state": "done",
             "lat_avg_us": 2.95, "lat_p99_us": 7.40,
             "iters": 1000},
        ],
        "summary": None,
    }]
    html = build_html_report(
        title="RDMA Topology Test — Session Report",
        runs=runs,
        generated_at="2026-06-16T05:50:00",
    )
    assert "Lat avg" in html
    assert "Lat p99" in html
    assert "2.95" in html


def test_html_escapes_dangerous_input():
    """Operator-controlled fields (labels, endpoint strings) must
    be HTML-escaped to prevent injection."""
    runs = [{
        "kind": "blast", "started_at": "2026-06-16T05:42:00",
        "test": "ib_send_bw",
        "params": {"note": "<script>alert(1)</script>"},
        "endpoints": {
            "server": "<img onerror=alert(1)>",
            "client": "tg",
        },
        "rows": [
            {"label": "<b>bad</b>", "state": "done", "bw_gbps": 1.0},
        ],
        "summary": None,
    }]
    html = build_html_report(
        title="x",
        runs=runs,
        generated_at="2026-06-16T05:50:00",
    )
    # No raw <script> in output.
    assert "<script>" not in html
    # Escaped form is present.
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


# ───── dialog wiring ────────────────────────────────────────────────────


def test_blast_dialog_has_export_button_and_run_log():
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    assert "self._export_btn = QPushButton(" in src
    assert "📄 Export report" in src
    assert "self._run_log:" in src
    assert "def _append_run_log_entry(" in src
    assert "def _on_export_report_clicked(" in src
    assert 'build_html_report' in src


def test_topology_dialog_has_export_button_and_run_log():
    src = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()
    assert "self._export_btn = QPushButton(" in src
    assert "📄 Export report" in src
    assert "self._run_log:" in src
    assert "def _append_run_log_entry(" in src
    assert "def _on_export_report_clicked(" in src
    assert 'build_html_report' in src
