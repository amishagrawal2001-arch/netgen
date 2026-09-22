"""v0.5.411 — Start All silent no-op (v0.5.410 trace-driven fix).

  BB1  start_all_streams valid_ports falls back to self.streams
       keys when the displayed_sids intersection came out empty.
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


def test_stream_logic_ast_parses():
    ast.parse(_read("traffic_client/stream_logic.py"))


def test_bb1_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.411 (audit stream-BB1)" in src


def test_bb1_fallback_to_streams_keys_when_valid_ports_empty():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("v0.5.411 (audit stream-BB1)")
    body = src[_idx:_idx + 2500]
    # The fallback populates valid_ports from self.streams keys.
    assert "if not valid_ports and getattr(self, \"streams\", None):" in body
    assert "valid_ports = set(self.streams.keys())" in body
    # And logs a warning so the trace shows the fallback fired.
    assert "displayed_sids/table mismatch" in body


def test_start_all_entry_diag_log_added():
    src = _read("traffic_client/stream_logic.py")
    assert "[STATE-START-ALL] ENTER" in src


# ─── version guard ───


def test_pyproject_at_least_0611():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 411)


# ─── regression guards ───


def test_v0410_indefinite_pin_intact():
    src = _read("traffic_client/statistics_section.py")
    assert '_GRACE_S = float("inf")' in src


def test_v0410_aa2_hand_off_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.410 (audit stream-AA2)" in src


def test_v0409_diag_intact():
    src = _read("traffic_client/stream_logic.py")
    assert "[STATE-STOP]" in src
