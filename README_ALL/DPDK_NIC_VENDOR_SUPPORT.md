# DPDK NIC Vendor Support Guide

This document explains DPDK support for different NIC vendors and how the installation script handles each type.

## Supported NIC Vendors

The DPDK installation script supports the following major NIC vendors:

| Vendor | Vendor ID | Kernel Driver(s) | DPDK PMD | Binding Mode | Notes |
|--------|-----------|------------------|----------|--------------|-------|
| **NVIDIA/Mellanox** | `15b3` | `mlx5_core` | `mlx5` | **Kernel** (no vfio) | Works with kernel driver |
| **Broadcom** | `14e4` | `bnxt_en` | `bnxt` | **vfio-pci** | NetXtreme-E (Thor/Thor2) |
| **Intel** | `8086` | `ice`, `ixgbe`, `i40e`, `igb` | `ice`, `ixgbe`, `i40e`, `igb` | **vfio-pci** | Various Intel Ethernet controllers |
| **AMD** | `1022`, `1023`, `1002` | Various | Various | **vfio-pci** | EPYC Embedded, Xilinx |

## Vendor-Specific Behavior

### NVIDIA/Mellanox (Vendor ID: 15b3)

**Special Handling:**
- ✅ **DO NOT bind to vfio-pci**
- ✅ Keep kernel driver `mlx5_core` loaded
- ✅ DPDK `mlx5` PMD works directly with kernel driver
- ✅ No driver binding required

**Supported Models:**
- ConnectX-3, ConnectX-4, ConnectX-5, ConnectX-6, ConnectX-7
- BlueField DPU

**Example:**
```bash
# No binding needed - DPDK works with kernel driver
# Just configure hugepages and use DPDK
```

### Broadcom (Vendor ID: 14e4)

**Binding Required:**
- ✅ Bind to `vfio-pci` for DPDK
- ✅ Kernel driver: `bnxt_en`
- ✅ DPDK PMD: `bnxt`

**Supported Models:**
- NetXtreme-E series (BCM57400, BCM57500)
- Thor/Thor2 adapters

**Example:**
```bash
# Bind to DPDK
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0

# Revert to kernel driver
sudo ./dpdk_start.sh --action revert --bdf 0000:8a:00.0
```

### Intel (Vendor ID: 8086)

**Binding Required:**
- ✅ Bind to `vfio-pci` for DPDK (typical)
- ✅ Various kernel drivers depending on model
- ✅ DPDK PMDs: `ice`, `ixgbe`, `i40e`, `igb`

**Supported Models:**
- Ethernet 700 Series (E810)
- Ethernet 800 Series
- X550, X540
- 82599 10 Gigabit Ethernet Controller
- Various other Intel Ethernet controllers

**Kernel Drivers:**
- `ice` - Intel Ethernet Controller E810 Series
- `ixgbe` - Intel 10 Gigabit Ethernet
- `i40e` - Intel 40 Gigabit Ethernet
- `igb` - Intel Gigabit Ethernet

**Example:**
```bash
# Bind to DPDK
sudo ./dpdk_start.sh --action bind --bdf 0000:01:00.0

# Revert to kernel driver (auto-detected or specify)
sudo ./dpdk_start.sh --action revert --bdf 0000:01:00.0 --kernel-driver ice
```

### AMD (Vendor IDs: 1022, 1023, 1002)

**Binding Required:**
- ✅ Bind to `vfio-pci` for DPDK (typical)
- ✅ Various kernel drivers depending on model
- ✅ DPDK PMDs vary by model

**Supported Models:**
- EPYC Embedded 3000 family NICs
- Xilinx network adapters (some models)

**Example:**
```bash
# Bind to DPDK
sudo ./dpdk_start.sh --action bind --bdf 0000:03:00.0

# Revert to kernel driver
sudo ./dpdk_start.sh --action revert --bdf 0000:03:00.0
```

## Installation Script Behavior

The `install_dpdk.sh` script automatically:

1. **Detects NIC vendor** by PCI vendor ID
2. **Shows appropriate guidance** for each vendor type
3. **Handles binding correctly** based on vendor:
   - Mellanox: Skips binding, keeps kernel driver
   - Others: Binds to vfio-pci

### Example Output

```
NIC to Interface Mapping Summary:

  • 0000:8a:00.0 → ens5np0 (bnxt_en) - Device 1760
    Vendor: Broadcom (ID: 14e4)
    ✅ Bind to vfio-pci for DPDK

  • 0000:9f:00.0 → ens6np0 (mlx5_core) - MT2910 Family [ConnectX-7]
    Vendor: NVIDIA/Mellanox (ID: 15b3)
    ⚠️  KEEP kernel driver (mlx5_core) - DPDK mlx5 PMD works with kernel driver

  • 0000:01:00.0 → enp1s0f0 (ice) - Intel Ethernet Controller E810
    Vendor: Intel (ID: 8086)
    ✅ Bind to vfio-pci for DPDK (typical)
```

## DPDK PMD Support

DPDK includes Poll Mode Drivers (PMDs) for many NIC vendors. The level of support varies:

- **Full Support**: NVIDIA/Mellanox, Broadcom, Intel (most models)
- **Good Support**: AMD (selected models)
- **Limited Support**: Some specialized or older NICs

## Checking Your NIC

To check your NIC vendor and model:

```bash
# List all network interfaces with vendor info
lspci | grep -i ethernet

# Get detailed info for a specific PCI device
lspci -n -s 0000:8a:00.0

# Check current driver
ls -l /sys/bus/pci/devices/0000:8a:00.0/driver
```

## Troubleshooting

### Issue: NIC not detected

**Solution:** Check if your NIC vendor ID is in the supported list. You can manually specify binding even if not auto-detected.

### Issue: Binding fails

**Solution:**
- Ensure IOMMU is enabled in BIOS/UEFI
- Check if interface has active routes (use `--force-bind` if needed)
- Verify kernel driver is not in use

### Issue: DPDK doesn't work after binding

**Solution:**
- Verify hugepages are configured
- Check DPDK PMD is available for your NIC model
- Review DPDK logs for PMD-specific errors

## Additional Resources

- [DPDK NIC Support List](https://doc.dpdk.org/guides/nics/)
- [DPDK PMD Documentation](https://doc.dpdk.org/guides/prog_guide/poll_mode_driver.html)
- Vendor-specific DPDK guides:
  - [NVIDIA DPDK Guide](https://developer.nvidia.com/networking/dpdk)
  - [Intel DPDK Guide](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-ethernet-controllers-dpdk.html)




