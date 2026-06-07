"""v0.5.0 Phase 3 — GUI tarball-install dispatch + release.yml wiring.

Phase 1a built the tarball in CI. Phase 2 added the install scripts
inside it. Phase 3 wires the operator-facing pieces:

  1. SshInstallWorker accepts a `tarball_path` kwarg. When set,
     uploads ONE tarball (no wheel + installer + supporting tree)
     and spawns `tar -xzf ... && /opt/netgen-server/bin/netgen-
     install` instead of `python3 install_ostg_complete.py`.

  2. The Fresh Install file picker accepts .whl AND .tar.gz. The
     dispatcher infers install path from the file extension.

  3. The validation that demands install_ostg_complete.py is now
     skipped for tarball-install (the tarball ships its own
     install scripts).

  4. build-server-tarball.yml attaches the tarball to the GitHub
     release on v* tag pushes — alongside release.yml's wheel /
     .dmg / .exe / .AppImage.

Pin all four contracts in source so a refactor that rolls back any
of them surfaces here, not at the next "fresh install on a new
server" failure.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest


_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "install_server_dialog.py"
)
_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)


def test_ssh_install_worker_accepts_tarball_path():
    """SshInstallWorker.__init__ must accept a `tarball_path` kwarg.
    Without it, the GUI client has no way to dispatch the tarball
    flow — operator's only path is still install_ostg_complete.py."""
    from widgets.install_server_dialog import SshInstallWorker
    sig = inspect.signature(SshInstallWorker.__init__)
    assert "tarball_path" in sig.parameters, (
        "SshInstallWorker.__init__ doesn't accept tarball_path — the "
        "GUI client can't drive v0.5.0 tarball installs."
    )
    # And the default must be None so existing wheel-install
    # callsites still work.
    assert sig.parameters["tarball_path"].default is None, (
        "tarball_path default isn't None — existing wheel-install "
        "callsites would break."
    )


def test_tarball_branch_skips_wheel_and_installer_uploads():
    """When tarball_path is set, the worker must NOT also upload
    the wheel / installer / supporting files. Operator-reported
    pattern across v0.4.x: redundant uploads stretched the install
    over ~5 minutes of SFTP; tarball install is one file."""
    src = _DIALOG.read_text()
    # Find the worker's run() block — specifically the bit where
    # we decide between tarball vs wheel upload.
    assert "if self.tarball_path:" in src, (
        "No tarball-branching condition in SshInstallWorker. Code "
        "would upload the wheel even when tarball_path is set."
    )
    # The tarball branch must close the sftp session early —
    # nothing else needs uploading.
    m = re.search(
        r"if self\.tarball_path:[\s\S]+?(?=^\s{16}else:)",
        src,
        re.MULTILINE,
    )
    assert m, "tarball branch / else split not found"
    branch = m.group(0)
    assert "sftp.put(self.tarball_path" in branch, (
        "Tarball branch doesn't sftp the tarball itself"
    )


def test_tarball_branch_spawns_netgen_install_not_install_ostg():
    """The spawn command for tarball installs must invoke
    /opt/netgen-server/bin/netgen-install, NOT
    python3 install_ostg_complete.py. The whole point of v0.5.0
    is that operator-side install runs entirely off the bundled
    venv — no system pip."""
    src = _DIALOG.read_text()
    # The installer_invocation block must branch on self.tarball_path
    # and produce a tar-and-run command.
    m = re.search(
        r"if self\.tarball_path:\s*\n[\s\S]+?installer_invocation\s*=\s*\(",
        src,
    )
    assert m, "tarball-aware installer_invocation branch not found"
    # The tarball install command must extract + run the bundled script
    assert "tar --strip-components=1 -xzf" in src, (
        "Tarball spawn command missing tar -xzf — it should extract "
        "the tarball before running netgen-install."
    )
    assert "/opt/netgen-server/bin/netgen-install" in src, (
        "Tarball spawn command doesn't invoke "
        "/opt/netgen-server/bin/netgen-install. The bundled script "
        "is the whole point — without it we're not actually using "
        "the tarball, just the wheel inside it."
    )


def test_file_picker_accepts_tarball_extension():
    """The file picker that drives the Fresh Install field must
    surface .tar.gz as a valid extension. Otherwise operators have
    to use the "All files" filter and the warning fires."""
    src = _DIALOG.read_text()
    # Find the QFileDialog call in _browse_wheel.
    m = re.search(
        r"def _browse_wheel\(self[\s\S]+?QFileDialog\.getOpenFileName\([\s\S]+?\)",
        src,
    )
    assert m, "_browse_wheel QFileDialog call not found"
    call = m.group(0)
    assert ".tar.gz" in call, (
        "_browse_wheel's file filter doesn't include .tar.gz — "
        "operators can't easily pick the v0.5.0 server tarball."
    )
    assert ".whl" in call, (
        "_browse_wheel's file filter dropped .whl entirely — that "
        "breaks routine wheel upgrades."
    )


