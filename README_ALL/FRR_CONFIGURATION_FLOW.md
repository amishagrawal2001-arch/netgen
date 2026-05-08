# FRR Configuration Flow When New Device is Added

## Overview
This document explains the complete flow of configuring FRR (Free Range Routing) when a new device is added in the OSTG application.

---

## Step-by-Step Flow

### **Phase 1: User Adds Device in UI**

1. **User Action**: User clicks "Add Device" button in the Devices tab
2. **Dialog Opens**: `AddDeviceDialog` opens (`widgets/add_device_dialog.py`)
3. **User Fills Form**:
   - Device name, interface, VLAN, MAC address
   - IPv4/IPv6 addresses and masks
   - Gateways
   - Loopback IPv4/IPv6 addresses
   - Protocol configurations (BGP, OSPF, ISIS)
4. **Dialog Returns**: User clicks "Add Device" → `dialog.get_values()` returns all config
5. **Device Stored Locally**: Device info stored in `main_window.all_devices` dictionary
   - Key: Interface label (e.g., "TG 0 - ens4np0")
   - Value: List of device dictionaries
   - Each device has: `device_id`, `Device Name`, `IPv4`, `IPv6`, `protocols`, `bgp_config`, `ospf_config`, `isis_config`
6. **UI Updated**: Device table refreshed to show new device with "Pending" status

---

### **Phase 2: User Clicks "Apply"**

1. **User Action**: User selects device(s) and clicks "Apply" button
2. **Handler Called**: `apply_selected_device()` in `widgets/devices_tab.py`
3. **Background Worker**: `MultiDeviceApplyWorker` thread created
4. **For Each Device**: `_apply_device_to_server_sync()` is called

---

### **Phase 3: Client Sends Device Apply Request**

**Location**: `widgets/devices_tab.py` → `_apply_device_to_server_sync()`

#### **Step 3.1: Prepare Basic Device Payload**
```python
basic_payload = {
    "device_id": device_id,
    "device_name": device_name,
    "interface": iface_norm,
    "vlan": vlan,
    "ipv4": ipv4,
    "ipv6": ipv6,
    "ipv4_mask": ipv4_mask,
    "ipv6_mask": ipv6_mask,
    "ipv4_gateway": ipv4_gateway,
    "ipv6_gateway": ipv6_gateway,
    "loopback_ipv4": loopback_ipv4,
    "loopback_ipv6": loopback_ipv6,
    "protocols": protocols,
    "bgp_config": bgp_config,
    "ospf_config": ospf_config,
    "isis_config": isis_config
}
```

#### **Step 3.2: Send to `/api/device/apply`**
- **Request**: `POST {server_url}/api/device/apply`
- **Payload**: `basic_payload`
- **Timeout**: 30 seconds

---

### **Phase 4: Server Processes Device Apply**

**Location**: `run_tgen_server.py` → `@app.route("/api/device/apply")`

#### **Step 4.1: Create VLAN Interface (if needed)**
- If VLAN != "0":
  - Check if `vlan{vlan}` exists (e.g., `vlan20`)
  - Create if missing: `ip link add link {interface} name vlan{vlan} type vlan id {vlan}`
  - Bring interface up: `ip link set vlan{vlan} up`

#### **Step 4.2: Configure Interface IP Addresses**
- **IPv4**: `ip addr add {ipv4}/{ipv4_mask} dev {iface_name}`
- **IPv6**: `ip addr add {ipv6}/{ipv6_mask} dev {iface_name}`

#### **Step 4.3: Configure Static Routes**
- **IPv4 Gateway**: `ip route add default via {ipv4_gateway} dev {iface_name}`
- **IPv6 Gateway**: `ip -6 route add default via {ipv6_gateway} dev {iface_name}`

#### **Step 4.4: Check/Create FRR Container**
```python
from utils.frr_docker import FRRDockerManager
frr_manager = FRRDockerManager()
container_name = frr_manager._get_container_name(device_id, device_name)
# Format: "ostg-frr-{device_id}"
```

- Check if container exists:
  - If exists and running → use it
  - If exists but stopped → remove and recreate
  - If doesn't exist → will be created during protocol config

