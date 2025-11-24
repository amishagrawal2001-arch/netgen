# Juniper Switch VXLAN Configuration Analysis

## Juniper Switch Configuration

### Key Points from Juniper Config:

1. **VLAN-Aware Mode**: `service-type vlan-aware`
2. **VLAN Mapping**: VLAN 100 → VNI 5000
3. **Remote VTEP**: `192.255.0.1` (your OSTG device)
4. **Local VTEP**: `192.168.250.1` (Juniper switch)
5. **L3 Interface**: `irb.100` (for VLAN 100)

### Juniper Configuration Details:

```
MACVRF_VLAN_AWARE {
    instance-type mac-vrf;
    service-type vlan-aware;        ← VLAN-aware mode
    extended-vni-list 5000;
    
    vlans {
        vlan100 {
            vlan-id 100;
            l3-interface irb.100;
            vxlan {
                vni 5000;          ← VLAN 100 → VNI 5000
            }
        }
    }
}
```

## Current OSTG Configuration

### Current Status:
- **VNI**: 5000 ✓
- **VLAN Mapping**: Vlan: 0 (NO mapping) ✗
- **Mode**: VLAN-unaware (untagged) ✗
- **Bridge**: Untagged mode (VLAN 1) ✗

### The Problem:

**MISMATCH**: 
- Juniper expects: **VLAN 100 tagged traffic** → VNI 5000
- OSTG sends: **Untagged traffic** → VNI 5000

This mismatch will cause:
- Traffic not being properly forwarded
- VLAN tags not preserved
- Connectivity issues

## Solution: Configure VLAN-Aware Mode on OSTG

To match the Juniper configuration, you need to:

### Step 1: Enable VLAN-Aware Bridging

Configure the bridge to be VLAN-aware:

```bash
# Inside FRR container
docker exec -it <container_name> bash

# Enable VLAN filtering on bridge
ip link set br5000 type bridge vlan_filtering 1

# Add VLAN 100 to bridge
bridge vlan add vid 100 dev br5000 self
bridge vlan add vid 100 dev vx5000-* pvid untagged
```

### Step 2: Configure VLAN-to-VNI Mapping in FRR

```bash
vtysh

configure terminal
vxlan vlan 100 vni 5000
exit
write
```

### Step 3: Configure Interface VLAN

If you have a physical interface or veth connected to the bridge:

```bash
# Add VLAN 100 to interface
bridge vlan add vid 100 dev <interface>
```

## Complete Configuration Flow

### On OSTG Side (FRR Container):

1. **Bridge Configuration**:
   ```bash
   ip link set br5000 type bridge vlan_filtering 1
   bridge vlan add vid 100 dev br5000 self
   bridge vlan add vid 100 dev vx5000-* pvid untagged
   ```

2. **FRR Configuration**:
   ```bash
   vtysh -c "configure terminal" \
         -c "vxlan vlan 100 vni 5000" \
         -c "exit" \
         -c "write"
   ```

3. **Verify**:
   ```bash
   vtysh -c "show evpn vni detail"
   # Should show: Vlan: 100 (not 0)
   ```

### Expected Result:

After configuration:
- **VNI**: 5000
- **VLAN Mapping**: VLAN 100 → VNI 5000
- **Mode**: VLAN-aware
- **Traffic**: VLAN 100 tagged frames → VXLAN tunnel → Juniper

## Traffic Flow After Configuration

```
OSTG Device                    Juniper Switch
     |                              |
     | VLAN 100 tagged frame        |
     v                              |
[br5000] (VLAN-aware)              |
     |                              |
     | VLAN 100 preserved           |
     v                              |
[vx5000-*] (VNI 5000)              |
     |                              |
     | VXLAN Encapsulation          |
     | (VNI 5000, VLAN 100 in frame)|
     v                              |
[Underlay Network]                  |
     |=============================>|
     |                              |
     | VXLAN Decapsulation         |
     | Extract VLAN 100 frame      |
     v                              |
[Juniper VTEP]                     |
     |                              |
     | VLAN 100 → VNI 5000 mapping |
     v                              |
[vlan100] (VLAN-aware)              |
     |                              |
     | VLAN 100 tagged              |
     v                              |
[et-0/0/20.0] or [irb.100]         |
```

## Verification Commands

### On OSTG Side:

```bash
# Check EVPN VNI
vtysh -c "show evpn vni detail"
# Should show: Vlan: 100

# Check bridge VLAN
bridge vlan show br5000
# Should show VLAN 100

# Check VXLAN VLAN mapping
vtysh -c "show vxlan vlan"
# Should show: VLAN 100 → VNI 5000
```

### On Juniper Side:

```bash
# Verify remote VTEP
show ethernet-switching vxlan-tunnel-end-point remote

# Verify EVPN routes
show route table bgp.evpn.0

# Verify VLAN mapping
show routing-instances MACVRF_VLAN_AWARE vlans
```

## Important Notes

1. **VLAN ID Match**: OSTG must use VLAN 100 to match Juniper's configuration
2. **VLAN-Aware Mode**: Both sides must be in VLAN-aware mode
3. **L3 Interface**: Juniper has `irb.100` for L3 routing on VLAN 100
4. **Route Target**: Juniper uses `target:65000:5000` - ensure BGP EVPN RT matches

## Next Steps

1. Configure VLAN-aware mode on OSTG bridge
2. Map VLAN 100 to VNI 5000 in FRR
3. Ensure traffic is VLAN 100 tagged
4. Verify EVPN routes are exchanged
5. Test connectivity with VLAN 100 tagged traffic