def test_validation_skips_installer_path_for_tarball():
    """Tarball installs don't need install_ostg_complete.py as a
    second file (the scripts are inside the tarball). Pre-fix the
    validation HARD-required it; v0.5.0 must skip when the chosen
    file is a .tar.gz."""
    src = _DIALOG.read_text()
    # The relaxed validation block must check the extension before
    # demanding the installer path.
    assert re.search(
        r"\.tar\.gz['\"]?\s*\)?\s*$.*\n.*if not _is_tarball_check",
        src, re.MULTILINE,
    ) or (
        "_is_tarball_check" in src
        and "Pick install_ostg_complete.py" in src
        and "Not needed for server-tarball" in src
    ), (
        "Fresh Install validation still hard-requires "
        "install_ostg_complete.py for tarball installs. Operator "
        "would have to pass a dummy file or the dialog refuses."
    )


def test_dispatcher_routes_tar_gz_to_tarball_path():
    """The button-handler must split the file-picker's single output
    into wheel_path vs tarball_path based on extension. Pin that
    routing so a refactor doesn't accidentally drop tar.gz files
    into wheel_path (which would fail on the remote pip)."""
    src = _DIALOG.read_text()
    # Find the SshInstallWorker construction inside the click
    # handler. Match the OUTER paren — track depth so nested
    # parens (e.g. `("" if is_tarball else wheel)`) don't end the
    # match early.
    start = src.find("self._worker = SshInstallWorker(")
    assert start >= 0, "SshInstallWorker construction not found"
    depth = 0
    end = -1
    for i in range(start, len(src)):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end > start
    block = src[start:end]
    # Must reference both wheel_path and tarball_path, and the
    # routing must use the .tar.gz extension test.
    assert "tarball_path" in block, (
        "Worker construction doesn't pass tarball_path — dispatch "
        "regressed to wheel-only."
    )
    assert "is_tarball" in src or ".tar.gz" in block, (
        "No extension-based routing logic before worker construction"
    )


# ────────────────────────── release.yml wiring ──────────────────────


def test_workflow_triggers_on_v_tags():
    """build-server-tarball.yml must trigger on v0.5.* tags
    (and forward versions). Without this, v0.5.0 releases don't
    get the tarball asset attached."""
    src = _WORKFLOW.read_text()
    # The tags trigger must include v0.5.* (and equivalent for
    # forward versions). Match the most-specific tag pattern.
    assert "tags:" in src, "Workflow has no tags trigger"
    assert "v0.5.*" in src, (
        "Workflow doesn't fire on v0.5.* tags — the first v0.5.0 "
        "release won't get the tarball attached. release.yml's "
        "wheel/.dmg/.exe/.AppImage attach, but the tarball that "
        "v0.5.0 SHIPS doesn't show up on the release page."
    )


def test_workflow_attaches_tarball_to_github_release_on_tag():
    """The Attach step must use softprops/action-gh-release@v2 and
    must be gated on `startsWith(github.ref, 'refs/tags/v')` so it
    only fires on tag pushes (not workflow_dispatch / branch pushes
    used for development testing)."""
    src = _WORKFLOW.read_text()
    # Must include the release-attach step
    assert "softprops/action-gh-release" in src, (
        "Workflow doesn't use softprops/action-gh-release to attach "
        "to the GH release. The tarball would only ever be a CI "
        "artifact, not a downloadable release asset."
    )
    # Must be conditional on tag pushes
    assert re.search(
        r"if:\s*startsWith\(github\.ref,\s*['\"]refs/tags/v['\"]\)",
        src,
    ), (
        "Workflow's release-attach step isn't gated on tag pushes — "
        "branch-push test runs would try (and fail) to attach to "
        "a release that doesn't exist."
    )
    # And the files glob must match what the build step produced.
    assert re.search(
        r"files:\s*netgen-server-\*-linux-x86_64\.tar\.gz",
        src,
    ), (
        "Release-attach step's files glob doesn't match the tarball "
        "filename produced earlier in the workflow."
    )


def test_workflow_does_not_clobber_release_body():
    """release.yml owns the release body (CHANGELOG-driven notes).
    The tarball workflow must NOT generate or append body content
    or it'd race with release.yml and stomp on those notes."""
    src = _WORKFLOW.read_text()
    # generate_release_notes must be false in the tarball workflow
    # (release.yml already generates them).
    assert "generate_release_notes: false" in src, (
        "Workflow has generate_release_notes != false — would race "
        "with release.yml to stamp the release body."
    )
    assert "append_body: false" in src, (
        "Workflow doesn't set append_body: false — would tack the "
        "tarball workflow's body content onto release.yml's notes."
    )
