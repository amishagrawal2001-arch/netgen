"""
Device Database Management for OSTG
SQLite-based device database with comprehensive device tracking
"""

import sqlite3
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

class DeviceDatabase:
    """SQLite-based device database for OSTG"""
    
    def __init__(self, db_path: str = "/opt/OSTG/device_database.db"):
        """
        Initialize device database.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.backup_path = f"{db_path}.backup"
        self.ensure_db_directory()
        self.init_database()
        logger.info(f"[DEVICE DB] Initialized database at {self.db_path}")
    
    def ensure_db_directory(self):
        """Ensure database directory exists with proper permissions."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        # Set proper permissions (readable/writable by ostg user)
        try:
            os.chmod(db_dir, 0o755)
        except Exception as e:
            logger.warning(f"[DEVICE DB] Could not set directory permissions: {e}")
    
    def init_database(self):
        """Initialize database with tables and indexes."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
            conn.execute("PRAGMA synchronous = NORMAL")  # Good balance of safety/speed
            
            # Create devices table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    server_interface TEXT,  -- Server interface where device is configured
                    vlan INTEGER DEFAULT 0,
                    ipv4_address TEXT,
                    ipv4_mask TEXT DEFAULT '24',
                    ipv6_address TEXT,
                    ipv6_mask TEXT DEFAULT '64',
                    ipv4_gateway TEXT,
                    ipv6_gateway TEXT,
                    mac_address TEXT,
                    protocols TEXT,  -- JSON array of protocols
                    bgp_config TEXT,  -- JSON object
                    ospf_config TEXT,  -- JSON object
                    isis_config TEXT,  -- JSON object
                    status TEXT DEFAULT 'Stopped',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_arp_check TIMESTAMP,
                    arp_status TEXT DEFAULT 'Unknown',
                    arp_ipv4_resolved BOOLEAN DEFAULT FALSE,
                    arp_ipv6_resolved BOOLEAN DEFAULT FALSE,
                    arp_gateway_resolved BOOLEAN DEFAULT FALSE,
                    bgp_established BOOLEAN DEFAULT FALSE,
                    bgp_ipv4_established BOOLEAN DEFAULT FALSE,
                    bgp_ipv6_established BOOLEAN DEFAULT FALSE,
                    bgp_ipv4_state TEXT DEFAULT 'Unknown',
                    bgp_ipv6_state TEXT DEFAULT 'Unknown',
                    last_bgp_check TIMESTAMP,
                    ospf_established BOOLEAN DEFAULT FALSE,
                    ospf_state TEXT DEFAULT 'Unknown',
                    ospf_neighbors TEXT,
                    last_ospf_check TIMESTAMP
                )
            """)
            
            # Create device statistics table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    arp_resolved BOOLEAN,
                    bgp_established BOOLEAN,
                    bgp_ipv4_established BOOLEAN,
                    bgp_ipv6_established BOOLEAN,
                    bgp_ipv4_state TEXT,
                    bgp_ipv6_state TEXT,
                    ospf_established BOOLEAN,
                    ospf_state TEXT,
                    ospf_neighbors TEXT,
                    ospf_adjacent BOOLEAN,
                    ping_success BOOLEAN,
                    arp_ipv4_resolved BOOLEAN,
                    arp_ipv6_resolved BOOLEAN,
                    arp_gateway_resolved BOOLEAN,
                    last_bgp_check TIMESTAMP,
                    last_ospf_check TIMESTAMP,
                    last_ping_check TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
                )
            """)
            
            # Create device events log table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,  -- JSON object
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
                )
            """)
            
            # Create route pools table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS route_pools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_name TEXT UNIQUE NOT NULL,
                    subnet TEXT NOT NULL,
                    address_family TEXT NOT NULL DEFAULT 'ipv4',
                    route_count INTEGER NOT NULL,
                    first_host_ip TEXT,
                    last_host_ip TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create device route pool attachments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_route_pools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    pool_name TEXT NOT NULL,
                    neighbor_ip TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE,
                    FOREIGN KEY (pool_name) REFERENCES route_pools(pool_name) ON DELETE CASCADE,
                    UNIQUE(device_id, pool_name, neighbor_ip)
                )
            """)
            
            # Create indexes for better performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_interface ON devices(interface)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_server_interface ON devices(server_interface)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_created ON devices(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_device_timestamp ON device_stats(device_id, timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_device_timestamp ON device_events(device_id, timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON device_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_route_pools_name ON route_pools(pool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_route_pools_device ON device_route_pools(device_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_route_pools_pool ON device_route_pools(pool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_route_pools_neighbor ON device_route_pools(neighbor_ip)")
            
            conn.commit()
            logger.info("[DEVICE DB] Database tables and indexes created successfully")
            
            # Run database migrations
            logger.info("[DEVICE DB] Starting database migrations")
            self._run_migrations(conn)
            logger.info("[DEVICE DB] Database migrations completed")
    
    def _run_migrations(self, conn):
        """Run database migrations to add new columns or modify schema."""
        try:
            logger.info("[DEVICE DB] Running database migrations")
            
            # Check if server_interface column exists
            cursor = conn.execute("PRAGMA table_info(devices)")
            columns = [column[1] for column in cursor.fetchall()]
            logger.info(f"[DEVICE DB] Current devices table columns: {columns}")
            
            if 'server_interface' not in columns:
                logger.info("[DEVICE DB] Adding server_interface column to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN server_interface TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added server_interface column")
            
            # Check if BGP IPv4/IPv6 columns exist in devices table
            if 'bgp_ipv4_established' not in columns:
                logger.info("[DEVICE DB] Adding BGP IPv4/IPv6 columns to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN bgp_ipv4_established BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN bgp_ipv6_established BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN bgp_ipv4_state TEXT DEFAULT 'Unknown'")
                conn.execute("ALTER TABLE devices ADD COLUMN bgp_ipv6_state TEXT DEFAULT 'Unknown'")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added BGP IPv4/IPv6 columns to devices table")
            
            # Check if OSPF status columns exist in devices table
            logger.info(f"[DEVICE DB] Checking for OSPF columns in devices table. Current columns: {columns}")
            if 'ospf_established' not in columns:
                logger.info("[DEVICE DB] Adding OSPF status columns to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_established BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_state TEXT DEFAULT 'Unknown'")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_neighbors TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN last_ospf_check TIMESTAMP")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added OSPF status columns to devices table")
            else:
                logger.info("[DEVICE DB] OSPF status columns already exist in devices table")
            
            # Check if BGP IPv4/IPv6 columns exist in device_stats table
            cursor = conn.execute("PRAGMA table_info(device_stats)")
            stats_columns = [column[1] for column in cursor.fetchall()]
            
            if 'bgp_ipv4_established' not in stats_columns:
                logger.info("[DEVICE DB] Adding BGP IPv4/IPv6 columns to device_stats table")
                conn.execute("ALTER TABLE device_stats ADD COLUMN bgp_ipv4_established BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN bgp_ipv6_established BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN bgp_ipv4_state TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN bgp_ipv6_state TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added BGP IPv4/IPv6 columns to device_stats table")
            
            # Check if OSPF status columns exist in device_stats table
            logger.info(f"[DEVICE DB] Checking for OSPF columns in device_stats table. Current columns: {stats_columns}")
            if 'ospf_established' not in stats_columns:
                logger.info("[DEVICE DB] Adding OSPF status columns to device_stats table")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_established BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_state TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_neighbors TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN last_ospf_check TIMESTAMP")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added OSPF status columns to device_stats table")
            else:
                logger.info("[DEVICE DB] OSPF status columns already exist in device_stats table")
            
            # Check if OSPF IPv4/IPv6 specific columns exist in devices table
            if 'ospf_ipv4_running' not in columns:
                logger.info("[DEVICE DB] Adding OSPF IPv4/IPv6 specific columns to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv4_running BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv6_running BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv4_established BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv6_established BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv4_uptime TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv6_uptime TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added OSPF IPv4/IPv6 specific columns to devices table")
            else:
                logger.info("[DEVICE DB] OSPF IPv4/IPv6 specific columns already exist in devices table")
                
            # Check if OSPF uptime columns exist in devices table (separate check)
            if 'ospf_ipv4_uptime' not in columns:
                logger.info("[DEVICE DB] Adding OSPF uptime columns to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv4_uptime TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN ospf_ipv6_uptime TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added OSPF uptime columns to devices table")
            else:
                logger.info("[DEVICE DB] OSPF uptime columns already exist in devices table")

            # Check if OSPF IPv4/IPv6 specific columns exist in device_stats table
            if 'ospf_ipv4_running' not in stats_columns:
                logger.info("[DEVICE DB] Adding OSPF IPv4/IPv6 specific columns to device_stats table")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv4_running BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv6_running BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv4_established BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv6_established BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv4_uptime TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv6_uptime TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added OSPF IPv4/IPv6 specific columns to device_stats table")
            else:
                logger.info("[DEVICE DB] OSPF IPv4/IPv6 specific columns already exist in device_stats table")
                
            # Check if OSPF uptime columns exist in device_stats table (separate check)
            if 'ospf_ipv4_uptime' not in stats_columns:
                logger.info("[DEVICE DB] Adding OSPF uptime columns to device_stats table")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv4_uptime TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN ospf_ipv6_uptime TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added OSPF uptime columns to device_stats table")
            else:
                logger.info("[DEVICE DB] OSPF uptime columns already exist in device_stats table")
            
            # Check if isis_config column exists in devices table
            if 'isis_config' not in columns:
                logger.info("[DEVICE DB] Adding isis_config column to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_config TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added isis_config column to devices table")
            
            # Check if ISIS status columns exist in devices table
            if 'isis_running' not in columns:
                logger.info("[DEVICE DB] Adding ISIS status columns to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_running BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_established BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_state TEXT DEFAULT 'Unknown'")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_neighbors TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_areas TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_system_id TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_net TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_uptime TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN last_isis_check TIMESTAMP")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_manual_override BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN isis_manual_override_time TIMESTAMP")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added ISIS status columns to devices table")
            else:
                logger.info("[DEVICE DB] ISIS status columns already exist in devices table")
            
            # Check if ISIS status columns exist in device_stats table
            if 'isis_running' not in stats_columns:
                logger.info("[DEVICE DB] Adding ISIS status columns to device_stats table")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_running BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_established BOOLEAN")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_state TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_neighbors TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_areas TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_system_id TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_net TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN isis_uptime TEXT")
                conn.execute("ALTER TABLE device_stats ADD COLUMN last_isis_check TIMESTAMP")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added ISIS status columns to device_stats table")
            else:
                logger.info("[DEVICE DB] ISIS status columns already exist in device_stats table")
            
            # Check if address_family column exists in route_pools table
            cursor = conn.execute("PRAGMA table_info(route_pools)")
            route_pool_columns = [column[1] for column in cursor.fetchall()]
            
            if 'address_family' not in route_pool_columns:
                logger.info("[DEVICE DB] Adding address_family column to route_pools table")
                conn.execute("ALTER TABLE route_pools ADD COLUMN address_family TEXT NOT NULL DEFAULT 'ipv4'")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added address_family column to route_pools table")
            
            # Check if increment_type column exists in route_pools table
            if 'increment_type' not in route_pool_columns:
                logger.info("[DEVICE DB] Adding increment_type column to route_pools table")
                conn.execute("ALTER TABLE route_pools ADD COLUMN increment_type TEXT NOT NULL DEFAULT 'host'")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added increment_type column to route_pools table")
            
        except Exception as e:
            logger.error(f"[DEVICE DB] Migration failed: {e}")
            # Don't raise the exception to avoid breaking the database initialization
    
    def add_device(self, device_data: Dict[str, Any]) -> bool:
        """
        Add a new device to the database.
        
        Args:
            device_data: Dictionary containing device information
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            device_id = device_data.get("device_id")
            if not device_id:
                logger.error("[DEVICE DB] Cannot add device without device_id")
                return False
            
            with sqlite3.connect(self.db_path) as conn:
                # Check if device already exists
                cursor = conn.execute("SELECT device_id FROM devices WHERE device_id = ?", (device_id,))
                if cursor.fetchone():
                    logger.warning(f"[DEVICE DB] Device {device_id} already exists, updating instead")
                    return self.update_device(device_id, device_data)
                
                # Prepare device data
                device_info = {
                    'device_id': device_id,
                    'device_name': device_data.get("device_name", f"device_{device_id}"),
                    'interface': device_data.get("interface", ""),
                    'server_interface': device_data.get("server_interface", ""),
                    'vlan': device_data.get("vlan", 0),
                    'ipv4_address': device_data.get("ipv4_address", ""),
                    'ipv4_mask': device_data.get("ipv4_mask", "24"),
                    'ipv6_address': device_data.get("ipv6_address", ""),
                    'ipv6_mask': device_data.get("ipv6_mask", "64"),
                    'ipv4_gateway': device_data.get("ipv4_gateway", ""),
                    'ipv6_gateway': device_data.get("ipv6_gateway", ""),
                    'mac_address': device_data.get("mac_address", ""),
                    'protocols': json.dumps(device_data.get("protocols", [])),
                    'bgp_config': json.dumps(device_data.get("bgp_config", {})),
                    'ospf_config': json.dumps(device_data.get("ospf_config", {})),
                    'isis_config': json.dumps(device_data.get("isis_config", {})),
                    'status': device_data.get("status", "Stopped"),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                
                # Insert device
                conn.execute("""
                    INSERT INTO devices (
                        device_id, device_name, interface, server_interface, vlan, ipv4_address, ipv4_mask,
                        ipv6_address, ipv6_mask, ipv4_gateway, ipv6_gateway, mac_address,
                        protocols, bgp_config, ospf_config, isis_config, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, tuple(device_info.values()))
                
                conn.commit()
                logger.info(f"[DEVICE DB] Successfully added device {device_id}")
                
                # Log device creation event
                self.log_device_event(device_id, "created", device_data)
                
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to add device {device_id}: {e}")
            return False
    
    def update_device(self, device_id: str, device_data: Dict[str, Any]) -> bool:
        """
        Update an existing device in the database.
        
        Args:
            device_id: Device ID to update
            device_data: Dictionary containing updated device information
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check if device exists
                cursor = conn.execute("SELECT device_id FROM devices WHERE device_id = ?", (device_id,))
                if not cursor.fetchone():
                    logger.warning(f"[DEVICE DB] Device {device_id} not found for update, creating new")
                    return self.add_device(device_data)
                
                # Prepare update data
                update_fields = []
                update_values = []
                
                # Map device_data to database fields
                field_mapping = {
                    'device_name': 'device_name',
                    'interface': 'interface',
                    'server_interface': 'server_interface',
                    'vlan': 'vlan',
                    'ipv4_address': 'ipv4_address',
                    'ipv4_mask': 'ipv4_mask',
                    'ipv6_address': 'ipv6_address',
                    'ipv6_mask': 'ipv6_mask',
                    'ipv4_gateway': 'ipv4_gateway',
                    'ipv6_gateway': 'ipv6_gateway',
                    'mac_address': 'mac_address',
                    'protocols': 'protocols',
                    'bgp_config': 'bgp_config',
                    'ospf_config': 'ospf_config',
                    'isis_config': 'isis_config',
                    'status': 'status',
                    'bgp_established': 'bgp_established',
                    'bgp_ipv4_established': 'bgp_ipv4_established',
                    'bgp_ipv6_established': 'bgp_ipv6_established',
                    'bgp_ipv4_state': 'bgp_ipv4_state',
                    'bgp_ipv6_state': 'bgp_ipv6_state',
                    'last_bgp_check': 'last_bgp_check',
                    'arp_ipv4_resolved': 'arp_ipv4_resolved',
                    'arp_ipv6_resolved': 'arp_ipv6_resolved',
                    'arp_gateway_resolved': 'arp_gateway_resolved',
                    'arp_status': 'arp_status',
                    'last_arp_check': 'last_arp_check',
                    'ospf_established': 'ospf_established',
                    'ospf_state': 'ospf_state',
                    'ospf_neighbors': 'ospf_neighbors',
                    'last_ospf_check': 'last_ospf_check',
                    'ospf_ipv4_running': 'ospf_ipv4_running',
                    'ospf_ipv6_running': 'ospf_ipv6_running',
                    'ospf_ipv4_established': 'ospf_ipv4_established',
                    'ospf_ipv6_established': 'ospf_ipv6_established',
                    'ospf_ipv4_uptime': 'ospf_ipv4_uptime',
                    'ospf_ipv6_uptime': 'ospf_ipv6_uptime',
                    'isis_running': 'isis_running',
                    'isis_established': 'isis_established',
                    'isis_state': 'isis_state',
                    'isis_neighbors': 'isis_neighbors',
                    'isis_areas': 'isis_areas',
                    'isis_system_id': 'isis_system_id',
                    'isis_net': 'isis_net',
                    'isis_uptime': 'isis_uptime',
                    'last_isis_check': 'last_isis_check',
                    'isis_manual_override': 'isis_manual_override',
                    'isis_manual_override_time': 'isis_manual_override_time'
                }
                
                for key, db_field in field_mapping.items():
                    if key in device_data:
                        if key in ['protocols', 'bgp_config', 'ospf_config', 'isis_config']:
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(json.dumps(device_data[key]))
                        else:
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(device_data[key])
                
                if not update_fields:
                    logger.warning(f"[DEVICE DB] No fields to update for device {device_id}")
                    return True
                
                # Add updated_at timestamp
                update_fields.append("updated_at = ?")
                update_values.append(datetime.now(timezone.utc).isoformat())
                update_values.append(device_id)
                
                # Execute update
                query = f"UPDATE devices SET {', '.join(update_fields)} WHERE device_id = ?"
                conn.execute(query, update_values)
                conn.commit()
                
                logger.info(f"[DEVICE DB] Successfully updated device {device_id}")
                
                # Log device update event
                self.log_device_event(device_id, "updated", device_data)
                
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update device {device_id}: {e}")
            return False
    
    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a device by ID.
        
        Args:
            device_id: Device ID to retrieve
            
        Returns:
            Dict containing device information or None if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
                row = cursor.fetchone()
                
                if row:
                    device = dict(row)
                    # Parse JSON fields
                    device['protocols'] = json.loads(device['protocols'] or '[]')
                    device['bgp_config'] = json.loads(device['bgp_config'] or '{}')
                    device['ospf_config'] = json.loads(device['ospf_config'] or '{}')
                    # Parse isis_config, handling double-encoded JSON if present
                    isis_config_raw = device.get('isis_config') or '{}'
                    try:
                        isis_config = json.loads(isis_config_raw)
                        # If still a string after parsing, it was double-encoded
                        if isinstance(isis_config, str):
                            isis_config = json.loads(isis_config)
                        device['isis_config'] = isis_config
                    except (json.JSONDecodeError, TypeError):
                        device['isis_config'] = {}
                    return device
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get device {device_id}: {e}")
            return None
    
    def get_all_devices(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all devices, optionally filtered by status.
        
        Args:
            status_filter: Optional status to filter by
            
        Returns:
            List of device dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                if status_filter:
                    cursor = conn.execute("SELECT * FROM devices WHERE status = ? ORDER BY created_at DESC", (status_filter,))
                else:
                    cursor = conn.execute("SELECT * FROM devices ORDER BY created_at DESC")
                
                devices = []
                for row in cursor.fetchall():
                    device = dict(row)
                    # Parse JSON fields
                    device['protocols'] = json.loads(device['protocols'] or '[]')
                    device['bgp_config'] = json.loads(device['bgp_config'] or '{}')
                    device['ospf_config'] = json.loads(device['ospf_config'] or '{}')
                    # Parse isis_config, handling double-encoded JSON if present
                    isis_config_raw = device.get('isis_config') or '{}'
                    try:
                        isis_config = json.loads(isis_config_raw)
                        # If still a string after parsing, it was double-encoded
                        if isinstance(isis_config, str):
                            isis_config = json.loads(isis_config)
                        device['isis_config'] = isis_config
                    except (json.JSONDecodeError, TypeError):
                        device['isis_config'] = {}
                    devices.append(device)
                
                return devices
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get all devices: {e}")
            return []
    
    def get_devices_by_interface(self, interface: str, include_vlans: bool = True) -> List[Dict[str, Any]]:
        """
        Get all devices associated with an interface.
        
        Args:
            interface: Base interface name (e.g., "ens4np0")
            include_vlans: If True, also match devices on VLAN interfaces of this base interface
            
        Returns:
            List of device dictionaries matching the interface
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Build query to match devices
                # Match if device interface equals the base interface
                # Or if device is on a VLAN of this interface (vlanXX@interface or vlanXX where parent is interface)
                conditions = ["interface = ?", "server_interface LIKE ?"]
                params = [interface, f"%{interface}%"]
                
                if include_vlans:
                    # Also match VLAN interfaces: vlanXX@interface format
                    conditions.append("interface LIKE ?")
                    params.append(f"vlan%@{interface}")
                
                query = f"SELECT * FROM devices WHERE {' OR '.join(conditions)} ORDER BY created_at DESC"
                logger.info(f"[DEVICE DB] get_devices_by_interface: interface={interface}, include_vlans={include_vlans}")
                logger.info(f"[DEVICE DB] Query: {query}")
                logger.info(f"[DEVICE DB] Params: {params}")
                cursor = conn.execute(query, params)
                
                devices = []
                for row in cursor.fetchall():
                    device = dict(row)
                    # Parse JSON fields
                    device['protocols'] = json.loads(device['protocols'] or '[]')
                    device['bgp_config'] = json.loads(device['bgp_config'] or '{}')
                    device['ospf_config'] = json.loads(device['ospf_config'] or '{}')
                    # Parse isis_config, handling double-encoded JSON if present
                    isis_config_raw = device.get('isis_config') or '{}'
                    try:
                        isis_config = json.loads(isis_config_raw)
                        if isinstance(isis_config, str):
                            isis_config = json.loads(isis_config)
                        device['isis_config'] = isis_config
                    except (json.JSONDecodeError, TypeError):
                        device['isis_config'] = {}
                    devices.append(device)
                
                return devices
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get devices by interface {interface}: {e}")
            return []
    
    def remove_device(self, device_id: str) -> bool:
        """
        Remove a device from the database.
        
        Args:
            device_id: Device ID to remove
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                # Enable foreign keys for cascade deletion (must be set for each connection)
                conn.execute("PRAGMA foreign_keys = ON")
                
                # Verify foreign keys are enabled
                cursor = conn.execute("PRAGMA foreign_keys")
                fk_enabled = cursor.fetchone()[0]
                if not fk_enabled:
                    logger.warning(f"[DEVICE DB] Foreign keys not enabled for device removal {device_id}, enabling manually")
                    conn.execute("PRAGMA foreign_keys = ON")
                
                # Check if device exists
                cursor = conn.execute("SELECT device_id, device_name FROM devices WHERE device_id = ?", (device_id,))
                device_row = cursor.fetchone()
                if not device_row:
                    logger.warning(f"[DEVICE DB] Device {device_id} not found for removal")
                    conn.close()
                    return True  # Consider it successful if already removed
                
                device_name = device_row[1] if len(device_row) > 1 else "Unknown"
                logger.info(f"[DEVICE DB] Removing device {device_id} ({device_name}) from database")
                
                # Log removal event before deleting
                try:
                    self.log_device_event(device_id, "removed", {"device_id": device_id, "device_name": device_name})
                except Exception as log_error:
                    logger.warning(f"[DEVICE DB] Failed to log removal event: {log_error}")
                    # Continue with removal even if logging fails
                
                # Delete device (cascade will handle related records)
                cursor = conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
                rows_deleted = cursor.rowcount
                
                if rows_deleted == 0:
                    logger.warning(f"[DEVICE DB] No rows deleted for device {device_id}")
                    conn.rollback()
                    conn.close()
                    return False
                
                # Explicitly commit the transaction
                conn.commit()
                
                # Verify deletion
                cursor = conn.execute("SELECT device_id FROM devices WHERE device_id = ?", (device_id,))
                if cursor.fetchone():
                    logger.error(f"[DEVICE DB] Device {device_id} still exists after deletion attempt!")
                    conn.rollback()
                    conn.close()
                    return False
                
                logger.info(f"[DEVICE DB] Successfully removed device {device_id} ({device_name}) - {rows_deleted} row(s) deleted")
                conn.close()
                return True
                
            except Exception as e:
                conn.rollback()
                conn.close()
                raise e
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to remove device {device_id}: {e}")
            import traceback
            logger.error(f"[DEVICE DB] Traceback: {traceback.format_exc()}")
            return False
    
    def update_device_status(self, device_id: str, status: str) -> bool:
        """
        Update device status.
        
        Args:
            device_id: Device ID
            status: New status (Running, Stopped, etc.)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE devices 
                    SET status = ?, updated_at = ? 
                    WHERE device_id = ?
                """, (status, datetime.now(timezone.utc).isoformat(), device_id))
                conn.commit()
                
                logger.info(f"[DEVICE DB] Updated device {device_id} status to {status}")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update device {device_id} status: {e}")
            return False
    
    def update_arp_status(self, device_id: str, arp_results: Dict[str, Any]) -> bool:
        """
        Update device ARP status.
        
        Args:
            device_id: Device ID
            arp_results: Dictionary containing ARP resolution results
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE devices 
                    SET arp_status = ?, 
                        arp_ipv4_resolved = ?, 
                        arp_ipv6_resolved = ?, 
                        arp_gateway_resolved = ?,
                        last_arp_check = ?,
                        updated_at = ?
                    WHERE device_id = ?
                """, (
                    arp_results.get('overall_status', 'Unknown'),
                    arp_results.get('ipv4_resolved', False),
                    arp_results.get('ipv6_resolved', False),
                    arp_results.get('gateway_resolved', False),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    device_id
                ))
                conn.commit()
                
                # Log ARP check event
                self.log_device_event(device_id, "arp_check", arp_results)
                
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update ARP status for device {device_id}: {e}")
            return False
    
    def update_bgp_status(self, device_id: str, bgp_results: Dict[str, Any]) -> bool:
        """
        Update device BGP status.
        
        Args:
            device_id: Device ID
            bgp_results: Dictionary containing BGP status results
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE devices 
                    SET bgp_established = ?, 
                        last_bgp_check = ?,
                        updated_at = ?
                    WHERE device_id = ?
                """, (
                    bgp_results.get('bgp_established', False),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    device_id
                ))
                conn.commit()
                
                # Log BGP status event
                self.log_device_event(device_id, "bgp_status_check", bgp_results)
                
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update BGP status for device {device_id}: {e}")
            return False
    
    def update_device_statistics(self, device_id: str, stats_data: Dict[str, Any]) -> bool:
        """
        Update device statistics.
        
        Args:
            device_id: Device ID
            stats_data: Dictionary containing statistics data
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check if statistics record exists
                cursor = conn.execute("SELECT device_id FROM device_stats WHERE device_id = ?", (device_id,))
                if cursor.fetchone():
                    # Update existing record (now also includes ISIS fields)
                    conn.execute("""
                        UPDATE device_stats 
                        SET arp_resolved = ?, 
                            bgp_established = ?, 
                            bgp_ipv4_established = ?,
                            bgp_ipv6_established = ?,
                            bgp_ipv4_state = ?,
                            bgp_ipv6_state = ?,
                            ospf_established = ?,
                            ospf_state = ?,
                            ospf_neighbors = ?,
                            ospf_adjacent = ?, 
                            ping_success = ?,
                            arp_ipv4_resolved = ?,
                            arp_ipv6_resolved = ?,
                            arp_gateway_resolved = ?,
                            last_bgp_check = ?,
                            last_ospf_check = ?,
                            last_ping_check = ?,
                            isis_running = ?,
                            isis_established = ?,
                            isis_state = ?,
                            isis_neighbors = ?,
                            isis_areas = ?,
                            isis_system_id = ?,
                            isis_net = ?,
                            isis_uptime = ?,
                            last_isis_check = ?,
                            timestamp = ?
                        WHERE device_id = ?
                    """, (
                        stats_data.get('arp_resolved', False),
                        stats_data.get('bgp_established', False),
                        stats_data.get('bgp_ipv4_established', False),
                        stats_data.get('bgp_ipv6_established', False),
                        stats_data.get('bgp_ipv4_state', 'Unknown'),
                        stats_data.get('bgp_ipv6_state', 'Unknown'),
                        stats_data.get('ospf_established', False),
                        stats_data.get('ospf_state', 'Unknown'),
                        stats_data.get('ospf_neighbors'),
                        stats_data.get('ospf_adjacent', False),
                        stats_data.get('ping_success', False),
                        stats_data.get('arp_ipv4_resolved', False),
                        stats_data.get('arp_ipv6_resolved', False),
                        stats_data.get('arp_gateway_resolved', False),
                        stats_data.get('last_bgp_check'),
                        stats_data.get('last_ospf_check'),
                        stats_data.get('last_ping_check'),
                        # ISIS fields
                        stats_data.get('isis_running'),
                        stats_data.get('isis_established'),
                        stats_data.get('isis_state'),
                        stats_data.get('isis_neighbors'),
                        stats_data.get('isis_areas'),
                        stats_data.get('isis_system_id'),
                        stats_data.get('isis_net'),
                        stats_data.get('isis_uptime'),
                        stats_data.get('last_isis_check'),
                        datetime.now(timezone.utc).isoformat(),
                        device_id
                    ))
                else:
                    # Insert new record (now also includes ISIS fields)
                    conn.execute("""
                        INSERT INTO device_stats (
                            device_id, arp_resolved, bgp_established, bgp_ipv4_established, bgp_ipv6_established, 
                            bgp_ipv4_state, bgp_ipv6_state, ospf_established, ospf_state, ospf_neighbors, ospf_adjacent, ping_success,
                            arp_ipv4_resolved, arp_ipv6_resolved, arp_gateway_resolved,
                            last_bgp_check, last_ospf_check, last_ping_check,
                            isis_running, isis_established, isis_state, isis_neighbors, isis_areas, isis_system_id, isis_net, isis_uptime, last_isis_check,
                            timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        device_id,
                        stats_data.get('arp_resolved', False),
                        stats_data.get('bgp_established', False),
                        stats_data.get('bgp_ipv4_established', False),
                        stats_data.get('bgp_ipv6_established', False),
                        stats_data.get('bgp_ipv4_state', 'Unknown'),
                        stats_data.get('bgp_ipv6_state', 'Unknown'),
                        stats_data.get('ospf_established', False),
                        stats_data.get('ospf_state', 'Unknown'),
                        stats_data.get('ospf_neighbors'),
                        stats_data.get('ospf_adjacent', False),
                        stats_data.get('ping_success', False),
                        stats_data.get('arp_ipv4_resolved', False),
                        stats_data.get('arp_ipv6_resolved', False),
                        stats_data.get('arp_gateway_resolved', False),
                        stats_data.get('last_bgp_check'),
                        stats_data.get('last_ospf_check'),
                        stats_data.get('last_ping_check'),
                        # ISIS fields
                        stats_data.get('isis_running'),
                        stats_data.get('isis_established'),
                        stats_data.get('isis_state'),
                        stats_data.get('isis_neighbors'),
                        stats_data.get('isis_areas'),
                        stats_data.get('isis_system_id'),
                        stats_data.get('isis_net'),
                        stats_data.get('isis_uptime'),
                        stats_data.get('last_isis_check'),
                        datetime.now(timezone.utc).isoformat()
                    ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update device statistics for device {device_id}: {e}")
            return False
    
    def log_device_event(self, device_id: str, event_type: str, event_data: Dict[str, Any]) -> bool:
        """
        Log a device event.
        
        Args:
            device_id: Device ID
            event_type: Type of event (created, updated, removed, arp_check, etc.)
            event_data: Event data dictionary
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO device_events (device_id, event_type, event_data, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (device_id, event_type, json.dumps(event_data), datetime.now(timezone.utc).isoformat()))
                conn.commit()
                
                logger.debug(f"[DEVICE DB] Logged event {event_type} for device {device_id}")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to log event for device {device_id}: {e}")
            return False
    
    def get_device_events(self, device_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get device events.
        
        Args:
            device_id: Device ID
            limit: Maximum number of events to return
            
        Returns:
            List of event dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM device_events 
                    WHERE device_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (device_id, limit))
                
                events = []
                for row in cursor.fetchall():
                    event = dict(row)
                    event['event_data'] = json.loads(event['event_data'] or '{}')
                    events.append(event)
                
                return events
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get events for device {device_id}: {e}")
            return []
    
    def get_device_statistics(self, device_id: str, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get device statistics for the last N hours.
        
        Args:
            device_id: Device ID
            hours: Number of hours to look back
            
        Returns:
            List of statistics dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM device_stats 
                    WHERE device_id = ? 
                    AND timestamp >= datetime('now', '-{} hours')
                    ORDER BY timestamp DESC
                """.format(hours), (device_id,))
                
                stats = []
                for row in cursor.fetchall():
                    stats.append(dict(row))
                
                return stats
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get statistics for device {device_id}: {e}")
            return []
    
    def backup_database(self) -> bool:
        """
        Create a backup of the database.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if os.path.exists(self.db_path):
                shutil.copy2(self.db_path, self.backup_path)
                logger.info(f"[DEVICE DB] Database backed up to {self.backup_path}")
                return True
            else:
                logger.warning("[DEVICE DB] Database file does not exist for backup")
                return False
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to backup database: {e}")
            return False
    
    def restore_database(self) -> bool:
        """
        Restore database from backup.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if os.path.exists(self.backup_path):
                shutil.copy2(self.backup_path, self.db_path)
                logger.info(f"[DEVICE DB] Database restored from {self.backup_path}")
                return True
            else:
                logger.warning("[DEVICE DB] Backup file does not exist for restore")
                return False
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to restore database: {e}")
            return False
    
    def get_database_info(self) -> Dict[str, Any]:
        """
        Get database information and statistics.
        
        Returns:
            Dictionary containing database information
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Get device count by status
                cursor = conn.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM devices 
                    GROUP BY status
                """)
                status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
                
                # Get total device count
                cursor = conn.execute("SELECT COUNT(*) as total FROM devices")
                total_devices = cursor.fetchone()['total']
                
                # Get recent events count
                cursor = conn.execute("""
                    SELECT COUNT(*) as count 
                    FROM device_events 
                    WHERE timestamp >= datetime('now', '-24 hours')
                """)
                recent_events = cursor.fetchone()['count']
                
                # Get database file size
                db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
                
                return {
                    'total_devices': total_devices,
                    'status_counts': status_counts,
                    'recent_events_24h': recent_events,
                    'database_size_bytes': db_size,
                    'database_path': self.db_path,
                    'backup_path': self.backup_path,
                    'backup_exists': os.path.exists(self.backup_path)
                }
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get database info: {e}")
            return {}
    
    def cleanup_old_data(self, days: int = 30) -> bool:
        """
        Clean up old statistics and events data.
        
        Args:
            days: Number of days to keep data
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Clean up old statistics
                cursor = conn.execute("""
                    DELETE FROM device_stats 
                    WHERE timestamp < datetime('now', '-{} days')
                """.format(days))
                stats_deleted = cursor.rowcount
                
                # Clean up old events (keep more recent events)
                cursor = conn.execute("""
                    DELETE FROM device_events 
                    WHERE timestamp < datetime('now', '-{} days')
                """.format(days))
                events_deleted = cursor.rowcount
                
                conn.commit()
                
                logger.info(f"[DEVICE DB] Cleaned up {stats_deleted} old statistics and {events_deleted} old events")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to cleanup old data: {e}")
            return False
    
    # BGP Route Pool Management Methods
    
    def add_route_pool(self, pool_data: Dict[str, Any]) -> bool:
        """
        Add a new BGP route pool to the database.
        
        Args:
            pool_data: Dictionary containing pool information
                - name: Pool name (required)
                - subnet: Network subnet (required)
                - route_count: Number of routes to generate (required)
                - first_host_ip: First host IP (optional)
                - last_host_ip: Last host IP (optional)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            pool_name = pool_data.get("name")
            if not pool_name:
                logger.error("[DEVICE DB] Cannot add route pool without name")
                return False
            
            with sqlite3.connect(self.db_path) as conn:
                # Check if pool already exists
                cursor = conn.execute("SELECT id FROM route_pools WHERE pool_name = ?", (pool_name,))
                if cursor.fetchone():
                    logger.warning(f"[DEVICE DB] Route pool '{pool_name}' already exists, updating instead")
                    return self.update_route_pool(pool_name, pool_data)
                
                # Determine address family from subnet
                subnet = pool_data.get("subnet", "")
                address_family = self._detect_address_family(subnet)
                
                # Prepare pool data
                pool_info = {
                    'pool_name': pool_name,
                    'subnet': subnet,
                    'address_family': address_family,
                    'route_count': pool_data.get("route_count", 1),
                    'first_host_ip': pool_data.get("first_host_ip", ""),
                    'last_host_ip': pool_data.get("last_host_ip", ""),
                    'increment_type': pool_data.get("increment_type", "host"),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                
                # Insert pool
                conn.execute("""
                    INSERT INTO route_pools (
                        pool_name, subnet, address_family, route_count, first_host_ip, last_host_ip, increment_type, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, tuple(pool_info.values()))
                
                conn.commit()
                logger.info(f"[DEVICE DB] Successfully added route pool '{pool_name}'")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to add route pool '{pool_name}': {e}")
            return False
    
    def update_route_pool(self, pool_name: str, pool_data: Dict[str, Any]) -> bool:
        """
        Update an existing BGP route pool in the database.
        
        Args:
            pool_name: Pool name to update
            pool_data: Dictionary containing updated pool information
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check if pool exists
                cursor = conn.execute("SELECT id FROM route_pools WHERE pool_name = ?", (pool_name,))
                if not cursor.fetchone():
                    logger.warning(f"[DEVICE DB] Route pool '{pool_name}' not found for update, creating new")
                    return self.add_route_pool(pool_data)
                
                # Prepare update data
                update_fields = []
                update_values = []
                
                # Map pool_data to database fields
                field_mapping = {
                    'subnet': 'subnet',
                    'route_count': 'route_count',
                    'first_host_ip': 'first_host_ip',
                    'last_host_ip': 'last_host_ip',
                    'increment_type': 'increment_type'
                }
                
                # If subnet is being updated, also update address_family
                if 'subnet' in pool_data:
                    subnet = pool_data['subnet']
                    address_family = self._detect_address_family(subnet)
                    update_fields.append("address_family = ?")
                    update_values.append(address_family)
                
                for key, db_field in field_mapping.items():
                    if key in pool_data:
                        update_fields.append(f"{db_field} = ?")
                        update_values.append(pool_data[key])
                
                if not update_fields:
                    logger.warning(f"[DEVICE DB] No fields to update for route pool '{pool_name}'")
                    return True
                
                # Add updated_at timestamp
                update_fields.append("updated_at = ?")
                update_values.append(datetime.now(timezone.utc).isoformat())
                update_values.append(pool_name)
                
                # Execute update
                query = f"UPDATE route_pools SET {', '.join(update_fields)} WHERE pool_name = ?"
                conn.execute(query, update_values)
                conn.commit()
                
                logger.info(f"[DEVICE DB] Successfully updated route pool '{pool_name}'")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update route pool '{pool_name}': {e}")
            return False
    
    def get_route_pool(self, pool_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a route pool by name.
        
        Args:
            pool_name: Pool name to retrieve
            
        Returns:
            Dict containing pool information or None if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM route_pools WHERE pool_name = ?", (pool_name,))
                row = cursor.fetchone()
                
                if row:
                    return dict(row)
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get route pool '{pool_name}': {e}")
            return None
    
    def get_all_route_pools(self) -> List[Dict[str, Any]]:
        """
        Get all BGP route pools.
        
        Returns:
            List of pool dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM route_pools ORDER BY created_at DESC")
                
                pools = []
                for row in cursor.fetchall():
                    pools.append(dict(row))
                
                return pools
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get all route pools: {e}")
            return []
    
    def remove_route_pool(self, pool_name: str) -> bool:
        """
        Remove a BGP route pool from the database.
        
        Args:
            pool_name: Pool name to remove
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check if pool exists
                cursor = conn.execute("SELECT id FROM route_pools WHERE pool_name = ?", (pool_name,))
                if not cursor.fetchone():
                    logger.warning(f"[DEVICE DB] Route pool '{pool_name}' not found for removal")
                    return True  # Consider it successful if already removed
                
                # Remove pool
                conn.execute("DELETE FROM route_pools WHERE pool_name = ?", (pool_name,))
                conn.commit()
                
                logger.info(f"[DEVICE DB] Successfully removed route pool '{pool_name}'")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to remove route pool '{pool_name}': {e}")
            return False
    
    def save_route_pools_batch(self, pools_data: List[Dict[str, Any]]) -> bool:
        """
        Save multiple route pools in a batch operation.
        
        Args:
            pools_data: List of pool dictionaries
            
        Returns:
            bool: True if all successful, False otherwise
        """
        try:
            success_count = 0
            for pool_data in pools_data:
                if self.add_route_pool(pool_data):
                    success_count += 1
                else:
                    logger.error(f"[DEVICE DB] Failed to save pool: {pool_data.get('name', 'unknown')}")
            
            logger.info(f"[DEVICE DB] Batch save completed: {success_count}/{len(pools_data)} pools saved")
            return success_count == len(pools_data)
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to save route pools batch: {e}")
            return False
    
    # Device-Pool Relationship Management Methods
    
    def attach_route_pools_to_device(self, device_id: str, neighbor_ip: str, pool_names: List[str]) -> bool:
        """
        Attach route pools to a device for a specific neighbor.
        
        Args:
            device_id: Device ID
            neighbor_ip: BGP neighbor IP address
            pool_names: List of pool names to attach
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # First, remove existing attachments for this device and neighbor
                conn.execute("""
                    DELETE FROM device_route_pools 
                    WHERE device_id = ? AND neighbor_ip = ?
                """, (device_id, neighbor_ip))
                
                # Add new attachments
                for pool_name in pool_names:
                    conn.execute("""
                        INSERT INTO device_route_pools (device_id, pool_name, neighbor_ip, created_at)
                        VALUES (?, ?, ?, ?)
                    """, (device_id, pool_name, neighbor_ip, datetime.now(timezone.utc).isoformat()))
                
                conn.commit()
                logger.info(f"[DEVICE DB] Attached {len(pool_names)} route pools to device {device_id} for neighbor {neighbor_ip}")
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to attach route pools to device {device_id}: {e}")
            return False
    
    def get_device_route_pools(self, device_id: str) -> Dict[str, List[str]]:
        """
        Get route pools attached to a device, grouped by neighbor IP.
        
        Args:
            device_id: Device ID
            
        Returns:
            Dictionary mapping neighbor IPs to lists of pool names
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT neighbor_ip, pool_name 
                    FROM device_route_pools 
                    WHERE device_id = ? 
                    ORDER BY neighbor_ip, pool_name
                """, (device_id,))
                
                pools_by_neighbor = {}
                for row in cursor.fetchall():
                    neighbor_ip = row['neighbor_ip']
                    pool_name = row['pool_name']
                    
                    if neighbor_ip not in pools_by_neighbor:
                        pools_by_neighbor[neighbor_ip] = []
                    pools_by_neighbor[neighbor_ip].append(pool_name)
                
                return pools_by_neighbor
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get route pools for device {device_id}: {e}")
            return {}
    
    def remove_device_route_pools(self, device_id: str, neighbor_ip: str = None) -> bool:
        """
        Remove route pool attachments from a device.
        
        Args:
            device_id: Device ID
            neighbor_ip: Optional neighbor IP to remove only specific attachments
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                if neighbor_ip:
                    conn.execute("""
                        DELETE FROM device_route_pools 
                        WHERE device_id = ? AND neighbor_ip = ?
                    """, (device_id, neighbor_ip))
                    logger.info(f"[DEVICE DB] Removed route pool attachments for device {device_id} and neighbor {neighbor_ip}")
                else:
                    conn.execute("""
                        DELETE FROM device_route_pools 
                        WHERE device_id = ?
                    """, (device_id,))
                    logger.info(f"[DEVICE DB] Removed all route pool attachments for device {device_id}")
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to remove route pool attachments for device {device_id}: {e}")
            return False
    
    def get_pool_usage(self, pool_name: str) -> List[Dict[str, Any]]:
        """
        Get devices and neighbors using a specific route pool.
        
        Args:
            pool_name: Pool name to check
            
        Returns:
            List of dictionaries with device_id and neighbor_ip
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT device_id, neighbor_ip, created_at 
                    FROM device_route_pools 
                    WHERE pool_name = ? 
                    ORDER BY created_at DESC
                """, (pool_name,))
                
                usage = []
                for row in cursor.fetchall():
                    usage.append({
                        'device_id': row['device_id'],
                        'neighbor_ip': row['neighbor_ip'],
                        'attached_at': row['created_at']
                    })
                
                return usage
                
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get usage for pool {pool_name}: {e}")
            return []
    
    def _detect_address_family(self, subnet: str) -> str:
        """
        Detect the address family (IPv4 or IPv6) from a subnet string.
        
        Args:
            subnet: Subnet string (e.g., "192.168.1.0/24" or "2001:db8::/64")
            
        Returns:
            str: "ipv4" or "ipv6"
        """
        if not subnet:
            return "ipv4"  # Default to IPv4
        
        # Check if it contains IPv6 indicators
        if ":" in subnet:
            return "ipv6"
        else:
            return "ipv4"