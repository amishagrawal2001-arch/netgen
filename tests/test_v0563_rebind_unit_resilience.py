"""v0.5.63 — netgen-dpdk-rebind.service ordering + hot-remove
resilience.

Audit findings M11 + M12.

M11: pre-fix unit had:
    After=systemd-modules-load.service
    Wants=systemd-modules-load.service
    Before=netgen-server.service network.target

`Wants=systemd-modules-load.service` is redundant —
systemd-modules-load is already `WantedBy=sysinit.target` and
runs on every boot. `Wants=` adds nothing.

Also missing: `ConditionPathExists=/etc/netgen/dpdk-interfaces.json`.
On hosts that never ran DPDK setup the unit logged "nothing to
do" every boot.

M12: pre-fix the helper script set `rc=1` on ANY single bind
failure. After a NIC hot-remove or BIOS PCI renumber the unit
went to "failed" state. systemctl `After=netgen-dpdk-
rebind.service` consumers blocked → wedged boot.

Fix:
  - Track per-entry success/failure/missing
  - SKIP entries missing from /sys/bus/pci (hot-remove)
  - Prune missing entries from the registry
  - Return 0 if at least one bind succeeded OR we pruned
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def _unit_content() -> str:
    src = _src()
    m = re.search(
        r'unit_content\s*=\s*"""(.+?)"""',
        src,
        re.DOTALL,
    )
    assert m, "unit_content not found in _ensure_dpdk_rebind_unit"
    return m.group(1)


def _script_content() -> str:
    src = _src()
    m = re.search(
        r'script_content\s*=\s*r?"""(.+?)"""',
        src,
        re.DOTALL,
    )
    assert m, "script_content not found"
    return m.group(1)


def test_unit_drops_wants_systemd_modules_load():
    """Wants=systemd-modules-load.service is redundant — the
    target unit is already WantedBy sysinit. Removing it
    declutters the dependency graph."""
    unit = _unit_content()
    assert "Wants=systemd-modules-load.service" not in unit, (
        "Unit still has redundant Wants=systemd-modules-load.service"
    )
    # After= stays — that's the ordering edge.
    assert "After=systemd-modules-load.service" in unit, (
        "Unit lost After=systemd-modules-load.service — broken "
        "ordering edge"
    )


def test_unit_has_condition_path_exists():
    """Unit must skip cleanly on hosts that never ran DPDK
    setup. ConditionPathExists is the systemd-idiomatic way."""
    unit = _unit_content()
    assert "ConditionPathExists=/etc/netgen/dpdk-interfaces.json" in unit, (
        "Unit missing ConditionPathExists — would log 'nothing "
        "to do' every boot on non-DPDK hosts"
    )


def test_script_skips_missing_pci_devices():
    """Helper must check /sys/bus/pci/devices/<bdf> before
    invoking dpdk-devbind. Hot-removed devices used to error;
    now they should skip cleanly."""
    script = _script_content()
    assert "/sys/bus/pci/devices/" in script, (
        "Helper doesn't check /sys/bus/pci/devices/ — "
        "hot-removed devices would still error every boot"
    )


def test_script_returns_0_on_partial_success():
    """If at least one bind succeeded OR we pruned a missing
    entry, the unit must return 0. Pre-fix any single failure
    set rc=1 → unit failed → downstream services blocked."""
    script = _script_content()
    # The new logic uses `succeeded`, `failed`, `missing` lists.
    assert "succeeded" in script, (
        "Helper doesn't track succeeded list"
    )
    assert "failed" in script, "Helper doesn't track failed list"
    assert "missing" in script, "Helper doesn't track missing list"
    # And the exit policy is "succeed if succeeded OR missing".
    assert re.search(
        r"if\s+succeeded\s+or\s+missing:[\s\S]{0,80}?return\s+0",
        script,
    ), (
        "Exit policy doesn't return 0 on partial success — "
        "downstream services would still block"
    )


def test_script_prunes_missing_entries_from_registry():
    """Helper must rewrite the registry without the missing
    entries so subsequent boots don't keep tripping over them."""
    script = _script_content()
    assert "REGISTRY" in script, "Registry path constant lost"
    # The prune path: filter out missing PCIs, write back.
    assert re.search(
        r"data\[[\"']binds[\"']\]\s*=\s*\[[\s\S]{0,200}?missing\]",
        script,
    ), (
        "Helper doesn't filter binds against missing list — "
        "registry stays polluted"
    )
    # Atomic write to the registry (tmp + os.replace).
    assert "os.replace" in script, (
        "Helper doesn't os.replace into registry — risk of "
        "corruption on crash mid-write"
    )


def test_pyproject_version_at_least_0563():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 63), (
        f"Version {m.group(1)} < 0.5.63"
    )
