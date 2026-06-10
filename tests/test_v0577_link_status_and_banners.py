"""v0.5.77 — link status in iface table + audit batch 2.

Operator on srv06 (Jun 9 2026):

  "also show the link status of interface on admin console,
   also check any further gaps and bugs in admin console"

Two audit fan-outs returned 27 findings. This release ships:

  1. Operator-explicit ask: link status (sysfs carrier + speed +
     duplex) per kernel-bound iface, rendered as a muted second
     line under the State pill so column count is unchanged.

  2. Audit HIGH #1: reboot_needed banner. /api/admin/health has
     reported reboot_needed since v0.5.69 but the JS never
     rendered it — after Configure IOMMU the operator saw a
     green toast and walked away thinking it took effect.

  3. Audit HIGH #2: install/upgrade-running indicator banner.
     install_running / rdma_install_running / upgrade_running
     are all in /api/admin/health since v0.5.69; pre-fix only
     install_running was consumed → operators clicking
     destructive actions mid-wheel-upgrade raced and lost.

  4. Audit endpoint #1 (HIGH TOCTOU): _ADMIN_INSTALL_LOCK around
     re-check + Popen + state-set in /api/admin/install_dpdk.
     Pre-fix two parallel POSTs both saw `proc.poll() is None`
     and both spawned — second overwrote first's state dict.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─────── Link status ───────


def test_get_link_info_helper_defined():
    src = _src()
    assert "def _get_link_info(" in src, (
        "_get_link_info() helper not defined"
    )
    # Reads sysfs, not ethtool.
    m = re.search(
        r"def _get_link_info\([\s\S]+?(?=\n\n\ndef [a-z_]|\n\n\n# )",
        src,
    )
    assert m
    body = m.group(0)
    assert "/sys/class/net/" in body
    assert "/carrier" in body
    assert "/speed" in body
    assert "/duplex" in body
    assert "subprocess.run(" not in body, (
        "_get_link_info shouldn't subprocess — sysfs only"
    )


def test_link_info_handles_down_link_speed_minus_one():
    """Kernel returns -1 for speed on a down link. Helper must
    map that to None (not surface -1 as a real speed)."""
    src = _src()
    m = re.search(
        r"def _get_link_info\([\s\S]+?(?=\n\n\ndef [a-z_]|\n\n\n# )",
        src,
    )
    body = m.group(0)
    # Either explicit `if _i > 0` guard or `if int(...) > 0`.
    assert re.search(r"_i\s*>\s*0|>\s*0\s+else\s+None", body), (
        "_get_link_info doesn't guard against -1 speed"
    )


def test_iface_response_includes_link_field():
    src = _src()
    idx = src.find('"card_model": _detect_nic_model(pci)')
    assert idx > 0
    post = src[idx:idx + 800]
    assert '"link":' in post, (
        "DPDK iface entry doesn't include `link` field"
    )
    assert "_get_link_info(iface)" in post


def test_admin_js_renders_link_under_state_pill():
    src = _src()
    # The JS must reference i.link and render carrier states.
    assert "i.link" in src, (
        "Admin JS doesn't read i.link from interfaces"
    )
    # Up + down + unknown branches all present.
    assert "link.carrier === true" in src
    assert "link.carrier === false" in src
    # Speed conversion: Mbps → Gb/s for >=1000.
    assert "1000" in src and "Gb/s" in src, (
        "JS doesn't convert Mbps to Gb/s for fast links"
    )
    # Glyph for visual scan.
    assert "↑" in src and "↓" in src, (
        "Missing link up/down glyphs"
    )


# ─────── Banner: reboot_needed ───────


def test_admin_js_renders_reboot_needed_banner():
    src = _src()
    # Banner element created dynamically.
    assert "banner-reboot" in src, (
        "No reboot banner element"
    )
    # Reads d.reboot_needed from /api/admin/health.
    assert re.search(
        r"d\.reboot_needed",
        src,
    ), "JS doesn't consume d.reboot_needed"
    # Surfaces the reasons list.
    assert "reboot_reasons" in src, (
        "JS doesn't render reboot_reasons"
    )
    # Banner has 'reboot required' visible text.
    assert "reboot required" in src.lower() or "Host reboot required" in src


# ─────── Banner: install/upgrade in progress ───────


def test_admin_js_renders_inflight_banner_for_three_flags():
    src = _src()
    # All three in-flight flags from /api/admin/health.
    assert "d.install_running" in src
    assert "d.rdma_install_running" in src, (
        "JS doesn't consume d.rdma_install_running"
    )
    assert "d.upgrade_running" in src, (
        "JS doesn't consume d.upgrade_running"
    )
    # Renders the inflight banner.
    assert "banner-inflight" in src
    # Mentions destructive blocking.
    assert "destructive actions are blocked" in src or \
           "in progress" in src.lower()


# ─────── Install lock (HIGH TOCTOU) ───────


def test_admin_install_lock_defined():
    src = _src()
    assert "_ADMIN_INSTALL_LOCK = threading.Lock()" in src, (
        "_ADMIN_INSTALL_LOCK not defined"
    )


def test_install_dpdk_holds_lock_around_spawn():
    """The check-then-set TOCTOU window between the early
    `proc.poll() is None` read and the Popen must be closed by a
    lock-held re-check + spawn + state-set."""
    src = _src()
    # Find install_dpdk function.
    m = re.search(
        r"def api_admin_install_dpdk\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    # `with _ADMIN_INSTALL_LOCK:` block must exist.
    assert "with _ADMIN_INSTALL_LOCK:" in body, (
        "install_dpdk doesn't take _ADMIN_INSTALL_LOCK around spawn"
    )
    # Inside the lock, re-check the state.
    assert re.search(
        r"with _ADMIN_INSTALL_LOCK:\s*\n[\s\S]{0,500}?_ADMIN_INSTALL_STATE\.get\([\"']process[\"']\)",
        body,
    ), "Lock doesn't wrap a re-check of _ADMIN_INSTALL_STATE"
    # Popen is INSIDE the lock context.
    assert re.search(
        r"with _ADMIN_INSTALL_LOCK:[\s\S]{0,1500}?subprocess\.Popen\(",
        body,
    ), "Popen isn't inside the lock context"
    # State set is INSIDE the lock context (must precede the
    # final `return jsonify(...)` AND be after the Popen).
    assert re.search(
        r'with _ADMIN_INSTALL_LOCK:[\s\S]{0,2000}?_ADMIN_INSTALL_STATE\[[\"\']process[\"\']\]\s*=\s*new_proc',
        body,
    ), "_ADMIN_INSTALL_STATE['process'] assignment not under lock"


# ─────── Version ───────


def test_pyproject_version_at_least_0577():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 77), (
        f"Version {m.group(1)} < 0.5.77"
    )
