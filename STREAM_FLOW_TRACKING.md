# Stream Flow Tracking - How It Works

## Overview

Stream flow tracking is a mechanism that allows the traffic generator to track both **sent (TX)** and **received (RX)** packets for a stream, enabling loss percentage calculation and packet-level statistics. This document explains how the system identifies and matches packets.

---

## Architecture

### Components

1. **TX Side (Packet Generation)**
   - Located in `generate_packets()` function (`multithreaded_traffic_gen.py`)
   - Generates packets and embeds unique signatures when flow tracking is enabled
   - Increments TX counter for each packet sent

2. **RX Side (Packet Reception)**
   - Located in `start_rx_counter()` function (`multithreaded_traffic_gen.py`)
   - Runs a background sniffer thread on the RX interface
   - Matches received packets using two methods: **signature matching** (primary) and **tuple matching** (fallback)
   - Increments RX counter for matched packets

3. **Stream Tracker**
   - Central registry (`StreamTracker` class) that maintains TX/RX counts per stream
   - Thread-safe counter updates
   - Provides statistics to the UI

---

## Packet Matching Mechanisms

The system uses **two complementary methods** to match packets:

### 1. Signature Matching (Primary Method) - Scapy Path Only

**How it works:**
- When flow tracking is enabled and using Scapy (not DPDK), each TX packet is embedded with a unique signature before transmission
- The signature format is: `[{stream_id}#{sequence_number}]`
  - Example: `[13943fd1-6642-4c5b-9f67-97f491a0ff8d#1234]`
- The signature is embedded in the packet payload:
  - For UDP packets: Prepended to the UDP payload
  - For other packets: Appended to the Raw layer payload

**TX Side (Packet Embedding):**
```python
# In generate_packets(), for each packet:
def add_sig(pkt):
    if flow_tracking_enabled:
        return _append_sig_with_seq(pkt, stream_id, seq)
    return pkt
```

**RX Side (Signature Detection):**
```python
# In start_rx_counter(), the sniffer checks:
def _sig_present(pkt) -> bool:
    # Check Raw layer payload for signature
    if Raw in pkt:
        if sig_prefix in bytes(pkt[Raw].load):
            return True
    # Fallback: check raw frame bytes
    raw_frame = bytes(pkt)
    return sig_prefix in raw_frame
```

**Advantages:**
- ✅ **100% accurate** - Unique per packet, no false positives
- ✅ **Works across NAT/translation** - Signature survives packet modifications
- ✅ **Works with any protocol** - Independent of L3/L4 headers

**Limitations:**
- ❌ Only works with Scapy backend (not DPDK)
- ❌ Requires packet payload modification (may affect some protocols)

---

### 2. Tuple Matching (Fallback Method)

**How it works:**
- If signature matching fails (or not available), the system falls back to matching packets based on **network tuple** (L2/L3/L4 headers)
- The tuple includes:
  - **L2**: Source MAC, Destination MAC, VLAN ID
  - **L3**: Source IP, Destination IP (IPv4 or IPv6)
  - **L4**: Protocol (UDP/TCP/ICMP), Source Port, Destination Port

**Matching Logic:**
```python
def _tuple_match(pkt) -> bool:
    # 1. Check VLAN (if configured)
    if vlan_id configured:
        if packet VLAN != expected VLAN:
            return False
    
    # 2. Check MAC addresses (if enforced)
    if enforce_mac:
        if MACs don't match (forward or reverse):
            return False
    
    # 3. Check IP addresses (IPv4 or IPv6)
    if IP addresses configured:
        if IPs don't match (forward or reverse):
            return False
    
    # 4. Check L4 protocol and ports
    if UDP/TCP:
        if ports don't match (forward or reverse):
            return False
    elif ICMP:
        return True if ICMP present
    
    return True
```

**Direction Handling:**
- The system supports **bidirectional matching** (`direction: "either"`)
  - Forward direction: `src_ip → dst_ip`, `sport → dport`
  - Reverse direction: `dst_ip → src_ip`, `dport → sport`
- A packet matches if it matches **either** direction

**Relaxed Mode:**
- For UDP packets, if strict matching fails, the system can relax to:
  - Match if **any** configured port appears (source or destination)
  - Useful for protocols like RoCEv2 where ports may vary

**Auto-Relaxation:**
- If no packets match for 2 seconds, the system automatically relaxes to:
  - Accept **any UDP packet** on the RX interface (for RoCEv2 compatibility)
  - Logs a warning: `[RX] auto-relax enabled: counting any UDP frames`

**Advantages:**
- ✅ Works with **both Scapy and DPDK** backends
- ✅ No packet modification required
- ✅ Supports bidirectional flows

**Limitations:**
- ❌ **Less accurate** - May match unrelated packets with same tuple
- ❌ **False positives** - Other streams with same IP/port combination
- ❌ **NAT issues** - Fails if packets are translated (IP/port changed)

---

## Packet Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    TX Side (Packet Generation)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  generate_packets() │
                    └─────────────────┘
                              │
                              ▼
              ┌─────────────────────────────┐
              │  Flow Tracking Enabled?     │
              └─────────────────────────────┘
                      │              │
                   YES│              │NO
                      │              │
        ┌─────────────┘              └─────────────┐
        │                                           │
        ▼                                           ▼
