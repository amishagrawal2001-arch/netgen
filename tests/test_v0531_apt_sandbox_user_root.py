"""v0.5.31 — every apt invocation must set APT::Sandbox::User=root.

Operator-reported on srv06 (Jun 8 2026, post v0.5.30 diagnostic).
The v0.5.30 hard gate finally surfaced the actual root cause that
three releases of "install python3-pyelftools" couldn't fix:

  E: setgroups 65534 failed - setgroups (1: Operation not permitted)
  Err:15 ... Could not open file .../python3-pyelftools_0.30-1_all.deb
      - open (13: Permission denied)
  W: chown to _apt:root of directory /var/cache/apt/archives/partial
     failed - SetupAPTPartialDirectory (1: Operation not permitted)

Apt by default tries to drop privileges to the unprivileged `_apt`
user for downloads. When invoked from netgen-server.service, the
systemd sandbox blocks setgroups() (RestrictSUIDSGID=true or a
seccomp filter), so apt can't complete the drop → can't access
its own cache directories → fails the entire batch silently.
Operator sees Step 5 meson elftools error and is none the wiser.

Fix: `-o APT::Sandbox::User=root` on EVERY apt invocation tells
apt to skip the privilege drop and stay root. Safe — netgen-
server runs as root anyway. Sidesteps the systemd sandbox cleanly.

These tests pin the option on each apt invocation across both
install_dpdk.sh and install_rdma.sh. Anyone removing it (or
adding a new apt call without it) earns a test failure here, not
the next "Step 5 meson elftools" failure on a systemd-restricted
host.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_INSTALL_DPDK = _REPO / "resources" / "dpdk" / "install_dpdk.sh"
_INSTALL_RDMA = _REPO / "resources" / "dpdk" / "install_rdma.sh"


def _apt_invocations(script_path: Path):
    """Return every apt-get invocation (install or update) line/block
    from a script, EXCLUDING lines where apt-get appears inside a
    log_*/echo recovery-text string."""
    src = script_path.read_text()
    blocks = []
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("#") or re.match(
            r'^(log_(error|warning|info|success|step)|echo|printf)\b',
            stripped,
        ):
            i += 1
            continue
        if re.search(r'\bapt-get\b\s*(install|update)\b', line):
            block = [line]
            while block[-1].rstrip().endswith("\\"):
                i += 1
                if i >= len(lines):
                    break
                block.append(lines[i])
            blocks.append("\n".join(block))
        i += 1
    return blocks


def test_every_apt_invocation_in_install_dpdk_has_sandbox_user_root():
    blocks = _apt_invocations(_INSTALL_DPDK)
    assert blocks, "No apt-get invocations found in install_dpdk.sh"
    for block in blocks:
        assert "APT::Sandbox::User=root" in block, (
            f"install_dpdk.sh has an apt-get invocation WITHOUT "
            f"APT::Sandbox::User=root:\n{block.strip()}\n\n"
            f"Without this, systemd-restricted hosts get the "
            f"setgroups EPERM failure → apt silently no-ops → "
            f"Step 5 meson dies with 'missing module: elftools'."
        )


def test_every_apt_invocation_in_install_rdma_has_sandbox_user_root():
    blocks = _apt_invocations(_INSTALL_RDMA)
    assert blocks, "No apt-get invocations found in install_rdma.sh"
    for block in blocks:
        assert "APT::Sandbox::User=root" in block, (
            f"install_rdma.sh has an apt-get invocation WITHOUT "
            f"APT::Sandbox::User=root:\n{block.strip()}\n\n"
            f"Same root cause as v0.5.31 DPDK fix — systemd sandbox "
            f"will silently fail apt downloads."
        )


def test_apt_sandbox_option_documented_with_rationale():
    src = _INSTALL_DPDK.read_text()
    deps_match = re.search(
        r"deps_install_cmd=.*APT::Sandbox::User=root",
        src,
    )
    assert deps_match, "deps_install_cmd doesn't use Sandbox::User=root"
    preceding = src[:deps_match.start()][-1500:]
    has_rationale = (
        "setgroups" in preceding
        or "RestrictSUIDSGID" in preceding
        or "systemd" in preceding.lower()
    )
    assert has_rationale, (
        "APT::Sandbox::User=root appears without a comment "
        "explaining WHY (setgroups EPERM under systemd). A "
        "future refactor will drop the option without context."
    )


def test_pyproject_version_at_least_0531():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 31), (
        f"Version {m.group(1)} < 0.5.31"
    )
