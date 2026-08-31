"""v0.5.235 — DHCP server audit bundle #3: 2 blockers + 1 user-visible.

Post-close-out audit surfaced 9 findings. This ship closes the 3
highest-impact ones (both blockers plus the observable interface-
IP-accumulation bug).

- B1 (utils/dhcp.py:1892 pre-fix) — _ensure_ipv4_address only
  anchored the primary pool; additional_pools on different subnets
  never got an interface IP → dnsmasq refused those ranges.
- B2 (run_tgen_server.py:7404 pre-fix) — attach_pools with
  replace_existing=True wiped the whole dhcp_cfg dict including
  every ipv6_* field, silently disabling IPv6 DHCP on dual-stack.
- U1 (utils/dhcp.py:2544 pre-fix) — stop_dhcp_server removed the
  IPv6 anchor but had no IPv4 equivalent. Rotate 10 pools ->
  interface accumulates 10 stale /24 addresses.

Remaining 6 audit findings (U2/U3/U4 + P1/P2/P3) queued for
v0.5.236.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


DHCP = _read("utils/dhcp.py")
SERVER = _read("run_tgen_server.py")


# --- B1: additional pools get anchored ------------------------------------

def test_b1_additional_pools_iterated_for_anchor():
    """Iterate additional_pools and call _ensure_ipv4_address for each,
    skipping subnets already anchored by the primary."""
    assert "for _add_pool in additional_pools:" in DHCP
    assert "_anchored_subnets = set()" in DHCP
    # Actually calls the anchor helper for the extra pool
    idx = DHCP.find("for _add_pool in additional_pools:")
    body = DHCP[idx:idx + 2000]
    assert "_ensure_ipv4_address(" in body
    assert "_add_start, _add_end" in body


def test_b1_dedupes_primary_subnet():
    """A primary pool + additional pool in the same /24 shouldn't
    double-anchor .1 (would fail with EEXIST or produce a warning)."""
    idx = DHCP.find("for _add_pool in additional_pools:")
    body = DHCP[idx:idx + 2500]
    assert "_add_key in _anchored_subnets" in body
    assert "continue" in body


# --- B2: attach preserves IPv6 config ------------------------------------

def test_b2_attach_preserves_ipv6_keys():
    """The `else: dhcp_cfg = {}` branch is gone — replaced with a
    key-preserving dict comprehension that keeps ipv6_* fields."""
    idx = SERVER.find("if not replace_existing and existing_config:")
    body = SERVER[idx:idx + 2000]
    assert "_preserve_keys = (" in body
    for k in ("ipv6_pool_start", "ipv6_pool_end", "ipv6_prefix",
              "ipv6_gateway", "ipv6_server_ip", "ipv6_enabled",
              "ipv4_enabled"):
        assert f'"{k}"' in body


def test_b2_no_bare_dhcp_cfg_reset_to_empty():
    """Guard against a future refactor that reintroduces
    `dhcp_cfg = {}` at this site — that's exactly the wipe."""
    idx = SERVER.find("if not replace_existing and existing_config:")
    body = SERVER[idx:idx + 600]
    # No plain `dhcp_cfg = {}` between `else:` and the preserve dict
    # comprehension.
    assert "dhcp_cfg = {}\n" not in body


# --- U1: stop_dhcp_server removes IPv4 anchors ---------------------------

def test_u1_remove_ipv4_address_helper_exists():
    assert "def _remove_ipv4_address(interface: str, address: str, prefix: str, container=None)" in DHCP
    # Uses `ip -4 addr del`
    assert '"ip", "-4", "addr", "del"' in DHCP


def test_u1_stop_iterates_stored_pool_networks_for_cleanup():
    """The stop path derives each anchor from stored pool_networks
    (which the start path saves) and calls _remove_ipv4_address."""
    idx = DHCP.find("if ipv6_server_ip and ipv6_prefix:")
    body = DHCP[idx:idx + 3000]
    assert "_stored_nets = dhcp_cfg.get(\"pool_networks\")" in body
    assert "_remove_ipv4_address(" in body


def test_u1_skips_tiny_pools_that_have_no_anchor():
    """/31 and /32 pools never got an anchor (start path guarded
    via v0.5.230 P server-9 fix) — stop must skip them too or
    it'll try to remove an address that was never added."""
    idx = DHCP.find("_stored_nets = dhcp_cfg.get(\"pool_networks\")")
    body = DHCP[idx:idx + 2000]
    assert "_hosts = list(_net.hosts())" in body
    assert "if not _hosts:" in body


# --- Version bump --------------------------------------------------------

def test_version_bumped():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 235)
