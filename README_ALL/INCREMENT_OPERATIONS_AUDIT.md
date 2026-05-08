# Increment Operations Audit

## Summary
This document audits all increment operations in the OSTG client and server to ensure they are handled correctly.

## Issues Found and Fixed

### ✅ Issue 1: MAC Decrement Mode Not Handled
**Status**: FIXED

**Problem**: 
- Client UI supports "Decrement" mode for MAC source and destination addresses
- Server only checked for "Increment" mode, ignoring "Decrement" mode
- When "Decrement" was selected, MAC addresses would remain fixed

**Fix**: 
- Updated `utils/generic.py` to check for both "Increment" and "Decrement" modes
- For "Decrement" mode, the step is negated before applying to the increment function
- The `increment_mac()` function already handles negative steps correctly (uses addition with negative step)

**Files Modified**:
- `utils/generic.py` (lines 257-274)

## Verified Increment Operations

### ✅ MAC Address Increments
- **Source MAC**: ✅ Increment, ✅ Decrement, ✅ Fixed, ✅ Resolve (handled separately)
- **Destination MAC**: ✅ Increment, ✅ Decrement, ✅ Fixed
- **Field Names**: `mac_source_mode`, `mac_source_step`, `mac_source_count`, `mac_destination_mode`, `mac_destination_step`, `mac_destination_count`
- **Server Handling**: ✅ Fixed in this audit

### ✅ IPv4 Address Increments
- **Source IPv4**: ✅ Increment, ✅ Fixed
- **Destination IPv4**: ✅ Increment, ✅ Fixed
- **Field Names**: `ipv4_source_mode`, `ipv4_source_increment_step`, `ipv4_source_increment_count`, `ipv4_destination_mode`, `ipv4_destination_increment_step`, `ipv4_destination_increment_count`
- **Server Handling**: ✅ Working correctly

### ✅ IPv6 Address Increments
- **Source IPv6**: ✅ Increment, ✅ Fixed
- **Destination IPv6**: ✅ Increment, ✅ Fixed
- **Field Names**: `ipv6_source_mode`, `ipv6_source_increment_step`, `ipv6_source_increment_count`, `ipv6_destination_mode`, `ipv6_destination_increment_step`, `ipv6_destination_increment_count`
- **Server Handling**: ✅ Working correctly

### ✅ VLAN ID Increments
- **VLAN ID**: ✅ Increment (checkbox), ✅ Fixed
- **Field Names**: `vlan_increment`, `vlan_increment_value`, `vlan_increment_count`
- **Server Handling**: ✅ Working correctly

### ✅ TCP Port Increments
- **Source Port**: ✅ Increment (checkbox), ✅ Fixed
- **Destination Port**: ✅ Increment (checkbox), ✅ Fixed
- **Field Names**: `tcp_increment_source_port`, `tcp_source_port_step`, `tcp_source_port_count`, `tcp_increment_destination_port`, `tcp_destination_port_step`, `tcp_destination_port_count`
- **Server Handling**: ✅ Working correctly

