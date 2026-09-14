"""v0.5.316 — DHCP-client lease fallback in `update_device_table`.

v0.5.294/302/313 planted the DHCP-client lease-fallback logic in
`populate_device_table` — but that's the WRONG call site for the
nav-blank symptom. On a server-tree interface selection change,
the rebuild goes through `update_device_table`, NOT
`populate_device_table`.

`update_device_table` bypassed the fallback entirely — it just did
`value = device.get(header, "")` for every column, so for a DHCP-
client row the operator-declared "IPv4" is empty and stays empty.
v0.5.314 synced the lease surface into `all_devices`, but there
was no reader for those synced fields at the nav path.

Fix: mirror the populate-time fallback into `update_device_table`
for the 4 affected columns — IPv4, IPv6, IPv4 Gateway, IPv6
Gateway. Static-config devices are untouched (falsy dhcp_mode
gate).
"""
from __future__ import annotations

from pathlib import Path

_DEVICES_TAB = Path(__file__).resolve().parents[1] / "widgets" / "devices_tab.py"


def _src() -> str:
    return _DEVICES_TAB.read_text()


def test_marker_present():
    assert "v0.5.316 (audit dhcp-lease-fallback-in-update-path)" in _src()


def test_fallback_helper_defined_inside_update_device_table():
    """The helper closure must be defined inside `update_device_table`
    (not at module scope) — that keeps it out of the class API and
    makes it obvious the fix is co-located with the bug."""
    src = _src()
    umt_idx = src.index("def update_device_table(")
    # Find next `def ` (function boundary)
    next_def = src.index("\n    def ", umt_idx + 10)
    body = src[umt_idx:next_def]
    assert "def _dhcp_client_fallback(device, header, base_value)" in body


def test_fallback_short_circuits_on_static_config():
    """Static-config devices (dhcp_mode != 'client') must skip the
    fallback and return the base value unchanged. Otherwise a
    static device with an empty IPv4 would get a spurious
    "(leased)" suffix from a stale dhcp_lease_ip."""
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    assert 'if _mode != "client":' in body
    assert "return base_value" in body


def test_fallback_covers_all_four_columns():
    """All 4 columns that populate_device_table already handles
    must be mirrored here. Otherwise nav still loses the value in
    the missed column."""
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    for hdr in ('"IPv4"', '"IPv6"', '"IPv4 Gateway"', '"IPv6 Gateway"'):
        assert f'if header == {hdr}:' in body, (
            f"header {hdr} not handled in fallback helper"
        )


def test_fallback_ipv4_reads_dhcp_lease_ip_then_ipv4_address():
    """v4 fallback order (matches populate_device_table:4585+):
    1. `dhcp_lease_ip` (freshly-parsed from lease)
    2. `ipv4_address` (CIDR-strip fallback, v0.5.297)"""
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    # dhcp_lease_ip must be tried BEFORE ipv4_address
    _lease_idx = body.index('device.get("dhcp_lease_ip")')
    _addr_idx = body.index('device.get("ipv4_address")')
    assert _lease_idx < _addr_idx


def test_fallback_ipv4_gateway_reads_lease_then_static():
    """v4 gateway fallback (matches populate:4649+): prefer
    dhcp_lease_gateway, then ipv4_gateway."""
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    idx4gw = body.index('if header == "IPv4 Gateway":')
    tail = body[idx4gw:idx4gw + 800]
    assert 'device.get("dhcp_lease_gateway")' in tail
    assert 'device.get("ipv4_gateway")' in tail


def test_fallback_ipv6_reads_dhcp_lease_ip6_then_ipv6_address():
    """v6 fallback (matches populate:4611+): dhcp_lease_ip6
    first, then ipv6_address (CIDR-split)."""
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    _lease_idx = body.index('device.get("dhcp_lease_ip6")')
    _addr_idx = body.index('device.get("ipv6_address")')
    assert _lease_idx < _addr_idx


def test_fallback_ipv6_gateway_reads_dhcp_lease_gateway6():
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    idx6gw = body.index('if header == "IPv6 Gateway":')
    tail = body[idx6gw:idx6gw + 800]
    assert 'device.get("dhcp_lease_gateway6")' in tail
    assert 'device.get("ipv6_gateway")' in tail


def test_fallback_only_fires_on_empty_base_value():
    """If the row already has a value (populate_device_table
    already stashed a lease-suffixed string, OR the operator
    entered a static IP), do NOT rewrite it. Otherwise the
    "(leased)" suffix could get double-appended."""
    src = _src()
    marker = "v0.5.316 (audit dhcp-lease-fallback-in-update-path)"
    idx = src.index(marker)
    body = src[idx:idx + 5000]
    # First check inside the helper: `if base_value: return base_value`
    assert "if base_value:" in body


def test_fallback_wired_into_column_iteration():
    """The fallback must be called from the value-building loop
    for exactly the 4 affected headers. A helper defined but
    not called is dead code."""
    src = _src()
    umt_idx = src.index("def update_device_table(")
    body = src[umt_idx:umt_idx + 15000]
    assert 'if header in ("IPv4", "IPv6", "IPv4 Gateway", "IPv6 Gateway"):' in body
    assert "value = _dhcp_client_fallback(device, header, value)" in body


def test_devices_tab_ast_parses():
    """v0.5.300 lesson — the whole file must still parse after
    a 100-line edit inside a nested try-block."""
    import ast
    ast.parse(_src())
