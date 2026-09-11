"""v0.5.294 — Devices tab IPv4 column shows DHCP-client leased IP.

Operator screenshot 2026-09-11: device4 (DHCP-client on vlan30) got
lease 192.16.30.105/24 (verified via `ip addr show vlan30` +
dhcp_lease_ip in DB), but the client'''s Devices tab IPv4 column was
EMPTY. add_device sets IPv4 from the operator-declared static field
(empty for DHCP clients) and _apply_device_status_row never touched
the IPv4 cell — so DHCP clients showed IPv4="" forever.

Fix in widgets/devices_tab.py:
1. populate_device_table: on first load, when IPv4 is empty AND
   dhcp_mode=client, fall back to dhcp_lease_ip with " (leased)"
   suffix.
2. _apply_device_status_row: on every poll, refresh the IPv4 cell
   for DHCP-client devices so leases acquired after the row was
   added show up in the UI.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def _src() -> str:
    return (REPO / "widgets" / "devices_tab.py").read_text()


def test_v05294_marker_present():
    src = _src()
    assert "v0.5.294 (audit dhcp-lease-visibility)" in src


def test_populate_falls_back_to_dhcp_lease_ip():
    """populate_device_table extracts ipv4 from device_info["IPv4"].
    Post-fix, when that is empty AND dhcp_mode=client, it must fall
    back to device_info["dhcp_lease_ip"] with " (leased)" suffix."""
    src = _src()
    # Locate the fallback block inside populate_device_table.
    idx = src.find("def populate_device_table")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "dhcp_lease_ip" in body
    assert '"client"' in body
    assert '(leased)' in body


def test_populate_only_fires_when_ipv4_is_empty():
    """The fallback must NOT clobber operator-declared static IPv4.
    Guarded by `if not ipv4:` — only triggers when the static field
    is empty."""
    src = _src()
    # Anchor to populate_device_table (not the refresh block that
    # shares the marker prefix).
    idx = src.find("def populate_device_table")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "if not ipv4:" in body
    assert "v0.5.294" in body


def test_refresh_updates_ipv4_cell_on_poll():
    """_apply_device_status_row (poll-refresh path) must also read
    dhcp_lease_ip and update the IPv4 cell — otherwise a lease
    acquired after the row was added never shows up in the UI."""
    src = _src()
    idx = src.find("def _apply_device_status_row")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "v0.5.294" in body
    assert "dhcp_lease_ip" in body
    assert 'self.COL["IPv4"]' in body


def test_refresh_only_fires_for_dhcp_client_devices():
    """The poll-refresh path must gate on dhcp_mode=client — never
    touch IPv4 cells for non-DHCP devices (would clobber static
    config)."""
    src = _src()
    idx = src.find("v0.5.294 (audit dhcp-lease-visibility): refresh")
    body = src[idx:idx + 3000]
    assert '_dhcp_mode_now ==' in body
    assert '"client"' in body


def test_refresh_skips_when_static_ipv4_configured():
    """If the operator DID declare a static IPv4 on a DHCP device
    (unusual but possible), the poll-refresh must NOT overwrite the
    static text with a "(leased)" one."""
    src = _src()
    idx = src.find("v0.5.294 (audit dhcp-lease-visibility): refresh")
    body = src[idx:idx + 3000]
    assert '_static_configured' in body
    assert 'not _static_configured' in body


def test_refresh_clears_cell_when_lease_released():
    """When dhcp_lease_ip is cleared (lease released / DHCP stopped),
    the cell must go back to empty — otherwise stale lease text
    would persist forever."""
    src = _src()
    idx = src.find("v0.5.294 (audit dhcp-lease-visibility): refresh")
    body = src[idx:idx + 3000]
    assert '_existing.endswith(" (leased)")' in body
    assert '_ipv4_item.setText("")' in body


def test_refresh_is_best_effort():
    """The refresh block must be wrapped in try/except so any
    per-row failure doesn'''t break the rest of the poll's
    row-application work."""
    src = _src()
    idx = src.find("v0.5.294 (audit dhcp-lease-visibility): refresh")
    body = src[idx:idx + 3000]
    assert "try:" in body
    assert "except Exception as _lease_display_exc:" in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 294)
