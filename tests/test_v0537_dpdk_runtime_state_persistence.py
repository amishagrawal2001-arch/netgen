"""v0.5.37 — /api/dpdk/hugepages + /api/dpdk/load_modules persist
their runtime changes across reboots.

Operator-reported on srv06 (Jun 8 2026, post v0.5.34 Reboot Server
button worked):

  Diagnostics:
    ✓ DPDK installed (libdpdk)
    ✓ tx_worker binary
    ✗ Hugepages configured           ← regressed after reboot
    ✓ IOMMU enabled in kernel
    ✓ vfio module loaded
    ✗ vfio-pci module loaded         ← regressed after reboot

The Make DPDK Ready dialog had previously reported both as ✓.
Then the operator rebooted (testing the v0.5.34 Reboot Server
button). Hugepages allocation and vfio modprobe were RUNTIME-ONLY
side effects — no /etc/sysctl.d/ or /etc/modules-load.d/ writes
existed, so the kernel state was lost on the next boot.

Pre-v0.5.37:

  /api/dpdk/hugepages:
    echo $N > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
    (then mount /mnt/huge)
    # No sysctl.d write. systemd-sysctl has nothing to restore.

  /api/dpdk/load_modules:
    modprobe vfio
    modprobe vfio-pci
    # No modules-load.d write. systemd-modules-load doesn't know
    # to reload them.

v0.5.37 adds persistence writes at the end of each endpoint's
success path:

  /api/dpdk/hugepages → /etc/sysctl.d/99-netgen-hugepages.conf
    vm.nr_hugepages = N

  /api/dpdk/load_modules → /etc/modules-load.d/netgen-dpdk.conf
    vfio
    vfio-pci

Both writes are best-effort — if /etc isn't writable we log a
warning but the runtime change has already succeeded, so the
endpoint returns success either way (caller polls `persisted`
field in the response if they care).

These tests pin both writes. Anyone removing them earns a test
failure here, not the next reboot-induced state-loss surprise.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _hugepages_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef _parse_dpdk_devbind_status|\ndef [a-z])",
        src,
    )
    assert m, "dpdk_hugepages body not found"
    return m.group(0)


def _load_modules_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_load_modules\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    assert m, "dpdk_load_modules body not found"
    return m.group(0)


# ────────────────── /api/dpdk/hugepages persistence ─────────────────


def test_hugepages_writes_sysctl_d_persistence_file():
    """After runtime sysfs allocation succeeds, the endpoint must
    write the canonical /etc/sysctl.d/99-netgen-hugepages.conf so
    systemd-sysctl reapplies vm.nr_hugepages on every boot."""
    body = _hugepages_body()
    assert "/etc/sysctl.d/99-netgen-hugepages.conf" in body, (
        "/api/dpdk/hugepages doesn't write to "
        "/etc/sysctl.d/99-netgen-hugepages.conf. Hugepages are "
        "lost on reboot."
    )
    # Pin the canonical sysctl key.
    assert "vm.nr_hugepages" in body, (
        "Persistence file doesn't set `vm.nr_hugepages` — wrong key."
    )


def test_hugepages_persistence_after_runtime_success():
    """The persistence write must happen AFTER the sysfs write
    has succeeded — otherwise we'd persist a value the kernel
    couldn't allocate. Match the order: hugepage_file write happens
    BEFORE the sysctl.d write."""
    body = _hugepages_body()
    sysfs_pos = body.find("nr_hugepages")
    persist_pos = body.find("/etc/sysctl.d/99-netgen-hugepages.conf")
    assert sysfs_pos >= 0 and persist_pos >= 0, "Required tokens not found"
    assert sysfs_pos < persist_pos, (
        "Persistence write to /etc/sysctl.d/ happens BEFORE the "
        "sysfs allocation. If sysfs write fails, we'd still persist "
        "the requested count → kernel tries again on next boot and "
        "still fails. Reorder: sysfs first, persist on success."
    )


def test_hugepages_persistence_is_best_effort():
    """If /etc isn't writable (read-only root, container, etc.) the
    persistence write may fail. The runtime allocation has already
    succeeded by then, so the endpoint must NOT return 500 — it
    should log a warning and surface the persistence failure in
    the JSON response."""
    body = _hugepages_body()
    # Look for try/except around the persistence write.
    persist_block = re.search(
        r"persist_path\s*=\s*[\"']/etc/sysctl\.d/99-netgen-hugepages\.conf[\"']"
        r"[\s\S]+?return\s+jsonify",
        body,
    )
    assert persist_block, "Persistence block not located"
    block = persist_block.group(0)
    assert "try:" in block and "except" in block, (
        "Persistence write isn't wrapped in try/except — a "
        "PermissionError would crash the request and the runtime "
        "allocation looks like it failed."
    )
    assert "logging.warning" in block, (
        "Failed persistence doesn't log a warning — operator has "
        "no signal that the file wasn't written."
    )


def test_hugepages_response_includes_persisted_field():
    """The JSON response must include a `persisted: bool` field so
    the client can warn the operator if persistence failed."""
    body = _hugepages_body()
    # Locate the success-path jsonify.
    success_resp = re.search(
        r'return jsonify\(\{[\s\S]+?"success":\s*True[\s\S]+?\}\),\s*200',
        body,
    )
    assert success_resp, "Success-path jsonify not found"
    resp = success_resp.group(0)
    assert '"persisted"' in resp or "'persisted'" in resp, (
        "Success response doesn't expose `persisted` — client "
        "can't tell whether the change will survive a reboot."
    )


# ───────────── /api/dpdk/load_modules persistence ───────────────────


def test_load_modules_writes_modules_load_d_persistence_file():
    """After successful modprobe calls, the endpoint must write
    /etc/modules-load.d/netgen-dpdk.conf so systemd-modules-load
    auto-loads vfio + vfio-pci on every boot."""
    body = _load_modules_body()
    assert "/etc/modules-load.d/netgen-dpdk.conf" in body, (
        "/api/dpdk/load_modules doesn't write to "
        "/etc/modules-load.d/netgen-dpdk.conf. vfio + vfio-pci "
        "won't auto-load on next boot."
    )


def test_load_modules_persists_each_module():
    """The persistence file must contain each module name on its own
    line (the format systemd-modules-load expects). The current
    module set is `vfio` + `vfio-pci`."""
    body = _load_modules_body()
    # The write loop should iterate modules_to_load.
    assert re.search(
        r"for\s+module\s+in\s+modules_to_load:[\s\S]{0,200}?pf\.write",
        body,
    ), (
        "Persistence write doesn't iterate modules_to_load — "
        "modules-load.d file would be empty or partial."
    )


def test_load_modules_persistence_is_best_effort():
    """Same as hugepages — runtime modprobe succeeds first; if /etc
    isn't writable, log warning + return success with a clear
    `persisted` flag. Don't 500 on a write that failed AFTER the
    main action succeeded."""
    body = _load_modules_body()
    persist_block = re.search(
        r"persist_path\s*=\s*[\"']/etc/modules-load\.d/netgen-dpdk\.conf[\"']"
        r"[\s\S]+?return\s+jsonify",
        body,
    )
    assert persist_block, "Persistence block not located"
    block = persist_block.group(0)
    assert "try:" in block and "except" in block, (
        "Persistence write isn't wrapped in try/except."
    )
    assert "logging.warning" in block, (
        "Failed persistence doesn't log a warning."
    )


def test_load_modules_response_includes_persisted_field():
    body = _load_modules_body()
    success_resp = re.search(
        r'return jsonify\(\{[\s\S]+?"success":\s*True[\s\S]+?\}\),\s*200',
        body,
    )
    assert success_resp, "Success-path jsonify not found"
    resp = success_resp.group(0)
    assert '"persisted"' in resp or "'persisted'" in resp, (
        "Success response doesn't expose `persisted`."
    )


def test_pyproject_version_at_least_0537():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 37), (
        f"Version {m.group(1)} < 0.5.37"
    )
