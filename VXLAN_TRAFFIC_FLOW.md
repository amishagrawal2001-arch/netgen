# VXLAN Traffic Flow Explanation

## Overview
VXLAN (Virtual eXtensible LAN) is a network virtualization technology that creates Layer 2 overlay networks over a Layer 3 underlay network. This document explains how traffic flows through VXLAN tunnels in the OSTG system.

## Key Components

### 1. **VTEP (VXLAN Tunnel End Point)**
- **Local VTEP**: The local device's IP address (e.g., `192.255.0.1`)
- **Remote VTEP**: The remote device's IP address (e.g., `192.168.250.1`)
- VTEPs are the endpoints that encapsulate/decapsulate VXLAN traffic

### 2. **VNI (VXLAN Network Identifier)**
- A 24-bit identifier (e.g., 5000) that identifies a specific VXLAN segment
- Multiple VNIs can exist on the same VTEP

### 3. **Bridge Interface**
- Linux bridge (e.g., `br5000`) that connects:
  - VXLAN interface (`vx5000-e74e85`)
  - veth interfaces (for local traffic)
  - Other interfaces as needed

### 4. **VXLAN Interface**
- The tunnel interface (e.g., `vx5000-e74e85`) that performs encapsulation/decapsulation

## Traffic Flow Scenarios

### Scenario 1: Local to Remote (Unicast Traffic)

```
[Local Device]                    [Remote Device]
     |                                  |
     | 1. Application sends packet      |
     |    (Ethernet frame)              |
     |                                  |
     v                                  |
[br5000]                               |
(Bridge)                               |
     |                                  |
     | 2. Bridge forwards to VXLAN     |
     |    interface                     |
     |                                  |
     v                                  |
[vx5000-e74e85]                        |
(VXLAN Interface)                      |
     |                                  |
     | 3. VXLAN Encapsulation:          |
     |    - Original Ethernet frame    |
     |    - VXLAN header (VNI=5000)    |
     |    - UDP header (port 4789)      |
     |    - IP header (src: 192.255.0.1 |
     |                  dst: 192.168.250.1)
     |    - Ethernet header (underlay)  |
     |                                  |
     v                                  |
[ens4np0]                              |
(Underlay Interface)                   |
     |                                  |
     | 4. Packet sent over physical     |
     |    network                       |
     |                                  |
     |=================================>|
     |                                  |
     | 5. Packet received on remote     |
     |    underlay interface            |
     |                                  |
     v                                  |
[Remote VXLAN Interface]               |
     |                                  |
     | 6. VXLAN Decapsulation:         |
     |    - Remove outer headers        |
     |    - Extract original frame      |
     |                                  |
     v                                  |
[Remote Bridge]                        |
     |                                  |
     | 7. Bridge forwards to            |
     |    destination                   |
     |                                  |
     v                                  |
[Remote Application]                  |
```

### Scenario 2: Broadcast/Unknown Unicast/Multicast (BUM) Traffic

```
[Local Device]                    [Remote Device 1]    [Remote Device 2]
     |                                  |                      |
     | 1. Broadcast/ARP request        |                      |
     |                                  |                      |
     v                                  |                      |
[br5000]                               |                      |
(Bridge)                               |                      |
     |                                  |                      |
     | 2. Bridge floods to VXLAN        |                      |
     |                                  |                      |
     v                                  |                      |
[vx5000-e74e85]                        |                      |
(VXLAN Interface)                      |                      |
     |                                  |                      |
     | 3. VXLAN Encapsulation with      |                      |
     |    Multicast Group (239.0.0.155) |                      |
     |                                  |                      |
     v                                  |                      |
[ens4np0]                              |                      |
     |                                  |                      |
     | 4. Multicast replication         |                      |
     |    (handled by underlay)         |                      |
     |                                  |                      |
     |===========>|                      |                      |
     |            |===========>|         |                      |
     |            |            |         |                      |
     | 5. Each remote VTEP receives     |                      |
     |    and decapsulates              |                      |
     |                                  |                      |
     v                                  v                      v
[Remote Bridges]                       |                      |
     |                                  |                      |
     | 6. Each bridge floods to         |                      |
     |    local interfaces              |                      |
     |                                  |                      |
     v                                  v                      v
[Remote Applications]                  |                      |
```

## Detailed Encapsulation Process

### Step-by-Step Encapsulation (Local → Remote)

1. **Application Layer**
   ```
   Source MAC: aa:bb:cc:dd:ee:ff
   Dest MAC:   11:22:33:44:55:66
   Source IP:  192.255.0.100
   Dest IP:    192.255.0.200
   Payload:    [Application Data]
   ```

2. **Bridge Layer (br5000)**
   - Bridge receives the Ethernet frame
   - Checks FDB (Forwarding Database) for destination MAC
   - If MAC is learned on VXLAN interface, forwards to VXLAN interface
   - If MAC is unknown, floods to all bridge ports (including VXLAN)