### ✅ TCP Sequence Number Increments
- **Sequence Number**: ⚠️ Server supports increment but client UI doesn't expose it
- **Field Names**: `tcp_sequence_count`, `tcp_sequence_step` (server expects these but client doesn't send)
- **Server Handling**: ✅ Code exists but not exposed in UI
- **Note**: This is an optional feature. If needed, UI controls can be added later.

### ✅ UDP Port Increments
- **Source Port**: ✅ Increment (checkbox), ✅ Fixed
- **Destination Port**: ✅ Increment (checkbox), ✅ Fixed
- **Field Names**: `udp_increment_source_port`, `udp_source_port_step`, `udp_source_port_count`, `udp_increment_destination_port`, `udp_destination_port_step`, `udp_destination_port_count`
- **Server Handling**: ✅ Working correctly

## Field Name Mapping

### Client → Server Field Names

| Client Field | Server Field | Status |
|-------------|--------------|--------|
| `mac_source_mode` | `mac_source_mode` | ✅ Match |
| `mac_source_step` | `mac_source_step` | ✅ Match |
| `mac_source_count` | `mac_source_count` | ✅ Match |
| `mac_destination_mode` | `mac_destination_mode` | ✅ Match |
| `mac_destination_step` | `mac_destination_step` | ✅ Match |
| `mac_destination_count` | `mac_destination_count` | ✅ Match |
| `ipv4_source_mode` | `ipv4_source_mode` | ✅ Match |
| `ipv4_source_increment_step` | `ipv4_source_increment_step` | ✅ Match |
| `ipv4_source_increment_count` | `ipv4_source_increment_count` | ✅ Match |
| `ipv4_destination_mode` | `ipv4_destination_mode` | ✅ Match |
| `ipv4_destination_increment_step` | `ipv4_destination_increment_step` | ✅ Match |
| `ipv4_destination_increment_count` | `ipv4_destination_increment_count` | ✅ Match |
| `ipv6_source_mode` | `ipv6_source_mode` | ✅ Match |
| `ipv6_source_increment_step` | `ipv6_source_increment_step` | ✅ Match |
| `ipv6_source_increment_count` | `ipv6_source_increment_count` | ✅ Match |
| `ipv6_destination_mode` | `ipv6_destination_mode` | ✅ Match |
| `ipv6_destination_increment_step` | `ipv6_destination_increment_step` | ✅ Match |
| `ipv6_destination_increment_count` | `ipv6_destination_increment_count` | ✅ Match |
| `vlan_increment` | `vlan_increment` | ✅ Match |
| `vlan_increment_value` | `vlan_increment_value` | ✅ Match |
| `vlan_increment_count` | `vlan_increment_count` | ✅ Match |
| `tcp_increment_source_port` | `tcp_increment_source_port` | ✅ Match |
| `tcp_source_port_step` | `tcp_source_port_step` | ✅ Match |
| `tcp_source_port_count` | `tcp_source_port_count` | ✅ Match |
| `tcp_increment_destination_port` | `tcp_increment_destination_port` | ✅ Match |
| `tcp_destination_port_step` | `tcp_destination_port_step` | ✅ Match |
| `tcp_destination_port_count` | `tcp_destination_port_count` | ✅ Match |
| `udp_increment_source_port` | `udp_increment_source_port` | ✅ Match |
| `udp_source_port_step` | `udp_source_port_step` | ✅ Match |
| `udp_source_port_count` | `udp_source_port_count` | ✅ Match |
| `udp_increment_destination_port` | `udp_increment_destination_port` | ✅ Match |
| `udp_destination_port_step` | `udp_destination_port_step` | ✅ Match |
| `udp_destination_port_count` | `udp_destination_port_count` | ✅ Match |

## Implementation Details

### MAC Address Increment/Decrement
- **Helper Function**: `utils/helpers.py::increment_mac(mac_str, step=1)`
- **Logic**: Converts MAC to integer, adds step, wraps at 48-bit boundary using `& 0xFFFFFFFFFFFF`
- **Decrement Support**: Uses negative step (e.g., `step=-1` for decrement)

### IPv4 Address Increment
- **Helper Function**: `utils/helpers.py::increment_ip(ip_str, step=1)`
- **Logic**: Uses `ipaddress.IPv4Address` which handles overflow automatically

### IPv6 Address Increment
- **Helper Function**: `utils/helpers.py::increment_ipv6(ip_str, step=1)`
- **Logic**: Uses `ipaddress.IPv6Address` which handles overflow automatically

### VLAN ID Increment
- **Logic**: Direct integer addition: `vlan_id + i * vlan_step` for `i in range(count)`
- **Validation**: VLAN IDs must be 1-4094

### TCP/UDP Port Increments
- **Logic**: Direct integer addition: `port + step * i` for `i in range(count)`
- **Validation**: Ports must be 0-65535

## Testing Recommendations

1. **MAC Decrement**: Test with base MAC `00:00:00:00:00:10`, step=1, count=5 → should generate: `10, 0F, 0E, 0D, 0C`
2. **MAC Increment**: Test with base MAC `00:00:00:00:00:01`, step=1, count=5 → should generate: `01, 02, 03, 04, 05`
3. **IPv4 Increment**: Test with base IP `10.0.0.1`, step=1, count=5 → should generate: `10.0.0.1, 10.0.0.2, 10.0.0.3, 10.0.0.4, 10.0.0.5`
4. **IPv6 Increment**: Test with base IP `2001:db8::1`, step=1, count=5 → should generate: `2001:db8::1, 2001:db8::2, 2001:db8::3, 2001:db8::4, 2001:db8::5`
5. **VLAN Increment**: Test with base VLAN 100, step=10, count=5 → should generate: `100, 110, 120, 130, 140`
6. **Port Increments**: Test TCP/UDP source/destination port increments with various steps and counts

## Conclusion

All increment operations are now correctly handled by both client and server, with the exception of TCP sequence number increment which is not exposed in the UI (but server code exists for it). The MAC Decrement mode bug has been fixed.


