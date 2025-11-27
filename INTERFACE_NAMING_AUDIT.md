# Interface Naming Audit Report

## Summary
This document provides a comprehensive audit of interface name handling across the OSTG codebase to identify and fix inconsistencies that have caused bugs.

## Issues Found and Fixed

### 1. ✅ Database Storage - Interface Names Not Normalized
**Location**: `run_tgen_server.py` lines 2911, 3057, 3159
**Issue**: Interface names were stored in database with malformed prefixes like "- ens4np0" or " - ens4np0"
**Fix**: Changed to use `interface_normalized` instead of raw `interface` when storing to database

### 2. ✅ IS-IS Configuration - Interface Name Not Normalized
**Location**: `run_tgen_server.py` lines 1406-1407, 1829-1874
**Issue**: Interface names from client requests were not normalized before use in IS-IS config
**Fix**: Added normalization function and applied it to interface names in IS-IS configure endpoint

### 3. ✅ OSPF/IS-IS Configuration - Interface Name Normalization
**Location**: `utils/ospf.py`, `utils/isis.py`
**Status**: ✅ Already fixed - Both files have normalization logic to remove "- " and " - " prefixes

### 4. ✅ VLAN Interface Naming - actual_vlan_interface Support
**Location**: `run_tgen_server.py`, `utils/ospf.py`, `utils/isis.py`
**Status**: ✅ Already implemented - Code checks for `actual_vlan_interface` in database when unique VLAN names (e.g., `vlan20-ens4np0`) are created

### 5. ✅ Interface Name Extraction from UI
**Location**: `widgets/devices_tab.py` line 7479
**Status**: ✅ Already implemented - `_normalize_iface_label()` function correctly extracts interface names from UI labels

### 6. ✅ Server Tree Interface Selection
**Location**: `traffic_client/server_section.py`, `widgets/devices_tab.py`
**Status**: ✅ Already fixed - TG ID extraction uses " - " delimiter correctly

## Normalization Functions

### Client Side
- **`_normalize_iface_label()`** in `widgets/devices_tab.py` (line 7479)
  - Removes "TG X - " prefix
  - Removes "Port:" prefix
  - Extracts base interface name

### Server Side
- **`normalize_iface()`** in `run_tgen_server.py` (line 2255, 567)
  - Removes "TG X - " prefix
  - Removes ":" prefix
  - Extracts base interface name

- **`_normalize_iface()`** in `utils/device_manager.py` (line 20)
  - Similar normalization logic
  - Handles VLAN interface naming conventions

### Protocol Configuration
- **OSPF**: `utils/ospf.py` lines 265-268, 298-301, 1008-1011
  - Normalizes interface names by removing "- " and " - " prefixes
  - Checks for `actual_vlan_interface` in database

- **IS-IS**: `utils/isis.py` lines 265-268
  - Normalizes interface names by removing "- " and " - " prefixes
  - Checks for `actual_vlan_interface` in database

## Interface Name Formats

### UI Display Format
- `"TG 0 - ens4np0"` - Full format with TG ID prefix
- `"TG 0 - Port: • ens4np0"` - Legacy format with Port prefix

### Storage Format (Database)
- `"ens4np0"` - Normalized base interface name
- `"vlan20"` - VLAN interface name
- `"vlan20-ens4np0"` - Unique VLAN interface name (when same VLAN ID used on different physical interfaces)

### Linux Kernel Format
- `"ens4np0"` - Physical interface
- `"vlan20"` - VLAN interface (Linux shows as `vlan20@ens4np0` in `ip link show`)
- `"vlan20-ens4np0"` - Unique VLAN interface name

### FRR Configuration Format
- `"ens4np0"` - Physical interface
- `"vlan20"` or `"vlan20-ens4np0"` - VLAN interface (uses `actual_vlan_interface` if available)

## Critical Code Paths

### 1. Device Apply (`/api/device/apply`)
- **Input**: `interface` from client (may have "- " prefix)
- **Normalization**: Line 2255-2265, uses `normalize_iface()` function
- **Storage**: Line 2911, 3057 - Uses `interface_normalized` ✅ FIXED
- **VLAN Handling**: Lines 2308-2451 - Creates unique names when needed, stores `actual_vlan_interface`

