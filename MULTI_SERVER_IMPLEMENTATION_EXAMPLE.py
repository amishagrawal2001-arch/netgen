"""
Example implementation of multi-server support architecture.

This file demonstrates the key components for implementing robust multi-server support.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import requests


# ============================================================================
# 1. Server Manager (Centralized Server Registry)
# ============================================================================

@dataclass
class ServerInfo:
    """Server information structure."""
    server_id: str
    address: str
    tg_id: int
    online: bool = True
    last_seen: Optional[datetime] = None
    interfaces: List[Dict] = None
    
    def __post_init__(self):
        if self.interfaces is None:
            self.interfaces = []


class ServerManager:
    """Centralized server management and registry."""
    
    def __init__(self):
        self.servers: Dict[str, ServerInfo] = {}
        self.server_by_tg_id: Dict[int, str] = {}
        self.server_by_url: Dict[str, str] = {}
    
    def register_server(self, server_id: str, address: str, tg_id: int, **kwargs) -> ServerInfo:
        """Register a new server."""
        server_info = ServerInfo(
            server_id=server_id,
            address=address,
            tg_id=tg_id,
            **kwargs
        )
        
        self.servers[server_id] = server_info
        self.server_by_tg_id[tg_id] = server_id
        self.server_by_url[address] = server_id
        
        return server_info
    
    def get_server_url(self, server_id: Optional[str] = None, 
                      tg_id: Optional[int] = None,
                      device_info: Optional[Dict] = None) -> Optional[str]:
        """Get server URL by server_id, tg_id, or from device_info."""
        # Priority 1: From device_info (most explicit)
        if device_info:
            server_url = device_info.get("server_url")
            if server_url:
                return server_url
            
            # Try server_id from device
            server_id = device_info.get("server_id")
            if server_id and server_id in self.servers:
                return self.servers[server_id].address
            
            # Try tg_id from device
            tg_id = device_info.get("tg_id")
            if tg_id is not None:
                return self.get_server_url(tg_id=tg_id)
        
        # Priority 2: By server_id
        if server_id and server_id in self.servers:
            return self.servers[server_id].address
        
        # Priority 3: By tg_id
        if tg_id is not None and tg_id in self.server_by_tg_id:
            server_id = self.server_by_tg_id[tg_id]
            return self.servers[server_id].address
        
        return None
    
    def get_server_by_device(self, device_info: Dict) -> Optional[ServerInfo]:
        """Get server info for a device."""
        server_id = device_info.get("server_id")
        if server_id and server_id in self.servers:
            return self.servers[server_id]
        return None
    
    def validate_device_server(self, device_info: Dict) -> Tuple[bool, Optional[str]]:
        """Validate that device's server is still registered."""
        server_id = device_info.get("server_id")
        if not server_id:
            return False, "Device has no server_id"
        
        if server_id not in self.servers:
            return False, f"Server {server_id} not found in registry"
        
        server = self.servers[server_id]
        if not server.online:
            return False, f"Server {server_id} is offline"
        
        return True, None
    
    def mark_server_offline(self, server_id: str):
        """Mark a server as offline."""
        if server_id in self.servers:
            self.servers[server_id].online = False
    
    def mark_server_online(self, server_id: str):
        """Mark a server as online."""
        if server_id in self.servers:
            self.servers[server_id].online = True
            self.servers[server_id].last_seen = datetime.now()
    
    def get_all_servers(self) -> List[ServerInfo]:
        """Get all registered servers."""
        return list(self.servers.values())
    
    def get_servers_by_status(self, online: bool) -> List[ServerInfo]:
        """Get servers by online status."""
        return [s for s in self.servers.values() if s.online == online]


# ============================================================================
# 2. Device Migration Utilities
# ============================================================================

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
        except (ValueError, IndexError):
            pass
        
        return None
    
    @staticmethod
    def extract_server_id_from_url(url: str) -> str:
        """Extract server ID from URL like 'http://svl-hp-ai-srv04:5051'."""
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
        # If device already has server info, validate and return
        if device.get("server_id") and device.get("server_url"):
            # Validate server still exists
            is_valid, error = server_manager.validate_device_server(device)
            if is_valid:
                return device
            # If invalid, fall through to re-migration
        
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
                    return device
            return device  # Cannot migrate without TG ID or server URL
        
        # Find server by TG ID
        server_url = server_manager.get_server_url(tg_id=tg_id)
        if not server_url:
            return device  # Server not found
        
        # Extract server_id from URL
        server_id = cls.extract_server_id_from_url(server_url)
        
        # Update device with server information
        device["server_id"] = server_id
        device["server_url"] = server_url
        device["tg_id"] = tg_id
        
        return device
    
    @classmethod
    def migrate_all_devices(cls, all_devices: Dict, server_manager: ServerManager) -> Dict:
        """Migrate all devices in all_devices structure."""
        migrated_count = 0
        
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
        
        print(f"[MIGRATION] Migrated {migrated_count} devices to server-aware structure")
        return all_devices


# ============================================================================
# 3. Server-Aware Device Operations
# ============================================================================