#### **Step 4.5: Configure Loopback IPs (if provided)**
- If container exists and running:
  - **Configuration Method**: Uses FRR `vtysh` commands (not Linux `ip` commands)
  - **Commands**:
    ```bash
    vtysh << 'EOF'
    configure terminal
    interface lo
     ip address {loopback_ipv4}/32
     ipv6 address {loopback_ipv6}/128
    exit
    exit
    write memory
    EOF
    ```
  - Both IPv4 and IPv6 loopback addresses are configured in a single `vtysh` session
- If container doesn't exist yet → will be configured later during protocol setup

#### **Step 4.6: Update Database**
- Save device to database if it doesn't exist
- Update device status to "Running"
- Store all device info (IPs, protocols, configs)

---

### **Phase 5: Client Configures Protocols Sequentially**

After `/api/device/apply` succeeds, client configures protocols in order:

#### **Step 5.1: Configure BGP (if enabled)**

**Location**: `widgets/devices_tab.py` → `_apply_bgp_to_server_sync()`

**Flow**:
1. Check if "BGP" in `protocols` and `bgp_config` exists
2. Prepare BGP payload:
   ```python
   bgp_payload = {
       "device_id": device_id,
       "device_name": device_name,
       "interface": interface,
       "vlan": vlan,
       "ipv4": ipv4,
       "ipv6": ipv6,
       "bgp_config": bgp_config,
       "all_route_pools": bgp_route_pools
   }
   ```
3. Send to server: `POST {server_url}/api/device/bgp/configure`

**Server Side** (`run_tgen_server.py` → `@app.route("/api/device/bgp/configure")`):
1. Check if FRR container exists
2. If not → **Create FRR Container**:
   - **Location**: `utils/frr_docker.py` → `FRRDockerManager.start_frr_container()`
   - **Container Creation**:
     ```python
     container = client.containers.run(
         image="ostg-frr:latest",
         name="ostg-frr-{device_id}",
         network_mode='host',
         privileged=True,
         environment={
             'FRR_DAEMONS': 'bgpd ospfd',
             'LOCAL_ASN': bgp_asn,
             'ROUTER_ID': router_id,  # Loopback IPv4 if available, else interface IPv4
             ...
         }
     )
     ```
   - **Wait**: 5 seconds for FRR daemons to start
3. **Configure Global Router-ID** (`utils/frr_docker.py` → `_configure_global_router_id()`):
   - **Configuration Method**: Uses FRR `vtysh` commands
   - **Commands**:
     ```bash
     vtysh << 'EOF'
     configure terminal
     ip router-id {loopback_ipv4}  # Must be loopback IPv4
     exit
     EOF
     ```
   - **Router-ID Selection**:
     - First: Get loopback IPv4 from database
     - Fallback: Interface IPv4 if loopback not available
     - Last resort: Default "192.168.0.2"
   - **Note**: Global router-id is configured once when container is created, used by all protocols
4. **Configure Loopback** (if loopback IPs provided):
   - Configure loopback IPv4/IPv6 inside container using `vtysh`
5. **Call**: `utils/bgp.py` → `configure_bgp_for_device()`
   - **Note**: BGP configuration is now only in `bgp.py`, not in `frr_docker.py`
6. **Build vtysh Commands**:
   ```bash
   configure terminal
   router bgp {local_as}
   bgp router-id {router_id}  # Loopback IPv4 (uses global router-id)
   bgp log-neighbor-changes
   bgp graceful-restart
   neighbor {neighbor_ipv4} remote-as {neighbor_as}
   neighbor {neighbor_ipv4} update-source {update_source_ipv4}
   neighbor {neighbor_ipv4} timers {keepalive} {hold_time}
   address-family ipv4 unicast
   neighbor {neighbor_ipv4} activate
   network {ipv4_network}
   exit-address-family
   # ... similar for IPv6
   ```
7. **Execute**: Commands sent via `vtysh` using here-doc (stdin input) to maintain context
8. **Update Database**: Save BGP config and status

---

#### **Step 5.2: Configure OSPF (if enabled)**

**Location**: `widgets/devices_tab.py` → `_apply_ospf_to_server_sync()`

**Flow**:
1. Check if "OSPF" in `protocols` and `ospf_config` exists
2. Prepare OSPF payload:
   ```python
   ospf_payload = {
       "device_id": device_id,
       "device_name": device_name,
       "interface": interface,
       "vlan": vlan,
       "ipv4": ipv4,
       "ipv6": ipv6,
       "ospf_config": ospf_config,
       "route_pools_per_area": {},
       "all_route_pools": []
   }
   ```
