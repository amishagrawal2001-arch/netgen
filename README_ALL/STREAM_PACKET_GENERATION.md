# Stream Packet Formation and Traffic Generation

## Overview

OSTG (Open Source Traffic Generator) generates network traffic by constructing packets layer-by-layer according to stream configuration and sending them at specified rates. This document explains the complete flow from stream configuration to packet transmission.

## Architecture

The traffic generation system has two main paths:
1. **Scapy-based path** (default): Uses Python's Scapy library for packet construction and sending
2. **DPDK path** (optional): Uses DPDK's `tx_worker` for high-performance packet generation

---

## 1. Stream Configuration

### Stream Data Structure

A stream configuration contains:
- **Protocol Selection**: L1, L2, L3, L4, VLAN, Payload options
- **Protocol Data**: Specific values for each layer (MAC addresses, IP addresses, ports, etc.)
- **Rate Control**: PPS, bit rate, load percentage, or line rate
- **Duration**: Continuous or time-limited
- **Flow Tracking**: Enable/disable RX packet counting

### Example Stream Configuration:
```json
{
  "stream_id": "be58b7a9-c087-4dfa-b785-fd197abbc1f5",
  "name": "ICMP",
  "protocol_selection": {
    "L1": "MAC",
    "VLAN": "Tagged",
    "L2": "Ethernet II",
    "L3": "IPv4",
    "L4": "ICMP",
    "Payload": "None"
  },
  "protocol_data": {
    "mac": {
      "mac_source_address": "00:00:00:00:00:02",
      "mac_destination_address": "00:00:00:00:00:01"
    },
    "vlan": {
      "vlan_id": "100",
      "vlan_priority": "0"
    },
    "ipv4": {
      "ipv4_source": "10.0.0.1",
      "ipv4_destination": "11.0.0.2",
      "ipv4_ttl": "64"
    }
  },
  "stream_rate_type": "Packets Per Second (PPS)",
  "stream_pps_rate": "1000",
  "flow_tracking_enabled": true
}
```

---

## 2. Packet Formation Process

### Step 1: Configuration Expansion (`get_packet_config()`)

**Location**: `utils/generic.py`

Expands stream configuration into lists of values for packet variation:

```python
def get_packet_config(stream_data):
    # Extract protocol data
    mac = protocol_data.get("mac", {})
    vlan = protocol_data.get("vlan", {})
    ipv4 = protocol_data.get("ipv4", {})
    
    # Expand MAC addresses (if increment mode)
    mac_src_list = [mac.get("mac_source_address")]
    if mac.get("mac_source_mode") == "Increment":
        step = int(mac.get("mac_source_step", 1))
        count = int(mac.get("mac_source_count", 1))
        mac_src_list = [increment_mac(mac_src_list[0], step * i) 
                        for i in range(count)]
    
    # Similar expansion for:
    # - MAC destination addresses
    # - IPv4 source/destination addresses
    # - IPv6 source/destination addresses
    # - TCP/UDP source/destination ports
    # - VLAN IDs
    
    return {
        "mac_src_list": mac_src_list,
        "mac_dst_list": mac_dst_list,
        "ipv4_src_list": ipv4_src_list,
        "ipv4_dst_list": ipv4_dst_list,
        "vlan_ids": vlan_ids,
        # ... etc
    }
```

**Purpose**: Creates lists of values to cycle through for packet variation (e.g., incrementing MAC addresses, IP addresses, ports).

---

### Step 2: Packet Construction (`build_generic_packet()`)

**Location**: `utils/generic.py`

Builds packets layer-by-layer using Scapy:

#### Layer 2 (Ethernet)
```python
# Base Ethernet frame
pkt = Ether(src=src_mac, dst=dst_mac)
```

#### VLAN (802.1Q)
```python
if vlan_id > 0:
    pkt /= Dot1Q(
        vlan=int(vlan_id),
        prio=int(vlan_priority) & 0x7,  # Priority (0-7)
        dei=int(vlan_cfi_dei) & 0x1      # DEI/CFI bit
    )
```

#### MPLS (if selected)
```python
if l2 == "MPLS":
    pkt /= MPLS(
        label=int(mpls_label),
        ttl=int(mpls_ttl),
        cos=int(mpls_experimental)
    )
```