class ServerAwareDeviceOperations:
    """Device operations with explicit server awareness."""
    
    def __init__(self, server_manager: ServerManager):
        self.server_manager = server_manager
    
    def apply_device(self, device_info: Dict, timeout: int = 15) -> Tuple[bool, str]:
        """Apply device configuration to its associated server."""
        # Get server URL from device (explicit)
        server_url = self.server_manager.get_server_url(device_info=device_info)
        
        if not server_url:
            return False, "No server URL found for device"
        
        # Validate server is still registered and online
        is_valid, error = self.server_manager.validate_device_server(device_info)
        if not is_valid:
            return False, error or "Server validation failed"
        
        # Apply to server
        try:
            response = requests.post(
                f"{server_url}/api/device/apply",
                json=device_info,
                timeout=timeout
            )
            
            if response.status_code == 200:
                return True, "Device applied successfully"
            else:
                return False, f"Server returned status {response.status_code}: {response.text}"
        
        except requests.exceptions.ConnectionError:
            # Server is down - mark as offline
            server_id = device_info.get("server_id")
            if server_id:
                self.server_manager.mark_server_offline(server_id)
            return False, "Server unreachable"
        
        except Exception as e:
            return False, f"Error applying device: {str(e)}"
    
    def get_device_status(self, device_info: Dict, timeout: int = 5) -> Optional[Dict]:
        """Get device status from its associated server."""
        server_url = self.server_manager.get_server_url(device_info=device_info)
        
        if not server_url:
            return None
        
        device_id = device_info.get("device_id")
        if not device_id:
            return None
        
        try:
            response = requests.get(
                f"{server_url}/api/device/database/devices/{device_id}",
                timeout=timeout
            )
            
            if response.status_code == 200:
                return response.json()
        
        except Exception as e:
            print(f"Error getting device status: {e}")
        
        return None
    
    def ping_device(self, device_info: Dict, target_ip: str, timeout: int = 15) -> Tuple[bool, str]:
        """Ping from device's server."""
        server_url = self.server_manager.get_server_url(device_info=device_info)
        
        if not server_url:
            return False, "No server URL found for device"
        
        try:
            response = requests.post(
                f"{server_url}/api/device/ping",
                json={"ip_address": target_ip, "device_id": device_info.get("device_id")},
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("success", False), result.get("message", "")
            else:
                return False, f"Server returned status {response.status_code}"
        
        except Exception as e:
            return False, f"Error pinging: {str(e)}"


# ============================================================================
# 4. Integration Example (How to use in main application)
# ============================================================================

class ExampleIntegration:
    """Example of how to integrate ServerManager into the main application."""
    
    def __init__(self):
        # Initialize server manager
        self.server_manager = ServerManager()
        self.device_ops = ServerAwareDeviceOperations(self.server_manager)
        
        # Initialize from existing server_interfaces
        self.server_interfaces = [
            {"tg_id": 0, "address": "http://svl-hp-ai-srv04:5051"},
            {"tg_id": 1, "address": "http://svl-hp-ai-srv05:5051"},
        ]
        
        self._initialize_servers()
    
    def _initialize_servers(self):
        """Initialize ServerManager from server_interfaces."""
        for server in self.server_interfaces:
            server_id = DeviceServerMigration.extract_server_id_from_url(server["address"])
            self.server_manager.register_server(
                server_id=server_id,
                address=server["address"],
                tg_id=server["tg_id"],
                online=server.get("online", True)
            )
    
    def migrate_existing_devices(self, all_devices: Dict) -> Dict:
        """Migrate existing devices to server-aware structure."""
        return DeviceServerMigration.migrate_all_devices(
            all_devices,
            self.server_manager
        )
    
    def apply_device_example(self, device_info: Dict):
        """Example: Apply device using server-aware operations."""
        success, message = self.device_ops.apply_device(device_info)
        if success:
            print(f"✅ Device {device_info.get('device_name')} applied: {message}")
        else:
            print(f"❌ Device {device_info.get('device_name')} failed: {message}")
    
    def get_devices_by_server(self, all_devices: Dict, server_id: str) -> List[Dict]:
        """Get all devices for a specific server."""
        devices = []
        for interface_key, device_list in all_devices.items():
            for device in device_list:
                if device.get("server_id") == server_id:
                    devices.append(device)
        return devices


# ============================================================================
# 5. Usage Example
# ============================================================================

if __name__ == "__main__":
    # Example usage
    integration = ExampleIntegration()
    
    # Example device (before migration)
    device_before = {
        "device_id": "123",
        "device_name": "device1",
        "Interface": "TG 0 - ens4np0",
        "IPv4": "192.168.1.2",
        "VLAN": "0"
    }
    
    # Migrate device
    device_after = DeviceServerMigration.migrate_device(
        device_before,
        integration.server_manager
    )
    
    print("Device before migration:", device_before)
    print("Device after migration:", device_after)
    
    # Apply device using server-aware operations
    integration.apply_device_example(device_after)
    
    # Get all devices for a server
    devices = integration.get_devices_by_server(
        {"TG 0 - ens4np0": [device_after]},
        "svl-hp-ai-srv04"
    )
    print(f"Found {len(devices)} devices on svl-hp-ai-srv04")

