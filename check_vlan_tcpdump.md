# How to Check VLAN Tags in tcpdump

## Your Packet Analysis

Looking at your hex dump:
```
0x0000:  4500 006e 0000 0000 4001 658d 0a00 0001
```

**Analysis:**
- `45` = IPv4 header (version 4, header length 5 = 20 bytes)
- This means the packet starts directly with IP header
- **No VLAN tag present** - if VLAN was present, you'd see `8100` (VLAN EtherType) before the IP header

## Standard Ethernet Frame Structure

### Without VLAN:
```
[6 bytes: Dest MAC] [6 bytes: Src MAC] [2 bytes: EtherType] [IP Header...]
```

### With VLAN Tag (802.1Q):
```
[6 bytes: Dest MAC] [6 bytes: Src MAC] [2 bytes: 0x8100] [2 bytes: TCI] [2 bytes: EtherType] [IP Header...]
```

Where TCI (Tag Control Information) contains:
- Bits 0-11: VLAN ID
- Bits 12-14: Priority (PCP)
- Bit 15: DEI/CFI

## tcpdump Commands to Check VLAN

### 1. Show Ethernet Headers (Best Method)
```bash
tcpdump -i <interface> -e
```
**Output with VLAN:**
```
06:55:37.056576 00:11:22:33:44:55 > aa:bb:cc:dd:ee:ff, ethertype 802.1Q (0x8100), length 90: vlan 20, p 0, ethertype IPv4, IP ...
```

**Output without VLAN:**
```
06:55:37.056576 00:11:22:33:44:55 > aa:bb:cc:dd:ee:ff, ethertype IPv4 (0x0800), length 90: IP ...
```

### 2. Filter Only VLAN Packets
```bash
tcpdump -i <interface> vlan
```

### 3. Filter Specific VLAN ID
```bash
tcpdump -i <interface> vlan <vlan_id>
# Example: tcpdump -i ens5np0 vlan 20
```

### 4. Show VLAN Info Verbosely
```bash
tcpdump -i <interface> -e -v vlan
```

### 5. Capture and Save to File
```bash
tcpdump -i <interface> -e -w capture.pcap
# Then analyze with: tcpdump -r capture.pcap -e
```

## Reading Hex Dump for VLAN

### Example with VLAN Tag:
```
0x0000:  aabb ccdd eeff 0011 2233 4455 8100 0014
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
         Dest MAC (6)    Src MAC (6)    VLAN Tag
         
0x0010:  0800 4500 006e ...
         ^^^^ ^^^^
         IPv4  IP Header
```

**Breakdown:**
- `aabb ccdd eeff` = Destination MAC
- `0011 2233 4455` = Source MAC  
- `8100` = VLAN EtherType (0x8100)
- `0014` = TCI (VLAN ID = 0x014 = 20, PCP = 0, DEI = 0)
- `0800` = IPv4 EtherType
- `4500...` = IPv4 header

### Your Packet (No VLAN):
```
0x0000:  [MAC addresses not shown] 4500 006e ...
         ^^^^
         IPv4 header directly (no VLAN tag)
```

## Quick Check Script

```bash
#!/bin/bash
# Check if packets on interface have VLAN tags

INTERFACE=$1
if [ -z "$INTERFACE" ]; then
    echo "Usage: $0 <interface>"
    exit 1
fi

echo "Checking for VLAN tags on $INTERFACE..."
echo "Press Ctrl+C to stop"
echo ""

tcpdump -i $INTERFACE -c 10 -e 2>/dev/null | while read line; do
    if echo "$line" | grep -q "vlan"; then
        echo "✓ VLAN detected: $line"
    else
        echo "✗ No VLAN: $line"
    fi
done
```

## For Your Specific Case

To check if your outgoing packets have VLAN tags:

```bash
# On the TX interface (where packets are sent)
tcpdump -i ens5np0 -e -c 5

# On the RX interface (where packets are received, if flow tracking)
tcpdump -i ens4np0 -e -c 5

# Filter for ICMP packets with VLAN
tcpdump -i <interface> -e 'icmp and vlan'
```

## Common Issues

1. **VLAN tag removed by kernel**: Some interfaces strip VLAN tags before tcpdump sees them
   - Solution: Use `tcpdump -i <interface> -e` on the physical interface, or check VLAN sub-interface

2. **VLAN sub-interface**: If using VLAN sub-interface (e.g., `ens5np0.20`), packets won't show VLAN tag
   - Solution: Capture on parent interface to see tags

3. **Hardware offload**: Some NICs strip VLAN tags in hardware
   - Solution: Disable offload: `ethtool -K <interface> rx-vlan-offload off`

## Verification Commands

```bash
# Check interface VLAN configuration
ip link show <interface>

# Check VLAN sub-interfaces
ip link show type vlan

# Capture on parent interface to see tags
tcpdump -i <parent_interface> -e

# Capture on VLAN sub-interface (tags already stripped)
tcpdump -i <parent_interface>.<vlan_id> -e
```

