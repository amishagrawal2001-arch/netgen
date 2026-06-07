"""Regression test for v0.4.8: installer's deps-install pass must
ACTUALLY install dependencies, and failure must be FATAL.

Operator-reported on san-hp-srv06 (fresh install on a clean Ubuntu
24.04 Noble host via the GUI client's Fresh Install tab):

  netgen-server.service: Scheduled restart job, restart counter is at 743.
  [ostg-server] Failed to import run_tgen_server: No module named 'flask'
  netgen-server.service: Main process exited, code=exited, status=2/INVALIDARGUMENT

The wheel installed (the verify phase printed `✓ ostg-server command
available`), but Flask + scapy + requests didn't. systemd then
crash-looped the server 743+ times trying to import a Flask-based
module that had no Flask.

Root cause: pre-v0.4.8 the two-pass install was:

  1. pip3 install --break-system-packages --force-reinstall --no-deps <wheel>
  2. pip3 install --break-system-packages --upgrade-strategy only-if-needed <wheel>

Step 2 was supposed to install missing deps. But `--upgrade-strategy
only-if-needed` + the wheel being already current (step 1 just
installed it) can lead pip to decide "package is at target version,
nothing to do" and skip dependency resolution entirely.

Worse: step 2 failure was treated as non-fatal WARNING. So the
installer happily reported success while the server was about to
crash-loop.

v0.4.8 fixes both:
  1. Step 2 uses `--force-reinstall` to GUARANTEE pip re-resolves
     the dep graph (already-installed deps are no-ops at the
     install layer, so the speed hit is small).
  2. Step 2 failure is now FATAL — raise SystemExit(1).
  3. Plus a post-install `python3 -c "import flask, scapy, requests"`
     sanity check that catches python-version mismatches (e.g. pip3
     installs for a different python than /usr/bin/python3).

This test pins the contract in source so a refactor that drops
--force-reinstall or downgrades the failure handling re-introduces
the san-hp-srv06 bug.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALLER = (
    Path(__file__).resolve().parents[1] / "install_ostg_complete.py"
)


def test_deps_pass_uses_force_reinstall_not_upgrade_strategy():
    """The deps pass must use --force-reinstall (which forces full
    dep-graph resolution). Pre-v0.4.8 used `--upgrade-strategy
    only-if-needed` which could silently skip dep installation when
    the wheel was already at the target version."""
    src = _INSTALLER.read_text()
    # The pre-v0.4.8 line was:
    #   f"pip3 install {pep668}--upgrade-strategy only-if-needed {remote_wheel_path}"
    # Post-v0.4.8 must use --force-reinstall.
    # Check for the actual pip COMMAND pattern, not just the
    # substring — the substring appears in the v0.4.8 fix comment
    # block, which is fine. Real commands have the `{pep668}` /
    # `{remote_wheel_path}` template substitutions; comments don't.
    assert not re.search(
        r"\{pep668\}--upgrade-strategy only-if-needed",
        src,
    ), (
        "Installer still uses `pip3 install {pep668}--upgrade-strategy "
        "only-if-needed {remote_wheel_path}` for the deps pass — that "
        "can no-op when the wheel is already current, leaving Flask + "
        "scapy + requests uninstalled. Operator-reported on "
        "san-hp-srv06 with restart counter 743+."
    )
    # The deps pass must use --force-reinstall. We verify by finding
    # the deps-install block and checking its pip command.
    m = re.search(
        r"deps_result\s*=\s*self\.run_command\([\s\S]+?check=False",
        src,
    )
    assert m, "deps_result run_command call not found"
    block = m.group(0)
    assert "--force-reinstall" in block, (
        "deps pass pip command doesn't include --force-reinstall — "
        "pip can no-op on a current install and never resolve deps."
    )


def test_deps_pass_failure_is_fatal():
    """Pre-v0.4.8 a non-zero deps-pass exit code only logged a
    WARNING and continued. v0.4.8 must raise SystemExit so a
    crash-looping server isn't deployed silently."""
    src = _INSTALLER.read_text()
    # Find the deps-install block — from `deps_result = self.run_command`
    # down to the next `def ` or other major boundary.
    m = re.search(
        r"deps_result\s*=\s*self\.run_command[\s\S]+?(?=# v0\.4\.8: post-install)",
        src,
    )
    assert m, "couldn't locate deps-install block"
    block = m.group(0)
    # Must raise SystemExit when deps install ultimately fails.
    assert "raise SystemExit(1)" in block, (
        "Deps-install failure is not fatal — installer would report "
        "success while the server crash-loops with `No module named "
        "'flask'`. Make it raise SystemExit."
    )
    # And the error log must mention 'No module named' (or equivalent
    # operator-facing context) so the failure is self-explanatory.
    assert "flask" in block.lower(), (
        "Fatal-failure log message doesn't reference the symptom "
        "(`No module named 'flask'`). Without the breadcrumb the "
        "operator has to dig through systemd logs to find out why."
    )


