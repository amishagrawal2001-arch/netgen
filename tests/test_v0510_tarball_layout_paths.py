"""Regression test for v0.5.10: the tarball's share/netgen layout
must match what netgen-install's preflight check (and the runtime
code) expect.

Operator-reported on san-hp-srv06 after the v0.5.9 clock-skew fix
finally let tar succeed:

  2026-06-07 08:25:43,668 [INFO] [PRE-FLIGHT] Checking environment...
  2026-06-07 08:25:43,668 [INFO]   - OS: ubuntu 24.04
  2026-06-07 08:25:43,669 [ERROR] Install root /opt/netgen-server
    is missing expected files:
  2026-06-07 08:25:43,669 [ERROR] - /opt/netgen-server/share/netgen/
    resources/dpdk
  2026-06-07 08:25:43,669 [ERROR] The tarball must be extracted
    intact to a single root.
  [client] installer exit rc=3

The preflight in scripts/tarball/netgen-install line 197 lists:
  install_root / "share" / "netgen" / "resources" / "dpdk"

But the CI workflow at the same release was doing:
  cp -r resources/dpdk "$ROOT/share/netgen/"

Which lands at `share/netgen/dpdk/`, missing the `resources/`
parent. Latent since v0.5.0. CI never caught it because the smoke
test exits on _require_root() before the preflight runs.

Plus: runtime code in run_tgen_server.py:12840 and friends
hardcodes `/opt/OSTG/resources/dpdk/...` paths from the pre-tarball
system-pip era. Without a compat symlink, every DPDK op fails at
runtime even after a clean install.

v0.5.10 fixes both:

  1. Workflow puts files at share/netgen/resources/dpdk/ (with the
     `resources/` parent).
  2. netgen-install creates /opt/OSTG → share/netgen compat
     symlink so runtime's hardcoded /opt/OSTG/resources/dpdk/...
     paths resolve.
  3. CI round-trip test EXPLICITLY checks the required layout
     paths exist post-extract — the check netgen-install's
     _preflight would have done, but without needing root.
"""
from __future__ import annotations

import re
from pathlib import Path


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "build-server-tarball.yml"
)
_NETGEN_INSTALL = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "tarball" / "netgen-install"
)


def test_workflow_packs_resources_dpdk_with_parent_dir():
    """The CI must place dpdk under share/netgen/resources/dpdk/,
    not share/netgen/dpdk/. netgen-install's preflight at line
    ~197 requires share/netgen/resources/dpdk/ — operator-reported
    failure mode."""
    src = _WORKFLOW.read_text()
    # Must mention the parent dir layout.
    assert "share/netgen/resources" in src, (
        "Workflow doesn't mention share/netgen/resources — the "
        "path netgen-install's preflight requires. v0.5.9 and "
        "earlier landed dpdk at share/netgen/dpdk/, missing the "
        "resources/ parent."
    )
    # And the dpdk copy step must target the resources/ subdir.
    assert re.search(
        r'cp\s+-r\s+resources/dpdk\s+"?\$ROOT/share/netgen/resources/?"?',
        src,
    ), (
        "Workflow's `cp resources/dpdk` doesn't target "
        "$ROOT/share/netgen/resources/. Without preserving the "
        "`resources/` parent, netgen-install's preflight check "
        "fails with 'missing expected files'."
    )


def test_roundtrip_test_validates_layout_paths():
    """v0.5.7 added shebang-exec smoke for netgen-install, but
    that smoke EXITS at _require_root() before _preflight runs —
    so the layout check never validated in CI. v0.5.10 explicitly
    pins the four required paths in the round-trip step."""
    src = _WORKFLOW.read_text()
    # Find the round-trip step.
    m = re.search(
        r"Verify tarball extracts cleanly[\s\S]+?(?=\n      - name:|\Z)",
        src,
    )
    assert m, "Round-trip step not found"
    body = m.group(0)
    # Must enumerate the four required paths that match
    # netgen-install's _preflight check.
    required = [
        "python-runtime/bin/python3",
        "netgen-venv/bin/ostg-server",
        "share/netgen/resources/dpdk",
        "share/netgen/Dockerfile.frr",
    ]
    for path in required:
        assert path in body, (
            f"Round-trip step doesn't check for {path}. "
            f"netgen-install's _preflight requires it — if the "
            f"tarball is missing it, an operator hits 'missing "
            f"expected files' at install time."
        )


