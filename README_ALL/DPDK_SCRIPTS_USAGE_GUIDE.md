# DPDK Scripts Usage Guide

This guide explains when and how to use the DPDK scripts located in `/opt/OSTG/resources/dpdk/`.

## Available Scripts

1. **`verify_dpdk.sh`** - Check DPDK installation and PCI bindings
2. **`dpdk_start.sh`** - Setup DPDK environment (hugepages, bind NICs, test)
3. **`dpdk_tx_worker.sh`** - Build the DPDK tx_worker application

---

## 1. `verify_dpdk.sh` - Verification Script

### **When to Use:**
- ✅ **First time setup** - Verify DPDK is installed correctly
- ✅ **Before using DPDK** - Check if devices are bound and hugepages configured
- ✅ **Troubleshooting** - Diagnose DPDK-related issues
- ✅ **Regular checks** - Monitor DPDK status

### **Usage:**
```bash
cd /opt/OSTG/resources/dpdk
sudo bash verify_dpdk.sh
```

### **What it checks:**
- DPDK library installation and version
- DPDK tools availability (`dpdk-devbind.py`, `dpdk-hugepages.py`)
- `tx_worker` binary existence and linking
- PCI device bindings (which devices are bound to DPDK)
- Kernel modules (vfio-pci, uio_pci_generic, etc.)
- Hugepages configuration
- Available PMD libraries

### **Example Output:**
```
✅ DPDK is installed and ready
⚠️  No devices bound to DPDK (using kernel drivers)
⚠️  Hugepages not configured
```

---

## 2. `dpdk_start.sh` - DPDK Environment Setup

### **When to Use:**
- ✅ **Initial setup** - Configure hugepages and bind NICs to DPDK
- ✅ **Before high-speed traffic** - Prepare system for DPDK packet generation
- ✅ **Testing DPDK** - Run smoke tests with `dpdk-testpmd`
- ✅ **Reverting changes** - Return NICs to kernel drivers

### **Common Actions:**

#### **Show Current Status:**
```bash
cd /opt/OSTG/resources/dpdk
sudo ./dpdk_start.sh --show
```

#### **Install Dependencies:**
```bash
sudo ./dpdk_start.sh --action deps
```

#### **Build DPDK (if needed):**
```bash
sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk
```

#### **Bind NIC to DPDK (Broadcom/BNXT):**
```bash
# By PCI address
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0

# By interface name
sudo ./dpdk_start.sh --action bind --iface ens5np0

# Force bind if routes exist
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0 --force-bind
```

#### **Configure Hugepages:**
```bash
# Set 1024 hugepages (2GB) for a specific PCI device
sudo ./dpdk_start.sh --action huge --bdf 0000:8a:00.0 --hugepages-2mb 1024

# Or set hugepages globally
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

#### **Test DPDK (smoke test):**
```bash
sudo ./dpdk_start.sh --action test --bdf 0000:8a:00.0 --txpkts 64 --burst 64
```

#### **Revert NIC to Kernel Driver:**
```bash
# Auto-detect kernel driver
sudo ./dpdk_start.sh --action revert --bdf 0000:8a:00.0

# Specify kernel driver explicitly
sudo ./dpdk_start.sh --action revert --bdf 0000:8a:00.0 --kernel-driver bnxt_en
```

#### **Do Everything (Full Setup):**
```bash
# Complete setup: deps + build + bind + hugepages + test
sudo ./dpdk_start.sh --iface ens5np0

# Skip build if DPDK already built
sudo ./dpdk_start.sh --bdf 0000:8a:00.0 --no-build
```

### **Important Notes:**

**Mellanox/NVIDIA NICs (vendor 15b3):**
- ⚠️ **DO NOT bind to vfio-pci** - The `mlx5` PMD works with kernel driver `mlx5_core`
- ✅ Keep `mlx5_core` loaded
- ✅ Script automatically skips vfio-pci binding for Mellanox devices

**Broadcom/BNXT NICs:**
- ✅ Bind to `vfio-pci` for DPDK
- ✅ Use `bnxt_en` as kernel driver when reverting

---

## 3. `dpdk_tx_worker.sh` - Build DPDK Application

### **When to Use:**
- ✅ **First time build** - Build the `tx_worker` binary
- ✅ **After DPDK updates** - Rebuild after DPDK library changes
- ✅ **Code changes** - Rebuild after modifying `tx_worker.c`
- ✅ **Missing binary** - If `tx_worker` binary is missing

### **Usage:**

#### **Build with Dependencies Installation:**
```bash
cd /opt/OSTG/resources/dpdk
sudo ./dpdk_tx_worker.sh --install-deps --rewrite-src
```

#### **Build Using Existing DPDK:**
```bash
# Using DPDK build tree
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src

