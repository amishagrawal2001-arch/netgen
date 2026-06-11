"""v0.5.98 — audit batch 7: LOW polish (final).

Consolidates the remaining LOW items from the v0.5.91 admin-
console audit:

  L3 — Duplicate `@app.before_request` / `@app.after_request`
        hooks. v0.5.92 didn't touch them — every request was
        logged twice in journalctl. Deleted the newer pair
        that added [REQUEST DATA] (more noise than signal).

  M13 — `_ethtool_link_fallback`'s non-timeout error path was
        at `logging.debug` so real OSError / parse crashes
        never surfaced at default log level. Bumped to
        warning.

  UX polish:
    - Drawer "Failed:" used raw hex `#b91c1c`. Now `var(--bad)`.
    - Lifecycle button span gains `flex-wrap: wrap` so the 5
      glyphs drop to a 2nd line gracefully at narrow widths.
    - `refreshInterfaces()` skips the "Loading…" placeholder
      when content already exists. No more jarring 700ms-after-
      action flicker.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── L3: duplicate before_request hooks ───


def test_only_one_request_logger_pair_remains():
    src = _src()
    # The remaining pair is _log_request_info / _log_response_info.
    # The deleted pair was log_request_info / log_response_info
    # (no leading underscore).
    assert "def log_request_info(" not in src, (
        "Duplicate before_request hook still present"
    )
    assert "def log_response_info(" not in src, (
        "Duplicate after_request hook still present"
    )
    assert "def _log_request_info(" in src, (
        "Original underscore-prefixed hook was deleted"
    )


def test_request_data_noise_removed():
    src = _src()
    # The `[REQUEST DATA]` debug line was part of the deleted
    # duplicate. No remaining `logging.debug(f"[REQUEST DATA]`
    # call sites should fire it (a mention in a comment is OK).
    assert 'logging.debug(f"[REQUEST DATA]' not in src
    assert 'logging.debug("[REQUEST DATA]' not in src


# ─── M13: ETHTOOL warning level ───


def test_ethtool_non_timeout_logs_at_warning():
    src = _src()
    # Find the _ethtool_link_fallback function.
    m = re.search(
        r"def _ethtool_link_fallback[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert 'logging.warning(f"[ETHTOOL]' in body, (
        "ETHTOOL non-timeout errors still at debug level"
    )


# ─── UX polish ───


def test_drawer_failure_uses_css_var_for_bad_color():
    src = _src()
    # Pre-fix the drawer fetch-error branch used hex `#b91c1c`.
    # Post-fix it uses `var(--bad)`. Both are in this neighbour-
    # hood; check that the fetch-error td.innerHTML line uses
    # the css var.
    m = re.search(
        r'td\.innerHTML\s*=\s*`<div\s+style="color:\s*var\(--bad\)',
        src,
    )
    assert m, (
        "Drawer fetch-error branch still uses raw hex color"
    )


def test_lifecycle_buttons_wrap_on_narrow_viewport():
    src = _src()
    idx = src.find("let lifecycleBtn = ''")
    block = src[idx:idx + 3000]
    # The .iface-ctl span container should set flex-wrap so the
    # 5 glyph buttons can drop to a 2nd line.
    container_match = re.search(
        r'<span class="iface-ctl" style="([^"]+)"',
        block,
    )
    assert container_match
    style = container_match.group(1)
    assert "flex-wrap: wrap" in style, (
        "Lifecycle button group doesn't wrap at narrow widths"
    )


def test_refresh_interfaces_skips_loading_placeholder_when_populated():
    src = _src()
    idx = src.find("async function refreshInterfaces(")
    body = src[idx:idx + 3000]
    # The pre-fix unconditional `wrap.innerHTML = ...Loading...`
    # should now be guarded by a content check.
    assert "wrap.querySelector('.iface-empty')" in body or "wrap.firstChild" in body, (
        "Loading… still flashes on every refresh"
    )


# ─── Version ───


def test_pyproject_version_at_least_0598():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 98)