def test_netgen_install_creates_ostg_compat_symlink():
    """The runtime code (run_tgen_server.py:12840 etc.) hardcodes
    paths like /opt/OSTG/resources/dpdk/dpdk_bind.sh — legacy from
    the pre-tarball system-pip era. v0.5.10 netgen-install must
    install a /opt/OSTG → /opt/netgen-server/share/netgen symlink
    so the hardcoded paths still resolve.

    Without this, the install completes successfully BUT every
    DPDK op fails at runtime because dpdk_bind.sh is "not found".
    """
    src = _NETGEN_INSTALL.read_text()
    # Must have a compat-symlink helper.
    assert "_create_ostg_compat_symlink" in src or \
           "/opt/OSTG" in src, (
        "netgen-install doesn't create the /opt/OSTG compat "
        "symlink. Runtime code at run_tgen_server.py:12840+ "
        "hardcodes /opt/OSTG/resources/dpdk/... — without the "
        "symlink every DPDK runtime op fails."
    )
    # The symlink must point at install_root/share/netgen.
    assert re.search(
        r'(/opt/OSTG|legacy).*(?:symlink_to|->).*share.*netgen|'
        r'share.*netgen.*(?:symlink_to|->).*(/opt/OSTG|legacy)',
        src,
        re.DOTALL,
    ), (
        "netgen-install's compat-symlink code doesn't link "
        "/opt/OSTG → <install_root>/share/netgen. The runtime "
        "expects to find resources/dpdk/ under /opt/OSTG/."
    )


def test_netgen_install_wires_compat_symlink_into_main():
    """The _create_ostg_compat_symlink helper must actually be
    CALLED from main(). Just defining it without wiring it up is
    a common refactor error."""
    src = _NETGEN_INSTALL.read_text()
    # Find main() body.
    m = re.search(r"def main\(\)[\s\S]+?(?=^def |\Z)", src, re.MULTILINE)
    assert m, "main() not found"
    body = m.group(0)
    assert "_create_ostg_compat_symlink" in body, (
        "main() doesn't call _create_ostg_compat_symlink. The "
        "helper exists but is never invoked — runtime DPDK ops "
        "still fail."
    )


def test_netgen_install_compat_symlink_is_idempotent():
    """Re-running netgen-install on an existing install must NOT
    crash on the symlink step. Handle three cases:
      - /opt/OSTG doesn't exist: create symlink
      - /opt/OSTG is already the right symlink: no-op
      - /opt/OSTG is a real directory (legacy install): warn,
        don't overwrite
    """
    src = _NETGEN_INSTALL.read_text()
    # Find the helper body.
    m = re.search(
        r"def _create_ostg_compat_symlink[\s\S]+?(?=^def |\Z)",
        src,
        re.MULTILINE,
    )
    assert m, "_create_ostg_compat_symlink helper not found"
    body = m.group(0)
    # Must check is_symlink and is_dir distinctly.
    assert "is_symlink" in body, (
        "Compat-symlink helper doesn't check is_symlink — would "
        "fail or stomp on the wrong target on re-run."
    )
    assert "is_dir" in body, (
        "Compat-symlink helper doesn't check is_dir — would "
        "stomp on a legacy /opt/OSTG real directory."
    )
    # Idempotent path: if already pointing at the right target,
    # log and return without re-creating.
    assert "already" in body.lower() or "no-op" in body.lower(), (
        "Compat-symlink helper doesn't log the idempotent path. "
        "Operators re-running netgen-install should see something "
        "explicit, not a silent re-link."
    )


def test_pyproject_version_at_least_0510():
    """Sanity check: don't ship this fix on a v0.5.9 tag."""
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    # >= 0.5.10
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 10), (
        f"Version {m.group(1)} < 0.5.10 — this fix can't be "
        f"shipped under a previously-tagged version."
    )
