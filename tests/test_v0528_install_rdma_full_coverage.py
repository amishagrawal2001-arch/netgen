"""v0.5.28 — install_rdma.sh comprehensive dep coverage.

Operator request after v0.5.27 split shipped:
  "make sure all the dependencies for rdma should be taken care
   during Setup RDMA"

The v0.5.27 minimum-viable install_rdma.sh covered the core
(libibverbs-dev, rdma-core, perftest, ibverbs-utils,
infiniband-diags + Mellanox libmlx5-dev) but missed:

  Userspace libs:    librdmacm-dev, libibmad-dev, libibumad-dev,
                     libibnetdisc-dev
  Test tools:        rdmacm-utils (rping, ucmatose, ucmd)
  Python bindings:   python3-pyverbs
  Subnet manager:    opensm
  Firmware tools:    mstflint
  Older Mellanox:    libmlx4-dev (ConnectX-3 / ConnectX-2)
  Kernel modules:    rdma_ucm (paired with rdma_cm — without it,
                     librdmacm calls fail with EBADF on
                     /dev/infiniband/rdma_cm), iw_cm (iWARP CM)

These tests pin the comprehensive set so a future refactor doesn't
silently slim the dep list and break operator workflows.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALL_RDMA = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_rdma.sh"
)
_SETUP_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "setup_rdma_dialog.py"
)


# v0.5.27 baseline + v0.5.28 additions
CORE_PACKAGES_V0528 = [
    "libibverbs-dev",       # v0.5.27
    "librdmacm-dev",        # v0.5.28
    "libibmad-dev",         # v0.5.28
    "libibumad-dev",        # v0.5.28
    "libibnetdisc-dev",     # v0.5.28
    "rdma-core",            # v0.5.27
    "perftest",             # v0.5.27
    "ibverbs-utils",        # v0.5.27
    "rdmacm-utils",         # v0.5.28
    "infiniband-diags",     # v0.5.27
    "python3-pyverbs",      # v0.5.28
    "opensm",               # v0.5.28
    "mstflint",             # v0.5.28
]

MLX_PACKAGES_V0528 = [
    "libmlx5-dev",  # v0.5.27 — ConnectX-4 / 5 / 6
    "libmlx4-dev",  # v0.5.28 — ConnectX-3 / 2 (older lab gear)
]

KMODS_V0528 = [
    "ib_uverbs",   # v0.5.27 — userspace verbs
    "rdma_cm",     # v0.5.27 — kernel RDMA CM
    "rdma_ucm",    # v0.5.28 — userspace RDMA CM bridge
    "ib_umad",     # v0.5.27 — userspace MAD
    "iw_cm",       # v0.5.28 — iWARP CM
]


def _core_apt_cmd() -> str:
    src = _INSTALL_RDMA.read_text()
    m = re.search(r'core_apt_cmd="([\s\S]+?)"', src)
    assert m, "core_apt_cmd assignment not found in install_rdma.sh"
    return m.group(1)


def _mlx_apt_cmd() -> str:
    src = _INSTALL_RDMA.read_text()
    m = re.search(r'mlx5_apt_cmd="([\s\S]+?)"', src)
    assert m, "mlx5_apt_cmd assignment not found in install_rdma.sh"
    return m.group(1)


# ────────────────────── Core batch coverage ─────────────────────────


def test_core_batch_contains_every_v0528_package():
    """All 13 core packages must appear in core_apt_cmd. A
    refactor trimming any of them silently degrades the RDMA
    stack — librdmacm-dev / rdmacm-utils especially: without
    them, RDMA-CM client calls fail at runtime."""
    cmd = _core_apt_cmd()
    missing = [p for p in CORE_PACKAGES_V0528 if p not in cmd]
    assert not missing, (
        f"core_apt_cmd missing v0.5.28 packages: {missing}. "
        f"This breaks RDMA workflows that depend on them."
    )


def test_mlx_batch_covers_old_and_new_mellanox():
    """libmlx5-dev (ConnectX-4 and newer) was v0.5.27. v0.5.28
    adds libmlx4-dev for ConnectX-3 / ConnectX-2 — still common in
    lab gear from 2014-2018."""
    cmd = _mlx_apt_cmd()
    missing = [p for p in MLX_PACKAGES_V0528 if p not in cmd]
    assert not missing, (
        f"mlx5_apt_cmd missing v0.5.28 Mellanox packages: {missing}. "
        f"Older Mellanox NICs (ConnectX-3 and earlier) lose dev "
        f"headers without libmlx4-dev."
    )


def test_kmod_list_includes_v0528_additions():
    """rdma_ucm (paired with rdma_cm — without it librdmacm calls
    EBADF on /dev/infiniband/rdma_cm) and iw_cm (iWARP). Both
    are harmless on hosts without matching hardware."""
    src = _INSTALL_RDMA.read_text()
    m = re.search(
        r'rdma_modules=\(\s*([\s\S]+?)\s*\)', src,
    )
    assert m, "rdma_modules array not found"
    declared = m.group(1)
    missing = [mod for mod in KMODS_V0528 if f'"{mod}"' not in declared]
    assert not missing, (
        f"rdma_modules array missing v0.5.28 modules: {missing}. "
        f"rdma_ucm absence breaks every librdmacm-using tool "
        f"(perftest, rping, etc.)."
    )


# ─────────── opensm-not-auto-enabled regression guard ───────────────


def test_opensm_disabled_after_install():
    """opensm.service can take over fabric management. On RoCE-only
    hosts that's harmless; on a fabric with an existing subnet
    manager (switch SM or another opensm instance), having two
    SMs fight is destructive. install_rdma.sh must disable +
    stop opensm post-install — operator can systemctl enable it
    explicitly if they want."""
    src = _INSTALL_RDMA.read_text()
    assert "systemctl disable" in src and "opensm" in src, (
        "install_rdma.sh doesn't disable opensm.service after "
        "install — a second SM on the fabric could fight the "
        "existing one."
    )
    # The disable must be conditional on the service existing
    # (avoids errors on hosts where opensm wasn't installed).
    assert re.search(
        r'systemctl\s+list-unit-files[\s\S]+?opensm\.service',
        src,
    ), (
        "opensm disable isn't gated on the service existing — "
        "would log a confusing error on hosts without opensm."
    )


# ─────────────────── Wizard dialog reflects the full set ────────────


def test_dialog_intro_mentions_v0528_additions():
    """The Setup RDMA dialog's intro text must surface the new
    packages so operators can see what they're getting. Otherwise
    the wizard description becomes stale once the script gets
    richer."""
    src = _SETUP_DIALOG.read_text()
    # Must reference the v0.5.28 marquee packages.
    for pkg in (
        "librdmacm-dev", "rdmacm-utils", "python3-pyverbs",
        "opensm", "mstflint", "libmlx4-dev",
    ):
        assert pkg in src, (
            f"Setup RDMA dialog intro doesn't mention {pkg!r}. "
            f"Operators won't see they're getting it."
        )
    # And the new kmods.
    for mod in ("rdma_ucm", "iw_cm"):
        assert mod in src, (
            f"Setup RDMA dialog intro doesn't mention kernel module "
            f"{mod!r}. The dialog's contract with the user drifts "
            f"from what the script does."
        )


# ────────────────────────── v0.5.27 invariants preserved ─────────────


def test_core_batch_still_separated_from_mlx_batch():
    """The split between core (always-installable) and mlx
    (Mellanox-MOFED-optional, fail-tolerant) must remain — v0.5.28
    just expanded the contents. If anyone collapses the two
    batches, hosts without MOFED apt repo break."""
    src = _INSTALL_RDMA.read_text()
    assert "core_apt_cmd" in src and "mlx5_apt_cmd" in src, (
        "install_rdma.sh lost the core/mlx batch split — non-MOFED "
        "hosts would fail the entire install when libmlx5-dev / "
        "libmlx4-dev miss from apt."
    )


def test_mlx_install_failure_is_non_fatal():
    """The Mellanox batch must remain fail-tolerant. v0.5.28 doubles
    the packages in it (libmlx4-dev + libmlx5-dev) — if EITHER
    misses on a host without MOFED, the whole batch fails, and
    the script must keep going."""
    src = _INSTALL_RDMA.read_text()
    # The mlx install must be in an if/else that warns rather than
    # exits on failure.
    m = re.search(
        r'if\s+eval\s+"\$mlx5_apt_cmd"[\s\S]+?else[\s\S]+?log_warning',
        src,
    )
    assert m, (
        "install_rdma.sh's Mellanox batch isn't fault-tolerant — "
        "a failure on libmlx4-dev would now abort the whole install "
        "even though it's optional."
    )


def test_pyproject_version_at_least_0528():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 28), (
        f"Version {m.group(1)} < 0.5.28"
    )
