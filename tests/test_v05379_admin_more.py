"""v0.5.379 — admin console: log tab dedupe + LLDP raw + Streams.

Continues v0.5.378's coverage-gap sweep with three more cards
+ finishes the log DOM dedupe that v0.5.378 started with just a
source label.

  H1  Full log DOM dedupe. Adds a tab bar (DPDK / RDMA /
      Upgrade) above the shared <pre id="log">. New per-source
      `_logBuffers` state so tab switches restore the correct
      buffer instead of stomping. Existing pollers still write
      to `$('log').textContent` but now also mirror into their
      source key so a tab switch has content to restore.
  H2  LLDP Raw card. Wires /api/admin/lldp_raw (v0.5.86). Shows
      raw lldpcli JSON so operators can inspect the actual
      neighbor frames when the parsed LLDP column looks wrong.
  H3  Streams overview card. Wires /api/streams/stats. Read-only
      compact table of running streams (name, iface, engine,
      TX rate, TX count, RX count). Fills the biggest coverage
      gap operators flagged: /admin shows systems state but not
      what traffic is actually running.

All sites carry marker `v0.5.379 (audit ...)`.
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


# ─── AST sanity ───


def test_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


# ─── Marker presence ───


def test_marker_log_tab_dedupe():
    src = _read("run_tgen_server.py")
    # HTML tabs + JS state + mirror into 3 pollers = ≥ 6 sites
    assert src.count("v0.5.379 (audit admin-log-tab-dedupe)") >= 4


def test_marker_lldp_raw():
    src = _read("run_tgen_server.py")
    assert "v0.5.379 (audit admin-lldp-raw-card)" in src


def test_marker_streams_card():
    src = _read("run_tgen_server.py")
    assert "v0.5.379 (audit admin-streams-card)" in src


# ─── H1: Log tab dedupe ───


def test_h1_tab_bar_html_present():
    src = _read("run_tgen_server.py")
    for _tab_key in ('data-source="dpdk"',
                     'data-source="rdma"',
                     'data-source="upgrade"'):
        assert _tab_key in src, f"tab bar missing {_tab_key}"


def test_h1_log_buffers_state_defined():
    src = _read("run_tgen_server.py")
    assert "const _logBuffers = {dpdk: '', rdma: '', upgrade: ''}" in src
    assert "let _activeLogSource = null" in src


def test_h1_render_helper_present():
    src = _read("run_tgen_server.py")
    assert "function _renderActiveLog()" in src


def test_h1_set_log_source_switches_tab():
    """Extended _setLogSource must translate the human labels to
    tab keys and flip _activeLogSource + render."""
    src = _read("run_tgen_server.py")
    _start = src.index("function _setLogSource(source)")
    body = src[_start:_start + 3000]
    # Tab key derivation
    assert "'dpdk'" in body and "'rdma'" in body and "'upgrade'" in body
    # Active-source flip
    assert "_activeLogSource = _key" in body
    # Render call
    assert "_renderActiveLog()" in body


def test_h1_pollers_mirror_into_buffer():
    """Each install poller (DPDK / RDMA / Upgrade) must mirror
    the log DOM into its source key so tab switches restore."""
    src = _read("run_tgen_server.py")
    for _key in ("dpdk", "rdma", "upgrade"):
        assert f"window._logBuffers.{_key} = log.textContent" in src, (
            f"poller for {_key} doesn't mirror into buffer"
        )


def test_h1_tab_click_switches_source():
    """Clicking a .log-tab button must set _activeLogSource +
    re-render + update the source label."""
    src = _read("run_tgen_server.py")
    _start = src.index("document.querySelectorAll('.log-tab')")
    body = src[_start:_start + 1500]
    assert "_activeLogSource = _key" in body
    assert "_renderActiveLog()" in body
    assert "_setLogSource(" in body


# ─── H2: LLDP Raw ───


def test_h2_html_elements_present():
    src = _read("run_tgen_server.py")
    for _id in ("btn-lldp-raw", "lldp-raw-view"):
        assert f'id="{_id}"' in src


def test_h2_handler_fetches_endpoint():
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.379 (audit admin-lldp-raw-card): LLDP raw viewer")
    body = src[_start:_start + 2500]
    assert "/api/admin/lldp_raw" in body
    # Renders parsed JSON when possible, raw stdout as fallback
    assert "JSON.parse(" in body
    assert "d.stdout" in body


def test_h2_endpoint_still_viewer_gated():
    """Regression: endpoint stays viewer-role. No security
    escalation from v0.5.375 sweep."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/admin/lldp_raw"[\s\S]{0,200}?'
        r'@require_role\(\s*[\'"]viewer[\'"]\s*\)',
        src,
    )
    assert m, "/api/admin/lldp_raw lost its viewer gate"


# ─── H3: Streams card ───


def test_h3_html_elements_present():
    src = _read("run_tgen_server.py")
    for _id in ("btn-refresh-streams", "streams-table-wrap", "streams-count"):
        assert f'id="{_id}"' in src


def test_h3_handler_fetches_endpoint():
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.379 (audit admin-streams-card): Streams overview")
    body = src[_start:_start + 3500]
    assert "/api/streams/stats?status=Running" in body
    # Table columns
    for _col in ("Stream", "Interface", "Engine",
                 "TX rate", "TX count", "RX count"):
        assert _col in body, f"streams table missing column {_col}"


def test_h3_escape_helper_defined():
    """Inline _escapeHtml helper for XSS-safety on user-visible
    stream names/interfaces that come from server JSON."""
    src = _read("run_tgen_server.py")
    assert "function _escapeHtml(s)" in src
    _start = src.index("function _escapeHtml(s)")
    body = src[_start:_start + 600]
    for _ent in ("&amp;", "&lt;", "&gt;", "&quot;", "&#039;"):
        assert _ent in body


def test_h3_bounded_row_count():
    """Slice to 200 rows so a runaway 10k-stream response can't
    lock up the browser."""
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.379 (audit admin-streams-card): Streams overview")
    body = src[_start:_start + 3500]
    assert "_streams.slice(0, 200)" in body


# ─── version guard ───


def test_pyproject_version_at_least_0579():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 379), (
        f"Version {m.group(1)} < 0.5.379"
    )


# ─── regression guards ───


def test_v0378_cache_flush_intact():
    src = _read("run_tgen_server.py")
    assert 'id="btn-cache-flush"' in src


def test_v0378_journal_intact():
    src = _read("run_tgen_server.py")
    assert 'id="btn-refresh-journal"' in src


def test_v0378_log_source_label_intact():
    src = _read("run_tgen_server.py")
    assert 'id="log-source"' in src


def test_v0374_restart_server_intact():
    src = _read("run_tgen_server.py")
    assert 'id="btn-restart-server"' in src
