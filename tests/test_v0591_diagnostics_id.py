"""v0.5.91 — Diagnostics + ID batch.

Three new operator-facing features in the iface table:

1. **Driver / firmware version** — `ethtool -i` parsed once
   per iface (60s TTL) and surfaced as an inline `· fw <ver>`
   badge in the kernel-driver column. Hovering the badge
   shows full driver/version/firmware in a tooltip.

2. **Flash LED** (💡) — new per-row button. POSTs to
   `/api/admin/iface/<name>/flash` which spawns `ethtool -p
   <name> 5`. Operator can spot which physical cable belongs
   to which kernel iface name. Admin-gated.

3. **Click-row-to-expand drawer** (ℹ️) — fetches
   `/api/admin/iface/<name>/details` which collects link
   settings, drvinfo, feature flags, coalescing, ring,
   stats, and permanent address via separate `ethtool`
   commands. Rendered as a sibling row below the clicked
   one with one collapsible `<details>` block per section.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── _ethtool_drvinfo helper ───


def test_drvinfo_helper_defined():
    src = _src()
    assert "def _ethtool_drvinfo(" in src
    assert "_DRVINFO_CACHE" in src
    assert "_DRVINFO_CACHE_TTL" in src


def test_drvinfo_helper_parses_canonical_fields():
    src = _src()
    m = re.search(
        r"def _ethtool_drvinfo[\s\S]+?(?=\ndef _)",
        src,
    )
    assert m
    body = m.group(0)
    # Maps ethtool -i field names → canonical keys.
    assert '"driver"' in body
    assert '"firmware-version"' in body
    assert '"fw_version"' in body
    assert '"bus-info"' in body
    assert '"expansion-rom-version"' in body
    # 2-second subprocess timeout.
    assert "timeout=2" in body


def test_drvinfo_cache_ttl_60s():
    src = _src()
    m = re.search(r"_DRVINFO_CACHE_TTL\s*=\s*([0-9.]+)", src)
    assert m and float(m.group(1)) >= 30.0, (
        "TTL too low for static driver-info"
    )


def test_drvinfo_validates_iface_name_before_subprocess():
    src = _src()
    m = re.search(
        r"def _ethtool_drvinfo[\s\S]+?(?=\ndef _)",
        src,
    )
    body = m.group(0)
    assert "_RE_IFACE_NAME" in body, (
        "drvinfo helper doesn't validate iface name — could pass "
        "operator-supplied junk to subprocess"
    )


# ─── _ethtool_full_dump helper ───


def test_full_dump_helper_defined():
    src = _src()
    assert "def _ethtool_full_dump(" in src


def test_full_dump_covers_all_standard_sections():
    src = _src()
    m = re.search(
        r"def _ethtool_full_dump[\s\S]+?(?=\ndef _)",
        src,
    )
    assert m
    body = m.group(0)
    # Required ethtool views — operator's go-to debug set.
    for label in ("link", "drvinfo", "features", "coalesce",
                  "ring", "stats", "perm_addr"):
        assert f'"{label}"' in body, f"Missing section: {label}"
    # Each section has a 3s timeout.
    assert "timeout=3" in body


# ─── Iface response surface ───


def test_iface_response_includes_drvinfo():
    """The /api/dpdk/interfaces response must include `drvinfo`
    per row so the JS can render the firmware badge."""
    src = _src()
    assert '"drvinfo": _ethtool_drvinfo(iface)' in src


# ─── New endpoints ───


def test_flash_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/iface/<iface>/flash"' in src
    assert "def api_admin_iface_flash(" in src


def test_flash_endpoint_admin_gated():
    src = _src()
    m = re.search(
        r'@app\.route\("/api/admin/iface/<iface>/flash"[\s\S]'
        r'{0,300}?def api_admin_iface_flash',
        src,
    )
    assert m and "@require_role" in m.group(0)


def test_flash_endpoint_uses_ethtool_p():
    src = _src()
    m = re.search(
        r"def api_admin_iface_flash[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    assert '"ethtool"' in body and '"-p"' in body
    # Uses Popen so the API returns immediately rather than
    # blocking for the full 5-second blink.
    assert "subprocess.Popen" in body


def test_flash_endpoint_clamps_seconds_1_to_60():
    src = _src()
    m = re.search(
        r"def api_admin_iface_flash[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    # Operator-supplied `seconds` must be bounded.
    assert "max(1, min(60" in body


def test_details_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/iface/<iface>/details"' in src
    assert "def api_admin_iface_details(" in src


def test_details_endpoint_returns_drvinfo_and_sections():
    src = _src()
    m = re.search(
        r"def api_admin_iface_details[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    assert '"drvinfo": _ethtool_drvinfo(iface)' in body
    assert '"sections": _ethtool_full_dump(iface)' in body


# ─── JS surface ───


def test_iface_row_renders_flash_and_details_buttons():
    src = _src()
    assert 'data-iface-action="flash"' in src
    assert 'data-iface-action="details"' in src
    # And the existing up/down/reset stay alongside.
    assert 'data-iface-action="up"' in src
    assert 'data-iface-action="down"' in src
    assert 'data-iface-action="reset"' in src


def test_fw_badge_rendered_inline_in_kernel_driver_cell():
    src = _src()
    assert "fwBadge" in src
    assert "i.drvinfo && i.drvinfo.fw_version" in src
    # The badge appears in the kdrv cell.
    assert "${escapeHtml(kdrv)}${fwBadge}" in src


def test_details_drawer_handler_defined():
    src = _src()
    assert "ifaceDetailsToggle" in src
    # Lazy-fetches + caches per iface.
    assert "_ifaceDetailsCache" in src
    # Uses GET /api/admin/iface/<name>/details
    assert "/details" in src
    # Renders one <details> per section.
    assert "Link settings (ethtool)" in src
    assert "Driver info (ethtool -i)" in src
    assert "Driver statistics (ethtool -S)" in src


def test_flash_branch_handles_failure_with_toast():
    src = _src()
    idx = src.find("if (realAction === 'flash')")
    assert idx > 0
    body = src[idx:idx + 1500]
    assert "Flashing" in body and "LED" in body
    # Catches network errors.
    assert ".catch(" in body


# ─── Version ───


def test_pyproject_version_at_least_0591():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 91)
