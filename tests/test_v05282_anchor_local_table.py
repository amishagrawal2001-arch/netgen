"""v0.5.282 — Three fixes for "switch can't ping anchor" root
causes surfaced by agent-traced code review:

- ARP-J1: VRF local-table entry check + explicit install
- ARP-J2: _add_route_and_vrf_copy skips default-table on VRF-slaved iface
- ARP-J3: startup sysctl sweep so existing deployments benefit
  without DHCP-device restart
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()
ARP_MON = (REPO / "utils" / "arp_monitor.py").read_text()


# --- ARP-J1: VRF local-table entry ------------------------------


def test_ensure_ipv4_address_probes_vrf_local_table():
    """After ip addr add succeeds, must probe the VRF's local
    table for `local <server_ip>` entry. Kernel doesn't reliably
    auto-install this on VRF-slaved interfaces on some kernel
    versions / timing races."""
    idx = DHCP.find("def _ensure_ipv4_address")
    end = DHCP.find("\n\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.282 (ARP-J1)" in body
    # Probes `ip route show table local vrf <name>`.
    assert '"ip", "route", "show", "table", "local"' in body
    assert '"vrf", _vrf_name2' in body


def test_ensure_ipv4_address_installs_local_when_missing():
    """When the probe returns no `local <ip>` line, install
    explicitly with `ip route add table local ...`."""
    idx = DHCP.find("v0.5.282 (ARP-J1)")
    body = DHCP[idx:idx + 3500]
    # The install command.
    assert '"ip", "route", "add", "table", "local"' in body
    assert '"local", f"{server_ip}/32"' in body
    assert '"proto", "kernel", "scope", "host"' in body
    assert '"vrf", _vrf_name2' in body


def test_ensure_ipv4_address_local_check_is_non_fatal():
    """Probe / install failures must not abort the anchor add —
    log at debug and continue."""
    idx = DHCP.find("v0.5.282 (ARP-J1)")
    body = DHCP[idx:idx + 3500]
    assert "except Exception" in body
    # Debug-level so rootless test envs don't spam warn.
    assert "logger.debug" in body


# --- ARP-J2: skip default-table write on VRF-slaved iface -------


def test_add_route_and_vrf_copy_skips_default_table_when_vrf_set():
    """`_add_route_and_vrf_copy` must not write to the default
    main table when the interface is VRF-slaved. Pre-fix wrote
    to BOTH unconditionally, cluttering the default table with
    routes for interfaces that live in a VRF."""
    idx = DHCP.find("def _add_route_and_vrf_copy")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.282 (ARP-J2)" in body
    # The main-table write is guarded behind `if not vrf_name:`.
    assert "if not vrf_name:" in body
    # And the guard precedes the `ip route replace` main-table call.
    guard_idx = body.find("if not vrf_name:\n        try:")
    main_write_idx = body.find('"ip", ip_flag, "route", "replace"', guard_idx)
    assert 0 < guard_idx < main_write_idx


def test_add_route_and_vrf_copy_still_writes_vrf_copy():
    """VRF copy path must still write to vrf_name's table
    (that's the whole point of the function)."""
    idx = DHCP.find("def _add_route_and_vrf_copy")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end]
    # VRF-scoped mirror at the bottom, `if not vrf_name: return`
    # is the pre-fix guard for the VRF copy.
    assert "vrf_cmd.extend([\"vrf\", vrf_name])" in body


# --- ARP-J3: startup sysctl sweep -------------------------------


def test_arp_monitor_sweeps_sysctls_at_start():
    """arp_monitor.start() must call the sysctl sweep so
    existing deployments benefit from the DHCP-K1 fix
    without needing to restart the DHCP-server device."""
    idx = ARP_MON.find("def start(self):")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "v0.5.282 (ARP-J3)" in body
    assert "self._sweep_arp_sysctls()" in body


def test_sysctl_sweep_sets_both_all_and_default_scopes():
    """`net.ipv4.conf.<iface>.<key>` semantics: kernel uses
    max(all, per-iface) for arp_ignore. Setting `all=0` alone
    doesn't force per-iface to 0 if per-iface is >0. Also set
    `default` scope (template for new interfaces) so newly-
    created subifs inherit the safe baseline."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    for key in (
        "net.ipv4.conf.all.arp_ignore",
        "net.ipv4.conf.all.arp_announce",
        "net.ipv4.conf.default.arp_ignore",
        "net.ipv4.conf.default.arp_announce",
    ):
        assert key in body, f"sysctl sweep missing {key}"


def test_sysctl_sweep_is_non_fatal():
    """sysctl -w can fail on rootless containers / locked
    sysctl / non-Linux dev hosts. Must log at debug and
    continue, not raise."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "except Exception" in body
    # Failure logged at debug.
    assert "logger.debug" in body
    # Success at info so operators see it in the log.
    assert "logger.info" in body


def test_sysctl_sweep_documents_kernel_semantics():
    """The comment must explain why we set BOTH all and default —
    a future author who thinks 'all should be enough' would
    otherwise strip the default-scope sysctls."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "max(all" in body


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 282)