#### Layer 3 (IP)
```python
if l3 == "IPv4":
    pkt /= IP(
        src=src_ip,
        dst=dst_ip,
        ttl=int(ipv4_ttl),
        tos=tos,  # Calculated from DSCP/TOS/ECN settings
        id=int(ipv4_identification),
        flags=flags,  # DF, MF bits
        frag=int(ipv4_fragment_offset)
    )
elif l3 == "IPv6":
    pkt /= IPv6(
        src=src_ipv6,
        dst=dst_ipv6,
        hlim=int(ipv6_hop_limit),
        tc=int(ipv6_traffic_class),
        fl=int(ipv6_flow_label)
    )
```

#### Layer 4 (Transport)
```python
if l4 == "UDP":
    pkt = build_udp_l4(pkt, stream_data, pkt_cfg, ...)
elif l4 == "TCP":
    pkt /= TCP(
        sport=tcp_sport,
        dport=tcp_dport,
        flags=parse_tcp_flags(tcp_flags),  # SYN, ACK, etc.
        seq=tcp_seq,
        ack=tcp_ack,
        window=tcp_window
    )
elif l4 == "ICMP":
    pkt /= ICMP()
elif l4 == "IGMP":
    pkt /= IGMP(type=igmp_type, gaddr=igmp_maddr)
```

#### Payload & Signature
```python
# For flow tracking, embed stream signature
if flow_tracking_enabled:
    sig = f"[{stream_id}#{seq}]".encode()
    pkt /= Raw(load=sig + user_payload)
```

**Scapy's `/` operator**: Chains protocol layers together (e.g., `Ether() / IP() / TCP()`).

---

### Step 3: Signature Embedding (`_append_sig_with_seq()`)

**Location**: `multithreaded_traffic_gen.py`

For flow tracking, each packet gets a unique signature:

```python
def _append_sig_with_seq(pkt, stream_id: str, seq: int):
    sig = f"[{stream_id}#{seq}]".encode()
    
    # Embed in UDP payload or Raw layer
    if UDP in pkt:
        # Remove existing payload, prepend signature
        pkt[UDP].remove_payload()
        pkt = pkt / Raw(load=(sig + original_payload))
    else:
        # Append to Raw layer or create new one
        pkt = pkt / Raw(load=sig)
    
    # Recompute checksums and lengths
    # (Scapy does this automatically, but we ensure it)
    return pkt
```

**Purpose**: Allows RX sniffer to identify packets from this specific stream for loss calculation.

---

## 3. Traffic Generation Flow

### Entry Point: `launch_single_stream()`

**Location**: `run_tgen_server.py`

```python
def launch_single_stream(stream_data, interface):
    # 1. Generate unique stream ID
    stream_id = stream_data.setdefault("stream_id", str(uuid.uuid4()))
    
    # 2. Create stop event (threading.Event)
    stop_event = Event()
    
    # 3. Register stream in tracker
    stream_tracker.add_stream({
        "interface": interface,
        "stream_id": stream_id,
        "stop_event": stop_event,
        "flow_tracking_enabled": flow_tracking,
        # ...
    })
    
    # 4. Start packet generation thread
    tx_thread = threading.Thread(
        target=generate_packets,
        args=(stream_data, interface, stop_event),
        daemon=True
    )
    tx_thread.start()
    
    return {"stream_id": stream_id, "status": "started"}
```

---

### Main Generation Loop: `generate_packets()`

**Location**: `multithreaded_traffic_gen.py`

#### 3.1 Initialization

```python
def generate_packets(stream_data, interface, stop_event):
    # 1. Extract stream configuration
    stream_id = stream_data.get("stream_id")
    protocol_selection = stream_data.get("protocol_selection", {})
    protocol_data = stream_data.get("protocol_data", {})
    l4_sel = protocol_selection.get("L4", "")
    
    # 2. Start RX sniffer (if flow tracking enabled)
    if flow_tracking_enabled:
        rx_selector = _build_rx_selector_for_stream(stream_data)
        rx_thread = start_rx_counter(
            rx_interface, stream_name, stream_id, 
            stream_tracker, stop_event, selector=rx_selector
        )
    
    # 3. Check for DPDK backend (high-performance path)
    if _DPDK_AVAILABLE and should_use_dpdk(stream_data):
        return _dpdk_backend.run_stream(...)  # DPDK path
    
    # 4. Calculate rate parameters
    interval, batch_size = calculate_interval(stream_rate_type, stream_data)
    # interval = seconds between batches
    # batch_size = packets per batch
```

