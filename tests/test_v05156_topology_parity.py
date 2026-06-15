"""v0.5.156 Slice A: Topology dialog gains Blast's v0.5.152-155
quality features.

Operator: "go A first then B, and then C"

Audit findings v0.5.155 surfaced two Topology blockers vs Blast:
  #1 — Topology fires perftest immediately without same-subnet
       / DOWN port / missing-IP detection. Operator hits QP→RTR
       with no recovery path.
  #2 — Topology has no Parallel workers / 🚀 Max BW. Operator
       can't push past single-CPU line rate.

v0.5.156 Slice A imports Blast's `_detect_start_blockers` +
`_StartBlockerConfirmDialog` for #1, and adds a per-pair
parallel-worker spinbox + Max BW button for #2.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


# ───── #1: auto-detect on Start ────────────────────────────────────────


def test_topology_imports_blocker_helpers_from_blast():
    """Reuse Blast's helpers — don't duplicate the detection /
    confirm-dialog logic."""
    assert "_detect_start_blockers" in SRC
    assert "_StartBlockerConfirmDialog" in SRC


def test_on_start_clicked_defers_when_same_host_pair_exists():
    """Same-host pair = trap risk. Defer perftest fire behind a
    probe + confirm."""
    body = _extract_method(SRC, "_on_start_clicked")
    assert "same_host_plans" in body
    assert "_topology_probe_then_start" in body
    # And skips the probe when no same-host pair OR ops already
    # applied fixes (e.g., via Pre-flight).
    assert "_preflight_state_ids" in body


def test_probe_method_polls_both_endpoints():
    """Probe SERVER and CLIENT of the sample pair in parallel."""
    body = _extract_method(SRC, "_topology_probe_then_start")
    assert "/api/rdma/probe" in body
    # Two-side probe loop.
    assert "for side, hca, port in" in body


def test_on_probe_complete_routes_to_confirm():
    """After both probes, run _detect_start_blockers → pop dialog
    when a blocker is detected → handle apply/continue/cancel/
    open_preflight."""
    body = _extract_method(SRC, "_topology_on_probe_complete")
    assert "_detect_start_blockers(" in body
    assert "_StartBlockerConfirmDialog(" in body
    assert '"apply"' in body or 'choice == "apply"' in body
    assert '"continue"' in body or 'choice == "continue"' in body
    assert '"open_preflight"' in body or 'choice == "open_preflight"' in body


def test_apply_path_validates_before_configure():
    """Same validate-first pattern as Blast v0.5.153."""
    body = _extract_method(SRC, "_topology_apply_test_ips_then_start")
    val_pos = body.index("/api/rdma/test_ifaces/validate")
    body.index("/api/rdma/test_ifaces/configure")
    # Validate kicked off first; configure runs in its success
    # callback.
    assert "_on_validated" in body
    assert "_on_applied" in body
    assert "i.get(\"severity\") == \"error\"" in body


def test_proceed_with_topology_start_extracted():
    """Original per-pair start logic moved into
    `_proceed_with_topology_start` so the auto-detect can defer
    it."""
    assert "def _proceed_with_topology_start(" in SRC


# ───── #2: Parallel workers + 🚀 Max BW ─────────────────────────────────


def test_parallel_workers_spinbox_added():
    assert "self._parallel_workers_spin = QSpinBox()" in SRC
    assert "self._parallel_workers_spin.setRange(1, 64)" in SRC


def test_max_bw_button_added():
    assert "🚀 Max BW" in SRC
    assert "self._max_bw_btn.clicked.connect(self._on_max_bw_clicked)" in SRC


def test_max_bw_divides_by_pair_count():
    """Topology's twist: each pair gets N workers, so the picker
    must divide NUMA-local CPU count by pair_count to avoid
    oversubscribing the host."""
    body = _extract_method(SRC, "_on_max_bw_clicked")
    assert "pair_count" in body
    assert "// _pair_count" in body or "/ _pair_count" in body


def test_max_bw_caches_host_info():
    """The Max BW button populates _host_info_cache so the worker
    spawn loop can read NUMA pinning without re-querying."""
    body = _extract_method(SRC, "_on_max_bw_clicked")
    assert "self._host_info_cache = data" in body


def test_on_client_started_fans_out_extras_when_wc_gt_1():
    """After each pair's worker 0 is up, fan out per-pair extras
    when Parallel workers > 1."""
    body = _extract_method(SRC, "_on_client_started")
    assert "_parallel_workers_spin.value()" in body
    assert "_start_pair_extra_workers" in body


def test_start_pair_extra_workers_method_exists():
    assert "def _start_pair_extra_workers(" in SRC


def test_extra_workers_get_cpu_pin_and_numa_pin():
    """Each extra worker pair passes cpu_pin (per-worker) +
    numa_pin (HCA's home node)."""
    body = _extract_method(SRC, "_start_pair_extra_workers")
    assert '"cpu_pin": cpu' in body
    assert '"numa_pin": numa_pin' in body


def test_extras_use_distinct_listen_ports():
    """Each pair worker gets `base_listen_port + worker_idx` so
    perftest's control TCP doesn't collide."""
    body = _extract_method(SRC, "_start_pair_extra_workers")
    assert "plan.base_listen_port + worker_idx" in body


def test_extras_use_unique_handshakes():
    """Per-pair, per-worker handshake_id so netgen's handshake
    broker pairs the right server with the right client."""
    body = _extract_method(SRC, "_start_pair_extra_workers")
    assert "uuid.uuid4().hex" in body
    # Includes worker_idx + pair_index for uniqueness.
    assert "pair_index" in body
    assert "worker_idx" in body


def test_extras_tracked_per_pair():
    """`_pair_extra_workers` dict keyed by pair_index so cleanup
    can iterate them later."""
    body = _extract_method(SRC, "_start_pair_extra_workers")
    assert "self._pair_extra_workers" in body
    assert "plan.pair_index" in body


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
