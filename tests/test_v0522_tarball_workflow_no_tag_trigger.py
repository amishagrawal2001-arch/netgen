"""Regression test for v0.5.22: the server-tarball CI workflow
must NOT auto-trigger on tag pushes.

Operator request: "do not generate tar.gz at every release".

Rationale:
  * Tarball is ~196 MB; per-tag build costs ~5 min of CI
  * Tarball is only consumed by fresh installs; existing installs
    upgrade via wheel — they never touch the tarball
  * Most releases in the v0.5.x cascade today (v0.5.6 → v0.5.21)
    changed code bundled in the wheel, not the tarball-only assets
    (bundled CPython, install scripts, FRR Docker context)
  * Rebuilding the tarball for each tag was waste

v0.5.22 dropped the tag trigger from build-server-tarball.yml.
What's left:
  * workflow_dispatch (manual via `gh workflow run` or Actions UI)
  * push to claude/** branches with path filter on
    scripts/tarball/**, the workflow file, or pyproject.toml

So operators wanting a fresh tarball for a tag:
  gh workflow run build-server-tarball.yml --ref v0.5.22

These tests pin: tag trigger gone, manual dispatch + branch
trigger preserved.
"""
from __future__ import annotations

import re
from pathlib import Path


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)


def test_workflow_has_no_tag_trigger():
    """The `tags:` filter must not appear anywhere active in the
    workflow. Comments referencing the historical trigger for
    changelog context are fine; an active YAML key is not."""
    src = _WORKFLOW.read_text()
    # Walk YAML lines, skip comment-only lines.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Look for `tags:` as a YAML key — followed by either a
        # value or a newline (block start).
        if re.match(r'^tags\s*:\s*$', stripped) or \
           re.match(r"^tags\s*:\s*\[", stripped):
            raise AssertionError(
                f"build-server-tarball.yml still has an active "
                f"`tags:` trigger:\n  {line!r}\n"
                f"v0.5.22 dropped this — tarball should only build "
                f"via workflow_dispatch or branch-push-with-paths."
            )


def test_workflow_keeps_workflow_dispatch():
    """Manual dispatch must remain — operators need a way to build
    a tarball for a specific tag when they actually want one."""
    src = _WORKFLOW.read_text()
    assert "workflow_dispatch:" in src, (
        "build-server-tarball.yml dropped workflow_dispatch — "
        "operators now have NO way to build a tarball on demand."
    )


def test_workflow_keeps_branch_push_with_path_filter():
    """The dev-loop trigger (push to claude/** when tarball-relevant
    files change) is what catches script breakage in CI before a
    release. Must stay."""
    src = _WORKFLOW.read_text()
    # Must trigger on push.
    assert "push:" in src, (
        "build-server-tarball.yml lost its `push:` trigger entirely."
    )
    # Must have a paths filter that includes scripts/tarball/**
    assert "scripts/tarball/**" in src, (
        "Path filter doesn't include scripts/tarball/** — script "
        "edits during dev wouldn't trigger CI test of the tarball "
        "build."
    )
    # Must include the workflow file itself + pyproject.toml.
    assert "build-server-tarball.yml" in src, (
        "Path filter doesn't include the workflow itself — edits "
        "to the workflow couldn't be tested without manual dispatch."
    )


def test_workflow_comment_explains_why_tag_dropped():
    """Future maintainers will wonder why this workflow doesn't
    follow the project's other auto-tag-trigger pattern (release.yml
    still does). The change deserves a comment explaining the
    rationale + the manual-dispatch escape hatch."""
    src = _WORKFLOW.read_text()
    assert "do not generate tar.gz at every release" in src or \
           "Operator request" in src, (
        "Workflow doesn't explain WHY the tag trigger was dropped. "
        "Future maintainers will re-add it without context."
    )
    assert "workflow_dispatch" in src or "gh workflow run" in src, (
        "Comment doesn't show operators HOW to build a tarball "
        "on demand. Without that, the convenience of the dropped "
        "trigger becomes a usability cliff."
    )


def test_release_yml_still_runs_on_tags():
    """The OTHER release workflow (wheel + .dmg + .exe + .AppImage)
    must still auto-build on tags. That's where most of the release
    value lives — only the tarball is opt-in."""
    release_yml = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "release.yml"
    )
    if not release_yml.exists():
        # Different filename in this repo? Search likely candidates.
        candidates = list(
            (Path(__file__).resolve().parents[1] / ".github" / "workflows")
            .glob("release*.yml")
        )
        assert candidates, "No release*.yml workflow found"
        release_yml = candidates[0]
    src = release_yml.read_text()
    assert "tags:" in src, (
        f"{release_yml.name} no longer triggers on tags — wheel + "
        f"clients won't be built per release. (v0.5.22 only meant "
        f"to drop the TARBALL workflow's tag trigger.)"
    )


def test_pyproject_version_at_least_0522():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 22), (
        f"Version {m.group(1)} < 0.5.22"
    )
