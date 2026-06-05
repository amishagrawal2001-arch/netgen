"""Regression test: same-version wheel rebuild must actually replace
the installed package.

Operator scenario:
    1. Server has v0.3.16 wheel installed.
    2. Developer rebuilds the wheel from updated source (same
       version string, new contents — "Option A" rebuild path).
    3. Fresh Install re-uploads the wheel and runs pip.

Pre-fix bug:
    ``pip3 install /path/to/foo-0.3.16-py3-none-any.whl``
    resolves the wheel filename to ``foo==0.3.16``, sees
    ``foo==0.3.16`` already installed, prints "Requirement
    already satisfied", and does NOTHING. The new wheel
    contents are silently discarded.

Fix:
    Pass ``--force-reinstall --no-deps`` for the primary install
    so the wheel content is unconditionally swapped, then a
    separate deps-only pass (no --force-reinstall, no --no-deps)
    handles dependency installation on fresh hosts.

This test pins both invocations so the trap can't reopen."""
from __future__ import annotations

import re

_INSTALLER = "/Users/surajsharma/dev/netgen/install_ostg_complete.py"


def _src():
    return open(_INSTALLER).read()


def _strip_strings_and_comments(src):
    """Drop docstrings + comments so we don't false-match on
    explanatory text. Triple-quoted strings get removed first,
    then `#`-to-EOL comments. Single-quoted/double-quoted strings
    are left alone (they contain the actual pip commands)."""
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    out = re.sub(r"'''[\s\S]*?'''", "", out)
    out = "\n".join(
        line.split("#", 1)[0] for line in out.split("\n")
    )
    return out


def test_primary_wheel_install_uses_force_reinstall():
    """The primary wheel install must pass --force-reinstall so a
    same-version rebuild actually replaces the installed package.
    Without this flag, pip says 'Requirement already satisfied' and
    silently skips — operator's server keeps running stale code."""
    code = _strip_strings_and_comments(_src())
    # Find pip3 install lines that pass a wheel path (not a package name)
    wheel_install_calls = re.findall(
        r'pip3 install[^"\']*\{remote_wheel_path\}',
        code,
    )
    assert wheel_install_calls, (
        "no `pip3 install ... {remote_wheel_path}` call found in installer"
    )
    # At least one of these calls must have --force-reinstall
    force_reinstall_calls = [
        c for c in wheel_install_calls if "--force-reinstall" in c
    ]
    assert force_reinstall_calls, (
        "no pip3 install of the wheel uses --force-reinstall — "
        "same-version rebuilds will be silently skipped by pip. "
        f"Found these wheel install calls: {wheel_install_calls}"
    )


def test_force_reinstall_paired_with_no_deps():
    """--force-reinstall reinstalls the wheel; --no-deps avoids
    re-resolving the entire dependency graph (which is slow and
    can trigger distutils-uninstall errors on OS-managed packages).
    These flags must travel together on the primary install."""
    code = _strip_strings_and_comments(_src())
    # Every --force-reinstall on a wheel install must be paired
    # with --no-deps.
    for line in code.split("\n"):
        if "--force-reinstall" not in line:
            continue
        if "{remote_wheel_path}" not in line:
            continue
        assert "--no-deps" in line, (
            f"--force-reinstall without --no-deps on wheel install — "
            f"will needlessly re-resolve deps + risk distutils "
            f"conflicts. Line: {line.strip()}"
        )


def test_separate_deps_only_pass_exists():
    """The first-install case still needs deps resolved. A second
    pip invocation (without --no-deps, without --force-reinstall)
    handles that — idempotent on already-installed hosts, fresh
    install on bare hosts."""
    code = _strip_strings_and_comments(_src())
    # Look for a pip3 install of the wheel that does NOT have
    # --no-deps (the deps-resolving pass).
    deps_passes = [
        line for line in code.split("\n")
        if "pip3 install" in line
        and "{remote_wheel_path}" in line
        and "--no-deps" not in line
    ]
    assert deps_passes, (
        "no deps-only install pass found — fresh hosts will install "
        "the wheel with --no-deps and end up missing transitive "
        "dependencies. Expected a second pip3 install of the wheel "
        "without --no-deps."
    )


def test_distutils_retry_preserves_force_reinstall():
    """The distutils-conflict retry branch must keep the
    --force-reinstall + --no-deps flags. Pre-existing retry was
    `pip3 install --ignore-installed <wheel>` — that loses the
    force-reinstall semantics and re-triggers the same-version-
    skip behavior."""
    code = _strip_strings_and_comments(_src())
    # Find the retry call — has --ignore-installed
    retry_lines = [
        line for line in code.split("\n")
        if "--ignore-installed" in line
        and "{remote_wheel_path}" in line
    ]
    assert retry_lines, "distutils-conflict retry call not found"
    for line in retry_lines:
        assert "--force-reinstall" in line, (
            f"distutils retry missing --force-reinstall — "
            f"same-version skip will re-bite. Line: {line.strip()}"
        )
        assert "--no-deps" in line, (
            f"distutils retry missing --no-deps — will needlessly "
            f"re-resolve deps. Line: {line.strip()}"
        )