# Using pkg-config directory
sudo ./dpdk_tx_worker.sh --dpdk-pc-dir /usr/local/lib/x86_64-linux-gnu/pkgconfig --rewrite-src
```

#### **Build and Install:**
```bash
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --install --rewrite-src
```

### **Output:**
The binary will be built at:
```
/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
```

---

## Typical Workflow

### **Scenario 1: First Time DPDK Setup**

```bash
# 1. Verify current status
cd /opt/OSTG/resources/dpdk
sudo bash verify_dpdk.sh

# 2. Install dependencies and build DPDK
sudo ./dpdk_start.sh --action deps
sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk

# 3. Build tx_worker
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src

# 4. Configure hugepages
sudo ./dpdk_start.sh --action huge --bdf 0000:8a:00.0 --hugepages-2mb 1024

# 5. Bind NIC to DPDK (if Broadcom/BNXT)
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0

# 6. Verify everything is ready
sudo bash verify_dpdk.sh
```

### **Scenario 2: Using DPDK for High-Speed Traffic**

```bash
# 1. Check status
cd /opt/OSTG/resources/dpdk
sudo bash verify_dpdk.sh

# 2. If hugepages not configured, set them up
sudo ./dpdk_start.sh --action huge --bdf 0000:8a:00.0 --hugepages-2mb 2048

# 3. If NIC not bound (for Broadcom/BNXT), bind it
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0

# 4. Verify
sudo bash verify_dpdk.sh

# 5. Now DPDK is ready - traffic generation will use DPDK automatically
#    when stream has DPDK enabled in the UI
```

### **Scenario 3: Revert NIC Back to Kernel**

```bash
# Unbind from DPDK and return to kernel driver
cd /opt/OSTG/resources/dpdk
sudo ./dpdk_start.sh --action revert --bdf 0000:8a:00.0

# Verify
sudo bash verify_dpdk.sh
```

### **Scenario 4: Troubleshooting**

```bash
# 1. Check overall status
cd /opt/OSTG/resources/dpdk
sudo bash verify_dpdk.sh

# 2. Check PCI bindings
dpdk-devbind.py --status

# 3. Check hugepages
cat /proc/meminfo | grep -i huge

# 4. Test DPDK
sudo ./dpdk_start.sh --action test --bdf 0000:8a:00.0
```

---

## Quick Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `verify_dpdk.sh` | Check DPDK status | Before using DPDK, troubleshooting |
| `dpdk_start.sh` | Setup DPDK environment | Initial setup, bind NICs, configure hugepages |
| `dpdk_tx_worker.sh` | Build DPDK app | Build/rebuild tx_worker binary |

---

## Common Commands Cheat Sheet

```bash
# Quick status check
sudo bash verify_dpdk.sh

# Show PCI bindings
dpdk-devbind.py --status

# Bind device to DPDK
dpdk-devbind.py --bind=vfio-pci 0000:8a:00.0

# Unbind device (return to kernel)
dpdk-devbind.py --bind=bnxt_en 0000:8a:00.0

# Set hugepages
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Check hugepages
cat /proc/meminfo | grep HugePages
```

---

## Notes

- **All scripts require `sudo`** - DPDK operations need root privileges
- **Mellanox NICs** - Don't bind to vfio-pci, keep `mlx5_core` driver
- **Broadcom/BNXT NICs** - Bind to `vfio-pci` for DPDK
- **Hugepages are required** - DPDK needs hugepages to function
- **Verify before use** - Always run `verify_dpdk.sh` before using DPDK




