"""v0.5.181: Σ row polish + Topology sweep checkbox.

After v0.5.180 the operator's report rendered correctly but had
three cosmetic issues:

  #3 `avg X (min X, max X)` when spread = 0 (single-sample runs)
  #4 MsgRate Σ shows `0.1587` while BW Σ shows
     `avg 156.65 (min 156.65, max 156.65)` — format mismatch
  #5 Iters column shows `—` on Σ rows; could sum across samples

And one deferred feature gap:

  M-RE-1 Topology dialog has no sweep checkbox parity with Blast
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
    _render_lat_summary,
    build_html_report,
)


# ───────── #3: same-value collapse ─────────


def test_fmt_summary_cell_collapses_same_value():
    assert _fmt_summary_cell("156.65", "156.65", "156.65") == "avg 156.65"


def test_fmt_summary_cell_keeps_parens_when_spread():
    assert _fmt_summary_cell(
        "156.65", "150.10", "160.20"
    ) == "avg 156.65 (min 150.10, max 160.20)"


def test_fmt_summary_cell_avg_only_when_no_min_max():
    assert _fmt_summary_cell("5.54", "—", "—") == "avg 5.54"


def test_fmt_summary_cell_em_dash_when_no_avg():
    assert _fmt_summary_cell("—", "1.0", "2.0") == "—"


# ───────── #4: MsgRate Σ parity with BW ─────────


def test_bw_summary_msgrate_carries_parens_when_spread():
    """MsgRate Σ should show `avg X (min Y, max Z)` not bare X."""
    s = {
        "samples": 3, "bw_avg_gbps": 100.0,
        "bw_min_gbps": 90.0, "bw_max_gbps": 110.0,
        "msgrate_avg_mpps": 0.30,
        "msgrate_min_mpps": 0.28,
        "msgrate_max_mpps": 0.32,
        "iters_sum": 3000000,
    }
    html = _render_bw_summary(s)
    # BW col: full parens
    assert "avg 100.00 (min 90.00, max 110.00)" in html
    # MsgRate col: full parens
    assert "avg 0.3000 (min 0.2800, max 0.3200)" in html
    # Iters col: sum, not "—"
    assert ">3000000<" in html


def test_bw_summary_collapses_single_sample():
    """Operator's actual case: 1 sample, 156.65 Gbps. Σ shows
    `avg 156.65` (not `avg 156.65 (min 156.65, max 156.65)`)."""
    s = {
        "samples": 1, "bw_avg_gbps": 156.65,
        "bw_min_gbps": 156.65, "bw_max_gbps": 156.65,
        "msgrate_avg_mpps": 0.2990,
        "msgrate_min_mpps": 0.2990,
        "msgrate_max_mpps": 0.2990,
        "iters_sum": 4785302,
    }
    html = _render_bw_summary(s)
    assert "avg 156.65" in html
    assert "min 156.65" not in html
    assert "max 156.65" not in html


# ───────── #5: Iters sum ─────────


def test_lat_summary_carries_iters_sum():
    s = {"samples": 1, "lat_avg_us": 5.54,
         "lat_min_us": 5.54, "lat_max_us": 5.54,
         "iters_sum": 1434217}
    html = _render_lat_summary(s)
    assert ">1434217<" in html
    # Same-value avg collapse:
    assert "avg 5.54" in html
    assert "min 5.54" not in html


# ───────── M-RE-1: Topology sweep checkbox ─────────


def test_topology_dialog_has_sweep_widgets():
    src = (REPO / "widgets"
           / "rdma_topology_dialog.py").read_text()
    assert "self._sweep_sizes_check = QCheckBox" in src
    assert "self._sweep_iters_spin = QSpinBox" in src
    assert "_on_sweep_toggled" in src
    assert "_refresh_sweep_visibility" in src


def test_topology_common_opts_forwards_sweep():
    src = (REPO / "widgets"
           / "rdma_topology_dialog.py").read_text()
    body_start = src.index("def _common_opts")
    body_end = src.index("\n    def ", body_start)
    body = src[body_start:body_end]
    assert 'opts["sweep_sizes"] = True' in body
    assert 'opts["iterations_per_size"]' in body
    # Visibility gate — don't propagate when checkbox is hidden
    # (BW test type).
    assert "isVisibleTo(self)" in body


def test_topology_sweep_hidden_on_bw_tests():
    src = (REPO / "widgets"
           / "rdma_topology_dialog.py").read_text()
    body_start = src.index("def _refresh_sweep_visibility")
    body_end = src.index("\n    def ", body_start)
    body = src[body_start:body_end]
    # Visibility flips on _lat suffix.
    assert 'endswith("_lat")' in body
    # When switching back to BW the checkbox auto-unticks.
    assert "setChecked(False)" in body


# ───────── end-to-end ─────────


def test_full_report_polish_renders_clean():
    """Operator's actual scenario from the v0.5.180 report:
    156.65 Gbps single-sample BW run. Σ should be tidy."""
    run = {
        "kind": "topology",
        "test": "read_bw",
        "started_at": "2026-06-17T11:20:57",
        "params": {"shape": "mesh", "iterations": 2,
                   "qp_count": 128},
        "endpoints": {"pairs": [{"idx": 0,
                                 "server": "srv06.mlx5_0",
                                 "client": "srv06.mlx5_3"}]},
        "rows": [
            {"label": "#0.0", "state": "rc=0",
             "bw_gbps": 156.65, "msgrate_mpps": 0.2988,
             "iters": 4781410},
            {"label": "#1.0", "state": "rc=0",
             "bw_gbps": 156.65, "msgrate_mpps": 0.2988,
             "iters": 4781432},
        ],
        "summary": {
            "samples": 2, "bw_avg_gbps": 156.65,
            "bw_min_gbps": 156.65, "bw_max_gbps": 156.65,
            "msgrate_avg_mpps": 0.2988,
            "msgrate_min_mpps": 0.2988,
            "msgrate_max_mpps": 0.2988,
            "iters_sum": 9562842,
        },
    }
    html = build_html_report(
        title="Polish test",
        runs=[run],
        generated_at="2026-06-17T11:25:00",
    )
    # Pre-fix output: `avg 156.65 (min 156.65, max 156.65)`
    # Post-fix output: `avg 156.65`
    assert "avg 156.65" in html
    assert "(min 156.65, max 156.65)" not in html
    # MsgRate now matches the format (avg X — collapsed since
    # the spread is zero).
    assert "avg 0.2988" in html
    # Iters sum shows in the Σ row.
    assert ">9562842<" in html