def test_deps_pass_has_pep668_retry_safety_net():
    """Same belt-and-suspenders the wheel install has: if PEP 668
    detection missed but pip's stderr says externally-managed,
    retry with --break-system-packages. Otherwise the deps pass
    would refuse to install on edge-case Python layouts."""
    src = _INSTALLER.read_text()
    # The deps-install block specifically (not the wheel-install one
    # that already had this retry).
    m = re.search(
        r"deps_result\s*=\s*self\.run_command[\s\S]+?(?=# v0\.4\.8: post-install)",
        src,
    )
    assert m
    block = m.group(0)
    # The retry pattern: check for externally-managed in err.lower()
    # and the --break-system-packages retry.
    assert "externally-managed" in block, (
        "Deps-install block missing PEP 668 retry. If detection "
        "misses (unconventional Python install path) the deps pass "
        "would fail without retrying."
    )
    assert "--break-system-packages" in block, (
        "Deps-install retry doesn't include --break-system-packages"
    )


def test_post_install_import_sanity_check_exists():
    """A fresh-install bug like the san-hp-srv06 one was invisible
    until the operator started the server. v0.4.8 adds an early
    `python3 -c \"import flask, scapy, requests\"` check that the
    installer runs after install completes. If imports fail, fail
    the install — better to surface during setup than during
    production startup."""
    src = _INSTALLER.read_text()
    # The check must run python3 with an import statement covering
    # the wheel's core deps. Match the actual command string.
    assert re.search(
        r'python3 -c "import flask',
        src,
    ), (
        "Post-install sanity-import check missing. Without it, a "
        "silent deps-install failure (or python-version mismatch) "
        "surfaces only when systemd starts the server and the "
        "service crash-loops."
    )
    # And the check must run AFTER the deps install. Find both
    # markers and verify ordering.
    deps_idx = src.find("Installing wheel dependencies")
    verify_idx = src.find("Verifying wheel deps are importable")
    assert deps_idx > 0 and verify_idx > 0
    assert verify_idx > deps_idx, (
        "Post-install import check runs BEFORE the deps install — "
        "ordering bug. The check exists only to catch deps install "
        "silently failing, so it must run after."
    )


def test_post_install_check_failure_is_fatal_too():
    """Same rule as the deps-install fix: if the sanity check fails,
    don't ship a broken install. Pin the SystemExit so a refactor
    that downgrades the check to WARNING reintroduces silent fails."""
    src = _INSTALLER.read_text()
    # Find the verify block. Start from `verify_cmd = (` and span
    # forward enough lines to cover the failure handler. ~40 lines
    # is plenty — the actual block is ~25 lines.
    start = src.find("verify_cmd = (")
    assert start >= 0, "verify_cmd block not found"
    # Slice ~50 lines forward.
    block = "\n".join(src[start:].splitlines()[:50])

    # Must raise SystemExit on verification failure.
    assert "raise SystemExit(1)" in block, (
        "Post-install sanity check failure isn't fatal. The whole "
        "point of the check is to catch silent install bugs — "
        "downgrading it to WARNING defeats it."
    )
    # And the error message must mention python-version mismatch as
    # the most likely real-world failure mode.
    assert "version mismatch" in block.lower() or "python" in block.lower(), (
        "Post-install fail log should hint at the most likely cause "
        "(python version mismatch between pip3 and /usr/bin/python3)"
    )


def test_no_no_deps_only_install_path_remains():
    """The wheel-install step still uses --no-deps for the fast
    artifact swap. That's fine — but it MUST be followed by the
    deps pass with --force-reinstall, not left as the only install
    step. Pin both halves present in sequence."""
    src = _INSTALLER.read_text()
    # Wheel install (step 1) — still uses --no-deps
    assert re.search(
        r"pip3 install \{pep668\}--force-reinstall --no-deps \{remote_wheel_path\}",
        src,
    ), "Step 1 (wheel install) no longer uses --force-reinstall --no-deps"
    # Deps install (step 2) — uses --force-reinstall WITHOUT --no-deps
    # so dependencies actually resolve.
    assert re.search(
        r"pip3 install \{pep668\}--force-reinstall \{remote_wheel_path\}",
        src,
    ), (
        "Step 2 (deps install) doesn't use --force-reinstall (without "
        "--no-deps). Without it, pip can no-op when the wheel is "
        "already current and never install Flask/scapy/requests."
    )
