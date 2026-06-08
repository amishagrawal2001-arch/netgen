"""v0.5.26 — install_dpdk.sh apt list covers DPDK 23.11 fully.

Operator request after the v0.5.25 pyelftools fix unblocked Step 5:
"check full what other dependency is missing for dpdk". This test
pins the comprehensive dep catalog so we don't drip-feed one
missing apt-package per operator report.

The script invokes meson with `-Dexamples=all` (see install_dpdk.sh
line ~592), which transitively pulls in every example's deps. DPDK
23.11 also enables telemetry by default (which needs jansson). The
v0.5.26 audit added 8 optional packages covering the realistic
surface:

  Mandatory (meson setup fails):
    build-essential, meson, ninja-build, pkg-config, libnuma-dev,
    python3-pyelftools, ${kernel_headers}
  Driver/lib (compile fails):
    libelf-dev, libpcap-dev, libibverbs-dev, rdma-core, perftest
  Optional (added v0.5.26):
    libssl-dev, libjansson-dev, libbpf-dev, libxdp-dev,
    libbsd-dev, zlib1g-dev, libfdt-dev, libarchive-dev

Anything missing from this list earns its own line + comment
upstairs explaining what it enables, so the next person reading
install_dpdk.sh can tell *why* a dep is there.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALL_DPDK = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_dpdk.sh"
)


# ─────────── Categories — each list MUST be in deps_install_cmd ──────


MANDATORY = [
    "build-essential",
    "meson",
    "ninja-build",
    "pkg-config",
    "libnuma-dev",
    "python3-pyelftools",
]

# v0.5.27: libibverbs-dev, rdma-core, perftest moved to
# install_rdma.sh — install_dpdk.sh's driver/lib list trims to
# DPDK-only items. The Mellanox PMD support still works because
# the operator runs install_rdma.sh first (or the wizard does).
DRIVER_AND_LIB = [
    "libelf-dev",
    "libpcap-dev",
]

OPTIONAL_V0526 = [
    "libssl-dev",
    "libjansson-dev",
    "libbpf-dev",
    "libxdp-dev",
    "libbsd-dev",
    "zlib1g-dev",
    "libfdt-dev",
    "libarchive-dev",
]


def _apt_command() -> str:
    src = _INSTALL_DPDK.read_text()
    m = re.search(r'deps_install_cmd=["\']([^"\']+)["\']', src)
    assert m, "deps_install_cmd assignment not found"
    return m.group(1)


def test_all_mandatory_packages_present():
    """Anything in MANDATORY must appear in deps_install_cmd — these
    are the packages without which DPDK 23.11's meson setup fails
    OUTRIGHT (not just degrades a feature)."""
    cmd = _apt_command()
    missing = [p for p in MANDATORY if p not in cmd]
    assert not missing, (
        f"deps_install_cmd missing mandatory packages: {missing}. "
        f"DPDK 23.11 meson setup will fail without them."
    )


def test_all_driver_packages_present():
    """Driver/library deps must be present — without them, specific
    PMDs (Mellanox, pcap) won't compile and netgen's RDMA test
    orchestrator won't have ib_send_bw etc."""
    cmd = _apt_command()
    missing = [p for p in DRIVER_AND_LIB if p not in cmd]
    assert not missing, (
        f"deps_install_cmd missing driver/lib packages: {missing}. "
        f"Compile errors on PMDs or runtime errors when netgen "
        f"tries to use RDMA features."
    )


def test_v0526_optional_packages_present():
    """The v0.5.26 audit-added optionals must all be there.
    Without them, examples-all builds skip features silently OR
    error out on configure-time checks for specific examples
    (l2fwd-crypto, ipsec-secgw, AF_XDP PMDs)."""
    cmd = _apt_command()
    missing = [p for p in OPTIONAL_V0526 if p not in cmd]
    assert not missing, (
        f"deps_install_cmd dropped v0.5.26 audit packages: "
        f"{missing}. Re-trim only after confirming -Dexamples=all "
        f"+ telemetry default-enable don't require them."
    )


def test_optional_deps_in_core_batch_not_mlx5():
    """v0.5.27 update: mlx5_install_cmd moved to install_rdma.sh.
    There IS no mlx5 batch in install_dpdk.sh anymore, so the
    "make sure optionals aren't in it" check is now a "make sure
    the batch is gone" check."""
    src = _INSTALL_DPDK.read_text()
    assert "mlx5_install_cmd" not in src, (
        "mlx5_install_cmd resurrected in install_dpdk.sh — moved to "
        "install_rdma.sh in v0.5.27."
    )


def test_no_duplicate_packages_in_apt_list():
    """A package listed twice doesn't break apt but signals a
    refactor that didn't notice the prior entry. Catch them."""
    cmd = _apt_command()
    # Strip the apt-get options + tokenize the package list portion.
    parts = cmd.split()
    # Packages are anything after `--option ... ftp::Timeout=30`
    # that doesn't start with `-` and doesn't look like a flag value.
    # Simpler: grab whatever has no `=` and no leading `-` and isn't
    # an apt subcommand keyword.
    keywords = {
        "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
    }
    pkgs = [
        p for p in parts
        if not p.startswith("-")
        and "=" not in p
        and not p.startswith("$")
        and p not in keywords
    ]
    dupes = sorted({p for p in pkgs if pkgs.count(p) > 1})
    assert not dupes, f"Duplicate packages in apt list: {dupes}"


def test_dep_comment_explains_optional_rationale():
    """The v0.5.26 dep additions deserve a comment explaining WHY
    each was added — otherwise a future refactor trims them without
    knowing what feature they enable."""
    src = _INSTALL_DPDK.read_text()
    # Look for the comment block immediately preceding the apt cmd.
    m = re.search(
        r"DPDK 23\.11 apt dependency catalog[\s\S]+?deps_install_cmd=",
        src,
    )
    assert m, (
        "No dep-catalog comment block. Future refactors will trim "
        "the optional packages without understanding what each "
        "enables."
    )
    block = m.group(0)
    # The comment must enumerate at least the major feature areas
    # so the rationale is discoverable.
    for keyword in ("telemetry", "AF_XDP", "crypto", "examples"):
        assert keyword in block, (
            f"Dep comment doesn't reference {keyword!r} — future "
            f"reader can't tell which deps support that feature."
        )


def test_pyproject_version_at_least_0526():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 26), (
        f"Version {m.group(1)} < 0.5.26"
    )
