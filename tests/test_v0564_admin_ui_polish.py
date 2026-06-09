"""v0.5.64 — admin UI hugepages tri-state pill + detailed error
toast + per-button busy guard.

Audit findings M13 + M14 + M15.

M13: `$('p-hugepages').style.color = (d.hugepages.total === 0)
? 'var(--bad)' : 'var(--ink)'`. Pre-fix only red vs default.
When all pages are EXHAUSTED (free === 0 with total > 0,
classic sign of a leaked DPDK process or competing app), the
operator saw `1024 / 0` in normal black text and missed it.
Now: red (total=0) / orange (free=0) / ink (otherwise).

M14: failure toast: `'Failed: ' + (d.message || d.error ||
'unknown')`. Many endpoints surface their real error under
`output` or `stderr` (dpdk_bind.sh stderr, modprobe stderr).
Toast lost the actual reason. Fall through to `output` and
`stderr` before defaulting.

M15: `btn-load-modules` / `btn-config-iommu` / `btn-config-hp`
had no disable while in-flight — triple-click → three
concurrent installs. Wrap each handler in `withButtonBusy` that
disables on entry and re-enables in finally.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_hugepages_pill_is_tri_state():
    """The hugepages color must distinguish three states:
    total=0 → bad; free=0 (with total>0) → warn; otherwise → ink."""
    src = _src()
    # The set-color expression must reference both `total === 0`
    # AND `free === 0`.
    m = re.search(
        r"p-hugepages[\s\S]{0,80}?style\.color\s*=\s*\([\s\S]+?\);",
        src,
    )
    assert m, "p-hugepages color expression not located"
    expr = m.group(0)
    assert "total === 0" in expr, (
        "Tri-state missing total === 0 branch"
    )
    assert "free === 0" in expr, (
        "Tri-state missing free === 0 branch — exhausted state "
        "is invisible"
    )
    assert "var(--warn)" in expr, (
        "Free-exhausted branch doesn't use var(--warn) — operator "
        "can't visually distinguish from healthy"
    )


def test_toast_fail_helper_includes_output_and_stderr():
    """The shared error-toast helper must fall through to
    `output` and `stderr` when message+error are missing."""
    src = _src()
    m = re.search(
        r"function toastFailDetailed\([\s\S]+?\}\n",
        src,
    )
    assert m, "toastFailDetailed helper not defined"
    body = m.group(0)
    for field in ("message", "error", "output", "stderr"):
        assert f"d?.{field}" in body or f"d.{field}" in body, (
            f"toastFailDetailed doesn't surface .{field}"
        )


def test_with_button_busy_helper_defined():
    src = _src()
    assert "function withButtonBusy(" in src, (
        "withButtonBusy helper not defined"
    )


def test_with_button_busy_uses_finally_to_reset():
    """The helper must re-enable the button in `finally` — a
    thrown exception can't be allowed to leave the button stuck.
    Match the helper body broadly enough to include the finally
    branch (which sits after a try block that has its own
    closing brace)."""
    src = _src()
    # The helper is short (~6 lines). Take 400 chars after the
    # signature.
    idx = src.find("function withButtonBusy(")
    assert idx >= 0
    body = src[idx:idx + 400]
    assert "finally" in body, (
        "withButtonBusy doesn't reset disabled in finally"
    )
    assert "disabled = false" in body, (
        "withButtonBusy doesn't reset disabled at all"
    )


def _handler_body(src: str, btn_id: str) -> str:
    """Locate a button click handler and capture ~1500 chars so
    the full handler body (with nested withButtonBusy callback)
    is included. Bracket-counting regex is overkill here."""
    needle = f"$('{btn_id}').addEventListener("
    idx = src.find(needle)
    assert idx >= 0, f"{btn_id} handler not located"
    return src[idx:idx + 1500]


def test_load_modules_handler_uses_button_busy():
    src = _src()
    body = _handler_body(src, "btn-load-modules")
    assert "withButtonBusy" in body, (
        "btn-load-modules click handler doesn't wrap in "
        "withButtonBusy — triple-clicks still race"
    )


def test_config_iommu_handler_uses_button_busy():
    src = _src()
    body = _handler_body(src, "btn-config-iommu")
    assert "withButtonBusy" in body, (
        "btn-config-iommu handler not button-busy guarded"
    )


def test_config_hp_handler_uses_button_busy():
    src = _src()
    body = _handler_body(src, "btn-config-hp")
    assert "withButtonBusy" in body, (
        "btn-config-hp handler not button-busy guarded"
    )


def test_handlers_use_toast_fail_detailed():
    """All three button handlers must call toastFailDetailed on
    failure (instead of the bare `'Failed: ' + d.message ||
    d.error` form)."""
    src = _src()
    for btn in ("btn-load-modules", "btn-config-iommu", "btn-config-hp"):
        body = _handler_body(src, btn)
        assert "toastFailDetailed" in body, (
            f"{btn} handler doesn't use toastFailDetailed — error "
            f"details still get lost"
        )


def test_pyproject_version_at_least_0564():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 64), (
        f"Version {m.group(1)} < 0.5.64"
    )
