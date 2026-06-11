"""v0.5.93 — audit batch 2: cache + race fixes.

From the v0.5.91 admin-console audit:

  H2 — LLDP cache `_LLDP_CACHE["ts"] = now;
        _LLDP_CACHE["by_iface"] = {}` ran BEFORE the lldpcli
        subprocess. On TimeoutExpired / non-zero rc, the cache
        wiped its prior good neighbor data AND blanked the
        result dict for 30 seconds. Operator saw "(no LLDP)"
        for every row after one transient blip — the exact
        v0.5.87-style failure mode.

  H3 — `_ADMIN_INSTALL_RDMA_STATE` check-then-spawn was OUTSIDE
        `_ADMIN_INSTALL_LOCK`. v0.5.71 fixed exactly this
        pattern for install_dpdk; install_rdma was missed.
        Two concurrent POSTs could both observe `proc is None`
        and both Popen — first wins state, second orphaned.

  M3 — Stale `_ETHTOOL_CACHE` + `_DRVINFO_CACHE` after iface
        lifecycle. Operator clicked Down, the 700ms refresh
        kept showing carrier=true + the pre-down speed for up
        to 30s (ETHTOOL_TTL) or 60s (DRVINFO_TTL).

  M4 — JS `_ifaceDetailsCache` had no TTL and no invalidation.
        Once an operator opened the ℹ️ drawer for an iface and
        then brought the link down, every subsequent open still
        served the cached `Link detected: yes`.

  M5 — `_ADMIN_UPGRADE_STATE` mutated from multiple sites
        without a lock. Same shape as install_dpdk's pre-v0.5.71
        bug; missed for the upgrade path.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── H2: LLDP cache fresh-before-success ───


def test_lldp_cache_does_not_blank_before_subprocess():
    """The cache must NOT set `by_iface = {}` before the
    subprocess. Build into a local + commit on success."""
    src = _src()
    # Capture the whole function — there are multiple `return
    # _LLDP_CACHE["by_iface"]` statements (early cache-hit + a
    # mid-function bailout); anchor on the next top-level def.
    m = re.search(
        r"def _refresh_lldp_cache[\s\S]+?(?=\n\ndef [a-z_]|\nclass )",
        src,
    )
    assert m
    body = m.group(0)
    # The builder dict.
    assert "new_by_iface = {}" in body, (
        "LLDP refresh should build into a local `new_by_iface`"
    )
    # Commit happens only on success.
    assert '_LLDP_CACHE["by_iface"] = new_by_iface' in body
    # And the old pre-fix blank-then-fill is gone.
    assert '_LLDP_CACHE["by_iface"] = {}' not in body, (
        "LLDP refresh still has the pre-fix blank-then-fill — "
        "would erase good data on transient lldpcli failure"
    )


def test_lldp_cache_bumps_ts_on_failure_to_avoid_hammering():
    src = _src()
    # Capture the whole function — there are multiple `return
    # _LLDP_CACHE["by_iface"]` statements (early cache-hit + a
    # mid-function bailout); anchor on the next top-level def.
    m = re.search(
        r"def _refresh_lldp_cache[\s\S]+?(?=\n\ndef [a-z_]|\nclass )",
        src,
    )
    body = m.group(0)
    # On TimeoutExpired we still bump ts so we don't re-spawn
    # lldpcli on every refresh, but leave by_iface intact.
    assert "TimeoutExpired" in body
    # ts gets bumped in success path + failure path (multiple
    # spots).
    assert body.count('_LLDP_CACHE["ts"]') >= 3


# ─── H3: install_rdma lock coverage ───


def test_install_rdma_check_then_spawn_is_locked():
    src = _src()
    m = re.search(
        r"def api_admin_install_rdma\([\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    assert m
    body = m.group(0)
    # The Popen + state mutation must happen inside the lock.
    assert "with _ADMIN_INSTALL_LOCK:" in body
    # Re-check before Popen (defense in depth).
    assert "Defense in depth" in body or "_existing" in body


# ─── M3: cache invalidation helper ───


def test_invalidate_iface_caches_helper_defined():
    src = _src()
    assert "def _invalidate_iface_caches(" in src
    m = re.search(
        r"def _invalidate_iface_caches[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_ETHTOOL_CACHE" in body
    assert "_DRVINFO_CACHE" in body
    assert "with _ETHTOOL_LOCK:" in body
    assert "with _DRVINFO_LOCK:" in body


def test_iface_up_down_reset_call_cache_invalidate():
    src = _src()
    for verb in ("up", "down", "reset"):
        m = re.search(
            rf"def api_admin_iface_{verb}\(iface\)"
            rf"[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
            src,
        )
        assert m, f"Handler for {verb} not found"
        body = m.group(0)
        assert "_invalidate_iface_caches(iface)" in body, (
            f"api_admin_iface_{verb} doesn't drop the cache — "
            f"operator sees stale link state for up to 30-60s"
        )


# ─── M4: JS details cache TTL + invalidation ───


def test_iface_details_cache_has_ttl():
    src = _src()
    assert "_IFACE_DETAILS_TTL_MS" in src, (
        "JS details cache has no TTL — stale forever once "
        "opened"
    )
    # Default TTL must be a small positive number.
    m = re.search(r"_IFACE_DETAILS_TTL_MS\s*=\s*(\d+)", src)
    assert m
    ttl = int(m.group(1))
    assert 5000 <= ttl <= 60000, (
        f"TTL {ttl}ms is too short or too long"
    )


def test_iface_details_cache_invalidated_after_lifecycle():
    src = _src()
    assert "_invalidateIfaceDetails" in src
    # Called from the success path of ifaceLifecycle.
    idx = src.find("async function ifaceLifecycle(")
    assert idx > 0
    body = src[idx:idx + 3000]
    assert "_invalidateIfaceDetails(iface.name)" in body, (
        "Lifecycle handler doesn't invalidate the drawer "
        "cache — stale data shown on re-open"
    )


# ─── M5: upgrade state lock ───


def test_admin_upgrade_lock_defined():
    src = _src()
    assert "_ADMIN_UPGRADE_LOCK" in src
    assert re.search(
        r"_ADMIN_UPGRADE_LOCK\s*=\s*threading\.Lock\(\)",
        src,
    )


def test_upgrade_wheel_handler_uses_lock():
    src = _src()
    m = re.search(
        r"def api_admin_upgrade_wheel\([\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    assert m
    body = m.group(0)
    # Lock wraps both the check + the Popen+state-update.
    assert body.count("with _ADMIN_UPGRADE_LOCK:") >= 2, (
        "upgrade_wheel handler doesn't lock both the initial "
        "check and the Popen/state mutation"
    )


# ─── Version ───


def test_pyproject_version_at_least_0593():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 93)
