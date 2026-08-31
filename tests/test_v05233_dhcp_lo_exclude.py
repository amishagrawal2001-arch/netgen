"""v0.5.233 — exclude lo + disable DNS in dnsmasq config.

Follow-up to v0.5.232 which wrapped dnsmasq with `ip vrf exec`.
That fix let dnsmasq bind() the pool's VRF-scoped IP, but
uncovered a second failure: dnsmasq's DNS resolver was still
trying to bind lo's addresses, and lo carries netgen-assigned
loopback IPs from OTHER devices (192.255.10.3, 192.255.30.1,
etc.). Those addresses aren't in the device's VRF routing
table, so bind() returned EADDRNOTAVAIL again — same error
class as v0.5.232, different address.

Two config changes close it:
- `except-interface=lo` — dnsmasq skips lo entirely. We don't
  want DHCP served on lo anyway.
- `port=0` — disables the DNS resolver. netgen uses dnsmasq
  strictly for DHCP; no reason for a DNS port per device, and
  removing DNS eliminates the second bind() code path that
  was scanning lo addresses.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def test_dnsmasq_config_excludes_lo():
    src = _read("utils/dhcp.py")
    assert '"except-interface=lo",' in src


def test_dnsmasq_config_disables_dns_port():
    src = _read("utils/dhcp.py")
    assert '"port=0",' in src


def test_dnsmasq_config_still_has_bind_interfaces_and_authoritative():
    """The v0.5.233 additions must not accidentally displace the
    pre-existing bind-interfaces / dhcp-authoritative lines."""
    src = _read("utils/dhcp.py")
    assert '"bind-interfaces",' in src
    assert '"dhcp-authoritative",' in src


def test_config_line_ordering_lo_before_bind_interfaces():
    """`except-interface` must come BEFORE `bind-interfaces` in the
    config file — dnsmasq processes interface directives in order,
    and bind-interfaces snapshots the current list."""
    src = _read("utils/dhcp.py")
    _idx_except = src.find('"except-interface=lo",')
    _idx_bind = src.find('"bind-interfaces",')
    assert _idx_except != -1 and _idx_bind != -1
    assert _idx_except < _idx_bind


def test_version_bumped():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 233)
