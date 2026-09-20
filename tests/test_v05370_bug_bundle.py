"""v0.5.370 — 8-bug bundle from install + admin-console audit.

Ships after v0.5.369 (install_rdma tolerance). Bundle picks the
verified bugs — TWO SEC + SIX correctness. Coverage-gap /
feature findings from the same audits are deferred to v0.5.371+
where they'll each get proper scoping.

Fixes:
    B1 — `loadHealth()` → `refreshHealth()` typo (v0.5.367
         regression). Silent because guarded by `typeof`; RDMA
         card didn't refresh after Install completed.
    B2 — SEC. /api/system/restart_service + /api/system/reboot
         gain @require_role("admin"). Pre-fix any authenticated
         caller (viewer token even) could restart/reboot.
    B3 — DPDK install TOCTOU. Hoisted early check + force-kill
         + RDMA-mutex check into _ADMIN_INSTALL_LOCK, mirroring
         RDMA's v0.5.93 shape.
    B4 — systemd-run transient unit names — replaced
         `int(time.time())` (1-sec resolution) with
         `_transient_unit_suffix()` (monotonic_ns + uuid4 tail)
         across 4 sites. Rapid retry won't collide on
         --collect'd unit any more.
    B5 — Fallback rc inference for the legacy+detached upgrade
         path. `systemd-run --no-block --collect` reaps unit on
         exit → `_systemd_unit_state` returns (False, None) →
         v0.5.368 restart branch didn't fire. New helper
         `_infer_upgrade_rc_from_log` reads the log tail for
         pip's "Successfully installed" line.
    B6 — netgen-upgrade tx_worker rebuild uses shutil.which()
         instead of `subprocess.run(["which", tool])`. `which`
         binary missing on minimal Ubuntu images = false-
         positive as "meson missing" = tx_worker rebuild
         skipped = v0.5.102 stale-binary bug re-emerges.
    B7 — SEC. /api/admin/upgrade_wheel/log bumped from viewer
         to operator role because the same handler ALSO
         schedules `systemctl restart` on pip completion
         (v0.5.368 fix). Viewer polling could trigger a
         service restart.
    B8 — /api/health returns BOTH `netgen_version` (on-disk,
         back-compat) AND `running_version` (frozen at module
         load, captured in `_STARTUP_NETGEN_VERSION`), plus
         `restart_pending` bool. Clients can now detect the
         v0.5.368 lie without needing pip log parsing.

All sites carry marker `v0.5.370 (audit …)`.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_SERVER = _REPO / "run_tgen_server.py"
_NETGEN_UPGRADE = _REPO / "resources" / "tarball" / "netgen-upgrade"


def _server_src() -> str:
    return _SERVER.read_text()


def _upgrade_src() -> str:
    return _NETGEN_UPGRADE.read_text()


# ─── ast sanity ───


def test_server_ast_parses():
    ast.parse(_server_src())


def test_netgen_upgrade_ast_parses():
    ast.parse(_upgrade_src())


# ─── B1: loadHealth typo ───


def test_b1_loadhealth_typo_fixed():
    """Pre-fix: `if (typeof loadHealth === 'function') loadHealth();`
    at the RDMA install completion path. `loadHealth` doesn't
    exist (real name is `refreshHealth`) so the typeof guard
    swallowed the error and the RDMA card silently stayed
    stale. Post-fix must call `refreshHealth`."""
    src = _server_src()
    # Locate _pollRdmaLog through to its click handler.
    start = src.index("async function _pollRdmaLog")
    end = src.index("if ($('btn-install-rdma'))", start)
    body = src[start:end]
    # Strip JS/Python comment lines so a comment REFERENCING
    # `loadHealth()` for context doesn't trip us — the actual
    # call must be gone but the historical note is fine.
    non_comment = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("//")
        and not _l.lstrip().startswith("#")
    )
    assert "loadHealth()" not in non_comment, (
        "v0.5.367 loadHealth() typo not cleaned up in _pollRdmaLog"
    )
    # The corrected call must be there.
    assert "refreshHealth()" in body, (
        "_pollRdmaLog doesn't call refreshHealth after Install"
    )


def test_b1_marker():
    assert "v0.5.370 (audit rdma-card-no-refresh-after-install)" \
        in _server_src()


# ─── B2: SEC — restart/reboot role gate ───


def test_b2_restart_service_requires_admin():
    src = _server_src()
    assert re.search(
        r'@app\.route\("/api/system/restart_service"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    ), "/api/system/restart_service missing @require_role('admin')"


def test_b2_reboot_requires_admin():
    src = _server_src()
    assert re.search(
        r'@app\.route\("/api/system/reboot"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    ), "/api/system/reboot missing @require_role('admin')"


def test_b2_marker():
    # Marker is hyphenation-wrapped as `# ... no-role-\n# gate`.
    # Collapse Python's hyphen-comment-continuation idiom first.
    src = re.sub(r"-\n\s*#\s*", "-", _server_src())
    assert "v0.5.370 (audit system-endpoints-no-role-gate)" in src


# ─── B3: DPDK install TOCTOU parity with RDMA v0.5.93 ───


def test_b3_dpdk_install_preflight_under_lock():
    """The early proc check + force-kill + RDMA-mutex check must
    all be INSIDE `with _ADMIN_INSTALL_LOCK:` (mirroring the
    RDMA v0.5.93 shape). Pre-fix these were outside the lock;
    only the final Popen re-acquired."""
    src = _server_src()
    fn_start = src.index("def api_admin_install_dpdk():")
    # First lock acquisition inside the handler.
    lock_pos = src.index("with _ADMIN_INSTALL_LOCK:", fn_start)
    # 1. force-kill logging must appear AFTER lock acquire.
    #    The msg is Python string-concat across two source lines
    #    (`"... force-killed previous "` + `"install process ..."`),
    #    so search for the first half token.
    force_kill_pos = src.index(
        "ADMIN INSTALL DPDK] force-killed previous",
        fn_start,
    )
    assert force_kill_pos > lock_pos, (
        "DPDK install force-kill still outside _ADMIN_INSTALL_LOCK "
        "— TOCTOU parity with RDMA v0.5.93 not applied"
    )
    # 2. RDMA-mutex 409 message must appear AFTER lock too.
    rdma_mutex_pos = src.index(
        "RDMA install is in progress; both install paths",
        fn_start,
    )
    assert rdma_mutex_pos > lock_pos, (
        "DPDK install RDMA mutex check still outside "
        "_ADMIN_INSTALL_LOCK — race window with RDMA install"
    )


def test_b3_marker():
    assert "v0.5.370 (audit dpdk-install-toctou-parity)" \
        in _server_src()


# ─── B4: systemd-run unit-name entropy ───


def test_b4_transient_unit_suffix_helper_exists():
    src = _server_src()
    assert "def _transient_unit_suffix()" in src
    assert "monotonic_ns" in src
    assert "uuid4" in src


def test_b4_no_bare_int_time_in_unit_names():
    """No systemd unit-name f-string may still use
    `int(time.time())` (or `int(_t.time())`) for its suffix.
    Legitimate use in `reboot_at_unix` (line ~28633) is a
    UNIX timestamp field, not a unit name, and is allowed."""
    src = _server_src()
    # Look for any line that constructs a `.service` unit name
    # AND embeds `int(*.time())`.
    for line_no, line in enumerate(src.splitlines(), start=1):
        if ".service" in line and "runner" in line and "int(" in line \
                and "time()" in line:
            raise AssertionError(
                f"Line {line_no}: unit name still uses "
                f"int(time()) — collision risk on rapid retry:\n"
                f"  {line.strip()}"
            )


def test_b4_all_four_sites_use_helper():
    """Structural: the 4 known transient-unit sites (hugetlbfs
    mount, modprobe, install_dpdk, install_rdma, upgrade_wheel)
    must all use `_transient_unit_suffix()`."""
    src = _server_src()
    # Enumerate every `netgen-<foo>-{...}.service` f-string.
    hits = re.findall(
        r'f"netgen-[a-z_\-]+-\{[^}]+\}\.service"',
        src,
    )
    assert hits, "No transient-unit f-strings found — regex drift?"
    for _hit in hits:
        assert "_transient_unit_suffix()" in _hit, (
            f"Transient unit name doesn't use "
            f"_transient_unit_suffix: {_hit}"
        )


def test_b4_marker():
    assert "v0.5.370 (audit systemd-run-unit-name-collision)" \
        in _server_src()


# ─── B5: legacy+detached upgrade restart fallback ───


def test_b5_log_rc_inference_helper_exists():
    src = _server_src()
    assert "def _infer_upgrade_rc_from_log" in src
    # Must look for pip's canonical success line.
    assert "Successfully installed" in src
    # And netgen-upgrade helper's own success marker.
    assert "[upgrade] verify: ok" in src


def test_b5_completion_branch_uses_log_fallback():
    """When systemd_unit is set AND `_systemd_unit_state`
    returns (False, None), the completion branch must fall
    back to log-body inspection."""
    src = _server_src()
    fn_pos = src.index("def api_admin_upgrade_wheel_log(")
    end_pos = src.index("\n@app.route", fn_pos + 1)
    body = src[fn_pos:end_pos]
    assert "_infer_upgrade_rc_from_log" in body, (
        "api_admin_upgrade_wheel_log doesn't call "
        "_infer_upgrade_rc_from_log — legacy+detached path "
        "still hits v0.5.368 lie"
    )
    # And the fallback must be gated on the exact reap-unknown
    # condition (return_code is None + not running).
    assert "return_code is None" in body


def test_b5_marker():
    assert "v0.5.370 (audit upgrade-wheel-legacy-detached-restart)" \
        in _server_src()


# ─── B6: netgen-upgrade shutil.which ───


def test_b6_shutil_which_replaces_subprocess():
    """netgen-upgrade's tx_worker build-dep check must NOT use
    `subprocess.run(["which", tool])` any more — the `which`
    binary isn't guaranteed on minimal Ubuntu images."""
    src = _upgrade_src()
    # The bad pattern must be gone (comment mentioning it is
    # allowed, but no actual subprocess.run call).
    for line_no, line in enumerate(src.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        if 'subprocess.run(["which"' in line or \
           "subprocess.run(['which'" in line:
            raise AssertionError(
                f"Line {line_no}: netgen-upgrade still uses "
                f"subprocess.run(['which', ...]): {line.strip()}"
            )
    # And the good pattern (shutil.which) must be present.
    assert "shutil.which(tool)" in src, (
        "netgen-upgrade doesn't use shutil.which(tool) for "
        "meson/ninja detection"
    )


def test_b6_pkgconfig_filenotfound_caught():
    """pkg-config missing must be caught as FileNotFoundError
    (same failure mode as `which` was hiding)."""
    src = _upgrade_src()
    # Structural: near the shutil.which loop, there should be
    # a try/except FileNotFoundError around the pkg-config call.
    idx = src.index("shutil.which(tool)")
    tail = src[idx:idx + 1000]
    assert "FileNotFoundError" in tail
    assert "pkg-config" in tail


def test_b6_marker():
    assert "v0.5.370 (audit netgen-upgrade-which-false-positive)" \
        in _upgrade_src()


# ─── B7: upgrade_wheel/log role bump ───


def test_b7_upgrade_wheel_log_requires_operator_not_viewer():
    src = _server_src()
    assert re.search(
        r'@app\.route\("/api/admin/upgrade_wheel/log"[\s\S]{0,800}?'
        r'@require_role\(\s*[\'"]operator[\'"]\s*\)',
        src,
    ), "/api/admin/upgrade_wheel/log not @require_role('operator')"
    # And viewer must NOT be the effective role — check the
    # decorator block above the def doesn't say 'viewer'.
    m = re.search(
        r'(@app\.route\("/api/admin/upgrade_wheel/log"[\s\S]{0,1500}?)'
        r'def api_admin_upgrade_wheel_log',
        src,
    )
    assert m
    block = m.group(1)
    # Strip comment lines so a comment REFERENCING the pre-fix
    # `@require_role("viewer")` for context doesn't trip us.
    non_comment = "\n".join(
        _l for _l in block.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert '@require_role("viewer")' not in non_comment, (
        "upgrade_wheel/log still declares viewer role in a "
        "decorator (not just a historical comment)"
    )


def test_b7_marker():
    assert "v0.5.370 (audit upgrade-wheel-log-viewer-role-elevation)" \
        in _server_src()


# ─── B8: /api/health running_version vs installed_version ───


def test_b8_startup_version_constant_exists():
    src = _server_src()
    assert "_STARTUP_NETGEN_VERSION" in src
    # Must be captured at module load, NOT in the health handler.
    idx = src.index("_STARTUP_NETGEN_VERSION")
    # Should not be preceded by 'def ' on the same-ish body.
    # Simpler check: it should appear near the top (before line
    # ~500), which is module scope.
    line_no = src[:idx].count("\n") + 1
    assert line_no < 500, (
        f"_STARTUP_NETGEN_VERSION defined at line {line_no} — "
        f"expected module-scope (top of file). Otherwise it will "
        f"re-read on-disk metadata every call and defeat the "
        f"drift-detection."
    )


def test_b8_health_returns_running_version():
    src = _server_src()
    # /api/health handler must emit running_version + restart_pending.
    m = re.search(
        r"def api_health\(\)[\s\S]+?return jsonify\(\{[\s\S]+?\}\)",
        src,
    )
    assert m
    body = m.group(0)
    assert '"running_version"' in body
    assert '"restart_pending"' in body
    assert "_STARTUP_NETGEN_VERSION" in body
    # And the historic field survives for back-compat.
    assert '"netgen_version"' in body


def test_b8_restart_pending_is_diff_not_hardcoded():
    """The restart_pending value must be `netgen_version !=
    _STARTUP_NETGEN_VERSION` (or equivalent), not a constant."""
    src = _server_src()
    m = re.search(
        r"def api_health\(\)[\s\S]+?return jsonify\(\{[\s\S]+?\}\)",
        src,
    )
    body = m.group(0)
    assert re.search(
        r'"restart_pending":\s*netgen_version\s*!=\s*_STARTUP_NETGEN_VERSION',
        body,
    ), "restart_pending isn't computed as a version-diff"


def test_b8_marker():
    assert "v0.5.370 (audit health-version-drift-lie)" \
        in _server_src()


# ─── overall version guard ───


def test_pyproject_version_at_least_0570():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 370), (
        f"Version {m.group(1)} < 0.5.370"
    )


# ─── regression guards ───


def test_v0369_install_rdma_marker_intact():
    """v0.5.369's install_rdma marker must survive since v0.5.370
    also touched the RDMA install path (unit name)."""
    src = _server_src()
    assert "v0.5.369 (audit rdma-install-must-not-fail)" in \
        (_REPO / "resources" / "dpdk" / "install_rdma.sh").read_text()


def test_v0368_upgrade_wheel_restart_marker_intact():
    """v0.5.368's upgrade-wheel-legacy-no-restart fix must
    survive since v0.5.370 also touched the same completion
    branch."""
    src = _server_src()
    assert "v0.5.368 (audit upgrade-wheel-legacy-no-restart)" in src


def test_v0367_rdma_install_button_still_wired():
    """v0.5.367 button + click handler must survive since
    v0.5.370 touched the same _pollRdmaLog function."""
    src = _server_src()
    assert 'id="btn-install-rdma"' in src
    assert "_pollRdmaLog" in src
    # Regression guard: click handler still exists.
    assert "btn-install-rdma').addEventListener" in src


def test_v093_rdma_install_toctou_fix_still_intact():
    """v0.5.93 fix that put RDMA install's check+spawn under
    _ADMIN_INSTALL_LOCK must survive."""
    src = _server_src()
    assert "v0.5.93 (audit H3)" in src
