# DPDK Integration Guide for OSTG

## Overview

DPDK integration is **already implemented** in the codebase! This guide explains what's already in place and what additional steps are needed to enable DPDK for high-performance packet generation.

## Current Status

### ✅ Already Implemented

1. **Server-side detection** (`multithreaded_traffic_gen.py`):
   - Automatically detects if DPDK backend is available
   - Falls back to Scapy if DPDK is not available
   - Checks for `dpdk_enable` flag in stream data

2. **Client-side UI** (`widgets/stream_dialog.py`):
   - DPDK checkbox in stream dialog ("Use DPDK (tx_worker)")
   - Collects `dpdk_enable` flag and sends it to server
   - Stores flag in both top-level and `protocol_selection`

3. **DPDK backend** (`utils/dpdk_tx_worker.py`):
   - Launches DPDK `tx_worker` binary
   - Handles stream synchronization
   - Supports mlx5 (kernel driver) and bnxt/Thor2 (vfio-pci)

4. **DPDK binary** (`resources/dpdk/tx_worker/`):
   - Source code and build system included
   - Binary needs to be built on the target server

## What Needs to Be Done

### 1. Build the DPDK tx_worker Binary

The `tx_worker` binary must be built on the server where traffic generation will run.

**Option A: Use the build script (Recommended)**
```bash
cd /opt/OSTG/resources/dpdk
./dpdk_tx_worker.sh --install-deps --rewrite-src
```

**Option B: Manual build**
```bash
cd /opt/OSTG/resources/dpdk/tx_worker
meson setup build
ninja -C build
```

**Prerequisites:**
- DPDK library installed (system DPDK or build tree)
- Build tools: `meson`, `ninja`, `pkg-config`
- Libraries: `libnuma-dev`, `libelf-dev`, `libpcap-dev`

### 2. Ensure DPDK Backend is Available

The server automatically detects DPDK availability. Check logs:
```bash
# Should see: "DPDK backend unavailable: ..." if not available
# Or: "[DPDK] using tx_worker backend" when enabled
journalctl -u ostg-server -f | grep -i dpdk
```

### 3. Configure DPDK Environment (if needed)

**For mlx5/NVIDIA NICs (vendor 15b3):**
- Runs with kernel driver (no vfio needed)
- Just ensure DPDK is installed

**For Broadcom/Thor2 NICs (vendor 14e4):**
- Requires vfio-pci binding
- IOMMU must be enabled in kernel
- Use `dpdk_start.sh` script for setup

**Set environment variables (optional):**
```bash
export TX_WORKER_BIN=/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
export DPDK_MEM_CHANNELS=4
```

### 4. Enable DPDK in Stream Configuration

**Via UI:**
1. Open "Add Stream" or "Edit Stream" dialog
2. Check the "Use DPDK (tx_worker)" checkbox
3. Save the stream

**Via API (if needed):**
```json
{
  "dpdk_enable": true,
  "protocol_selection": {
    "dpdk_enable": true
  }
}
```

## Code Flow

### Client Side
1. User checks "Use DPDK (tx_worker)" checkbox
2. `get_stream_details()` collects `dpdk_enable` flag (line 2922)
3. Flag is stored in both `stream_details["dpdk_enable"]` and `protocol_selection["dpdk_enable"]` (line 2967)
4. Stream data sent to server via `/api/traffic/start`

### Server Side
1. `generate_packets()` receives stream data
2. Checks `_DPDK_AVAILABLE` flag (line 32-38)
3. Calls `_dpdk_backend.should_use_dpdk(stream_data)` (line 989)
4. If enabled and available, launches `_dpdk_backend.run_stream()` (line 992)
5. Otherwise falls back to Scapy path

### DPDK Backend
1. `should_use_dpdk()` checks for:
   - `engine == "dpdk"`
   - `dpdk_enable == True`
   - `use_dpdk == True`
   - In top-level or `protocol_selection`
2. `run_stream()`:
   - Resolves `tx_worker` binary path
   - Extracts L2/L3/L4 fields from stream data
   - Builds DPDK command line
   - Launches `tx_worker` process
   - Monitors output and updates counters

## Binary Path Resolution

The `tx_worker` binary is searched in this order:
1. `$TX_WORKER_BIN` environment variable
2. `resources.dpdk.tx_worker/build/tx_worker` (packaged)
3. `../resources/dpdk/tx_worker/build/tx_worker` (relative to utils/)
4. `./resources/dpdk/tx_worker/build/tx_worker` (CWD)
5. `./tx_worker/build/tx_worker` (legacy)

## Verification

### Check if DPDK is Available
```bash
# On server
python3 -c "import utils.dpdk_tx_worker; print('DPDK available:', hasattr(utils.dpdk_tx_worker, 'should_use_dpdk'))"
```

### Check Binary Exists
```bash
ls -la /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
```

### Test DPDK Stream
1. Create a stream with DPDK enabled
2. Check server logs for: `[DPDK] using tx_worker backend`
3. Verify packet generation rate (should be much higher than Scapy)

## Troubleshooting

### "DPDK backend unavailable"
- Check if `utils/dpdk_tx_worker.py` exists and is importable
- Check Python path includes `/opt/OSTG`

### "tx_worker binary not found"
- Build the binary: `cd resources/dpdk/tx_worker && meson setup build && ninja -C build`
- Set `TX_WORKER_BIN` environment variable
- Check binary permissions: `chmod +x tx_worker`

### "missing required fields"
- Ensure stream has: `src_mac`, `dst_mac`, `src_ip`, `dst_ip`
- DPDK backend requires these fields (Scapy can infer defaults)

### Low Performance
- Check DPDK is actually being used (check logs)
- Verify NIC is DPDK-compatible
- Check hugepages: `cat /proc/meminfo | grep Huge`
- Verify NUMA node affinity matches NIC

## Performance Comparison

| Backend | Typical PPS | Notes |
|---------|-------------|-------|
| Scapy   | 100-1000    | Limited by kernel stack |
| DPDK    | 1M-10M+     | Hardware-dependent |

## Additional Configuration

### DPDK Core List
Set in stream data:
```json
{
  "dpdk_corelist": "1-2"
}
```

### Memory Channels
Set in stream data or environment:
```json
{
  "dpdk_mem_channels": "4"
}
```

### Burst Size
```json
{
  "burst": 256
}
```

## Summary

**To enable DPDK:**
1. ✅ Code is already integrated
2. ✅ UI checkbox exists
3. ⚠️ Build `tx_worker` binary on server
4. ⚠️ Ensure DPDK libraries are installed
5. ⚠️ Configure NIC (vfio for some, kernel driver for mlx5)
6. ✅ Check "Use DPDK" in stream dialog

The integration is **complete** - you just need to build and configure the DPDK environment on your server!

