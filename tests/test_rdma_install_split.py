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
from pathlib import Path

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


def test_install_dpdk_sh_no_longer_installs_libmlx5():
    """v0.5.27 update: libmlx5-dev moved to install_rdma.sh entirely.
    install_dpdk.sh must not reference it anymore — that's the RDMA
    stack's territory now. Pre-v0.5.27 this test enforced libmlx5-dev
    being in a SEPARATE batch from core deps; v0.5.27 enforces the
    stronger 'not in install_dpdk.sh at all' invariant."""
    src = open(_DPDK_SH).read()
    m = re.search(r'deps_install_cmd="[^"]+"', src)
    assert m, "deps_install_cmd not found in install_dpdk.sh"
    cmd = m.group(0)
    assert "libmlx5-dev" not in cmd, (
        "libmlx5-dev appears in deps_install_cmd — moved to "
        "install_rdma.sh in v0.5.27. See test_v0527_rdma_install_split."
    )
    # mlx5_install_cmd must be gone entirely from install_dpdk.sh.
    assert "mlx5_install_cmd" not in src, (
        "install_dpdk.sh still has mlx5_install_cmd — moved to "
        "install_rdma.sh in v0.5.27. The Mellanox optional pass "
        "now happens during Setup RDMA, not Setup DPDK."
    )


def test_install_rdma_sh_handles_mlx5_install_failure():
    """v0.5.27 update: the Mellanox-MOFED-optional libmlx5-dev install
    now lives in install_rdma.sh. The fault-tolerant 'try to install,
    warn if it fails' pattern moved with it. install_rdma.sh must
    keep the same shape: separate batch + non-fatal failure."""
    rdma_sh = Path(_DPDK_SH).parent / "install_rdma.sh"
    src = rdma_sh.read_text()
    # Mellanox-only batch must be a separate variable
    assert "mlx5_apt_cmd" in src or "mlx5_install_cmd" in src, (
        "install_rdma.sh doesn't split mlx5 into a separate apt batch "
        "— failures on non-MOFED hosts would poison the core install."
    )
    # And the if/else around the optional install must log a warning
    # rather than fail the script.
    assert re.search(r"if\s+eval\s+\"\$mlx5_apt_cmd\"", src) or \
           re.search(r"if\s+eval\s+\"\$mlx5_install_cmd\"", src), (
        "install_rdma.sh should conditionally eval the mlx5 batch + "
        "warn on failure (not abort the script)"
    )
    assert re.search(r"log_warning.*libmlx5-dev install failed", src, re.IGNORECASE) or \
           re.search(r"log_warning.*MOFED", src), (
        "install_rdma.sh missing operator-readable warning on mlx5 "
        "install failure"
    )
