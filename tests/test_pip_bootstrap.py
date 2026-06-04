"""Regression tests for the pip-bootstrap fix on Ubuntu 24.04 (Noble).

Operator hit this during fresh install:

    Attempting uninstall: packaging
        Found existing installation: packaging 24.0
    error: uninstall-no-record-file
    × Cannot uninstall packaging 24.0
    ╰─> The package's contents are unknown:
        no RECORD file was found for packaging.
    hint: The package was installed by debian.

Root cause: install_ostg_complete.py line 609 ran
``curl get-pip.py | python3.10`` which downloads the latest pip and
tries to uninstall whatever ``packaging`` is on sys.path before
installing its own. On Ubuntu 24.04 the system ships a
Debian-managed ``packaging`` 24.0 from apt — Debian packages have
no RECORD file (pip's safety check) so the uninstall step refuses,
aborting the entire pip bootstrap.

Fix: new ``_bootstrap_pip_for_python310`` method tries ensurepip
first (no network, stdlib, no Debian-uninstall trap) then falls back
to ``get-pip.py --ignore-installed`` (the ``--ignore-installed`` flag
is the key — it stops pip from trying to uninstall the Debian copy).

These tests pin the command STRINGS so anyone reverting to the
broken ``| python3.10`` pattern gets caught immediately."""
from __future__ import annotations

import re


_INSTALLER_PATH = "/Users/surajsharma/dev/netgen/install_ostg_complete.py"


def test_anti_pattern_get_pip_pipe_to_bare_python_is_gone():
    """The historical broken line was:
        curl -sS https://bootstrap.pypa.io/get-pip.py | python3.10
    With NO --ignore-installed flag, pip tries to uninstall the
    Debian-managed packaging and fails.
    This test fails the moment someone reintroduces that exact
    pattern via a copy-paste or partial revert."""
    src = open(_INSTALLER_PATH).read()
    # The broken pattern: pipe to python3.10 without anything after.
    # Match end-of-string or non-flag character after python3.10.
    broken = re.search(
        r'get-pip\.py.*\|\s*python3\.10\s*(?:"|\)|$)',
        src,
        re.MULTILINE,
    )
    assert broken is None, (
        f"Anti-pattern reintroduced — see _bootstrap_pip_for_python310 "
        f"docstring. Matched: {broken.group(0)!r}"
    )


def test_get_pip_fallback_uses_ignore_installed():
    """If get-pip.py IS used (as the fallback path), it must include
    --ignore-installed so it doesn't try to uninstall Debian's
    packaging."""
    src = open(_INSTALLER_PATH).read()
    # The fallback should pipe to ``python3.10 - --ignore-installed``
    # (the lone ``-`` tells Python the script is on stdin, anything
    # after is passed to the script as sys.argv).
    fixed = re.search(
        r'get-pip\.py.*\|\s*python3\.10\s+-\s+--ignore-installed',
        src,
        re.MULTILINE,
    )
    assert fixed is not None, (
        "Fallback get-pip.py invocation must include --ignore-installed "
        "to avoid the Debian-managed packaging uninstall trap."
    )


def test_ensurepip_is_primary_bootstrap_path():
    """ensurepip is the preferred path because it's offline, stdlib,
    and never touches the Debian packaging. Make sure the actual
    SUBPROCESS CALL to ensurepip comes before the SUBPROCESS CALL to
    get-pip.py. (Earlier version of this test compared bare-substring
    positions — the docstring mentions get-pip.py first to explain
    the historical broken path, which fooled the position check.)"""
    src = open(_INSTALLER_PATH).read()
    m = re.search(
        r"def _bootstrap_pip_for_python310\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "helper _bootstrap_pip_for_python310 not found"
    body = m.group(0)
    # Match the actual `run_command(...ensurepip...)` call vs the
    # `run_command(...get-pip.py...)` call. Both must exist and the
    # ensurepip one must come first.
    ensurepip_call = re.search(
        r"run_command\([^)]*ensurepip", body, re.DOTALL,
    )
    get_pip_call = re.search(
        r"run_command\([^)]*get-pip\.py", body, re.DOTALL,
    )
    assert ensurepip_call, "ensurepip subprocess call missing from helper"
    assert get_pip_call, "get-pip.py fallback subprocess call missing"
    assert ensurepip_call.start() < get_pip_call.start(), (
        "ensurepip subprocess call should be tried BEFORE get-pip.py "
        "(it's faster + doesn't hit the Debian-packaging-uninstall "
        "trap)"
    )


def test_helper_has_post_install_sanity_check():
    """After either bootstrap path lands, the helper must verify pip
    is actually callable from python3.10 — otherwise a partial
    bootstrap leaks into later install steps as a cryptic import
    failure."""
    src = open(_INSTALLER_PATH).read()
    m = re.search(
        r"def _bootstrap_pip_for_python310\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    body = m.group(0)
    assert "python3.10 -m pip --version" in body, (
        "Post-install sanity check missing — helper must verify "
        "`python3.10 -m pip --version` works before returning."
    )


def test_install_python_dependencies_calls_helper_not_raw_pipe():
    """The fix must replace the line in install_python_dependencies
    (the original failure site) with a call to the new helper, not
    leave the broken pattern + add the helper unused."""
    src = open(_INSTALLER_PATH).read()
    # Find install_python_dependencies
    m = re.search(
        r"def install_python_dependencies\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    body = m.group(0)
    assert "_bootstrap_pip_for_python310" in body, (
        "install_python_dependencies must call the new helper "
        "(not the historic broken pipe)."
    )
