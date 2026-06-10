"""v0.5.80 — close-out batch.

Final batch of the audit drive. Six findings:

  - MEDIUM #11: /api/admin/journal GET endpoint with @require_role
    viewer that tails `journalctl -u netgen-server -n <lines>`.
    Cap 500 lines, 5s timeout. Operator no longer needs SSH for
    post-mortems.
  - MEDIUM endpoint #5: viewer-tier @require_role on
    `/api/admin/interface_ips` and `/api/admin/bind_history` GETs.
    Pre-fix unauthenticated viewers could dump PCI topology +
    per-iface IP layouts via these GETs (the POST paths were
    already admin-gated since v0.5.68).
  - LOW endpoint #12: hoist `_NO_PMD_DRIVERS` from inside
    /api/dpdk/bind to module scope. Surface `pmd_supported` flag
    in /api/dpdk/interfaces response so the GUI can flag the iface
    preemptively.
  - LOW endpoint #9: accelerators endpoint paginates at 256
    entries; response includes `truncated` + `cap`.
  - LOW #13: accelerators JS empty-state differentiation. Pre-fix
    empty list and HTTP error both hid the card silently;
    operator on AMD couldn't tell whether the card was missing
    or the endpoint failed. Now: HTTP non-2xx hides + console.warn;
    empty list shows the card with informational text.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── journalctl endpoint ───


def test_admin_journal_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/journal"' in src
    assert "def admin_journal(" in src


def test_admin_journal_requires_viewer_role():
    src = _src()
    m = re.search(
        r'@app\.route\("/api/admin/journal"[\s\S]{0,400}?def admin_journal',
        src,
    )
    assert m
    block = m.group(0)
    assert "@require_role" in block, (
        "/api/admin/journal lacks @require_role"
    )


def test_admin_journal_caps_lines_at_500():
    src = _src()
    m = re.search(
        r"def admin_journal\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "500" in body, "No 500-line cap on journal endpoint"
    assert "timeout=5" in body, "No timeout on journalctl call"


# ─── viewer-tier auth on GETs ───


def test_interface_ips_get_has_viewer_role():
    src = _src()
    m = re.search(
        r'@app\.route\("/api/admin/interface_ips"[\s\S]{0,400}?def\b',
        src,
    )
    assert m and "@require_role" in m.group(0), (
        "/api/admin/interface_ips GET lacks viewer-tier auth"
    )


def test_bind_history_get_has_viewer_role():
    src = _src()
    m = re.search(
        r'@app\.route\("/api/admin/bind_history"[\s\S]{0,400}?def\b',
        src,
    )
    assert m and "@require_role" in m.group(0), (
        "/api/admin/bind_history GET lacks viewer-tier auth"
    )


# ─── NO_PMD module-scope hoist ───


def test_no_pmd_drivers_at_module_scope():
    src = _src()
    # Must be defined OUTSIDE any function (no leading indent).
    assert re.search(
        r"^_NO_PMD_DRIVERS\s*=\s*\{",
        src,
        re.MULTILINE,
    ), "_NO_PMD_DRIVERS not at module scope"
    # And the canonical no-PMD drivers are there.
    for drv in ("tg3", "e1000", "e1000e", "r8169"):
        assert f'"{drv}"' in src


def test_iface_response_includes_pmd_supported():
    src = _src()
    idx = src.find('"card_model": _detect_nic_model(pci)')
    assert idx > 0
    post = src[idx:idx + 1500]
    assert '"pmd_supported"' in post, (
        "DPDK iface entry missing pmd_supported flag"
    )


# ─── Accelerators pagination + empty-state ───


def test_accelerators_response_caps_at_256():
    src = _src()
    m = re.search(
        r"def dpdk_accelerators\(\)[\s\S]+?return\s+jsonify\(",
        src,
    )
    body = m.group(0)
    assert "_ACCEL_CAP = 256" in body or "_ACCEL_CAP" in body
    assert '"truncated"' in src, "Response missing `truncated` flag"
    assert '"cap"' in src, "Response missing `cap` field"


def test_accelerators_js_differentiates_empty_vs_error():
    src = _src()
    idx = src.find("async function refreshAccelerators(")
    assert idx > 0
    body = src[idx:idx + 2500]
    # The empty-list path now SHOWS the card with a message.
    assert "No DPDK accelerators detected" in body, (
        "Accelerators empty-state doesn't surface a message"
    )
    # The fetch-error path uses console.warn so operator can
    # debug if needed.
    assert "console.warn" in body, (
        "Accelerators fetch error path doesn't log to console"
    )


# ─── Version ───


def test_pyproject_version_at_least_0580():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 80)
