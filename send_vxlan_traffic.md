# How to Send Traffic Through VXLAN Tunnel

## Overview
Traffic sent to the bridge interface (`br5000`) will be automatically encapsulated by the VXLAN interface (`vx5000-e74e85`) and sent over the tunnel.

## Methods to Send VXLAN Traffic

### Method 1: Using Ping/ARPing from Bridge (Simplest)

```bash
# SSH into the server
ssh root@svl-hp-ai-srv04

# Enter the FRR container
docker exec -it ostg-frr-e74e85c5-f6e7-4582-ba0a-96b32e740753 bash

# Send ping from bridge to remote endpoint
# This will trigger MAC learning and generate EVPN routes
ping -I br5000 <remote_ip>

# Or use ARPing to trigger MAC learning
arping -I br5000 <remote_ip>

# Example: Ping a remote VTEP endpoint
ping -I br5000 192.168.250.1
```

### Method 2: Using ARPing to Trigger MAC Learning

```bash
# Inside the FRR container
arping -I br5000 -c 5 <remote_mac_or_ip>

# This will:
# 1. Send ARP requests through VXLAN tunnel
# 2. Learn remote MAC addresses
# 3. Trigger FRR to generate Type-2 EVPN routes
```

### Method 3: Using Scapy (Python)

```python
from scapy.all import *

# Create a packet with destination MAC that will be learned
pkt = Ether(dst="00:11:22:33:44:55", src="aa:bb:cc:dd:ee:ff") / \
      IP(dst="192.168.250.1", src="192.255.0.100") / \
      ICMP()

# Send through bridge interface
sendp(pkt, iface="br5000", verbose=True)
```

### Method 4: Using Traffic Generator in UI

1. **Add a Stream**:
   - Go to the Devices tab
   - Select your device
   - Add a new stream
   - Set the interface to the bridge name: `br5000`
   - Configure source/destination MACs and IPs
   - Start the stream

2. **Stream Configuration**:
   - **Interface**: `br5000` (the bridge interface)
   - **Source MAC**: Any MAC (will be learned)
   - **Destination MAC**: Remote MAC or broadcast `ff:ff:ff:ff:ff:ff`
   - **Source IP**: `192.255.0.100` (SVI IP) or any IP in the VXLAN network
   - **Destination IP**: Remote endpoint IP (e.g., `192.168.250.1`)

### Method 5: Create Test Interface on Bridge

```bash
# Inside FRR container
# Create a veth pair
ip link add veth-test type veth peer name veth-test-peer

# Add one end to the bridge
ip link set veth-test master br5000
ip link set veth-test up

# Assign IP to the test interface
ip addr add 192.255.0.200/24 dev veth-test

# Now ping from this interface
ping -I veth-test <remote_ip>
```

### Method 6: Using tcpdump to Monitor VXLAN Traffic

```bash
# Monitor VXLAN encapsulated traffic on underlay interface
tcpdump -i ens4np0 -n "udp port 4789"

# Monitor traffic on bridge
tcpdump -i br5000 -n

# Monitor VXLAN interface
tcpdump -i vx5000-e74e85 -n
```

## Important Notes

1. **Bridge Interface**: All traffic should be sent to `br5000`, not directly to `vx5000-e74e85`
2. **MAC Learning**: Traffic must flow through the tunnel to trigger MAC learning
3. **Remote VTEP**: Ensure remote VTEP is configured and reachable
4. **Multicast Group**: The VXLAN interface uses multicast group `239.0.0.155` for BUM traffic

## Verify Traffic is Being Sent

```bash
# Check bridge FDB for learned MACs
bridge fdb show br br5000

# Check VXLAN FDB
bridge fdb show dev vx5000-e74e85

# Check EVPN routes in FRR
vtysh -c "show bgp l2vpn evpn route"

# Check EVPN VNI status
vtysh -c "show evpn vni detail"
```

## Troubleshooting

1. **No traffic seen**: Check if bridge is up and VXLAN interface is attached
2. **No MAC learning**: Ensure `nolearning` flag is removed from VXLAN interface
3. **No EVPN routes**: Verify BGP EVPN is configured and VNI is recognized as L2


