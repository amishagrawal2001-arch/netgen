# Multi-Instance DPDK Implementation for 100Gbps/400Gbps NICs

## Overview

This implementation adds **multi-instance DPDK support** to enable line-rate traffic generation on high-speed NICs (100Gbps and 400Gbps). A single DPDK instance may not saturate these high-speed ports, so multiple instances are launched and their traffic is aggregated.

## What Was Implemented

### 1. **New Module: `utils/dpdk_tx_worker_multi.py`**
   - Launches multiple `tx_worker` instances per port
   - Automatically calculates optimal instance count based on target rate
   - Distributes cores across instances (NUMA-aware)
   - Aggregates statistics from all instances
   - Handles cleanup and monitoring

### 2. **Enhanced `multithreaded_traffic_gen.py`**
   - Auto-detects high-rate streams (>50M pps or line rate)
   - Automatically enables multi-instance mode for high rates
   - Falls back to single-instance if multi-instance unavailable
   - Supports explicit `dpdk_multi_instance` flag

### 3. **UI Enhancement: `widgets/stream_dialog.py`**
   - Added "Force Multi-Instance DPDK" checkbox
   - Auto-enabled for high rates, but can be manually enabled
   - Tooltips explain when to use multi-instance mode

## How It Works

### Instance Count Calculation

| Target Rate | Instance Count | Use Case |
|-------------|----------------|----------|
| ≤ 50M pps | 1 | Standard rates |
| 50M-100M pps | 2 | Medium-high rates |
| 100M-200M pps | 4 | High rates (100Gbps) |
| > 200M pps | 8 | Very high rates |
| Line Rate (400Gbps) | 16 | Maximum performance |
| Line Rate (100Gbps) | 4 | 100Gbps line rate |

### Core Distribution

- Cores are distributed evenly across instances
- NUMA-aware: Cores are selected from the same NUMA node as the NIC
- Each instance gets 1-2 cores (configurable)

### Rate Distribution

- Total target rate is divided equally among instances
- For line rate (`pps=0`), each instance runs at maximum speed
- Statistics are aggregated from all instances

## Usage

### Automatic (Recommended)

Multi-instance mode is **automatically enabled** when:
- Target rate > 50M pps, OR
- Line rate is selected (`stream_rate_type == "Line Rate"`)

No configuration needed - just enable DPDK and set a high rate!

### Manual Override

To force multi-instance mode:
1. Check "Use DPDK (tx_worker)" checkbox
2. Check "Force Multi-Instance DPDK (100Gbps+)" checkbox
3. Save stream

### Configuration Options

In stream data, you can set:
```json
{
  "dpdk_enable": true,
  "dpdk_multi_instance": true,  // Force multi-instance
  "dpdk_num_instances": 8,      // Explicit instance count
  "dpdk_corelist": "1-16",      // Cores to use
  "stream_rate_type": "Line Rate"
}
```

## Performance Expectations

### 100Gbps NIC
- **Single Instance:** ~50-80 Gbps (depending on CPU/NIC)
- **Multi-Instance (4 instances):** ~90-100 Gbps (near line rate)
- **Expected:** 95-100 Gbps with proper configuration

### 400Gbps NIC
- **Single Instance:** ~100-200 Gbps (depending on CPU/NIC)
- **Multi-Instance (16 instances):** ~350-400 Gbps (near line rate)
- **Expected:** 380-400 Gbps with proper configuration

## Requirements

1. **DPDK Backend Available**
   - `utils/dpdk_tx_worker.py` must be importable
   - `tx_worker` binary must be built

2. **Sufficient CPU Cores**
   - 100Gbps: ~4-8 cores recommended
   - 400Gbps: ~16-32 cores recommended
   - Cores should be on same NUMA node as NIC

3. **High-Speed NICs**
   - mlx5 (Mellanox/NVIDIA ConnectX)
   - bnxt (Broadcom NetXtreme-E/Thor2)
   - ice (Intel E810)

4. **Hugepages Configured**
   - DPDK requires hugepages
   - Typically 1024 x 2MB pages minimum

## Code Flow

```
User enables DPDK + High Rate
    ↓
multithreaded_traffic_gen.py detects high rate
    ↓
Auto-enables multi-instance mode
    ↓
dpdk_tx_worker_multi.py calculates instance count
    ↓
Launches N instances with distributed cores
    ↓
Each instance generates portion of total rate
    ↓
Statistics aggregated and reported
```

## Troubleshooting

### "Multi-instance backend not available"
- Ensure `utils/dpdk_tx_worker_multi.py` exists
- Check Python path includes `/opt/OSTG`

### "Only N cores available but M instances requested"
- Reduce instance count: Set `dpdk_num_instances` explicitly
- Or add more CPU cores

### Performance below expected
- Check CPU usage: Should be high across multiple cores
- Verify cores are on same NUMA node as NIC
- Check NIC driver and firmware versions
- Ensure hugepages are configured
- Verify DPDK PMD supports your NIC model

### Instances exiting unexpectedly
- Check DPDK logs for errors
- Verify NIC is bound correctly (vfio for some, kernel driver for mlx5)
- Check system resources (memory, hugepages)

## Next Steps

1. **Build DPDK tx_worker binary** on server
2. **Test with 100Gbps NIC** first (lower instance count)
3. **Verify statistics aggregation** works correctly
4. **Scale to 400Gbps** once 100Gbps is working
5. **Tune instance count** based on actual performance

## Future Enhancements

- [ ] Multi-queue support (use multiple TX queues per port)
- [ ] Dynamic instance count adjustment based on actual performance
- [ ] Better statistics aggregation (per-instance tracking)
- [ ] Load balancing across instances for variable rates
- [ ] Support for multi-port aggregation (combine multiple 100Gbps ports)