3. Send to server: `POST {server_url}/api/device/ospf/configure`

**Server Side** (`run_tgen_server.py` → `@app.route("/api/device/ospf/configure")`):
1. Check if FRR container exists
2. If not → Create container (same as BGP)
   - Global router-id is configured when container is created
3. **Configure Loopback** (if loopback IPs provided and not already configured)
4. **Call**: `utils/ospf.py` → `configure_ospf_neighbor()`
5. **Build vtysh Commands**:
   ```bash
   configure terminal
   router ospf
   ospf router-id {router_id}  # Loopback IPv4 (retrieved from database)
   network {ipv4_network} area {area_id}  # For IPv4
   exit
   interface {interface}
   ip ospf hello-interval {hello_interval}
   ip ospf dead-interval {dead_interval}
   ip ospf area {area_id}
   no ip ospf passive
   exit
   router ospf6
   ospf6 router-id {router_id}  # Loopback IPv4 (same as IPv4)
   exit
   interface {interface}
   ipv6 ospf6 area {ipv6_area_id}  # For IPv6 (separate area ID)
   exit
   ```
6. **Router-ID Selection**:
   - First: Get loopback IPv4 from database
   - Fallback: Use router_id from config if provided
   - Fallback: Interface IPv4 if loopback not available
7. **Execute**: Commands sent via `vtysh` using here-doc
8. **Update Database**: Save OSPF config and status

---

#### **Step 5.3: Configure ISIS (if enabled)**

**Location**: `widgets/devices_tab.py` → `_apply_isis_to_server_sync()`

**Flow**:
1. Check if "IS-IS" in `protocols` and `isis_config` exists
2. Prepare ISIS payload:
   ```python
   isis_payload = {
       "device_id": device_id,
       "device_name": device_name,
       "interface": interface,
       "vlan": vlan,
       "ipv4": ipv4,
       "ipv6": ipv6,
       "isis_config": isis_config
   }
   ```
3. Send to server: `POST {server_url}/api/device/isis/configure`

**Server Side** (`run_tgen_server.py` → `@app.route("/api/device/isis/configure")`):
1. Check if FRR container exists
2. If not → Create container (same as BGP)
   - Global router-id is configured when container is created
3. **Configure Loopback** (if loopback IPs provided and not already configured)
4. **Call**: `utils/isis.py` → `configure_isis_neighbor()`
5. **Build vtysh Commands**:
   ```bash
   configure terminal
   router isis CORE
   is-type {level}  # level-1-only, level-2-only, or level-1-2
   net {area_id}  # e.g., 49.0001.0000.0000.0001.00
   exit
   interface {interface}
   ip router isis CORE  # If IPv4 enabled
   ipv6 router isis CORE  # If IPv6 enabled
   isis network point-to-point
   exit
   ```
   - **Note**: Global router-id (`ip router-id`) is configured in `frr_docker.py` when container is created, not here
6. **Execute**: Commands sent via `vtysh` using here-doc
7. **Update Database**: Save ISIS config and status

---

### **Phase 6: FRR Container Configuration Details**

#### **Container Creation** (`utils/frr_docker.py` → `start_frr_container()`)

1. **Container Name**: `ostg-frr-{device_id}`
2. **Network Mode**: `host` (container shares host network stack)
3. **Privileged**: Yes (required for network operations)
4. **Environment Variables**:
   - `FRR_DAEMONS`: `bgpd ospfd` (or `bgpd ospfd isisd` if ISIS enabled)
   - `LOCAL_ASN`: BGP ASN (if BGP enabled)
   - `ROUTER_ID`: **Loopback IPv4 if available, else interface IPv4**
   - `DEVICE_NAME`: Device name
   - `NETWORK`: IPv4 network (e.g., `192.168.0.0`)
   - `NETMASK`: IPv4 mask (e.g., `24`)
   - `INTERFACE`: Interface name (e.g., `vlan20`)
   - `IP_ADDRESS`: IPv4 address
   - `IP_MASK`: IPv4 mask

5. **Router-ID Selection** (`_get_router_id()`):
   ```
   1. Check device_config for loopback_ipv4
   2. If not found, query database for loopback_ipv4
   3. Fallback to interface IPv4
   4. Last resort: default "192.168.0.2"
   ```

