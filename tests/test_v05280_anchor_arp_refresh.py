"""v0.5.280 — DHCP anchor reachability:
- DHCP-K1: set arp_ignore=0 + arp_announce=0 on anchor interface
- DHCP-K2: periodic gratuitous-ARP registry keeps switch MAC
  tables warm so anchor stays pingable during quiet windows
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()


# --- DHCP-K1: sysctl setup on anchor interface -------------------


def test_ensure_ipv4_address_sets_arp_ignore_zero():
    """The anchor interface must have arp_ignore=0 so ARP replies
    for SECONDARY IPs (the pool anchor) are actually sent."""
    idx = DHCP.find("def _ensure_ipv4_address")
    end = DHCP.find("\n\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.280 (DHCP-K1)" in body
    assert '("arp_ignore", "0")' in body
    assert '("arp_announce", "0")' in body
    # Uses `sysctl -w net.ipv4.conf.<iface>.<key>=<val>`.
    assert '"sysctl", "-w"' in body
    assert 'net.ipv4.conf.{interface}' in body


def test_sysctl_failures_are_non_fatal():
    """Failing to set sysctl (rootless container, sysctl locked)
    must not abort the anchor add — log at debug, continue."""
    idx = DHCP.find("v0.5.280 (DHCP-K1)")
    body = DHCP[idx:idx + 1500]
    assert "except Exception" in body
    # Failure logged at DEBUG level (rootless test envs shouldn't
    # spam WARN for this).
    assert "logger.debug" in body


# --- DHCP-K2: periodic gratuitous-ARP registry -------------------


def test_registry_infrastructure_defined():
    for name in (
        "_ANCHOR_ARP_REFRESH",
        "_ANCHOR_ARP_REFRESH_LOCK",
        "_ANCHOR_ARP_THREAD",
        "ANCHOR_ARP_REFRESH_INTERVAL",
    ):
        assert name in DHCP, f"{name} not defined"


def test_register_and_unregister_helpers_defined():
    assert "def _register_anchor_arp_refresh" in DHCP
    assert "def _unregister_anchor_arp_refresh" in DHCP
    assert "def _ensure_anchor_arp_thread" in DHCP
    assert "def _anchor_arp_refresh_loop" in DHCP


def test_ensure_ipv4_address_registers_anchor_on_success():
    """After the anchor IP is confirmed on the interface (either
    fresh-add OR already-assigned), the registry must be updated
    so the periodic thread refreshes ARP for it."""
    idx = DHCP.find("def _ensure_ipv4_address")
    end = DHCP.find("\n\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.280 (DHCP-K2)" in body
    assert "_register_anchor_arp_refresh(interface, server_ip)" in body


def test_stop_dhcp_server_unregisters_anchor():
    """stop_dhcp_server must remove the anchor from the periodic
    registry so the thread doesn't keep re-arping an address that
    no longer belongs to any active session."""
    idx = DHCP.find("def stop_dhcp_server")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.280 (DHCP-K2)" in body
    assert "_unregister_anchor_arp_refresh(interface)" in body


def test_register_is_idempotent_by_iface_ip_key():
    """Registering the same (interface, ip) twice must replace,
    not duplicate. Key format: `<interface>#<ip>`."""
    from utils import dhcp as mod
    mod._ANCHOR_ARP_REFRESH.clear()
    mod._register_anchor_arp_refresh("vlan10", "172.16.30.2")
    mod._register_anchor_arp_refresh("vlan10", "172.16.30.2")
    assert list(mod._ANCHOR_ARP_REFRESH.keys()) == ["vlan10#172.16.30.2"]
    assert mod._ANCHOR_ARP_REFRESH["vlan10#172.16.30.2"] == (
        "vlan10", "172.16.30.2",
    )


def test_unregister_by_iface_removes_all_entries_for_that_iface():
    from utils import dhcp as mod
    mod._ANCHOR_ARP_REFRESH.clear()
    mod._register_anchor_arp_refresh("vlan10", "172.16.30.2")
    mod._register_anchor_arp_refresh("vlan10", "172.16.30.3")
    mod._register_anchor_arp_refresh("vlan20", "10.0.0.5")
    removed = mod._unregister_anchor_arp_refresh("vlan10")
    assert removed == 2
    assert list(mod._ANCHOR_ARP_REFRESH.keys()) == ["vlan20#10.0.0.5"]


def test_unregister_by_iface_and_ip_removes_only_that_entry():
    from utils import dhcp as mod
    mod._ANCHOR_ARP_REFRESH.clear()
    mod._register_anchor_arp_refresh("vlan10", "172.16.30.2")
    mod._register_anchor_arp_refresh("vlan10", "172.16.30.3")
    removed = mod._unregister_anchor_arp_refresh(
        "vlan10", "172.16.30.2",
    )
    assert removed == 1
    assert list(mod._ANCHOR_ARP_REFRESH.keys()) == ["vlan10#172.16.30.3"]


def test_register_blank_values_no_op():
    """Blank interface or IP silently drops the register call —
    caller doesn't need to guard."""
    from utils import dhcp as mod
    mod._ANCHOR_ARP_REFRESH.clear()
    mod._register_anchor_arp_refresh("", "1.2.3.4")
    mod._register_anchor_arp_refresh("vlan10", "")
    assert mod._ANCHOR_ARP_REFRESH == {}


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 280)
