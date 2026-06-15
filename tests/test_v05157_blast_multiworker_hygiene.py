"""v0.5.157 Slice B: Blast + Topology multi-worker hygiene.

Audit findings from v0.5.156 sweep:
  1. `_host_info_cache` lives forever — HCA change between Starts
     reuses stale NUMA snapshot.
  2. TOTAL line excluded worker 0 (operator had to add the
     "[client] done ... BW=X" line to the "[TOTAL extras only]"
     line by hand).
  3. closeEvent didn't reset `_extra_workers` / `_total_emitted`,
     so a 2nd open of the dialog kept polling stale job_ids and
     suppressed the next run's summary.
  4. cpu_pin fell back to `list(range(N))` when host_info was
     missing — taskset errors on CPUs that don't exist.

Operator: "go A first then B, and then C" — this is slice B.
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


# ───── #1: cache reset per Start ─────────────────────────────────────────


def test_blast_proceed_with_start_resets_host_info_cache():
    """v0.5.157 added this reset; v0.5.159 backed it out — host_info
    is a HOST-level snapshot (NUMA + full hca_numa map), so reusing
    it across Starts is correct. The cache reset silently discarded
    the operator's 🚀 Max BW selection."""
    body = _extract_method(SRC_BLAST, "_proceed_with_start")
    assert "self._host_info_cache = None" not in body


def test_topology_proceed_with_topology_start_resets_host_info_cache():
    """Same revert as Blast — host_info is HOST-level."""
    body = _extract_method(SRC_TOPO, "_proceed_with_topology_start")
    assert "self._host_info_cache = {}" not in body
    assert "self._host_info_cache = None" not in body


# ───── #2: TOTAL line covers worker 0 too ────────────────────────────────


def test_blast_on_job_resp_captures_worker0_client_stats():
    """When the client side finishes, stash final_bw_avg_gbps and
    final_msg_rate_mpps on the dialog so _maybe_emit_total can
    include them in the sum."""
    body = _extract_method(SRC_BLAST, "_on_job_resp")
    assert "self._client_bw" in body
    assert "self._client_msgrate" in body
    assert "final_bw_avg_gbps" in body
    assert "final_msg_rate_mpps" in body


def test_blast_maybe_emit_total_includes_worker_zero():
    """The TOTAL line sums worker 0 + extras, not extras only."""
    body = _extract_method(SRC_BLAST, "_maybe_emit_total")
    assert "_client_bw" in body
    assert "_client_msgrate" in body
    # The "extras only" wording is gone.
    assert "extras only" not in body.lower()


def test_blast_on_job_resp_calls_maybe_emit_total():
    """Even when there are no extras, finishing worker 0 should
    still emit the TOTAL line so the operator sees one canonical
    summary."""
    body = _extract_method(SRC_BLAST, "_on_job_resp")
    assert "_maybe_emit_total" in body


# ───── #3: closeEvent resets _extra_workers + _total_emitted ─────────────


def test_blast_close_event_resets_extras():
    """v0.5.157 invariant: closeEvent resets per-run worker state.
    v0.5.159 dropped the _host_info_cache reset (Qt destroys the
    widget anyway, and the cache is host-level)."""
    body = _extract_method(SRC_BLAST, "closeEvent")
    assert "self._extra_workers = []" in body
    assert "self._total_emitted = False" in body


def test_blast_proceed_with_start_resets_total_guard():
    """Otherwise a 2nd Start in the same dialog session would
    early-return from _maybe_emit_total."""
    body = _extract_method(SRC_BLAST, "_proceed_with_start")
    assert "self._total_emitted = False" in body
    assert "self._client_bw = None" in body
    assert "self._client_msgrate = None" in body


def test_topology_close_event_resets_extras():
    """v0.5.159 dropped the _host_info_cache = {} reset for the
    same reason as Blast."""
    body = _extract_method(SRC_TOPO, "closeEvent")
    assert "_pair_extra_workers" in body
    assert "self._host_info_cache = {}" not in body


def test_topology_proceed_clears_pair_extras():
    body = _extract_method(SRC_TOPO, "_proceed_with_topology_start")
    assert "_pair_extra_workers" in body
    # Either .clear() or = {} — both are valid resets.
    assert ".clear()" in body or "= {}" in body


# ───── #4: cpu_pin clamp to cpu_count-1 ──────────────────────────────────


def test_blast_start_extras_clamps_cpu_pin():
    """No host should ever see taskset -c N for N >= cpu_count.
    When info has cpu_count, clamp every picked cpu down to
    cpu_count - 1."""
    body = _extract_method(SRC_BLAST, "_start_extra_workers")
    assert "cpu_count" in body
    assert "min(int(c), cpu_count - 1)" in body


def test_topology_start_pair_extras_clamps_cpu_pin():
    body = _extract_method(SRC_TOPO, "_start_pair_extra_workers")
    assert "cpu_count" in body
    assert "min(int(c), cpu_count - 1)" in body


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
