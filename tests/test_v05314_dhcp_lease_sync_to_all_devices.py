"""v0.5.314 — DHCP lease fields sync into all_devices on poll refresh.

Operator report on srv06 device4 (DHCP client) after v0.5.313:
IPv4 column briefly showed "192.16.30.105 (leased)" — but when
the operator switched interfaces in the server tree and came
back, the IPv4 cell went BLANK and the IPv4 Gateway didn't
populate at all. Waiting for the next 30 s poll brought it
back.

Root cause: `_apply_device_status_row` (widgets/devices_tab.py)
fetches the fresh DB row via /api/device/database/devices/<id>
and paints the cells directly (v0.5.294/v0.5.302/v0.5.313 setText
paths). But the underlying `all_devices` in-memory entry was
never updated with the runtime DHCP-lease surface — only its
"Status" field was propagated (`info["Status"] = device_status`).

When the operator switched interfaces, the table got wiped and
`populate_device_table` re-added rows from the STALE
`all_devices` entry. The v0.5.294/v0.5.302/v0.5.313 populate-
fallback blocks read `device_info.get("dhcp_lease_ip")` etc.,
saw None/empty, and the IPv4 cell stayed blank until the next
poll re-fetched the DB and re-painted.

Fix: in `_apply_device_status_row`, after syncing "Status",
also copy each runtime DHCP-lease field from `device_data`
(the fresh DB row) into `info` (the `all_devices` entry).
Populate now sees the current lease on re-add.

Fields synced:
  * dhcp_mode, dhcp_state (state gates the fallback branch)
  * dhcp_lease_ip / _mask / _gateway / _server / _subnet
  * dhcp_lease_ip6 / _prefix6 / _gateway6
  * ipv4_address / ipv6_address / ipv4_gateway / ipv6_gateway
    (some populate paths fall back to these too)
"""
from __future__ import annotations

from pathlib import Path

_DEVICES_TAB = Path(__file__).resolve().parents[1] / "widgets" / "devices_tab.py"


def _src() -> str:
    return _DEVICES_TAB.read_text()


def test_marker_present():
    assert "v0.5.314 (audit dhcp-lease-lost-on-nav)" in _src()


def test_sync_fires_alongside_status_propagation():
    """The sync loop must sit in the SAME try-block that already
    propagates `info["Status"] = device_status`. Otherwise a
    partial navigation → repopulate → poll-refresh cycle races
    with the sync."""
    src = _src()
    idx = src.index("v0.5.314 (audit dhcp-lease-lost-on-nav)")
    # Find the enclosing block by looking back for the Status sync.
    before = src[:idx]
    status_sync_idx = before.rindex('info["Status"] = device_status')
    # The v0.5.314 sync must live BELOW the Status sync, inside
    # the same try block (no intervening except: or def).
    between = src[status_sync_idx:idx]
    assert "except Exception" not in between, (
        "v0.5.314 sync must sit inside the SAME try block as the "
        "Status propagation — not in a separate try/except"
    )
    assert "def " not in between, (
        "v0.5.314 sync must not have moved into a different method"
    )


def test_sync_covers_v4_lease_fields():
    """All four v4 lease fields the populate-fallback and poll-
    refresh read must be synced."""
    src = _src()
    idx = src.index("v0.5.314 (audit dhcp-lease-lost-on-nav)")
    body = src[idx:idx + 3000]
    for field in (
        '"dhcp_lease_ip"',
        '"dhcp_lease_mask"',
        '"dhcp_lease_gateway"',
    ):
        assert field in body, f"v4 lease field {field} not in sync list"


def test_sync_covers_v6_lease_fields():
    """v6 lease surface (v0.5.302) must sync too — same navigation
    bug affects the IPv6 column display."""
    src = _src()
    idx = src.index("v0.5.314 (audit dhcp-lease-lost-on-nav)")
    body = src[idx:idx + 3000]
    for field in (
        '"dhcp_lease_ip6"',
        '"dhcp_lease_prefix6"',
        '"dhcp_lease_gateway6"',
    ):
        assert field in body, f"v6 lease field {field} not in sync list"


def test_sync_covers_dhcp_mode_gate():
    """dhcp_mode gates the "if _dhcp_mode == 'client'" fallback
    branch in populate_device_table (line ~4425). Without it
    synced, the fallback might skip even when the lease fields
    are present."""
    src = _src()
    idx = src.index("v0.5.314 (audit dhcp-lease-lost-on-nav)")
    body = src[idx:idx + 3000]
    assert '"dhcp_mode"' in body


def test_sync_covers_static_address_fields():
    """ipv4_address / ipv6_address are used as LATE fallbacks in
    the populate-fallback (v0.5.297 CIDR-strip path). Sync them
    too so navigation doesn't lose the fallback data either."""
    src = _src()
    idx = src.index("v0.5.314 (audit dhcp-lease-lost-on-nav)")
    body = src[idx:idx + 3000]
    assert '"ipv4_address"' in body
    assert '"ipv6_address"' in body


def test_sync_only_copies_fields_present_in_device_data():
    """Guard `if _sync_field in device_data:` so we don't
    OVERWRITE existing `info[field]` with None when the DB row
    happened to omit that field (partial fetch, monitor-driven
    partial update)."""
    src = _src()
    idx = src.index("v0.5.314 (audit dhcp-lease-lost-on-nav)")
    body = src[idx:idx + 3000]
    assert "if _sync_field in device_data:" in body


def test_devices_tab_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
