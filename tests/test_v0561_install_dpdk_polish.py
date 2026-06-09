"""v0.5.61 — install_dpdk.sh polish: kernel-headers fallback,
multi-mount disk-space, version-mismatch warning.

Audit findings M6 + M7 + M8.

M6: `linux-headers-$(uname -r)` may not exist as a package on
hosts running an older or out-of-band kernel (HWE rolled
forward, custom kernel, snapshot rollback). Pre-fix the apt
install batch fails for everything. Fall back to the meta-
package `linux-headers-generic` which tracks the latest stable
kernel from the host's repo.

M7: Disk-space check used `df -BG /`. On lab boxes where
`/opt` (DPDK_DIR) and `/usr/local` (install target) are
separate mounts, the `/` check is meaningless. Walk all paths
the install actually writes to.

M8: `check_dpdk_installed` prompted "reinstall?" with no version
context. Operators answered yes/no without realising they were
about to upgrade across an ABI boundary that orphans tx_worker.
Warn explicitly when installed version != this script's target.
"""
from __future__ import annotations

import re
from pathlib import Path


_SHELL = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_dpdk.sh"
)


def _src() -> str:
    return _SHELL.read_text()


def test_kernel_headers_falls_back_to_generic():
    """Pre-flight headers detection must fall back to
    `linux-headers-generic` when the precise version isn't in
    apt cache."""
    s = _src()
    # Must call `apt-cache show "$kernel_headers"` for the precise
    # version, then fallback to linux-headers-generic.
    assert re.search(
        r"apt-cache\s+show\s+\"?\$kernel_headers\"?",
        s,
    ), (
        "No apt-cache show probe for the precise kernel-headers "
        "package — out-of-band kernels would still apt-fail."
    )
    assert "linux-headers-generic" in s, (
        "No fallback to linux-headers-generic"
    )


def test_disk_check_walks_multiple_paths():
    """Disk-space check must consider /, /usr/local, and the
    DPDK build dir — not just /."""
    s = _src()
    # The naïve `df -BG /` ONE-PATH variant must be gone.
    one_path_naive = re.search(
        r"local\s+available=\$\(df -BG\s+/\s+\|\s+tail",
        s,
    )
    assert not one_path_naive, (
        "Disk-space check still hardcodes `/` only — separate "
        "/opt or /usr/local mounts silently bypass it."
    )
    # The fix iterates a path set.
    assert re.search(
        r"for\s+path\s+in[\s\S]{0,200}?/usr/local[\s\S]{0,80}?DPDK_DIR",
        s,
    ), (
        "Disk-space check doesn't walk /usr/local + DPDK_DIR"
    )


def test_check_dpdk_installed_warns_on_version_mismatch():
    """`check_dpdk_installed` must warn when the installed
    version differs from the target the script's other steps
    assume (23.11 currently)."""
    s = _src()
    m = re.search(
        r"check_dpdk_installed\(\)[\s\S]+?\n\}",
        s,
    )
    assert m
    body = m.group(0)
    assert "23.11" in body, (
        "No version comparison against 23.11 target — operators "
        "blindly answer 'reinstall?' across ABI breaks"
    )
    assert "tx_worker" in body or "ABI" in body.upper(), (
        "Version-mismatch warning doesn't mention tx_worker / "
        "ABI consequences — operator doesn't know what they're "
        "in for."
    )


def test_pyproject_version_at_least_0561():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 61), (
        f"Version {m.group(1)} < 0.5.61"
    )