### 2. IS-IS Configure (`/api/device/isis/configure`)
- **Input**: `interface` from `data.get("interface")` or `isis_config.get("interface")`
- **Normalization**: Lines 1829-1874 - Added normalization ✅ FIXED
- **Storage**: Interface name normalized before use

### 3. IS-IS Start (`/api/device/isis/start`)
- **Input**: `interface` from `data.get("interface")` or database
- **Normalization**: Lines 1406-1407 - Added normalization ✅ FIXED

### 4. OSPF/IS-IS Protocol Configuration
- **Input**: Interface from `ospf_config.get("interface")` or `isis_config.get("interface")`
- **Normalization**: `utils/ospf.py` and `utils/isis.py` - Already implemented ✅
- **Database Fallback**: Both check database for `actual_vlan_interface` ✅

### 5. Database Storage
- **Location**: `utils/device_database.py` `add_device()` and `update_device()`
- **Storage**: Interface name stored as-is from `device_data.get("interface")`
- **Fix Required**: Normalization should happen BEFORE calling `add_device()` or `update_device()` ✅ FIXED in `run_tgen_server.py`

## Remaining Potential Issues

### 1. FRR Docker Interface Configuration
**Location**: `utils/frr_docker.py` line 379
**Status**: ⚠️ Needs verification
- Interface name comes from `device_config.get('interface', '')`
- Should verify that `device_config` passed to this function has normalized interface names
- Currently relies on caller to normalize (which is done in `run_tgen_server.py`)

### 2. Client-Side Interface Storage
**Location**: `widgets/devices_tab.py` - Device creation/editing
**Status**: ⚠️ Needs verification
- Interface names stored in `all_devices` may have "- " prefix
- Should verify that `_normalize_iface_label()` is called consistently

### 3. Protocol Config Interface Field
**Location**: OSPF/ISIS configs stored in database
**Status**: ✅ Fixed - Normalization happens in `utils/ospf.py` and `utils/isis.py` when reading from database

## Recommendations

1. ✅ **Database Storage**: Always normalize interface names before storing (FIXED)
2. ✅ **IS-IS Configuration**: Normalize interface names from client requests (FIXED)
3. ⚠️ **Centralized Normalization**: Consider creating a single utility function for interface normalization to ensure consistency
4. ✅ **VLAN Interface Naming**: Continue using `actual_vlan_interface` for unique VLAN names (ALREADY IMPLEMENTED)
5. ✅ **Protocol Configuration**: Continue normalizing in `utils/ospf.py` and `utils/isis.py` (ALREADY IMPLEMENTED)

## Testing Checklist

- [ ] Device creation with interface name containing "- " prefix
- [ ] Device creation with interface name containing " - " prefix
- [ ] Device creation with interface name containing "TG X - " prefix
- [ ] OSPF configuration with malformed interface names
- [ ] IS-IS configuration with malformed interface names
- [ ] VLAN interface creation with same VLAN ID on different physical interfaces
- [ ] Database retrieval and protocol configuration using `actual_vlan_interface`
- [ ] Interface name extraction from server tree selection

## Files Modified

1. `run_tgen_server.py`
   - Line 2911: Changed `"interface": interface` to `"interface": interface_normalized`
   - Line 3057: Changed `"interface": interface` to `"interface": interface_normalized`
   - Line 3159: Changed to use `interface_normalized` in update_data
   - Lines 1829-1874: Added interface normalization in IS-IS configure endpoint
   - Lines 1406-1407: Added interface normalization in IS-IS start endpoint

## Conclusion

Most interface naming issues have been addressed. The critical fixes ensure that:
1. Interface names are normalized before database storage
2. Interface names are normalized in IS-IS configuration endpoints
3. OSPF/IS-IS protocol configuration already handles normalization correctly
4. VLAN interface naming with `actual_vlan_interface` is properly supported

The codebase should now handle interface names consistently across all code paths.

