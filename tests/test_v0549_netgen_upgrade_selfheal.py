"""v0.5.49 — wheel-bundled netgen-upgrade self-heals the installed
copy at /opt/netgen-server/bin/netgen-upgrade on server startup.

Operator-reported on srv06 (Jun 9 2026), after upgrading v0.5.47
→ v0.5.48 via the admin UI:

  "seem upgrade still doing uninstall and install."
  [INFO] $ /opt/netgen-server/netgen-venv/bin/pip install \
         --force-reinstall --no-cache-dir <wheel>
  Attempting uninstall: pytz ... (65 packages)

The v0.5.45 fix (drop `--force-reinstall` → use `--upgrade`)
lived only in `scripts/tarball/netgen-upgrade` in the source
repo. It shipped in the wheel as a static file, but the wheel
install only writes into the venv's site-packages — it never
touches `/opt/netgen-server/bin/netgen-upgrade`. So even though
v0.5.48 was installed, the actual upgrade-driver script was
still the months-old tarball-install-time copy with
--force-reinstall.

v0.5.49 closes the gap:

  1. `resources/tarball/netgen-upgrade` is now a wheel package
     data file — shipped alongside the Python code.
  2. `_ensure_netgen_upgrade_script_deployed()` runs at server
     startup, SHA-256 compares the bundled copy against
     `/opt/netgen-server/bin/netgen-upgrade`, and replaces (with
     backup) when they differ.
  3. A sync test pins that `scripts/tarball/netgen-upgrade` and
     `resources/tarball/netgen-upgrade` stay byte-identical.

Catch-22: the v0.5.48 → v0.5.49 upgrade itself still runs the
OLD --force-reinstall script (this fix can't apply to the
upgrade that delivers it). Future upgrades v0.5.49 → vX will be
clean because the self-heal runs in the v0.5.49 server process
AFTER restart, refreshing the script for next time.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVER = _REPO_ROOT / "run_tgen_server.py"


def test_bundled_netgen_upgrade_exists_in_resources():
    """The wheel must ship `resources/tarball/netgen-upgrade`.
    Without it, the self-heal helper has nothing to copy."""
    bundled = _REPO_ROOT / "resources" / "tarball" / "netgen-upgrade"
    assert bundled.is_file(), (
        f"Expected wheel-bundled copy at {bundled} — missing. "
        f"Self-heal has nothing to deploy."
    )
    assert bundled.stat().st_size > 1000, (
        "Bundled netgen-upgrade is suspiciously small — sync "
        "from scripts/tarball/ may have failed."
    )


def test_resources_tarball_is_a_package():
    """`resources/tarball/__init__.py` makes it a discoverable
    package so importlib.resources.files() can locate the
    bundled script."""
    init = _REPO_ROOT / "resources" / "tarball" / "__init__.py"
    assert init.is_file(), (
        "resources/tarball/__init__.py missing — importlib."
        "resources.files('resources.tarball') would fail."
    )


def test_scripts_and_resources_copies_are_byte_identical():
    """The two copies must stay in sync. If scripts/tarball/
    netgen-upgrade is the dev source-of-truth (used by the
    tarball builder), and resources/tarball/netgen-upgrade is
    the wheel-bundled copy (used by self-heal), they MUST match
    byte-for-byte. A future edit that touches only one will
    cause silent drift — the dev shell sees the new behaviour,
    but the running server self-heals to the OLD copy."""
    scripts_copy = _REPO_ROOT / "scripts" / "tarball" / "netgen-upgrade"
    resources_copy = _REPO_ROOT / "resources" / "tarball" / "netgen-upgrade"
    assert scripts_copy.is_file(), f"missing {scripts_copy}"
    assert resources_copy.is_file(), f"missing {resources_copy}"
    a = scripts_copy.read_bytes()
    b = resources_copy.read_bytes()
    assert a == b, (
        f"scripts/tarball/netgen-upgrade ({len(a)} bytes) and "
        f"resources/tarball/netgen-upgrade ({len(b)} bytes) "
        f"differ. Future edits must update BOTH copies."
    )


def test_pyproject_lists_resources_tarball_as_package_data():
    """`resources.tarball` package data must be declared in
    pyproject.toml or the wheel won't actually include the
    script."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text()
    assert re.search(
        r'["\']resources\.tarball["\']\s*=\s*\[[^\]]*netgen-upgrade',
        pyproject,
    ), (
        "pyproject.toml [tool.setuptools.package-data] doesn't "
        "include `resources.tarball = [\"netgen-upgrade\", ...]`."
        " Wheel won't ship the bundled script."
    )


