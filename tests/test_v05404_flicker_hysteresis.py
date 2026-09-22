"""v0.5.404 — stream status flicker follow-up (user-reported).

  U1  update_per_stream_statistics 'absent from stat_map' defaults to no-change.
  U2  _stopped_confirm_count hysteresis (threshold = 3).
  U3  _refresh_stream_status_in_place reads the same hysteresis cache.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


def _strip_comments(src: str) -> str:
    _no_triple = re.sub(r'"""[\s\S]*?"""', '', src)
    _no_triple = re.sub(r"'''[\s\S]*?'''", '', _no_triple)
    return "\n".join(
        _line for _line in _no_triple.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


# ─── U1 + U2 markers + implementation ───


def test_u1_u2_markers_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.404 (audit stats-U1 + U2)" in src


def test_u2_hysteresis_state_and_threshold():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 6000]
    # Per-sid confirm cache
    assert "_stopped_confirm_count" in body
    # Threshold = 3
    assert "_STOPPED_CONFIRM_THRESHOLD = 3" in body
    # Both explicit-stopped and absent-from-stat_map branches
    # use the same _accumulate_stopped_signal gate.
    assert "def _accumulate_stopped_signal" in body
    assert "if _accumulate_stopped_signal():" in body


def test_u2_paint_green_resets_confirm_count():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _paint_green")
    _end = _idx + 500
    body = src[_idx:_end]
    # Green paint drops the sid from the confirm dict
    assert "_confirms.pop(_sid, None)" in body


def test_u1_absent_from_stat_map_no_default_red_flip():
    """The old 'absent from stat_map' else branch unconditionally
    painted red. New branch only paints red if confirm-count says so."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 6000]
    # The gated-flip pattern (accumulate then check)
    assert "new_status = old_status" in body
    # Debug log documents the suppression
    assert "suppressing red flip" in body


# ─── U3: in-place refresh reads confirm cache ───


def test_u3_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.404 (audit stats-U3)" in src


def test_u3_refresh_in_place_gates_on_confirm_count():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.404 (audit stats-U3)")
    body = src[_idx:_idx + 3500]
    assert '_confirms_ro = getattr(self, "_stopped_confirm_count", None)' in body
    assert "_STOPPED_CONFIRM_THRESHOLD_RO = 3" in body
    # Gate: refuse to flip green→red unless threshold hit.
    # v0.5.405 W4 wrapped this condition across multiple lines +
    # added a pin-window exception, so check for the key parts
    # instead of the exact one-liner.
    assert 'color == "red"' in body
    assert 'pushed.get(sid) in ("green", "blue")' in body
    assert '_STOPPED_CONFIRM_THRESHOLD_RO' in body


# ─── version guard ───


def test_pyproject_at_least_0604():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 404)


# ─── regression guards ───


def test_v0403_bgp_t2_scanner_intact():
    src = _read("utils/bgp.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src


def test_v0402_quick_get_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.402 (audit startup S1" in src


def test_v0400_q1_aggregated_call_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q1)" in src


def test_v0393_j2_counter_advance_still_in_place():
    """The J2 counter-advance override must remain — U2 hysteresis
    is ADDITIVE to it, not a replacement."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.404 (audit stats-U1 + U2)")
    body = src[_idx:_idx + 6000]
    # J2 override branch still fires when counters advanced
    assert "if _counters_advanced:" in body
    assert "trusting" in body.lower() and "counters" in body.lower()
