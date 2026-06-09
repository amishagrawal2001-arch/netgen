"""v0.5.72 — admin UI polish + /api/admin/health worker-stall fix.

Audit findings M6, M8, M11, plus LOW items.

M6: _module_loaded ran subprocs (lsmod + modinfo) on every
admin/health call. 4 subprocs × up to 5s = 20s worst-case stall
on a contended box. Memoize for 10s. Also align the builtin
detection with /api/dpdk/verify's two-clause check.

M8: per-row Bind/Unbind handlers in the iface table used the
bare `Bind failed: ${data.message || ...}` toast — losing stderr
and output. Replace with toastFailDetailed (v0.5.64 helper).

M11: btn-refresh wasn't wrapped in withButtonBusy. Triple-clicks
on the Refresh button fired concurrent fetches that the in-flight
guard swallowed silently — but the button gave no feedback.

LOW: document.title is now `Netgen admin — <hostname>`; the
danger Configure IOMMU button gets a ⚠ glyph + aria-label.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_module_loaded_memoized():
    src = _src()
    health = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    ).group(0)
    # Cache attribute attached to the function.
    assert re.search(
        r"api_admin_health\._module_cache",
        health,
    ), (
        "_module_loaded doesn't attach a cache to the endpoint"
    )
    # TTL constant exists.
    assert re.search(
        r"_MODULE_CACHE_TTL\s*=\s*\d+",
        health,
    ), "No TTL constant on the module cache"


def test_module_loaded_uses_two_clause_builtin_check():
    """The improvement-class fix: builtin detection now mirrors
    /api/dpdk/verify's two clauses ((builtin) literal OR no
    `filename:` line). Pre-fix /api/admin/health disagreed with
    /api/dpdk/verify on builtin-vfio_pci kernels."""
    src = _src()
    health = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    ).group(0)
    assert re.search(
        r'"\(builtin\)"\s+in\s+mi\.stdout\s+or\s+"filename:"\s+not\s+in',
        health,
    ), (
        "_module_loaded doesn't use the two-clause builtin check; "
        "/api/admin/health will disagree with /api/dpdk/verify"
    )


def test_per_row_bind_handler_uses_toast_fail_detailed():
    src = _src()
    # The per-row handler is in the JS. Look for the click handler
    # context around 'Bind failed' or the toastFailDetailed call.
    # Pre-fix had `toast('Bind failed: ...')`; fix replaces with
    # toastFailDetailed(data, r.status).
    assert "Bind failed" not in src or src.count("Bind failed") < src.count("toastFailDetailed"), (
        "Bind handler still uses bare `Bind failed:` toast — "
        "stderr lost"
    )


def test_per_row_unbind_handler_uses_toast_fail_detailed():
    src = _src()
    assert "Unbind failed" not in src or src.count("Unbind failed") < src.count("toastFailDetailed"), (
        "Unbind handler still uses bare `Unbind failed:` toast"
    )


def test_btn_refresh_uses_with_button_busy():
    src = _src()
    # Find the btn-refresh handler and ensure it's wrapped.
    idx = src.find("$('btn-refresh').addEventListener(")
    assert idx >= 0
    body = src[idx:idx + 400]
    assert "withButtonBusy" in body, (
        "btn-refresh handler doesn't wrap in withButtonBusy — "
        "triple-clicks still race"
    )


def test_document_title_includes_hostname():
    src = _src()
    assert re.search(
        r"document\.title\s*=\s*`Netgen admin\s*—\s*\$\{d\.hostname\}`",
        src,
    ), (
        "document.title isn't updated to include hostname — "
        "multi-tab operators can't distinguish servers"
    )


def test_danger_button_has_glyph_and_aria_label():
    src = _src()
    # Find the Configure IOMMU button line.
    m = re.search(
        r'<button[^>]*id="btn-config-iommu"[^>]*>([^<]+)</button>',
        src,
    )
    assert m, "btn-config-iommu element not found"
    btn_html = m.group(0)
    assert "aria-label" in btn_html, (
        "Configure IOMMU button missing aria-label"
    )
    # Warning glyph in the visible text.
    assert "⚠" in m.group(1), (
        "Configure IOMMU button missing visible warning glyph"
    )


def test_pyproject_version_at_least_0572():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 72), (
        f"Version {m.group(1)} < 0.5.72"
    )
