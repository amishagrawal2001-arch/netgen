# DPDK Packet Generation Flow

This document explains the complete flow of packet generation when DPDK is enabled in the OSTG traffic generator.

## Overview

When DPDK is enabled for a stream, packets are generated and transmitted using the DPDK `tx_worker` C application instead of Scapy. This provides 10-100x better performance, enabling line-rate traffic generation (100Gbps, 400Gbps).

---

## Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. USER CONFIGURATION (Client UI)                               │
│    • User creates/edits stream in AddStreamDialog               │
│    • Checks "Use DPDK (tx_worker)" checkbox                     │
│    • Optionally checks "Force Multi-Instance DPDK"              │
│    • Configures packet fields (L2, L3, L4, rate, frame size)   │
│    • Clicks "Save"                                               │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. STREAM DATA PREPARATION (Client)                             │
│    • stream_data dictionary created with:                       │
│      - dpdk_enable: True                                        │
│      - dpdk_multi_instance: True/False                         │
│      - All packet fields (MAC, IP, ports, etc.)                 │
│      - Rate configuration (PPS or Line Rate)                    │
│      - Frame size/type                                          │
│    • Sent to server via POST /api/traffic/start                 │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. SERVER RECEIVES REQUEST (run_tgen_server.py)                 │
│    • Flask endpoint: /api/traffic/start                         │
│    • Validates stream_data                                      │
│    • Normalizes interface name                                  │
│    • Calls launch_single_stream()                               │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. STREAM LAUNCH (run_tgen_server.py)                           │
│    • Creates ThreadPoolExecutor                                 │
│    • Submits generate_packets() task                           │
│    • Stores Future in StreamTracker                             │
│    • Returns success to client                                  │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. PACKET GENERATION ENTRY POINT                                 │
│    (multithreaded_traffic_gen.py::generate_packets)            │
│                                                                 │
│    • Extracts stream_id, stream_name                            │
│    • Resolves frame_size, frame_type                            │
│    • Sets up RX sniffer (for statistics)                        │
│    • Checks if DPDK backend is available                        │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. DPDK DECISION LOGIC                                          │
│    (multithreaded_traffic_gen.py::generate_packets)            │
│                                                                 │
│    IF _DPDK_AVAILABLE AND should_use_dpdk(stream_data):       │
│      • Checks dpdk_enable flag                                  │
│      • Checks protocol_selection["dpdk_enable"]                │
│      • Checks engine == "dpdk"                                  │
│                                                                 │
│    IF DPDK requested:                                          │
│      • Resolves target PPS                                     │
│      • Checks for multi-instance mode                           │
│      • Auto-enables multi-instance if:                          │
│        - target_pps > 50M pps                                  │
│        - target_pps == 0 (line rate)                           │
│        - dpdk_multi_instance == True                            │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
        ┌───────────────┴───────────────┐
        │                               │
        ▼                               ▼
┌───────────────────┐         ┌──────────────────────┐
│ SINGLE-INSTANCE   │         │ MULTI-INSTANCE       │
│ DPDK PATH         │         │ DPDK PATH            │
│                   │         │                      │
│ (for rates        │         │ (for rates           │
│  < 50M pps)       │         │  > 50M pps or        │
│                   │         │  line rate)          │
└─────────┬─────────┘         └──────────┬───────────┘
          │                               │
          ▼                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7A. SINGLE-INSTANCE DPDK                                        │
