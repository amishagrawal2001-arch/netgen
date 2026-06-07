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
    """The FIRST `pip3 install ... {remote_wheel_path}` call (the
    wheel-artifact-only install, line ~1295) must be paired with
    --no-deps so the fast artifact swap doesn't re-resolve the
    whole dependency graph.

    v0.4.8 note: pre-v0.4.8 this test enforced 'every
    --force-reinstall on a wheel install must have --no-deps'.
    v0.4.8 INTENTIONALLY broke that for the deps-install pass:
    `pip3 install {pep668}--force-reinstall {remote_wheel_path}`
    (NO --no-deps) is the fix for the san-hp-srv06 silent
    Flask-not-installed bug. Test updated to check only the FIRST
    wheel-install call still has the --no-deps pairing."""
    code = _strip_strings_and_comments(_src())
    # Find the first `pip3 install ... {remote_wheel_path}` line
    # that includes --force-reinstall. That's the wheel-install
    # step (step 1). It must still be paired with --no-deps.
    first_match = None
    for line in code.split("\n"):
        if "pip3 install" in line and "--force-reinstall" in line and \
                "{remote_wheel_path}" in line:
            first_match = line
            break
    assert first_match is not None, (
        "no `pip3 install --force-reinstall {remote_wheel_path}` "
        "line found at all — wheel install step is missing."
    )
    assert "--no-deps" in first_match, (
        "First --force-reinstall on the wheel install dropped "
        f"--no-deps. Step 1 needs --no-deps for the fast artifact "
        f"swap; the deps-install pass that follows resolves the "
        f"dep graph. Line: {first_match.strip()}"
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
    """The distutils-conflict retry branches must keep
    --force-reinstall.

    v0.4.8 note: the wheel-install retry (step 1) keeps both
    --force-reinstall + --no-deps. The deps-install retry (step 2,
    new in v0.4.8) keeps --force-reinstall WITHOUT --no-deps
    because step 2's whole point is to resolve dependencies. So we
    check `--force-reinstall` on every retry, but only require
    `--no-deps` on retries that don't also include the
    multi-line-friendly substring patterns from the deps-install
    block. Also: the f-string can be split across lines, so we
    join wrapped lines before matching."""
    code = _strip_strings_and_comments(_src())
    # Glue any sequence of consecutive lines that look like one
    # multi-line f-string command — Python's implicit string
    # concatenation lets us write
    #   f"pip3 install {pep668}--force-reinstall "
    #   f"--ignore-installed {remote_wheel_path}"
    # and have it lex as one string. Test must match accordingly.
    raw_lines = code.split("\n")
    glued = []
    i = 0
    while i < len(raw_lines):
        cur = raw_lines[i].rstrip()
        # Pull in any following lines that ALSO start with f" / "
        # at the same logical position (i.e. they're continuations
        # of the same composed string).
        j = i + 1
        while j < len(raw_lines):
            nxt = raw_lines[j].lstrip()
            if nxt.startswith(("f\"", "\"")):
                cur = cur + " " + raw_lines[j].strip()
                j += 1
            else:
                break
        glued.append(cur)
        i = j if j > i + 1 else i + 1
    retry_lines = [
        line for line in glued
        if "--ignore-installed" in line
        and "{remote_wheel_path}" in line
    ]
    assert retry_lines, "distutils-conflict retry call not found"
    for line in retry_lines:
        assert "--force-reinstall" in line, (
            f"distutils retry missing --force-reinstall — "
            f"same-version skip will re-bite. Line: {line.strip()}"
        )
        # Note: --no-deps is no longer required on every retry. The
        # wheel-install retry (step 1) has it; the deps-install
        # retry (step 2, v0.4.8) deliberately omits it so deps
        # actually get resolved on the retry too.
