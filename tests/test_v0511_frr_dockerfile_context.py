"""Regression test for v0.5.11: FRR Docker build context.

Operator-reported on san-hp-srv06 after v0.5.10 cleared the
preflight gate:

  [4/7] COPY frr.conf.template /etc/frr/frr.conf.template
  ERROR: failed to calculate checksum of ref ...:
    "/frr.conf.template": not found
  [6/7] COPY start-frr.sh /usr/local/bin/start-frr.sh
  ERROR: failed to calculate checksum of ref ...:
    "/start-frr.sh": not found
  [WARNING] [FRR] Docker build failed: ... non-zero exit status 1.

The v0.5.0 CI workflow copied Dockerfile.frr to share/netgen/ root
AND copied ostg_docker/ as a subtree:

  share/netgen/Dockerfile.frr            ← top-level copy
  share/netgen/ostg_docker/Dockerfile.frr   ← same file
  share/netgen/ostg_docker/frr.conf.template
  share/netgen/ostg_docker/start-frr.sh

netgen-install was using share/netgen/Dockerfile.frr with
share/netgen/ as the build context. But the Dockerfile's COPY
directives reference sibling files (frr.conf.template, start-frr.sh)
that live in ostg_docker/, not at the build-context root. Every
COPY failed.

v0.5.11 fix:

  1. netgen-install._build_frr_image() uses share/netgen/ostg_docker/
     as both -f source AND build context. All 3 files coexist there.
  2. CI round-trip step parses Dockerfile.frr's COPY directives and
     verifies every referenced sibling file exists in the build
     context — catches the regression class statically without
     needing to actually run docker build.
  3. netgen-install's _verify_running() dumps journalctl + port-5050
     occupant + legacy ostg-server.service status when /api/health
     times out, so operators don't need a second ssh round-trip to
     diagnose the most common cause (legacy v0.4.x service still
     bound to :5050).
"""
from __future__ import annotations

import re
from pathlib import Path


_NETGEN_INSTALL = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "tarball" / "netgen-install"
)
_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)
_DOCKERFILE_FRR = (
    Path(__file__).resolve().parents[1] / "ostg_docker" / "Dockerfile.frr"
)


def test_build_frr_image_uses_ostg_docker_subdir_as_context():
    """netgen-install._build_frr_image() must use the ostg_docker/
    subdir as the build context. share/netgen/ alone doesn't have
    the sibling files Dockerfile.frr's COPY directives reference."""
    src = _NETGEN_INSTALL.read_text()
    m = re.search(
        r"def _build_frr_image[\s\S]+?(?=^def |\Z)",
        src,
        re.MULTILINE,
    )
    assert m, "_build_frr_image function not found"
    body = m.group(0)
    # Must reference ostg_docker/ as the preferred context path.
    assert "ostg_docker" in body, (
        "_build_frr_image doesn't reference ostg_docker/ — but "
        "that's where Dockerfile.frr's COPY siblings live. "
        "Without using ostg_docker/ as context, the build fails "
        "at the first COPY directive."
    )
    # Must check the siblings exist before declaring ostg_docker/
    # usable (so a corrupted tarball doesn't surface as a confusing
    # docker error).
    assert "frr.conf.template" in body, (
        "_build_frr_image doesn't validate frr.conf.template exists "
        "in the context. Add a precondition check that catches the "
        "regression at install time, not at docker build time."
    )
    assert "start-frr.sh" in body, (
        "_build_frr_image doesn't validate start-frr.sh exists in "
        "the context."
    )


def test_verify_running_dumps_diagnostics_on_timeout():
    """When /api/health times out, _verify_running() must dump
    actionable diagnostics into the install log. Without this,
    operators have to re-ssh to figure out why the service didn't
    come up — the install log just says 'did not respond within 60s'.
    """
    src = _NETGEN_INSTALL.read_text()
    m = re.search(
        r"def _verify_running[\s\S]+?(?=^def |\Z)",
        src,
        re.MULTILINE,
    )
    assert m, "_verify_running function not found"
    body = m.group(0)
    # Must invoke journalctl on timeout.
    assert "journalctl" in body, (
        "_verify_running doesn't dump journalctl on timeout. "
        "Operator only sees 'did not respond' — has to ssh in to "
        "figure out the actual error."
    )
    # Must check who's holding port 5050 (the most common reason
    # for the service to be 'started' but unreachable).
    assert re.search(r":5050|sport.*5050|5050", body), (
        "_verify_running doesn't check who's holding port 5050. "
        "Most v0.4.x → v0.5.x migrations fail here because legacy "
        "ostg-server.service is still active on the port."
    )
    # Must explicitly check for the legacy service.
    assert "ostg-server.service" in body, (
        "_verify_running doesn't check for legacy ostg-server.service. "
        "On v0.4.x migrations, that's almost always the blocker."
    )
    # And must surface a remediation hint with the exact command.
    assert "disable" in body and "ostg-server" in body, (
        "_verify_running doesn't give a remediation command. "
        "Operator should see the exact fix in the install log."
    )


def test_workflow_validates_frr_build_context():
    """The CI workflow must explicitly verify that every file
    Dockerfile.frr COPYs from the build context actually exists in
    that context. Without this static check, future additions to
    Dockerfile.frr (e.g., a new COPY config.yml ...) silently break
    the install for the next operator."""
    src = _WORKFLOW.read_text()
    # Find the round-trip step.
    m = re.search(
        r"Verify tarball extracts cleanly[\s\S]+?(?=\n      - name:|\Z)",
        src,
    )
    body = m.group(0)
    # Must check ostg_docker/ is the FRR context path.
    assert "ostg_docker" in body, (
        "Round-trip step doesn't check the ostg_docker/ FRR build "
        "context. The runtime install will fail on Docker build."
    )
    # Must enumerate the three known FRR siblings explicitly.
    for sibling in ("Dockerfile.frr", "frr.conf.template", "start-frr.sh"):
        assert sibling in body, (
            f"Round-trip step doesn't pin {sibling} as a required "
            f"FRR build-context file."
        )
    # Must parse Dockerfile.frr to catch future additions (not just
    # the 3 hardcoded ones).
    assert re.search(r"grep.*COPY.*Dockerfile\.frr", body), (
        "Round-trip step doesn't parse COPY directives out of "
        "Dockerfile.frr. A new sibling added in a future commit "
        "would silently break."
    )


def test_frr_dockerfile_copy_directives_reference_known_siblings():
    """Belt-and-braces: as long as Dockerfile.frr is in the source
    tree, any COPY src must be an existing sibling. Catches the
    'someone edited the Dockerfile to add a new COPY but forgot to
    create the file' regression at the source-tree level — before
    CI even runs."""
    src = _DOCKERFILE_FRR.read_text()
    siblings = {
        p.name for p in _DOCKERFILE_FRR.parent.iterdir() if p.is_file()
    }
    for m in re.finditer(r"^\s*COPY\s+(\S+)\s+\S+", src, re.MULTILINE):
        ref = m.group(1)
        # Skip absolute paths (those are in-image refs not sibling
        # files) and --from=... mounts.
        if ref.startswith("/") or ref.startswith("--"):
            continue
        # The ref is relative to the build context, which IS the
        # ostg_docker/ dir.
        assert ref in siblings, (
            f"Dockerfile.frr references COPY {ref!r} but no sibling "
            f"with that name exists in {_DOCKERFILE_FRR.parent}. "
            f"Either add the file or fix the Dockerfile."
        )


def test_pyproject_version_at_least_0511():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 11), (
        f"Version {m.group(1)} < 0.5.11"
    )
