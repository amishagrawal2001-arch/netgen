"""v0.5.65 — LOW polish: operstate casing parity, visibility-
aware polling, iface table overflow, accessible pill glyphs.

From the original audit LOW list:

* `/api/admin/interface_ips` returned `UP/DOWN/UNKNOWN`
  uppercase. v0.5.43 standardised `/api/interfaces` on
  lowercase. Clients comparing case-sensitively broke against
  one or the other.

* `setInterval(refreshHealth, 30000)` kept polling even when
  the admin tab was backgrounded. Wasteful on battery, and on
  busy operators' workstations the background polling collided
  with foreground tabs' fetches.

* iface table is 8 columns wide; on a 13" laptop split-screen
  the action button column got squeezed and `Bind to DPDK` /
  `Unbind` wrapped or pushed off-screen.

* `pill` was color-only — deuteranopic operators couldn't
  visually distinguish ok / warn / bad. No glyph prefix, no
  aria-label.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_interface_ips_operstate_lowercase():
    """The /api/admin/interface_ips response must lowercase the
    operstate to match /api/interfaces."""
    src = _src()
    # Locate the operstate assignment.
    m = re.search(
        r"out\[name\]\s*=\s*\{[\s\S]+?[\"']operstate[\"']\s*:\s*([^,\n]+)",
        src,
    )
    assert m, "interface_ips operstate write not located"
    expr = m.group(1).strip().rstrip(",}")
    assert ".lower()" in expr, (
        "operstate not lowercased — clients comparing across "
        "endpoints still break"
    )


def test_refresh_health_setinterval_checks_visibility():
    """The setInterval callback must guard with
    document.visibilityState === 'visible'."""
    src = _src()
    m = re.search(
        r"setInterval\(\(\)\s*=>\s*\{[\s\S]{0,200}?refreshHealth",
        src,
    )
    assert m, "setInterval(refreshHealth, ...) wrap with arrow not located"
    body = m.group(0)
    assert "visibilityState" in body, (
        "Polling doesn't check document.visibilityState — burns "
        "battery on backgrounded tabs"
    )


def test_visibilitychange_resumes_immediately():
    """When the tab becomes visible again, refreshHealth should
    fire immediately instead of waiting up to 30s for the next
    interval tick."""
    src = _src()
    assert re.search(
        r"addEventListener\([\"']visibilitychange[\"'][\s\S]{0,200}?refreshHealth",
        src,
    ), (
        "No visibilitychange listener — operator switching back "
        "to admin tab waits up to 30s for stale data to refresh"
    )


def test_iface_table_wrapped_in_overflow_div():
    """The iface table must sit inside a `<div
    style="overflow-x:auto">` so narrow viewports don't squeeze
    columns."""
    src = _src()
    assert re.search(
        r'overflow-x:auto[\s\S]{0,100}?<table class="iface"',
        src,
    ), (
        "Iface table not wrapped in overflow-x:auto — narrow "
        "viewports lose the action column"
    )


def test_pill_label_includes_glyph_prefix():
    """The pill rendering must prefix `✓`, `✗`, or `!` based on
    pillClass so color-blind operators can distinguish states."""
    src = _src()
    # Match the glyph mapping directly — substring check is
    # simpler than a regex with mixed quote escapes here.
    assert "ok:'✓ '" in src and "bad:'✗ '" in src, (
        "Pill rendering doesn't include glyph prefix — pure-"
        "color distinction breaks for deuteranopic operators"
    )


def test_pill_includes_aria_label():
    """The pill must have an aria-label so screen readers get
    the state description, not just the visual."""
    src = _src()
    # Match the pill template literal with aria-label attribute.
    assert re.search(
        r'<span class="pill[^"]*"[^>]+aria-label=',
        src,
    ), "Pill missing aria-label attribute"


def test_pyproject_version_at_least_0565():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 65), (
        f"Version {m.group(1)} < 0.5.65"
    )
