"""Server-side auto-install of OS-level system dependencies the wheel
itself cannot install.

The wheel-only upgrade path (Install Guide §9a) swaps Python code but
does NOT re-run ``install_ostg_complete.py``'s system-deps steps. A
server upgraded from a pre-v0.3.12 install (where _install_rdma_userspace
didn't exist yet) ends up with the new RDMA runtime code but no
perftest binary — Tools → RDMA Blast pops the red "perftest is NOT
installed" banner even though the operator just upgraded.

This module is the v0.3.18 self-heal that closes the gap: a daemon
thread fires at server startup, detects missing perftest, and runs
``apt-get install -y perftest rdma-core libibverbs-dev`` (or the
distro equivalent). No operator SSH step needed. Same flags the
v0.3.17 ``_apt_install`` helper uses, so it survives conffile
prompts on Ubuntu 24.04.

Scope (kept minimal — only packages the wheel's code actively
requires that aren't in any sane Linux baseline):

* ``perftest`` — ib_send_bw / ib_write_bw / ib_read_bw + _lat
  binaries; the actual RDMA traffic generators ``utils/rdma_perf``
  orchestrates.
* ``rdma-core`` — userspace verbs library + ``ibv_devinfo`` /
  ``ibv_devices`` / ``rxe_cfg``.
* ``libibverbs-dev`` — verbs API headers (transitive deps).

Out of scope — intentionally not auto-installed:

* **DPDK** — 10–30 min build, must be operator-initiated.
* **Docker** — operator may have their own Docker policy/version.
* **libmlx5-dev** — MOFED-only header; breaks the install on hosts
  without the Mellanox MOFED apt repo. install_ostg_complete.py
  handles this via a separate optional pass; the server auto-install
  intentionally skips Mellanox-specific to stay conservative.

Design properties (each pinned by tests/test_system_deps_auto_install.py):

1. **Async off the Flask startup critical path** — caller spawns a
   daemon thread. The module itself doesn't sleep at import.
2. **Once-per-uptime guard** via module-level ``_attempted`` flag.
   Even if ``ensure_rdma_userspace_installed`` is called from
   multiple places (e.g. startup + first RDMA endpoint hit) the
   actual apt invocation runs at most once per server lifetime.
3. **Time-bounded** — 60 sec ceiling on apt subprocess. apt-get
   stuck behind a dead mirror won't hang the thread forever.
4. **Distro-aware** (apt / dnf / yum / apk / zypper). Returns
   ``unsupported`` and logs a warning on anything else.
5. **Idempotent** — if perftest is already on PATH, skip silently.
   Detected via ``shutil.which("ib_send_bw")``.
6. **Logs to a dedicated file** (``/var/log/netgen-auto-install.log``)
   AND to the standard logger so operators can audit.
7. **Kill-switch env var** ``NETGEN_AUTO_INSTALL=0`` for managed
   systems that explicitly don't want the server installing
   packages.
8. **Never raises** — caller's daemon thread is expected to be
   wrapped in try/except but this module's public function
   absorbs all exceptions internally.
9. **Needs root** — the systemd unit already runs as root for
   VRF/DPDK reasons, so this is a no-op constraint. On a
   non-root run (operator running ``ostg-server`` directly for
   dev), apt-get itself will fail and we log + give up.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple


# Module-level once-per-uptime guard. ``_attempted`` flips True the
# moment ``ensure_rdma_userspace_installed`` starts work — even if
# the apt invocation fails, we don't retry within the same uptime
# (avoids spamming apt every 30 seconds when something's broken).
_lock = threading.Lock()
_attempted = False

# Where the auto-install log lands. Separate file so operators can
# grep for install activity without sifting through Flask request
# logs. /var/log is conventional on root services.
AUTO_INSTALL_LOG = "/var/log/netgen-auto-install.log"

# Env var to disable the auto-install entirely. Set to "0" or "false"
# on managed systems where apt changes by background processes are
# disallowed. Any other value (or unset) means auto-install is on.
KILL_SWITCH_ENV = "NETGEN_AUTO_INSTALL"

# Per-invocation timeout on the actual apt subprocess. apt-get
# behind a dead mirror has been observed to hang 10+ min.
APT_TIMEOUT_SEC = 60

# Canonical packages the wheel's RDMA code requires (intersection
# of all distros' name choices). Pre-validated against
# install_ostg_complete.py._install_rdma_userspace.
_PACKAGES = {
    "apt":    ["perftest", "rdma-core", "libibverbs-dev"],
    "dnf":    ["perftest", "rdma-core", "libibverbs-devel"],
    "yum":    ["perftest", "rdma-core", "libibverbs-devel"],
    "apk":    ["perftest", "rdma-core", "libibverbs-dev"],
    "zypper": ["perftest", "rdma-core", "libibverbs-devel"],
}


def _file_log(line: str) -> None:
    """Append a timestamped line to the dedicated auto-install log.
    Best-effort — silently swallows IO errors (caller runs in a
    daemon thread that mustn't crash on missing /var/log)."""
    try:
        Path(AUTO_INSTALL_LOG).parent.mkdir(parents=True, exist_ok=True)
        with open(AUTO_INSTALL_LOG, "a") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass


def _log(level: int, msg: str) -> None:
    """Log to both the standard logger and the dedicated file."""
    logging.log(level, f"[AUTO-INSTALL] {msg}")
    _file_log(f"[{logging.getLevelName(level)}] {msg}")


def _is_killed() -> bool:
    """Honour the kill-switch env var. Returns True if the operator
    has explicitly disabled auto-install."""
    val = os.environ.get(KILL_SWITCH_ENV, "").strip().lower()
    return val in ("0", "false", "no", "off")


def _detect_package_manager() -> Optional[str]:
    """Return the name of the available system package manager,
    matching the keys of ``_PACKAGES``. Returns None if the host
    runs something exotic we don't support."""
    for pm in ("apt", "dnf", "yum", "apk", "zypper"):
        # apt-get is the actual binary; apt is a frontend on newer
        # Debian/Ubuntu. Either presence indicates the apt family.
        if pm == "apt":
            if shutil.which("apt-get"):
                return "apt"
        elif shutil.which(pm):
            return pm
    return None


def _perftest_installed() -> bool:
    """Idempotency check. We use ``ib_send_bw`` as the canonical
    signal because it's the binary the wheel's RDMA code actually
    invokes; ``which perftest`` returns nothing (perftest is a
    meta-package, not a binary)."""
    return shutil.which("ib_send_bw") is not None


def _build_install_cmd(pm: str, packages: list) -> list:
    """Compose the apt/dnf/yum/apk/zypper install argv. Mirrors
    install_ostg_complete.py's ``_apt_install`` flag set for the
    apt family — ``--force-confdef`` / ``--force-confold`` to
    auto-resolve conffile diffs without prompting."""
    pkg_args = list(packages)
    if pm == "apt":
        return [
            "apt-get", "install", "-y",
            "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold",
            *pkg_args,
        ]
    if pm in ("dnf", "yum"):
        return [pm, "install", "-y", *pkg_args]
    if pm == "apk":
        return ["apk", "add", *pkg_args]
    if pm == "zypper":
        return ["zypper", "install", "-y", *pkg_args]
    # Should be unreachable — _detect_package_manager already
    # filtered to supported set.
    return []


def _apt_update_first() -> Tuple[bool, str]:
    """Refresh apt's package index. apt-get install on a stale
    index is the #1 cause of ``E: Unable to locate package``
    on a host that hasn't seen an apt-get update since boot.
    Returns (success, message)."""
    try:
        r = subprocess.run(
            ["apt-get", "update"],
            capture_output=True, text=True, timeout=APT_TIMEOUT_SEC,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
        if r.returncode == 0:
            return True, "apt-get update OK"
        return False, f"apt-get update rc={r.returncode}: {(r.stderr or r.stdout or '').strip()[:200]}"
    except subprocess.TimeoutExpired:
        return False, f"apt-get update timed out after {APT_TIMEOUT_SEC}s"
    except FileNotFoundError:
        return False, "apt-get not found (distro mismatch?)"
    except Exception as e:
        return False, f"apt-get update raised: {e}"


def ensure_rdma_userspace_installed() -> None:
    """Server startup self-heal: install perftest + rdma-core +
    libibverbs-dev if missing.

    Safe to call from a daemon thread. Never raises. Logs to both
    the standard logger and ``/var/log/netgen-auto-install.log``.
    Runs at most once per server uptime — the second and subsequent
    calls return immediately via the module-level ``_attempted``
    guard.

    Caller pattern (from run_tgen_server.py main):

        threading.Thread(
            target=ensure_rdma_userspace_installed,
            name="rdma-userspace-autoinstall",
            daemon=True,
        ).start()
    """
    global _attempted
    with _lock:
        if _attempted:
            # Another thread already did the work (or is doing it).
            return
        _attempted = True

    try:
        # Kill switch first — managed systems can opt out without
        # changing the wheel.
        if _is_killed():
            _log(logging.INFO,
                 f"{KILL_SWITCH_ENV}=0 set; skipping auto-install")
            return

        # Idempotency — if perftest already on PATH, nothing to do.
        if _perftest_installed():
            _log(logging.INFO,
                 "perftest (ib_send_bw) already on PATH — skipping")
            return

        # Distro detection.
        pm = _detect_package_manager()
        if pm is None:
            _log(logging.WARNING,
                 "no supported package manager found "
                 "(apt/dnf/yum/apk/zypper) — operator must install "
                 "perftest manually")
            return

        # Root check. Server's systemd unit runs as root by default,
        # so this branch only fires on a dev ostg-server invocation.
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            _log(logging.WARNING,
                 f"running as uid={os.geteuid()}, not root — "
                 "skipping auto-install (apt-get install needs root)")
            return

        packages = _PACKAGES[pm]
        _log(logging.INFO,
             f"perftest missing on {pm} host; installing: "
             f"{' '.join(packages)}")

        # apt family — refresh index first (best-effort; install
        # still attempted even if update fails since the index may
        # be fresh enough from systemd-timer-driven background
        # updates).
        if pm == "apt":
            ok, msg = _apt_update_first()
            _log(logging.INFO if ok else logging.WARNING, msg)

        cmd = _build_install_cmd(pm, packages)
        try:
            env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=APT_TIMEOUT_SEC, env=env,
            )
        except subprocess.TimeoutExpired:
            _log(logging.WARNING,
                 f"install timed out after {APT_TIMEOUT_SEC}s — "
                 "leaving as-is (operator can retry by restarting "
                 "the service or running the apt command manually)")
            return
        except Exception as e:
            _log(logging.WARNING, f"install subprocess raised: {e}")
            return

        if r.returncode == 0:
            # Verify the install actually placed ib_send_bw — apt
            # has been observed to return 0 even when packages
            # didn't actually land (broken dependency tree etc.).
            if _perftest_installed():
                _log(logging.INFO,
                     f"perftest installed successfully via {pm}; "
                     "RDMA features now available without server "
                     "restart")
            else:
                _log(logging.WARNING,
                     f"{pm} returned rc=0 but ib_send_bw still not "
                     "on PATH — possible packaging issue, install "
                     "perftest manually")
            return

        _log(logging.WARNING,
             f"{pm} install rc={r.returncode}: "
             f"{(r.stderr or r.stdout or '').strip()[:300]}")
    except Exception as e:
        # Final safety net — public API contract is "never raises".
        _log(logging.ERROR, f"unexpected error: {e}")
