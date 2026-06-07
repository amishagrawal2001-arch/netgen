"""Regression test for v0.5.8: tarball must not break on hosts
with NTP drift.

Operator-reported on san-hp-srv06 attempting v0.5.7 fresh install:

  tar: bin/netgen-install: time stamp 2026-06-07 08:06:33
       is 12.518725772 s in the future
  tar: bin/netgen-upgrade: time stamp ...
       is 12.518893021 s in the future
  ... (one warning per file in the tarball) ...
  [client] installer exit rc=3

srv06's host clock was ~15 seconds behind UTC. The tarball's
mtimes were "now" (CI runner's clock), so every file looked
"in the future" to srv06's tar. GNU tar exits non-zero on
future-timestamp warnings; the install dialog's `set -e`
aborts before the mv-into-place or netgen-install ever runs.

Two-pronged v0.5.8 fix:

  1. CI workflow bakes a DETERMINISTIC PAST mtime into every
     header — SOURCE_DATE_EPOCH from the latest git commit
     time. No operator's clock can be "behind" a past commit
     unless it's mis-set by years, and the tarball becomes
     reproducible-builds friendly as a side effect.

  2. Client install_server_dialog.py passes
     --warning=no-timestamp to tar extract. Suppresses the
     warning AND the non-zero exit. Belt-and-braces: protects
     operators using a v0.5.8+ client to install OLDER
     tarballs that were packed with "now" mtimes.

Same pattern as v0.5.7 (workflow + client both fixed): one
side hardens the artifact; the other hardens the extract.
"""
from __future__ import annotations

import re
from pathlib import Path


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)
_CLIENT = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "install_server_dialog.py"
)


def test_workflow_uses_source_date_epoch():
    """The pack step must set SOURCE_DATE_EPOCH from git's latest
    commit time. That's the reproducible-builds standard knob —
    using it AT LEAST documents the intent (vs. a hardcoded
    timestamp that'd look arbitrary)."""
    src = _WORKFLOW.read_text()
    assert "SOURCE_DATE_EPOCH" in src, (
        "Workflow doesn't reference SOURCE_DATE_EPOCH — the "
        "reproducible-builds convention. Without this we can't "
        "guarantee the tarball's mtimes are deterministic."
    )
    # Specifically from git commit time, not hardcoded.
    assert re.search(
        r"SOURCE_DATE_EPOCH=\$\(git\s+log\s+-1\s+--pretty=%ct\)",
        src,
    ), (
        "SOURCE_DATE_EPOCH isn't derived from `git log -1 "
        "--pretty=%ct`. Hardcoding a date would drift; deriving "
        "from the commit makes the tarball reproducible per-tag."
    )


def test_workflow_passes_mtime_to_tar_pack():
    """The pack step must pass --mtime to tar. Without it, files
    keep their on-disk mtime ("now" at build time), which is the
    future to any host with NTP drift."""
    src = _WORKFLOW.read_text()
    # Find the pack-tar invocation.
    m = re.search(
        r"tar\s+-czf\s+\"\$OUT\"[\s\S]+?\"\$ROOT\"",
        src,
    )
    assert m, "Pack-tar invocation not found in workflow"
    pack_cmd = m.group(0)
    assert re.search(
        r'--mtime=["\']?@\$\{?SOURCE_DATE_EPOCH\}?["\']?',
        pack_cmd,
    ), (
        "Pack tar doesn't pass --mtime=@${SOURCE_DATE_EPOCH}. "
        "Without it, on-disk mtimes (== build time) get baked "
        "into the tarball. Hosts with NTP drift see every file "
        "'in the future' and tar fails."
    )


def test_workflow_uses_sort_name_for_reproducibility():
    """When you're already baking deterministic mtimes for
    reproducibility, --sort=name should pair with it. Without
    sort, the tarball bytes vary with filesystem readdir order
    — which defeats reproducible-builds verification entirely."""
    src = _WORKFLOW.read_text()
    m = re.search(
        r"tar\s+-czf\s+\"\$OUT\"[\s\S]+?\"\$ROOT\"",
        src,
    )
    pack_cmd = m.group(0)
    assert "--sort=name" in pack_cmd, (
        "Pack tar lacks --sort=name. Reproducibility needs both "
        "stable mtimes AND stable file order; we have one but "
        "not the other."
    )


def test_client_extract_passes_warning_no_timestamp():
    """The client-side `sudo tar -xzf` must pass
    --warning=no-timestamp. This protects operators upgrading to
    a v0.5.8+ client but still using an older tarball that was
    packed with 'now' mtimes."""
    src = _CLIENT.read_text()
    # Find the install dialog's tar extract invocation.
    m = re.search(
        r'sudo tar\s+[\s\S]+?--strip-components=1[\s\S]+?-xzf\s+\{tb_name\}',
        src,
    )
    assert m, "Tar-extract invocation not found in install dialog"
    extract_cmd = m.group(0)
    assert "--warning=no-timestamp" in extract_cmd, (
        "Client extract doesn't pass --warning=no-timestamp. "
        "Operators on hosts with NTP drift hit the v0.5.7 srv06 "
        "trap: tar warns on every file → non-zero exit → "
        "set -e aborts → install never runs."
    )


def test_v058_changelog_documents_clock_skew():
    """The fix is non-obvious from the code alone — must be
    described in CHANGELOG so an operator searching for "future
    timestamp" or "rc=3" lands on the right release."""
    ch = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text()
    m = re.search(r"## \[0\.5\.8\][\s\S]+?(?=^## \[0\.5\.)", ch, re.MULTILINE)
    assert m, "CHANGELOG missing v0.5.8 section"
    body = m.group(0)
    # Must contain the key diagnostic terms an operator would search.
    assert "time stamp" in body or "timestamp" in body, (
        "v0.5.8 CHANGELOG doesn't mention timestamps — operators "
        "searching for the tar warning won't find this release."
    )
    assert "SOURCE_DATE_EPOCH" in body, (
        "v0.5.8 CHANGELOG doesn't explain the SOURCE_DATE_EPOCH "
        "fix. Future maintainers need the rationale."
    )


def test_pyproject_version_at_least_058():
    """Sanity check: don't ship this fix on a v0.5.7 tag."""
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 8), (
        f"Version {m.group(1)} < 0.5.8 — this fix can't be "
        f"shipped under a previously-tagged version."
    )
