"""v0.5.69 — /api/admin/health surfaces every field the admin
dashboard would otherwise need 3 other endpoints to learn.

Audit finding H1. The admin console polls /api/admin/health
every 30 s (visibility-aware since v0.5.65). Over the last 20
releases we added several state fields to /api/dpdk/status that
never made it back to the consolidated health endpoint:

  - reboot_needed / reboot_reasons (v0.5.51)
  - hugepages_mounted / hugepages_mount_point (v0.5.39)
  - hugepages 1GB support (v0.5.59)
  - hugepages per-NUMA-node breakdown (v0.5.54)
  - upgrade_running tracking
  - rdma_install_running tracking
  - libdpdk version vs 23.11 target (v0.5.61 install-time only)

Operators looking at the admin chip can be misled in all of
these cases — chip says healthy while one of these subsystems
is actually wrong.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _health_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    )
    assert m
    return m.group(0)


def test_health_exposes_reboot_needed_and_reasons():
    body = _health_body()
    assert "reboot_needed" in body, (
        "/api/admin/health doesn't expose reboot_needed"
    )
    assert "reboot_reasons" in body, (
        "/api/admin/health doesn't expose reboot_reasons"
    )
    # Must read the marker file the install_dpdk.sh helper writes.
    assert "netgen-reboot-required" in body, (
        "/api/admin/health doesn't read the reboot-required marker"
    )


def test_health_exposes_hugepages_mounted_and_mount_point():
    body = _health_body()
    assert '"mounted"' in body, (
        "/api/admin/health hugepages dict doesn't include `mounted`"
    )
    assert '"mount_point"' in body, (
        "/api/admin/health hugepages dict doesn't include `mount_point`"
    )
    # Must read /proc/mounts (the v0.5.39 detection method).
    assert "/proc/mounts" in body, (
        "/api/admin/health doesn't read /proc/mounts to detect hugetlbfs"
    )


def test_health_exposes_hugepages_per_size_breakdown():
    body = _health_body()
    assert '"per_size"' in body, (
        "/api/admin/health doesn't expose per-page-size hugepages"
    )
    # Must read 1GB leaf too (v0.5.59).
    assert "hugepages-1048576kB" in body, (
        "/api/admin/health doesn't probe the 1GB sysfs leaf"
    )


def test_health_exposes_hugepages_per_node_breakdown():
    body = _health_body()
    assert '"per_node"' in body, (
        "/api/admin/health doesn't expose per-NUMA-node hugepages"
    )
    assert "/sys/devices/system/node" in body, (
        "/api/admin/health doesn't enumerate NUMA nodes"
    )


def test_health_exposes_rdma_install_and_upgrade_running():
    body = _health_body()
    assert "rdma_install_running" in body, (
        "/api/admin/health doesn't expose rdma_install_running"
    )
    assert "upgrade_running" in body, (
        "/api/admin/health doesn't expose upgrade_running"
    )


def test_health_exposes_dpdk_version_mismatch():
    body = _health_body()
    assert "version_mismatch" in body, (
        "/api/admin/health doesn't compute libdpdk version_mismatch"
    )
    assert '"23.11"' in body or "'23.11'" in body, (
        "/api/admin/health doesn't reference the 23.11 target version"
    )


def test_health_issues_list_includes_reboot_and_mismatch():
    """The issues list (which drives the degraded verdict) must
    include the new failure modes."""
    body = _health_body()
    # Find the issues list construction.
    m = re.search(r"issues\s*=\s*\[\][\s\S]+?out\[[\"']issues[\"']\]\s*=\s*issues", body)
    assert m
    issues_block = m.group(0)
    assert "host reboot required" in issues_block, (
        "issues list doesn't mark reboot_needed as a degraded state"
    )
    assert "hugepages allocated but hugetlbfs not mounted" in issues_block, (
        "issues list doesn't catch the v0.5.39 mount-evaporated trap"
    )
    assert "version_mismatch" in issues_block, (
        "issues list doesn't include the DPDK target-version mismatch"
    )


def test_pyproject_version_at_least_0569():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 69), (
        f"Version {m.group(1)} < 0.5.69"
    )
