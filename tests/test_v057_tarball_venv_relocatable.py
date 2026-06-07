"""Regression test for v0.5.7: tarball venv must be relocatable
so the operator's fresh-install actually works.

Operator-reported on san-hp-srv06:

  [client] sftp put netgen-server-0.5.6-linux-x86_64.tar.gz
  [client] spawn: sudo tar -xzf ...; sudo mv .new /opt/netgen-server;
           sudo /opt/netgen-server/bin/netgen-install
  sudo: unable to execute /opt/netgen-server/bin/netgen-install:
        No such file or directory
  [client] installer exit rc=1

Root cause: `python -m venv` writes ABSOLUTE paths into pyvenv.cfg
and bin/python3 (pointing at the CI runner's filesystem). When the
operator extracts the tarball to /opt/netgen-server, the venv's
symlinks dangle, and the kernel reports the script's shebang
interpreter as "No such file or directory" — Linux's classic
mis-reporting of a missing shebang target as a missing script.

v0.5.6's round-trip CI test passed despite this because it extracted
to /tmp/tarball-roundtrip while the staging tree was still present
on the CI runner — the absolute symlink resolved through the leftover
state. False-positive.

v0.5.7 fix (in .github/workflows/build-server-tarball.yml):

  1. After `python -m venv`, sed-rewrite pyvenv.cfg's `home` to
     /opt/netgen-server/python-runtime/bin (the documented install
     location).
  2. Replace absolute bin/python3 symlinks with relative ones
     (../../python-runtime/bin/python3) — works at any extract path.
  3. Round-trip test deletes the staging tree BEFORE extracting so
     leftover state can't false-positive again.
  4. Round-trip extracts to /opt/netgen-server (matching the baked
     pyvenv.cfg) and verifies bin/netgen-install actually runs
     through the rewritten shebang.
"""
from __future__ import annotations

import re
from pathlib import Path


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)


def test_workflow_rewrites_pyvenv_cfg_home():
    """The venv's pyvenv.cfg `home` line must be sed-rewritten to
    point at /opt/netgen-server/python-runtime/bin. Without this,
    Python at startup reads `home = /home/runner/...` from the
    operator's extracted tarball — that path doesn't exist on the
    operator's host, sys.prefix is wrong, all imports fail."""
    src = _WORKFLOW.read_text()
    assert "pyvenv.cfg" in src, (
        "Workflow doesn't mention pyvenv.cfg — relocation step missing"
    )
    # FINAL_PREFIX must be set to /opt/netgen-server (the documented
    # install location). The sed must reference it together with
    # the home = ... pattern and pyvenv.cfg.
    assert 'FINAL_PREFIX="/opt/netgen-server"' in src or \
           "FINAL_PREFIX='/opt/netgen-server'" in src, (
        "FINAL_PREFIX isn't baked to /opt/netgen-server. The venv "
        "relocation needs a clearly-named install-prefix variable."
    )
    # The sed must rewrite `home = ` referencing the bundled python
    # runtime, and target pyvenv.cfg. We allow the command to span
    # multiple shell lines (line-continuation in YAML), so use DOTALL.
    assert re.search(
        r'sed\s+-i\s+.*home\s*=\s*[^"]*python-runtime/bin[\s\S]{0,200}?pyvenv\.cfg',
        src,
    ), (
        "pyvenv.cfg's `home` isn't rewritten to point at the "
        "bundled python-runtime/bin. Operator's host would inherit "
        "/home/runner/* from the CI runner — venv broken."
    )


def test_workflow_converts_absolute_python_symlinks_to_relative():
    """bin/python3 (and bin/python3.10) are written by `python -m
    venv` as ABSOLUTE symlinks. v0.5.7 must rewrite them to relative
    paths so the venv works at any extract location."""
    src = _WORKFLOW.read_text()
    # Must mention the relative symlink target.
    assert "../../python-runtime/bin/python3" in src, (
        "Workflow doesn't create relative symlinks "
        "(../../python-runtime/bin/python3). Operator's venv would "
        "still point at the CI runner's filesystem."
    )
    # Must enumerate the python/python3/python3.10 names.
    assert re.search(r"for\s+link_name\s+in\s+python3", src) or \
           re.search(r"python3.*python3\.10.*python", src), (
        "Workflow doesn't iterate over python/python3/python3.10 "
        "symlinks — only fixing one leaves the others broken."
    )