6. **Container Startup**:
   - Container starts with FRR daemons
   - Waits 5 seconds for daemons to initialize
   - **Configure Global Router-ID** (`_configure_global_router_id()`):
     - Uses loopback IPv4 from database (if available)
     - Falls back to interface IPv4 if loopback not configured
     - Configures `ip router-id {loopback_ipv4}` using `vtysh`
     - This global router-id is used by all protocols (BGP, OSPF, ISIS)
   - **Note**: BGP configuration is now handled by `bgp.py`, not here

#### **Interface Configuration in Container**

When protocols are configured, the container's interface is also configured:

1. **Interface IP Addresses**:
   - Configured during protocol setup (BGP, OSPF, ISIS)
   - Uses `interface {iface_name}` → `ip address {ipv4}/{mask}` → `ipv6 address {ipv6}/{mask}`

2. **Loopback IP Addresses**:
   - Configured during protocol setup
   - Uses `interface lo` → `ip address {loopback_ipv4}/32` → `ipv6 address {loopback_ipv6}/128`

---

### **Phase 7: Protocol Configuration in FRR**

#### **Global Router-ID Configuration** (`utils/frr_docker.py` → `_configure_global_router_id()`)

**When**: Configured when FRR container is created (in `start_frr_container()`)

**Commands**:
```bash
vtysh << 'EOF'
configure terminal
ip router-id {loopback_ipv4}  # Must be loopback IPv4
exit
EOF
```

**Router-ID Selection**:
1. Get loopback IPv4 from database
2. Fallback to interface IPv4 if loopback not available
3. Last resort: Default "192.168.0.2"

**Note**: This global router-id is used by all protocols (BGP, OSPF, ISIS). It's configured once when the container is created.

#### **BGP Configuration** (`utils/bgp.py` → `configure_bgp_for_device()`)

**Commands**:
```bash
vtysh << 'EOF'
configure terminal
router bgp {local_as}
bgp router-id {router_id}  # Loopback IPv4 (same as global router-id)
bgp log-neighbor-changes
bgp graceful-restart
interface {iface_name}  # e.g., vlan20
 ip address {ipv4}/{mask}
 ipv6 address {ipv6}/{mask}
exit
neighbor {neighbor_ipv4} remote-as {neighbor_as}
neighbor {neighbor_ipv4} update-source {update_source_ipv4}
neighbor {neighbor_ipv4} timers {keepalive} {hold_time}
address-family ipv4 unicast
neighbor {neighbor_ipv4} activate
network {ipv4_network}
exit-address-family
address-family ipv6 unicast
neighbor {neighbor_ipv6} activate
exit-address-family
exit
exit
EOF
```

**Note**: 
- BGP configuration is now only in `bgp.py`, not in `frr_docker.py`
- Here-doc (stdin input) is used instead of multiple `-c` arguments to maintain `vtysh` context for nested commands
- Router-id uses loopback IPv4 retrieved from database

#### **OSPF Configuration** (`utils/ospf.py` → `configure_ospf_neighbor()`)

**Commands**:
```bash
vtysh << 'EOF'
configure terminal
router ospf
ospf router-id {router_id}  # Loopback IPv4 (retrieved from database)
network {ipv4_network} area {ipv4_area_id}
exit
interface {interface}
ip ospf hello-interval {hello_interval}
ip ospf dead-interval {dead_interval}
ip ospf area {ipv4_area_id}
no ip ospf passive
exit
router ospf6
ospf6 router-id {router_id}  # Loopback IPv4 (same as IPv4)
exit
interface {interface}
ipv6 ospf6 area {ipv6_area_id}  # Separate area ID for IPv6
exit
EOF
```

**Router-ID Selection**:
1. Get loopback IPv4 from database (prioritized)
2. Fallback to router_id from config if provided
3. Fallback to interface IPv4 if loopback not available

#### **ISIS Configuration** (`utils/isis.py` → `configure_isis_neighbor()`)

**Commands**:
```bash
vtysh << 'EOF'
configure terminal
router isis CORE
is-type {level}  # level-1-only, level-2-only, or level-1-2
net {area_id}  # e.g., 49.0001.0000.0000.0001.00
exit
interface {interface}
ip router isis CORE  # If IPv4 enabled
ipv6 router isis CORE  # If IPv6 enabled
isis network point-to-point
exit
EOF
```

