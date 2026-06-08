"""v0.5.25 — install_dpdk.sh must apt-install python3-pyelftools.

Operator-reported on srv06 (Jun 7 2026), Step 5 (Building DPDK):

  Configuring DPDK build (disabling: net/mana)...
  Program python3 found: YES (/usr/bin/python3)
  buildtools/meson.build:58:8: ERROR: Problem encountered: missing python module: elftools
  A full log can be found at /opt/dpdk-build/build/meson-logs/meson-log.txt
  [x] DPDK meson setup failed

DPDK 23.11's `buildtools/meson.build:58` checks for the
`elftools` Python module (from python3-pyelftools apt package) so
its check-symbols.sh / ABI tooling can parse ELF binaries. The
check is hard-required — `meson setup` fails immediately without
it. install_dpdk.sh's apt batch had every other DPDK build prereq
(meson, ninja-build, libnuma-dev, libelf-dev, libpcap-dev,
libibverbs-dev, rdma-core, perftest, kernel headers) but missed
python3-pyelftools.

This test pins python3-pyelftools in the deps_install_cmd. Re-
trimming the dep list without checking against DPDK 23.11's meson
requirements would now trip the test, not the next operator.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALL_DPDK = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_dpdk.sh"
)


def test_apt_install_includes_pyelftools():
    """The deps_install_cmd line must include python3-pyelftools.
    Without it, DPDK 23.11 meson setup fails with 'missing python
    module: elftools' at step 5."""
    src = _INSTALL_DPDK.read_text()
    # Find the deps_install_cmd assignment.
    m = re.search(r'deps_install_cmd=["\']([^"\']+)["\']', src)
    assert m, "deps_install_cmd assignment not found in install_dpdk.sh"
    apt_cmd = m.group(1)
    assert "python3-pyelftools" in apt_cmd, (
        "deps_install_cmd is missing python3-pyelftools. DPDK 23.11's "
        "buildtools/meson.build:58 hard-requires the `elftools` "
        "Python module — meson setup fails immediately without it. "
        f"Current apt list:\n  {apt_cmd}"
    )


def test_pyelftools_in_core_batch_not_mlx5_batch():
    """python3-pyelftools must be in deps_install_cmd, NOT
    mlx5_install_cmd. The mlx5 batch is for the OPTIONAL Mellanox
    package and can fail independently on hosts without the MOFED
    apt repo (svl-d-ai-srv04, etc.). If pyelftools landed in the
    mlx5 batch, those hosts would silently lose pyelftools and DPDK
    build would still fail."""
    src = _INSTALL_DPDK.read_text()
    mlx5_m = re.search(r'mlx5_install_cmd=["\']([^"\']+)["\']', src)
    assert mlx5_m, "mlx5_install_cmd assignment not found"
    assert "python3-pyelftools" not in mlx5_m.group(1), (
        "python3-pyelftools landed in mlx5_install_cmd — hosts "
        "without MOFED apt repo would skip the mlx5 batch and "
        "lose pyelftools too. Move to deps_install_cmd."
    )


def test_apt_install_preserves_core_dpdk_deps():
    """Sanity check on the rest of the batch — guards against a
    refactor that drops other deps while adding pyelftools."""
    src = _INSTALL_DPDK.read_text()
    m = re.search(r'deps_install_cmd=["\']([^"\']+)["\']', src)
    apt_cmd = m.group(1)
    required = [
        "build-essential",
        "meson",
        "ninja-build",
        "pkg-config",
        "libnuma-dev",
        "libelf-dev",
        "libpcap-dev",
        "libibverbs-dev",
        "rdma-core",
        "perftest",
        "python3-pyelftools",
    ]
    missing = [pkg for pkg in required if pkg not in apt_cmd]
    assert not missing, (
        f"deps_install_cmd dropped required packages: {missing}. "
        f"DPDK build would fail at one of meson/ninja/compile/link/"
        f"meson-elftools-check stages."
    )


def test_pyproject_version_at_least_0525():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 25), (
        f"Version {m.group(1)} < 0.5.25"
    )
