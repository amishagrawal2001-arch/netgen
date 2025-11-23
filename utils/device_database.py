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
import ipaddress

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
    
    @staticmethod
    def _prepare_dhcp_config(raw_config: Any) -> Dict[str, Any]:
        """Parse and enrich DHCP config with derived pool metadata."""
        if not raw_config:
            return {}
        config: Dict[str, Any] = {}
        try:
            if isinstance(raw_config, str):
                config = json.loads(raw_config) if raw_config else {}
                if isinstance(config, str):
                    config = json.loads(config)
            elif isinstance(raw_config, dict):
                config = dict(raw_config)
            else:
                config = {}
        except (json.JSONDecodeError, TypeError):
            config = {}
        pool_start = config.get("pool_start")
        pool_end = config.get("pool_end")
        if pool_start and pool_end:
            if "pool_range" not in config:
                config["pool_range"] = f"{pool_start}-{pool_end}"
            if "pool_networks" not in config:
                try:
                    start_ip = ipaddress.IPv4Address(pool_start)
                    end_ip = ipaddress.IPv4Address(pool_end)
                    config["pool_networks"] = [
                        str(net) for net in ipaddress.summarize_address_range(start_ip, end_ip)
                    ]
                except Exception:
                    # Ignore derivation errors; leave networks unset
                    pass
        return config
    
    @staticmethod
    def _prepare_vxlan_config(raw_config: Any) -> Dict[str, Any]:
        """Normalize VXLAN configuration payloads for storage."""
        if not raw_config:
            return {}
        config: Dict[str, Any] = {}
        try:
            if isinstance(raw_config, str):
                config = json.loads(raw_config) if raw_config else {}
                if isinstance(config, str):
                    config = json.loads(config)
            elif isinstance(raw_config, dict):
                config = dict(raw_config)
            else:
                config = {}
        except (json.JSONDecodeError, TypeError):
            config = {}
        
        if not isinstance(config, dict):
            return {}
        
        def _clean_str(value: Any) -> str:
            return str(value).strip() if value is not None else ""
        
        vni = config.get("vni") or config.get("vxlan_id")
        try:
            config["vni"] = int(vni) if vni is not None else None
        except (TypeError, ValueError):
            config["vni"] = None
        
        udp_port = config.get("udp_port") or config.get("vxlan_udp_port")
        try:
            config["udp_port"] = int(udp_port) if udp_port is not None else 4789
        except (TypeError, ValueError):
            config["udp_port"] = 4789
        
        remote_values = (
            config.get("remote_peers")
            or config.get("remote_endpoints")
            or config.get("vxlan_remote_ip")
            or config.get("remote")
            or []
        )
        remote_peers: List[str] = []
        if isinstance(remote_values, str):
            tokens = [token.strip() for token in remote_values.replace(";", ",").split(",")]
            remote_peers = [token for token in tokens if token]
        elif isinstance(remote_values, (list, tuple, set)):
            remote_peers = [
                _clean_str(token)
                for token in remote_values
                if _clean_str(token)
            ]
        elif remote_values:
            candidate = _clean_str(remote_values)
            if candidate:
                remote_peers = [candidate]
        config["remote_peers"] = remote_peers
        
        config["local_ip"] = _clean_str(
            config.get("local_ip") or config.get("vxlan_local_ip") or config.get("source_ip")
        )
        config["underlay_interface"] = _clean_str(
            config.get("underlay_interface") or config.get("interface") or config.get("vxlan_underlay")
        )
        config["overlay_interface"] = _clean_str(
            config.get("overlay_interface") or config.get("vxlan_overlay")
        )
        config["vxlan_interface"] = _clean_str(config.get("vxlan_interface"))
        config["enabled"] = bool(config.get("enabled", True) and (config.get("vni") and remote_peers))
        return config
    
    @staticmethod
    def _normalize_gateway_routes_input(routes: Any) -> List[str]:
        """Normalize user-provided gateway routes into a list of CIDR strings."""
        if not routes:
            return []
        tokens: List[str] = []
        if isinstance(routes, str):
            try:
                parsed = json.loads(routes)
                if isinstance(parsed, (list, tuple, set)):
                    tokens.extend(parsed)
                elif isinstance(parsed, str):
                    tokens.append(parsed)
                else:
                    tokens.append(routes)
            except Exception:
                tokens.extend([part.strip() for part in routes.replace(";", ",").split(",")])
        elif isinstance(routes, (list, tuple, set)):
            for item in routes:
                if isinstance(item, str):
                    tokens.extend([part.strip() for part in item.replace(";", ",").split(",")])
                else:
                    tokens.append(str(item).strip())
        else:
            tokens.append(str(routes).strip())

        normalized = []
        for token in tokens:
            value = token.strip()
            if not value:
                continue
            normalized.append(value)
        return normalized

    @staticmethod
    def _serialize_gateway_routes(routes: Any) -> Optional[str]:
        """Serialize gateway routes to JSON for storage."""
        normalized = DeviceDatabase._normalize_gateway_routes_input(routes)
        if not normalized:
            return None
        try:
            return json.dumps(normalized)
        except Exception:
            return None

    @staticmethod
    def _deserialize_gateway_routes(routes_field: Any) -> List[str]:
        """Deserialize stored gateway routes JSON/text into list."""
        if not routes_field:
            return []
        if isinstance(routes_field, (list, tuple, set)):
            return [str(item).strip() for item in routes_field if str(item).strip()]
        if isinstance(routes_field, str):
            try:
                parsed = json.loads(routes_field)
                if isinstance(parsed, (list, tuple, set)):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                if isinstance(parsed, str):
                    return [parsed.strip()]
            except Exception:
                pass
            return [part.strip() for part in routes_field.replace(";", ",").split(",") if part.strip()]
        return [str(routes_field).strip()]
    
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
                    dhcp_mode TEXT,
                    dhcp_config TEXT,
                    dhcp_state TEXT DEFAULT 'Unknown',
                    dhcp_running BOOLEAN DEFAULT FALSE,
                    dhcp_lease_ip TEXT,
                    dhcp_lease_mask TEXT,
                    dhcp_lease_gateway TEXT,
                    dhcp_lease_server TEXT,
                    dhcp_lease_expires TIMESTAMP,
                    last_dhcp_check TIMESTAMP,
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
                    ,
                    vxlan_config TEXT,
                    vxlan_state TEXT DEFAULT 'Disabled',
                    vxlan_interface TEXT,
                    vxlan_enabled BOOLEAN DEFAULT FALSE,
                    vxlan_last_error TEXT,
                    vxlan_updated_at TIMESTAMP
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
            
            # Create DHCP pool definitions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dhcp_pools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_name TEXT UNIQUE NOT NULL,
                    pool_start TEXT NOT NULL,
                    pool_end TEXT NOT NULL,
                    gateway TEXT,
                    lease_time INTEGER,
                    gateway_routes TEXT,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create device DHCP pool attachments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_dhcp_pools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    pool_name TEXT NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE,
                    FOREIGN KEY (pool_name) REFERENCES dhcp_pools(pool_name) ON DELETE CASCADE,
                    UNIQUE(device_id, pool_name)
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dhcp_pools_name ON dhcp_pools(pool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_dhcp_pools_device ON device_dhcp_pools(device_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_dhcp_pools_pool ON device_dhcp_pools(pool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_dhcp_pools_primary ON device_dhcp_pools(device_id, is_primary)")
            
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
            
            vxlan_columns = {
                "vxlan_config": "TEXT",
                "vxlan_state": "TEXT DEFAULT 'Disabled'",
                "vxlan_interface": "TEXT",
                "vxlan_enabled": "BOOLEAN DEFAULT FALSE",
                "vxlan_last_error": "TEXT",
                "vxlan_updated_at": "TIMESTAMP",
            }
            for column_name, definition in vxlan_columns.items():
                if column_name not in columns:
                    logger.info(f"[DEVICE DB] Adding {column_name} column to devices table")
                    conn.execute(f"ALTER TABLE devices ADD COLUMN {column_name} {definition}")
                    conn.commit()
                    logger.info(f"[DEVICE DB] Successfully added {column_name} column")
            
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
            
            # Ensure dhcp_pools table has required columns
            cursor = conn.execute("PRAGMA table_info(dhcp_pools)")
            dhcp_pool_columns = [column[1] for column in cursor.fetchall()]
            if 'gateway' not in dhcp_pool_columns:
                logger.info("[DEVICE DB] Adding gateway column to dhcp_pools table")
                conn.execute("ALTER TABLE dhcp_pools ADD COLUMN gateway TEXT")
            if 'lease_time' not in dhcp_pool_columns:
                logger.info("[DEVICE DB] Adding lease_time column to dhcp_pools table")
                conn.execute("ALTER TABLE dhcp_pools ADD COLUMN lease_time INTEGER")
            if 'gateway_routes' not in dhcp_pool_columns:
                logger.info("[DEVICE DB] Adding gateway_routes column to dhcp_pools table")
                conn.execute("ALTER TABLE dhcp_pools ADD COLUMN gateway_routes TEXT")
            if 'description' not in dhcp_pool_columns:
                logger.info("[DEVICE DB] Adding description column to dhcp_pools table")
                conn.execute("ALTER TABLE dhcp_pools ADD COLUMN description TEXT")
            if 'created_at' not in dhcp_pool_columns:
                logger.info("[DEVICE DB] Adding created_at column to dhcp_pools table")
                conn.execute("ALTER TABLE dhcp_pools ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            if 'updated_at' not in dhcp_pool_columns:
                logger.info("[DEVICE DB] Adding updated_at column to dhcp_pools table")
                conn.execute("ALTER TABLE dhcp_pools ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            conn.commit()
            
            # Check if loopback_ipv4 and loopback_ipv6 columns exist in devices table
            # Refresh columns list after potential migrations
            cursor = conn.execute("PRAGMA table_info(devices)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'loopback_ipv4' not in columns:
                logger.info("[DEVICE DB] Adding loopback_ipv4 column to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN loopback_ipv4 TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added loopback_ipv4 column to devices table")
            
            if 'loopback_ipv6' not in columns:
                logger.info("[DEVICE DB] Adding loopback_ipv6 column to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN loopback_ipv6 TEXT")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added loopback_ipv6 column to devices table")
            
            # DHCP columns
            if 'dhcp_mode' not in columns:
                logger.info("[DEVICE DB] Adding DHCP columns to devices table")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_mode TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_config TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_state TEXT DEFAULT 'Unknown'")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_running BOOLEAN DEFAULT FALSE")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_ip TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_mask TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_gateway TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_server TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_expires TIMESTAMP")
                conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_subnet TEXT")
                conn.execute("ALTER TABLE devices ADD COLUMN last_dhcp_check TIMESTAMP")
                conn.commit()
                logger.info("[DEVICE DB] Successfully added DHCP columns to devices table")
            else:
                if 'dhcp_lease_subnet' not in columns:
                    logger.info("[DEVICE DB] Adding dhcp_lease_subnet column to devices table")
                    conn.execute("ALTER TABLE devices ADD COLUMN dhcp_lease_subnet TEXT")
                    conn.commit()
                    logger.info("[DEVICE DB] Successfully added dhcp_lease_subnet column to devices table")
            
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
                dhcp_config_raw = device_data.get("dhcp_config", {})
                dhcp_config_obj = self._prepare_dhcp_config(dhcp_config_raw)
                dhcp_mode = device_data.get("dhcp_mode") or dhcp_config_obj.get("mode", "")
                dhcp_state = device_data.get("dhcp_state", "Unknown")
                dhcp_running = device_data.get("dhcp_running", False)
                dhcp_lease_ip = device_data.get("dhcp_lease_ip", "")
                dhcp_lease_mask = device_data.get("dhcp_lease_mask", "")
                dhcp_lease_gateway = device_data.get("dhcp_lease_gateway", "")
                dhcp_lease_server = device_data.get("dhcp_lease_server", "")
                dhcp_lease_expires = device_data.get("dhcp_lease_expires")
                last_dhcp_check = device_data.get("last_dhcp_check")
                dhcp_lease_subnet = device_data.get("dhcp_lease_subnet", "")

                vxlan_config_prepared = self._prepare_vxlan_config(device_data.get("vxlan_config"))
                vxlan_enabled = bool(vxlan_config_prepared)

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
                    'loopback_ipv4': device_data.get("loopback_ipv4"),
                    'loopback_ipv6': device_data.get("loopback_ipv6"),
                    'protocols': json.dumps(device_data.get("protocols", [])),
                    'bgp_config': json.dumps(device_data.get("bgp_config", {})),
                    'ospf_config': json.dumps(device_data.get("ospf_config", {})),
                    'isis_config': json.dumps(device_data.get("isis_config", {})),
                    'dhcp_mode': dhcp_mode,
                    'dhcp_config': json.dumps(dhcp_config_obj),
                    'dhcp_state': dhcp_state,
                    'dhcp_running': int(bool(dhcp_running)),
                    'dhcp_lease_ip': dhcp_lease_ip,
                    'dhcp_lease_mask': dhcp_lease_mask,
                    'dhcp_lease_gateway': dhcp_lease_gateway,
                    'dhcp_lease_server': dhcp_lease_server,
                    'dhcp_lease_expires': dhcp_lease_expires,
                    'dhcp_lease_subnet': dhcp_lease_subnet,
                    'last_dhcp_check': last_dhcp_check,
                    'status': device_data.get("status", "Stopped"),
                    'vxlan_config': json.dumps(vxlan_config_prepared),
                    'vxlan_state': device_data.get(
                        "vxlan_state",
                        "Configured" if vxlan_enabled else "Disabled",
                    ),
                    'vxlan_interface': device_data.get("vxlan_interface", ""),
                    'vxlan_enabled': int(
                        bool(device_data.get("vxlan_enabled", vxlan_enabled))
                    ),
                    'vxlan_last_error': device_data.get("vxlan_last_error", ""),
                    'vxlan_updated_at': device_data.get("vxlan_updated_at") or datetime.now(timezone.utc).isoformat(),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                
                # Insert device
                columns_sql = ", ".join(device_info.keys())
                placeholders_sql = ", ".join(["?"] * len(device_info))
                conn.execute(
                    f"INSERT INTO devices ({columns_sql}) VALUES ({placeholders_sql})",
                    tuple(device_info.values()),
                )
                
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
                    'loopback_ipv4': 'loopback_ipv4',
                    'loopback_ipv6': 'loopback_ipv6',
                    'protocols': 'protocols',
                    'bgp_config': 'bgp_config',
                    'ospf_config': 'ospf_config',
                    'isis_config': 'isis_config',
                    'dhcp_mode': 'dhcp_mode',
                    'dhcp_config': 'dhcp_config',
                    'dhcp_state': 'dhcp_state',
                    'dhcp_running': 'dhcp_running',
                    'dhcp_lease_ip': 'dhcp_lease_ip',
                    'dhcp_lease_mask': 'dhcp_lease_mask',
                    'dhcp_lease_gateway': 'dhcp_lease_gateway',
                    'dhcp_lease_server': 'dhcp_lease_server',
                    'dhcp_lease_expires': 'dhcp_lease_expires',
                    'dhcp_lease_subnet': 'dhcp_lease_subnet',
                    'last_dhcp_check': 'last_dhcp_check',
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
                    'isis_manual_override_time': 'isis_manual_override_time',
                    'vxlan_config': 'vxlan_config',
                    'vxlan_state': 'vxlan_state',
                    'vxlan_interface': 'vxlan_interface',
                    'vxlan_enabled': 'vxlan_enabled',
                    'vxlan_last_error': 'vxlan_last_error',
                    'vxlan_updated_at': 'vxlan_updated_at',
                }
                
                for key, db_field in field_mapping.items():
                    if key in device_data:
                        if key == 'dhcp_config':
                            prepared = self._prepare_dhcp_config(device_data[key])
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(json.dumps(prepared))
                        elif key in ['protocols', 'bgp_config', 'ospf_config', 'isis_config']:
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(json.dumps(device_data[key]))
                        elif key == 'vxlan_config':
                            prepared_vxlan = self._prepare_vxlan_config(device_data[key])
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(json.dumps(prepared_vxlan))
                            if 'vxlan_enabled' not in device_data:
                                update_fields.append("vxlan_enabled = ?")
                                update_values.append(int(bool(prepared_vxlan)))
                            update_fields.append("vxlan_updated_at = ?")
                            update_values.append(datetime.now(timezone.utc).isoformat())
                        elif key == 'dhcp_running':
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(int(bool(device_data[key])))
                        elif key == 'vxlan_enabled':
                            update_fields.append(f"{db_field} = ?")
                            update_values.append(int(bool(device_data[key])))
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
                    device['dhcp_config'] = self._prepare_dhcp_config(device.get('dhcp_config'))
                    device['dhcp_running'] = bool(device.get('dhcp_running'))
                    device['vxlan_config'] = self._prepare_vxlan_config(device.get('vxlan_config'))
                    device['vxlan_enabled'] = bool(device.get('vxlan_enabled'))
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
                    device['dhcp_config'] = self._prepare_dhcp_config(device.get('dhcp_config'))
                    device['dhcp_running'] = bool(device.get('dhcp_running'))
                    device['vxlan_config'] = self._prepare_vxlan_config(device.get('vxlan_config'))
                    device['vxlan_enabled'] = bool(device.get('vxlan_enabled'))
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
                    device['dhcp_config'] = self._prepare_dhcp_config(device.get('dhcp_config'))
                    device['dhcp_running'] = bool(device.get('dhcp_running'))
                    device['vxlan_config'] = self._prepare_vxlan_config(device.get('vxlan_config'))
                    device['vxlan_enabled'] = bool(device.get('vxlan_enabled'))
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
    
    # DHCP Pool Management Methods
    
    def add_dhcp_pool(self, pool_data: Dict[str, Any]) -> bool:
        """Add a new DHCP pool definition."""
        try:
            pool_name = (pool_data.get("name") or pool_data.get("pool_name") or "").strip()
            if not pool_name:
                logger.error("[DEVICE DB] Cannot add DHCP pool without a name")
                return False

            pool_start = (pool_data.get("pool_start") or pool_data.get("start") or "").strip()
            pool_end = (pool_data.get("pool_end") or pool_data.get("end") or "").strip()
            if not pool_start or not pool_end:
                logger.error("[DEVICE DB] Cannot add DHCP pool without pool_start and pool_end")
                return False

            gateway = (pool_data.get("gateway") or "").strip() or None
            lease_time = pool_data.get("lease_time")
            try:
                lease_time = int(lease_time) if lease_time not in (None, "", False) else None
            except (TypeError, ValueError):
                lease_time = None
            gateway_routes = self._serialize_gateway_routes(
                pool_data.get("gateway_routes") or pool_data.get("gateway_route")
            )
            description = pool_data.get("description") or ""

            timestamp = datetime.now(timezone.utc).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT id FROM dhcp_pools WHERE pool_name = ?", (pool_name,))
                if cursor.fetchone():
                    logger.info(f"[DEVICE DB] DHCP pool '{pool_name}' exists, updating instead of adding")
                    return self.update_dhcp_pool(pool_name, pool_data)

                conn.execute(
                    """
                    INSERT INTO dhcp_pools (
                        pool_name, pool_start, pool_end, gateway, lease_time,
                        gateway_routes, description, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pool_name,
                        pool_start,
                        pool_end,
                        gateway,
                        lease_time,
                        gateway_routes,
                        description,
                        timestamp,
                        timestamp,
                    ),
                )
                conn.commit()
                logger.info(f"[DEVICE DB] Added DHCP pool '{pool_name}'")
                return True
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to add DHCP pool '{pool_data}': {e}")
            return False

    def update_dhcp_pool(self, pool_name: str, pool_data: Dict[str, Any]) -> bool:
        """Update an existing DHCP pool definition."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT id FROM dhcp_pools WHERE pool_name = ?", (pool_name,))
                if not cursor.fetchone():
                    logger.warning(f"[DEVICE DB] DHCP pool '{pool_name}' not found for update, creating new")
                    return self.add_dhcp_pool(pool_data)

                update_fields = []
                update_values = []

                field_mapping = {
                    "pool_start": "pool_start",
                    "pool_end": "pool_end",
                    "gateway": "gateway",
                    "lease_time": "lease_time",
                    "description": "description",
                }

                for key, column in field_mapping.items():
                    if key in pool_data and pool_data[key] is not None:
                        value = pool_data[key]
                        if key == "lease_time":
                            try:
                                value = int(value)
                            except (TypeError, ValueError):
                                value = None
                        if isinstance(value, str):
                            value = value.strip()
                        update_fields.append(f"{column} = ?")
                        update_values.append(value)

                if "gateway_routes" in pool_data or "gateway_route" in pool_data:
                    serialized_routes = self._serialize_gateway_routes(
                        pool_data.get("gateway_routes") or pool_data.get("gateway_route")
                    )
                    update_fields.append("gateway_routes = ?")
                    update_values.append(serialized_routes)

                if not update_fields:
                    logger.warning(f"[DEVICE DB] No fields provided to update for DHCP pool '{pool_name}'")
                    return True

                update_fields.append("updated_at = ?")
                update_values.append(datetime.now(timezone.utc).isoformat())
                update_values.append(pool_name)

                query = f"UPDATE dhcp_pools SET {', '.join(update_fields)} WHERE pool_name = ?"
                conn.execute(query, update_values)
                conn.commit()
                logger.info(f"[DEVICE DB] Updated DHCP pool '{pool_name}'")
                return True
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to update DHCP pool '{pool_name}': {e}")
            return False

    def get_dhcp_pool(self, pool_name: str) -> Optional[Dict[str, Any]]:
        """Get a DHCP pool by name."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM dhcp_pools WHERE pool_name = ?", (pool_name,))
                row = cursor.fetchone()
                if not row:
                    return None
                pool = dict(row)
                pool["gateway_routes"] = self._deserialize_gateway_routes(pool.get("gateway_routes"))
                return pool
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get DHCP pool '{pool_name}': {e}")
            return None

    def get_all_dhcp_pools(self) -> List[Dict[str, Any]]:
        """Get all DHCP pools."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM dhcp_pools ORDER BY created_at DESC")
                pools = []
                for row in cursor.fetchall():
                    pool = dict(row)
                    pool["gateway_routes"] = self._deserialize_gateway_routes(pool.get("gateway_routes"))
                    pools.append(pool)
                return pools
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get DHCP pools: {e}")
            return []

    def remove_dhcp_pool(self, pool_name: str) -> bool:
        """Remove a DHCP pool definition."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT id FROM dhcp_pools WHERE pool_name = ?", (pool_name,))
                if not cursor.fetchone():
                    logger.warning(f"[DEVICE DB] DHCP pool '{pool_name}' not found for removal")
                    return False
                conn.execute("DELETE FROM dhcp_pools WHERE pool_name = ?", (pool_name,))
                conn.commit()
                logger.info(f"[DEVICE DB] Removed DHCP pool '{pool_name}'")
                return True
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to remove DHCP pool '{pool_name}': {e}")
            return False

    def attach_dhcp_pools_to_device(
        self,
        device_id: str,
        primary_pool: Optional[str],
        additional_pools: Optional[List[str]] = None,
    ) -> bool:
        """Associate DHCP pools with a device."""
        additional_pools = additional_pools or []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM device_dhcp_pools WHERE device_id = ?", (device_id,))

                entries = []
                timestamp = datetime.now(timezone.utc).isoformat()
                if primary_pool:
                    entries.append((device_id, primary_pool, 1, timestamp))
                for pool_name in additional_pools:
                    if pool_name == primary_pool:
                        continue
                    entries.append((device_id, pool_name, 0, timestamp))

                if entries:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO device_dhcp_pools (device_id, pool_name, is_primary, attached_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        entries,
                    )
                conn.commit()
                logger.info(
                    f"[DEVICE DB] Attached DHCP pools to device {device_id}: primary={primary_pool}, additional={additional_pools}"
                )
                return True
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to attach DHCP pools to device {device_id}: {e}")
            return False

    def get_device_dhcp_pools(self, device_id: str) -> Dict[str, Any]:
        """Retrieve DHCP pools associated with a device."""
        result: Dict[str, Any] = {"primary": None, "additional": []}
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT pool_name, is_primary
                    FROM device_dhcp_pools
                    WHERE device_id = ?
                    ORDER BY is_primary DESC, attached_at ASC
                    """,
                    (device_id,),
                )
                for row in cursor.fetchall():
                    pool_name = row["pool_name"]
                    if row["is_primary"]:
                        result["primary"] = pool_name
                    else:
                        result.setdefault("additional", []).append(pool_name)
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to get DHCP pools for device {device_id}: {e}")
        return result

    def remove_device_dhcp_pools(self, device_id: str) -> bool:
        """Detach all DHCP pools from a device."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM device_dhcp_pools WHERE device_id = ?", (device_id,))
                conn.commit()
                logger.info(f"[DEVICE DB] Detached all DHCP pools from device {device_id}")
                return True
        except Exception as e:
            logger.error(f"[DEVICE DB] Failed to detach DHCP pools for device {device_id}: {e}")
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