│    (utils/dpdk_tx_worker.py::run_stream)                        │
│                                                                 │
│    • Resolves L2/L3/L4 fields from stream_data                │
│    • Converts interface to PCI BDF (Bus:Device.Function)       │
│    • Determines NUMA node for CPU affinity                     │
│    • Selects CPU cores on same NUMA node                       │
│    • Resolves target PPS from rate config                      │
│    • Builds tx_worker command line:                            │
│                                                                 │
│      tx_worker -l <cores> -n <mem_channels> \                 │
│                --file-prefix <unique_prefix> \                 │
│                -a <PCI_BDF> \                                  │
│                -- --src-mac <mac> --dst-mac <mac> \            │
│                   --src-ip <ip> --dst-ip <ip> \                │
│                   --src-port <port> --dst-port <port> \        │
│                   --size <frame_size> --pps <target_pps> \     │
│                   --stream-id <stream_id>                      │
│                                                                 │
│    • Launches tx_worker as subprocess                          │
│    • Monitors stdout for "STAT" lines                          │
│    • Updates StreamTracker with TX counts                      │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7B. MULTI-INSTANCE DPDK                                         │
│    (utils/dpdk_tx_worker_multi.py::run_multi_instance_stream)  │
│                                                                 │
│    • Calculates optimal instance count:                        │
│      - <= 50M pps: 1 instance                                  │
│      - 50M-100M pps: 2 instances                              │
│      - 100M-200M pps: 4 instances                             │
│      - > 200M pps: 4-8 instances                              │
│      - Line rate (400Gbps): 8-16 instances                    │
│                                                                 │
│    • Distributes CPU cores across instances                    │
│    • Launches multiple tx_worker processes:                    │
│      - Each with unique corelist                               │
│      - Each with unique file-prefix                            │
│      - Same PCI BDF (shared port)                              │
│      - Same packet fields                                       │
│      - PPS divided across instances                            │
│                                                                 │
│    • Monitors all subprocesses                                 │
│    • Aggregates TX counts from all instances                   │
│    • Updates StreamTracker with total counts                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 8. DPDK TX_WORKER APPLICATION                                   │
│    (resources/dpdk/tx_worker/tx_worker.c)                       │
│                                                                 │
│    A. INITIALIZATION PHASE:                                    │
│       • DPDK EAL (Environment Abstraction Layer) init:        │
│         - Parses command-line arguments                        │
│         - Initializes hugepage memory                           │
│         - Binds to PCI device (via vfio-pci or mlx5)          │
│         - Allocates memory pools                                │
│                                                                 │
│       • Port Configuration:                                    │
│         - Gets device capabilities                             │
│         - Enables hardware checksum offloads                   │
│         - Configures TX/RX queues                              │
│         - Sets up memory pools (mbuf pools)                    │
│         - Starts the port                                      │
│                                                                 │
│    B. PACKET TEMPLATE CREATION:                                │
│       • Builds header template:                                │
│         - Ethernet header (src/dst MAC)                        │
│         - VLAN header (if vlan_id specified)                   │
│         - IPv4 header (src/dst IP, TTL, protocol)              │
│         - UDP header (src/dst ports, length)                  │
│       • Calculates checksums (or sets HW offload flags)        │
│       • Determines payload length from frame_size              │
│                                                                 │
│    C. TRANSMISSION LOOP:                                       │
│       While (keep_running && !stop_event):                    │
│         1. Allocate mbufs (packet buffers) from pool          │
│         2. Build burst of packets:                            │
│            FOR each packet in burst:                           │
│              - Copy header template to mbuf                    │
│              - Append payload with signature:                  │
│                "[<stream_id>#<sequence>]"                     │
│              - Set hardware offload flags                      │
│              - Set packet metadata (L2/L3/L4 lengths)          │
│         3. Transmit burst via DPDK API:                       │
│            rte_eth_tx_burst(port_id, queue_id, pkts, count)   │
│         4. Rate limiting (if PPS specified):                  │
│            - Calculate cycles_per_burst                        │
│            - Wait until next_burst_time                        │
│            - Use TSC (Time Stamp Counter) for precision       │
│         5. Update statistics:                                 │
│            - Increment sent counter                            │
│            - Track dropped packets                             │
│            - Print "STAT" line periodically                     │
│                                                                 │
│    D. CLEANUP:                                                 │
│       • Stop port                                              │
│       • Close queues                                           │
│       • Free memory pools                                      │
│       • Exit DPDK EAL                                          │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 9. HARDWARE TRANSMISSION                                        │
│                                                                 │
│    • DPDK driver (vfio-pci or mlx5) sends packets directly    │
│      to NIC hardware                                            │
│    • NIC DMA transfers packets to wire                         │
│    • Hardware calculates checksums (if offloaded)              │
│    • Packets transmitted at line rate                          │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ 10. STATISTICS COLLECTION                                       │
│                                                                 │
│    A. TX Statistics (from tx_worker):                          │
│       • tx_worker prints "STAT stream=<id> tx=<count>"        │
│       • Python backend parses STAT lines                       │
│       • Updates StreamTracker.update_tx_by_id()                │
│                                                                 │
│    B. RX Statistics (from RX sniffer):                         │
│       • Separate sniffer thread captures packets               │
│       • Filters by MAC/IP/Port (relaxed matching for DPDK)    │
│       • Updates StreamTracker RX counts                         │
│                                                                 │
│    C. Server API (/api/streams/stats):                         │
│       • StreamTracker.get_stream_stats()                       │
│       • Returns TX/RX counts and rates                         │
│       • Client polls this endpoint periodically                │
└─────────────────────────────────────────────────────────────────┘