def test_selfheal_helper_defined():
    """`_ensure_netgen_upgrade_script_deployed()` must exist in
    run_tgen_server.py."""
    src = _SERVER.read_text()
    assert "def _ensure_netgen_upgrade_script_deployed(" in src, (
        "Helper missing — server won't self-heal the installed "
        "netgen-upgrade script."
    )


def test_selfheal_helper_called_at_startup():
    """The helper must be invoked somewhere outside its own
    definition (i.e., at startup). Without the call, the helper
    is dead code."""
    src = _SERVER.read_text()
    # Definition counts as 1 occurrence; we need at least one
    # additional call site.
    occurrences = src.count("_ensure_netgen_upgrade_script_deployed(")
    assert occurrences >= 2, (
        f"`_ensure_netgen_upgrade_script_deployed()` appears "
        f"{occurrences} time(s) — needs the definition plus at "
        f"least one call site at startup."
    )


def test_selfheal_uses_sha256_compare_not_blind_overwrite():
    """The self-heal must skip the write when content is already
    in sync — otherwise every server restart pointlessly
    rewrites the file and changes mtime, confusing operators
    who diff against the source repo. SHA-256 (or any hash)
    compare is the simple correct approach."""
    src = _SERVER.read_text()
    # Locate the helper body.
    m = re.search(
        r"def _ensure_netgen_upgrade_script_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_]|\Z)",
        src,
    )
    assert m
    body = m.group(0)
    assert "sha256" in body or "hashlib" in body, (
        "Self-heal doesn't use a content hash compare — every "
        "restart would rewrite the file unnecessarily."
    )
    # Compare-then-skip pattern: there must be an `if
    # existing_sha == bundled_sha: return` short-circuit.
    assert re.search(
        r"if\s+\w+_sha\s*==\s*\w+_sha:[\s\S]+?return",
        body,
    ), (
        "Self-heal compares hashes but doesn't short-circuit "
        "on match — would still overwrite."
    )


def test_selfheal_backs_up_old_version():
    """Before overwriting, the old script must be backed up so
    the operator can recover any local customizations. The
    backup filename should be predictable (e.g. include the old
    SHA prefix) so multiple upgrades don't lose history."""
    src = _SERVER.read_text()
    m = re.search(
        r"def _ensure_netgen_upgrade_script_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_]|\Z)",
        src,
    )
    body = m.group(0)
    assert re.search(r"\.bak\.|backup", body, re.IGNORECASE), (
        "Self-heal doesn't back up the old script before "
        "overwriting — local customizations would be lost."
    )


def test_selfheal_writes_atomically_with_os_replace():
    """Write to .new then os.replace — never overwrite the live
    script in place. A crash mid-write could leave the
    /opt/netgen-server/bin/netgen-upgrade truncated, and the
    next upgrade would fail."""
    src = _SERVER.read_text()
    m = re.search(
        r"def _ensure_netgen_upgrade_script_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_]|\Z)",
        src,
    )
    body = m.group(0)
    assert re.search(r"os\.replace\(", body), (
        "Self-heal doesn't use os.replace() for atomic write — "
        "a crash mid-write could leave a corrupted script."
    )


def test_selfheal_chmods_to_executable():
    """The written script must be chmod 0755 (or include the
    executable bit) — without it, the next upgrade would fail
    with 'permission denied' even though the file is there."""
    src = _SERVER.read_text()
    m = re.search(
        r"def _ensure_netgen_upgrade_script_deployed\(\)[\s\S]+?"
        r"(?=\ndef [a-z_]|\Z)",
        src,
    )
    body = m.group(0)
    assert re.search(r"os\.chmod\([\s\S]+?0o7\d\d", body), (
        "Self-heal doesn't chmod the new script to 0o755 — "
        "next upgrade fails with 'permission denied'."
    )


def test_pyproject_version_at_least_0549():
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 49), (
        f"Version {m.group(1)} < 0.5.49"
    )
