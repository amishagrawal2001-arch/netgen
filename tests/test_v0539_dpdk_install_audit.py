"""v0.5.39 — DPDK install audit fixes.

Operator request after the v0.5.38 close-out: "audit dpdk install
and make sure all the steps are taken care, we need to provide
user simple and easy install experience."

Four gaps closed:

  1. /mnt/huge mount lost on reboot — no /etc/fstab write
  2. NIC vfio-pci bind lost on reboot — no persistence mechanism
  3. Diagnostics doesn't surface /mnt/huge mount state
  4. "Already ready" dialog shows empty action list — operator-
     confusing

These tests pin each fix.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
_MAKE_READY = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)
_DIAGNOSTICS = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_diagnostics_dialog.py"
)


# ─────────────── 1. /mnt/huge fstab persistence ─────────────────────


def test_hugepages_endpoint_writes_fstab():
    """After the runtime hugetlbfs mount succeeds, /api/dpdk/hugepages
    must append an /etc/fstab entry so the mount survives reboot."""
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = m.group(0)
    assert "/etc/fstab" in body, (
        "/api/dpdk/hugepages doesn't reference /etc/fstab. "
        "Hugetlbfs mount won't survive reboot."
    )
    assert "hugetlbfs" in body, (
        "fstab write doesn't include 'hugetlbfs' filesystem type"
    )


def test_hugepages_fstab_write_is_idempotent():
    """If /etc/fstab already has a hugetlbfs entry, the endpoint
    must NOT append a duplicate. Match the existence-check before
    the append."""
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = m.group(0)
    # The write must be guarded by reading the existing file +
    # checking for the mount point / hugetlbfs string.
    assert re.search(
        r'open\(fstab_path,\s*["\']r["\']',
        body,
    ), (
        "fstab write doesn't read existing content first — would "
        "duplicate the entry on every hugepages call."
    )
    assert "in existing" in body or "not in existing" in body, (
        "No check for existing fstab content — duplication risk"
    )


# ─────────────── 2. NIC bind persistence ────────────────────────────


def test_bind_persistence_helpers_exist():
    """Two helper functions: _dpdk_persist_bind, _dpdk_unpersist_bind."""
    src = _SERVER.read_text()
    assert "def _dpdk_persist_bind(" in src, (
        "_dpdk_persist_bind helper missing — bind endpoint can't "
        "register binds for reboot persistence."
    )
    assert "def _dpdk_unpersist_bind(" in src, (
        "_dpdk_unpersist_bind helper missing — unbind endpoint "
        "can't remove the registry entry, leading to phantom "
        "re-binds on reboot."
    )


def test_bind_registry_file_at_canonical_path():
    """The registry must live at /etc/netgen/dpdk-interfaces.json.
    /etc/netgen/ is the project's chosen FHS location for state."""
    src = _SERVER.read_text()
    assert "/etc/netgen/dpdk-interfaces.json" in src, (
        "Bind registry isn't at /etc/netgen/dpdk-interfaces.json. "
        "Operators inspecting via SSH won't find it at the "
        "documented path."
    )


def test_rebind_systemd_unit_installed_on_first_bind():
    """A systemd oneshot unit (netgen-dpdk-rebind.service) must
    be auto-created on first bind. Idempotent on subsequent binds."""
    src = _SERVER.read_text()
    assert "netgen-dpdk-rebind.service" in src, (
        "No netgen-dpdk-rebind.service unit reference — reboot "
        "re-bind isn't scheduled."
    )
    assert "_ensure_dpdk_rebind_unit_installed" in src, (
        "No installer function for the rebind unit"
    )


def test_rebind_unit_orders_after_modules_load():
    """The unit's [Unit] section must include
    After=systemd-modules-load.service so vfio-pci is loaded
    BEFORE the bind helper runs (otherwise dpdk-devbind.py fails
    with 'driver vfio-pci not found')."""
    src = _SERVER.read_text()
    assert "After=systemd-modules-load.service" in src, (
        "Rebind unit doesn't order After systemd-modules-load — "
        "would race the vfio-pci load and fail to bind."
    )


def test_rebind_helper_script_path():
    """The helper script must live at /usr/local/sbin/ (canonical
    sbin location for admin-installed binaries)."""
    src = _SERVER.read_text()
    assert "/usr/local/sbin/netgen-dpdk-rebind" in src, (
        "Rebind helper not at /usr/local/sbin/ — wrong FHS slot."
    )


