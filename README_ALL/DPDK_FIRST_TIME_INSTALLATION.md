# DPDK First-Time Installation Guide

This guide walks you through installing DPDK on the server for the first time.

## Prerequisites

- Root/sudo access
- Network connectivity (for downloading dependencies)
- At least 2GB free disk space
- A network interface card (NIC) that supports DPDK

## Step-by-Step Installation

### Step 1: Navigate to DPDK Scripts Directory

```bash
cd /opt/OSTG/resources/dpdk
```

### Step 2: Verify Current Status

First, check what's already installed:

```bash
sudo bash verify_dpdk.sh
```

This will show you:
- If DPDK is already installed
- Current PCI device bindings
- Hugepages status
- Available tools

### Step 3: Install Build Dependencies

Install required build tools and libraries:

```bash
sudo ./dpdk_start.sh --action deps
```

This installs:
- `build-essential` (gcc, make, etc.)
- `meson` (build system)
- `ninja-build` (build tool)
- `pkg-config` (package configuration)
- `libnuma-dev` (NUMA library)
- `libelf-dev` (ELF library)
- `libpcap-dev` (packet capture library)

**Note:** If you see network timeout errors, the script will retry automatically. Some "Contents" file errors are harmless and can be ignored.

### Step 4: Build DPDK from Source

Build DPDK from the source tree:

```bash
sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk
```

**What this does:**
- Configures DPDK using meson
- Compiles DPDK libraries and PMDs
- Installs DPDK to `/usr/local/`
- Takes 10-30 minutes depending on CPU

**If DPDK source doesn't exist:**

If `~/SURAJ/dpdk` doesn't exist, you'll need to clone DPDK first:

```bash
# Clone DPDK (if not already present)
cd ~
git clone https://dpdk.org/git/dpdk
cd dpdk
git checkout v23.11  # or latest stable version

# Then build
cd /opt/OSTG/resources/dpdk
sudo ./dpdk_start.sh --action build --dpdk-dir ~/dpdk
```

### Step 5: Verify DPDK Installation

Check if DPDK was installed successfully:

```bash
# Check DPDK version
pkg-config --modversion libdpdk

# Check DPDK libraries
ls -la /usr/local/lib/x86_64-linux-gnu/librte_*.so* | head -10

# Check DPDK tools
which dpdk-devbind.py
which dpdk-hugepages.py
```

Expected output:
- DPDK version: `25.11.0` (or similar)
- Libraries present in `/usr/local/lib/x86_64-linux-gnu/`
- Tools available in `/usr/local/bin/`

### Step 6: Build tx_worker Application

Build the DPDK packet generation application:

```bash
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src
```

**What this does:**
- Generates `tx_worker.c` source code
- Compiles `tx_worker` binary
- Links against DPDK libraries
- Output: `/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker`

### Step 7: Configure Hugepages (Required)

DPDK requires hugepages to function. Configure them:

```bash
# Set 1024 hugepages (2GB total) - adjust based on your needs
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Verify
cat /proc/meminfo | grep HugePages
```

**Or use the script:**

```bash
# For a specific PCI device (uses NUMA node)
sudo ./dpdk_start.sh --action huge --bdf 0000:8a:00.0 --hugepages-2mb 1024

# Or set globally
sudo ./dpdk_start.sh --action huge --hugepages-2mb 1024
```

### Step 8: (Optional) Bind NIC to DPDK

**Important:** This step depends on your NIC type:

#### For Broadcom/BNXT NICs:

```bash
# Find your PCI address first
dpdk-devbind.py --status

# Bind to DPDK (replace with your PCI address)
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0

# If you see "routes exist" error, use --force-bind
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0 --force-bind
```

#### For Mellanox/NVIDIA NICs:

**DO NOT bind to vfio-pci!** Mellanox NICs work with the kernel driver:

```bash
# Keep mlx5_core driver loaded (default)
# DPDK mlx5 PMD works with kernel driver
# No binding needed!
```

### Step 9: Final Verification

Run the verification script to confirm everything is ready:

```bash
sudo bash verify_dpdk.sh
```

