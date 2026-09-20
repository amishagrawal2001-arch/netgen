"""v0.5.371 — install_dpdk.sh must never fail on infra grounds.

Mirrors v0.5.369's install_rdma.sh work — the "must never fail
for any reason" bar now applies to DPDK too. Same failure classes
were flagged by the install-codebase audit:

  * REQUIRED apt batch lacked --fix-missing (any single 404 aborted)
  * git binary hard-required but not auto-installed
  * git clone had no retry / no timeout / no mirror fallback
  * Step 5 meson/ninja: three `exit 1` sites on transient toolchain
    glitches, no build-dir salvage
  * hugepages `echo > /sys/.../nr_hugepages` unguarded — silent
    abort under `set -euo pipefail` on memory pressure or sysfs
    perm issues
  * IOMMU grep at Step 7b matched commented example lines →
    "already configured" false-positive → IOMMU never actually on
  * No `trap` on INT/TERM → Ctrl-C mid-install left partial state
    with no summary

Fixes carry marker `v0.5.371 (audit install-dpdk-must-not-fail)`.
Only remaining non-zero exits are:
  * root check (structural)
  * apt-based distro check (structural)
  * three user-consent "Continue anyway? no" exits
  * unknown CLI arg
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_SCRIPT = _REPO / "resources" / "dpdk" / "install_dpdk.sh"


def _src() -> str:
    return _SCRIPT.read_text()


# ─── syntax + markers ───


def test_bash_syntax_valid():
    r = subprocess.run(
        ["bash", "-n", str(_SCRIPT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_marker_present():
    """Multiple sites carry the v0.5.371 marker so a future edit
    knows which lines belong to this audit-fix."""
    src = _src()
    assert src.count("v0.5.371 (audit install-dpdk-must-not-fail)") >= 8


# ─── every infra-failure exit path is gone ───


def test_no_step5_meson_ninja_exit_1_survived():
    """Pre-fix Step 5 had four `exit 1` sites: meson setup, meson
    reconfigure (--wipe), ninja compile, ninja install. All must
    be gone (replaced by return 1 + status flag)."""
    src = _src()
    # Locate step_build_dpdk body.
    fn = re.search(
        r"step_build_dpdk\(\)\s*\{[\s\S]+?\n\}\n",
        src,
    )
    assert fn
    body = fn.group(0)
    # No exit statements in the function body.
    non_comment = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert re.search(r"\bexit\s+\d", non_comment) is None, (
        "step_build_dpdk still has bare `exit N` outside comments"
    )
    # And the fix's status-flag writes are present.
    assert '_netgen_dpdk_set dpdk_built failed' in body \
        or '_netgen_dpdk_set dpdk_built built_not_installed' in body
    assert '_netgen_dpdk_set dpdk_built ok' in body


def test_step6_tx_worker_exits_gone():
    """Pre-fix Step 6 tx_worker had three exit 1 sites (missing
    DPDK libs, missing binary, failed rebuild). All must be gone."""
    src = _src()
    fn = re.search(
        r"step_build_tx_worker\(\)\s*\{[\s\S]+?\n\}\n",
        src,
    )
    assert fn
    body = fn.group(0)
    non_comment = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert re.search(r"\bexit\s+\d", non_comment) is None, (
        "step_build_tx_worker still has bare `exit N`"
    )


def test_step3_git_clone_exits_gone():
    """Pre-fix Step 3 had exits at git-not-found, mkdir-fail,
    cd-fail, clone-fail, incomplete-tree. Only structural fails
    remain (mkdir/cd errors kept but with status flag + return
    instead of exit)."""
    src = _src()
    fn = re.search(
        r"step_clone_dpdk\(\)\s*\{[\s\S]+?\n\}\n",
        src,
    )
    assert fn
    body = fn.group(0)
    non_comment = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert re.search(r"\bexit\s+\d", non_comment) is None, (
        "step_clone_dpdk still has bare `exit N`"
    )


def test_all_bare_exits_are_pre_flight_or_user_consent():
    """Post-fix the only allowed bare `exit N` statements are
    structural pre-flight (root check, apt-only distro check),
    user-consent 'no' branches inside interactive prompts, and
    the unknown-CLI-arg guard. Enumerate + verify count is
    bounded."""
    src = _src()
    exit_lines = [
        (i + 1, line.strip())
        for i, line in enumerate(src.splitlines())
        if re.search(r"^\s*exit\s+\d", line)
        and not line.lstrip().startswith("#")
    ]
    # Expected sites (rough count — help exit 0, root exit 1,
    # distro exit 1, 3 user-consent exit 1's, unknown-arg exit 1):
    # about 6 to 8 total.
    assert 6 <= len(exit_lines) <= 10, (
        f"Unexpected bare exit count: {len(exit_lines)} — "
        f"either lost a structural check or added a new "
        f"infra-failure exit. Sites: {exit_lines}"
    )


# ─── apt tolerance (mirror v0.5.369) ───


def test_deps_install_uses_fix_missing():
    src = _src()
    # The batched deps install command must include --fix-missing.
    m = re.search(
        r'deps_install_cmd="[^"]*--fix-missing[^"]*"',
        src,
    )
    assert m, (
        "deps_install_cmd is missing --fix-missing — a single 404 "
        "on a stale external repo will still abort the whole batch"
    )


def test_per_package_retry_covers_essentials():
    """Tier 2 fallback: per-package retry on essentials so a
    single bad .deb can't block the rest."""
    src = _src()
    m = re.search(
        r"for pkg in ([^;]+); do",
        src,
    )
    assert m, "Per-package retry loop not found"
    pkgs = m.group(1).split()
    for _needed in ("build-essential", "meson", "ninja-build",
                    "pkg-config", "libnuma-dev", "libelf-dev",
                    "python3-pyelftools"):
        assert _needed in pkgs, (
            f"Per-package retry missing essential {_needed}"
        )


