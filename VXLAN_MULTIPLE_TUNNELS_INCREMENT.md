# VXLAN Multiple Tunnels with Increment Options - Behavior Explanation

## Overview

When a user enables increment options in the VXLAN dialog, the system generates multiple VXLAN tunnels based on the increment settings. This document explains how the increment mechanism works and what gets incremented.

## Increment Options Available

The VXLAN dialog (`widgets/add_vxlan_dialog.py`) provides the following increment options:

1. **VNI Increment**: Options: `+1`, `+10`, `+100`, `+1000`
2. **VLAN ID Increment**: Options: `+1`, `+10`, `+100`
3. **Bridge SVI IP Increment**: Select which octet to increment (`1st`, `2nd`, `3rd`, `4th`)
4. **Count**: Number of tunnels to generate (1-100)

## How Increment Works

### Location: `widgets/devices_tab.py` - `prompt_add_vxlan()` method (lines 2196-2253)

When the user enables increment and clicks "Add VXLAN", the following process occurs:

### Step 1: Extract Increment Configuration

```python
if vxlan_config.get("increment", {}).get("enabled", False):
    increment_config = vxlan_config["increment"]
    count = increment_config["count"]                    # Number of tunnels to create
    vni_increment = increment_config["vni_increment"]     # VNI step (1, 10, 100, or 1000)
    vlan_id_increment = increment_config.get("vlan_id_increment", 1)  # VLAN ID step
    svi_ip_octet = increment_config.get("svi_ip_octet", "3rd")  # Which octet to increment
```

### Step 2: Get Base Values

```python
base_vni = vxlan_config["vni"]                    # Starting VNI (e.g., 5000)
base_vlan_id = vxlan_config.get("vlan_id")        # Starting VLAN ID (e.g., 100)
base_svi_ip = vxlan_config.get("bridge_svi_ip", "10.0.0.100/24")  # Starting SVI IP
```

### Step 3: Generate Multiple Tunnels

For each tunnel from `i = 0` to `count - 1`:

#### 3.1: VNI Increment
```python
tunnel_config["vni"] = base_vni + (i * vni_increment)
```

**Example:**
- Base VNI: `5000`
- VNI Increment: `+10`
- Count: `3`
- Result:
  - Tunnel 1: VNI = `5000 + (0 * 10) = 5000`
  - Tunnel 2: VNI = `5000 + (1 * 10) = 5010`
  - Tunnel 3: VNI = `5000 + (2 * 10) = 5020`

#### 3.2: VLAN ID Increment (if provided)
```python
if base_vlan_id:
    tunnel_config["vlan_id"] = base_vlan_id + (i * vlan_id_increment)
```

**Example:**
- Base VLAN ID: `100`
- VLAN ID Increment: `+1`
- Count: `3`
- Result:
  - Tunnel 1: VLAN ID = `100 + (0 * 1) = 100`
  - Tunnel 2: VLAN ID = `100 + (1 * 1) = 101`
  - Tunnel 3: VLAN ID = `100 + (2 * 1) = 102`

#### 3.3: Bridge SVI IP Increment (if provided)
```python
# Parse CIDR notation
if "/" in base_svi_ip:
    ip_interface = ipaddress.IPv4Interface(base_svi_ip)
    ip_address = ip_interface.ip
    prefix = ip_interface.network.prefixlen
else:
    ip_address = ipaddress.IPv4Address(base_svi_ip)
    prefix = 24  # Default prefix

# Determine octet index
octet_map = {"1st": 0, "2nd": 1, "3rd": 2, "4th": 3}
octet_index = octet_map.get(svi_ip_octet, 2)

# Increment the specified octet
ip_parts = str(ip_address).split(".")
ip_parts[octet_index] = str(int(ip_parts[octet_index]) + i)
new_ip = ".".join(ip_parts)

tunnel_config["bridge_svi_ip"] = f"{new_ip}/{prefix}"
```

**Example:**
- Base SVI IP: `10.0.0.100/24`
- SVI IP Increment: `3rd` octet
- Count: `3`
- Result:
  - Tunnel 1: SVI IP = `10.0.0.100/24` (3rd octet: 0 + 0 = 0)
  - Tunnel 2: SVI IP = `10.0.1.100/24` (3rd octet: 0 + 1 = 1)
  - Tunnel 3: SVI IP = `10.0.2.100/24` (3rd octet: 0 + 2 = 2)

**Another Example (4th octet):**
- Base SVI IP: `10.0.0.100/24`
- SVI IP Increment: `4th` octet
- Count: `3`
- Result:
  - Tunnel 1: SVI IP = `10.0.0.100/24` (4th octet: 100 + 0 = 100)
  - Tunnel 2: SVI IP = `10.0.0.101/24` (4th octet: 100 + 1 = 101)
  - Tunnel 3: SVI IP = `10.0.0.102/24` (4th octet: 100 + 2 = 102)

### Step 4: Store Each Tunnel