**Expected output:**
```
✅ DPDK is installed and ready
✅ X device(s) bound to DPDK (or ⚠️ using kernel drivers for Mellanox)
✅ Hugepages configured
```

### Step 10: (Optional) Test DPDK

Run a smoke test to verify DPDK works:

```bash
# For Broadcom/BNXT (bound to vfio-pci)
sudo ./dpdk_start.sh --action test --bdf 0000:8a:00.0 --txpkts 64 --burst 64

# For Mellanox (using kernel driver)
sudo ./dpdk_start.sh --action test --bdf 0000:9f:00.0 --txpkts 64 --burst 64
```

## Complete Installation Script

Here's a complete script you can run:

```bash
#!/bin/bash
set -e

cd /opt/OSTG/resources/dpdk

echo "=== Step 1: Check Status ==="
sudo bash verify_dpdk.sh

echo ""
echo "=== Step 2: Install Dependencies ==="
sudo ./dpdk_start.sh --action deps

echo ""
echo "=== Step 3: Build DPDK ==="
sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk

echo ""
echo "=== Step 4: Build tx_worker ==="
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src

echo ""
echo "=== Step 5: Configure Hugepages ==="
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
cat /proc/meminfo | grep HugePages

echo ""
echo "=== Step 6: Final Verification ==="
sudo bash verify_dpdk.sh

echo ""
echo "=== Installation Complete! ==="
echo "Next steps:"
echo "  1. Bind NIC to DPDK (if Broadcom/BNXT):"
echo "     sudo ./dpdk_start.sh --action bind --bdf <PCI_ADDRESS>"
echo "  2. Test DPDK:"
echo "     sudo ./dpdk_start.sh --action test --bdf <PCI_ADDRESS>"
```

## Troubleshooting

### Issue: Network timeout during dependency installation

**Solution:** The script has retry logic. If it still fails:
```bash
# Try a different mirror or wait and retry
sudo apt-get update --option Acquire::http::Timeout=60
sudo ./dpdk_start.sh --action deps
```

### Issue: DPDK source not found

**Solution:** Clone DPDK first:
```bash
cd ~
git clone https://dpdk.org/git/dpdk
cd dpdk
git checkout v23.11
```

### Issue: Build fails with "rte_config.h not found"

**Solution:** Make sure DPDK is installed:
```bash
cd ~/SURAJ/dpdk/build
sudo ninja install
```

### Issue: Permission denied

**Solution:** All DPDK operations require sudo:
```bash
sudo ./dpdk_start.sh --action build
```

### Issue: Hugepages not persisting after reboot

**Solution:** Add to `/etc/sysctl.conf`:
```bash
echo "vm.nr_hugepages=1024" >> /etc/sysctl.conf
sysctl -p
```

Or add to `/etc/rc.local`:
```bash
echo "echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages" >> /etc/rc.local
```

## Quick Reference

| Step | Command | Purpose |
|------|---------|---------|
| Check status | `sudo bash verify_dpdk.sh` | Verify installation |
| Install deps | `sudo ./dpdk_start.sh --action deps` | Install build tools |
| Build DPDK | `sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk` | Build DPDK |
| Build app | `sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src` | Build tx_worker |
| Set hugepages | `echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages` | Configure hugepages |
| Bind NIC | `sudo ./dpdk_start.sh --action bind --bdf <PCI>` | Bind to DPDK |
| Test | `sudo ./dpdk_start.sh --action test --bdf <PCI>` | Test DPDK |

## Next Steps

After installation:

1. **Configure hugepages** (if not done)
2. **Bind NICs** (for Broadcom/BNXT only)
3. **Enable DPDK in UI** - When creating streams, check "Enable DPDK"
4. **Start traffic** - DPDK will be used automatically for high-speed generation

## Support

For issues, check:
- `README_ALL/DPDK_INSTALLATION_TROUBLESHOOTING.md` - Common issues
- `README_ALL/DPDK_SCRIPTS_USAGE_GUIDE.md` - Script usage
- Server logs: `/var/log/ostg-server.log`




