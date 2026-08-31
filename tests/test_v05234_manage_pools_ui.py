"""v0.5.234 — Manage DHCP Pools window: hide device-defaults + show IPv6.

Operator on srv06 2026-08-31: Manage DHCP Pools showed two rows —
"p1" (a legit named pool) and "device3" (an auto-generated per-
device snapshot with pool_start=192.168.30.10 but gateway=
172.16.30.1, i.e. inconsistent because it merged an old attach's
pool with the v0.5.225 template's gateway). The auto-generated
entry polluted the shared catalog and made it look like the
manager was serving corrupt data.

Also: v0.5.231 added IPv6 pool fields to DHCPPoolDialog but the
list view still showed only IPv4 columns — operators who defined
IPv6 pools couldn't SEE them from Manage DHCP Pools.

Fixes:
- populate_table skips entries with __source == "device-default"
- Table gains three IPv6 columns (Pool Start / End / Prefix)
- Column count 9 → 12
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


DHCP_TAB = _read("utils/devices_tab_dhcp.py")


def test_manage_pools_table_has_12_columns():
    assert "self.table = QTableWidget(0, 12)" in DHCP_TAB


def test_manage_pools_headers_include_ipv6_labels():
    assert '"IPv6 Pool Start"' in DHCP_TAB
    assert '"IPv6 Pool End"' in DHCP_TAB
    assert '"IPv6 Prefix"' in DHCP_TAB


def test_populate_table_skips_device_defaults():
    """The whole point of the fix — auto-generated per-device
    snapshots don't belong in the shared-catalog list."""
    idx = DHCP_TAB.find("def populate_table(self):")
    body = DHCP_TAB[idx:idx + 2000]
    assert 'if pool.get("__source") == "device-default":' in body
    assert "continue" in body


def test_populate_table_renders_ipv6_pool_fields():
    idx = DHCP_TAB.find("def populate_table(self):")
    body = DHCP_TAB[idx:idx + 2500]
    # Reads new key names, falls back to old alt-key names.
    assert 'pool.get("pool6_start", "") or pool.get("ipv6_pool_start", "")' in body
    assert 'pool.get("pool6_end", "") or pool.get("ipv6_pool_end", "")' in body
    assert 'pool.get("prefix6") or pool.get("ipv6_prefix")' in body


def test_column_order_ipv6_after_gateway_before_routes():
    """Column order matters for header/data alignment. Verify the
    IPv6 columns land between Gateway (col 3) and Gateway Routes
    (col 7 now, was 4)."""
    idx = DHCP_TAB.find('"Name",\n                "Pool Start"')
    header_block = DHCP_TAB[idx:idx + 500]
    # Sequence check.
    for expected_after in [
        ('"Gateway"', '"IPv6 Pool Start"'),
        ('"IPv6 Pool Start"', '"IPv6 Pool End"'),
        ('"IPv6 Pool End"', '"IPv6 Prefix"'),
        ('"IPv6 Prefix"', '"Gateway Routes"'),
    ]:
        i1 = header_block.find(expected_after[0])
        i2 = header_block.find(expected_after[1])
        assert i1 < i2, f"{expected_after[0]} must come before {expected_after[1]}"


def test_version_bumped():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 234)
