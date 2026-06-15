"""v0.5.159: critical extras-unwrap fix + UI polish + regression
undo of v0.5.157's host_info_cache reset.

Operator screenshot from a 16-worker Blast run on srv06 showed:
  * Worker 0 finished with [client] done BW=156.1 Gbps
  * Zero `[worker N/side] done` lines for extras 1-15
  * No `[TOTAL across …]` line
  * v0.5.158 warning fired even though operator clicked 🚀 Max BW

Root causes:

  1. CRITICAL: `_on_extra_job_resp` read `data.get("finished_at")`
     directly. But `/api/rdma/perftest/job/<id>` wraps the job in
     `{"job": {...}}` — `_on_job_resp` (worker 0) correctly unwraps
     `data["job"]`. Extras never transitioned to finished →
     TOTAL never emitted → per-worker done rows silently lost.
     Bug present since v0.5.155.

  2. REGRESSION: v0.5.157 added `self._host_info_cache = None`
     in `_proceed_with_start` AND in `_proceed_with_topology_
     start`. That wiped the 🚀 Max BW selection on every Start.
     host_info is a HOST-level snapshot — reusing it is correct.

  3. LATENT: v0.5.158 added `self._stats_view.append(...)` to
     Topology's `_start_pair_extra_workers`. Topology has
     `_stats_table` + `_status_label`, not `_stats_view` — that
     code path would AttributeError. Redirected to `_set_status_
     error`.

UI polish:
  * Verify + Max BW buttons widened (fixed-width was clipping
    text on macOS).
  * Test parameters grid vertical spacing 2 → 8 (rows were
    kissing on Retina).
  * `_stats_view` auto-scrolls to bottom on every textChanged
    so the final BW row stays visible without manual scroll.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOPO = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


# ───── #1: extras unwrap data["job"] ─────────────────────────────────────


def test_on_extra_job_resp_unwraps_job_envelope():
    """The /api/rdma/perftest/job/<id> response is wrapped as
    {"job": {...}}. Pre-v0.5.159 read .get("finished_at")
    directly — that's always None, so extras never transitioned
    to finished."""
    body = _extract_method(SRC_BLAST, "_on_extra_job_resp")
    assert 'job = data.get("job") or {}' in body
    assert 'job.get("finished_at")' in body
    # And the final stats come from the unwrapped dict, not data.
    assert 'job.get("returncode")' in body
    assert 'job.get("final_bw_avg_gbps")' in body
    assert 'job.get("final_msg_rate_mpps")' in body


def test_on_extra_job_resp_no_longer_reads_data_directly():
    """Once unwrapped, `data.get(...)` for the wire-format fields
    is gone (it was always returning None). Strip docstrings +
    comments before checking — those naturally mention the old
    bug strings."""
    code = _strip_doc_and_comments(
        _extract_method(SRC_BLAST, "_on_extra_job_resp"))
    assert 'data.get("finished_at")' not in code
    assert 'data.get("returncode")' not in code


# ───── #2: undo v0.5.157 _host_info_cache reset ──────────────────────────


def test_blast_proceed_with_start_no_longer_wipes_host_info_cache():
    code = _strip_doc_and_comments(
        _extract_method(SRC_BLAST, "_proceed_with_start"))
    assert "self._host_info_cache = None" not in code


def test_topology_proceed_with_topology_start_no_longer_wipes_cache():
    code = _strip_doc_and_comments(
        _extract_method(SRC_TOPO, "_proceed_with_topology_start"))
    assert "self._host_info_cache = {}" not in code
    assert "self._host_info_cache = None" not in code


def test_blast_close_event_no_longer_wipes_host_info_cache():
    code = _strip_doc_and_comments(_extract_method(SRC_BLAST, "closeEvent"))
    assert "self._host_info_cache = None" not in code


def test_topology_close_event_no_longer_wipes_host_info_cache():
    code = _strip_doc_and_comments(_extract_method(SRC_TOPO, "closeEvent"))
    assert "self._host_info_cache = {}" not in code


# ───── #3: topology fallback warning no longer crashes ──────────────────


def test_topology_fallback_warning_uses_status_label_not_stats_view():
    """`_stats_view` doesn't exist on the topology dialog. The
    v0.5.158 code would AttributeError if the fallback path
    fired. Redirected to `_set_status_error` (which writes the
    operator-visible status label)."""
    code = _strip_doc_and_comments(
        _extract_method(SRC_TOPO, "_start_pair_extra_workers"))
    assert "self._stats_view.append" not in code
    assert "self._set_status_error(" in code


# ───── #4: UI polish ────────────────────────────────────────────────────


def test_blast_qp_verify_button_uses_min_width():
    """Was setFixedWidth(78) → clipped on macOS. Min width lets
    Qt size for the platform's font metrics."""
    assert "self._qp_verify_btn.setFixedWidth(" not in SRC_BLAST
    assert "self._qp_verify_btn.setMinimumWidth(96)" in SRC_BLAST


def test_blast_max_bw_button_uses_min_width():
    assert "self._max_bw_btn.setFixedWidth(86)" not in SRC_BLAST
    assert "self._max_bw_btn.setMinimumWidth(108)" in SRC_BLAST


def test_topology_max_bw_button_uses_min_width():
    assert "self._max_bw_btn.setFixedWidth(82)" not in SRC_TOPO
    assert "self._max_bw_btn.setMinimumWidth(108)" in SRC_TOPO


def test_blast_test_params_vertical_spacing_loosened():
    """v0.5.152 set it to 2 (rows kissed). v0.5.159 bumps to 8."""
    # The exact "tg.setVerticalSpacing(2)" line is gone.
    assert "tg.setVerticalSpacing(2)" not in SRC_BLAST
    assert "tg.setVerticalSpacing(8)" in SRC_BLAST


def test_topology_test_params_vertical_spacing_loosened():
    # Topology was at 4; v0.5.159 bumps to 8 for parity.
    # Verify the 8 entry is present (file has multiple grids; the
    # test_params one is the relevant bump).
    assert "tg.setVerticalSpacing(8)" in SRC_TOPO


def test_blast_stats_view_auto_scrolls():
    """Connecting textChanged → _scroll_stats_to_bottom snaps the
    scrollbar to max after every append."""
    assert (
        "self._stats_view.textChanged.connect("
        "self._scroll_stats_to_bottom)" in SRC_BLAST
    )
    assert "def _scroll_stats_to_bottom(" in SRC_BLAST


def test_blast_stats_view_height_bumped():
    """Bumped 280 → 320 so the [client] done row stays visible
    after the running-tick spam."""
    assert "self._stats_view.setMinimumHeight(280)" not in SRC_BLAST
    assert "self._stats_view.setMinimumHeight(320)" in SRC_BLAST


# ───── helpers ──────────────────────────────────────────────────────────


def _strip_doc_and_comments(body: str) -> str:
    """Strip triple-quoted docstrings and `#` comments — these
    naturally mention the historical-bug strings the live code
    no longer contains."""
    no_doc = re.sub(r'""".*?"""', "", body, flags=re.DOTALL)
    return "\n".join(
        ln for ln in no_doc.splitlines() if not ln.lstrip().startswith("#")
    )


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)