#### 3.2 Rate Calculation (`calculate_interval()`)

**Location**: `multithreaded_traffic_gen.py`

```python
def calculate_interval(rate_type, stream_data):
    if rate_type == "Packets Per Second (PPS)":
        pps = int(stream_pps_rate)  # e.g., 1000
        batch_size = 1  # Send 1 packet at a time
        interval = 1.0 / pps  # 0.001 seconds (1ms)
        
    elif rate_type == "Bit Rate (Mbps)":
        bit_rate_mbps = int(stream_bit_rate)  # e.g., 100 Mbps
        packet_size_bits = frame_size * 8  # e.g., 64 bytes = 512 bits
        pps = (bit_rate_mbps * 1_000_000) / packet_size_bits
        interval = 1.0 / pps
        batch_size = 1
        
    elif rate_type == "Load (%)":
        # Calculate based on interface speed and packet size
        # ...
        
    elif rate_type == "Line Rate":
        batch_size = 512  # Large batches
        interval = 0.000001  # Very small delay (1 microsecond)
    
    return interval, batch_size
```

#### 3.3 Packet Generation Loop

**For Generic Packets (TCP/UDP/ICMP)**:

```python
# Expand configuration into lists
pkt_cfg = get_packet_config(stream_data)

# Initialize counters for cycling through lists
mac_idx = 0
ip_idx = 0
vlan_idx = 0

start_time = time.time()

while not stop_event.is_set():
    # 1. Handle PCAP interleaving (if enabled)
    send_pcap()  # Send one packet from PCAP file if due
    
    # 2. Build packet with current values
    pkt = build_generic_packet(
        stream_data, pkt_cfg,
        vlan_id=pkt_cfg["vlan_ids"][vlan_idx % len(pkt_cfg["vlan_ids"])],
        src_mac=pkt_cfg["mac_src_list"][mac_idx % len(pkt_cfg["mac_src_list"])],
        dst_mac=pkt_cfg["mac_dst_list"][mac_idx % len(pkt_cfg["mac_dst_list"])],
        src_ip=pkt_cfg["ipv4_src_list"][ip_idx % len(pkt_cfg["ipv4_src_list"])],
        dst_ip=pkt_cfg["ipv4_dst_list"][ip_idx % len(pkt_cfg["ipv4_dst_list"])],
        # ... etc
    )
    
    # 3. Add signature for flow tracking
    pkt = add_sig(pkt)  # Embeds [stream_id#seq]
    
    # 4. Send batch of packets
    to_send = []
    for _ in range(batch_size):
        to_send.append(pkt.copy())  # Copy packet for batch
    
    sendp(to_send, iface=interface, verbose=False)
    
    # 5. Update TX counters
    for _ in range(len(to_send)):
        stream_tracker.update_tx_by_id(interface, stream_id)
    
    # 6. Increment indices for next packet variation
    mac_idx += 1
    ip_idx += 1
    vlan_idx += 1
    
    # 7. Rate limiting
    if interval > 0:
        time.sleep(interval)
    
    # 8. Check duration limit
    if duration_mode == "Seconds":
        if time.time() - start_time >= duration_seconds:
            stop_event.set()
            break
    
    # 9. Check max packets limit
    if max_packets and tx_count >= max_packets:
        stop_event.set()
        break
```

**For Special Protocols**:

- **RoCEv2**: Uses `generate_rocev2_packet()` to build RoCEv2-specific packets
- **UEC**: Uses `generate_uec_rocev2_packet()` with QP/PASID cycling
- **ARP**: Uses `generate_arp_packet()` for ARP requests/replies

---

## 4. Packet Sending

### Scapy Path (`sendp()`)

**Location**: Scapy library

