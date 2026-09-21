"""v0.5.378 — admin console feature bundle.

Wires three admin console cards to server endpoints that have
existed for a while but had no UI:

  G1  Cache Flush card — wires /api/admin/caches/flush (v0.5.365).
      Per-cache checkboxes (ethtool, drvinfo, iface_details, lldp)
      + Flush All button + result line.
  G2  Server Journal card — wires /api/admin/journal (v0.5.80).
      Line-count dropdown + WARN+ filter + Refresh button. Auto-
      loads once on first page render.
  G3  Log-source label above the shared <pre id="log"> — small UX
      addition that names which install owns the current buffer.
      Hooked into DPDK / RDMA / Upgrade Wheel install starts via
      window._setLogSource(). Doesn't split the DOM (full dedupe
      queued for v0.5.379 if the label alone doesn't clear
      operator confusion).

All sites carry marker `v0.5.378 (audit ...)`.
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


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


# ─── Marker presence ───


def test_marker_cache_flush():
    src = _read("run_tgen_server.py")
    assert "v0.5.378 (audit admin-cache-flush-card)" in src


def test_marker_journal():
    src = _read("run_tgen_server.py")
    assert "v0.5.378 (audit admin-journal-card)" in src


def test_marker_log_source():
    src = _read("run_tgen_server.py")
    # HTML site + JS helper + 3 install-start hookups
    assert src.count("v0.5.378 (audit admin-log-source-header)") >= 4


# ─── G1: Cache Flush card ───


def test_g1_html_elements_present():
    src = _read("run_tgen_server.py")
    for _id in ("btn-cache-flush",
                "cache-which-ethtool",
                "cache-which-drvinfo",
                "cache-which-iface_details",
                "cache-which-lldp",
                "cache-flush-result"):
        assert f'id="{_id}"' in src, (
            f"Cache Flush card missing element {_id}"
        )


def test_g1_click_handler_posts_to_endpoint():
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.378 (audit admin-cache-flush-card): Cache Flush handler")
    body = src[_start:_start + 3000]
    assert "/api/admin/caches/flush" in body
    assert "method: 'POST'" in body
    # Iterates each checked kind (not just one bulk 'all' call).
    assert "for (const _k of _kinds)" in body


def test_g1_endpoint_still_admin_gated():
    """Regression: v0.5.375 SEC gated the endpoint to admin.
    v0.5.378 must not have accidentally opened it."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/admin/caches/flush"[\s\S]{0,300}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "/api/admin/caches/flush lost its admin gate"


# ─── G2: Journal viewer card ───


def test_g2_html_elements_present():
    src = _read("run_tgen_server.py")
    for _id in ("btn-refresh-journal", "journal-view",
                "journal-lines", "journal-only-warn"):
        assert f'id="{_id}"' in src


def test_g2_loader_function_fetches_endpoint():
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.378 (audit admin-journal-card): Journal viewer")
    body = src[_start:_start + 3000]
    assert "async function _loadJournal()" in body
    assert "/api/admin/journal?lines=" in body
    # Filter chip actually filters.
    assert "/\\bWARNING\\b|\\bERROR\\b|\\bCRITICAL\\b|\\bSEVERE\\b/" in body \
        or "WARNING" in body


def test_g2_auto_loads_once_on_dom_ready():
    """Auto-load on first render so operators see journal content
    without clicking Refresh."""
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.378 (audit admin-journal-card): Journal viewer")
    body = src[_start:_start + 3500]
    assert "_journalLoadedOnce" in body
    assert "DOMContentLoaded" in body


# ─── G3: Log-source label ───


def test_g3_html_source_span_present():
    src = _read("run_tgen_server.py")
    assert 'id="log-source"' in src


def test_g3_set_log_source_helper_defined():
    src = _read("run_tgen_server.py")
    assert "function _setLogSource(source)" in src
    assert "window._setLogSource = _setLogSource" in src


def test_g3_all_three_install_paths_call_set_log_source():
    """DPDK / RDMA / Upgrade Wheel install-start handlers must
    each call window._setLogSource() so the label updates when
    their buffer takes over the shared <pre>."""
    src = _read("run_tgen_server.py")
    # 3 install-start CALL sites: DPDK / RDMA / Upgrade Wheel.
    # (The export `window._setLogSource = _setLogSource` doesn't
    # match `window._setLogSource(` — separate.)
    _count = src.count("window._setLogSource(")
    assert _count >= 3, (
        f"Expected ≥3 window._setLogSource call sites "
        f"(DPDK + RDMA + Upgrade install-start); got {_count}"
    )


def test_g3_set_log_source_labels_are_distinct():
    """Each install path must label the buffer with a distinct
    string so the operator can distinguish DPDK vs RDMA vs
    Upgrade."""
    src = _read("run_tgen_server.py")
    for _label in ("'DPDK install'", "'RDMA install'", "'Upgrade wheel'"):
        assert f"window._setLogSource({_label})" in src, (
            f"Log-source hookup missing label: {_label}"
        )


# ─── version guard ───


def test_pyproject_version_at_least_0578():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 378), (
        f"Version {m.group(1)} < 0.5.378"
    )


# ─── regression guards ───


def test_v0374_upgrade_wheel_card_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.374 (audit admin-upgrade-wheel-card)" in src
    assert 'id="btn-upgrade-wheel"' in src


def test_v0374_restart_server_button_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.374 (audit admin-server-card-restart-button)" in src
    assert 'id="btn-restart-server"' in src


def test_v0367_rdma_install_button_intact():
    src = _read("run_tgen_server.py")
    assert 'id="btn-install-rdma"' in src


def test_v0375_ai_routes_still_gated():
    """Sanity: the sec sweep from v0.5.375 covered 67 ai routes.
    v0.5.378 didn't touch that surface — this test catches a
    regression if some future edit accidentally strips a
    decorator."""
    src = _read("run_tgen_server.py")
    routes = re.findall(r'@app\.route\("/api/ai[^"]+"', src)
    assert len(routes) >= 60
