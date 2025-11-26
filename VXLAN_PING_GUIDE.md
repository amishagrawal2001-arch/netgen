# VXLAN End-to-End Ping Guide

## Current Configuration

Based on your setup:
- **Bridge**: `br5000`
- **SVI IP**: `192.255.0.100/24` (configured for FRR L2 VNI recognition)
- **Local VTEP**: `192.255.0.1` (underlay IP)
- **Remote VTEP**: `192.168.250.1` (remote underlay IP)
- **VNI**: `5000`
- **VXLAN Network**: `192.255.0.0/24` (overlay network)

## IP Addresses for End-to-End Ping

### Option 1: Use SVI IP (Already Configured)

The bridge already has an SVI IP: **`192.255.0.100/24`**

```bash
# Inside FRR container
docker exec -it <container_name> bash

# Ping from bridge SVI IP
ping -I br5000 192.255.0.200  # If remote has 192.255.0.200
```

**Note**: The SVI IP (`192.255.0.100`) is primarily for FRR to recognize the VNI as L2. You can ping from it, but you need a remote device with an IP in the same VXLAN network.

### Option 2: Create Test Interface on Bridge

Create a veth pair and assign an IP in the VXLAN network:

```bash
# Inside FRR container
docker exec -it <container_name> bash

# Create veth pair
ip link add veth-test type veth peer name veth-test-peer

# Add one end to bridge
ip link set veth-test master br5000
ip link set veth-test up

# Assign IP in VXLAN network (192.255.0.0/24)
ip addr add 192.255.0.10/24 dev veth-test

# Now ping remote device
ping -I veth-test 192.255.0.20  # Remote device IP in VXLAN network
```

### Option 3: Ping Remote VTEP's Bridge IP

If the remote device also has a bridge with SVI IP:

```bash
# Local device
ping -I br5000 192.255.0.100  # Remote bridge SVI IP (if configured)

# Or if remote has different SVI IP
ping -I br5000 192.255.0.101  # Remote bridge SVI IP
```

## Setting Up End-to-End Connectivity

### On Local Device (Current Setup)

```bash
# Bridge already configured with:
# - IP: 192.255.0.100/24
# - VXLAN interface attached
# - Ready for traffic
```

### On Remote Device (Required)

The remote device needs:

1. **VXLAN Configuration**:
   - VNI: 5000 (same as local)
   - Local VTEP: 192.168.250.1
   - Remote VTEP: 192.255.0.1 (your local VTEP)

2. **Bridge Configuration**:
   - Bridge: `br5000`
   - SVI IP: `192.255.0.101/24` (different from local, but same subnet)

3. **Test Interface** (optional):
   ```bash
   # On remote device
   ip link add veth-remote type veth peer name veth-remote-peer
   ip link set veth-remote master br5000
   ip link set veth-remote up
   ip addr add 192.255.0.20/24 dev veth-remote
   ```

## Testing End-to-End Connectivity

### Test 1: Ping Remote Bridge SVI IP

```bash
# On local device (inside container)
ping -I br5000 192.255.0.101
```

### Test 2: Ping Remote Test Interface

```bash
# On local device
ping -I br5000 192.255.0.20
```

### Test 3: Ping from Test Interface

```bash
# Create test interface on local
ip link add veth-local type veth peer name veth-local-peer
ip link set veth-local master br5000
ip link set veth-local up
ip addr add 192.255.0.10/24 dev veth-local

# Ping remote
ping -I veth-local 192.255.0.20
```

## Important Notes

1. **VXLAN Network**: All IPs must be in the same overlay network (`192.255.0.0/24`)

2. **VTEP IPs**: These are underlay IPs, NOT overlay IPs:
   - Local VTEP: `192.255.0.1` (underlay)
   - Remote VTEP: `192.168.250.1` (underlay)
   - **You cannot ping VTEP IPs over VXLAN** - they're for encapsulation only

3. **Overlay IPs**: These are the IPs you ping:
   - Bridge SVI: `192.255.0.100/24` (overlay)
   - Test interfaces: `192.255.0.10/24`, `192.255.0.20/24` (overlay)

4. **MAC Learning**: First ping will flood (unknown destination), subsequent pings will be unicast

## Quick Test Script

```bash
#!/bin/bash
# Test VXLAN connectivity

CONTAINER="ostg-frr-<device_id>"
BRIDGE="br5000"
LOCAL_IP="192.255.0.10"
REMOTE_IP="192.255.0.20"

# Create test interface
docker exec $CONTAINER ip link add veth-test type veth peer name veth-test-peer
docker exec $CONTAINER ip link set veth-test master $BRIDGE
docker exec $CONTAINER ip link set veth-test up
docker exec $CONTAINER ip addr add ${LOCAL_IP}/24 dev veth-test

# Ping remote
echo "Pinging $REMOTE_IP from $LOCAL_IP over VXLAN..."
docker exec $CONTAINER ping -I veth-test -c 5 $REMOTE_IP

# Check learned MACs
echo "Learned MAC addresses:"
docker exec $CONTAINER bridge fdb show br $BRIDGE | grep -v "self\|permanent"
```

## Troubleshooting

### No Response to Ping

1. **Check remote device exists**:
   ```bash
   # Verify remote VTEP is reachable (underlay)
   ping 192.168.250.1
   ```

2. **Check VXLAN interface is up**:
   ```bash
   ip link show vx5000-*
   ```

3. **Check bridge is up**:
   ```bash
   ip link show br5000
   ```

4. **Check EVPN routes**:
   ```bash
   vtysh -c "show bgp l2vpn evpn route"
   ```

5. **Monitor VXLAN traffic**:
   ```bash
   tcpdump -i ens4np0 -n "udp port 4789"
   ```

### ARP Resolution

If ping fails, try ARPing first:
```bash
arping -I br5000 192.255.0.20
```

This will trigger MAC learning and ARP resolution.