┌───────────────┐                          ┌───────────────┐
│ Embed Signature│                         │ Send Packet   │
│ [stream_id#seq]│                         │ (No Signature)│
└───────────────┘                          └───────────────┘
        │                                           │
        └───────────────┬──────────────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  Send on TX      │
              │  Interface       │
              └─────────────────┘
                        │
                        │ (Network)
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                    RX Side (Packet Reception)                │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  Sniffer Thread │
              │  on RX Interface │
              └─────────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │  For Each Received Packet:    │
        └───────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                                 │
        ▼                                 ▼
┌──────────────────┐          ┌──────────────────┐
│ Method 1:        │          │ Method 2:        │
│ Signature Match  │          │ Tuple Match      │
└──────────────────┘          └──────────────────┘
        │                                 │
        │ Found?                          │ Found?
        │                                 │
    YES│                                 │YES
        │                                 │
        └───────────────┬─────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  Increment RX   │
              │  Counter         │
              └─────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  Update Stream  │
              │  Tracker         │
              └─────────────────┘
```

---

## Implementation Details

### TX Counter Update

```python
# In generate_packets(), after sending each packet:
stream_tracker.update_tx_by_id(interface, stream_id)
```

- Increments `tx_count` for the stream
- Thread-safe (uses lock)

### RX Counter Update

```python
# In start_rx_counter(), when packet matches:
def on_pkt(_pkt):
    tracker.update_rx(rx_interface, stream_name, stream_id)
```

- Increments `rx_count` for the stream
- Thread-safe (uses lock)

### Stream Tracker Structure

```python
{
    "stream_id": "uuid-here",
    "interface": "ens5np0",          # TX interface
    "rx_interface": "ens4np0",       # RX interface
    "stream_name": "ICMP",
    "tx_count": 1000000,             # Total packets sent
    "rx_count": 999500,              # Total packets received
    "flow_tracking_enabled": True,
    "rx_thread": <Thread object>,     # Sniffer thread
    "stop_event": <Event object>
}
```

---

## Loss Percentage Calculation

```python
loss_pct = ((tx_count - rx_count) / tx_count * 100) if tx_count > 0 else 0.0
```

**Example:**
- TX Count: 1,000,000 packets
- RX Count: 999,500 packets
- Loss: (1,000,000 - 999,500) / 1,000,000 * 100 = **0.05%**

---

## VLAN Handling

### TX Side
- Packets are sent with VLAN tag if configured
- Signature is embedded **after** VLAN tag

### RX Side
- If VLAN is configured, the system creates a **temporary VLAN sub-interface**
- Example: If RX interface is `ens4np0` and VLAN is `20`:
  - Creates: `ens4np0.20` (or `vlan20@ens4np0`)
  - Sniffs on the sub-interface to capture VLAN-tagged packets
  - BPF filter is automatically widened to include VLAN headers
- Sub-interface is cleaned up when stream stops

---

## BPF Filter Generation

The system generates a BPF (Berkeley Packet Filter) filter to reduce CPU usage:

```python
# Example BPF filters:
"udp and host 192.168.1.1 and host 192.168.1.2"
"tcp and port 80"
"icmp"
"vlan 20 and udp"
```

**Benefits:**
- Kernel-level filtering (very fast)
- Reduces packets sent to userspace
- Lower CPU usage

**Limitations:**
- BPF may be too restrictive for some protocols
- System falls back to `lfilter()` for complex matching

---

## Debugging Flow Tracking

### Enable Debug Logging

The system logs detailed information about packet matching:

```
[RX-MATCH] signature on ens4np0 (seen=1000, sig=950, tuple=50)
[RX-MATCH] tuple on ens4np0 (seen=1000, sig=950, tuple=50, relaxed=False)
[RX-DBG] ens4np0: seen=1000 matched=1000 sig=950 tuple=50 relaxed=False
```

**Log Fields:**
- `seen`: Total packets seen by sniffer
- `matched`: Packets that matched (signature or tuple)
- `sig`: Packets matched by signature
- `tuple`: Packets matched by tuple
- `relaxed`: Whether auto-relaxation is active

### Common Issues

1. **RX Count = 0**
   - Check if RX interface is UP: `ip link show ens4np0`
   - Check if packets are reaching RX interface: `tcpdump -i ens4np0`
   - Verify VLAN sub-interface exists (if VLAN configured)
   - Check BPF filter: May be too restrictive

2. **High Loss Percentage**
   - Verify packets are actually being received: `tcpdump -i ens4np0`
   - Check if signature is present: `tcpdump -i ens4np0 -X` (look for `[stream_id#`)
   - Verify tuple matching: Check IP/port configuration
   - Check for packet drops: `ethtool -S ens4np0 | grep drop`

3. **False Positives (RX > TX)**
   - Usually indicates tuple matching is too broad
   - Other streams may have same IP/port combination
   - Consider using signature matching (Scapy backend)

---

## Best Practices

1. **Use Signature Matching When Possible**
   - Most accurate method
   - Enable Scapy backend (not DPDK)

2. **Configure RX Interface Correctly**
   - Ensure RX interface is different from TX interface
   - Verify interface is UP before starting stream

3. **VLAN Configuration**
   - System automatically handles VLAN sub-interface creation
   - Ensure VLAN ID matches on both TX and RX sides

4. **Monitor Debug Logs**
   - Watch for `[RX-MATCH]` and `[RX-DBG]` messages
   - Verify signature vs tuple hit ratio

5. **Test Packet Reception**
   - Use `tcpdump` to verify packets are received
   - Check for signature presence in packet dumps

---

## Summary

Stream flow tracking uses a **two-tier matching system**:

1. **Primary**: Signature matching (Scapy only) - embeds unique `[stream_id#seq]` in each packet
2. **Fallback**: Tuple matching (Scapy + DPDK) - matches based on MAC/IP/Port/VLAN

The system automatically:
- Creates VLAN sub-interfaces when needed
- Generates BPF filters for performance
- Supports bidirectional flows
- Auto-relaxes matching for RoCEv2 compatibility

Loss percentage is calculated as: `(TX - RX) / TX * 100`

For best accuracy, use **signature matching** (Scapy backend) when possible.