```python
from scapy.all import sendp

# Send single packet
sendp(pkt, iface=interface, verbose=False)

# Send batch (list of packets)
sendp([pkt1, pkt2, pkt3], iface=interface, verbose=False)
```

**How it works**:
1. Scapy serializes the packet object into raw bytes
2. Uses `libpcap` (via Python's `socket` or `PF_PACKET`) to inject into kernel
3. Kernel sends packet out the specified interface

### DPDK Path (`tx_worker`)

**Location**: `resources/dpdk/tx_worker/tx_worker.c`

For high-performance scenarios:
1. Pre-builds packet template in memory
2. Uses DPDK's zero-copy memory pools (`rte_mbuf`)
3. Updates only variable fields (signature, sequence) per packet
4. Sends via DPDK's `rte_eth_tx_burst()` API
5. Achieves line-rate performance (millions of packets per second)

---

## 5. Flow Tracking (RX Counting)

### RX Sniffer (`start_rx_counter()`)

**Location**: `multithreaded_traffic_gen.py`

When flow tracking is enabled:

```python
def start_rx_counter(rx_interface, stream_name, stream_id, tracker, stop_event, selector):
    # 1. Build BPF filter from selector
    bpf_filter = _build_bpf(selector)
    # Example: "((host 10.0.0.1 or host 11.0.0.2) or (ip6 and ...)) and (icmp or icmp6)"
    
    # 2. Create VLAN sub-interface if needed (for VLAN-tagged packets)
    if vlan_id:
        sniff_iface = _ensure_vlan_rx_visible(rx_interface, vlan_id)
        # Creates ens4np0.100 if VLAN 100 is configured
    
    # 3. Start Scapy AsyncSniffer
    sniffer = AsyncSniffer(
        iface=sniff_iface,
        filter=bpf_filter,  # Kernel-level filtering
        prn=on_pkt,  # Callback for each matching packet
        stop_filter=lambda x: stop_event.is_set()
    )
    
    def on_pkt(pkt):
        # Match packet by signature or tuple
        if _sig_present(pkt) or _tuple_match(pkt):
            tracker.update_rx(rx_interface, stream_name, stream_id)
    
    sniffer.start()
    return sniffer
```

**Matching Methods**:
1. **Signature matching**: Looks for `[stream_id#seq]` in packet payload
2. **Tuple matching**: Matches MAC, IP, L4 ports, VLAN ID (tolerant matching)

---

## 6. Statistics Tracking

### Stream Tracker (`StreamTracker`)

**Location**: `multithreaded_traffic_gen.py`

```python
class StreamTracker:
    def __init__(self):
        self.active_streams = []  # List of active stream entries
        self.lock = threading.Lock()
    
    def update_tx_by_id(self, interface, stream_id):
        # Increment TX count for stream
        with self.lock:
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_id"] == stream_id:
                    s["tx_count"] += 1
    
    def update_rx(self, rx_interface, stream_name, stream_id):
        # Increment RX count for stream
        with self.lock:
            for s in self.active_streams:
                if s["stream_id"] == stream_id:
                    s["rx_count"] += 1
```

### Database Updates

**Location**: `run_tgen_server.py` → `_poll_stream_statistics()`

Every 2 seconds:
1. Fetches TX/RX counts from `stream_tracker`
2. Updates `stream_db` with counts and calculated rates
3. Rates calculated: `(current_count - last_count) / time_delta`

---

## 7. Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Client UI: User creates stream configuration            │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Client sends POST /api/traffic/start                   │
│    {streams: {port: [stream_data]}}                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Server: launch_single_stream()                          │
│    - Generate stream_id                                     │
│    - Create stop_event                                      │
│    - Register in stream_tracker                            │
│    - Start generate_packets() thread                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. generate_packets()                                      │
│    ├─ Expand config: get_packet_config()                   │
│    ├─ Start RX sniffer (if flow tracking)                  │
│    ├─ Calculate rate: calculate_interval()                │
│    └─ Enter generation loop:                               │
│       ├─ Build packet: build_generic_packet()             │
│       │  ├─ Ether()                                       │
│       │  ├─ Dot1Q() [if VLAN]                             │
│       │  ├─ IP() or IPv6()                                │
│       │  ├─ TCP()/UDP()/ICMP()                            │
│       │  └─ Raw() [signature + payload]                   │
│       ├─ Add signature: _append_sig_with_seq()            │
│       ├─ Send batch: sendp([pkt1, pkt2, ...])             │
│       ├─ Update TX count: stream_tracker.update_tx_by_id()│
│       └─ Sleep: time.sleep(interval)                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. RX Sniffer (parallel thread)                           │
│    ├─ Capture packets: AsyncSniffer(filter=bpf)          │
│    ├─ Match packets: signature or tuple                    │
│    └─ Update RX count: stream_tracker.update_rx()           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Statistics Polling (every 2 seconds)                   │
│    ├─ Fetch counts: stream_tracker.get_stream_stats()     │
│    ├─ Calculate rates: (count - last_count) / time_delta   │
│    └─ Update database: stream_db.update_stream_statistics()│
└─────────────────────────────────────────────────────────────┘
```

---

## 8. Key Files and Functions

| File | Function | Purpose |
|------|----------|---------|
| `run_tgen_server.py` | `launch_single_stream()` | Entry point, starts generation thread |
| `multithreaded_traffic_gen.py` | `generate_packets()` | Main generation loop |
| `multithreaded_traffic_gen.py` | `calculate_interval()` | Rate calculation |
| `multithreaded_traffic_gen.py` | `start_rx_counter()` | RX sniffer setup |
| `utils/generic.py` | `get_packet_config()` | Config expansion |
| `utils/generic.py` | `build_generic_packet()` | Packet construction |
| `multithreaded_traffic_gen.py` | `_append_sig_with_seq()` | Signature embedding |
| `multithreaded_traffic_gen.py` | `StreamTracker` | Statistics tracking |

---

## 9. Example: ICMP Packet Generation

**Configuration**:
- L2: Ethernet II
- VLAN: 100
- L3: IPv4 (10.0.0.1 → 11.0.0.2)
- L4: ICMP
- Rate: 1000 PPS

**Packet Construction**:
```python
# Step 1: Expand config
pkt_cfg = {
    "mac_src_list": ["00:00:00:00:00:02"],
    "mac_dst_list": ["00:00:00:00:00:01"],
    "vlan_ids": [100],
    "ipv4_src_list": ["10.0.0.1"],
    "ipv4_dst_list": ["11.0.0.2"]
}

# Step 2: Build packet
pkt = Ether(src="00:00:00:00:00:02", dst="00:00:00:00:00:01")
pkt /= Dot1Q(vlan=100, prio=0, dei=0)
pkt /= IP(src="10.0.0.1", dst="11.0.0.2", ttl=64)
pkt /= ICMP()
pkt /= Raw(load=b"[be58b7a9-c087-4dfa-b785-fd197abbc1f5#0]")

# Step 3: Send
sendp(pkt, iface="ens5np0")
```

**Result**: ICMP packet with VLAN tag 100, sent at 1000 packets/second.

---

## 10. Performance Considerations

### Scapy Path
- **Throughput**: ~100K-500K pps (depending on packet size)
- **Latency**: Variable (Python GIL, kernel overhead)
- **Use case**: General purpose, flexible packet generation

### DPDK Path
- **Throughput**: Line rate (millions of pps)
- **Latency**: Low (kernel bypass)
- **Use case**: High-performance testing, line-rate traffic

### Rate Limiting
- Uses `time.sleep(interval)` for rate control
- Batch sending reduces overhead
- For line rate, uses minimal sleep (1 microsecond)

---

## Summary

1. **Configuration**: User defines stream parameters (protocols, addresses, rates)
2. **Expansion**: Config expanded into lists for packet variation
3. **Construction**: Packets built layer-by-layer (L2 → L3 → L4 → Payload)
4. **Signature**: Unique signature embedded for flow tracking
5. **Generation**: Packets sent in batches at calculated intervals
6. **Tracking**: RX sniffer counts received packets
7. **Statistics**: Counts and rates updated in database every 2 seconds

The system is designed to be flexible (supports many protocols) and performant (DPDK path for high rates), while maintaining accurate statistics for traffic analysis.


