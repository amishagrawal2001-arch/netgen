# Multi-Server Support Architecture Recommendations

## Current State Analysis

### Existing Multi-Server Support
The application already has **basic multi-server support**:
- `server_interfaces` list stores multiple servers with `tg_id` and `address`
- Server tree UI displays multiple servers with their interfaces
- Devices are keyed by interface strings like `"TG 0 - ens4np0"`

### Current Limitations

1. **Implicit Server Association**: Devices don't explicitly store which server they belong to
   - Server URL is derived by parsing interface strings (`_get_server_url_from_interface`)
   - Fragile: breaks if interface naming changes
   - No validation that TG ID in interface string matches actual server

2. **No Server Registry**: No centralized server management
   - Server lookup scattered across codebase
   - No server health/status tracking per device
   - No server-specific configuration management

3. **Device Storage Structure**: `all_devices` dictionary keyed by interface
   ```python
   all_devices = {
       "TG 0 - ens4np0": [device1, device2],
       "TG 1 - ens5np0": [device3]
   }
   ```
   - Interface string contains server info, but not explicitly
   - Hard to query "all devices on server X"
   - Migration required if structure changes

4. **Session Management**: Server info stored in `session.json`, but device-to-server mapping is implicit

---

## Recommended Architecture

### 1. **Explicit Server Association in Device Data**

**Store server information directly in each device:**

```python
device_data = {
    "device_id": "uuid",
    "device_name": "device1",
    "server_id": "svl-hp-ai-srv04",  # NEW: Unique server identifier
    "server_url": "http://svl-hp-ai-srv04:5051",  # NEW: Direct server URL
    "tg_id": 0,  # NEW: TG ID for this server
    "interface": "ens4np0",  # Simplified: no TG prefix needed
    "interface_key": "TG 0 - ens4np0",  # Keep for backward compatibility
    # ... rest of device data
}
```

**Benefits:**
- Direct server lookup without string parsing
- Can query devices by server
- Server changes don't break device associations
- Easier to migrate devices between servers

### 2. **Server Registry/Manager Pattern**

**Create a centralized `ServerManager` class:**

```python
class ServerManager:
    """Centralized server management and registry."""
    
    def __init__(self):
        self.servers = {}  # {server_id: server_info}
        self.server_by_tg_id = {}  # {tg_id: server_id} for quick lookup
        self.server_by_url = {}  # {url: server_id} for reverse lookup
    
    def register_server(self, server_id, address, tg_id, **kwargs):
        """Register a new server."""
        server_info = {
            "server_id": server_id,
            "address": address,
            "tg_id": tg_id,
            "online": True,
            "last_seen": None,
            "interfaces": [],
            **kwargs
        }
        self.servers[server_id] = server_info
        self.server_by_tg_id[tg_id] = server_id
        self.server_by_url[address] = server_id
        return server_info
    
    def get_server_url(self, server_id=None, tg_id=None, device_info=None):
        """Get server URL by server_id, tg_id, or from device_info."""
        if device_info:
            return device_info.get("server_url") or \
                   self.get_server_url(tg_id=device_info.get("tg_id"))
        
        if server_id and server_id in self.servers:
            return self.servers[server_id]["address"]
        
        if tg_id and tg_id in self.server_by_tg_id:
            server_id = self.server_by_tg_id[tg_id]
            return self.servers[server_id]["address"]
        
        return None
    
    def get_server_by_device(self, device_info):
        """Get server info for a device."""
        server_id = device_info.get("server_id")
        if server_id and server_id in self.servers:
            return self.servers[server_id]
        return None
    
    def validate_device_server(self, device_info):
        """Validate that device's server is still registered."""
        server_id = device_info.get("server_id")
        if not server_id:
            return False, "Device has no server_id"
        
        if server_id not in self.servers:
            return False, f"Server {server_id} not found in registry"
        
        return True, None
```

**Usage in main window:**
```python
class TrafficGeneratorClient:
    def __init__(self):
        self.server_manager = ServerManager()
        # Initialize from server_interfaces
        for server in self.server_interfaces:
            server_id = self._extract_server_id(server["address"])
            self.server_manager.register_server(
                server_id=server_id,
                address=server["address"],
                tg_id=server["tg_id"]
            )
```

### 3. **Device Storage Structure Enhancement**

**Option A: Keep current structure, add server metadata**
```python
# Keep backward compatibility
all_devices = {
    "TG 0 - ens4np0": [device1, device2],  # device1 has server_id inside
    "TG 1 - ens5np0": [device3]
}

# Add server-indexed view
devices_by_server = {
    "svl-hp-ai-srv04": [device1, device2],
    "svl-hp-ai-srv05": [device3]
}
```

**Option B: Dual-key structure (Recommended)**
```python
# Primary: by server, then interface
all_devices = {
    "svl-hp-ai-srv04": {
        "TG 0 - ens4np0": [device1, device2],
        "TG 0 - ens5np0": [device3]
    },
    "svl-hp-ai-srv05": {
        "TG 1 - ens4np0": [device4]
    }
}

# Helper: flat interface-keyed view for backward compatibility
devices_by_interface = {
    "TG 0 - ens4np0": [device1, device2],
    "TG 1 - ens4np0": [device4]
}
```

**Recommendation: Option A** (easier migration, less breaking changes)

### 4. **Device Operations with Server Awareness**

