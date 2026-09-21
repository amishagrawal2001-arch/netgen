"""v0.5.398 — stream_database.py DB layer audit (5 items).

  O1  Cleanup queries use ISO cutoffs (matches storage format).
  O2  stop_stream zeros rates + returns False on 0-row match.
  O3  register_stream atomic UPSERT under BEGIN IMMEDIATE.
  O4  update_stream_statistics AND status='Running' + rowcount skip.
  O5  get_all_streams LIMIT + recent-stopped filter.
"""
from __future__ import annotations

import ast
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


def _strip_comments(src: str) -> str:
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_stream_database_ast_parses():
    ast.parse(_read("utils/stream_database.py"))


# ─── O1: datetime format ───


def test_o1_marker_present():
    src = _read("utils/stream_database.py")
    assert "v0.5.398 (audit stream-db O1)" in src


def test_o1_cleanup_uses_iso_cutoff_not_sqlite_datetime():
    """The two cleanup queries must NOT use SQLite's
    `datetime('now', ...)` (which emits space-separated timestamps
    that string-compare wrong against ISO-with-T rows). They must
    compute the cutoff in Python and pass it as a parameter."""
    src = _read("utils/stream_database.py")
    # Locate cleanup_old_statistics body
    _idx = src.index("def cleanup_old_statistics")
    _end = src.index("def cleanup_old_stopped_streams", _idx)
    _body = _strip_comments(src[_idx:_end])
    assert "datetime('now'" not in _body, (
        "cleanup_old_statistics still uses SQLite datetime('now', ...) "
        "— the string-compare bug is back"
    )
    assert "timedelta(days=days)" in _body

    _idx2 = src.index("def cleanup_old_stopped_streams")
    _end2 = src.index("def delete_stream", _idx2)
    _body2 = _strip_comments(src[_idx2:_end2])
    assert "datetime('now'" not in _body2
    assert "timedelta(hours=hours)" in _body2