Each tunnel configuration is stored by calling:
```python
self._update_device_protocol(row, "VXLAN", tunnel_config)
```

**Important Note:** The current implementation calls `_update_device_protocol` for each tunnel in the loop. This method should handle merging tunnels into a `{"tunnels": [...]}` format, but there may be a bug where it overwrites the previous tunnel instead of accumulating them.

## What Does NOT Get Incremented

The following fields remain constant across all generated tunnels:

1. **Local Endpoint (Local VTEP IP)**: Same for all tunnels
2. **Remote Endpoint(s) (Remote VTEP IPs)**: Same for all tunnels
3. **UDP Port**: Same for all tunnels (default: 4789)
4. **Underlay Interface**: Same for all tunnels (inherited from device)

## Complete Example

### User Input:
- **VNI**: `5000`
- **VNI Increment**: `+100`
- **VLAN ID**: `100`
- **VLAN ID Increment**: `+1`
- **Bridge SVI IP**: `10.0.0.100/24`
- **SVI IP Increment**: `3rd` octet
- **Count**: `5`
- **Local Endpoint**: `192.255.0.1`
- **Remote Endpoint**: `192.168.250.1`
- **UDP Port**: `4789`

### Generated Tunnels:

| Tunnel | VNI | VLAN ID | Bridge SVI IP | Local VTEP | Remote VTEP |
|--------|-----|---------|---------------|------------|-------------|
| 1 | 5000 | 100 | 10.0.0.100/24 | 192.255.0.1 | 192.168.250.1 |
| 2 | 5100 | 101 | 10.0.1.100/24 | 192.255.0.1 | 192.168.250.1 |
| 3 | 5200 | 102 | 10.0.2.100/24 | 192.255.0.1 | 192.168.250.1 |
| 4 | 5300 | 103 | 10.0.3.100/24 | 192.255.0.1 | 192.168.250.1 |
| 5 | 5400 | 104 | 10.0.4.100/24 | 192.255.0.1 | 192.168.250.1 |

## Storage Format

When increment is enabled, each tunnel should be stored in the device's `vxlan_config` in the following format:

```json
{
  "tunnels": [
    {
      "vni": 5000,
      "vlan_id": 100,
      "bridge_svi_ip": "10.0.0.100/24",
      "local_ip": "192.255.0.1",
      "remote_peers": ["192.168.250.1"],
      "udp_port": 4789
    },
    {
      "vni": 5100,
      "vlan_id": 101,
      "bridge_svi_ip": "10.0.1.100/24",
      "local_ip": "192.255.0.1",
      "remote_peers": ["192.168.250.1"],
      "udp_port": 4789
    },
    ...
  ]
}
```

## Current Implementation Issue

**Bug:** The current implementation in `prompt_add_vxlan()` (lines 2247-2253) calls `_update_device_protocol()` for each tunnel in the loop. However, `_update_device_protocol()` may not properly handle accumulating multiple tunnels into the `{"tunnels": [...]}` format. Instead, it might overwrite the previous tunnel configuration.

**Expected Behavior:** All tunnels should be accumulated into a single `{"tunnels": [...]}` structure, similar to how single tunnels are handled (lines 2255-2303).

**Recommended Fix:** Instead of calling `_update_device_protocol()` for each tunnel, the increment logic should:
1. Generate all tunnel configurations in a list
2. Build a single `{"tunnels": [...]}` structure
3. Call `_update_device_protocol()` once with the complete tunnels structure

## UI Display

When multiple tunnels are configured, the VXLAN status table (`utils/devices_tab_vxlan.py`) displays each tunnel as a separate row with:
- Device name shown as: `{device_name} (Tunnel {index+1}/{total_count})`
- Each tunnel's individual VNI, VLAN ID, SVI IP, etc.

## Server-Side Processing

When the device is applied to the server (`run_tgen_server.py`), the server processes each tunnel in the `{"tunnels": [...]}` array:

1. Creates a separate bridge for each tunnel (e.g., `br5000`, `br5100`, etc.)
2. Creates a separate VXLAN interface for each tunnel (e.g., `vx5000-{hash}`, `vx5100-{hash}`, etc.)
3. Configures each tunnel's SVI IP on the appropriate VLAN subinterface
4. Sets up BGP EVPN for each VNI
5. Starts background threads to configure ARP/FDB entries for each tunnel

## Summary

- **VNI**: Increments by the selected step (1, 10, 100, or 1000) for each tunnel
- **VLAN ID**: Increments by the selected step (1, 10, or 100) for each tunnel (if provided)
- **Bridge SVI IP**: Increments the selected octet (1st, 2nd, 3rd, or 4th) by `i` for each tunnel
- **Count**: Determines how many tunnels are generated (1-100)
- **Other fields**: Remain constant across all tunnels

The increment mechanism allows users to quickly create multiple VXLAN tunnels with systematic variations in VNI, VLAN ID, and SVI IP addresses.

