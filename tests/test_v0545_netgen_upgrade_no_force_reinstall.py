"""v0.5.45 — netgen-upgrade uses --upgrade, not --force-reinstall.

Operator-reported on srv06 (Jun 8 2026, v0.5.43 → v0.5.44):

  "seems when upgrading the new version it uninstalls first then
   installs again.. pls check if this is necessary?"

The wheel upgrade log showed 67 packages getting uninstalled and
reinstalled. Only 2 of them actually changed version
(ostg-trafficgen 0.5.43→0.5.44 and Flask-Cors 6.0.4→6.0.5). The
other 65 packages went X.Y.Z → X.Y.Z — identical version
re-installed for no reason. Just wasted time + disk churn + a
100+ line log of useless `Successfully uninstalled X` followed
by `Successfully installed X` pairs.

Root cause: `netgen-upgrade` ran
  pip install --force-reinstall --no-cache-dir <wheel>

`--force-reinstall` forces every dep through the
uninstall→reinstall cycle regardless of whether the installed
version satisfies the new constraint. It was added in v0.4.8
because pip would no-op on same-version installs (a lab-dev
rebuild scenario). But operator-shipped upgrades always bump
the version, so the original motivation doesn't apply.

v0.5.45 drops --force-reinstall in favour of --upgrade. Pip's
default --upgrade-strategy=only-if-needed touches only packages
whose installed version doesn't satisfy the new constraint.

For the rare "rebuilt wheel with same version" case, the
operator can pass --force-reinstall explicitly:
  sudo netgen-upgrade --force-reinstall my-rebuilt.whl

These tests pin: default uses --upgrade (no --force-reinstall),
operator can opt-in via CLI flag, --no-cache-dir is preserved.
"""
from __future__ import annotations

import re
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "tarball" / "netgen-upgrade"
)


def _main_body() -> str:
    src = _SCRIPT.read_text()
    m = re.search(
        r"def main\(\)[\s\S]+?(?=\nif __name__|\Z)",
        src,
    )
    assert m, "main() body not located in netgen-upgrade"
    return m.group(0)


def test_default_pip_command_uses_upgrade_not_force_reinstall():
    """The default pip command must use `--upgrade`, not the
    pre-fix `--force-reinstall` literal."""
    body = _main_body()
    # The else-branch (no CLI --force-reinstall) must append
    # --upgrade to pip_cmd.
    m = re.search(
        r"else:\s*\n\s+pip_cmd\.append\([\"']--upgrade[\"']\)",
        body,
    )
    assert m, (
        "Default path doesn't append --upgrade to pip_cmd. "
        "Operators will see the full uninstall+reinstall churn "
        "every upgrade."
    )


def test_force_reinstall_not_in_default_pip_cmd():
    """The original `--force-reinstall` literal must NOT appear in
    the default pip invocation. Match the pre-fix string at the
    pip_cmd construction site."""
    body = _main_body()
    # Look for the buggy literal in the immediate vicinity of pip
    # install — anywhere outside the explicit opt-in `if
    # force_reinstall:` branch.
    pip_cmd_block = re.search(
        r"pip_cmd\s*=\s*\[.*?str\(venv_pip\)[\s\S]+?_run\(pip_cmd",
        body,
    )
    assert pip_cmd_block, "pip_cmd construction block not located"
    block = pip_cmd_block.group(0)
    # `--force-reinstall` may appear ONLY inside `if force_reinstall:`.
    # Strip that branch and assert the literal is gone elsewhere.
    sanitised = re.sub(
        r"if\s+force_reinstall:[\s\S]+?else:",
        "if force_reinstall:\n    else:",
        block,
    )
    assert "--force-reinstall" not in sanitised, (
        "`--force-reinstall` appears OUTSIDE the explicit opt-in "
        "branch — would still apply on every default upgrade."
    )


def test_cli_force_reinstall_flag_supported():
    """The --force-reinstall CLI flag must still be supported for
    lab-dev rebuilds of same-version wheels. Check that argv is
    scanned for it."""
    body = _main_body()
    assert re.search(
        r'force_reinstall\s*=\s*[\"\']--force-reinstall[\"\']\s+in\s+argv',
        body,
    ), (
        "No CLI opt-in for --force-reinstall. Lab-dev rebuilds of "
        "same-version wheels would silently no-op."
    )


def test_cli_force_reinstall_branch_appends_flag():
    """When the operator passes --force-reinstall on the CLI, the
    branch must actually append it to pip_cmd. Otherwise the flag
    is parsed but never applied."""
    body = _main_body()
    # Pattern: `if force_reinstall:` followed by
    # `pip_cmd.append("--force-reinstall")`.
    assert re.search(
        r"if\s+force_reinstall:\s*\n\s+pip_cmd\.append\([\"']--force-reinstall[\"']\)",
        body,
    ), (
        "CLI --force-reinstall path doesn't append the flag to "
        "pip_cmd — flag is parsed but ineffective."
    )


def test_no_cache_dir_preserved():
    """--no-cache-dir keeps the pip cache out of /root, important
    for the bundled-venv install. Must be preserved across the
    v0.5.45 refactor."""
    body = _main_body()
    pip_cmd_m = re.search(
        r"pip_cmd\s*=\s*\[[\s\S]+?\]",
        body,
    )
    assert pip_cmd_m
    assert "--no-cache-dir" in pip_cmd_m.group(0), (
        "--no-cache-dir dropped from pip_cmd — pip cache would "
        "accumulate under /root/.cache/pip on every upgrade."
    )


def test_rationale_comment_present():
    """The non-obvious decision (drop --force-reinstall) deserves
    a comment block explaining WHY. Future maintainers will
    re-add the flag without context unless the reason is in code."""
    body = _main_body()
    assert "v0.5.45" in body, (
        "No v0.5.45 marker in the function — change isn't traceable"
    )
    # Comment must explain the motivation (e.g., operator complaint,
    # upgrade-strategy=only-if-needed).
    assert (
        "operator" in body.lower() or
        "only-if-needed" in body or
        "uninstall" in body.lower()
    ), (
        "Comment doesn't explain why --force-reinstall was dropped"
    )


def test_pyproject_version_at_least_0545():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 45), (
        f"Version {m.group(1)} < 0.5.45"
    )
