"""v0.5.288 — DeviceDatabase log spam: fast-return migrations on
subsequent constructions + downgrade always-fires wrappers to DEBUG.

Operator on srv06 2026-09-08 grepped 5 min of netgen-server logs
for `dhcp`, got ZERO matches — the log was 100% schema-column
dumps every ~3 seconds. Root cause: DeviceDatabase() is
constructed inline from ~30 sites (run_tgen_server, bgp,
frr_docker, ...). Every construction ran _run_migrations which
dumped the full ~100-column list TWICE at INFO. Post-fix, the
first construction per process runs migrations once and every
subsequent construction fast-returns.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _fresh_db_path(tag: str) -> str:
    return str(Path(tempfile.gettempdir()) / f"netgen_v05288_{tag}_{os.getpid()}.db")


def _reset_migration_gate() -> None:
    """Clear the module-level singleton so each test starts fresh.
    Otherwise tests would depend on order and the operator's real
    "second construction" behavior wouldn't be observable."""
    from utils import device_database as m
    with m._MIGRATIONS_LOCK:
        m._MIGRATIONS_APPLIED_FOR_PATHS.clear()


# ─────────────────────────────────────────────────────────────────
# Behavioral: singleton gate skips migrations on second construction
# ─────────────────────────────────────────────────────────────────

def test_second_construction_skips_migrations(caplog):
    """The operator symptom was recurring 'Checking for OSPF
    columns' at INFO. Post-fix: first construction runs migrations
    (may log INFO for schema-change events); second construction
    fast-returns from _run_migrations with ZERO log records from
    that function."""
    _reset_migration_gate()
    from utils.device_database import DeviceDatabase
    db_path = _fresh_db_path("second_ctor")
    # First construction: full migrations run.
    DeviceDatabase(db_path=db_path)
    # Second construction: should hit the singleton fast-return.
    with caplog.at_level(logging.DEBUG, logger="utils.device_database"):
        DeviceDatabase(db_path=db_path)
    # No "Adding X column" / "already exist" messages should
    # appear on the second construction — those all live inside
    # _run_migrations, which fast-returned.
    for record in caplog.records:
        assert "Adding " not in record.message, (
            f"second construction re-ran a migration step: {record.message!r}"
        )
        assert "Successfully added " not in record.message
        assert "already exist" not in record.message


def test_second_construction_no_column_dumps(caplog):
    """The single loudest signal in the operator's log: dumping
    the full column list every construction. Post-fix, second
    construction must not dump columns at ANY log level — the
    fast-return happens before the PRAGMA query."""
    _reset_migration_gate()
    from utils.device_database import DeviceDatabase
    db_path = _fresh_db_path("col_dump")
    DeviceDatabase(db_path=db_path)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="utils.device_database"):
        DeviceDatabase(db_path=db_path)
    for record in caplog.records:
        # These are the two specific dumps that spammed the log.
        assert "Current devices table columns" not in record.message, (
            f"second construction re-dumped devices columns: {record.message[:120]!r}"
        )
        assert "Checking for OSPF columns" not in record.message


def test_two_paths_migrate_independently(caplog):
    """Singleton keys by path — a second DB path should still run
    its own migrations. Verifies we don't accidentally silence
    migrations on truly-new databases."""
    _reset_migration_gate()
    from utils.device_database import DeviceDatabase
    path_a = _fresh_db_path("path_a")
    path_b = _fresh_db_path("path_b")
    DeviceDatabase(db_path=path_a)
    with caplog.at_level(logging.DEBUG, logger="utils.device_database"):
        DeviceDatabase(db_path=path_b)
    # path_b is new — its migration must have run (at least at
    # DEBUG). Look for the running-migrations marker.
    assert any(
        "Running database migrations" in r.message
        for r in caplog.records
    ), "second unique DB path should still migrate"


# ─────────────────────────────────────────────────────────────────
# Behavioral: log levels downgraded
# ─────────────────────────────────────────────────────────────────

