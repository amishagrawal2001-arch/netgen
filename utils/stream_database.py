"""
Stream Database Management for OSTG
SQLite-based stream database for tracking active streams and statistics
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

class StreamDatabase:
    """SQLite-based stream database for OSTG"""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize stream database (uses same DB file as device database).

        Args:
            db_path: Path to SQLite database file. When None (default),
                     resolved via utils.device_database._resolve_db_path()
                     — env var → /opt/netgen/database.db → legacy
                     /opt/OSTG/device_database.db fallback. Both
                     DeviceDatabase and StreamDatabase share the same
                     file so they must agree on resolution.
        """
        if db_path is None:
            try:
                from utils.device_database import _resolve_db_path
                db_path = _resolve_db_path()
            except Exception:
                # Defensive fallback if the import path differs in some
                # deployment layouts.
                db_path = "/opt/netgen/database.db"
        self.db_path = db_path
        self.ensure_db_directory()
        self.init_database()
        logger.info(f"[STREAM DB] Initialized stream database at {self.db_path}")
    
    def ensure_db_directory(self):
        """Ensure database directory exists with proper permissions."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        # Set proper permissions (readable/writable by ostg user)
        try:
            os.chmod(db_dir, 0o755)
        except Exception as e:
            logger.warning(f"[STREAM DB] Could not set directory permissions: {e}")
    
    def init_database(self):
        """Initialize database with streams table and indexes."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
            conn.execute("PRAGMA synchronous = NORMAL")  # Good balance of safety/speed
            
            # Create streams table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS streams (
                    stream_id TEXT PRIMARY KEY,
                    stream_name TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    rx_interface TEXT,
                    server_url TEXT,
                    tg_id INTEGER,
                    flow_tracking_enabled BOOLEAN DEFAULT FALSE,
                    status TEXT DEFAULT 'Stopped',  -- 'Running', 'Stopped', 'Error'
                    tx_count INTEGER DEFAULT 0,
                    rx_count INTEGER DEFAULT 0,
                    tx_rate REAL DEFAULT 0.0,  -- packets per second
                    rx_rate REAL DEFAULT 0.0,   -- packets per second
                    last_tx_count INTEGER DEFAULT 0,
                    last_rx_count INTEGER DEFAULT 0,
                    last_update TIMESTAMP,
                    started_at TIMESTAMP,
                    stopped_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    stream_config TEXT  -- JSON object with full stream configuration
                )
            """)
            
            # Create stream statistics history table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stream_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stream_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tx_count INTEGER DEFAULT 0,
                    rx_count INTEGER DEFAULT 0,
                    tx_rate REAL DEFAULT 0.0,
                    rx_rate REAL DEFAULT 0.0,
                    FOREIGN KEY (stream_id) REFERENCES streams(stream_id) ON DELETE CASCADE
                )
            """)
            
            # Create indexes for faster queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_streams_interface ON streams(interface)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_streams_status ON streams(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_streams_tg_id ON streams(tg_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_stats_stream_id ON stream_stats(stream_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_stats_timestamp ON stream_stats(timestamp)")
            
            conn.commit()
            logger.info("[STREAM DB] Database tables and indexes created")
    
    def register_stream(self, stream_id: str, stream_name: str, interface: str, 
                       rx_interface: Optional[str] = None, server_url: Optional[str] = None,
                       tg_id: Optional[int] = None, flow_tracking_enabled: bool = False,
                       stream_config: Optional[Dict[str, Any]] = None) -> bool:
        """
        Register a new stream in the database.
        
        Args:
            stream_id: Unique stream identifier
            stream_name: Human-readable stream name
            interface: TX interface name
            rx_interface: RX interface name (optional)
            server_url: Server URL where stream is running
            tg_id: Traffic generator ID
            flow_tracking_enabled: Whether flow tracking is enabled
            stream_config: Full stream configuration (JSON)
            
        Returns:
            True if successful, False otherwise
        """
        # v0.5.398 (audit stream-db O3): atomic UPSERT under
        # BEGIN IMMEDIATE. Pre-fix, register_stream did
        # SELECT-then-INSERT-or-UPDATE across THREE separate
        # statements with no transaction wrapping them. Two known bugs:
        #   1. TOCTOU: two concurrent register_streams for the same
        #      stream_id both saw `exists=False`, one INSERT succeeded,
        #      the other raised IntegrityError caught by the bare
        #      `except Exception` at :209 — returned False with no way
        #      to distinguish "duplicate" from "disk full" for
        #      telemetry.
        #   2. Delete race: if delete_stream slipped between the SELECT
        #      and the UPDATE branch, the UPDATE affected zero rows,
        #      commit() succeeded, and this method still returned
        #      True — SAME "DB lies" class as M3.
        # Fix: BEGIN IMMEDIATE to acquire the write lock up front,
        # then INSERT ... ON CONFLICT(stream_id) DO UPDATE in a single
        # atomic statement. Also compute the was_stopped decision via
        # ON CONFLICT with a SELECT inside — no separate query.
        try:
            config_json = json.dumps(stream_config) if stream_config else None
            now = datetime.now(timezone.utc).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                conn.isolation_level = None  # manage txn ourselves
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Snapshot pre-existing status under the write
                    # lock so the was_stopped branch decision matches
                    # what the UPSERT will see.
                    cursor_status = conn.execute(
                        "SELECT status FROM streams WHERE stream_id = ?",
                        (stream_id,),
                    )
                    _row = cursor_status.fetchone()
                    _existed = _row is not None
                    _was_stopped = _existed and _row[0] == 'Stopped'

                    if _existed and not _was_stopped:
                        # Continuing stream — preserve last_update /
                        # counter baselines for accurate rate calc.
                        conn.execute("""
                            UPDATE streams SET
                                stream_name = ?,
                                interface = ?,
                                rx_interface = ?,
                                server_url = ?,
                                tg_id = ?,
                                flow_tracking_enabled = ?,
                                status = 'Running',
                                updated_at = ?,
                                stream_config = ?
                            WHERE stream_id = ?
                        """, (
                            stream_name, interface, rx_interface, server_url, tg_id,
                            int(flow_tracking_enabled), now, config_json, stream_id,
                        ))
                    else:
                        # New OR reincarnating (was_stopped) — INSERT
                        # ON CONFLICT UPDATE so the whole thing is one
                        # atomic statement no matter which branch wins
                        # the race. Reset baseline counters (they're
                        # the from-zero-since-last-start counters, not
                        # the historical totals).
                        conn.execute("""
                            INSERT INTO streams (
                                stream_id, stream_name, interface, rx_interface,
                                server_url, tg_id, flow_tracking_enabled, status,
                                started_at, updated_at, last_update,
                                last_tx_count, last_rx_count, tx_count, rx_count,
                                stream_config
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Running', ?, ?, ?, 0, 0, 0, 0, ?)
                            ON CONFLICT(stream_id) DO UPDATE SET
                                stream_name = excluded.stream_name,
                                interface = excluded.interface,
                                rx_interface = excluded.rx_interface,
                                server_url = excluded.server_url,
                                tg_id = excluded.tg_id,
                                flow_tracking_enabled = excluded.flow_tracking_enabled,
                                status = 'Running',
                                started_at = excluded.started_at,
                                updated_at = excluded.updated_at,
                                last_update = excluded.last_update,
                                last_tx_count = 0,
                                last_rx_count = 0,
                                tx_count = 0,
                                rx_count = 0,
                                tx_rate = 0.0,
                                rx_rate = 0.0,
                                stopped_at = NULL,
                                stream_config = excluded.stream_config
                        """, (
                            stream_id, stream_name, interface, rx_interface,
                            server_url, tg_id, int(flow_tracking_enabled),
                            now, now, now, config_json,
                        ))
                    conn.execute("COMMIT")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    raise

            logger.info(f"[STREAM DB] Registered stream '{stream_name}' (ID: {stream_id}) on {interface}")
            return True
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to register stream {stream_id}: {e}")
            return False
    
    def update_stream_statistics(self, stream_id: str, tx_count: int, rx_count: int,
                                tx_rate: Optional[float] = None, rx_rate: Optional[float] = None) -> bool:
        """
        Update stream statistics in the database.
        
        Args:
            stream_id: Stream identifier
            tx_count: Total TX packet count
            rx_count: Total RX packet count
            tx_rate: TX rate in packets per second (optional)
            rx_rate: RX rate in packets per second (optional)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                # Get previous counts to calculate rates if not provided
                cursor = conn.execute(
                    "SELECT tx_count, rx_count, last_tx_count, last_rx_count, last_update FROM streams WHERE stream_id = ?",
                    (stream_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    prev_tx, prev_rx, last_tx, last_rx, last_update_str = row
                    
                    # Debug logging
                    logger.info(f"[STREAM DB] Updating stats for {stream_id}: tx_count={tx_count} (prev_tx={prev_tx}, last_tx={last_tx}), rx_count={rx_count} (prev_rx={prev_rx}, last_rx={last_rx}), last_update={last_update_str}")
                    
                    # Calculate rates if not provided
                    if tx_rate is None or rx_rate is None:
                        # Initialize last_update if it's None (first update)
                        if not last_update_str:
                            # Use started_at or current time as baseline
                            cursor2 = conn.execute(
                                "SELECT started_at FROM streams WHERE stream_id = ?",
                                (stream_id,)
                            )
                            started_row = cursor2.fetchone()
                            if started_row and started_row[0]:
                                try:
                                    last_update = datetime.fromisoformat(started_row[0].replace('Z', '+00:00'))
                                    last_update_str = started_row[0]
                                except Exception:
                                    last_update = datetime.now(timezone.utc)
                                    last_update_str = now
                            else:
                                last_update = datetime.now(timezone.utc)
                                last_update_str = now
                        
                        if last_update_str:
                            try:
                                last_update = datetime.fromisoformat(last_update_str.replace('Z', '+00:00'))
                                current_time = datetime.now(timezone.utc)
                                time_diff = (current_time - last_update).total_seconds()
                                
                                # Need at least 1 second difference to calculate meaningful rate
                                if time_diff >= 1.0:
                                    if tx_rate is None:
                                        # Always use last_tx_count for rate calculation (it's the count from last update)
                                        # If last_tx_count is None or 0, use prev_tx (current tx_count in DB) as fallback
                                        if last_tx is not None:
                                            base_tx = last_tx
                                        elif prev_tx is not None:
                                            base_tx = prev_tx
                                        else:
                                            base_tx = 0
                                        
                                        # Calculate rate if counts have changed
                                        if tx_count != base_tx:
                                            tx_rate = (tx_count - base_tx) / time_diff
                                            logger.info(f"[STREAM DB] ✅ Calculated tx_rate for {stream_id}: ({tx_count} - {base_tx}) / {time_diff:.2f}s = {tx_rate:.2f} pps (last_tx={last_tx}, prev_tx={prev_tx})")
                                        else:
                                            # Counts haven't changed - try to preserve existing rate
                                            cursor_rate = conn.execute(
                                                "SELECT tx_rate FROM streams WHERE stream_id = ?",
                                                (stream_id,)
                                            )
                                            rate_row = cursor_rate.fetchone()
                                            if rate_row and rate_row[0] and rate_row[0] > 0:
                                                tx_rate = rate_row[0]
                                                logger.debug(f"[STREAM DB] Preserving existing tx_rate for {stream_id}: {tx_rate:.2f} pps")
                                            else:
                                                tx_rate = 0.0
                                                logger.warning(f"[STREAM DB] ⚠️ Cannot calculate tx_rate for {stream_id}: counts unchanged (tx_count={tx_count}, base_tx={base_tx}, last_tx={last_tx}, prev_tx={prev_tx})")
                                    
                                    if rx_rate is None:
                                        # Same logic for RX - always use last_rx_count
                                        if last_rx is not None:
                                            base_rx = last_rx
                                        elif prev_rx is not None:
                                            base_rx = prev_rx
                                        else:
                                            base_rx = 0
                                        
                                        # Calculate rate if counts have changed
                                        if rx_count != base_rx:
                                            rx_rate = (rx_count - base_rx) / time_diff
                                            logger.info(f"[STREAM DB] ✅ Calculated rx_rate for {stream_id}: ({rx_count} - {base_rx}) / {time_diff:.2f}s = {rx_rate:.2f} pps")
                                        else:
                                            # Counts haven't changed - try to preserve existing rate
                                            cursor_rate = conn.execute(
                                                "SELECT rx_rate FROM streams WHERE stream_id = ?",
                                                (stream_id,)
                                            )
                                            rate_row = cursor_rate.fetchone()
                                            if rate_row and rate_row[0] and rate_row[0] > 0:
                                                rx_rate = rate_row[0]
                                                logger.debug(f"[STREAM DB] Preserving existing rx_rate for {stream_id}: {rx_rate:.2f} pps")
                                            else:
                                                rx_rate = 0.0
                                                logger.warning(f"[STREAM DB] ⚠️ Cannot calculate rx_rate for {stream_id}: counts unchanged (rx_count={rx_count}, base_rx={base_rx})")
                                elif time_diff > 0:
                                    # Less than 1 second - rate would be inaccurate, keep previous rate or 0
                                    # Get current rates from DB to preserve them
                                    cursor_rate = conn.execute(
                                        "SELECT tx_rate, rx_rate FROM streams WHERE stream_id = ?",
                                        (stream_id,)
                                    )
                                    rate_row = cursor_rate.fetchone()
                                    if rate_row:
                                        existing_tx_rate, existing_rx_rate = rate_row
                                        tx_rate = tx_rate if tx_rate is not None else (existing_tx_rate if existing_tx_rate else 0.0)
                                        rx_rate = rx_rate if rx_rate is not None else (existing_rx_rate if existing_rx_rate else 0.0)
                                    else:
                                        tx_rate = tx_rate or 0.0
                                        rx_rate = rx_rate or 0.0
                                    logger.debug(f"[STREAM DB] Time diff too small ({time_diff:.2f}s) for {stream_id}, preserving existing rates")
                                else:
                                    tx_rate = tx_rate or 0.0
                                    rx_rate = rx_rate or 0.0
                            except Exception as e:
                                logger.debug(f"[STREAM DB] Error calculating rates for {stream_id}: {e}")
                                tx_rate = tx_rate or 0.0
                                rx_rate = rx_rate or 0.0
                        else:
                            tx_rate = tx_rate or 0.0
                            rx_rate = rx_rate or 0.0
                    
                    # v0.5.398 (audit stream-db O4): guard against
                    # reincarnating rates on a just-stopped row.
                    # Pre-fix, this UPDATE had no `AND status='Running'`
                    # clause — a stats update racing with stop_stream
                    # (from another thread OR from
                    # _poll_stream_statistics's own reconcile at
                    # run_tgen_server.py:30814) would overwrite the
                    # zero'd rates from stop_stream (v0.5.398 O2)
                    # with the pre-stop live tx_rate/rx_rate. Combined
                    # with pre-O2 behavior, a Stopped stream could
                    # show tx_rate=42000 pps indefinitely. Now: filter
                    # on status='Running'; if rowcount==0 the row was
                    # stopped/deleted between the SELECT above and
                    # this UPDATE — log + skip the history INSERT (a
                    # history row for a stopped stream is misleading
                    # noise).
                    _upd_cursor = conn.execute("""
                        UPDATE streams SET
                            tx_count = ?,
                            rx_count = ?,
                            tx_rate = ?,
                            rx_rate = ?,
                            last_tx_count = ?,
                            last_rx_count = ?,
                            last_update = ?,
                            updated_at = ?
                        WHERE stream_id = ? AND status = 'Running'
                    """, (tx_count, rx_count, tx_rate, rx_rate, tx_count, rx_count, now, now, stream_id))
                    if _upd_cursor.rowcount == 0:
                        logger.debug(
                            f"[STREAM DB] Skipping stats update for "
                            f"{stream_id}: row is no longer Running "
                            f"(stopped/deleted between fetch and write)"
                        )
                        conn.commit()
                        return False

                    # Insert into history table (only when the UPDATE
                    # actually touched a Running row).
                    conn.execute("""
                        INSERT INTO stream_stats (stream_id, timestamp, tx_count, rx_count, tx_rate, rx_rate)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (stream_id, now, tx_count, rx_count, tx_rate, rx_rate))

                    conn.commit()
                    return True
                else:
                    logger.warning(f"[STREAM DB] Stream {stream_id} not found for statistics update")
                    return False
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to update statistics for stream {stream_id}: {e}")
            return False
    
    def stop_stream(self, stream_id: str) -> bool:
        """
        Mark a stream as stopped in the database.

        Args:
            stream_id: Stream identifier

        Returns:
            True if a row was updated, False if the stream_id was not
            found or the update failed. v0.5.398 (audit stream-db O2):
            pre-fix this returned True unconditionally on any commit
            success — even when zero rows matched (already-deleted or
            typo id). Callers at run_tgen_server.py:2516 / :2574 /
            :30719 / :30814 all trust the bool, so a stopped stream
            missing from DB looked successful and the operator got no
            feedback that the stop-write didn't land.
        """
        # v0.5.398 (audit stream-db O2): also zero tx_rate / rx_rate.
        # Pre-fix, stop_stream left those fields intact so
        # `get_all_streams(status='Stopped')` returned rows displaying
        # the LIVE pps values from the last poll. This is the same
        # "DB lies about state" class as v0.5.396 M3 — the client's
        # Traffic Statistics tab would show a "Stopped" stream still
        # putting out packets. tx_count / rx_count are preserved
        # (they're historical totals the operator wants to keep
        # visible for the final tally); only the per-second rates
        # are cleared.
        try:
            now = datetime.now(timezone.utc).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    UPDATE streams SET
                        status = 'Stopped',
                        stopped_at = ?,
                        updated_at = ?,
                        tx_rate = 0.0,
                        rx_rate = 0.0
                    WHERE stream_id = ?
                """, (now, now, stream_id))
                _rowcount = cursor.rowcount
                conn.commit()

            if _rowcount == 0:
                logger.warning(
                    f"[STREAM DB] stop_stream: no row matched "
                    f"stream_id={stream_id} — already-deleted or typo id"
                )
                return False
            logger.info(f"[STREAM DB] Marked stream {stream_id} as stopped (rowcount={_rowcount})")
            return True
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to stop stream {stream_id}: {e}")
            return False
    
    def get_all_streams(self, status: Optional[str] = None,
                        tg_id: Optional[int] = None,
                        limit: int = 1000,
                        include_stopped_within_hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get all streams from the database.

        Args:
            status: Filter by status ('Running', 'Stopped', 'Error')
            tg_id: Filter by traffic generator ID
            limit: Hard cap on the number of rows returned.
                v0.5.398 (audit stream-db O5): pre-fix this method had
                no LIMIT and was called by the ~2 s poll thread in
                run_tgen_server.py + by every /api/streams client
                poll. Combined with the pre-O1 broken cleanup, the
                `streams` table grew without bound and each poll
                turned into O(N) plus O(N * JSON parse) for the
                stream_config field. Default 1000 protects the poll
                path; callers that legitimately need everything
                (admin exports, cleanup drivers) can pass a bigger
                number or None.
            include_stopped_within_hours: Only return Stopped rows
                whose stopped_at is within this many hours (defaults
                to matching cleanup_old_stopped_streams's 24 h
                default). Older Stopped rows are pending-cleanup and
                the UI shouldn't render them.

        Returns:
            List of stream dictionaries
        """
        # Compute the "recent Stopped" cutoff in the same ISO shape
        # rows are stored (v0.5.398 O1 fix — string compare only works
        # when both sides use the same format).
        _stopped_cutoff = None
        if include_stopped_within_hours and include_stopped_within_hours > 0:
            _stopped_cutoff = (
                datetime.now(timezone.utc)
                - timedelta(hours=include_stopped_within_hours)
            ).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                query = "SELECT * FROM streams WHERE 1=1"
                params: List[Any] = []

                if status:
                    query += " AND status = ?"
                    params.append(status)

                if tg_id is not None:
                    query += " AND tg_id = ?"
                    params.append(tg_id)

                # v0.5.398 O5: when NO explicit status filter, hide
                # stopped rows older than the cutoff so the UI/poll
                # doesn't render pending-cleanup ghosts.
                if not status and _stopped_cutoff is not None:
                    query += (
                        " AND (status = 'Running' OR status IS NULL"
                        " OR stopped_at IS NULL"
                        " OR stopped_at >= ?)"
                    )
                    params.append(_stopped_cutoff)

                query += " ORDER BY created_at DESC"
                if limit is not None and limit > 0:
                    query += f" LIMIT {int(limit)}"

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

                streams = []
                for row in rows:
                    stream = dict(row)
                    # Parse JSON fields
                    if stream.get("stream_config"):
                        try:
                            stream["stream_config"] = json.loads(stream["stream_config"])
                        except Exception:
                            pass
                    streams.append(stream)

                return streams
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to get streams: {e}")
            return []
    
    def get_stream_by_id(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific stream by ID.
        
        Args:
            stream_id: Stream identifier
            
        Returns:
            Stream dictionary or None if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM streams WHERE stream_id = ?", (stream_id,))
                row = cursor.fetchone()
                
                if row:
                    stream = dict(row)
                    # Parse JSON fields
                    if stream.get("stream_config"):
                        try:
                            stream["stream_config"] = json.loads(stream["stream_config"])
                        except Exception:
                            pass
                    return stream
                return None
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to get stream {stream_id}: {e}")
            return None
    
    def cleanup_old_statistics(self, days: int = 7) -> int:
        """
        Clean up old statistics records.

        Args:
            days: Number of days to keep statistics

        Returns:
            Number of records deleted
        """
        # v0.5.398 (audit stream-db O1): datetime format-compare bug.
        # Rows are stored via `datetime.now(timezone.utc).isoformat()`
        # which emits e.g. "2026-09-21T15:30:00.123456+00:00" — ISO
        # 8601 with the literal 'T' separator at position 10. But
        # SQLite's `datetime('now', ...)` emits
        # "2026-09-21 15:30:00" — space separator at position 10.
        # String comparison in SQLite (default TEXT collation) sees
        # 'T' (0x54) > ' ' (0x20) at position 10, so EVERY ISO-formatted
        # `timestamp` value compares as GREATER than ANY SQLite-formatted
        # cutoff value from the same instant. Result: `WHERE timestamp
        # < cutoff` matched ZERO rows, both cleanup queries were
        # silently dead code, and the DB grew unbounded despite the
        # poll thread calling cleanup every ~20 s. On weeks-long srv06
        # uptime the `stream_stats` history table exploded. Fix:
        # compute the cutoff in the same ISO shape Python writes
        # (with 'T' separator + '+00:00') so the string compare is
        # correct.
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=days)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM stream_stats
                    WHERE timestamp < ?
                """, (cutoff,))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"[STREAM DB] Cleaned up {deleted} old statistics records (cutoff={cutoff})")
                return deleted
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to cleanup old statistics: {e}")
            return 0

    def cleanup_old_stopped_streams(self, hours: int = 24) -> int:
        """
        Clean up old stopped streams from the database.

        Args:
            hours: Number of hours to keep stopped streams (default: 24 hours = 1 day)

        Returns:
            Number of streams deleted
        """
        # v0.5.398 (audit stream-db O1): same datetime format-compare
        # bug as cleanup_old_statistics above. `datetime('now', '-N
        # hours')` returned a space-separated string that compared
        # LESS than any ISO-formatted `stopped_at`/`updated_at`, so
        # this cleanup was dead code too — combined with the
        # exploding stream_stats table, srv06's DB grew forever until
        # /api/streams/stats polls degraded to O(N).
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=hours)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                # v0.5.266 (audit DB-F1): SQL operator-precedence bug —
                # AND binds tighter than OR, so the pre-fix WHERE
                # clause parsed as `(status='Stopped' AND stopped_at
                # IS NOT NULL AND stopped_at < X) OR (stopped_at IS
                # NULL AND updated_at < X)`. The second OR-branch
                # had NO status filter, so ANY stream — Running
                # streams whose stopped_at is naturally NULL — with
                # updated_at older than the window got silently
                # deleted. A stream whose collector paused for an
                # hour vanished from the DB while still on the wire.
                # Parenthesize both OR-branches under a single
                # status='Stopped' guard.
                cursor = conn.execute("""
                    DELETE FROM streams
                    WHERE status = 'Stopped'
                      AND (
                            (stopped_at IS NOT NULL AND stopped_at < ?)
                         OR (stopped_at IS NULL     AND updated_at < ?)
                          )
                """, (cutoff, cutoff))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"[STREAM DB] Cleaned up {deleted} old stopped stream(s) (older than {hours} hours, cutoff={cutoff})")
                return deleted
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to cleanup old stopped streams: {e}")
            return 0
    
    def delete_stream(self, stream_id: str) -> bool:
        """
        Delete a stream from the database.

        v0.5.375 (audit db-foreign-keys-off): sibling of the
        device_database.remove_route_pool / remove_dhcp_pool fix
        — pre-fix the bare `sqlite3.connect(self.db_path)` opened
        with FKs OFF, so any child rows in stream_stats keyed on
        stream_id became permanent orphans on delete. Now:
        `PRAGMA foreign_keys = ON` first so ON DELETE CASCADE
        actually fires.

        Args:
            stream_id: Stream identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # v0.5.375: enable FKs on this connection.
                try:
                    conn.execute("PRAGMA foreign_keys = ON")
                except Exception as _pragma_exc:
                    logger.warning(
                        f"[STREAM DB] PRAGMA foreign_keys ON failed: "
                        f"{_pragma_exc}"
                    )
                cursor = conn.execute("DELETE FROM streams WHERE stream_id = ?", (stream_id,))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"[STREAM DB] Deleted stream {stream_id}")
                return deleted > 0
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to delete stream {stream_id}: {e}")
            return False

