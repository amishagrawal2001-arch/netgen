"""v0.5.266 — DB layer audit: 5 correctness fixes."""

from pathlib import Path
import re
import sqlite3
import tempfile
import sys

REPO = Path(__file__).resolve().parents[1]
DDB = (REPO / "utils" / "device_database.py").read_text()
SDB = (REPO / "utils" / "stream_database.py").read_text()


# --- DB-F1: SQL operator-precedence fix ---------------------------


def test_cleanup_old_streams_parenthesizes_or_branches():
    assert "audit DB-F1" in SDB
    # Both OR branches nested under a single `WHERE status='Stopped'`.
    assert "WHERE status = 'Stopped'" in SDB
    assert "AND (" in SDB
    # The old broken shape (unparenthesized OR at same level as AND) is gone
    # as a live SQL line.
    live_old = [
        line for line in SDB.splitlines()
        if "AND (stopped_at IS NOT NULL AND stopped_at" in line
        and "OR" not in SDB[
            SDB.find(line) : SDB.find(line) + 300
        ]  # look for OR sibling within window
    ]
    # Note: less strict check — the fixed shape has AND ( ... OR ... ) all
    # inside a single parenthesized group. Confirm by structural check:
    idx = SDB.find("audit DB-F1")
    body = SDB[idx:idx + 2000]
    # New nested parens shape.
    assert "AND (" in body and "OR (stopped_at IS NULL" in body


def test_cleanup_old_streams_runtime_only_deletes_stopped():
    """Runtime proof: create a Running stream older than the cutoff
    and a Stopped stream older than the cutoff. Only the Stopped
    one should be deleted."""
    sys.path.insert(0, str(REPO))
    try:
        from utils.stream_database import StreamDatabase
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            sdb = StreamDatabase(db_path=str(db_path))
            # Insert two streams directly with ancient updated_at
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute("""
                    INSERT INTO streams (stream_id, stream_name, interface,
                        stream_config, status, created_at, updated_at,
                        started_at, stopped_at, tx_count, rx_count)
                    VALUES ('running-1', 'running', 'eth0', '{}',
                        'Running', '2020-01-01', '2020-01-01',
                        '2020-01-01', NULL, 0, 0)
                """)
                conn.execute("""
                    INSERT INTO streams (stream_id, stream_name, interface,
                        stream_config, status, created_at, updated_at,
                        started_at, stopped_at, tx_count, rx_count)
                    VALUES ('stopped-1', 'stopped', 'eth0', '{}',
                        'Stopped', '2020-01-01', '2020-01-01',
                        '2020-01-01', '2020-01-01', 0, 0)
                """)
                conn.commit()
            deleted = sdb.cleanup_old_stopped_streams(hours=1)
            assert deleted == 1  # only the stopped one
            # Running stream must survive.
            with sqlite3.connect(str(db_path)) as conn:
                surviving = conn.execute(
                    "SELECT stream_id FROM streams"
                ).fetchall()
                assert ("running-1",) in surviving
                assert ("stopped-1",) not in surviving
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


# --- DB-F4: device_events_archive table for terminal events -------


def test_device_events_archive_table_created():
    assert "audit DB-F4" in DDB
    assert "CREATE TABLE IF NOT EXISTS device_events_archive" in DDB
    # Explicit: NO FK, so the row survives parent deletion.
    idx = DDB.find("CREATE TABLE IF NOT EXISTS device_events_archive")
    body = DDB[idx:idx + 1000]
    assert "FOREIGN KEY" not in body
    # And there's an index on device_id.
    assert "idx_events_archive_device" in DDB


def test_remove_device_writes_to_archive_before_delete():
    """The removal event must go to the archive table so the FK
    cascade doesn't purge it. Same-transaction as the DELETE for
    atomicity."""
    # Anchor on `def remove_device` — there are TWO occurrences of
    # `audit DB-F4` (schema comment + remove_device body).
    idx = DDB.find("def remove_device")
    end = DDB.find("\n    def ", idx + 1)
    body = DDB[idx:end if end > 0 else idx + 5000]
    assert "audit DB-F4" in body
    assert "INSERT INTO device_events_archive" in body
    assert '"removed"' in body
    # Pre-fix log_device_event(..., "removed", ...) call is gone.
    live_old = [
        line for line in DDB.splitlines()
        if 'self.log_device_event(device_id, "removed"' in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"old log_device_event('removed') still live: {live_old!r}"


# --- DB-F6: backup uses SQLite .backup() API ---------------------


def test_backup_uses_sqlite_backup_api_not_shutil():
    assert "audit DB-F6" in DDB
    idx = DDB.find("def backup_database")
    end = DDB.find("\n    def restore_database", idx + 1)
    body = DDB[idx:end if end > 0 else idx + 3000]
    # SQLite's online-backup call.
    assert "_src.backup(_dst)" in body
    # And the old shutil.copy2 primary path is gone (fallback for
    # pointer only is OK).
    live_shutil = [
        line for line in body.splitlines()
        if "shutil.copy2(self.db_path, self.backup_path)" in line
        and not line.lstrip().startswith("#")
    ]
    assert live_shutil == [], f"shutil.copy2 primary backup still live: {live_shutil!r}"


def test_backup_rotates_to_timestamped_file():
    idx = DDB.find("audit DB-F6")
    body = DDB[idx:idx + 3000]
    # Rotation uses a datetime-formatted suffix.
    assert '_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")' in body
    assert '_dated = f"{self.backup_path}.{_ts}"' in body


# --- DB-F13: save_route_pools_batch = single transaction ---------


def test_save_route_pools_batch_single_transaction():
    assert "audit DB-F13" in DDB
    idx = DDB.find("def save_route_pools_batch")
    end = DDB.find("\n    def ", idx + 1)
    body = DDB[idx:end if end > 0 else idx + 3000]
    # Single connection scope + BEGIN.
    assert 'conn.execute("BEGIN")' in body
    assert "conn.commit()" in body
    # Fallback for missing helper is guarded but does exist.
    assert "except AttributeError:" in body


# --- DB-F14: JSON parse errors log a warning ---------------------


def test_dhcp_config_parse_error_logs_warning():
    idx = DDB.find("def _prepare_dhcp_config")
    end = DDB.find("\n    @staticmethod", idx + 1)
    body = DDB[idx:end if end > 0 else idx + 2500]
    assert "audit DB-F14" in body
    # Warning includes truncated raw payload.
    assert "Failed to parse dhcp_config JSON" in body
    assert "raw={str(raw_config)[:200]!r}" in body


def test_vxlan_config_parse_error_logs_warning():
    idx = DDB.find("def _prepare_vxlan_config")
    end = DDB.find("\n    @staticmethod", idx + 1)
    body = DDB[idx:end if end > 0 else idx + 3000]
    assert "audit DB-F14" in body
    assert "Failed to parse vxlan_config JSON" in body


# --- Runtime: JSON parse doesn't corrupt state --------------------


def test_dhcp_config_parse_returns_empty_on_bad_json():
    """The fix retains the {} fallback so bad input doesn't crash,
    just gets logged."""
    sys.path.insert(0, str(REPO))
    try:
        from utils.device_database import DeviceDatabase
        # Static method — no need to instantiate.
        result = DeviceDatabase._prepare_dhcp_config("{{ not valid json ")
        assert result == {}
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 266)