**Note**: Global router-id (`ip router-id`) is configured in `frr_docker.py` when container is created, not in ISIS configuration. ISIS uses the global router-id automatically.

---

## Key Points

1. **Container Creation**: FRR container is created during the **first protocol configuration** (BGP, OSPF, or ISIS), not during `/api/device/apply`

2. **Global Router-ID Configuration**:
   - **Location**: `utils/frr_docker.py` → `_configure_global_router_id()`
   - **When**: Configured when FRR container is created (in `start_frr_container()`)
   - **Command**: `ip router-id {loopback_ipv4}` (global FRR command)
   - **Router-ID Priority**: Loopback IPv4 is **required** (must be loopback IPv4):
     - First: Get loopback IPv4 from database
     - Fallback: Interface IPv4 if loopback not available
     - Last resort: Default "192.168.0.2"
   - **Usage**: All protocols (BGP, OSPF, ISIS) use this global router-id

3. **BGP Configuration Location**: BGP configuration is now **only in `bgp.py`**, not in `frr_docker.py`:
   - `utils/bgp.py` → `configure_bgp_for_device()` handles full BGP configuration
   - `utils/frr_docker.py` only manages container lifecycle and global router-id

4. **Protocol-Specific Router-ID**:
   - **BGP**: Uses loopback IPv4 from database as `bgp router-id`
   - **OSPF**: Uses loopback IPv4 from database as `ospf router-id` and `ospf6 router-id`
   - **ISIS**: Uses global `ip router-id` configured in `frr_docker.py` (no protocol-specific router-id needed)

5. **Protocol Order**: Protocols are configured sequentially:
   - BGP first
   - OSPF second
   - ISIS third

6. **Interface Configuration**: Device interface (e.g., `vlan20`) is configured with IP addresses during protocol setup (BGP, OSPF, ISIS), not during `/api/device/apply`

7. **Loopback Configuration**: Loopback IPs are configured:
   - **Method**: Uses FRR `vtysh` commands (not Linux `ip` commands)
   - **When**: In `/api/device/apply` if container already exists
   - **Otherwise**: During protocol configuration when container is created

8. **vtysh Execution**: Commands use here-doc (stdin input) to maintain context for nested commands like `interface` → `ip address`

9. **Database Updates**: Each protocol endpoint updates the database with:
   - Protocol configuration
   - Protocol status (Running/Established)
   - Manual override flags (to prevent monitors from overwriting)

---

## Sequence Diagram (Simplified)

```
User → Add Device Dialog → Store in Memory → Click Apply
  ↓
Client: _apply_device_to_server_sync()
  ↓
Step 1: POST /api/device/apply
  → Server: Create VLAN interface
  → Server: Configure IP addresses
  → Server: Configure static routes
  → Server: Check/create FRR container (if exists)
  → Server: Configure loopback (if container exists)
  → Server: Update database
  ↓
Step 2: POST /api/device/bgp/configure (if BGP enabled)
  → Server: Create FRR container (if not exists)
  → Server: Configure global router-id (loopback IPv4)
  → Server: Configure loopback (if not done)
  → Server: Configure BGP via vtysh (bgp.py)
  → Server: Update database
  ↓
Step 3: POST /api/device/ospf/configure (if OSPF enabled)
  → Server: Use existing container
  → Server: Configure OSPF via vtysh
  → Server: Update database
  ↓
Step 4: POST /api/device/isis/configure (if ISIS enabled)
  → Server: Use existing container
  → Server: Configure ISIS via vtysh
  → Server: Update database
  ↓
Client: Refresh UI tables
```

---

## Files Involved

1. **Client UI**: `widgets/devices_tab.py` - Device management and apply flow
2. **Device Dialog**: `widgets/add_device_dialog.py` - Device creation dialog
3. **Server API**: `run_tgen_server.py` - REST API endpoints
4. **FRR Docker**: `utils/frr_docker.py` - Container management and global router-id configuration
5. **BGP Config**: `utils/bgp.py` - BGP configuration (only location for BGP config)
6. **OSPF Config**: `utils/ospf.py` - OSPF configuration
7. **ISIS Config**: `utils/isis.py` - ISIS configuration
8. **Database**: `utils/device_database.py` - Device data persistence