3. **VXLAN Encapsulation**
   ```
   Original Frame:
   ┌─────────────────────────────────┐
   │ Ethernet Header                  │
   │   Src MAC: aa:bb:cc:dd:ee:ff    │
   │   Dst MAC: 11:22:33:44:55:66    │
   │   Type: 0x0800 (IPv4)           │
   ├─────────────────────────────────┤
   │ IP Header                        │
   │   Src IP: 192.255.0.100         │
   │   Dst IP: 192.255.0.200         │
   ├─────────────────────────────────┤
   │ Payload                          │
   └─────────────────────────────────┘
   
   After VXLAN Encapsulation:
   ┌─────────────────────────────────┐
   │ Outer Ethernet Header           │
   │   Src MAC: [Local VTEP MAC]    │
   │   Dst MAC: [Remote VTEP MAC]   │
   │   Type: 0x0800 (IPv4)           │
   ├─────────────────────────────────┤
   │ Outer IP Header                 │
   │   Src IP: 192.255.0.1 (VTEP)   │
   │   Dst IP: 192.168.250.1 (VTEP) │
   │   Protocol: UDP (17)            │
   ├─────────────────────────────────┤
   │ UDP Header                      │
   │   Src Port: [random]            │
   │   Dst Port: 4789 (VXLAN)        │
   ├─────────────────────────────────┤
   │ VXLAN Header                     │
   │   Flags: 0x08                    │
   │   Reserved: 0x000000             │
   │   VNI: 5000 (24 bits)            │
   │   Reserved: 0x00                 │
   ├─────────────────────────────────┤
   │ Original Ethernet Frame          │
   │   (unchanged)                    │
   └─────────────────────────────────┘
   ```

4. **Underlay Transmission**
   - Encapsulated packet is sent over the physical network
   - Routed based on outer IP header (underlay routing)
   - Reaches remote VTEP

5. **VXLAN Decapsulation (Remote)**
   - Remote VTEP receives packet on UDP port 4789
   - Checks VNI (5000) to determine which VXLAN interface
   - Removes outer headers (Ethernet, IP, UDP, VXLAN)
   - Forwards original frame to bridge

6. **Bridge Forwarding (Remote)**
   - Bridge receives original Ethernet frame
   - Checks FDB for destination MAC
   - Forwards to appropriate interface or floods if unknown

## MAC Learning Process

### How MAC Addresses are Learned

1. **Initial State**
   - Bridge FDB is empty
   - No MAC addresses learned

2. **First Packet (Unknown Destination)**
   - Bridge floods packet to all ports (including VXLAN)
   - VXLAN interface encapsulates and sends to remote VTEP
   - Remote VTEP decapsulates and floods locally
   - Remote device responds

3. **Response Packet**
   - Remote device sends response
   - Remote bridge learns source MAC → VXLAN interface mapping
   - Response is encapsulated and sent back
   - Local bridge learns source MAC → VXLAN interface mapping

4. **Subsequent Packets**
   - Bridge uses learned MAC addresses
   - Unicast traffic sent directly to learned port
   - No flooding needed for known destinations

## EVPN Route Types and Traffic Flow

### Type-2 Routes (MAC/IP Advertisement)
- Advertise learned MAC addresses
- Used for unicast traffic forwarding
- Example: `MAC 11:22:33:44:55:66 → VTEP 192.168.250.1`

### Type-3 Routes (Inclusive Multicast Ethernet Tag - IMET)
- Advertise VTEP IP for BUM traffic
- Used for broadcast/unknown unicast/multicast replication
- Example: `VNI 5000 → VTEP 192.255.0.1 → Multicast 239.0.0.155`

## Traffic Flow Examples

### Example 1: Ping from Local to Remote

```bash
# On local device
ping -I br5000 192.255.0.200

# Flow:
1. ICMP packet created with:
   - Src IP: 192.255.0.100 (SVI IP on br5000)
   - Dst IP: 192.255.0.200 (remote device)

2. Bridge (br5000) receives packet
   - Checks FDB for destination MAC
   - If unknown, floods to VXLAN interface

3. VXLAN interface (vx5000-e74e85) encapsulates:
   - Original: Ethernet/IP/ICMP
   - Adds: VXLAN header (VNI=5000)
   - Adds: UDP header (port 4789)
   - Adds: Outer IP (src: 192.255.0.1, dst: 192.168.250.1)
   - Adds: Outer Ethernet

4. Packet sent over underlay (ens4np0)

5. Remote VTEP receives and decapsulates

6. Remote bridge forwards to destination

7. Response follows reverse path
```

### Example 2: ARP Request

```bash
# ARP request for unknown MAC
arping -I br5000 192.255.0.200

# Flow:
1. ARP request broadcast on br5000

2. Bridge floods to all ports (including VXLAN)

3. VXLAN encapsulates with multicast group (239.0.0.155)

4. Multicast replication in underlay

5. All remote VTEPs receive and decapsulate

6. Each remote bridge floods locally

7. Target device responds

8. Response is unicast back to source
```

## Key Points

1. **Encapsulation Overhead**
   - Original frame: ~64-1500 bytes
   - Encapsulated: +50 bytes (VXLAN + UDP + IP + Ethernet headers)

2. **MTU Considerations**
   - VXLAN interface MTU: 1450 (to account for encapsulation)
   - Underlay MTU: 1500 (standard Ethernet)
   - Total: 1450 + 50 = 1500 bytes

3. **MAC Learning**
   - Happens automatically when traffic flows
   - Stored in bridge FDB
   - Advertised via EVPN Type-2 routes

4. **BUM Traffic**
   - Uses multicast group for replication
   - All remote VTEPs receive broadcast/unknown unicast
   - Each VTEP floods locally

5. **Unicast Traffic**
   - Uses learned MAC addresses
   - Direct forwarding to specific VTEP
   - More efficient than flooding

## Troubleshooting Traffic Flow

### Check Bridge FDB
```bash
bridge fdb show br br5000
```

### Check VXLAN Interface
```bash
ip -d link show vx5000-e74e85
```

### Monitor VXLAN Traffic
```bash
# On underlay interface
tcpdump -i ens4np0 -n "udp port 4789"

# On bridge
tcpdump -i br5000 -n

# On VXLAN interface
tcpdump -i vx5000-e74e85 -n
```

### Check EVPN Routes
```bash
vtysh -c "show bgp l2vpn evpn route"
vtysh -c "show evpn vni detail"
```

