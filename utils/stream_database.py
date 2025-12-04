"""
Stream Database Management for OSTG
SQLite-based stream database for tracking active streams and statistics
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

class StreamDatabase:
    """SQLite-based stream database for OSTG"""
    
    def __init__(self, db_path: str = "/opt/OSTG/device_database.db"):
        """
        Initialize stream database (uses same DB file as device database).
        
        Args:
            db_path: Path to SQLite database file
        """
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
        try:
            config_json = json.dumps(stream_config) if stream_config else None
            now = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                # Check if stream already exists
                cursor = conn.execute("SELECT stream_id FROM streams WHERE stream_id = ?", (stream_id,))
                exists = cursor.fetchone()
                
                if exists:
                    # Check if stream was previously stopped - if so, reset last_update
                    cursor_status = conn.execute(
                        "SELECT status FROM streams WHERE stream_id = ?",
                        (stream_id,)
                    )
                    status_row = cursor_status.fetchone()
                    was_stopped = status_row and status_row[0] == 'Stopped'
                    
                    # Update existing stream
                    # If stream was stopped, reset last_update to current time for accurate rate calculation
                    if was_stopped:
                        conn.execute("""
                            UPDATE streams SET
                                stream_name = ?,
                                interface = ?,
                                rx_interface = ?,
                                server_url = ?,
                                tg_id = ?,
                                flow_tracking_enabled = ?,
                                status = ?,
                                started_at = ?,
                                updated_at = ?,
                                last_update = ?,
                                last_tx_count = 0,
                                last_rx_count = 0,
                                tx_count = 0,
                                rx_count = 0,
                                stream_config = ?
                            WHERE stream_id = ?
                        """, (
                            stream_name, interface, rx_interface, server_url, tg_id,
                            int(flow_tracking_enabled), 'Running', now, now, now, config_json, stream_id
                        ))
                    else:
                        # Stream is continuing - preserve last_update for rate calculation
                        conn.execute("""
                            UPDATE streams SET
                                stream_name = ?,
                                interface = ?,
                                rx_interface = ?,
                                server_url = ?,
                                tg_id = ?,
                                flow_tracking_enabled = ?,
                                status = ?,
                                updated_at = ?,
                                stream_config = ?
                            WHERE stream_id = ?
                        """, (
                            stream_name, interface, rx_interface, server_url, tg_id,
                            int(flow_tracking_enabled), 'Running', now, config_json, stream_id
                        ))
                else:
                    # Insert new stream with initial last_update
                    conn.execute("""
                        INSERT INTO streams (
                            stream_id, stream_name, interface, rx_interface, server_url, tg_id,
                            flow_tracking_enabled, status, started_at, updated_at, last_update, stream_config
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        stream_id, stream_name, interface, rx_interface, server_url, tg_id,
                        int(flow_tracking_enabled), 'Running', now, now, now, config_json
                    ))
                conn.commit()
            
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
                    
                    # Update stream statistics
                    conn.execute("""
                        UPDATE streams SET
                            tx_count = ?,
                            rx_count = ?,
                            tx_rate = ?,
                            rx_rate = ?,
                            last_tx_count = ?,
                            last_rx_count = ?,
                            last_update = ?,
                            updated_at = ?
                        WHERE stream_id = ?
                    """, (tx_count, rx_count, tx_rate, rx_rate, tx_count, rx_count, now, now, stream_id))
                    
                    # Insert into history table
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
            True if successful, False otherwise
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE streams SET
                        status = 'Stopped',
                        stopped_at = ?,
                        updated_at = ?
                    WHERE stream_id = ?
                """, (now, now, stream_id))
                conn.commit()
            
            logger.info(f"[STREAM DB] Marked stream {stream_id} as stopped")
            return True
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to stop stream {stream_id}: {e}")
            return False
    
    def get_all_streams(self, status: Optional[str] = None, tg_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all streams from the database.
        
        Args:
            status: Filter by status ('Running', 'Stopped', 'Error')
            tg_id: Filter by traffic generator ID
            
        Returns:
            List of stream dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                query = "SELECT * FROM streams WHERE 1=1"
                params = []
                
                if status:
                    query += " AND status = ?"
                    params.append(status)
                
                if tg_id is not None:
                    query += " AND tg_id = ?"
                    params.append(tg_id)
                
                query += " ORDER BY created_at DESC"
                
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
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM stream_stats
                    WHERE timestamp < datetime('now', '-' || ? || ' days')
                """, (days,))
                deleted = cursor.rowcount
                conn.commit()
                logger.info(f"[STREAM DB] Cleaned up {deleted} old statistics records")
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
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Delete stopped streams that haven't been updated in the specified time
                cursor = conn.execute("""
                    DELETE FROM streams
                    WHERE status = 'Stopped'
                    AND (stopped_at IS NOT NULL AND stopped_at < datetime('now', '-' || ? || ' hours'))
                    OR (stopped_at IS NULL AND updated_at < datetime('now', '-' || ? || ' hours'))
                """, (hours, hours))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"[STREAM DB] Cleaned up {deleted} old stopped stream(s) (older than {hours} hours)")
                return deleted
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to cleanup old stopped streams: {e}")
            return 0
    
    def delete_stream(self, stream_id: str) -> bool:
        """
        Delete a stream from the database.
        
        Args:
            stream_id: Stream identifier
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM streams WHERE stream_id = ?", (stream_id,))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"[STREAM DB] Deleted stream {stream_id}")
                return deleted > 0
        except Exception as e:
            logger.error(f"[STREAM DB] Failed to delete stream {stream_id}: {e}")
            return False