**Update device operations to use explicit server association:**

```python
def _apply_device_to_server_sync(self, device_info):
    """Apply device configuration to its associated server."""
    # Get server URL from device (explicit)
    server_url = device_info.get("server_url")
    if not server_url:
        # Fallback to server manager
        server_url = self.main_window.server_manager.get_server_url(
            device_info=device_info
        )
    
    if not server_url:
        return False, "No server URL found for device"
    
    # Validate server is still registered
    is_valid, error = self.main_window.server_manager.validate_device_server(
        device_info
    )
    if not is_valid:
        return False, error
    
    # Apply to server
    response = requests.post(f"{server_url}/api/device/apply", ...)
    return response.status_code == 200, response.text
```

### 5. **Session Management Enhancement**

**Update session.json to include server registry:**

```json
{
  "servers": [
    {
      "server_id": "svl-hp-ai-srv04",
      "tg_id": 0,
      "address": "http://svl-hp-ai-srv04:5051",
      "online": true
    }
  ],
  "devices": {
    "TG 0 - ens4np0": [
      {
        "device_id": "uuid",
        "device_name": "device1",
        "server_id": "svl-hp-ai-srv04",  // NEW
        "server_url": "http://svl-hp-ai-srv04:5051",  // NEW
        "tg_id": 0,  // NEW
        "interface": "ens4np0",
        // ... rest of device data
      }
    ]
  }
}
```

### 6. **Migration Strategy**

**Phase 1: Add server fields without breaking existing code**
```python
def _migrate_devices_to_server_aware(self):
    """Migrate existing devices to include server information."""
    for interface_key, devices in self.all_devices.items():
        for device in devices:
            # If device already has server info, skip
            if device.get("server_id"):
                continue
            
            # Extract server info from interface key
            tg_id = self._extract_tg_id_from_interface(interface_key)
            server = self._find_server_by_tg_id(tg_id)
            
            if server:
                device["server_id"] = server["server_id"]
                device["server_url"] = server["address"]
                device["tg_id"] = tg_id
```

**Phase 2: Update all device operations to use explicit server info**
- Replace `_get_server_url_from_interface()` calls with `device_info.get("server_url")`
- Update `get_server_url()` to check device info first

**Phase 3: Remove interface string parsing (optional)**
- Once all devices have explicit server info, can simplify interface keys

### 7. **UI Enhancements**

**Server-aware device filtering:**
```python
def update_device_table(self, devices=None, server_filter=None):
    """Update device table with optional server filtering."""
    if server_filter:
        # Filter devices by server
        filtered_devices = {}
        for iface, device_list in devices.items():
            for device in device_list:
                if device.get("server_id") == server_filter:
                    if iface not in filtered_devices:
                        filtered_devices[iface] = []
                    filtered_devices[iface].append(device)
        devices = filtered_devices
    
    # ... rest of update logic
```

**Server status indicators in device table:**
- Add server column to device table
- Show server online/offline status
- Color-code devices by server health

### 8. **Error Handling and Resilience**

**Handle server failures gracefully:**
```python
def apply_device_with_retry(self, device_info, max_retries=3):
    """Apply device with automatic retry on server failure."""
    server_url = device_info.get("server_url")
    
    for attempt in range(max_retries):
        try:
            response = requests.post(f"{server_url}/api/device/apply", ...)
            if response.status_code == 200:
                return True, "Success"
        except requests.exceptions.ConnectionError:
            # Server is down - mark as offline
            self.main_window.server_manager.mark_server_offline(
                device_info.get("server_id")
            )
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            return False, "Server unreachable"
    
    return False, "Max retries exceeded"
```

---

## Implementation Priority

### High Priority (Core Functionality)
1. ✅ Add `server_id`, `server_url`, `tg_id` to device data structure
2. ✅ Create `ServerManager` class
3. ✅ Migration function to populate server fields in existing devices
4. ✅ Update `_apply_device_to_server_sync` to use explicit server info

### Medium Priority (User Experience)
5. ✅ Update session save/load to include server registry
6. ✅ Add server filtering to device table
7. ✅ Update all device operations (ping, ARP, etc.) to use explicit server info

### Low Priority (Nice to Have)
8. ✅ Server health monitoring per device
9. ✅ Device migration between servers UI
10. ✅ Server-specific configuration profiles

---

## Code Structure Recommendations

### New Files
```
utils/server_manager.py          # ServerManager class
utils/device_server_migration.py # Migration utilities
```

### Modified Files
```
traffic_client/main.py           # Initialize ServerManager
widgets/devices_tab.py           # Use explicit server info
traffic_client/server_section.py # Integrate with ServerManager
traffic_client/menu_actions.py  # Update session save/load
```

---

## Backward Compatibility

**Maintain compatibility during migration:**
- Keep `_get_server_url_from_interface()` as fallback
- Support both old (interface-parsed) and new (explicit) server lookup
- Migration runs automatically on session load
- Old sessions continue to work

---

## Testing Strategy

1. **Unit Tests**: Test ServerManager with various server configurations
2. **Integration Tests**: Test device operations across multiple servers
3. **Migration Tests**: Verify old sessions migrate correctly
4. **Failure Tests**: Test behavior when servers go offline

---

## Example Implementation

See `MULTI_SERVER_IMPLEMENTATION_EXAMPLE.py` for a complete code example.

