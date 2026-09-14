"""v0.5.313 — two DHCP client display fixes surfaced by srv06 device4:

  1. IPv4 Gateway column blank for DHCP-client rows even after the
     lease landed. v0.5.294 populated `COL[IPv4]` with the leased
     address but never touched `COL[IPv4 Gateway]`. The v0.5.302
     IPv6 branch DOES populate both `COL[IPv6]` and
     `COL[IPv6 Gateway]` — v4 parity was missed.

  2. Old lease IP persists in the row after operator edits a
     DHCP-client device to change its interface. The DB row's
     dhcp_lease_ip / _mask / _gateway describe a lease that no
     longer exists on the new interface. The poll refresh reads
     them and re-displays the stale value until (or unless) the
     new lease lands.

Fix (widgets/devices_tab.py):
  * Add IPv4 Gateway population from `dhcp_lease_gateway` in both
    the poll-refresh path (mirrors the v0.5.302 IPv6 branch)
    AND the populate_device_table initial-add fallback.
  * QSignalBlocker discipline (v0.5.301 lesson) so setText on
    the gateway cell can't re-trigger `on_cell_changed` as an
    inline-edit-loop.

Fix (utils/dhcp.py: start_dhcp_client):
  * BEFORE spawning dhclient/dhcp6c, clear all dhcp_lease_*
    fields in the DB (v4 + v6). Next poll shows blank; the new
    lease repopulates once it lands. Honest state — old lease
    is gone on the new interface.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─────────────────────────── Fix 1: IPv4 Gateway column


def test_marker_1_present_in_devices_tab():
    assert "v0.5.313 (audit dhcp-lease-gateway-display-gap)" in _read("widgets/devices_tab.py")


def test_poll_refresh_populates_ipv4_gateway_from_dhcp_lease():
    """Poll refresh reads dhcp_lease_gateway and writes
    "<gw> (leased)" into COL[IPv4 Gateway] — mirrors the v0.5.302
    IPv6 branch which was the missed pattern for v4."""
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.313 (audit dhcp-lease-gateway-display-gap)")
    body = src[idx:idx + 3000]
    assert '_gw_now = str(device_data.get("dhcp_lease_gateway") or "").strip()' in body
    assert 'self.COL.get("IPv4 Gateway")' in body
    assert 'f"{_gw_now} (leased)"' in body


def test_poll_refresh_ipv4_gateway_uses_signal_blocker():
    """v0.5.301 lesson applies to the gateway cell too — automated
    setText must NOT trigger the inline-edit-loop."""
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.313 (audit dhcp-lease-gateway-display-gap)")
    body = src[idx:idx + 3000]
    assert "with QSignalBlocker(self.devices_table):" in body


def test_poll_refresh_ipv4_gateway_clears_on_lease_release():
    """When dhcp_lease_gateway is cleared (lease released,
    interface changed), the gateway cell must go back to empty —
    otherwise stale " (leased)" text sticks forever."""
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.313 (audit dhcp-lease-gateway-display-gap)")
    body = src[idx:idx + 3000]
    assert '_existing_gw.endswith(" (leased)")' in body
    assert '_gw_item.setText("")' in body


def test_populate_device_table_ipv4_gateway_fallback_added():
    """Initial add path (populate_device_table) mirrors the
    v0.5.302 IPv6 gateway fallback — dhcp-client device with
    empty operator gateway shows the leased gateway with
    "(leased)" suffix."""
    src = _read("widgets/devices_tab.py")
    # There are TWO v0.5.313 markers now — one for poll-refresh,
    # one for populate_device_table.
    assert src.count("v0.5.313 (audit dhcp-lease-gateway-display-gap)") >= 2
    # Populate branch reads dhcp_lease_gateway.
    assert 'device_info.get("dhcp_lease_gateway")' in src


# ─────────────────────────── Fix 2: Stale lease cleared on reapply


def test_marker_2_present_in_dhcp():
    assert "v0.5.313 (audit dhcp-lease-stale-on-reapply)" in _read("utils/dhcp.py")


def test_stale_lease_cleared_before_dhclient_spawn():
    """The clear must run BEFORE the dhclient/dhcp6c invocation.
    If it runs after, there's a window where the new dhclient
    might not have written the fresh lease yet but the old one
    is still in the DB — no improvement over pre-fix."""
    src = _read("utils/dhcp.py")
    clear_idx = src.index("v0.5.313 (audit dhcp-lease-stale-on-reapply)")
    # Find the first dhclient IPv4 spawn AFTER the clear marker.
    dhclient_idx = src.index('"dhclient", "-4", "-r", "-pf"', clear_idx)
    assert clear_idx < dhclient_idx, (
        "stale-lease clear must sit BEFORE the dhclient spawn"
    )


def test_stale_lease_clear_covers_all_v4_and_v6_fields():
    """All 8 runtime lease fields must be cleared — otherwise a
    partial clear leaves the row in a weird half-stale state."""
    src = _read("utils/dhcp.py")
    idx = src.index("v0.5.313 (audit dhcp-lease-stale-on-reapply)")
    body = src[idx:idx + 3000]
    for field in (
        '"dhcp_lease_ip": ""',
        '"dhcp_lease_mask": ""',
        '"dhcp_lease_gateway": ""',
        '"dhcp_lease_server": ""',
        '"dhcp_lease_expires": None',
        '"dhcp_lease_subnet": ""',
        '"dhcp_lease_ip6": ""',
        '"dhcp_lease_prefix6": ""',
        '"dhcp_lease_gateway6": ""',
    ):
        assert field in body, f"stale-lease clear missing {field!r}"


def test_stale_lease_clear_guarded_on_device_id():
    """The clear must not fire when device_id is empty — nothing
    to update."""
    src = _read("utils/dhcp.py")
    idx = src.index("v0.5.313 (audit dhcp-lease-stale-on-reapply)")
    body = src[idx:idx + 3000]
    assert "if device_id:" in body


# ─────────────────────────── AST parse


def test_edited_files_ast_parse():
    """v0.5.300 lesson."""
    import ast
    for rel in ("widgets/devices_tab.py", "utils/dhcp.py"):
        ast.parse(_read(rel))