def test_workflow_validates_no_absolute_symlinks_remain():
    """After the rewrite, the workflow must explicitly check that
    NO absolute symlinks remain in netgen-venv/bin/. The v0.5.6
    CI verification passed because the existing check (line 167)
    only confirmed the symlink target CONTAINED `netgen-server*`
    — and the CI runner's path does. Need a stricter check that
    forbids any leading `/`."""
    src = _WORKFLOW.read_text()
    # Look for a case-statement or equivalent that rejects /* targets.
    assert re.search(
        r'case\s+"\$tgt"\s+in\s*\n\s*/\*\)\s*echo\s+"ERROR',
        src,
    ) or re.search(
        r'\[\s*-L\s+.*\].*readlink.*case.*\n.*/\*\).*ERROR',
        src,
        re.DOTALL,
    ), (
        "Workflow doesn't explicitly forbid absolute symlinks in "
        "netgen-venv/bin/ after the rewrite. v0.5.6's only check "
        "(target contains 'netgen-server*') accepted CI-runner "
        "absolute paths."
    )


def test_roundtrip_deletes_staging_before_extract():
    """The round-trip extract test must `rm -rf` the staging tree
    BEFORE extracting the tarball. Otherwise leftover symlink
    targets from the build steps resolve through the staging tree
    on the CI runner, and a broken absolute-path venv appears to
    work — exactly the v0.5.6 false-positive."""
    src = _WORKFLOW.read_text()
    # Find the round-trip step body.
    m = re.search(
        r"Verify tarball extracts cleanly[\s\S]+?(?=\n      - name:|\Z)",
        src,
    )
    assert m, "Round-trip step not found in workflow"
    body = m.group(0)
    # Must rm -rf the staging tree (either by $ROOT or netgen-server-*).
    assert re.search(r'rm\s+-rf\s+["\']?\$\{?ROOT\}?', body) or \
           re.search(r'rm\s+-rf\s+netgen-server-', body), (
        "Round-trip step doesn't delete the staging tree before "
        "extracting. Leftover state false-positives the test — "
        "this is exactly how the v0.5.6 srv06 bug shipped."
    )
    # And `staging/` too — that's where python-build-standalone lands.
    assert "rm -rf staging" in body, (
        "Round-trip step doesn't delete `staging/` — symlink "
        "targets could still resolve via the staging Python tree."
    )


def test_roundtrip_extracts_to_opt_netgen_server():
    """v0.5.7: round-trip test extracts to /opt/netgen-server (the
    documented install path) so it matches the baked pyvenv.cfg
    `home`. Any other extract path would invalidate the bake-in."""
    src = _WORKFLOW.read_text()
    m = re.search(
        r"Verify tarball extracts cleanly[\s\S]+?(?=\n      - name:|\Z)",
        src,
    )
    body = m.group(0)
    assert "/opt/netgen-server" in body, (
        "Round-trip step doesn't extract to /opt/netgen-server. "
        "The baked pyvenv.cfg only works at that exact path; "
        "extracting elsewhere skips the real-world test."
    )


def test_roundtrip_executes_netgen_install_through_shebang():
    """The actual operator-reported failure mode was
    `sudo /opt/netgen-server/bin/netgen-install` returning "No
    such file or directory". The round-trip MUST actually exec
    bin/netgen-install (not just import-check Python). Without
    this we'd ship another false-positive — different shape, same
    bug."""
    src = _WORKFLOW.read_text()
    m = re.search(
        r"Verify tarball extracts cleanly[\s\S]+?(?=\n      - name:|\Z)",
        src,
    )
    body = m.group(0)
    # Must invoke the script through its shebang.
    assert re.search(
        r'"\$EXTRACTED/bin/netgen-install"|/opt/netgen-server/bin/netgen-install',
        body,
    ), (
        "Round-trip doesn't actually exec bin/netgen-install — "
        "the shebang chain (the thing that failed for srv06) is "
        "untested. Need to actually run it; any non-127 exit "
        "with output proves the interpreter loaded."
    )
    # Must check for rc=127 / "no such file" (the srv06 signature).
    assert re.search(r'127|no such file', body, re.IGNORECASE), (
        "Round-trip doesn't check for rc=127 / 'no such file' — "
        "the operator-observed signature of the broken shebang."
    )


def test_pyproject_version_at_least_057():
    """Sanity check: don't ship this fix on a v0.5.6 tag."""
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert m, "pyproject.toml has no version"
    parts = [int(x) for x in m.group(1).split(".")]
    # >= 0.5.7
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 7), (
        f"Version {m.group(1)} < 0.5.7 — this fix can't be shipped "
        f"under a previously-tagged version."
    )
