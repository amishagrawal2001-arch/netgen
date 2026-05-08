# DPDK Bind Script Usage Guide

## Overview

`dpdk_bind.sh` is a standalone script for binding and unbinding NICs to/from DPDK drivers. It replaces the binding functionality from `dpdk_start.sh` and provides a cleaner, more focused interface.

## Features

- ✅ **Vendor-aware**: Automatically detects NIC vendor (NVIDIA, Broadcom, Intel, AMD)
- ✅ **Smart binding**: Handles vendor-specific requirements (e.g., Mellanox doesn't need binding)
- ✅ **Safety checks**: Warns about active routes before binding
- ✅ **Auto-detection**: Automatically detects kernel drivers for unbinding
- ✅ **User-friendly**: Clear status display with NIC-to-interface mapping

## Usage

### Show NIC Status

```bash
cd /opt/OSTG/resources/dpdk
sudo ./dpdk_bind.sh status
# or
sudo ./dpdk_bind.sh show
```

**Output:**
- Formatted table of all NICs
- PCI → Interface → Driver mapping
- Vendor information
- Binding recommendations

### Bind NIC to DPDK

```bash
# Basic binding
sudo ./dpdk_bind.sh bind 0000:8a:00.0

# Force binding (ignore active routes)
sudo ./dpdk_bind.sh bind 0000:8a:00.0 --force
```

**What it does:**
- Detects NIC vendor
- Warns if Mellanox (no binding needed)
- Checks for active routes
- Brings interface down
- Unbinds from kernel driver
- Binds to vfio-pci

### Unbind NIC from DPDK

```bash
# Auto-detect kernel driver
sudo ./dpdk_bind.sh unbind 0000:8a:00.0

# Specify kernel driver explicitly
sudo ./dpdk_bind.sh unbind 0000:8a:00.0 --kernel-driver bnxt_en
```

**What it does:**
- Detects current driver
- Unbinds from vfio-pci
- Loads kernel driver
- Binds to kernel driver
- Brings interface up

## Examples

### Example 1: Check Status

```bash
sudo ./dpdk_bind.sh status
```

Output:
```
Available Network Interfaces:

PCI Address     Interface            Driver          NUMA        Type       Status
────────────────────────────────────────────────────────────────────────────────────────────
0000:8a:00.0    ens5np0              bnxt_en         Node 1      Broadcom   
0000:9f:00.0    ens6np0              mlx5_core       Node 1      Mellanox   

NIC to Interface Mapping Summary:

  • 0000:8a:00.0 → ens5np0 (bnxt_en) - Device 1760
    Vendor: Broadcom (ID: 14e4)
    ✅ Bind to vfio-pci for DPDK

  • 0000:9f:00.0 → ens6np0 (mlx5_core) - MT2910 Family [ConnectX-7]
    Vendor: NVIDIA/Mellanox (ID: 15b3)
    ⚠️  KEEP kernel driver (mlx5_core) - DPDK mlx5 PMD works with kernel driver
```

### Example 2: Bind Broadcom NIC

```bash
sudo ./dpdk_bind.sh bind 0000:8a:00.0
```

Output:
```
[INFO] Binding 0000:8a:00.0 (Broadcom) to DPDK...
[INFO] Bringing interface ens5np0 down...
[INFO] Unbinding from bnxt_en...
[INFO] Loading vfio-pci driver...
[INFO] Binding to vfio-pci...
[✓] NIC bound to vfio-pci successfully
```

### Example 3: Bind with Force (Active Routes)

```bash
sudo ./dpdk_bind.sh bind 0000:8a:00.0 --force
```

Output:
```
[INFO] Binding 0000:8a:00.0 (Broadcom) to DPDK...
[!] Interface ens5np0 has active routes
[!] Force binding despite active routes...
[INFO] Bringing interface ens5np0 down...
...
[✓] NIC bound to vfio-pci successfully
```

### Example 4: Unbind NIC

```bash
sudo ./dpdk_bind.sh unbind 0000:8a:00.0
```

Output:
```
[INFO] Unbinding 0000:8a:00.0 (Broadcom) from DPDK...
[INFO] Unbinding from vfio-pci...
[INFO] Loading kernel driver: bnxt_en
[INFO] Binding to kernel driver: bnxt_en
[INFO] Bringing interface ens5np0 up...
[✓] NIC bound to kernel driver successfully
```

### Example 5: Mellanox NIC (No Binding Needed)

```bash
sudo ./dpdk_bind.sh bind 0000:9f:00.0
```

Output:
```
[INFO] Binding 0000:9f:00.0 (NVIDIA/Mellanox) to DPDK...
[!] NVIDIA/Mellanox NIC detected - DO NOT bind to vfio-pci
[INFO] Mellanox mlx5 PMD works with kernel driver (mlx5_core)
[INFO] No binding needed for Mellanox/NVIDIA NICs
[✓] NIC is ready for DPDK (using kernel driver)
```

## Command Reference

| Command | Description |
|---------|-------------|
| `status` or `show` | Show NIC status and bindings |
| `bind <PCI>` | Bind NIC to DPDK (vfio-pci) |
| `bind <PCI> --force` | Force bind (ignore active routes) |
| `unbind <PCI>` | Unbind NIC (return to kernel driver) |
| `unbind <PCI> --kernel-driver <drv>` | Unbind with specific kernel driver |

## Integration with install_dpdk.sh

The `install_dpdk.sh` script uses `dpdk_bind.sh` for Step 8 (NIC Binding). You can also use `dpdk_bind.sh` independently after installation.

## Comparison: dpdk_bind.sh vs dpdk_start.sh

| Feature | dpdk_bind.sh | dpdk_start.sh |
|---------|--------------|---------------|
| **Purpose** | NIC binding/unbinding only | Full DPDK setup (deps, build, bind, test) |
| **Focus** | Single responsibility | Multiple responsibilities |
| **Vendor detection** | ✅ Yes | ✅ Yes |
| **Status display** | ✅ Enhanced | ✅ Basic |
| **Standalone** | ✅ Yes | ✅ Yes |
| **Used by install_dpdk.sh** | ✅ Yes (Step 8) | ❌ No longer needed |

## Troubleshooting

### Issue: Binding fails with "active routes"

**Solution:**
```bash
# Remove routes first, or use --force
sudo ./dpdk_bind.sh bind 0000:8a:00.0 --force
```

### Issue: Cannot determine kernel driver

**Solution:**
```bash
# Specify kernel driver explicitly
sudo ./dpdk_bind.sh unbind 0000:8a:00.0 --kernel-driver bnxt_en
```

### Issue: vfio-pci not loaded

**Solution:**
The script automatically loads vfio-pci. If it fails:
```bash
sudo modprobe vfio-pci
```

## Notes

- **All operations require root/sudo**
- **Mellanox NICs**: No binding needed - script will detect and skip
- **Active routes**: Script warns before binding - use `--force` to override
- **Kernel driver**: Auto-detected, but can be specified explicitly




