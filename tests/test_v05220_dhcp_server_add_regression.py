"""Runtime regression tests for v0.5.220 — the migration-ordering fix
that made "Add DHCP-server device" work again on a truly fresh
netgen-server install.

Background — v0.5.219 audit fix R1 (see CHANGELOG.md, v0.5.220 entry):

    Pre-fix, `_run_migrations` in `utils/device_database.py` read the
    devices/device_stats column lists once at the top of the function,
    then never refreshed them. Two "separate check" blocks (line ~556
    for devices, line ~580 for device_stats) tested
    `if 'ospf_ipv4_uptime' not in columns:` against those STALE lists
    — but the ospf_ipv4_uptime column had already been added a few
    lines above by the ospf_ipv4_running bundled add. On a genuinely
    fresh DB the check saw the column as missing, ran ALTER TABLE ADD
    COLUMN a second time, SQLite raised "duplicate column name:
    ospf_ipv4_uptime", the outer try/except at line ~741 swallowed
    everything, and every subsequent migration step (including
    v0.5.217 fix G's dhcp_manual_override columns, plus loopback_ipv4,
    isis_manual_override, dhcp_lease_subnet, and roughly ten other
    ALTER-TABLE blocks) was silently skipped.

    Downstream, `add_device` — which INSERTs INTO devices with
    columns like `loopback_ipv4` and `loopback_ipv6` — then failed
    with "no such column: loopback_ipv4", returned False, and the
    device row never landed. From the operator's side, the /api/
    device/apply endpoint returned HTTP 200 with {"status":
    "applied"} but nothing actually happened, matching the report
    "tried to add dhcp server device on the server, it does not
    seem to be working."

The tests here build a truly-fresh SQLite file (ONLY the source
`CREATE TABLE` statements — no migration columns pre-populated,
which is how a first-time install actually looks) and then verify
that DeviceDatabase(db_path=…) completes the migration end-to-end,
add_device works, and the C3 clear that start_dhcp_server writes at
the top of every start also lands.

The tests would ALL fail on v0.5.219 with the described migration
abort. On v0.5.220 they pass.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_create_table(src_text: str, table: str) -> str:
    """Pull one `CREATE TABLE IF NOT EXISTS <table> (...)` statement out of
    the source of utils/device_database.py. Used to seed a truly-fresh DB
    that matches what init_database would have produced BEFORE any
    migration ADD COLUMN blocks run."""
    pattern = (
        r'conn\.execute\("""\s*(CREATE TABLE IF NOT EXISTS '
        + re.escape(table)
        + r' .*?)"""\)'
    )
    match = re.search(pattern, src_text, re.DOTALL)
    assert match, f"could not extract {table} CREATE TABLE from source"
    return match.group(1)