def test_dpkg_query_post_check_on_essentials():
    """Tier 3 post-check: dpkg-query on the essentials. If any
    is missing we set deps_ready=degraded and let Step 5 report
    the shortfall — but the script keeps going."""
    src = _src()
    assert 'dpkg-query -W -f=' in src
    # And the deps_ready status flag branches on it.
    assert "_netgen_dpdk_set deps_ready ok" in src
    assert "_netgen_dpdk_set deps_ready degraded" in src


def test_pyelftools_hard_gate_softened():
    """The v0.5.30 pyelftools hard-gate exited 1. v0.5.371
    softens it to a loud warning + deps_ready=degraded so Step 5
    can report the failure in context."""
    src = _src()
    # Locate the pyelftools gate.
    m = re.search(
        r'python3 -c "import elftools"[\s\S]{0,1500}?fi',
        src,
    )
    assert m
    body = m.group(0)
    non_comment = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert re.search(r"\bexit\s+\d", non_comment) is None, (
        "pyelftools hard-gate still has `exit N` — should be a "
        "warning + status flag"
    )


# ─── git clone retry + auto-install ───


def test_git_clone_has_retry_loop():
    """Step 3 must attempt the clone at least twice (full clone
    → shallow → shallow-to-mirror). Structural check: a for-loop
    over `_clone_attempts` array."""
    src = _src()
    assert "_clone_attempts=(" in src
    # And uses --depth 1 in at least one attempt.
    assert "--depth 1" in src
    # And has a GitHub mirror as fallback.
    assert "github.com/DPDK/dpdk" in src


def test_git_binary_auto_installed_if_missing():
    """Pre-fix: `command -v git || exit 1` — no auto-install. Now
    the script tries apt-get install git before failing."""
    src = _src()
    # Locate the git check.
    m = re.search(
        r'command -v git[\s\S]{0,800}?fi',
        src,
    )
    assert m
    body = m.group(0)
    assert "apt-get install" in body and "git" in body, (
        "git binary not auto-installed when missing"
    )


# ─── hugepages sysfs guard ───


def test_hugepages_sysfs_write_wrapped():
    """The `echo > /sys/.../nr_hugepages` must be inside a
    `set +e ... set -e` block so a sysfs perm error or memory
    pressure doesn't abort the script."""
    src = _src()
    m = re.search(
        r'set \+e\s*\n\s*echo "\$pages" >[\s\S]{0,400}?set -e',
        src,
    )
    assert m, (
        "Hugepages sysfs write not wrapped in set +e / set -e — "
        "a failure still aborts under pipefail"
    )


def test_hugepages_reports_partial_allocation():
    """Partial-allocation state must be distinguished from
    total-fail. When new_total > 0 but < requested, report
    'partial' not 'failed'. Structural check: the verify block
    after the sysfs write branches to at least 'ok', 'partial',
    and 'failed' status values."""
    src = _src()
    # Anchor on the sysfs write itself so we pick the SET-block
    # verify, not the pre-check.
    write_pos = src.index("echo \"$pages\" > /sys/kernel/mm/hugepages")
    tail = src[write_pos:write_pos + 3000]
    for _state in ("_netgen_dpdk_set hugepages_configured ok",
                   "_netgen_dpdk_set hugepages_configured partial",
                   "_netgen_dpdk_set hugepages_configured failed"):
        assert _state in tail, (
            f"Hugepages verify block missing state flag: {_state}"
        )


