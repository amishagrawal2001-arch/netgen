"""v0.5.368 — /api/admin/upgrade_wheel: schedule systemctl restart
on the legacy (system-pip) path.

Operator on svl-d-ai-srv04 2026-09-20 uploaded v0.5.367 via the
desktop client. Log showed pip installed cleanly, `/api/health`
returned `netgen_version: 0.5.367`, and the client declared
"upgrade verified". But the running process still had the pre-
upgrade `_ADMIN_HTML` in memory — the RDMA install button never
appeared on /admin.

### Root cause

`api_admin_upgrade_wheel_log`'s completion branch keys off
`systemd_unit`:

    if return_code == 0 and not systemd_unit:
        <spawn detached `systemctl restart netgen-server`>
    elif return_code == 0 and systemd_unit:
        <do nothing — assume the helper restarts>

That's only true for the **tarball** path
(`upgrade_mode = "tarball:netgen-upgrade"`, which actually runs
`/opt/netgen-server/bin/netgen-upgrade`). On the **legacy** path
(`upgrade_mode = "legacy:system-pip+…"`), the systemd-run wrap
runs raw `python3 -m pip install …` — pip installs the wheel and
exits with no restart. netgen-server keeps the old code in
memory; `/api/health` reads the on-disk metadata pip just updated
and returns the new version, so the client thinks the upgrade
succeeded.

### Fix

Distinguish tarball vs legacy via `upgrade_mode` (already computed
higher up in the same handler but never persisted to state).
Persist it, then in the completion branch key off
`upgrade_mode.startswith("tarball:")` — only the tarball helper
does its own restart. All other modes (legacy, plus any future
non-helper mode) must schedule a restart from netgen-server
itself.

Both fix sites carry the marker
`v0.5.368 (audit upgrade-wheel-legacy-no-restart)`.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _src():
    return (_REPO / "run_tgen_server.py").read_text()


def test_marker_present():
    src = _src()
    assert src.count("v0.5.368 (audit upgrade-wheel-legacy-no-restart)") >= 2


def test_state_stores_upgrade_mode():
    """The completion branch needs to know the mode. Structural
    check that the POST handler persists `upgrade_mode` into
    `_ADMIN_UPGRADE_STATE`."""
    src = _src()
    fn_idx = src.index("def api_admin_upgrade_wheel(")
    _next = src.index("\n@app.route(\"/api/admin/upgrade_wheel/log", fn_idx)
    body = src[fn_idx:_next]
    assert '"upgrade_mode": upgrade_mode' in body


def test_completion_branch_keys_off_upgrade_mode_not_systemd_unit():
    """The completion branch must read `upgrade_mode` from state
    and short-circuit only when the mode starts with `tarball:`.
    Structural check for the exact pattern."""
    src = _src()
    _log_fn_idx = src.index("def api_admin_upgrade_wheel_log(")
    _next = src.index("\n@app.route", _log_fn_idx + 1)
    body = src[_log_fn_idx:_next]
    assert '_ADMIN_UPGRADE_STATE.get("upgrade_mode")' in body
    assert '_upgrade_mode.startswith("tarball:")' in body


def test_legacy_path_triggers_restart():
    """Structural check that the fix's `not _helper_restarts`
    branch calls `systemctl restart netgen-server`. Pre-fix the
    same restart code lived under `not systemd_unit`, which was
    always False on modern hosts (systemd-run is always
    available). Post-fix the guard is on the mode."""
    src = _src()
    _log_fn_idx = src.index("def api_admin_upgrade_wheel_log(")
    _next = src.index("\n@app.route", _log_fn_idx + 1)
    body = src[_log_fn_idx:_next]
    # Restart guard: `not _helper_restarts`.
    assert "not _helper_restarts" in body
    # Restart shell command must still be there.
    assert "systemctl restart netgen-server" in body


def test_tarball_path_still_short_circuits():
    """Guard against the fix regressing the tarball path.
    Pre-fix behavior for the tarball layout was to let the helper
    do its own restart — the fix must preserve that."""
    src = _src()
    _log_fn_idx = src.index("def api_admin_upgrade_wheel_log(")
    _next = src.index("\n@app.route", _log_fn_idx + 1)
    body = src[_log_fn_idx:_next]
    # Post-fix, tarball-only branch flags restart_scheduled without
    # calling systemctl — same shape as before but keyed on the
    # mode rather than systemd_unit.
    assert "elif return_code == 0 and _helper_restarts:" in body


def test_no_bare_systemd_unit_gate_survived():
    """Regression guard: the pre-fix pattern
    `not systemd_unit and not _ADMIN_UPGRADE_STATE.get(
    "restart_scheduled")` must be gone from the completion branch."""
    src = _src()
    _log_fn_idx = src.index("def api_admin_upgrade_wheel_log(")
    _next = src.index("\n@app.route", _log_fn_idx + 1)
    body = src[_log_fn_idx:_next]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "not systemd_unit and" not in _code_only


# --- Regression guards ---


def test_v0_5_23_systemd_run_wrap_still_intact():
    """The v0.5.23 systemd-run wrap that isolates pip in its own
    cgroup must stay — the whole point of that fix (netgen-server
    can crash mid-pip without corrupting the install) still
    applies. The v0.5.368 fix only changes the completion-branch
    decision, not the spawn logic."""
    src = _src()
    fn_idx = src.index("def api_admin_upgrade_wheel(")
    _next = src.index("\n@app.route(\"/api/admin/upgrade_wheel/log", fn_idx)
    body = src[fn_idx:_next]
    assert "systemd-run" in body.lower() or "_systemd_run_available" in body
    assert "netgen-upgrade-runner" in body


def test_v0_5_367_admin_rdma_install_button_still_intact():
    """Regression guard so the v0.5.368 fix didn't accidentally
    revert v0.5.367 (both edits touch run_tgen_server.py)."""
    src = _src()
    assert "v0.5.367 (audit admin-rdma-install-button)" in src


def test_ast_parses():
    ast.parse(_src())
