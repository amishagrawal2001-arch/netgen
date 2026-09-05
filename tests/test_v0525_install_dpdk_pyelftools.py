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


def _all_deps() -> str:
    """v0.5.256 (drift): v0.5.192 refactored install_dpdk.sh to
    split `deps_install_cmd` into two shell variables — `deps_required`
    (all-or-fail) and `deps_optional` (best-effort, for AF_XDP on
    boxes without Ubuntu universe). Concatenate both here so tests
    can assert "any package the script tries to install" without
    caring which batch it lives in."""
    src = _INSTALL_DPDK.read_text()
    parts = []
    for var in ("deps_required", "deps_optional"):
        m = re.search(rf'{var}=["\']([^"\']+)["\']', src)
        if m:
            parts.append(m.group(1))
    return " ".join(parts)


def test_apt_install_includes_pyelftools():
    """The apt list must include python3-pyelftools. Without it,
    DPDK 23.11 meson setup fails with 'missing python module:
    elftools' at step 5."""
    apt_cmd = _all_deps()
    assert "python3-pyelftools" in apt_cmd, (
        "install_dpdk.sh apt list missing python3-pyelftools. "
        "DPDK 23.11's buildtools/meson.build:58 hard-requires the "
        "`elftools` Python module — meson setup fails immediately "
        f"without it. Current apt list:\n  {apt_cmd}"
    )


def test_pyelftools_in_core_batch_not_mlx5_batch():
    """v0.5.27 update: mlx5_install_cmd moved to install_rdma.sh
    along with the rest of the RDMA stack. This test now just
    confirms pyelftools is somewhere in the deps batches (the only
    ones left in install_dpdk.sh). Pre-v0.5.27 this test also
    enforced pyelftools NOT being in the mlx5 batch — moot now
    that the batch is gone."""
    src = _INSTALL_DPDK.read_text()
    # mlx5_install_cmd MUST be gone — that's v0.5.27's contract.
    assert "mlx5_install_cmd" not in src, (
        "mlx5_install_cmd resurrected in install_dpdk.sh — moved to "
        "install_rdma.sh in v0.5.27. See test_v0527_rdma_install_split."
    )
    # And pyelftools must be in one of the batches.
    assert "python3-pyelftools" in _all_deps(), (
        "python3-pyelftools missing from install_dpdk.sh deps"
    )


def test_apt_install_preserves_core_dpdk_deps():
    """Sanity check on the rest of the batch — guards against a
    refactor that drops other deps while adding pyelftools."""
    apt_cmd = _all_deps()
    # v0.5.27: libibverbs-dev, rdma-core, perftest moved to
    # install_rdma.sh. Mellanox MOFED-optional libmlx5-dev moved
    # there too. install_dpdk.sh's required list is now DPDK-only.
    required = [
        "build-essential",
        "meson",
        "ninja-build",
        "pkg-config",
        "libnuma-dev",
        "libelf-dev",
        "libpcap-dev",
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
