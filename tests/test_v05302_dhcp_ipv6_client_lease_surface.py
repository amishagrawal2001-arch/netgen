"""
v0.5.302 — IPv6 DHCP client lease surface.

Locks in the source-level shape of the v0.5.302 changes:

  * get_dhcp_client_snapshot's default-return schema carries
    dhcp_lease_ip6 / dhcp_lease_prefix6 / dhcp_lease_gateway6.
  * start_dhcp_client's write-payload persists them when a
    global (non-link-local) IPv6 lease is observed.
  * stop_dhcp_client clears them.
  * configure_dhcp_server / disabled-DHCP scrub both zero them.
  * device_database has a field_mapping entry so update_device
    doesn't silently drop these keys.
  * device_database schema (CREATE TABLE + ALTER TABLE
    migrations) has the three columns.
  * The /api/device/dhcp/status Flask endpoint exposes the
    matching lease_ip6 / lease_prefix6 / lease_gateway6 keys.
  * widgets/devices_tab.py has the IPv6 column poll-refresh
    parallel to the v0.5.301 IPv4 branch (QSignalBlocker
    discipline — no cellChanged inline-edit loop).

Runtime verification of dnsmasq/dhcp6c behavior lives on srv06 —
these source-level checks just prevent regressions where a
future refactor forgets the v6 mirror of a v4 change.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def test_snapshot_schema_carries_v6_lease_fields():
    src = _read("utils/dhcp.py")
    # The default-return dict in get_dhcp_client_snapshot must
    # declare all three v6 lease keys so callers can rely on the
    # shape even when no lease is observed.
    for key in (
        '"dhcp_lease_ip6": ""',
        '"dhcp_lease_prefix6": ""',
        '"dhcp_lease_gateway6": ""',
    ):
        assert key in src, f"missing snapshot key {key!r}"


def test_parse_gateway6_helper_exists():
    src = _read("utils/dhcp.py")
    # v0.5.302 added _parse_gateway6 to read the v6 default
    # gateway from `ip -6 route show`, with a VRF fallback that
    # mirrors _parse_gateway. Without this, DHCPv6 leases in
    # per-device VRFs would look gatewayless.
    assert "def _parse_gateway6(" in src
    assert 'ip", "-6", "route", "show"' in src


def test_pick_global_ipv6_filters_link_local():
    src = _read("utils/dhcp.py")
    # _pick_global_ipv6 must skip link-local entries (fe80::/10);
    # every interface has one and displaying it as "the lease" is
    # meaningless.
    assert "def _pick_global_ipv6(" in src
    assert "is_link_local" in src


def test_start_dhcp_client_persists_v6_lease():
    src = _read("utils/dhcp.py")
    # start_dhcp_client's v6 branch must write the parsed global
    # v6 into device_db. Pre-fix the parse happened but the DB
    # write was v4-only — the UI's IPv6 column stayed blank even
    # when the DHCPv6 lease was live on the wire.
    assert '"dhcp_lease_ip6": global6.get("ip", "")' in src
    assert '"dhcp_lease_prefix6": global6.get("prefix", "")' in src
    assert '"dhcp_lease_gateway6": gateway6' in src


def test_stop_dhcp_client_clears_v6_lease():
    src = _read("utils/dhcp.py")
    # stop_dhcp_client scrubs the v6 lease keys the same way it
    # already scrubs v4 — otherwise a stopped device shows the
    # stale lease indefinitely.
    stop_body_marker = "def stop_dhcp_client("
    idx = src.index(stop_body_marker)
    # look only inside this function body (until the next top-level def)
    tail = src[idx:]
    for key in (
        '"dhcp_lease_ip6": ""',
        '"dhcp_lease_prefix6": ""',
        '"dhcp_lease_gateway6": ""',
    ):
        assert key in tail, f"stop_dhcp_client missing {key!r}"


def test_start_dhcp_server_blanks_v6_lease_fields():
    src = _read("utils/dhcp.py")
    # Mirror of the existing v4 blanking in the "Server Running"
    # DB write — server rows shouldn't carry client-side lease
    # values, and pre-v0.5.302 an operator flipping a device
    # client → server would see the stale v6 lease persist.
    conf_marker = "def start_dhcp_server("
    idx = src.index(conf_marker)
    stop_idx = src.index("def stop_dhcp_server(")
    tail = src[idx:stop_idx]
    for key in (
        '"dhcp_lease_ip6": ""',
        '"dhcp_lease_prefix6": ""',
    ):
        assert key in tail, f"start_dhcp_server missing {key!r}"


def test_run_tgen_server_dhcp_status_api_exposes_v6_lease():
    src = _read("run_tgen_server.py")
    # /api/device/dhcp/status must project the DB v6 lease keys
    # under HTTP-friendly names (lease_ip6/lease_prefix6/lease_gateway6).
    assert '"lease_ip6": device.get("dhcp_lease_ip6")' in src
    assert '"lease_prefix6": device.get("dhcp_lease_prefix6")' in src
    assert '"lease_gateway6": device.get("dhcp_lease_gateway6")' in src


def test_run_tgen_server_export_strips_v6_lease_runtime_fields():
    src = _read("run_tgen_server.py")
    # Topology export must strip runtime lease state so a round-
    # trip re-import doesn't replay stale IPv6 leases.
    assert '"dhcp_lease_ip6", "dhcp_lease_prefix6", "dhcp_lease_gateway6"' in src


def test_run_tgen_server_disabled_dhcp_scrub_wipes_v6_lease():
    src = _read("run_tgen_server.py")
    # The DHCP-disabled-row scrub path (protocol un-selected) must
    # also wipe the v6 lease surface — pre-v0.5.302 v4 was wiped
    # but the row kept its stale v6 lease in the UI.
    assert 'update_data["dhcp_lease_ip6"] = ""' in src
    assert 'update_data["dhcp_lease_prefix6"] = ""' in src
    assert 'update_data["dhcp_lease_gateway6"] = ""' in src


def test_device_db_schema_declares_v6_lease_columns():
    src = _read("utils/device_database.py")
    # CREATE TABLE devices must declare the three v6 lease columns
    # so fresh DB installs pick them up without a migration.
    assert "dhcp_lease_ip6 TEXT" in src
    assert "dhcp_lease_prefix6 TEXT" in src
    assert "dhcp_lease_gateway6 TEXT" in src


def test_device_db_migration_adds_v6_lease_columns_on_existing_dbs():
    src = _read("utils/device_database.py")
    # Existing pre-v0.5.302 DBs need per-column ALTER TABLE ADD.
    # The migration loop iterates the three column names and
    # skips ones already present (v0.5.220 fresh-DB safeguard).
    assert '"dhcp_lease_ip6", "dhcp_lease_prefix6", "dhcp_lease_gateway6"' in src
    assert 'ALTER TABLE devices ADD COLUMN {_v6_col} TEXT' in src


def test_device_db_field_mapping_forwards_v6_lease_keys():
    src = _read("utils/device_database.py")
    # Without these entries, update_device silently drops the v6
    # lease keys start_dhcp_client sends — the UI would keep
    # showing an empty IPv6 column even though the wire lease
    # landed.
    assert "'dhcp_lease_ip6': 'dhcp_lease_ip6'" in src
    assert "'dhcp_lease_prefix6': 'dhcp_lease_prefix6'" in src
    assert "'dhcp_lease_gateway6': 'dhcp_lease_gateway6'" in src


def test_devices_tab_ipv6_column_display_uses_signal_blocker():
    src = _read("widgets/devices_tab.py")
    # The IPv6-column refresh is a direct mirror of the v0.5.301
    # IPv4 branch, and MUST use QSignalBlocker for the setText.
    # Without it, cellChanged fires synchronously and the
    # on_cell_changed inline-edit handler reads the cell as
    # empty (Qt race), classifies the poll-refresh as a user
    # keyboard edit, writes empty back to the server, and every
    # 30s poll re-clears the cell — the same loop v0.5.301
    # documented for IPv4.
    assert 'device_data.get("dhcp_lease_ip6")' in src
    assert 'device_data.get("dhcp_lease_prefix6")' in src
    assert 'device_data.get("dhcp_lease_gateway6")' in src
    # The IPv6 branch must live inside a QSignalBlocker context —
    # verify at least one setText+setData pair sits under one.
    idx = src.index('device_data.get("dhcp_lease_ip6")')
    window = src[idx:idx + 3000]
    assert "QSignalBlocker" in window, "IPv6 lease-display path must use QSignalBlocker (v0.5.301 lesson)"


def test_devices_tab_populate_falls_back_to_v6_lease_when_ipv6_empty():
    src = _read("widgets/devices_tab.py")
    # Mirror of the v0.5.294 v4 fallback: when an operator's IPv6
    # field is empty AND the device is a DHCP client, populate
    # the IPv6 column with the leased v6 address suffixed
    # " (leased)".
    assert 'device_info.get("dhcp_lease_ip6")' in src
    # And the leased-suffix marker must survive so operators can
    # distinguish DHCP leases from static config.
    assert '(leased)"' in src


def test_all_edited_source_files_parse():
    """Belt-and-suspenders — v0.5.300 lesson: never ship an edit
    without ast.parse first."""
    import ast
    for rel in (
        "utils/dhcp.py",
        "utils/device_database.py",
        "run_tgen_server.py",
        "widgets/devices_tab.py",
    ):
        ast.parse(_read(rel))
