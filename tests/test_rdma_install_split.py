"""Regression tests for the v0.3.16 libmlx5-dev all-or-nothing fix.

Operator scenario from svl-d-ai-srv04:

  [INFO] Installing RDMA userspace + perftest (for Tools → RDMA)...
  Reading package lists...
  E: Unable to locate package libmlx5-dev
  [WARNING] perftest binary not on PATH after install — Tools → RDMA
            will report 'perftest not installed'.

Root cause: `_install_rdma_userspace` put all 4 packages (perftest,
rdma-core, libibverbs-dev, libmlx5-dev) into ONE `apt-get install`
command. ``libmlx5-dev`` ships ONLY in Mellanox MOFED's apt repo,
NOT Ubuntu main. On a host without MOFED configured, apt returned
rc=100 and installed NONE of the 4 packages — including the 3 that
WERE available in Ubuntu main. Operator's RDMA features stayed
broken even though ~75% of the package list was installable.

Same problem in install_dpdk.sh's apt-get install: libmlx5-dev
bundled with build-essential + meson + libibverbs-dev + perftest
meant ANY of those failing took down the whole DPDK build-deps
install.

Fix: split into two passes — CORE (always-available) + MELLANOX-
OPTIONAL (libmlx5-dev). Core failure aborts; MOFED-only failure
just warns.

These tests pin the split so a refactor can't silently merge the
two batches back together."""
from __future__ import annotations

import re

_INSTALLER = "/Users/surajsharma/dev/netgen/install_ostg_complete.py"
_DPDK_SH = "/Users/surajsharma/dev/netgen/resources/dpdk/install_dpdk.sh"


def _helper_body():
    src = open(_INSTALLER).read()
    m = re.search(
        r"def _install_rdma_userspace\(self\):.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "_install_rdma_userspace not found"
    return m.group(0)


def test_rdma_userspace_helper_has_two_apt_install_passes():
    """The helper must invoke _apt_install TWICE on the apt branch:
    once for the core packages, once for libmlx5-dev. Single-batch
    was the pre-fix bug."""
    body = _helper_body()
    # Count _apt_install calls inside the `if pm == "apt":` branch
    apt_branch = re.search(
        r'if pm == "apt":.*?elif pm in \(', body, re.DOTALL,
    )
    assert apt_branch, "apt branch not found in _install_rdma_userspace"
    branch = apt_branch.group(0)
    call_count = branch.count("_apt_install(")
    assert call_count >= 2, (
        f"apt branch should invoke _apt_install twice (core + libmlx5-dev "
        f"split); found {call_count} calls. Pre-fix had 1 call lumping "
        f"all 4 packages together."
    )


def test_rdma_userspace_separates_libmlx5_from_core():
    """libmlx5-dev must NOT appear in the same _apt_install call as
    perftest / rdma-core / libibverbs-dev. Strip docstring/comments
    first to avoid false-matching the explanatory text."""
    body = _helper_body()
    # Remove docstring + comment lines
    code_lines = []
    in_doc = False
    marker = None
    for ln in body.split("\n"):
        s = ln.strip()
        for q in ('"""', "'''"):
            if s.count(q) == 1:
                if not in_doc:
                    in_doc, marker = True, q
                elif q == marker:
                    in_doc, marker = False, None
                continue
        if in_doc or s.startswith("#"):
            continue
        code_lines.append(ln.split("#", 1)[0])
    code = "\n".join(code_lines)
    # The pre-fix string was a single argument containing all four
    # packages.
    bad_combined = re.search(
        r'_apt_install\(\s*["\'][^"\']*libmlx5-dev[^"\']*'
        r'(?:perftest|rdma-core|libibverbs-dev)',
        code,
    )
    assert bad_combined is None, (
        f"libmlx5-dev appears in the same _apt_install call as core "
        f"packages — splitting them was the v0.3.16 fix; this is a "
        f"regression. Match: {bad_combined.group(0)[:120]!r}"
    )


def test_rdma_userspace_mlx5_install_is_check_false():
    """The Mellanox-optional pass must NOT raise on failure (host
    without MOFED is normal, not an error). Either check=False or
    explicit returncode-handling is acceptable."""
    body = _helper_body()
    # Find the libmlx5-dev call site
    m = re.search(
        r'_apt_install\(\s*["\']libmlx5-dev["\'][^)]*\)',
        body,
    )
    assert m, "libmlx5-dev _apt_install call not found"
    call = m.group(0)
    assert "check=False" in call, (
        f"libmlx5-dev install must use check=False — otherwise a "
        f"MOFED-less host aborts the install. Call: {call}"
    )


def test_rdma_userspace_warns_on_mlx5_failure():
    """When libmlx5-dev install fails, helper must log a WARNING
    explaining why (not silent skip — operator should know).

    Uses ``.*?`` greedy across newlines instead of ``[^)]*`` because
    the warning text contains literal `(host lacks ... )` parens
    that would break a `)`-excluding regex."""
    body = _helper_body()
    assert re.search(
        r'self\.log\(\s*"libmlx5-dev not available.*?"WARNING"',
        body,
        re.DOTALL,
    ), (
        "helper should log a WARNING when libmlx5-dev install fails "
        "(operator needs to know MOFED-specific headers aren't "
        "available)"
    )


def test_install_dpdk_sh_splits_libmlx5_from_core_deps():
    """install_dpdk.sh's apt-get install of build deps must NOT
    include libmlx5-dev in the same batch as build-essential / meson
    / perftest. Otherwise ANY failure (including the MOFED case)
    fails the entire DPDK build-deps install."""
    src = open(_DPDK_SH).read()
    # Find the main deps_install_cmd assignment
    m = re.search(
        r'deps_install_cmd="[^"]+"',
        src,
    )
    assert m, "deps_install_cmd not found in install_dpdk.sh"
    cmd = m.group(0)
    assert "libmlx5-dev" not in cmd, (
        "libmlx5-dev appears in deps_install_cmd — must be split "
        "into a separate optional install. Pre-fix bundling it "
        "with build-essential + perftest meant MOFED-less hosts "
        "failed the whole DPDK build-deps install."
    )
    # The new optional command must exist
    assert "mlx5_install_cmd=" in src, (
        "install_dpdk.sh missing mlx5_install_cmd — the split "
        "Mellanox-optional pass must be a separate variable."
    )
    # Both core + mlx5 must mention libibverbs-dev / libmlx5-dev
    # respectively (sanity that the split is meaningful, not just
    # an empty rename).
    assert "libibverbs-dev" in cmd, \
        "deps_install_cmd lost libibverbs-dev — split must preserve core"
    mlx5_m = re.search(r'mlx5_install_cmd="[^"]+"', src)
    assert mlx5_m and "libmlx5-dev" in mlx5_m.group(0), \
        "mlx5_install_cmd must include libmlx5-dev"


def test_install_dpdk_sh_tolerates_mlx5_install_failure():
    """install_dpdk.sh must wrap the mlx5 install in a conditional
    that warns on failure rather than aborting. Pre-fix the whole
    deps batch was fatal if any package was missing."""
    src = open(_DPDK_SH).read()
    # Look for the conditional + log_warning fallback near
    # mlx5_install_cmd
    assert re.search(
        r'if\s+eval\s+"\$mlx5_install_cmd"', src,
    ), (
        "install_dpdk.sh should conditionally eval $mlx5_install_cmd "
        "and warn on failure (not abort the script)"
    )
    # Find the failure-branch log_warning
    assert re.search(
        r'log_warning\s+"libmlx5-dev not available', src,
    ), "install_dpdk.sh missing operator-readable warning on mlx5 install failure"
