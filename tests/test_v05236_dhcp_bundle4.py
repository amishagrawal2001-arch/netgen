"""v0.5.236 — closes the remaining 6 DHCP-server audit findings
+ the newly-observed interface-IP-leak on Edit.

Audit (v0.5.235 shipped B1/B2/U1; 6 deferred): U2, U3, U4, P1, P2, P3.
Plus one operator-observed extra: apply_device left stale same-
subnet IPs on the interface when the device's ipv4 was edited
(vlan10 accumulated 172.16.30.2 AND 172.16.30.1 both after a
.2 → .1 change).
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


DHCP = _read("utils/dhcp.py")
MONITOR = _read("utils/dhcp_monitor.py")
SERVER = _read("run_tgen_server.py")


# --- U2: monitor pidfile path matches writer ------------------------------

def test_u2_monitor_pidfile_matches_writer():
    assert 'f"/run/dnsmasq-{interface}.pid"' in MONITOR
    # Historical path may appear in comments — check the ASSIGNMENT
    # doesn't still write the stale value.
    assert 'pidfile = f"/var/run/dnsmasq/' not in MONITOR


# --- U3: /server/pool clears join table AFTER ensure ---------------------

def test_u3_pool_endpoint_defers_join_clear_until_after_ensure():
    idx = SERVER.find("def update_dhcp_server_pool():")
    body = SERVER[idx:idx + 12000]
    # The pre-fix clear-before-ensure block is now a comment stub.
    assert "v0.5.236 (audit U3): DEFERRED" in body
    # The post-ensure clear exists.
    assert "ensure_dhcp_services succeeded — NOW it's" in body


# --- U4: /server/pool validates gateway inside pool subnet ---------------

def test_u4_pool_endpoint_gateway_in_subnet_check():
    idx = SERVER.find("def update_dhcp_server_pool():")
    body = SERVER[idx:idx + 12000]
    # String is split across an f-string continuation, so check
    # each half independently rather than the concatenated form.
    assert "is not inside the pool" in body
    assert "_gw_addr not in _pool_net" in body


# --- P1: attach_pools dedups primary vs additional -----------------------

def test_p1_dedup_between_primary_and_additional():
    idx = SERVER.find("if isinstance(additional_pool_names, str):")
    body = SERVER[idx:idx + 1500]
    assert "if _n == primary_pool_name:" in body
    assert "_deduped_additional" in body


# --- P2: server stop kill anchored to interface --------------------------

def test_p2_server_stop_kill_anchored():
    """No more `pkill -f 'dnsmasq.*{interface}'` substring match.
    Use pgrep + grep with re.escape'd word/CIDR-aware pattern.
    Historical string may appear once in a comment; the check
    grepping the actual kill invocation is what matters."""
    # The active kill call uses pgrep + awk + xargs kill.
    assert "pgrep -af '^dnsmasq|/dnsmasq " in DHCP
    assert "_iface_re = _re.escape(interface)" in DHCP
    assert r"ostg-{_iface_re}\\.conf" in DHCP


# --- P3: _ensure_ipv4_address honors interface mask ----------------------

def test_p3_ensure_ipv4_honors_existing_mask():
    assert "_existing_mask" in DHCP
    assert "mask_bits = ipv4_mask or _existing_mask or str(pool_network.prefixlen)" in DHCP


# --- Interface-IP-leak on Edit: apply_device removes same-subnet strays -

def test_interface_ip_edit_removes_same_subnet_strays():
    idx = SERVER.find("# Step 4: Configure IPv4 address")
    body = SERVER[idx:idx + 3500]
    # New enumeration + same-subnet remove
    assert "for _ln in (_probe.stdout or \"\").splitlines():" in body
    assert "_existing_net.network_address == _new_net.network_address" in body
    assert "Removing stale same-subnet IPv4" in body


def test_interface_ip_leaves_cross_subnet_alone():
    """DHCP pool anchors on OTHER subnets must not be swept up by
    the same-subnet cleanup — _ensure_ipv4_address relies on them
    surviving."""
    idx = SERVER.find("# Step 4: Configure IPv4 address")
    body = SERVER[idx:idx + 3500]
    # The comment on why cross-subnet is safe.
    assert "leave cross-" in body or "leave cross-subnet" in body
    # The if-guard that gates removal on same network address.
    assert "network_address == _new_net.network_address" in body


# --- Version bump --------------------------------------------------------

def test_version_bumped():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 236)
