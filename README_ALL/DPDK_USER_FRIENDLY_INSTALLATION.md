# User-Friendly DPDK Installation Guide

This guide explains how to use the new interactive DPDK installation script that makes installation much easier for first-time users.

## Quick Start

### Interactive Installation (Recommended)

Simply run the installation script and follow the prompts:

```bash
cd /opt/OSTG/resources/dpdk
sudo ./install_dpdk.sh
```

The script will:
- ✅ Check system requirements
- ✅ Detect DPDK source automatically
- ✅ Guide you through each step
- ✅ Show progress and status
- ✅ Handle errors gracefully
- ✅ Provide helpful prompts and suggestions

### Automated Installation (Non-Interactive)

For automated installations or scripts:

```bash
cd /opt/OSTG/resources/dpdk
sudo ./install_dpdk.sh --auto
```

This runs with default values and minimal prompts.

## Features

### 🎯 Automatic Detection

- **DPDK Source**: Automatically finds DPDK source in common locations
- **NIC Type**: Detects Mellanox vs Broadcom/BNXT NICs
- **System Status**: Checks disk space, network, and existing installations

### 📋 Interactive Prompts

- **Yes/No Questions**: Clear prompts for each step
- **Input Validation**: Validates user input before proceeding
- **Default Values**: Sensible defaults for all options

### 📊 Progress Indicators

- **Color-coded Output**: Green (success), Yellow (warning), Red (error)
- **Step Numbers**: Clear step-by-step progress
- **Status Messages**: Detailed status for each operation

### 🛡️ Error Handling

- **Graceful Failures**: Continues when possible, exits when critical
- **Retry Options**: Offers to retry failed operations
- **Rollback Info**: Provides information to undo changes

## Installation Steps

The script guides you through these steps:

### Step 1: Pre-flight Checks
- Verifies root access
- Checks disk space (needs 2GB+)
- Checks network connectivity
- Detects existing DPDK installation

### Step 2: DPDK Source Detection
- Searches common locations for DPDK source
- Offers to clone if not found
- Validates DPDK source directory

### Step 3: Clone DPDK (if needed)
- Automatically clones DPDK if source not found
- Checks out stable version (v23.11)

### Step 4: Install Dependencies
- Installs build tools (meson, ninja, etc.)
- Shows what will be installed
- Handles network timeouts gracefully

### Step 5: Build DPDK
- Builds DPDK from source
- Shows estimated time (10-30 minutes)
- Can be skipped with `--skip-build` flag

### Step 6: Build tx_worker
- Builds the DPDK packet generation application
- Links against DPDK libraries

### Step 7: Configure Hugepages
- Prompts for number of hugepages
- Provides recommendations based on use case
- Verifies configuration

### Step 8: NIC Binding (Optional)
- Shows available NICs
- Detects NIC type (Mellanox vs Broadcom)
- Handles binding appropriately
- Warns about Mellanox (no binding needed)

### Step 9: Verification
- Runs comprehensive verification
- Checks all components
- Reports any issues

### Step 10: Summary
- Shows completion status
- Provides next steps
- Links to documentation

## Usage Examples

### Example 1: First-Time Installation

```bash
cd /opt/OSTG/resources/dpdk
sudo ./install_dpdk.sh
```

**Interactive prompts:**
```
[INFO] Checking system requirements...
[✓] Disk space OK: 50GB available
[✓] Network connectivity OK
[INFO] Searching for DPDK source...
[✓] DPDK source detected: /root/SURAJ/dpdk
[INFO] This will install: build-essential, meson, ninja-build...
Install dependencies? [Y/n]: y
[INFO] Running dependency installation...
[✓] Dependencies installed successfully
...
```

### Example 2: Skip Build (DPDK Already Built)

```bash
sudo ./install_dpdk.sh --skip-build
```

### Example 3: Specify DPDK Directory

```bash
sudo ./install_dpdk.sh --dpdk-dir /opt/dpdk
```

### Example 4: Automated Installation

```bash
sudo ./install_dpdk.sh --auto --dpdk-dir ~/SURAJ/dpdk
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--auto` | Run in non-interactive mode (use defaults) |
| `--skip-build` | Skip DPDK build step |
| `--dpdk-dir DIR` | Specify DPDK source directory |
| `--help, -h` | Show help message |

## Comparison: Old vs New

### Old Way (Manual Steps)

```bash
# Step 1: Check status
sudo bash verify_dpdk.sh

# Step 2: Install deps
sudo ./dpdk_start.sh --action deps

# Step 3: Build DPDK
sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk

# Step 4: Build tx_worker
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src

# Step 5: Configure hugepages
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Step 6: Verify
sudo bash verify_dpdk.sh
```

**Issues:**
- ❌ Need to know exact commands
- ❌ No error handling
- ❌ No progress indication
- ❌ Manual verification at each step

### New Way (Interactive Script)

```bash
# Single command - guided installation
sudo ./install_dpdk.sh
```

**Benefits:**
- ✅ Guided step-by-step process
- ✅ Automatic error handling
- ✅ Progress indicators
- ✅ Helpful prompts and suggestions
- ✅ Automatic detection
- ✅ Validation at each step

## Troubleshooting

### Issue: Script asks too many questions

**Solution:** Use `--auto` flag for non-interactive mode:
```bash
sudo ./install_dpdk.sh --auto
```

### Issue: DPDK source not found

**Solution:** The script will offer to clone DPDK automatically, or specify location:
```bash
sudo ./install_dpdk.sh --dpdk-dir /path/to/dpdk
```

### Issue: Build takes too long

**Solution:** Skip build if DPDK is already built:
```bash
sudo ./install_dpdk.sh --skip-build
```

### Issue: Permission denied

**Solution:** Make sure to run with sudo:
```bash
sudo ./install_dpdk.sh
```

## Advanced Usage

### Custom Installation Path

```bash
export DPDK_DIR=/custom/path/to/dpdk
sudo ./install_dpdk.sh
```

### Silent Installation (for scripts)

```bash
sudo ./install_dpdk.sh --auto --dpdk-dir ~/SURAJ/dpdk 2>&1 | tee install.log
```

### Partial Installation

You can still use individual scripts for specific steps:
- `dpdk_start.sh` - For specific actions (bind, hugepages, test)
- `dpdk_tx_worker.sh` - For rebuilding tx_worker
- `verify_dpdk.sh` - For status checks

## Next Steps After Installation

1. **Verify Installation:**
   ```bash
   sudo bash verify_dpdk.sh
   ```

2. **Bind NICs** (if not done during installation):
   ```bash
   sudo ./dpdk_start.sh --action bind --bdf <PCI_ADDRESS>
   ```

3. **Test DPDK:**
   ```bash
   sudo ./dpdk_start.sh --action test --bdf <PCI_ADDRESS>
   ```

4. **Enable DPDK in UI:**
   - When creating streams, check "Enable DPDK"
   - DPDK will be used automatically for high-speed traffic

## Support

For issues or questions:
- Check `README_ALL/DPDK_FIRST_TIME_INSTALLATION.md` - Detailed manual guide
- Check `README_ALL/DPDK_SCRIPTS_USAGE_GUIDE.md` - Script usage reference
- Check `README_ALL/DPDK_INSTALLATION_TROUBLESHOOTING.md` - Common issues