def test_first_construction_wrapper_lines_at_debug(caplog):
    """The four always-fires wrapper log lines must be DEBUG, not
    INFO. These wrap init_database + _run_migrations and fire
    even on repeated constructions."""
    _reset_migration_gate()
    from utils.device_database import DeviceDatabase
    db_path = _fresh_db_path("wrapper_debug")
    with caplog.at_level(logging.DEBUG, logger="utils.device_database"):
        DeviceDatabase(db_path=db_path)
    wrapper_lines = {
        "Initialized database at",
        "Database tables and indexes created successfully",
        "Starting database migrations",
        "Database migrations completed",
    }
    for record in caplog.records:
        for marker in wrapper_lines:
            if marker in record.message:
                assert record.levelno == logging.DEBUG, (
                    f"wrapper line {marker!r} still at "
                    f"{record.levelname} (expected DEBUG). "
                    f"Full: {record.message[:200]!r}"
                )


def test_column_dumps_at_debug_when_migration_runs(caplog):
    """When migrations DO run (first construction), the two
    schema-dump lines must be at DEBUG so a normal INFO-level
    operator log stays readable even on server restart."""
    _reset_migration_gate()
    from utils.device_database import DeviceDatabase
    db_path = _fresh_db_path("first_dump_debug")
    with caplog.at_level(logging.DEBUG, logger="utils.device_database"):
        DeviceDatabase(db_path=db_path)
    for record in caplog.records:
        if "Current devices table columns" in record.message:
            assert record.levelno == logging.DEBUG, (
                f"column dump still at {record.levelname}"
            )
        if "Checking for OSPF columns in devices table" in record.message:
            assert record.levelno == logging.DEBUG, (
                f"OSPF-check dump still at {record.levelname}"
            )


def test_actual_schema_changes_still_at_info(caplog):
    """When a NEW schema change actually happens (fresh DB missing
    a column), the 'Adding X' / 'Successfully added X' lines must
    STAY at INFO — operators need to see real schema evolution."""
    _reset_migration_gate()
    from utils.device_database import DeviceDatabase
    db_path = _fresh_db_path("real_change_info")
    with caplog.at_level(logging.DEBUG, logger="utils.device_database"):
        DeviceDatabase(db_path=db_path)
    saw_add_info = False
    for record in caplog.records:
        if record.message.startswith("[DEVICE DB] Adding ") or record.message.startswith(
            "[DEVICE DB] Successfully added "
        ):
            saw_add_info = True
            # These fire once at first init when tables are being
            # populated with all their columns — but no, wait:
            # CREATE TABLE creates devices with the full schema at
            # once so "Adding" only fires on TRUE post-hoc
            # migrations. May not fire on a truly fresh DB.
            assert record.levelno == logging.INFO, (
                f"real migration event {record.message!r} at "
                f"{record.levelname} — must stay INFO"
            )
    # Note: we don't assert saw_add_info because a fresh DB has
    # every column in its CREATE TABLE — the ADD COLUMN paths
    # only fire on old DBs. The test above already verifies the
    # LEVEL if such a line is emitted.


# ─────────────────────────────────────────────────────────────────
# Source-level lock-ins
# ─────────────────────────────────────────────────────────────────

def test_source_has_v05288_marker():
    src = (REPO / "utils" / "device_database.py").read_text()
    assert "v0.5.288 (log-spam fix)" in src


def test_source_defines_singleton_gate():
    src = (REPO / "utils" / "device_database.py").read_text()
    assert "_MIGRATIONS_APPLIED_FOR_PATHS" in src
    assert "_MIGRATIONS_LOCK" in src
    # And the lock is a threading.Lock (not None, not asyncio).
    assert "threading.Lock()" in src


def test_run_migrations_returns_early_on_second_call():
    """Source-level: _run_migrations body must start with the
    singleton check + fast return before doing any log or PRAGMA."""
    src = (REPO / "utils" / "device_database.py").read_text()
    idx = src.find("def _run_migrations(self, conn):")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    # The singleton check appears before the migrations logging.
    check_pos = body.find("_MIGRATIONS_APPLIED_FOR_PATHS")
    running_log_pos = body.find("Running database migrations")
    assert check_pos > 0 and running_log_pos > 0 and check_pos < running_log_pos, (
        "singleton check must precede migration logging"
    )
    # The check adds path to the set.
    assert "_MIGRATIONS_APPLIED_FOR_PATHS.add(self.db_path)" in body


# ─────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────

def test_version_bumped():
    import re
    src = (REPO / "pyproject.toml").read_text()
    match = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert match, "no version line in pyproject.toml"
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    assert (major, minor, patch) >= (0, 5, 288), (
        f"version {major}.{minor}.{patch} < 0.5.288"
    )
