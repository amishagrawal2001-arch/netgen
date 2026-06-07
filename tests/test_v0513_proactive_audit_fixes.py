"""Regression tests for v0.5.13: proactive audit findings.

This release was NOT triggered by an operator report. It's the
result of a comprehensive audit of every hardcoded path in the
runtime code, looking for what would break on a fresh v0.5.x
host that has no legacy /opt/OSTG/ directory.

Two latent bugs found:

1. utils/device_database._resolve_db_path() defaults to
   /opt/netgen/database.db. The v0.5.x tarball installs to
   /opt/netgen-server/. Resolution flow:
     - NETGEN_DB_PATH env: unset on fresh install
     - OSTG_DB_PATH env: unset
     - /opt/netgen/database.db: doesn't exist
     - /opt/OSTG/device_database.db: doesn't exist
   Result: returns /opt/netgen/database.db; sqlite tries to open
   it; parent dir doesn't exist; CRASH.

   Same shape for run_tgen_server.py's AI settings path resolver
   (resolves to /opt/netgen/.netgen_ai_server_settings.env).

   Why srv06 worked: legacy /opt/OSTG/ existed from the v0.4.x
   install; fallback resolution returned the legacy path. A truly
   fresh host doesn't have this safety net.

2. utils/frr_vrf.py:32 falls back to "ostg-frr:latest" when the
   primary `_resolve_frr_image()` call throws. netgen-install only
   tagged the image as "netgen-frr:latest" — the fallback path
   would `docker run` an image that doesn't exist.

   utils/frr_docker.py:183 already adds the dual tag when it
   builds via the lazy self-heal path, but only THEN. Installs
   that complete successfully without ever triggering lazy build
   have only one tag.

v0.5.13 fixes:

  1. netgen-install creates /opt/netgen → install_root compat
     symlink (mirroring v0.5.10's /opt/OSTG handling).
  2. netgen-install dual-tags the FRR image as both
     netgen-frr:latest AND ostg-frr:latest.
"""
from __future__ import annotations

import re
from pathlib import Path


_NETGEN_INSTALL = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "tarball" / "netgen-install"
)


def test_netgen_install_creates_opt_netgen_symlink():
    """utils/device_database._resolve_db_path() and the AI settings
    resolver in run_tgen_server.py both default to /opt/netgen/...
    paths. The v0.5.x tarball install is at /opt/netgen-server/.
    Need a compat symlink so the default paths resolve correctly
    on a fresh host without legacy /opt/OSTG/."""
    src = _NETGEN_INSTALL.read_text()
    assert "_create_netgen_compat_symlink" in src, (
        "netgen-install doesn't create the /opt/netgen compat "
        "symlink. Fresh hosts will crash on first DB op because "
        "/opt/netgen/database.db's parent dir doesn't exist."
    )
    # The function must symlink /opt/netgen → install_root.
    assert re.search(
        r'/opt/netgen[^-].*symlink_to.*install_root|'
        r'legacy.*=.*Path\("/opt/netgen"\)|'
        r'legacy\.symlink_to\(target\)',
        src,
        re.DOTALL,
    ), (
        "Compat symlink doesn't link /opt/netgen → install_root. "
        "Default DB/settings paths won't resolve."
    )


def test_netgen_install_dual_tags_frr_image():
    """netgen-install must tag the FRR image as BOTH netgen-frr:latest
    (the new name) AND ostg-frr:latest (the legacy name) so
    utils/frr_vrf.py:32's defensive fallback resolves correctly."""
    src = _NETGEN_INSTALL.read_text()
    # Find _build_frr_image body.
    m = re.search(
        r"def _build_frr_image[\s\S]+?(?=^def |\Z)",
        src,
        re.MULTILINE,
    )
    body = m.group(0)
    # Must invoke docker tag with the legacy name.
    assert re.search(
        r"docker.*tag.*netgen-frr:latest.*ostg-frr:latest|"
        r"docker.*tag.*\"netgen-frr:latest\".*\"ostg-frr:latest\"",
        body,
    ), (
        "_build_frr_image doesn't add the ostg-frr:latest tag. "
        "utils/frr_vrf.py:32 falls back to that tag on _resolve_frr_image() "
        "exception — without the tag, the fallback hits 'no such image'."
    )


def test_netgen_install_wires_netgen_compat_into_main():
    """The /opt/netgen compat helper must actually be CALLED from
    main(). Define-without-wire-up is a common refactor error."""
    src = _NETGEN_INSTALL.read_text()
    m = re.search(r"def main\(\)[\s\S]+?(?=^def |\Z)", src, re.MULTILINE)
    body = m.group(0)
    assert "_create_netgen_compat_symlink" in body, (
        "main() doesn't call _create_netgen_compat_symlink. Helper "
        "exists but never runs — fresh hosts still hit the bug."
    )


def test_netgen_compat_symlink_is_idempotent():
    """Re-running netgen-install on an existing install must NOT
    crash on the symlink step. Three cases to handle:
      - /opt/netgen doesn't exist: create symlink
      - /opt/netgen is already the right symlink: no-op
      - /opt/netgen is a real directory: warn, don't overwrite
    """
    src = _NETGEN_INSTALL.read_text()
    m = re.search(
        r"def _create_netgen_compat_symlink[\s\S]+?(?=^def |\Z)",
        src,
        re.MULTILINE,
    )
    assert m, "_create_netgen_compat_symlink not found"
    body = m.group(0)
    assert "is_symlink" in body, (
        "Compat-symlink helper doesn't check is_symlink"
    )
    assert "is_dir" in body, (
        "Compat-symlink helper doesn't check is_dir — would stomp "
        "on a pre-existing real /opt/netgen directory."
    )
    assert "already" in body.lower(), (
        "Compat helper doesn't surface the idempotent path. "
        "Re-runs should log that the link's already correct."
    )


def test_pyproject_version_at_least_0513():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 13), (
        f"Version {m.group(1)} < 0.5.13"
    )
