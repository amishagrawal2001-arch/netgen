"""v0.5.232 — dnsmasq launches inside the device's VRF.

Bug driver (operator on srv06 2026-08-30): "Attach DHCP Pool"
failed with `dnsmasq: failed to create listening socket for
192.168.30.1: Address not available` even though vlan10 had
192.168.30.1/24 assigned. Root cause: vlan10 is `master
vrf-6f4c03646a1`, so its addresses are globally VISIBLE via
`ip addr show` but bind() from the default VRF context returns
EADDRNOTAVAIL — the kernel searches only the default routing
table for the source IP.

Fix: wrap dnsmasq launch with `ip vrf exec <vrf>` when the
device has a resolved VRF. When there's no VRF (older non-VRF
devices), launch dnsmasq directly as before.

The v0.5.222 fix (assigning the pool's .1 to the interface) is
still needed and still works — this fix just makes dnsmasq
able to actually bind to that address once assigned.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def test_dnsmasq_launch_wrapped_in_ip_vrf_exec_when_vrf_present():
    src = _read("utils/dhcp.py")
    # New launch block: derive VRF, wrap with ip vrf exec.
    assert "_dnsmasq_vrf = _resolve_device_vrf(device_id)" in src
    assert 'cmd = ["ip", "vrf", "exec", _dnsmasq_vrf,' in src


def test_dnsmasq_launch_falls_back_to_direct_when_no_vrf():
    """When _resolve_device_vrf returns None (older devices without
    VRF isolation), launch dnsmasq directly — no ip vrf exec wrapper
    which would fail with 'vrf None does not exist'."""
    src = _read("utils/dhcp.py")
    # The direct-launch fallback branch must still exist.
    assert 'cmd = ["dnsmasq", f"--conf-file={conffile}"]' in src


def test_dnsmasq_launch_logs_vrf_context():
    src = _read("utils/dhcp.py")
    assert "Launching dnsmasq inside VRF" in src


def test_only_one_dnsmasq_launch_path():
    """Guard against a future refactor that adds a second dnsmasq
    launch site and forgets the VRF wrapper. The single launch
    should be inside the vrf-wrap block above."""
    src = _read("utils/dhcp.py")
    # Only the launch pattern should exist once (in the fallback).
    assert src.count('["dnsmasq", f"--conf-file={conffile}"]') == 1


def test_version_bumped():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 232)
