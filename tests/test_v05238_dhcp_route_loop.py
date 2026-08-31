"""v0.5.238 — DHCP route installer strips `via <gw>` when gw is a local IP.

Operator on srv06 2026-08-31: pings from DHCP clients arrived at the
server but no reply left. `ip vrf exec <vrf> ping -c 2 <client>`
from the server also failed. Root cause: `_add_route_and_vrf_copy`
installed pool-network routes as `ip route add <net> via
<pool_gateway> dev <iface>` — and the pool's gateway was the
SERVER'S own interface IP (172.16.30.1). Kernel then ARP'd for
its own IP to reach 172.16.30.x → self-MAC → self-loop, packets
never left the box.

Fix: probe the interface's assigned IPs and, if the requested
gateway matches one of them, drop `via <gw>` from the ip route
command. The connected /24 route already covers the pool subnet;
no next-hop is needed for the fragment routes.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()


def test_add_route_helper_probes_interface_addresses():
    """The fix uses ip -4 -o addr show <iface> to enumerate the
    interface's own IPs and check if the gateway matches."""
    idx = DHCP.find("def _add_route_and_vrf_copy(")
    body = DHCP[idx:idx + 5000]
    assert "ip -4 -o addr show dev" in body
    assert "_own_ips = set()" in body


def test_add_route_helper_drops_via_when_gateway_is_local():
    idx = DHCP.find("def _add_route_and_vrf_copy(")
    body = DHCP[idx:idx + 5000]
    assert "if gateway in _own_ips:" in body
    assert "_effective_gateway = \"\"" in body


def test_main_and_vrf_branches_both_use_effective_gateway():
    """Both the main-table `ip route replace` and the VRF-mirror
    branch must honor _effective_gateway, otherwise the VRF
    routing table gets the bogus `via <own-ip>` route even when
    the main table doesn't."""
    idx = DHCP.find("def _add_route_and_vrf_copy(")
    body = DHCP[idx:idx + 6000]
    # Two branches should both check _effective_gateway.
    assert body.count("if _effective_gateway:") >= 2


def test_no_leftover_bare_gateway_extends_in_route_helper():
    """After the fix, neither branch may extend cmd with `via` +
    the raw `gateway` variable — must use `_effective_gateway`."""
    idx = DHCP.find("def _add_route_and_vrf_copy(")
    body = DHCP[idx:idx + 6000]
    # No `cmd.extend(["via", gateway])` (raw gateway) — must be
    # `_effective_gateway`.
    assert 'cmd.extend(["via", gateway])' not in body
    assert 'vrf_cmd.extend(["via", gateway])' not in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 238)
