"""Regression tests for the dpkg auto-heal pre-flight step.

Operator scenario this guards against (svl-d-ai-srv04 field report):

1. A previous Fresh Install ran with the pre-v0.3.16 installer that
   hit the dpkg conffile prompt on containerd.io's
   /etc/containerd/config.toml.
2. dpkg left containerd.io + docker-ce in a half-configured "iU"
   state. apt's archive cache also got out of sync.
3. The operator retries Fresh Install with the v0.3.16+ fixed
   installer (which has the --force-confdef + --force-confold flags
   that would have prevented the original failure).
4. Without auto-heal, apt-get install fails on the retry with
   `Internal Error, No file name for containerd.io:amd64` because
   the package is half-installed and apt can't recover with
   --reinstall alone. The retry produces the SAME failure even
   though the underlying bug is fixed — bad UX.

Fix: ``_heal_dpkg_state`` pre-flight runs BEFORE
install_system_dependencies and detects + recovers the half-state.
The operator's retry just works.

These tests pin the pre-flight's existence, invocation order, and
the specific commands in the recovery sequence (so a future refactor
can't silently regress it back to the broken behavior)."""
from __future__ import annotations

import re


_INSTALLER_PATH = "/Users/surajsharma/dev/netgen/install_ostg_complete.py"


def _installer_src():
    return open(_INSTALLER_PATH).read()


def test_heal_dpkg_state_method_exists():
    """The pre-flight helper must exist as a method on the installer."""
    src = _installer_src()
    assert re.search(
        r"def _heal_dpkg_state\(self\):",
        src,
    ), "_heal_dpkg_state() method missing — see test docstring for context."


def test_heal_runs_before_install_system_dependencies_local():
    """install_local() must call _heal_dpkg_state() BEFORE
    install_system_dependencies(). Order matters — recovering the
    dpkg state mid-install (after some packages have been touched)
    can cascade into more breakage."""
    src = _installer_src()
    m = re.search(
        r"def install_local\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "install_local() not found"
    body = m.group(0)
    heal_pos = body.find("_heal_dpkg_state()")
    deps_pos = body.find("install_system_dependencies()")
    assert heal_pos != -1, \
        "install_local() must call _heal_dpkg_state()"
    assert deps_pos != -1, \
        "install_local() must still call install_system_dependencies()"
    assert heal_pos < deps_pos, (
        "_heal_dpkg_state() must run BEFORE "
        "install_system_dependencies() — otherwise the broken state "
        "trips up the system-deps install before we get a chance to "
        "clean it."
    )


def test_heal_runs_before_install_system_dependencies_remote():
    """install_remote() must also call _heal_dpkg_state() before
    system deps — same reasoning as the local path."""
    src = _installer_src()
    m = re.search(
        r"def install_remote\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "install_remote() not found"
    body = m.group(0)
    heal_pos = body.find("_heal_dpkg_state()")
    deps_pos = body.find("install_system_dependencies()")
    assert heal_pos != -1, \
        "install_remote() must call _heal_dpkg_state()"
    assert heal_pos < deps_pos, \
        "_heal_dpkg_state() must run before install_system_dependencies()"


def _heal_body():
    """Return the source of _heal_dpkg_state for inspection."""
    src = _installer_src()
    m = re.search(
        r"def _heal_dpkg_state\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "_heal_dpkg_state() body not found"
    return m.group(0)


def test_heal_short_circuits_on_non_apt():
    """Only apt has the conffile-prompt failure mode. The heal step
    must check the package manager and return early on
    dnf/yum/apk/zypper — running ``dpkg --audit`` on those systems
    is wasted I/O at best and may produce a misleading warning at
    worst."""
    body = _heal_body()
    assert 'pm != "apt"' in body or 'pm == "apt"' in body, (
        "_heal_dpkg_state must guard on package_manager == 'apt'"
    )


def test_heal_checks_dpkg_audit():
    """The detection step must run ``dpkg --audit`` — that's the
    canonical way to identify packages in half-configured state."""
    body = _heal_body()
    assert "dpkg --audit" in body, (
        "_heal_dpkg_state must run `dpkg --audit` to detect "
        "half-configured packages"
    )


def test_heal_force_removes_docker_stack():
    """The recovery step must include force-removal of the docker
    stack — it's the most common culprit (the v0.3.16 user case)
    and the only way to clear the half-state of these specific
    packages."""
    body = _heal_body()
    assert "dpkg --remove --force-all" in body, (
        "_heal_dpkg_state must use `dpkg --remove --force-all` to "
        "clear half-configured packages — --reinstall is not enough."
    )
    # The docker stack must be named in the removal list.
    for pkg in ("containerd.io", "docker-ce", "docker-ce-cli"):
        assert pkg in body, (
            f"_heal_dpkg_state must force-remove {pkg} — that's the "
            f"specific stuck-package family from the v0.3.16 bug report."
        )


def test_heal_runs_dpkg_configure_with_force_flags():
    """For half-states OTHER than the docker stack, the heal step
    must run ``dpkg --configure -a --force-confdef --force-confold``
    so dpkg can complete any other stuck packages non-interactively.
    Without --force-conf flags, dpkg would prompt and EOF again."""
    body = _heal_body()
    assert "dpkg --configure -a" in body, (
        "_heal_dpkg_state must run `dpkg --configure -a` to complete "
        "non-docker half-states"
    )
    assert "--force-confdef" in body and "--force-confold" in body, (
        "dpkg --configure -a needs both --force-confdef AND "
        "--force-confold to suppress the conffile prompt that "
        "aborted the original install."
    )


def test_heal_cleans_apt_cache():
    """The cache mismatch is what produces the "Internal Error,
    No file name for <pkg>" message. The heal step must clear the
    apt cache so subsequent installs can re-fetch packages
    cleanly."""
    body = _heal_body()
    assert "apt-get clean" in body, \
        "_heal_dpkg_state must run `apt-get clean`"
    assert "apt-get update" in body, \
        "_heal_dpkg_state must refresh apt metadata via `apt-get update`"


def test_heal_does_not_use_check_true():
    """Every command in _heal_dpkg_state runs with check=False so a
    single failing step (e.g. apt-get update can't reach a mirror)
    doesn't abort the install before the main path even starts.
    The main install path has its own retry logic; heal is best-
    effort cleanup."""
    body = _heal_body()
    # Count run_command calls and their check= argument
    runs = re.findall(
        r"self\.run_command\([^)]+\)",
        body,
        re.DOTALL,
    )
    assert len(runs) >= 4, (
        "_heal_dpkg_state should run at least 4 commands "
        "(audit + clean + remove + configure + update + audit). "
        f"Found {len(runs)}."
    )
    # Every call that doesn't capture output should have check=False.
    # The two capture_output=True calls (the audit + re-audit) may
    # omit check=False because we read the output explicitly.
    for call in runs:
        is_audit = "capture_output=True" in call
        if not is_audit:
            assert "check=False" in call, (
                f"_heal_dpkg_state run_command without check=False — "
                f"a failure in heal would abort the install. Call:\n  {call}"
            )