def _seed_fresh_db(db_path: str) -> None:
    """Seed a truly-fresh DB: only the source CREATE TABLE statements
    (devices + device_stats + device_events + route_pools +
    device_route_pools + dhcp_pools + device_dhcp_pools), no migration
    columns pre-added.

    This is the SHAPE a real first-time netgen-server install produces
    the moment init_database runs — the migration is meant to bring the
    schema up to date from here.
    """
    src_text = (REPO / "utils" / "device_database.py").read_text()
    creates = [
        _extract_create_table(src_text, name)
        for name in (
            "devices",
            "device_stats",
            "device_events",
            "device_state_history",
            "route_pools",
            "device_route_pools",
            "dhcp_pools",
            "device_dhcp_pools",
        )
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for stmt in creates:
            conn.execute(stmt)
        conn.commit()


def _open_and_run_migration(db_path: str):
    """Instantiate DeviceDatabase, which triggers init_database →
    _run_migrations. Returns the instance so callers can exercise
    add_device / update_device against the migrated DB."""
    from utils.device_database import DeviceDatabase
    return DeviceDatabase(db_path=db_path)


def _columns_in(db_path: str, table: str) -> set:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# R1 — migration must complete end-to-end on a truly-fresh DB.
# ---------------------------------------------------------------------------

def test_R1_migration_completes_on_truly_fresh_db():
    """The pre-fix migration ABORTED at the first duplicate ADD COLUMN
    on a fresh DB (either the devices-table ospf_ipv4_uptime block at
    line ~556 or the device_stats-table one at line ~580), silently
    skipping every migration step after that abort point. Assert every
    column those skipped blocks would have added is now present."""
    tmp = tempfile.NamedTemporaryFile(
        prefix="netgen_v05220_r1_", suffix=".db", delete=False,
    )
    tmp.close()
    db_path = tmp.name
    try:
        _seed_fresh_db(db_path)

        # Sanity: the fresh seed should NOT already have the
        # post-abort columns (they're added by migration, not by
        # CREATE TABLE).
        pre_cols = _columns_in(db_path, "devices")
        assert "loopback_ipv4" not in pre_cols, (
            "seed shape drifted — loopback_ipv4 should be added by "
            "migration, not by CREATE TABLE"
        )
        assert "dhcp_manual_override" not in pre_cols, (
            "seed shape drifted — dhcp_manual_override should be "
            "added by migration, not by CREATE TABLE"
        )

        # Trigger the migration.
        _open_and_run_migration(db_path)

        # Every column the pre-fix migration would have silently
        # skipped (from line ~561 onward) MUST now be present.
        cols = _columns_in(db_path, "devices")

        missing = []
        for expected in (
            # The immediate duplicate that triggered the abort — must
            # exist (it was added by the ospf_ipv4_running block).
            "ospf_ipv4_uptime", "ospf_ipv6_uptime",
            # Every column added by blocks AFTER the abort point.
            "isis_config",
            "device_type", "container_id",
            "connection_method", "connection_host", "connection_port",
            "connection_username", "connection_info",
            "isis_running", "isis_established", "isis_state",
            "isis_neighbors", "isis_areas", "isis_system_id",
            "isis_net", "isis_uptime", "last_isis_check",
            "isis_manual_override", "isis_manual_override_time",
            "loopback_ipv4", "loopback_ipv6",
            "dhcp_lease_subnet",
            "dhcp_manual_override", "dhcp_manual_override_time",
        ):
            if expected not in cols:
                missing.append(expected)

        assert not missing, (
            f"migration is still aborting mid-way — these columns "
            f"were expected on a fresh-DB post-migration but are "
            f"missing: {missing!r}. On v0.5.219 the migration "
            f"aborted at the ospf_ipv4_uptime duplicate ADD COLUMN "
            f"and every block after that was silently skipped."
        )

        # Same check for device_stats — the second aborting block
        # was line ~580 (stats-table ospf_ipv4_uptime). Every column
        # after that in the stats-table blocks must be present.
        stats_cols = _columns_in(db_path, "device_stats")
        stats_missing = []
        for expected in (
            "ospf_ipv4_uptime", "ospf_ipv6_uptime",
            "isis_running", "isis_established", "isis_state",
            "isis_neighbors", "isis_areas", "isis_system_id",
            "isis_net", "isis_uptime", "last_isis_check",
        ):
            if expected not in stats_cols:
                stats_missing.append(expected)
        assert not stats_missing, (
            f"device_stats migration still incomplete — these "
            f"columns should be present after v0.5.220 fix R1: "
            f"{stats_missing!r}"
        )
    finally:
        for suffix in ("", "-wal", "-shm", ".backup"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def test_R1_add_device_works_on_fresh_db():
    """Operator's exact failing path: fresh netgen-server install,
    submit an Add-Device (DHCP-server or otherwise) — the /api/device/
    apply endpoint's `add_device` call must succeed.

    Pre-fix, `add_device`'s INSERT INTO devices (… loopback_ipv4,
    loopback_ipv6 …) VALUES (…) raised
        table devices has no column named loopback_ipv4
    because the migration had aborted before the loopback columns
    were added. add_device caught the exception and returned False;
    the device row never landed and the /api/device/apply response
    still said {"status": "applied"}, so the operator saw nothing
    but a silent no-op.
    """
    tmp = tempfile.NamedTemporaryFile(
        prefix="netgen_v05220_r1_add_", suffix=".db", delete=False,
    )
    tmp.close()
    db_path = tmp.name
    try:
        _seed_fresh_db(db_path)
        db = _open_and_run_migration(db_path)

        # This is the exact shape run_tgen_server.py:5121-5149
        # sends to add_device on a fresh Add-DHCP-server request.
        ok = db.add_device({
            "device_id": "dev-dhcp-srv-r1",
            "device_name": "test-dhcp-server-r1",
            "interface": "ens1f0",
            "ipv4_address": "192.168.30.1",
            "ipv4_mask": "24",
            "loopback_ipv4": "192.168.30.1",
            "vlan": "0",
            "protocols": ["DHCP"],
            "dhcp_config": {
                "mode": "server",
                "ipv4_enabled": True,
                "pool_start": "192.168.30.10",
                "pool_end": "192.168.30.200",
                "gateway": "192.168.30.1",
                "lease_time": 24,
            },
            "dhcp_mode": "server",
            "status": "Running",
        })
        assert ok is True, (
            "add_device returned False on a fresh netgen-server DB "
            "— pre-v0.5.220, this was the exact silent failure that "
            "made 'Add DHCP-server device does not seem to be "
            "working' the operator symptom."
        )
        row = db.get_device("dev-dhcp-srv-r1")
        assert row is not None, (
            "device row is missing after a successful add_device — "
            "the DB write silently dropped"
        )
        assert row.get("device_name") == "test-dhcp-server-r1"
        # loopback_ipv4 in particular — the column that pre-fix
        # didn't exist.
        assert row.get("loopback_ipv4") == "192.168.30.1", (
            f"loopback_ipv4 did not round-trip through add_device / "
            f"get_device — got {row.get('loopback_ipv4')!r}"
        )
    finally:
        for suffix in ("", "-wal", "-shm", ".backup"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def test_R1_start_dhcp_server_c3_write_works_on_fresh_db():
    """v0.5.219 fix C3 writes
        {"dhcp_manual_override": False, "dhcp_manual_override_time": None}
    at the top of start_dhcp_server (utils/dhcp.py:1465-1473) so the
    monitor's 120s override guard is cleared on every explicit Start.

    Pre-fix (the v0.5.220 bug), the dhcp_manual_override column was
    never created on a fresh DB, so `_update_device_db` (which wraps
    the call in a try/except) silently dropped the write — the
    override could not be cleared, the monitor would still skip the
    device for up to 120s after Add, and even after 120s the row
    lacked the columns the monitor uses to reason about state.

    Post-fix, both columns exist and the C3 write lands.
    """
    tmp = tempfile.NamedTemporaryFile(
        prefix="netgen_v05220_r1_c3_", suffix=".db", delete=False,
    )
    tmp.close()
    db_path = tmp.name
    try:
        _seed_fresh_db(db_path)
        db = _open_and_run_migration(db_path)

        # Seed a device row (mimics /api/device/apply having added it).
        db.add_device({
            "device_id": "dev-c3-r1",
            "device_name": "c3-r1",
            "interface": "eth1",
        })

        # Run C3's exact payload through the same wrapper
        # start_dhcp_server uses (_update_device_db). We check the
        # end state on the DB directly.
        from utils.dhcp import _update_device_db
        _update_device_db(db, "dev-c3-r1", {
            "dhcp_manual_override": False,
            "dhcp_manual_override_time": None,
        })

        # Read back — both fields must be persisted.
        row = db.get_device("dev-c3-r1")
        assert row is not None
        # SQLite BOOLEAN DEFAULT FALSE stores as INTEGER 0. Accept
        # either representation post-write.
        assert row.get("dhcp_manual_override") in (0, False), (
            f"dhcp_manual_override not persisted — got "
            f"{row.get('dhcp_manual_override')!r}. Pre-v0.5.220 "
            f"the column didn't exist and the write silently "
            f"dropped inside _update_device_db's except."
        )
        assert row.get("dhcp_manual_override_time") is None, (
            f"dhcp_manual_override_time should be NULL after the "
            f"C3 clear, got {row.get('dhcp_manual_override_time')!r}"
        )
    finally:
        for suffix in ("", "-wal", "-shm", ".backup"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# R1 source-level lock-in — guard against a future refactor that
# reintroduces the stale-columns-list ordering.
# ---------------------------------------------------------------------------

def _run_migrations_body() -> str:
    src = (REPO / "utils" / "device_database.py").read_text()
    match = re.search(
        r"def\s+_run_migrations\s*\(\s*self\s*,\s*conn\s*\)\s*:\s*"
        r"(.*?)(?=\n\s{0,4}def\s+\w+\s*\(self)",
        src, re.DOTALL,
    )
    assert match, "could not locate _run_migrations body"
    return match.group(1)


def _lines_between_last_pragma_and_check(body: str, pragma_table: str,
                                        check_var: str) -> int:
    """Return the number of newlines between the LAST
    `PRAGMA table_info(<pragma_table>)` and the FIRST following
    `if 'ospf_ipv4_uptime' not in <check_var>:`. Returns -1 if the
    check comes before any PRAGMA (regressed → stale list) or if
    the check text isn't found at all.
    """
    pragma_re = re.compile(
        rf"PRAGMA\s+table_info\s*\(\s*{re.escape(pragma_table)}\s*\)"
    )
    check_re = re.compile(
        rf"if\s+'ospf_ipv4_uptime'\s+not\s+in\s+{re.escape(check_var)}\s*:"
    )
    check_match = check_re.search(body)
    if not check_match:
        return -1
    check_start = check_match.start()
    # Find the LAST PRAGMA that ends before the check starts.
    last_pragma_end = -1
    for m in pragma_re.finditer(body):
        if m.end() > check_start:
            break
        last_pragma_end = m.end()
    if last_pragma_end < 0:
        return -1
    return body[last_pragma_end:check_start].count("\n")


def test_R1_migration_refreshes_columns_before_ospf_uptime_check():
    """Both "separate check" blocks for ospf_ipv4_uptime MUST refresh
    their column list from PRAGMA immediately before their check.
    Otherwise the stale-list ordering pattern returns and every
    fresh-DB install regresses again.

    The devices-table block must have a `PRAGMA table_info(devices)`
    read within a short window above `if 'ospf_ipv4_uptime' not in
    columns:`, and the device_stats block must have a
    `PRAGMA table_info(device_stats)` read within a short window
    above `if 'ospf_ipv4_uptime' not in stats_columns:`.
    """
    body = _run_migrations_body()

    devices_gap = _lines_between_last_pragma_and_check(
        body, "devices", "columns",
    )
    assert devices_gap >= 0, (
        "no PRAGMA table_info(devices) refresh appears before the "
        "ospf_ipv4_uptime devices-table check — the v0.5.220 R1 "
        "fix has regressed. Adding the column depends on a FRESH "
        "column-list read; a stale list will lie on a fresh DB and "
        "re-add the column → duplicate column name → migration "
        "aborts, and every subsequent ADD COLUMN block gets skipped."
    )
    assert devices_gap < 15, (
        f"PRAGMA table_info(devices) is {devices_gap} lines above "
        f"the ospf_ipv4_uptime check — the nearest PRAGMA is likely "
        f"an unrelated earlier refresh, not a dedicated one for this "
        f"block. Add a fresh PRAGMA read directly before the check."
    )

    stats_gap = _lines_between_last_pragma_and_check(
        body, "device_stats", "stats_columns",
    )
    assert stats_gap >= 0, (
        "no PRAGMA table_info(device_stats) refresh appears before "
        "the ospf_ipv4_uptime stats-table check — the v0.5.220 R1 "
        "fix has regressed on the stats side."
    )
    assert stats_gap < 15, (
        f"PRAGMA table_info(device_stats) is {stats_gap} lines "
        f"above the stats ospf_ipv4_uptime check — the nearest "
        f"PRAGMA is likely an unrelated earlier refresh, not a "
        f"dedicated one. Add a fresh PRAGMA read directly before "
        f"the check."
    )


def test_R1_version_bumped_to_at_least_0_5_220():
    """Guard against merges that would land the R1 fix without
    bumping pyproject.toml."""
    pyproject = (REPO / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match, "could not find version in pyproject.toml"
    version = match.group(1)
    parts = [int(p) for p in version.split(".")]
    assert parts >= [0, 5, 220], (
        f"pyproject.toml version {version!r} is behind the R1 fix "
        f"(expected >= 0.5.220)"
    )
