"""v0.5.158 Slice C: dead-alias cleanup + fallback warning surface.

Operator: "go A first then B, and then C" — final slice.

Two items the Slice B audit flagged as polish:

  1. v0.5.153 left `_SameSubnetTrapConfirmDialog =
     _StartBlockerConfirmDialog` as a back-compat alias. Nothing
     else in the codebase imported the old name; drop it so a new
     reader doesn't think the alias is load-bearing.

  2. When `_host_info_cache` is empty OR `pick_workers_for_hca`
     can't find the HCA in the NUMA map, the worker spawn loop
     silently fell back to `list(range(N))` — cross-NUMA RAM
     access caps aggregate BW and operators get no signal. Now
     both Blast and Topology surface a `[workers] ⚠ no host_info
     cached …` (or per-pair equivalent) line to `_stats_view`.
"""
from __future__ import annotations

import sys
from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOPO = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


# ───── #1: dead alias gone ───────────────────────────────────────────────


def test_dead_alias_removed():
    """v0.5.153 added an alias; v0.5.158 drops it (no callers)."""
    assert "_SameSubnetTrapConfirmDialog = _StartBlockerConfirmDialog" \
        not in SRC_BLAST
    assert "_SameSubnetTrapConfirmDialog =" not in SRC_BLAST


def test_canonical_class_still_present():
    """Drop the alias, not the real class."""
    assert "class _StartBlockerConfirmDialog(" in SRC_BLAST


# ───── #2: host_info fallback warning surfaced ───────────────────────────


def test_blast_emits_fallback_warning_when_no_host_info():
    body = _extract_method(SRC_BLAST, "_start_extra_workers")
    assert "fallback_reason" in body
    # No-cache branch.
    assert "no host_info cached" in body
    # Surface goes to the visible stats view (matches all the other
    # operator-facing chunks).
    assert "_stats_view.append" in body
    assert "[workers] ⚠" in body


def test_blast_emits_fallback_warning_when_hca_not_in_numa_map():
    body = _extract_method(SRC_BLAST, "_start_extra_workers")
    # The HCA-not-found branch leaves numa_pin=None.
    assert "not in host's NUMA map" in body


def test_topology_emits_per_pair_fallback_warning():
    """v0.5.159: the warning routes through _set_status_error
    (Topology has no _stats_view; the v0.5.158 .append() would
    AttributeError). The fallback_reason strings + per-pair
    prefix still live in the method body."""
    body = _extract_method(SRC_TOPO, "_start_pair_extra_workers")
    assert "fallback_reason" in body
    # Per-pair labeling.
    assert "[pair #" in body or "pair #" in body
    assert "no host_info cached" in body
    assert "not in host's NUMA map" in body
    # The .append path is gone; warnings go through the status
    # label instead.
    assert "_set_status_error" in body


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
