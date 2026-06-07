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

import re
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


# ─────────────────────────────────────── Phase 2 contracts ─────────────────


def test_install_scripts_exist_in_source():
    """The three install scripts must exist in the source tree.
    Without them the tarball workflow can't copy them in."""
    root = Path(__file__).resolve().parents[1]
    for name in ("netgen-install", "netgen-upgrade", "netgen-uninstall"):
        p = root / "scripts" / "tarball" / name
        assert p.exists(), (
            f"scripts/tarball/{name} missing. Phase 2 deliverables "
            f"include these three scripts inside the tarball's bin/."
        )
        # Must be executable in source (shebang gets rewritten in CI;
        # the executable bit comes from chmod 0755 in the workflow).
        text = p.read_text()
        assert text.startswith("#!"), (
            f"scripts/tarball/{name} doesn't start with a shebang"
        )


def test_workflow_copies_install_scripts_into_tarball():
    """Each script must be copied from scripts/tarball/ into the
    tarball's bin/ during the workflow's Assemble step."""
    src = _WORKFLOW.read_text()
    for name in ("netgen-install", "netgen-upgrade", "netgen-uninstall"):
        # The for-loop in the workflow iterates these names — the
        # variable substitution shows up as `for script in
        # netgen-install netgen-upgrade netgen-uninstall`.
        assert name in src, (
            f"Workflow doesn't copy bin/{name} into the tarball. "
            f"Without it, end users have nothing to run after extract."
        )
    assert 'cp "$src" "$dst"' in src, (
        "Workflow doesn't actually copy script files into the tarball"
    )
    assert "chmod 0755" in src, (
        "Workflow doesn't chmod the scripts executable in the tarball"
    )


def test_workflow_rewrites_shebang_to_bundled_python():
    """The shebang must point at the bundled venv's Python, not
    /usr/bin/env python3. On a target where /usr/bin/env can't find
    python3 (or finds the wrong version), env-shebang scripts would
    silently fail with a confusing error. The bundled-path shebang
    sidesteps the entire system-Python question — which is the whole
    point of v0.5.0."""
    src = _WORKFLOW.read_text()
    assert "/opt/netgen-server/netgen-venv/bin/python" in src, (
        "Workflow doesn't rewrite shebangs to the bundled venv's "
        "python path. Scripts would fall back to /usr/bin/env "
        "python3 — defeating the whole 'no system Python' design."
    )


def test_workflow_slims_venv():
    """Pre-Phase-2 the venv was 690 MB. The workflow must strip
    __pycache__, tests/, and similar artifacts so the tarball stays
    under ~150 MB compressed (Grafana-tier download size)."""
    src = _WORKFLOW.read_text()
    assert "__pycache__" in src, (
        "Workflow doesn't strip __pycache__ from the venv. Python "
        "recreates these on first import; shipping them is dead "
        "weight (~30-40% of site-packages)."
    )
    # tests/ directories in installed packages are noise
    assert re.search(r"-name tests", src) or re.search(r"-name 'tests'", src) \
        or "-name 'tests'" in src, (
        "Workflow doesn't strip `tests` directories from the venv. "
        "Scapy's tests alone are ~30 MB."
    )


def test_install_script_has_preflight_gate():
    """v0.5.0 design: refuse install on unsupported OS instead of
    half-installing and failing later. Pin the supported-OS gate so
    a refactor that drops it doesn't re-open the 'breaks on Debian'
    failure surface."""
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "tarball" / "netgen-install").read_text()
    assert "SUPPORTED_DISTROS" in src, (
        "netgen-install has no SUPPORTED_DISTROS gate — anything "
        "goes, install proceeds on whatever distro the operator "
        "ran us on."
    )
    # Ubuntu 22.04 and 24.04 are the two we currently test the
    # tarball on.
    for codename in ("22.04", "24.04"):
        assert codename in src, (
            f"netgen-install's SUPPORTED_DISTROS missing Ubuntu {codename}"
        )
    # And the pre-flight must check it.
    assert "_preflight" in src and "Unsupported OS" in src, (
        "netgen-install's pre-flight doesn't surface 'Unsupported OS' "
        "— operator gets generic failures instead of a clear refusal."
    )


def test_upgrade_script_uses_force_reinstall():
    """v0.4.8 bug class: pip silently no-ops upgrades when it thinks
    the version matches. netgen-upgrade must use --force-reinstall
    to prevent the same trap."""
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "tarball" / "netgen-upgrade").read_text()
    assert "--force-reinstall" in src, (
        "netgen-upgrade missing --force-reinstall. Same bug class "
        "as install_ostg_complete.py's v0.4.8 deps-pass silent "
        "no-op: pip won't re-resolve the dep graph without it."
    )
    # And the post-upgrade sanity import — also a v0.4.8 design.
    assert "import flask" in src or "flask" in src, (
        "netgen-upgrade has no post-upgrade import check. A bad wheel "
        "would land and the next systemd restart would crash-loop."
    )


def test_uninstall_script_refuses_unsafe_purge_paths():
    """Defensive: --purge does rmtree on the install root. If the
    operator passed --install-root pointing at /, /home, /etc, that's
    catastrophic. Whitelist: basename must contain 'netgen'."""
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "tarball" / "netgen-uninstall").read_text()
    assert "Refusing to recursively delete" in src or \
           "netgen" in src.split("rmtree")[0][-500:], (
        "netgen-uninstall --purge has no whitelist guard on the "
        "rmtree target. Operator typo on --install-root could "
        "delete /home or /etc."
    )
