"""v0.5.51 — install_dpdk.sh configures GRUB IOMMU + signals reboot;
/api/dpdk/status surfaces the reboot-required marker.

Audit findings C3 + C4 from the DPDK area sweep:

  C3 (CRITICAL): install_dpdk.sh has no GRUB/IOMMU setup at all.
       vfio-pci binding succeeds at step 8 even when IOMMU is
       off → kernel refuses vfio-pci attach → DPDK apps fail
       1 second after the operator sees "bind successful".

  C4 (CRITICAL): no reboot-needed signal. The sysctl persistence
       file is written but `sysctl --system` is never invoked;
       the running kernel keeps the old nr_hugepages until reboot.
       Worse, the operator has no way to know reboot is required
       — no marker file, no exit code, no API field.

v0.5.51 closes both:

  1. NEW `step_configure_iommu` between hugepages and NIC binding.
     Detects CPU vendor from /proc/cpuinfo, checks /proc/cmdline
     for existing params (idempotent), backs up /etc/default/grub
     with timestamp, appends `intel_iommu=on iommu=pt` (Intel)
     or `amd_iommu=on iommu=pt` (AMD) to GRUB_CMDLINE_LINUX_DEFAULT
     (preferred) or GRUB_CMDLINE_LINUX, runs `update-grub` (or
     `grub2-mkconfig` for RHEL family), marks reboot required.

  2. `netgen_mark_reboot_required(reason)` helper writes
     `/run/netgen-reboot-required` (or `/var/run/...` fallback)
     listing the reasons. `step_summary` surfaces a loud banner
     and exits with code 75 (EX_TEMPFAIL) when reboot is needed.

  3. `step_configure_hugepages` now runs `sysctl --system` after
     writing /etc/sysctl.d so the running kernel picks up the
     change immediately when possible.

  4. /api/dpdk/status reads the marker file and exposes
     `reboot_needed` + `reboot_reasons` so the admin chip can warn
     even after the install log has scrolled off.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SHELL = _REPO / "resources" / "dpdk" / "install_dpdk.sh"
_SERVER = _REPO / "run_tgen_server.py"


def _shell() -> str:
    return _SHELL.read_text()


def test_step_configure_iommu_defined():
    """A new `step_configure_iommu` function must exist."""
    assert "step_configure_iommu()" in _shell(), (
        "step_configure_iommu() missing — installer still won't "
        "set up IOMMU."
    )


def test_step_configure_iommu_wired_into_main_flow():
    """The new step must run between hugepages and NIC binding —
    binding to vfio-pci is the operation that needs IOMMU active."""
    s = _shell()
    main_block = re.search(
        r"# Run installation steps[\s\S]+?step_summary\s*\n\}",
        s,
    )
    assert main_block, "main() install-steps block not located"
    body = main_block.group(0)
    # Match the indented call lines specifically — the comment at
    # the top of main() mentions step_configure_iommu by name, so
    # plain `body.find(...)` would hit the comment first.
    hp_idx = body.find("\n    step_configure_hugepages\n")
    iommu_idx = body.find("\n    step_configure_iommu\n")
    bind_idx = body.find("\n    step_nic_binding\n")
    assert hp_idx >= 0 and iommu_idx >= 0 and bind_idx >= 0, (
        "Expected step ordering not found in main()"
    )
    assert hp_idx < iommu_idx < bind_idx, (
        "step_configure_iommu must run between hugepages and NIC "
        "binding (so cmdline is enqueued before vfio-pci touch). "
        f"Got hp={hp_idx} iommu={iommu_idx} bind={bind_idx}"
    )


def test_iommu_detects_cpu_vendor():
    """Must detect Intel vs AMD from /proc/cpuinfo. Hardcoding
    one vendor would break the other class of host."""
    s = _shell()
    iommu_block = re.search(
        r"step_configure_iommu\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    assert iommu_block
    body = iommu_block.group(0)
    assert "GenuineIntel" in body and "AuthenticAMD" in body, (
        "step_configure_iommu must detect both Intel + AMD "
        "vendors from /proc/cpuinfo"
    )
    assert "intel_iommu=on" in body and "amd_iommu=on" in body, (
        "Both vendor-specific cmdline params must be set"
    )
    assert "iommu=pt" in body, (
        "iommu=pt (passthrough) must be added regardless of vendor"
    )


def test_iommu_skips_when_live_kernel_already_has_params():
    """Idempotency: if /proc/cmdline already has the params, the
    step must be a no-op. Otherwise it'd duplicate entries on
    every install run."""
    s = _shell()
    iommu_block = re.search(
        r"step_configure_iommu\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    body = iommu_block.group(0)
    assert "/proc/cmdline" in body, (
        "step_configure_iommu must check /proc/cmdline for "
        "already-active IOMMU — otherwise re-runs duplicate params"
    )


def test_iommu_backs_up_grub_before_edit():
    """Damage to /etc/default/grub leaves the box unbootable.
    Must back up before sed-ing."""
    s = _shell()
    iommu_block = re.search(
        r"step_configure_iommu\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    body = iommu_block.group(0)
    assert re.search(
        r"cp\s+\"?\$\{?grub_file\}?\"?\s+\"?\$\{?backup\}?",
        body,
    ), (
        "step_configure_iommu doesn't back up /etc/default/grub "
        "before editing — a sed failure could brick boot"
    )


def test_iommu_supports_both_update_grub_and_grub2_mkconfig():
    """Debian/Ubuntu use `update-grub`; RHEL family uses
    `grub2-mkconfig`. Both paths must be present."""
    s = _shell()
    iommu_block = re.search(
        r"step_configure_iommu\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    body = iommu_block.group(0)
    assert "update-grub" in body, "no update-grub path"
    assert "grub2-mkconfig" in body, "no grub2-mkconfig path"


def test_netgen_mark_reboot_required_helper_exists():
    """The helper that tracks reboot-needed state must exist."""
    s = _shell()
    assert "netgen_mark_reboot_required(" in s, (
        "netgen_mark_reboot_required() helper missing"
    )


def test_netgen_mark_reboot_required_writes_marker_file():
    """Must write to /run/netgen-reboot-required (or /var/run/...
    fallback) so the API endpoint can pick it up after the install
    log has scrolled off."""
    s = _shell()
    helper = re.search(
        r"netgen_mark_reboot_required\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    assert helper
    body = helper.group(0)
    assert "/run/netgen-reboot-required" in body, (
        "helper doesn't write to /run/netgen-reboot-required"
    )
    assert "/var/run/netgen-reboot-required" in body, (
        "helper doesn't fall back to /var/run/... for older systems"
    )


def test_step_summary_exits_75_when_reboot_required():
    """`step_summary` must exit 75 (EX_TEMPFAIL) when REBOOT_REQUIRED
    is set, so the wrapping admin endpoint can detect 'success but
    needs reboot' distinctly from plain success."""
    s = _shell()
    summary = re.search(
        r"step_summary\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    assert summary
    body = summary.group(0)
    assert re.search(r"REBOOT_REQUIRED", body), (
        "step_summary doesn't reference REBOOT_REQUIRED"
    )
    assert re.search(r"\bexit\s+75\b", body), (
        "step_summary doesn't exit 75 — admin endpoint can't "
        "distinguish 'reboot needed' from plain success"
    )


def test_hugepages_step_applies_sysctl_system():
    """The hugepages step writes to /etc/sysctl.d, but pre-fix
    never ran `sysctl --system` — so the running kernel kept the
    old value until reboot. The fix must apply it now."""
    s = _shell()
    # Locate the hugepages step.
    hp = re.search(
        r"step_configure_hugepages\(\)[\s\S]+?^\}",
        s,
        re.MULTILINE,
    )
    assert hp
    body = hp.group(0)
    assert re.search(r"sysctl\s+--system", body), (
        "step_configure_hugepages doesn't call `sysctl --system` "
        "after writing /etc/sysctl.d/... — reboot required to "
        "see the new nr_hugepages."
    )


def test_dpdk_status_exposes_reboot_needed_field():
    """/api/dpdk/status must read the marker file and expose
    `reboot_needed` + `reboot_reasons` in the response."""
    src = _SERVER.read_text()
    status_handler = re.search(
        r"def dpdk_status\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert status_handler
    body = status_handler.group(0)
    assert '"reboot_needed"' in body, (
        "/api/dpdk/status doesn't surface reboot_needed in JSON "
        "— admin chip can't warn the operator after install log "
        "is gone"
    )
    assert '"reboot_reasons"' in body, (
        "/api/dpdk/status doesn't surface reboot_reasons — "
        "operator has to guess what needs rebooting"
    )
    assert "netgen-reboot-required" in body, (
        "/api/dpdk/status doesn't read the marker file the shell "
        "writes"
    )


def test_pyproject_version_at_least_0551():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 51), (
        f"Version {m.group(1)} < 0.5.51"
    )
