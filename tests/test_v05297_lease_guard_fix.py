"""v0.5.297 — Fix v0.5.294 lease-display guard misfire.

Operator on srv06 2026-09-11 (v0.5.296 running): device4 (DHCP
client on vlan30) got 192.16.30.105 via DHCP successfully, but
IPv4 column STILL empty despite v0.5.294 shipping the fallback.

Root cause: server persists the leased IP into BOTH dhcp_lease_ip
AND ipv4_address DB columns (ipv4_address gets "192.16.30.105/24"
with CIDR). v0.5.294's refresh-path guard was
`if _lease_now and not _static_configured:` — where
`_static_configured` checked ipv4_address. Since ipv4_address was
populated, the guard misfired ("looks like static config") and
refused to update the cell.

For dhcp_mode=client, ipv4_address is NEVER operator-declared
static — the field only carries meaning for non-DHCP devices.
Fix: drop the _static_configured guard for dhcp_mode=client.
Always prefer dhcp_lease_ip.

Also extend populate_device_table's initial-load fallback to
strip /CIDR off ipv4_address when dhcp_lease_ip isn't present in
the cache (covers session-shape data that doesn't include lease
field).
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def _src() -> str:
    return (REPO / "widgets" / "devices_tab.py").read_text()


def test_v05297_marker_present():
    assert "v0.5.297 (audit dhcp-lease-display-guard)" in _src()


def test_refresh_no_longer_gates_on_static_configured():
    """The whole point of v0.5.297 — the _static_configured check
    is gone from the refresh path (was blocking legit updates)."""
    src = _src()
    idx = src.find("def _apply_device_status_row")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    # The refresh block for lease display MUST NOT contain the
    # v0.5.294 guard anymore.
    assert "not _static_configured" not in body


def test_refresh_still_only_fires_for_dhcp_client():
    """Regression: don't start writing lease-shaped text into
    non-DHCP devices' IPv4 cells."""
    src = _src()
    idx = src.find("def _apply_device_status_row")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "_dhcp_mode_now == \"client\"" in body


def test_refresh_still_shows_leased_suffix():
    src = _src()
    idx = src.find("def _apply_device_status_row")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "(leased)" in body


def test_refresh_still_clears_on_lease_release():
    """If dhcp_lease_ip goes empty (lease released), the cell
    must clear — but ONLY when the current text ends with
    '(leased)' to avoid stomping on any manually-typed text."""
    src = _src()
    idx = src.find("def _apply_device_status_row")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert '_existing.endswith(" (leased)")' in body


def test_populate_falls_back_to_ipv4_address_when_no_lease_field():
    """v0.5.297 extension of populate path: if dhcp_lease_ip isn't
    in the cache (session-shape data), also read ipv4_address and
    strip any /CIDR suffix."""
    src = _src()
    idx = src.find("def populate_device_table")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "v0.5.297" in body
    assert 'device_info.get("ipv4_address")' in body
    assert '.split("/", 1)[0]' in body


def test_populate_still_gates_on_dhcp_client():
    """Regression: only fires for dhcp_mode=client."""
    src = _src()
    idx = src.find("def populate_device_table")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert 'if _dhcp_mode == "client":' in body


def test_v05294_marker_still_present():
    """Regression: v0.5.294 marker survives the v0.5.297 edit."""
    assert "v0.5.294 (audit dhcp-lease-visibility)" in _src()


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 297)
