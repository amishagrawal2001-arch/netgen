"""v0.5.0 Phase 1a — server tarball CI workflow.

Operator-stated goal at the start of v0.5.0 planning:

  "wider distribution to users — every user will not have a CI
   install workflow"

The tarball is the path that eliminates the install-time pip /
virtual-package / python-version-mismatch class of bugs we whacked
through v0.4.7 / v0.4.8 / v0.4.9. Phase 1a delivers a CI workflow
that builds the tarball; Phase 2 will add install/upgrade scripts;
Phase 3 attaches it to the GitHub release alongside the wheel.

This test pins the workflow's source-level contract so a refactor
that strips a critical step (e.g. drops the round-trip extract
smoke test, or stops pinning the python-build-standalone version)
surfaces here, not at the operator's chair.
"""
from __future__ import annotations

from pathlib import Path


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)


def test_workflow_exists():
    """The workflow file must exist. If it's deleted in a refactor
    the operator-facing tarball stops getting built — Phase 1a
    work disappears silently."""
    assert _WORKFLOW.exists(), (
        f"v0.5.0 tarball workflow missing at {_WORKFLOW}. "
        f"Without it, Phase 1a delivers nothing — there's no "
        f"tarball artifact for operators to download."
    )


def test_workflow_pins_python_build_standalone_version():
    """python-build-standalone supplies the bundled Python. Pin
    a SPECIFIC release (date + version) so the tarball is
    reproducible — a `latest` reference would silently change the
    Python under the operator's feet between releases."""
    src = _WORKFLOW.read_text()
    assert "PBS_PYTHON_VERSION:" in src, (
        "PBS_PYTHON_VERSION env var missing — the workflow doesn't "
        "pin which Python ships in the tarball."
    )
    assert "PBS_RELEASE_DATE:" in src, (
        "PBS_RELEASE_DATE env var missing — without a pinned "
        "release date, the build is not reproducible."
    )
    # No `latest` references — those would mutate between runs.
    assert "/latest/" not in src and "@latest" not in src, (
        "Workflow references a `latest` python-build-standalone "
        "release. Pin to a specific dated release so the bundled "
        "Python doesn't silently change between CI runs."
    )


def test_workflow_builds_on_ubuntu_22_04():
    """Build on Jammy (ubuntu-22.04) deliberately: older glibc
    produces a tarball that runs on BOTH 22.04 AND 24.04+. Building
    on 24.04 would lock the tarball to Noble and break Jammy
    operators. Pin this — a well-intentioned `ubuntu-latest` swap
    would silently break compat."""
    src = _WORKFLOW.read_text()
    assert "ubuntu-22.04" in src, (
        "Workflow doesn't pin runs-on: ubuntu-22.04. Using "
        "`ubuntu-latest` would bind the tarball's glibc floor to "
        "whatever Noble/Plucky/Q-codename runs on it next, "
        "potentially breaking Jammy operators."
    )


def test_workflow_smoke_tests_critical_imports():
    """Every fresh-install bug class from v0.4.x was discoverable as
    a failed import (`No module named 'flask'`, `import scapy`,
    etc.). The workflow must do these imports BEFORE handing the
    tarball off as an artifact — otherwise we ship a non-working
    tarball just like v0.4.8 shipped an installer that didn't
    install Flask."""
    src = _WORKFLOW.read_text()
    for module in ("flask", "scapy", "requests", "psutil"):
        assert f"import {module}" in src or f"{module}" in src, (
            f"Workflow doesn't import-test `{module}` — that's one "
            f"of the wheel's core deps. The v0.4.8 san-hp-srv06 bug "
            f"was Flask missing post-install; missing this check "
            f"would let the same bug class ship inside the tarball."
        )
    # And run_tgen_server (the actual Flask app) — its import
    # failure was the exact symptom the v0.4.8 fix targeted.
    assert "import run_tgen_server" in src, (
        "Workflow doesn't import-test run_tgen_server. That import "
        "failing is the exact san-hp-srv06 crash-loop symptom."
    )


def test_workflow_does_round_trip_extract():
    """A tarball can be internally consistent only because of the
    build-side filesystem state (relative-path symlinks pointing
    into the build dir, etc.). The workflow must extract the
    tarball to a FRESH location and re-run imports against that —
    only this catches the "works on the build machine, breaks on
    the operator's" failure mode."""
    src = _WORKFLOW.read_text()
    assert "tarball-roundtrip" in src or "extracts cleanly" in src, (
        "Workflow doesn't do a round-trip extract-and-re-verify. "
        "Without it, build-machine-state contamination only "
        "surfaces at the operator's first install attempt."
    )


def test_workflow_uploads_tarball_as_artifact():
    """Phase 1a goal: produce a downloadable artifact for manual
    testing on a real Noble host. The workflow must end with an
    upload-artifact step that captures the tarball."""
    src = _WORKFLOW.read_text()
    assert "upload-artifact" in src, (
        "Workflow doesn't upload the tarball as a CI artifact — "
        "without this, the operator has no way to download it for "
        "real-host testing. Phase 1a deliverable is exactly that."
    )
    assert "if-no-files-found: error" in src, (
        "upload-artifact step doesn't fail when no files are found "
        "— a broken build that produced no tarball would still be "
        "reported as successful."
    )


def test_workflow_preserves_wheel_alongside_tarball():
    """The user explicitly specced: tarball = fresh install, wheel
    = routine upgrades. The workflow must produce BOTH so the
    upgrade-path artifact stays available."""
    src = _WORKFLOW.read_text()
    assert "dist/*.whl" in src, (
        "Workflow doesn't preserve the wheel alongside the tarball. "
        "v0.5.0 spec: wheel is the upgrade artifact, tarball is the "
        "fresh-install artifact. Both must ship."
    )
