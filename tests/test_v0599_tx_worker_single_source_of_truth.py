"""v0.5.99 (post-tag, follow-up to install-path fix): refactor
every tx_worker presence check to delegate to the launcher's
`_resolve_tx_worker_bin()`.

Operator on srv06 (Jun 11 2026): asked that the DPDK Runtime
tile track the missing-binary case the same way the launcher
does, so a discrepancy can never silently mask a real bug
again. Implementation: single source of truth.

Pre-fix the codebase had FOUR independent candidate lists:
  1. /api/dpdk/status         (run_tgen_server.py ~L12887)
  2. dpdk verify endpoint     (run_tgen_server.py ~L13855)
  3. /api/admin/health        (run_tgen_server.py ~L16919)
  4. _resolve_tx_worker_bin() (utils/dpdk_tx_worker.py:798)

Lists 1+2+3 had `/usr/local/bin/tx_worker`, list 4 did not. On
srv06 (install_dpdk.sh Step 6 installs to /usr/local/bin/) the
admin console reported tx_worker present while every actual
launch errored 'binary not found'. The operator hit it head-on.

Fix: lists 1, 2, 3 now call `_resolve_tx_worker_bin()`. If the
import fails (test contexts, partial installs), each falls
back to a small in-line list — but the production path always
matches the launcher's view of reality.

Also adds the resolver path inline next to the DPDK Runtime
tile so the operator can see WHERE the server thinks the binary
is without crawling the API.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── /api/dpdk/status delegates ───


def test_dpdk_status_delegates_to_resolver():
    src = _src()
    # Find the /api/dpdk/status path that touches tx_worker_exists.
    idx = src.find("tx_worker_exists = False")
    assert idx > 0, "tx_worker_exists block not found"
    body = src[idx:idx + 3000]
    assert "_resolve_tx_worker_bin" in body, (
        "/api/dpdk/status doesn't delegate to the launcher's "
        "resolver — drift risk"
    )


# ─── dpdk verify endpoint delegates ───


def test_dpdk_verify_delegates_to_resolver():
    src = _src()
    idx = src.find('messages.append(f"tx_worker found:')
    if idx < 0:
        idx = src.find("tx_worker_binary = True")
    assert idx > 0
    # Walk a window AROUND the bind.
    window = src[max(0, idx - 1500):idx + 1500]
    assert "_resolve_tx_worker_bin" in window, (
        "DPDK verify endpoint doesn't delegate to the launcher's "
        "resolver"
    )


# ─── /api/admin/health delegates ───


def test_admin_health_delegates_to_resolver():
    src = _src()
    # Anchor on the assignment that sets out["tx_worker"] —
    # that's the presence-check site (post-refactor it calls
    # _resolve_tx_worker_bin inside its own try/except block).
    idx = src.find('out["tx_worker"] = {"present": True')
    assert idx > 0, "presence-set call not found"
    window = src[max(0, idx - 1500):idx + 500]
    assert "_resolve_tx_worker_bin" in window, (
        "/api/admin/health doesn't delegate to the resolver"
    )


# ─── All three fallback branches still cover /usr/local/bin/ ───


def test_all_fallback_lists_include_usr_local_bin():
    """If the resolver import errors, each call site has a
    defensive in-line list. All three must still cover
    /usr/local/bin/tx_worker — that's the standard install
    target and where install_dpdk.sh Step 6 installs."""
    src = _src()
    # Count occurrences of the path inside `except Exception:`
    # / fallback blocks. There should be at least one per
    # delegating call site (3 sites).
    fallback_pattern = re.compile(
        r"except Exception:[\s\S]{0,500}?/usr/local/bin/tx_worker",
    )
    matches = fallback_pattern.findall(src)
    assert len(matches) >= 3, (
        f"Expected ≥3 defensive fallbacks covering /usr/local/bin/,"
        f" found {len(matches)}"
    )


# ─── DPDK Runtime tile shows the resolver path inline ───


def test_dpdk_runtime_tile_surfaces_resolver_path():
    src = _src()
    assert "txw-path-hint" in src, (
        "DPDK Runtime tile doesn't render the resolver path inline"
    )
    # And on failure, points at install_dpdk.sh / TX_WORKER_BIN.
    assert "not on any resolver path" in src, (
        "Missing failure-state hint that the binary isn't on the "
        "resolver path"
    )
    assert "TX_WORKER_BIN" in src, (
        "Missing env-override pointer in the operator-facing hint"
    )
