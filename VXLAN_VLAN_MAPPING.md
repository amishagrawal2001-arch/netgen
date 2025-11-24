# VXLAN VLAN Mapping Explanation

## Current Configuration

Based on your setup:

- **Device VLAN**: `20`
- **VXLAN VNI**: `5000`
- **Overlay Interface**: `vlan20` (interface name)
- **EVPN VNI VLAN Mapping**: `Vlan: 0` (no mapping)

## Answer: No VLAN Mapping Currently Configured

**The VXLAN VNI 5000 is NOT mapped to any VLAN ID.**

From the EVPN output:
```
VNI: 5000
 Type: L2
 Vlan: 0          ← This means NO VLAN mapping
 Bridge: br5000
```

## What This Means

### Current Setup (VLAN-Unaware Bridging)

- **Bridge Mode**: Untagged (VLAN 1, PVID)
- **VXLAN Operation**: All traffic is untagged
- **VLAN-to-VNI Mapping**: None (Vlan: 0)

The bridge `br5000` operates in **VLAN-unaware mode**, meaning:
- All traffic is treated as untagged
- No VLAN tags are preserved or mapped
- The VXLAN tunnel carries untagged Ethernet frames

### Bridge VLAN Status

```
br5000            1 PVID Egress Untagged
vx5000-f4f3bd     1 PVID Egress Untagged
```

All interfaces are on **VLAN 1** (untagged).

## If You Want VLAN-to-VNI Mapping

To map VLAN 20 to VNI 5000, you would need to configure **VLAN-aware bridging**:

### Option 1: Configure in FRR (VLAN-aware mode)

```bash
# Inside FRR container
vtysh

# Configure VLAN-to-VNI mapping
configure terminal
vxlan vlan 20 vni 5000
exit
write
```

This would:
- Map VLAN 20 → VNI 5000
- Enable VLAN-aware bridging
- Preserve VLAN tags across the VXLAN tunnel

### Option 2: Configure Bridge VLAN Filtering

```bash
# Enable VLAN filtering on bridge
ip link set br5000 type bridge vlan_filtering 1

# Add VLAN 20 to bridge
bridge vlan add vid 20 dev br5000 self
bridge vlan add vid 20 dev vx5000-f4f3bd

# Configure VXLAN to map VLAN 20
# (This requires FRR configuration)
```

## Current vs. VLAN-Aware Comparison

### Current (VLAN-Unaware)
```
Device VLAN 20 → Bridge (untagged) → VXLAN VNI 5000 (untagged) → Remote (untagged)
```

### With VLAN Mapping (VLAN-Aware)
```
Device VLAN 20 → Bridge (VLAN 20 tagged) → VXLAN VNI 5000 (VLAN 20 preserved) → Remote (VLAN 20)
```

## Summary

**Question**: What VLAN ID is VXLAN mapped to?

**Answer**: 
- **Currently: No VLAN mapping (Vlan: 0)**
- The VXLAN VNI 5000 operates in **VLAN-unaware mode**
- All traffic is **untagged** (treated as VLAN 1)
- The device has VLAN 20 configured, but it's **not mapped** to VNI 5000

If you need VLAN-to-VNI mapping, you would need to:
1. Configure VLAN-aware bridging on the bridge
2. Configure the VLAN-to-VNI mapping in FRR
3. Ensure VLAN tags are preserved across the tunnel

