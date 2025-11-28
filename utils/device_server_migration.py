"""
Device Server Migration Utilities - Migrate devices to server-aware structure.
"""

import logging
from typing import Dict, Optional
from utils.server_manager import ServerManager

logger = logging.getLogger(__name__)


class DeviceServerMigration:
    """Utilities for migrating devices to server-aware structure."""
    
    @staticmethod
    def extract_tg_id_from_interface(interface_key: str) -> Optional[int]:
        """Extract TG ID from interface key like 'TG 0 - ens4np0'."""
        if not interface_key or "TG" not in interface_key:
            return None
        
        try:
            # Extract "TG 0" part
            if " - " in interface_key:
                tg_part = interface_key.split(" - ", 1)[0].strip()
            else:
                tg_part = interface_key.split("-", 1)[0].strip()
            
            # Extract number from "TG 0"
            parts = tg_part.split()
            if len(parts) >= 2 and parts[0] == "TG":
                return int(parts[1])
        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to extract TG ID from '{interface_key}': {e}")
        
        return None
    
    @staticmethod
    def extract_server_id_from_url(url: str) -> str:
        """Extract server ID from URL like 'http://svl-hp-ai-srv04:5051'."""
        if not url:
            return "unknown"
        
        # Remove protocol
        if "://" in url:
            url = url.split("://", 1)[1]
        
        # Remove port
        if ":" in url:
            url = url.split(":")[0]
        
        return url
    
    @classmethod
    def migrate_device(cls, device: Dict, server_manager: ServerManager) -> Dict:
        """Migrate a device to include server information."""
        if not isinstance(device, dict):
            return device
        
        # If device already has complete server info, validate and return
        if device.get("server_id") and device.get("server_url"):
            # Validate server still exists
            is_valid, error = server_manager.validate_device_server(device)
            if is_valid:
                return device
            # If invalid, fall through to re-migration
            logger.debug(f"Device {device.get('device_name')} has invalid server info, re-migrating")
        
        # Extract server info from interface key
        interface_key = device.get("Interface", "") or device.get("interface_key", "")
        tg_id = cls.extract_tg_id_from_interface(interface_key)
        
        if tg_id is None:
            # Try to find server by existing server_url if present
            server_url = device.get("server_url")
            if server_url:
                server_id = cls.extract_server_id_from_url(server_url)
                if server_id in server_manager.servers:
                    device["server_id"] = server_id
                    device["server_url"] = server_url
                    device["tg_id"] = server_manager.servers[server_id].tg_id
                    logger.debug(f"Migrated device {device.get('device_name')} using existing server_url")
                    return device
            logger.warning(f"Cannot migrate device {device.get('device_name')}: no TG ID or server URL found")
            return device  # Cannot migrate without TG ID or server URL
        
        # Find server by TG ID
        server_url = server_manager.get_server_url(tg_id=tg_id)
        if not server_url:
            logger.warning(f"Cannot migrate device {device.get('device_name')}: server for TG {tg_id} not found")
            return device  # Server not found
        
        # Extract server_id from URL
        server_id = cls.extract_server_id_from_url(server_url)
        
        # Update device with server information
        device["server_id"] = server_id
        device["server_url"] = server_url
        device["tg_id"] = tg_id
        
        logger.debug(f"Migrated device {device.get('device_name')} to server {server_id} (TG {tg_id})")
        return device
    
    @classmethod
    def migrate_all_devices(cls, all_devices: Dict, server_manager: ServerManager) -> Dict:
        """Migrate all devices in all_devices structure."""
        if not isinstance(all_devices, dict):
            return all_devices
        
        migrated_count = 0
        skipped_count = 0
        
        for interface_key, devices in all_devices.items():
            if not isinstance(devices, list):
                continue
            
            for device in devices:
                if not isinstance(device, dict):
                    continue
                
                original_server_id = device.get("server_id")
                cls.migrate_device(device, server_manager)
                
                if device.get("server_id") != original_server_id:
                    migrated_count += 1
                elif original_server_id:
                    skipped_count += 1
        
        if migrated_count > 0:
            logger.info(f"Migrated {migrated_count} devices to server-aware structure (skipped {skipped_count} already migrated)")
        
        return all_devices

