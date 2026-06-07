"""Regression test for v0.5.12: every console_scripts entry-point
in netgen-venv/bin/ must have a shebang that resolves on the
operator's host.

Operator-reported on san-hp-srv06 after v0.5.11's FRR fix landed:

  $ journalctl -u netgen-server.service -n 30
  netgen-server.service: Failed to execute
    /opt/netgen-server/netgen-venv/bin/ostg-server:
    No such file or directory
  netgen-server.service: Main process exited,
    code=exited, status=203/EXEC

Exit code 203/EXEC is Linux's classic "shebang interpreter missing"
report. The script file existed; its shebang `#!/home/runner/work/
netgen/netgen/netgen-server-X.Y.Z/netgen-venv/bin/python3` pointed
at a path that only exists on the CI runner.

v0.5.7 fixed shebangs for bin/netgen-install, bin/netgen-upgrade,
bin/netgen-uninstall (the three scripts the CI workflow's step 5
explicitly rewrites). It MISSED the dozens of entry-point scripts
pip installs in netgen-venv/bin/: ostg-server, ostg-client,
netgen-cli, ostg-docker-install, pip, pip3, flask, ...

v0.5.12 fix:

  1. CI workflow step 3b walks netgen-venv/bin/, rewrites any
     shebang matching `*netgen-server-*/netgen-venv/bin/python*`
     to `#!/opt/netgen-server/netgen-venv/bin/python` (which is
     a relative symlink to ../../python-runtime/bin/python3
     thanks to v0.5.7, so it resolves at any extract location).
  2. CI verifies post-rewrite that NO scripts have
     `^#!/home/runner` shebangs.
  3. Round-trip step verifies the 4 known operator-facing
     scripts (ostg-server etc.) have an executable interpreter
     at the extract location.

The operator-side flow is unaffected: when an operator runs
netgen-upgrade, pip generates fresh shebangs based on the venv's
own python — those are already correct. Only the CI initial-build
flow needed this gate.
"""
from __future__ import annotations

import re
from pathlib import Path


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)


def test_workflow_rewrites_venv_bin_shebangs():
    """After `pip install` lands the wheel into netgen-venv, the
    workflow must walk netgen-venv/bin/* and rewrite CI-runner
    shebangs to the relocatable /opt/netgen-server path."""
    src = _WORKFLOW.read_text()
    # Must mention the rewrite step explicitly.
    assert "Rewriting CI-runner shebangs" in src or \
           re.search(r"rewriting.*shebang", src, re.IGNORECASE), (
        "Workflow doesn't have a step that rewrites shebangs in "
        "netgen-venv/bin/. Operator-side ExecStart hits 203/EXEC."
    )
    # Must iterate over netgen-venv/bin/* (not just one or two
    # named scripts — pip installs many).
    assert re.search(
        r'for\s+\w+\s+in\s+"\$ROOT/netgen-venv/bin"/\*',
        src,
    ), (
        "Workflow doesn't iterate over netgen-venv/bin/*. Naming "
        "scripts individually (ostg-server, netgen-cli, ...) "
        "would miss pip, pip3, flask, and any future entry point."
    )
    # Must match the CI-runner path pattern (netgen-server-VERSION/
    # netgen-venv/...).
    assert re.search(
        r'netgen-server-\*?/netgen-venv/bin/python',
        src,
    ), (
        "Workflow's shebang-rewrite pattern doesn't match the "
        "CI-runner path shape (netgen-server-X.Y.Z/netgen-venv/"
        "bin/python). Rewrite won't fire on the actual scripts."
    )
    # Must rewrite TO the relocatable bundled-venv python.
    assert "#!/opt/netgen-server/netgen-venv/bin/python" in src, (
        "Workflow rewrites shebangs but not to "
        "#!/opt/netgen-server/netgen-venv/bin/python. The "
        "bundled-venv python is the only path that's guaranteed "
        "to exist on the operator's host."
    )


def test_workflow_verifies_no_ci_paths_leaked():
    """After the rewrite, the workflow must explicitly check that
    NO scripts have `^#!/home/runner` shebangs. Belt-and-braces:
    catches any edge case the rewrite logic missed (e.g., a
    multi-line shebang, BOM, etc.)."""
    src = _WORKFLOW.read_text()
    assert re.search(
        r"grep\s+-l\s+'\^#!/home/runner'",
        src,
    ), (
        "Workflow doesn't grep for #!/home/runner leak. Even if "
        "the rewrite logic is correct, future pip behavior could "
        "drop a script that bypasses our case statement. Pin the "
        "post-rewrite invariant."
    )


def test_roundtrip_verifies_entry_point_shebangs_resolve():
    """The round-trip step (which extracts the tarball to a fresh
    location and re-validates) must check that the named operator-
    facing entry points have an executable interpreter at their
    shebang path. Mirrors what systemd's ExecStart will do."""
    src = _WORKFLOW.read_text()
    m = re.search(
        r"Verify tarball extracts cleanly[\s\S]+?(?=\n      - name:|\Z)",
        src,
    )
    body = m.group(0)
    # Must enumerate ostg-server at minimum (the systemd-targeted one).
    assert "ostg-server" in body, (
        "Round-trip step doesn't check the ostg-server shebang. "
        "That's the script systemd's ExecStart targets — if its "
        "shebang is broken, the service is dead on first boot."
    )
    # Must check for `^#!/home/runner` rejection.
    assert "/home/runner" in body, (
        "Round-trip step doesn't reject CI-runner-path shebangs. "
        "Without this we ship false-positive green CI for the "
        "same regression every time."
    )
    # Must verify the interpreter file is executable, not just
    # check the shebang text. A shebang pointing at a non-existent
    # interpreter passes a string-check but fails at exec(2).
    assert re.search(r'-x\s+"\$interp"|test\s+-x', body), (
        "Round-trip step doesn't `-x` test the interpreter target. "
        "A shebang can pass a string-check but still hit "
        "203/EXEC at runtime."
    )


def test_pyproject_version_at_least_0512():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 12), (
        f"Version {m.group(1)} < 0.5.12"
    )