def test_o1_runtime_repro_iso_vs_sqlite_compare():
    """Regression guard: prove that in SQLite's default TEXT
    collation, an ISO-with-T timestamp compares as GREATER than the
    same instant formatted with a space separator (which is what
    SQLite's datetime() emits). This is the root cause of O1."""
    _now = datetime.now(timezone.utc)
    _iso = _now.isoformat()
    _sqlite_style = _now.strftime("%Y-%m-%d %H:%M:%S")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        conn = sqlite3.connect(_p)
        conn.execute("CREATE TABLE t (ts TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (_iso,))
        conn.commit()
        # Under the pre-fix cleanup query, `ts < datetime('now')`
        # would match zero rows even when the row is OLDER than now
        # (because ISO 'T' > space). Simulate:
        c = conn.execute("SELECT COUNT(*) FROM t WHERE ts < ?", (_sqlite_style,))
        _matched_with_space = c.fetchone()[0]
        assert _matched_with_space == 0, (
            "expected the format-compare bug to still be reproducible "
            "so the O1 fix has something to guard against"
        )
        # With ISO-shaped cutoff (matching how rows are stored), a
        # future cutoff DOES match:
        _future_iso = (
            _now + timedelta(seconds=1)
        ).isoformat()
        c = conn.execute("SELECT COUNT(*) FROM t WHERE ts < ?", (_future_iso,))
        _matched_iso = c.fetchone()[0]
        assert _matched_iso == 1
        conn.close()
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── O2: stop_stream zeros rates + rowcount check ───


def test_o2_marker_present():
    src = _read("utils/stream_database.py")
    assert "v0.5.398 (audit stream-db O2)" in src


def test_o2_stop_stream_zeros_rates_and_checks_rowcount():
    src = _read("utils/stream_database.py")
    _idx = src.index("def stop_stream(self, stream_id: str) -> bool:")
    _end = src.index("def get_all_streams", _idx)
    body = src[_idx:_end]
    # UPDATE zeros rates
    assert "tx_rate = 0.0" in body
    assert "rx_rate = 0.0" in body
    # Checks cursor.rowcount
    assert "cursor.rowcount" in body
    # Returns False when no row matched
    assert "return False" in body


def test_o2_runtime_stop_stream_zeros_rates_and_returns_false_on_miss():
    from utils.stream_database import StreamDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = StreamDatabase(db_path=_p)
        db.init_database()
        assert db.register_stream(
            stream_id="live-sid", stream_name="s", interface="eth0",
        )
        assert db.update_stream_statistics(
            "live-sid", tx_count=100, rx_count=50,
            tx_rate=1000.0, rx_rate=500.0,
        )
        # Confirm live rates before stop
        row = db.get_stream_by_id("live-sid")
        assert row and row.get("tx_rate", 0) > 0

        # Stop the stream
        assert db.stop_stream("live-sid") is True

        # Rates zeroed
        row = db.get_stream_by_id("live-sid")
        assert row["status"] == "Stopped"
        assert row["tx_rate"] == 0.0
        assert row["rx_rate"] == 0.0

        # Missing stream_id returns False (not True)
        assert db.stop_stream("does-not-exist") is False
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── O3: atomic UPSERT ───


def test_o3_marker_present():
    src = _read("utils/stream_database.py")
    assert "v0.5.398 (audit stream-db O3)" in src


def test_o3_uses_begin_immediate_and_on_conflict():
    src = _read("utils/stream_database.py")
    _idx = src.index("def register_stream(")
    _end = src.index("def update_stream_statistics", _idx)
    body = src[_idx:_end]
    assert 'BEGIN IMMEDIATE' in body
    assert "ON CONFLICT(stream_id) DO UPDATE SET" in body


def test_o3_runtime_register_stream_races_no_dupes():
    """20 concurrent register_stream calls for the same stream_id must
    all succeed (return True) and the row must exist exactly once."""
    from utils.stream_database import StreamDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = StreamDatabase(db_path=_p)
        db.init_database()
        _errs = []
        _results = []

        def _worker():
            try:
                ok = db.register_stream(
                    stream_id="race-sid",
                    stream_name="n",
                    interface="eth0",
                )
                _results.append(ok)
            except Exception as e:
                _errs.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not _errs, f"race raised: {_errs[:3]}"
        assert all(_results), f"some register_stream returned False: {_results}"
        # Exactly one row
        rows = db.get_all_streams()
        assert len(rows) == 1
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── O4: status='Running' guard ───


def test_o4_marker_present():
    src = _read("utils/stream_database.py")
    assert "v0.5.398 (audit stream-db O4)" in src


def test_o4_update_uses_status_guard():
    src = _read("utils/stream_database.py")
    _idx = src.index("def update_stream_statistics")
    _end = src.index("def stop_stream", _idx)
    body = src[_idx:_end]
    assert "AND status = 'Running'" in body
    # Skips history INSERT when rowcount==0
    assert "_upd_cursor.rowcount == 0" in body


def test_o4_runtime_stopped_row_not_reincarnated_by_stats_update():
    from utils.stream_database import StreamDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = StreamDatabase(db_path=_p)
        db.init_database()
        db.register_stream(stream_id="sid", stream_name="s", interface="eth0")
        db.stop_stream("sid")
        # Attempt a stats update on the stopped row
        _ok = db.update_stream_statistics(
            "sid", tx_count=999, rx_count=999,
            tx_rate=9999.0, rx_rate=9999.0,
        )
        # Should refuse the update; status stays Stopped, rates stay 0.
        row = db.get_stream_by_id("sid")
        assert row["status"] == "Stopped"
        assert row["tx_rate"] == 0.0
        assert row["rx_rate"] == 0.0
        # update returned False per O4
        assert _ok is False
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── O5: LIMIT + recent-stopped filter ───


def test_o5_marker_present():
    src = _read("utils/stream_database.py")
    assert "v0.5.398 (audit stream-db O5)" in src


def test_o5_get_all_streams_has_limit_and_recent_filter():
    src = _read("utils/stream_database.py")
    _idx = src.index("def get_all_streams(")
    _end = src.index("def get_stream_by_id", _idx)
    body = src[_idx:_end]
    # Signature exposes limit + include_stopped_within_hours
    assert "limit: int = 1000" in body
    assert "include_stopped_within_hours" in body
    # LIMIT applied when > 0
    assert "LIMIT" in body


def test_o5_runtime_stopped_ghost_not_returned_by_default():
    from utils.stream_database import StreamDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = StreamDatabase(db_path=_p)
        db.init_database()
        db.register_stream(stream_id="ghost", stream_name="g", interface="eth0")
        db.stop_stream("ghost")
        # Backdate stopped_at to 48h ago
        _old_iso = (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat()
        with sqlite3.connect(_p) as _c:
            _c.execute("UPDATE streams SET stopped_at=? WHERE stream_id='ghost'",
                       (_old_iso,))
            _c.commit()
        # Default call: ghost is excluded
        _rows = db.get_all_streams()
        _ids = {r["stream_id"] for r in _rows}
        assert "ghost" not in _ids, (
            "48h-old Stopped row leaked into default get_all_streams — "
            "pending-cleanup ghost should be hidden"
        )
        # Explicit status='Stopped' still returns it (admin/debug)
        _rows_all = db.get_all_streams(status="Stopped")
        _ids_all = {r["stream_id"] for r in _rows_all}
        assert "ghost" in _ids_all
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── version guard ───


def test_pyproject_at_least_0598():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 398)


# ─── regression guards ───


def test_v0397_find_or_add_stream_intact():
    src = _read("multithreaded_traffic_gen.py")
    assert "def find_or_add_stream(self, stream):" in src


def test_v0396_launch_reservations_intact():
    src = _read("run_tgen_server.py")
    assert "_launch_reservations = set()" in src


def test_v0397_set_stream_field_intact():
    src = _read("multithreaded_traffic_gen.py")
    assert "def set_stream_field(self, interface, stream_id, key, value):" in src
