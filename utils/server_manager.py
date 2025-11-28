"""
Server Manager - Centralized server management and registry for multi-server support.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class ServerInfo:
    """Server information structure."""
    server_id: str
    address: str
    tg_id: int
    online: bool = True
    last_seen: Optional[datetime] = None
    interfaces: List[Dict] = field(default_factory=list)
    
    def __post_init__(self):
        if self.interfaces is None:
            self.interfaces = []


class ServerManager:
    """Centralized server management and registry."""
    
    def __init__(self):
        self.servers: Dict[str, ServerInfo] = {}
        self.server_by_tg_id: Dict[int, str] = {}
        self.server_by_url: Dict[str, str] = {}
        logger.debug("ServerManager initialized")
    
    def register_server(self, server_id: str, address: str, tg_id: int, **kwargs) -> ServerInfo:
        """Register a new server."""
        # Normalize address (remove trailing slash)
        address = address.rstrip('/')
        
        server_info = ServerInfo(
            server_id=server_id,
            address=address,
            tg_id=tg_id,
            **kwargs
        )
        
        self.servers[server_id] = server_info
        self.server_by_tg_id[tg_id] = server_id
        self.server_by_url[address] = server_id
        
        logger.info(f"Registered server: {server_id} (TG {tg_id}) at {address}")
        return server_info
    
    def unregister_server(self, server_id: str):
        """Unregister a server."""
        if server_id in self.servers:
            server = self.servers[server_id]
            # Remove from all indexes
            if server.tg_id in self.server_by_tg_id:
                del self.server_by_tg_id[server.tg_id]
            if server.address in self.server_by_url:
                del self.server_by_url[server.address]
            del self.servers[server_id]
            logger.info(f"Unregistered server: {server_id}")
    
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
    
    def get_server_id(self, tg_id: Optional[int] = None, address: Optional[str] = None) -> Optional[str]:
        """Get server_id by tg_id or address."""
        if tg_id is not None and tg_id in self.server_by_tg_id:
            return self.server_by_tg_id[tg_id]
        
        if address:
            address = address.rstrip('/')
            if address in self.server_by_url:
                return self.server_by_url[address]
        
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
            logger.warning(f"Marked server {server_id} as offline")
    
    def mark_server_online(self, server_id: str):
        """Mark a server as online."""
        if server_id in self.servers:
            self.servers[server_id].online = True
            self.servers[server_id].last_seen = datetime.now()
            logger.info(f"Marked server {server_id} as online")
    
    def get_all_servers(self) -> List[ServerInfo]:
        """Get all registered servers."""
        return list(self.servers.values())
    
    def get_servers_by_status(self, online: bool) -> List[ServerInfo]:
        """Get servers by online status."""
        return [s for s in self.servers.values() if s.online == online]
    
    def update_server_interfaces(self, server_id: str, interfaces: List[Dict]):
        """Update interfaces for a server."""
        if server_id in self.servers:
            self.servers[server_id].interfaces = interfaces
            logger.debug(f"Updated interfaces for server {server_id}: {len(interfaces)} interfaces")
    
    def initialize_from_server_interfaces(self, server_interfaces: List[Dict], clear_existing: bool = True):
        """Initialize ServerManager from existing server_interfaces list.
        
        Args:
            server_interfaces: List of server dictionaries with 'address', 'tg_id', etc.
            clear_existing: If True, clear existing servers before initializing (default: True)
        """
        # Clear existing servers if requested (prevents duplicates on re-initialization)
        if clear_existing:
            self.servers.clear()
            self.server_by_tg_id.clear()
            self.server_by_url.clear()
            logger.debug("Cleared existing servers before re-initialization")
        
        for server in server_interfaces:
            address = server.get("address", "")
            tg_id = server.get("tg_id", 0)
            
            # Extract server_id from address
            server_id = self._extract_server_id_from_url(address)
            
            # Register server (register_server handles duplicates by overwriting)
            self.register_server(
                server_id=server_id,
                address=address,
                tg_id=tg_id,
                online=server.get("online", True),
                interfaces=server.get("interfaces", [])
            )
        
        logger.info(f"Initialized ServerManager with {len(self.servers)} servers")
    
    @staticmethod
    def _extract_server_id_from_url(url: str) -> str:
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

