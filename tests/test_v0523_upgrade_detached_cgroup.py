"""v0.5.23 — wheel upgrade must run in a detached systemd cgroup.

Operator-reported on srv06 (Jun 7 2026):

  Successfully uninstalled ostg-trafficgen-0.5.13:
  [client] log poll error: Connection reset by peer (retrying)
  [client] pip exited rc=None; aborting

Diagnosis: the upgrade endpoint spawned pip as a child of
netgen-server's flask process. Pip was in netgen-server.service's
cgroup. When pip uninstalled ostg-trafficgen mid-flight, a flask
worker handling a stats poll tripped an ImportError on the now-
deleted code → flask crashed → systemd reaped the cgroup →
**pip was killed too** (cgroup-kill, not parent-death). Operator
saw rc=None (signal-killed, no exit code), wheel never installed,
half-uninstalled site-packages required manual SSH recovery.

v0.5.23 wraps the pip spawn in `systemd-run --no-block --collect
--unit=netgen-upgrade-runner-<ts>.service` so pip runs in its
OWN cgroup. Whatever happens to netgen-server.service no longer
affects pip. The netgen-upgrade script itself triggers the post-
install restart, which cleanly cycles netgen-server to load the
new code while the detached cgroup keeps pip alive.

State across restart: _ADMIN_UPGRADE_STATE persists to
/var/lib/netgen-server/upgrade-state.json so the post-restart
server can still answer /api/admin/upgrade_wheel/log polls
truthfully (instead of reporting "no upgrade" → client logs
"pip exited rc=None; aborting" on a successful upgrade).

Status tracking: when systemd_unit is set, the log endpoint
must use `systemctl is-active` + `systemctl show
--property=ExecMainStatus` instead of proc.poll(). The local
Popen handle only sees systemd-run's dispatcher exit (immediate
with --no-block), not the actual unit lifecycle.

Pin all four contracts so a refactor surfaces here rather than
the next time an operator runs a wheel upgrade on srv06.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _upgrade_endpoint_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef api_)",
        src,
    )
    assert m, "api_admin_upgrade_wheel body not found"
    return m.group(0)


def _log_endpoint_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_upgrade_wheel_log\(\)[\s\S]+?(?=\n@app\.route|\ndef api_|\n_ADMIN_HTML)",
        src,
    )
    assert m, "api_admin_upgrade_wheel_log body not found"
    return m.group(0)


# ────────────────── 1. systemd-run detection + wrap ─────────────────


def test_systemd_run_availability_helper_exists():
    """A cached availability helper must exist so we don't shell out
    on every upgrade. Pattern matches _PIP_BREAK_FLAG_DETECTED."""
    src = _SERVER.read_text()
    assert "_systemd_run_available" in src, (
        "No _systemd_run_available helper — every upgrade request "
        "would re-stat systemd-run."
    )
    assert "_SYSTEMD_RUN_PATH_DETECTED" in src, (
        "Detection result not cached on the module global."
    )
    assert re.search(
        r'["\']_SYSTEMD_RUN_PATH_DETECTED["\']\s+not in\s+globals\(\)',
        src,
    ), "Cache check isn't `not in globals()` — would re-stat on each call."


def test_systemd_run_availability_requires_root():
    """systemd-run for SYSTEM units needs euid==0. Non-root caller
    would get a permission denied with no clear signal back to the
    operator. Helper must refuse to return a path when not root."""
    src = _SERVER.read_text()
    # The helper body must reference os.geteuid() == 0
    m = re.search(
        r"def _systemd_run_available\(\)[\s\S]+?(?=\ndef |\Z)", src,
    )
    assert m, "_systemd_run_available body missing"
    assert "geteuid" in m.group(0), (
        "_systemd_run_available doesn't gate on euid — non-root "
        "would get a confusing 'Failed to start transient unit' "
        "error mid-upgrade."
    )


def test_upgrade_endpoint_wraps_in_systemd_run_when_available():
    """The endpoint must wrap the pip command in systemd-run when
    available. The wrapping must use --no-block + --collect +
    --unit=netgen-upgrade-runner-<ts>.service so the unit survives
    the parent (netgen-server) being restarted."""
    body = _upgrade_endpoint_body()
    assert "_systemd_run_available()" in body, (
        "Endpoint doesn't probe for systemd-run — would always run "
        "in the parent cgroup → v0.5.22 srv06 failure recurs."
    )
    assert "--no-block" in body, (
        "Missing --no-block — systemd-run would block on the unit, "
        "tying the upgrade's lifetime back to the parent process."
    )
    assert "--collect" in body, (
        "Missing --collect — failed units linger forever in "
        "systemctl-list-units, eventually breaking restart."
    )
    assert "netgen-upgrade-runner-" in body, (
        "Unit name doesn't include the netgen-upgrade-runner prefix "
        "— operators grepping `systemctl list-units` won't find it."
    )
    # The unit name must end in a value that makes it unique per
    # invocation (a timestamp suffix), otherwise concurrent or rapid
    # repeat upgrades clash on the unit name.
    assert re.search(
        r'f["\']netgen-upgrade-runner-\{[^}]+\}\.service["\']',
        body,
    ), (
        "Unit name isn't an f-string with a per-invocation suffix — "
        "repeat upgrades would conflict on the unit name."
    )


def test_upgrade_endpoint_pipes_output_to_log_file():
    """When wrapped, the unit's stdout/stderr must redirect to the
    /var/log/netgen-upgrade.log file (the same one the client is
    polling). Without this redirect the operator's log viewer goes
    silent the moment pip is spawned."""
    body = _upgrade_endpoint_body()
    assert "--property=StandardOutput=append:" in body, (
        "systemd-run wrap doesn't redirect StandardOutput to the "
        "log file — client log poll would show nothing."
    )
    assert "--property=StandardError=append:" in body, (
        "systemd-run wrap doesn't capture StandardError — error "
        "messages from pip wouldn't reach the client."
    )


def test_upgrade_endpoint_sets_home_for_subprocess():
    """The detached unit doesn't inherit netgen-server's env. pip's
    --user, ~/.cache, and pip-tools all expect $HOME — set it
    explicitly so pip doesn't fall into the same trap as the v0.5.21
    install_dpdk.sh HOME-unbound bug."""
    body = _upgrade_endpoint_body()
    assert "--setenv=HOME=" in body, (
        "systemd-run wrap doesn't set HOME — pip's cache + tooling "
        "would fail with HOME-unbound errors in the detached unit."
    )


def test_upgrade_endpoint_stamps_detached_mode_in_log():
    """upgrade_mode must surface the detached suffix so an operator
    reading /var/log/netgen-upgrade.log can tell the systemd-run
    path was taken."""
    body = _upgrade_endpoint_body()
    assert "+detached" in body, (
        "upgrade_mode doesn't get a +detached suffix — log doesn't "
        "reveal whether the new code path was active."
    )


def test_upgrade_endpoint_records_systemd_unit_in_state():
    """The endpoint must store the unit name in _ADMIN_UPGRADE_STATE
    so subsequent log-endpoint polls know to query systemctl."""
    body = _upgrade_endpoint_body()
    assert '"systemd_unit"' in body or "'systemd_unit'" in body, (
        "Endpoint doesn't write systemd_unit into _ADMIN_UPGRADE_STATE "
        "— the log endpoint can't tell that systemd-run was used."
    )
    # Also: the JSON response should surface it for the client.
    assert re.search(
        r'jsonify\(\s*\{[^}]*systemd_unit', body,
    ) or '"systemd_unit": systemd_unit' in body, (
        "Endpoint response doesn't surface systemd_unit — client "
        "can't see whether detached mode kicked in."
    )


# ─────────────────── 2. status via systemctl, not proc.poll ─────────


def test_log_endpoint_uses_systemctl_when_unit_set():
    """When _ADMIN_UPGRADE_STATE has systemd_unit, the log endpoint
    must use systemctl to compute (running, return_code) instead of
    proc.poll() — Popen tracks systemd-run's dispatcher exit, not
    the unit lifecycle."""
    body = _log_endpoint_body()
    assert "_systemd_unit_state(" in body or "systemctl is-active" in body, (
        "Log endpoint still relies on proc.poll() — would report "
        "the upgrade as done immediately (systemd-run --no-block "
        "exits in milliseconds)."
    )
    assert "systemd_unit" in body, (
        "Log endpoint doesn't even look at systemd_unit field in "
        "state — branching logic missing entirely."
    )


def test_systemd_unit_state_helper_exists():
    """A helper that maps a unit name to (running, return_code) so
    the log endpoint stays readable."""
    src = _SERVER.read_text()
    assert "def _systemd_unit_state(" in src, (
        "No _systemd_unit_state helper — log endpoint would be "
        "littered with shell-outs to systemctl."
    )
    m = re.search(
        r"def _systemd_unit_state\([\s\S]+?(?=\ndef |\Z)", src,
    )
    body = m.group(0)
    assert "is-active" in body, "Helper doesn't call systemctl is-active"
    assert "ExecMainStatus" in body, (
        "Helper doesn't pull ExecMainStatus — can't return the "
        "true exit code from a completed unit."
    )


def test_log_endpoint_skips_server_side_restart_when_detached():
    """When systemd_unit is set, the detached netgen-upgrade script
    already calls `systemctl restart netgen-server` itself. The
    server-side restart trigger (sh -c 'sleep 2 && systemctl
    restart netgen-server') MUST be skipped — otherwise we get a
    double-restart race where the first restart kills the
    in-flight netgen-upgrade script."""
    body = _log_endpoint_body()
    # The restart-scheduling block must check systemd_unit to gate
    # whether to spawn the detached restart shell.
    # Pattern: the existing `subprocess.Popen(["sh", "-c", "sleep 2
    # && systemctl restart netgen-server"], ...)` block must be
    # behind a `not systemd_unit` guard.
    m = re.search(
        r"if return_code == 0[\s\S]+?subprocess\.Popen\(\s*\[\"sh\"",
        body,
    )
    assert m, "Server-side restart trigger block not found"
    guard = m.group(0)
    assert "not systemd_unit" in guard, (
        "Server-side restart trigger isn't gated on `not "
        "systemd_unit` — would race with the netgen-upgrade "
        "script's own restart call."
    )


def test_log_endpoint_flags_restart_scheduled_when_detached():
    """Even when we skip the server-side restart, the client needs
    to know restart_scheduled=True so it switches to polling
    /api/health. Without the flag the client would assume the
    upgrade never finished cleanly."""
    body = _log_endpoint_body()
    # After the restart-skip path, restart_scheduled must still be
    # set to True so the client transitions to /api/health polling.
    assert re.search(
        r'elif return_code == 0 and systemd_unit:[\s\S]+?'
        r'_ADMIN_UPGRADE_STATE\[["\']restart_scheduled["\']\]\s*=\s*True',
        body,
    ), (
        "Detached path doesn't set restart_scheduled=True — client "
        "would keep polling the log endpoint forever."
    )


# ─────────────── 3. State persistence across restart ────────────────


def test_state_persistence_file_path_pinned():
    """The state file must live at /var/lib/netgen-server/
    upgrade-state.json — /var/lib is the right FHS location for
    package state. /tmp would lose across reboots; /etc is for
    config."""
    src = _SERVER.read_text()
    assert "_ADMIN_UPGRADE_STATE_FILE" in src, (
        "No state-file constant — path scattered across the code."
    )
    assert "/var/lib/netgen-server/upgrade-state.json" in src, (
        "State file isn't at /var/lib/netgen-server/. Wrong FHS "
        "location → reboot wipes; upgrade-tracking breaks."
    )


def test_state_persist_helper_atomic():
    """_admin_upgrade_persist must write atomically (tmp + rename)
    so a kill-mid-write doesn't leave an unparseable JSON the
    server can't reload on startup."""
    src = _SERVER.read_text()
    m = re.search(
        r"def _admin_upgrade_persist\(\)[\s\S]+?(?=\ndef |\Z)", src,
    )
    assert m, "_admin_upgrade_persist not found"
    body = m.group(0)
    assert ".tmp" in body and "os.replace" in body, (
        "_admin_upgrade_persist doesn't use tmp + os.replace — "
        "kill-mid-write would corrupt the state file."
    )


def test_state_snapshot_excludes_popen():
    """_admin_upgrade_state_snapshot must NOT include the Popen
    object — it's unpickleable and would crash json.dump."""
    src = _SERVER.read_text()
    m = re.search(
        r"def _admin_upgrade_state_snapshot\(\)[\s\S]+?(?=\ndef |\Z)", src,
    )
    assert m, "_admin_upgrade_state_snapshot not found"
    body = m.group(0)
    assert '"process"' in body or "'process'" in body, (
        "Snapshot helper doesn't reference 'process' key — can't "
        "tell whether it's excluded."
    )
    assert "!=" in body and "process" in body, (
        "Snapshot doesn't EXCLUDE 'process' — json.dump would "
        "crash on the Popen object."
    )


def test_state_loaded_on_module_import():
    """The persisted state must reload when the module imports.
    Otherwise the post-restart server's _ADMIN_UPGRADE_STATE is
    empty → next log-endpoint poll returns 'no upgrade' → client
    logs 'pip exited rc=None; aborting' on a SUCCESSFUL upgrade."""
    src = _SERVER.read_text()
    assert "_admin_upgrade_load()" in src, (
        "_admin_upgrade_load is never called — persisted state "
        "isn't actually reloaded on import."
    )
    # The call must be at module scope, not inside a function only
    # invoked on demand.
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "_admin_upgrade_load()":
            # Must NOT be indented (i.e. module scope).
            assert not line.startswith(" ") and not line.startswith("\t"), (
                f"_admin_upgrade_load() call at line {i+1} is "
                f"indented — only runs inside some function, not "
                f"on import."
            )
            break
    else:
        raise AssertionError("_admin_upgrade_load() call not located")


def test_state_load_tolerates_missing_or_corrupt_file():
    """First-run servers won't have the state file. Mid-write
    interrupted servers may have an unparseable one. Loader must
    swallow both quietly — the wrong choice (crash) would brick
    netgen-server startup."""
    src = _SERVER.read_text()
    m = re.search(
        r"def _admin_upgrade_load\(\)[\s\S]+?(?=\ndef |\Z)", src,
    )
    assert m, "_admin_upgrade_load not found"
    body = m.group(0)
    assert "isfile" in body, (
        "_admin_upgrade_load doesn't check file exists before "
        "reading — first-run startup would log scary file-not-found."
    )
    assert "except" in body and "pass" in body, (
        "_admin_upgrade_load doesn't swallow exceptions — corrupt "
        "state file would prevent netgen-server from starting."
    )


# ─────────────────── 4. Legacy path still works ─────────────────────


def test_legacy_path_unchanged_when_systemd_run_unavailable():
    """When systemd-run is unavailable (non-systemd hosts, non-root,
    Docker), the upgrade endpoint must fall back to the v0.5.22
    behavior: spawn pip directly + use proc.poll() + server-side
    restart trigger."""
    body = _upgrade_endpoint_body()
    log_body = _log_endpoint_body()
    # Endpoint: systemd_unit defaults to None when systemd-run not
    # available.
    assert "systemd_unit = None" in body, (
        "Endpoint doesn't initialise systemd_unit to None — code "
        "paths without systemd-run would leave it undefined."
    )
    # Log endpoint: when systemd_unit is None, MUST fall back to
    # proc.poll() (else legacy install hosts break entirely).
    assert "proc.poll()" in log_body, (
        "Log endpoint dropped the proc.poll() fallback — legacy "
        "install hosts can no longer track upgrade progress."
    )


def test_pyproject_version_at_least_0523():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 23), (
        f"Version {m.group(1)} < 0.5.23"
    )