def test_bind_endpoint_calls_persist():
    """On bind success, the endpoint must call _dpdk_persist_bind."""
    src = _SERVER.read_text()
    bind_m = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = bind_m.group(0)
    assert "_dpdk_persist_bind(" in body, (
        "dpdk_bind doesn't call _dpdk_persist_bind on success — "
        "the runtime bind succeeds but won't be re-applied at boot."
    )


def test_unbind_endpoint_calls_unpersist():
    """On unbind success, the endpoint must call _dpdk_unpersist_bind
    so a reboot doesn't re-bind the NIC the operator just unbound."""
    src = _SERVER.read_text()
    unbind_m = re.search(
        r"def dpdk_unbind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = unbind_m.group(0)
    assert "_dpdk_unpersist_bind(" in body, (
        "dpdk_unbind doesn't clear the registry entry — reboot "
        "would re-bind a NIC the operator explicitly unbound."
    )


# ─────────────── 3. Diagnostics /mnt/huge surface ───────────────────


def test_dpdk_status_includes_hugepages_mounted():
    """/api/dpdk/status response must include hugepages_mounted +
    hugepages_mount_point fields so Diagnostics can render them."""
    src = _SERVER.read_text()
    status_m = re.search(
        r"def dpdk_status\(\)[\s\S]+?return jsonify\(\{[\s\S]+?\}\),\s*200",
        src,
    )
    body = status_m.group(0)
    assert '"hugepages_mounted"' in body, (
        "/api/dpdk/status response doesn't include hugepages_mounted "
        "— Diagnostics can't surface mount state."
    )
    assert "/proc/mounts" in body, (
        "Status check doesn't read /proc/mounts — has no way to "
        "determine whether hugetlbfs is mounted."
    )


def test_diagnostics_dialog_renders_mount_row():
    """Diagnostics dialog must include a row for the mount state.
    Match the canonical label."""
    src = _DIAGNOSTICS.read_text()
    assert "Hugepages mounted" in src, (
        "Diagnostics dialog doesn't render a 'Hugepages mounted' "
        "row — operators won't see the mount-state ✓/✗."
    )
    assert "hugepages_mounted" in src, (
        "Diagnostics dialog doesn't read the hugepages_mounted "
        "field from /api/dpdk/status"
    )


# ─────────────── 4. "Already ready" UX ──────────────────────────────


def test_already_ready_path_shows_summary_not_empty():
    """When DPDK is already ready, the dialog must show a positive
    summary of what's in place — not just "Nothing to do" + empty
    list. Look for the summary-rows pattern."""
    src = _MAKE_READY.read_text()
    # Locate the is_dpdk_ready branch.
    m = re.search(
        r"if\s+is_dpdk_ready\(data\):[\s\S]+?return",
        src,
    )
    assert m, "is_dpdk_ready branch not found in dialog"
    block = m.group(0)
    # Pre-v0.5.39 the branch just set _detail.setText("✓ DPDK is
    # already ready...") and returned. v0.5.39 builds a row list.
    assert "Current state" in block or "rows_html" in block, (
        "Already-ready branch doesn't show a state summary — "
        "operator sees just 'ready' with no detail of what's done."
    )


def test_already_ready_run_button_stays_enabled():
    """In the already-ready path, Run All Steps must stay ENABLED
    so the operator can trigger a re-bind for a different NIC.
    Pre-fix it was 'Nothing to do' + disabled."""
    src = _MAKE_READY.read_text()
    m = re.search(
        r"if\s+is_dpdk_ready\(data\):[\s\S]+?return",
        src,
    )
    block = m.group(0)
    assert "setEnabled(True)" in block, (
        "Already-ready branch leaves Run button disabled — "
        "operator can't re-bind a new NIC without closing + "
        "re-opening the dialog."
    )
    # And the button text should indicate what the next action
    # actually does (bind a NIC), not the misleading "Nothing to do".
    assert "Bind another NIC" in block or "Bind NIC" in block or \
           "another" in block.lower(), (
        "Run button label doesn't communicate what it does in the "
        "already-ready state."
    )


def test_pyproject_version_at_least_0539():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 39), (
        f"Version {m.group(1)} < 0.5.39"
    )