# ─── IOMMU grep comment-aware ───


def test_iommu_grep_filters_comment_lines():
    """Pre-fix the GRUB grep matched `#GRUB_CMDLINE_LINUX_DEFAULT
    = "... intel_iommu=on"` (commented example). Post-fix must
    filter comment lines FIRST via `grep -v '^\\s*#'` (or an
    equivalent literal-hash prefix filter) BEFORE the
    `GRUB_CMDLINE_LINUX*=.*${needed_param}` grep."""
    src = _src()
    # Locate the specific GRUB idempotency-check region — starts
    # at the anchored-check comment, ends at the `iommu=pt` grep.
    start = src.index("Anchored idempotency check against the file")
    end = src.index("iommu=pt", start) + 200
    body = src[start:end]
    # The comment-filter must appear before the GRUB_CMDLINE grep.
    assert "grep -v '^\\s*#'" in body or "grep -v '^#'" in body, (
        "IOMMU grep still matches commented example lines — "
        "false-positive as 'already configured' → IOMMU never on"
    )


# ─── trap handler ───


def test_trap_handler_installed():
    """INT + TERM traps must call the same reporting function
    that dumps status so the operator sees what step was in
    flight when the interrupt landed."""
    src = _src()
    assert re.search(r"trap\s+'[^']*_netgen_dpdk_trap[^']*'\s+INT", src)
    assert re.search(r"trap\s+'[^']*_netgen_dpdk_trap[^']*'\s+TERM", src)


def test_trap_handler_reports_status():
    """The trap function itself must iterate the status array
    and emit each key=value so the log stream has actionable
    detail when the script is killed."""
    src = _src()
    m = re.search(
        r"_netgen_dpdk_trap\(\)\s*\{[\s\S]+?\n\}\n",
        src,
    )
    assert m
    body = m.group(0)
    assert "NETGEN_DPDK_STATUS" in body


# ─── final summary ───


def test_final_summary_iterates_status_array():
    """step_summary must render each entry of NETGEN_DPDK_STATUS
    with ok=success/unknown=info/other=warning coloring, so the
    log stream and the /admin log tail both surface the actual
    state instead of a hardcoded 'success' line."""
    src = _src()
    m = re.search(
        r"step_summary\(\)\s*\{[\s\S]+?\n\}\n",
        src,
    )
    assert m
    body = m.group(0)
    assert "NETGEN_DPDK_STATUS" in body, (
        "step_summary doesn't iterate status array — banner still "
        "reports fake success on failed installs"
    )
    # And the overall-status flip that determines success vs
    # warnings.
    assert "_overall_ok" in body


def test_status_array_covers_all_key_steps():
    """The status array must include an entry for every major
    step so a Ctrl-C at any point produces a specific message."""
    src = _src()
    # Locate the array init.
    m = re.search(
        r"NETGEN_DPDK_STATUS=\(\s*([^)]+)\s*\)",
        src,
    )
    assert m
    entries = m.group(1)
    for _key in ("source_ready", "deps_ready", "dpdk_built",
                 "tx_worker_built", "hugepages_configured",
                 "iommu_configured"):
        assert _key in entries, (
            f"Status array missing '{_key}' — interrupt at that "
            f"step won't emit its state"
        )


# ─── regression guards ───


def test_v0555_apt_log_capture_intact():
    """v0.5.55's tee to /tmp/dpdk_deps_install.log must survive."""
    src = _src()
    assert "/tmp/dpdk_deps_install.log" in src


def test_v0530_pyelftools_probe_intact():
    """v0.5.30 pyelftools probe must still fire — only the
    exit-on-fail changed to warn-on-fail."""
    src = _src()
    assert 'python3 -c "import elftools"' in src


def test_v0551_reboot_marker_intact():
    """v0.5.51 netgen-reboot-required marker file logic
    survives."""
    src = _src()
    assert "netgen_mark_reboot_required" in src


def test_v0369_install_rdma_marker_intact():
    """Sanity: v0.5.369 marker on install_rdma.sh (sibling
    script) still there since v0.5.371 mirrored its pattern."""
    rdma = (_REPO / "resources" / "dpdk" / "install_rdma.sh").read_text()
    assert "v0.5.369 (audit rdma-install-must-not-fail)" in rdma


def test_pyproject_version_at_least_0571():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 371), (
        f"Version {m.group(1)} < 0.5.371"
    )
