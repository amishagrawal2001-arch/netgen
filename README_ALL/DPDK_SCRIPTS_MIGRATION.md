# DPDK Scripts Migration Guide

## Overview

The DPDK scripts have been reorganized for better modularity and user-friendliness. This guide explains the migration from old scripts to new ones.

## Script Changes

### New Scripts (Recommended)

| Script | Purpose | Replaces |
|--------|---------|----------|
| `install_dpdk.sh` | **Complete DPDK installation** | `dpdk_start.sh --action deps/build` |
| `dpdk_bind.sh` | **NIC binding/unbinding** | `dpdk_start.sh --action bind/revert` |
| `verify_dpdk.sh` | **Status verification** | `dpdk_start.sh --show` |
| `dpdk_tx_worker.sh` | **Build tx_worker app** | (Standalone, used by install_dpdk.sh) |

### Deprecated Scripts

| Script | Status | Replacement |
|--------|--------|-------------|
| `dpdk_start.sh` | ⚠️ **Deprecated** | Use new scripts above |

## Migration Table

### Installation

**Old Way:**
```bash
sudo ./dpdk_start.sh --action deps
sudo ./dpdk_start.sh --action build --dpdk-dir ~/SURAJ/dpdk
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src
```

**New Way:**
```bash
sudo ./install_dpdk.sh
```

### NIC Binding

**Old Way:**
```bash
sudo ./dpdk_start.sh --action bind --bdf 0000:8a:00.0
sudo ./dpdk_start.sh --action revert --bdf 0000:8a:00.0
```

**New Way:**
```bash
sudo ./dpdk_bind.sh bind 0000:8a:00.0
sudo ./dpdk_bind.sh unbind 0000:8a:00.0
```

### Status Check

**Old Way:**
```bash
sudo ./dpdk_start.sh --show
```

**New Way:**
```bash
sudo ./dpdk_bind.sh status
# or
sudo bash verify_dpdk.sh
```

### Hugepages Configuration

**Old Way:**
```bash
sudo ./dpdk_start.sh --action huge --bdf 0000:8a:00.0 --hugepages-2mb 1024
```

**New Way:**
```bash
# During installation (interactive)
sudo ./install_dpdk.sh

# Or manually
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

## Feature Comparison

| Feature | dpdk_start.sh | New Scripts |
|---------|---------------|-------------|
| **Install dependencies** | ✅ `--action deps` | ✅ `install_dpdk.sh` Step 4 |
| **Build DPDK** | ✅ `--action build` | ✅ `install_dpdk.sh` Step 5 |
| **Build tx_worker** | ❌ Separate script | ✅ `install_dpdk.sh` Step 6 |
| **Configure hugepages** | ✅ `--action huge` | ✅ `install_dpdk.sh` Step 7 |
| **Bind NIC** | ✅ `--action bind` | ✅ `dpdk_bind.sh bind` |
| **Unbind NIC** | ✅ `--action revert` | ✅ `dpdk_bind.sh unbind` |
| **Show status** | ✅ `--action show` | ✅ `dpdk_bind.sh status` |
| **Test DPDK** | ✅ `--action test` | ⚠️ Manual (see below) |
| **Interactive mode** | ❌ No | ✅ Yes (`install_dpdk.sh`) |
| **Vendor detection** | ✅ Basic | ✅ Enhanced |

## Missing Features

### DPDK Test (dpdk-testpmd)

The `--action test` feature from `dpdk_start.sh` is not directly replaced. You can run it manually:

```bash
# Manual test
cd ~/SURAJ/dpdk/build
sudo ./app/dpdk-testpmd -l 0-1 -n 4 -a 0000:8a:00.0 -- --forward-mode=txonly --txpkts=64
```

Or create a simple test script if needed.

## Why the Change?

### Benefits of New Scripts

1. **Modularity**: Each script has a single, clear purpose
2. **User-friendly**: Interactive installation with guided steps
3. **Better error handling**: More robust and informative
4. **Vendor-aware**: Enhanced NIC vendor detection and handling
5. **Self-contained**: `install_dpdk.sh` doesn't depend on other scripts

### Problems with dpdk_start.sh

1. **Too many responsibilities**: Does deps, build, bind, test, revert, show
2. **Not user-friendly**: Requires knowing exact commands
3. **No interactive mode**: All actions require command-line flags
4. **Less modular**: Hard to use individual features

## Backward Compatibility

`dpdk_start.sh` is still available but **deprecated**. It will continue to work, but:

- ⚠️ No new features will be added
- ⚠️ May be removed in future versions
- ✅ Use new scripts for better experience

## Quick Reference

### For First-Time Users

```bash
# One command does everything
sudo ./install_dpdk.sh
```

### For Advanced Users

```bash
# Bind/unbind NICs
sudo ./dpdk_bind.sh bind 0000:8a:00.0
sudo ./dpdk_bind.sh unbind 0000:8a:00.0

# Check status
sudo ./dpdk_bind.sh status
sudo bash verify_dpdk.sh

# Rebuild tx_worker
sudo ./dpdk_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk --rewrite-src
```

## Summary

| Task | Old Script | New Script |
|------|------------|------------|
| **Install DPDK** | `dpdk_start.sh --action deps/build` | `install_dpdk.sh` |
| **Bind NIC** | `dpdk_start.sh --action bind` | `dpdk_bind.sh bind` |
| **Unbind NIC** | `dpdk_start.sh --action revert` | `dpdk_bind.sh unbind` |
| **Check Status** | `dpdk_start.sh --show` | `dpdk_bind.sh status` |
| **Verify Setup** | `dpdk_start.sh --show` | `verify_dpdk.sh` |

**Recommendation**: Use the new scripts for a better experience!




