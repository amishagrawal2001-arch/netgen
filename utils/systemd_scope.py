"""v0.5.169: wrap tx_worker / rx_worker spawns in `systemd-run --scope`
so the kernel guarantees process lifecycle tracking.

Problem: v0.5.168 reactively detects orphan workers after the fact.
Whatever fix we ship in user-space (pkill backstop, finally blocks,
process-group kills) the worker can outlive its parent — the
operator's srv06 incident proved it.

systemd's scope units solve this structurally. When tx_worker is
launched via `systemd-run --scope --unit=netgen-tx-<stream_id>`,
the kernel:

  1. Puts the worker in its own cgroup.
  2. Tags it with a name we can stop unambiguously via
     `systemctl stop netgen-tx-<id>.scope` — no PID guessing, no
     pgrep race, kernel-enforced delivery.
  3. Keeps the unit alive when ostg-server crashes — but the unit
     is enumerable on next startup via
     `systemctl list-units 'netgen-{tx,rx}-*.scope'`, so the
     reaper can find every previous-session orphan in one call.

This module is pure-function — no spawning, no side effects. The
callers (utils/dpdk_tx_worker.py and friends) compose the prefix
into their cmd list before `subprocess.Popen(...)`.

Fallback path: if `systemd-run` isn't available (rare — only
non-systemd containers, BSD, macOS dev boxes), the wrapper returns
an empty prefix and the worker spawns naked. v0.5.168's reactive
orphan detection still works in that case; we just don't get the
structural guarantee.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from typing import List, Optional

logger = logging.getLogger(__name__)


# v0.5.169: cache the systemd-run path so we don't shell out on every
# spawn. Resolved lazily on first call to `has_systemd_run()` —
# import-time resolution would force every test to mock the PATH.
_systemd_run_path: Optional[str] = None
_systemd_run_resolved = False


# Unit name constraints: systemd accepts [A-Za-z0-9:_.\\-], roughly
# the same as filesystem-safe names. We sanitise to a tight subset
# (alphanumerics + dash) so the unit name is predictable from a
# stream_id UUID.
_UNIT_SAFE = re.compile(r"[^A-Za-z0-9-]")


def has_systemd_run() -> bool:
    """Probe PATH for `systemd-run`. Cached after first call.

    Returns False on:
      * macOS / BSD dev machines (no systemd)
      * Containers without /usr/bin/systemd-run mounted
      * Any host where PATH doesn't include /usr/bin

    Callers gracefully fall back to plain Popen in this case —
    losing the structural orphan guarantee but keeping the worker
    functional."""
    global _systemd_run_path, _systemd_run_resolved
    if _systemd_run_resolved:
        return _systemd_run_path is not None
    _systemd_run_path = shutil.which("systemd-run")
    _systemd_run_resolved = True
    if _systemd_run_path is None:
        logger.info(
            "[systemd-scope] systemd-run not on PATH — workers will "
            "spawn naked (orphans can still leak; v0.5.168 reactive "
            "reaping still applies)"
        )
    return _systemd_run_path is not None


def sanitise_unit_name(role: str, stream_id: str) -> str:
    """Build a deterministic unit name from (role, stream_id).

    Output shape: `netgen-tx-3ede73ca-79a1-4d1e-adac-e1aa85662fed`
    (no `.scope` suffix — systemd-run appends it automatically when
    --scope is set).

    role must be `tx` or `rx`. stream_id is normally a UUID but we
    sanitise defensively against operator-controlled strings.

    Constraints:
      * <=200 chars total — systemd's NAME_MAX safety margin
      * Only [A-Za-z0-9-] — strict subset of systemd's allowed set
    """
    role = (role or "x").lower()[:4]
    sid = _UNIT_SAFE.sub("-", str(stream_id or ""))
    sid = sid.strip("-") or "anon"
    name = f"netgen-{role}-{sid}"
    return name[:200]


def build_systemd_run_prefix(
    *,
    role: str,
    stream_id: str,
    use_sudo: bool = False,
    extra_properties: Optional[List[str]] = None,
) -> List[str]:
    """Return the systemd-run argv list that prefixes the worker cmd.

    Empty list when systemd-run isn't available — callers can
    splat without a None check:

        cmd = systemd_scope.build_systemd_run_prefix(...) + worker_cmd
        subprocess.Popen(cmd, ...)

    Flags chosen:
      * --scope        — direct ancestor cgroup, no service unit
                         layer between us and the worker.
      * --collect      — auto-cleanup the unit when the scope exits
                         (otherwise systemd keeps it as a failed-
                         state record indefinitely).
      * --unit=<name>  — predictable name we can `systemctl stop`
                         later without grepping `list-units`.
      * --quiet        — don't print "Running scope as unit ..." to
                         stderr — pollutes the worker's log stream.
      * --no-block     — return immediately rather than waiting for
                         the scope to exit (we want the worker
                         running in the background).

    `extra_properties` allows the caller to add resource limits
    (`-p MemoryMax=8G`, etc) — empty by default. Operators can use
    this to constrain runaway workers from a future config.

    `use_sudo` prepends `sudo --non-interactive` when the calling
    process isn't root. Mirrors the _maybe_sudo() pattern from
    v0.5.50."""
    if not has_systemd_run():
        return []
    unit = sanitise_unit_name(role, stream_id)
    cmd: List[str] = []
    if use_sudo and os.geteuid() != 0:
        cmd.extend(["sudo", "--non-interactive"])
    cmd.extend([
        _systemd_run_path or "systemd-run",
        "--scope",
        "--collect",
        f"--unit={unit}",
        "--quiet",
    ])
    for prop in (extra_properties or []):
        cmd.extend(["-p", prop])
    return cmd


def stop_scope_for_stream(
    role: str, stream_id: str, *, use_sudo: bool = False
) -> bool:
    """Best-effort `systemctl stop netgen-<role>-<sid>.scope`.

    Returns True when the unit was found + stopped (or already
    inactive); False when systemd or the unit is missing. Callers
    use this as a kernel-guaranteed Stop after v0.5.168's pkill
    backstop — if pkill missed, the cgroup stop catches it.
    """
    if not has_systemd_run():
        return False
    import subprocess
    unit = sanitise_unit_name(role, stream_id) + ".scope"
    cmd: List[str] = []
    if use_sudo and os.geteuid() != 0:
        cmd.extend(["sudo", "--non-interactive"])
    cmd.extend(["systemctl", "stop", unit])
    try:
        rc = subprocess.run(
            cmd, capture_output=True, timeout=5,
        ).returncode
        return rc == 0
    except subprocess.SubprocessError as exc:
        logger.debug(
            f"[systemd-scope] stop {unit} failed: {exc}"
        )
        return False


def list_netgen_scopes() -> List[str]:
    """Enumerate `netgen-{tx,rx}-*.scope` units currently active.

    Used by the server on startup to detect units that survived a
    crash: each one is an orphan from a previous session. The
    /api/streams/orphans endpoint can then unify
    `find_dpdk_workers()` (PID-based) with this list (cgroup-based)
    for a complete picture."""
    if not has_systemd_run():
        return []
    import subprocess
    try:
        proc = subprocess.run(
            ["systemctl", "list-units",
             "--type=scope",
             "--no-legend",
             "--plain",
             "netgen-tx-*.scope", "netgen-rx-*.scope"],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.SubprocessError:
        return []
    if proc.returncode != 0:
        return []
    units: List[str] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".scope"):
            units.append(parts[0])
    return units


# ───── test hook ───────────────────────────────────────────────────


def _reset_cache_for_tests() -> None:
    """Tests that mock `shutil.which` need to bust the cache between
    runs. Not part of the public API."""
    global _systemd_run_path, _systemd_run_resolved
    _systemd_run_path = None
    _systemd_run_resolved = False
