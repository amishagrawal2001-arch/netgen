"""v0.5.402 — client startup speed fix (user-reported).

  S1  _FetchIfacesWorker uses quick_get (drops 7 s → 2 s per unreachable server).
  S2  _RetryWorker uses quick_get too.
  S3  (docs only — verified per-server workers already parallel-spawn)
  S4  Preserve session-cached online:True during initial render (no red flash).
  S5  Log per-server probe wall time.
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


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


# ─── S1: _FetchIfacesWorker uses quick_get ───


def test_s1_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.402 (audit startup S1" in src


def test_s1_fetch_ifaces_worker_uses_quick_get():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("class _FetchIfacesWorker(QThread):")
    _end = _idx + 4500
    body = src[_idx:_end]
    # quick_get with hasattr guard
    assert 'hasattr(self._conn_mgr, "quick_get")' in body
    assert "self._conn_mgr.quick_get(" in body
    # The old retry-adapter call is gone from the code (comment
    # mentions are OK).
    _stripped = _strip_comments(body)
    assert "self._conn_mgr.get(" not in _stripped, (
        "_FetchIfacesWorker still calls the retry-adapter get() — "
        "S1 regression"
    )


def test_s1_logs_probe_wall_time():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("class _FetchIfacesWorker(QThread):")
    body = src[_idx:_idx + 4500]
    # S5 requirement folded into this worker too
    assert "_time.monotonic()" in body
    assert "[SERVER TREE] Probe" in body


# ─── S2: _RetryWorker uses quick_get ───


def test_s2_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.402 (audit startup S2)" in src


def test_s2_retry_worker_uses_quick_get():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.402 (audit startup S2)")
    body = src[_idx:_idx + 2500]
    assert "self._conn_mgr.quick_get(" in body
    assert "[SERVER TREE] Retry-probe" in body


# ─── S4: preserve online state ───


def test_s4_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.402 (audit startup S4)" in src


def test_s4_does_not_flip_online_to_false():
    """Pre-fix, the else branch of the interface-cache check
    unconditionally wrote `server["online"] = False`. Fix
    preserves whatever `server["online"]` already holds."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.402 (audit startup S4)")
    body = _strip_comments(src[_idx:_idx + 2000])
    # No blanket "server['online'] = False" in this block
    assert 'server["online"] = False' not in body
    assert "if \"online\" not in server:" in body


# ─── S5: log wall time ───


def test_s5_marker_present():
    """S5 log line already asserted in _s1_ and _s2_ tests; also
    require the log messages differ so ops can spot which worker
    fired."""
    src = _read("traffic_client/server_section.py")
    assert "[SERVER TREE] Probe" in src
    assert "[SERVER TREE] Retry-probe" in src


# ─── ConnectionManager sanity ───


def test_connection_manager_quick_get_bypasses_retry_adapter():
    """quick_get must NOT go through self.session (retry adapter);
    it must call requests.get directly."""
    src = _read("traffic_client/server_retry_workers.py")
    _idx = src.index("def quick_get(self")
    _end = src.index("def post(self", _idx)
    body = src[_idx:_end]
    assert "return requests.get(url, timeout=timeout" in body
    # Explicitly documents the bypass
    assert "no mounted adapter" in body.lower() or "bypass" in body.lower()


# ─── version guard ───


def test_pyproject_at_least_0602():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 402)


# ─── regression guards ───


def test_v0401_import_devices_async_intact():
    src = _read("traffic_client/menu_actions.py")
    assert "v0.5.401 (audit menu R1)" in src


def test_v0400_q1_aggregated_call_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q1)" in src