```

---

## Key Components

### 1. **Client UI** (`widgets/stream_dialog.py`)
- User enables DPDK via checkbox
- Stream configuration collected
- Sent to server as JSON

### 2. **Server API** (`run_tgen_server.py`)
- Receives stream start request
- Launches packet generation thread
- Manages stream lifecycle

### 3. **Packet Generator** (`multithreaded_traffic_gen.py`)
- Entry point: `generate_packets()`
- Decides: DPDK vs Scapy
- Handles RX statistics collection

### 4. **DPDK Backend** (`utils/dpdk_tx_worker.py`)
- Single-instance launcher
- Builds command line
- Launches `tx_worker` subprocess
- Monitors statistics

### 5. **Multi-Instance Backend** (`utils/dpdk_tx_worker_multi.py`)
- Calculates instance count
- Launches multiple `tx_worker` processes
- Aggregates statistics

### 6. **DPDK Application** (`resources/dpdk/tx_worker/tx_worker.c`)
- C application using DPDK libraries
- Direct hardware access
- High-performance packet generation
- Rate limiting and statistics

---

## Decision Points

### When is DPDK Used?

1. **DPDK Available**: `_DPDK_AVAILABLE == True`
   - DPDK libraries installed
   - `tx_worker` binary exists
   - Backend module importable

2. **Stream Requests DPDK**: `should_use_dpdk(stream_data) == True`
   - `stream_data["dpdk_enable"] == True`
   - OR `protocol_selection["dpdk_enable"] == True`
   - OR `stream_data["engine"] == "dpdk"`

### Single vs Multi-Instance?

**Single-Instance** used when:
- `dpdk_multi_instance == False` AND
- `target_pps <= 50M pps` AND
- `target_pps != 0` (not line rate)

**Multi-Instance** used when:
- `dpdk_multi_instance == True` OR
- `target_pps > 50M pps` OR
- `target_pps == 0` (line rate)

---

## Packet Building Process

### In DPDK (`tx_worker.c`):

1. **Template Creation** (once):
   ```c
   // Build header template
   struct rte_ether_hdr *eth = ...;
   eth->src_addr = src_mac;
   eth->dst_addr = dst_mac;
   
   struct rte_ipv4_hdr *ip = ...;
   ip->src_addr = src_ip;
   ip->dst_addr = dst_ip;
   
   struct rte_udp_hdr *udp = ...;
   udp->src_port = src_port;
   udp->dst_port = dst_port;
   ```

2. **Per-Packet Construction** (in loop):
   ```c
   // Allocate mbuf from pool
   struct rte_mbuf *m = rte_pktmbuf_alloc(mp);
   
   // Copy template to mbuf
   rte_memcpy(p, hdr_template, hdr_len);
   
   // Append payload with signature
   snprintf(payload, "[%s#%lu]", stream_id, seq++);
   
   // Set hardware offload flags
   m->ol_flags |= RTE_MBUF_F_TX_IPV4 | RTE_MBUF_F_TX_IP_CKSUM;
   ```

3. **Burst Transmission**:
   ```c
   // Transmit entire burst
   uint16_t sent = rte_eth_tx_burst(port_id, 0, pkts, burst_size);
   ```

---

## Performance Characteristics

### Scapy Path:
- **Rate**: 50K - 500K pps (max ~1M pps)
- **Latency**: Higher (kernel stack overhead)
- **CPU**: Single core, high overhead

### DPDK Single-Instance:
- **Rate**: 1M - 10M pps (max ~50M pps)
- **Latency**: Low (direct hardware access)
- **CPU**: 1-2 cores, efficient

### DPDK Multi-Instance:
- **Rate**: 10M - 100M+ pps (line rate capable)
- **Latency**: Very low
- **CPU**: Multiple cores (2-16), scales with instances

---

## Key Differences from Scapy Path

| Aspect | Scapy Path | DPDK Path |
|--------|-----------|-----------|
| **Packet Building** | Python/Scapy objects | C template + mbuf copy |
| **Transmission** | `sendp()` via kernel | Direct hardware DMA |
| **Rate Limiting** | `time.sleep()` | TSC-based precision |
| **Checksums** | Software calculation | Hardware offload |
| **Memory** | Python heap | Hugepage memory pools |
| **CPU** | Single core | Multi-core (NUMA-aware) |
| **Performance** | ~500K pps | 10M-100M+ pps |

---

## Stop Flow

When user clicks "Stop Stream":

1. **Client**: Sends POST `/api/traffic/stop`
2. **Server**: Sets `stop_event.set()` for stream
3. **tx_worker**: Checks `keep_running` flag (via signal handler)
4. **tx_worker**: Exits transmission loop
5. **tx_worker**: Prints final "STAT_FINAL" line
6. **Backend**: Parses final stats, updates StreamTracker
7. **Backend**: Subprocess exits
8. **Server**: Removes stream from StreamTracker
9. **Client**: Updates UI (stream disappears from stats)

---

## Troubleshooting Flow

If DPDK stream fails to start:

1. **Check DPDK Availability**:
   - Verify `_DPDK_AVAILABLE == True`
   - Check `tx_worker` binary exists
   - Verify DPDK libraries installed

2. **Check Prerequisites**:
   - Hugepages configured?
   - VFIO modules loaded?
   - IOMMU enabled (for Broadcom/Intel/AMD)?
   - Interface bound to DPDK?

3. **Check Command Line**:
   - Review `tx_worker` command in logs
   - Verify PCI BDF is correct
   - Check CPU corelist is valid

4. **Check tx_worker Output**:
   - Review subprocess stdout/stderr
   - Look for EAL initialization errors
   - Check for port binding failures

5. **Fallback to Scapy**:
   - If DPDK fails, falls back to Scapy path
   - Logs warning: "DPDK handoff failed; falling back to Scapy"

---

## Summary

The DPDK packet generation flow provides a high-performance alternative to Scapy by:

1. **Bypassing the kernel** - Direct hardware access via DPDK drivers
2. **Efficient packet building** - Template-based construction in C
3. **Hardware acceleration** - Checksum offloads, DMA transfers
4. **Multi-core scaling** - Multiple instances for line-rate performance
5. **Precise rate control** - TSC-based timing for accurate PPS

This enables the system to generate traffic at line rates (100Gbps, 400Gbps) that would be impossible with traditional kernel-based packet generation.




