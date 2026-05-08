# Link Down Troubleshooting - Additional Diagnostics

This document describes the additional diagnostics collected by `collect_bf3_link_flap_logs.sh` to help troubleshoot link down issues.

## New Diagnostics Added

### 1. **PCIe Link Status**
   - **Location**: `interfaces/<iface>/<iface>_info.txt`
   - **What it collects**:
     - Current PCIe link speed and width
     - Maximum PCIe link speed and width
     - PCIe slot information
   - **Why it helps**: PCIe link issues can cause network interface problems

### 2. **Interrupt Statistics**
   - **Location**: `interfaces/<iface>/<iface>_interrupts.txt`
   - **What it collects**:
     - CPU interrupt distribution for the interface
     - Interrupt rate and patterns
   - **Why it helps**: High interrupt rates or CPU imbalance can cause packet drops and link issues

### 3. **Interface Statistics History**
   - **Location**: `interfaces/<iface>/<iface>_stats_history.txt`
   - **What it collects**:
     - Multiple snapshots of interface statistics over time (3 samples with 2-second intervals)
     - RX/TX packet counts, errors, drops
   - **Why it helps**: Shows patterns in packet loss or errors that correlate with link downs

### 4. **Power Management Settings**
   - **Location**: `interfaces/<iface>/<iface>_power_management.txt`
   - **What it collects**:
     - Interface power control settings
     - PCIe device power state
     - PCIe ASPM (Active State Power Management) configuration
   - **Why it helps**: Aggressive power management can cause link drops

### 5. **Link State History with Timestamps**
   - **Location**: `interfaces/<iface>/<iface>_link_history.txt`
   - **What it collects**:
     - Detailed timeline of link up/down events from dmesg
     - Journalctl link events with timestamps
     - Human-readable timestamps for correlation
   - **Why it helps**: Identifies patterns and timing of link downs

### 6. **Driver-Specific Diagnostics**
   - **Location**: `interfaces/<iface>/<iface>_driver_diagnostics.txt`
   - **What it collects**:
     - Driver version and firmware version
     - Driver module parameters
     - Interface statistics from sysfs
   - **Why it helps**: Driver bugs or misconfiguration can cause link issues

### 7. **Link Partner Information**
   - **Location**: `interfaces/<iface>/<iface>_neighbor_info.txt`
   - **What it collects**:
     - LLDP neighbor information (if available)
     - Link partner advertised capabilities
     - Supported link modes
   - **Why it helps**: Mismatched link partner settings can cause link instability

### 8. **Temperature and Thermal Diagnostics**
   - **Location**: `system/temperature.txt`
   - **What it collects**:
     - System temperature sensors
     - PCI device temperature (if available)
     - Thermal zone information
   - **Why it helps**: Overheating can cause link drops

### 9. **PCIe Error Logs**
   - **Location**: `pci/pcie_errors.txt`
   - **What it collects**:
     - PCIe AER (Advanced Error Reporting) errors
     - PCIe link retrain events
     - Correctable/uncorrectable PCIe errors
   - **Why it helps**: PCIe errors can cause interface issues

### 10. **System Load and Resource Usage**
   - **Location**: `system/system_load.txt`
   - **What it collects**:
     - CPU load average
     - Memory usage
     - I/O wait statistics
     - Network interface statistics
     - SoftIRQ statistics (network-related)
   - **Why it helps**: High system load can correlate with link downs

### 11. **SFP+/Optics Detailed Diagnostics**
   - **Location**: `system/optics_detailed.txt`
   - **What it collects**:
     - SFP+ module information from sysfs
     - QSFP+ module data
     - DOM (Digital Optical Monitoring) data if available
   - **Why it helps**: Faulty optics or cables can cause link downs

### 12. **Enhanced mlxlink Diagnostics**
   - **Location**: `nvidia/_dev_mst_*.txt`
   - **What it collects** (if supported):
     - BER (Bit Error Rate) information
     - Eye diagram data
     - PRBS (Pseudo-Random Binary Sequence) test results
   - **Why it helps**: Signal quality issues can cause link instability

### 13. **DPU/BlueField Diagnostics**
   - **Location**: `dpu/dpu_local.txt`, `dpu/bf*.txt`, `dpu/dpfctl*.txt`
   - **What it collects**:
     - DPU detection and PCI device identification
     - DPU kernel messages and journal logs
     - DPU firmware information
     - DPU device tree information
     - DPU-specific tool outputs (bfb, bfshell, bf-telemetry, dpfctl, bfconfig)
     - DPU system logs from /run/log/dpulogs
   - **Why it helps**: DPU issues can affect network performance and link stability

### 14. **GPU Diagnostics**
   - **Location**: `gpu/gpu_info.txt`, `gpu/nvidia_gpu_detailed.txt`
   - **What it collects**:
     - GPU detection and PCI device identification
     - GPU PCIe link information (speed, width, status)
     - NVIDIA GPU information (if nvidia-smi available)
     - GPU kernel messages and journal logs
     - GPU driver information (DRM devices)
     - GPU module information
     - GPU temperature monitoring
     - GPU power management settings
     - NVLink information (for NVIDIA GPUs, if available)
   - **Why it helps**: GPU PCIe link issues can affect system stability and may correlate with network issues

## Enhanced Critical Issues Analysis

The script now also checks for:

1. **PCIe Link Issues**: Detects PCIe errors and link retrains
2. **Temperature Issues**: Flags high temperatures (>80°C)
3. **Correlation Analysis**: Links system load with link down events

## Usage Recommendations

1. **Run during active link down events** if possible to capture real-time data
2. **Use longer time windows** (`-t 24` or more) to capture historical patterns
3. **Check the critical issues summary** at the end of script execution
4. **Review interface-specific diagnostics** in `interfaces/<iface>/` directory
5. **Correlate timestamps** between different log files to identify root causes

## Common Root Causes Identified by These Diagnostics

- **Physical Layer**: Optics/cable issues (SFP+ diagnostics, BER)
- **PCIe Issues**: Link width/speed problems (PCIe diagnostics)
- **Power Management**: Aggressive ASPM causing link drops (power management logs)
- **Driver Issues**: Driver bugs or misconfiguration (driver diagnostics)
- **System Load**: High CPU/memory pressure (system load logs)
- **Temperature**: Overheating causing instability (temperature logs)
- **Link Partner**: Mismatched settings with switch (neighbor info)

## Commands Executed by the Script

All commands executed by `collect_bf3_link_flap_logs.sh` are logged to `commands_run.txt` in the output directory. Below is a comprehensive list of all commands organized by category:

### System-Level Commands

```bash
# System information
uname -a
lsb_release -a
cat /etc/os-release
date
uptime
dmesg | tail -200
lscpu
numactl --hardware
lsmod
lsblk
free -h
```

### PCI Device Detection and Information

```bash
# PCI device detection
lspci -D
lspci -nn
lspci -vvv
lspci -D | grep -Ei "Mellanox|NVIDIA.*(ConnectX|BlueField)" | awk '{print $1}'

# Per-PCI-slot information
lspci -s <slot> -vvv
sudo grep . /sys/bus/pci/devices/<slot>/aer/*
sudo cat /sys/bus/pci/devices/<slot>/current_link_speed
sudo cat /sys/bus/pci/devices/<slot>/current_link_width
sudo cat /sys/bus/pci/devices/<slot>/max_link_speed
sudo cat /sys/bus/pci/devices/<slot>/max_link_width
```

### Temperature and Thermal Diagnostics

```bash
# Temperature sensors
sensors 2>/dev/null
cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -20

# PCI device temperature
find /sys -name "*temp*" -path "*/pci*" -type f 2>/dev/null | head -20
```

### NVIDIA/Mellanox Firmware and Configuration

```bash
# MST (Mellanox Software Tools)
mst start
mst status
mst status 2>/dev/null | awk '/^\/dev\/mst\// {print $1}'

# Firmware management
mlxfwmanager --version
mlxfwmanager --query
mlxfwmanager --log

# Device configuration
mlxconfig -d <dev> query

# mlxlink diagnostics (if supported)
mlxlink -d <dev> -p <port> --ber
mlxlink -d <dev> -p <port> --eye
mlxlink -d <dev> -p <port> --prbs
mlxlink -d <dev> -p <port> --fec-histogram
mlxlink -d <dev> -p <port> --counters
mlxlink -d <dev> -p <port> --pmaos
mlxlink -d <dev> -p <port> --stats
mlxlink -d <dev> --rx_fec_histogram --show_histogram
```

### Interface-Specific Commands

```bash
# Basic interface information
ip addr show dev <iface>
ip -s link show dev <iface>
ip -d link show dev <iface>
ethtool <iface>
ethtool -i <iface>
ethtool -S <iface>
ethtool -k <iface>
ethtool --show-priv-flags <iface> 2>/dev/null
ethtool -m <iface> 2>/dev/null
ethtool --show-eee <iface> 2>/dev/null
ethtool --phy-statistics <iface> 2>/dev/null

# Interface statistics
ethtool <iface> | grep -E "Supported|Advertised|Link|Speed|Duplex"

# PCIe link information for interface
readlink -f /sys/class/net/<iface>/device
cat /sys/class/net/<iface>/device/current_link_speed
cat /sys/class/net/<iface>/device/current_link_width
cat /sys/class/net/<iface>/device/max_link_speed
cat /sys/class/net/<iface>/device/max_link_width

# Interrupt statistics
cat /proc/interrupts | grep -i <iface>

# Power management
cat /sys/class/net/<iface>/device/power/runtime_status
cat /sys/class/net/<iface>/device/power/control
cat /sys/class/net/<iface>/power/control

# Interface state
cat /sys/class/net/<iface>/carrier
cat /sys/class/net/<iface>/operstate
cat /sys/class/net/<iface>/speed
cat /sys/class/net/<iface>/duplex
cat /sys/class/net/<iface>/mtu

# Interface statistics from sysfs
cat /sys/class/net/<iface>/statistics/rx_errors
cat /sys/class/net/<iface>/statistics/tx_errors
cat /sys/class/net/<iface>/statistics/rx_dropped
cat /sys/class/net/<iface>/statistics/tx_dropped
cat /sys/class/net/<iface>/statistics/rx_crc_errors
cat /sys/class/net/<iface>/statistics/collisions

# Link history
dmesg | grep -i <iface> | grep -iE "[Ll]ink" | tail -50
journalctl -k --since "<hours> hours ago" | grep -i <iface> | grep -iE "[Ll]ink" | tail -50
dmesg -T 2>/dev/null | grep -i <iface> | grep -iE "[Ll]ink" | tail -30
grep -i <iface> /var/log/kern.log | grep -iE "[Ll]ink" | tail -50

# Driver information
ethtool -i <iface> | grep -E "driver|version|firmware"
modinfo <driver_name> 2>/dev/null
ls -la /sys/module/<driver_name>/parameters/ 2>/dev/null

# Link partner information
ethtool <iface> | grep -A 20 "Advertised\|Supported\|Link partner"
lldpctl -f json 2>/dev/null
```

### System Load and Resource Usage

```bash
# System load
uptime
top -bn1 | head -20
free -h

# I/O statistics
iostat -x 1 3 2>/dev/null
iostat -xz 1 5 2>/dev/null

# CPU statistics
mpstat -P ALL 1 5 2>/dev/null
sar -u 1 5 2>/dev/null
sar -q 1 5 2>/dev/null
sar -r 1 5 2>/dev/null
sar -b 1 5 2>/dev/null
sar -d 1 5 2>/dev/null
sar -n DEV 1 5 2>/dev/null

# Virtual memory statistics
vmstat 1 5

# Network statistics
cat /proc/net/dev
cat /proc/softirqs | grep -E "NET|NET_RX|NET_TX"
cat /proc/interrupts
cat /proc/loadavg
```

### Kernel and Journal Logs

```bash
# Kernel messages
dmesg
dmesg | grep -i -e mlx -e nvidia -e bluefield -e bf3
dmesg | tail -500

# Journal logs
journalctl -k --since "<hours> hours ago"
journalctl -k --since "<hours> hours ago" | grep -i -e mlx -e nvidia -e pci -e link
journalctl -u NetworkManager --since "<hours> hours ago"

# Kernel log file
tail -500 /var/log/kern.log
grep -iE "link down|link up|mlx|nvidia|pci" /var/log/kern.log | tail -200
```

### PCIe Error Logs

```bash
# PCIe errors
dmesg | grep -iE "pci.*error|pcie.*error|aer.*error" | tail -50
dmesg | grep -iE "pci.*retrain|link.*retrain" | tail -50

# PCIe AER (Advanced Error Reporting)
find /sys -name "*aer*" -type f 2>/dev/null | head -30
grep -r . /sys/bus/pci/devices/<slot>/aer/* 2>/dev/null
```

### DPU/BlueField Diagnostics

```bash
# DPU detection
lspci -D | grep -qi "BlueField\|DPU"
lspci -D | grep -iE "BlueField|DPU|Mellanox.*ConnectX"

# DPU kernel messages
dmesg | grep -iE "bluefield|dpu|bf[0-9]|bfb" | tail -100
journalctl -k --since "<hours> hours ago" | grep -iE "bluefield|dpu|bf[0-9]|bfb" | tail -100
grep -iE "bluefield|dpu|bf[0-9]|bfb" /var/log/kern.log | tail -100

# DPU firmware information
find /sys -name "*bluefield*" -o -name "*dpu*" 2>/dev/null | head -20

# DPU device tree
find /sys/firmware/devicetree -name "*bluefield*" -o -name "*dpu*" 2>/dev/null | head -20

# DPU tools (if available)
bfb --version
bfb --status
bfshell -c "show port"
bfshell -c "show system"
bfshell -c "show version"
bf-telemetry -a
bf-telemetry --status
dpfctl version
dpfctl status
dpfctl show resources
bfconfig --version
bfconfig --status

# DPU system logs
ls -lah /run/log/dpulogs/ 2>/dev/null
find /run/log/dpulogs -type f -exec tail -50 {} \; 2>&1
```

### GPU Diagnostics

```bash
# GPU detection
lspci -D | grep -qiE "VGA|3D|Display.*NVIDIA|AMD.*GPU|Intel.*Graphics"
lspci -D | grep -iE "VGA|3D|Display|Graphics|GPU"

# NVIDIA GPU information (if nvidia-smi available)
nvidia-smi
nvidia-smi -q
nvidia-smi topo -m
nvidia-smi pmon -c 1
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv
nvidia-smi --query-gpu=utilization.gpu,utilization.memory --format=csv
nvidia-smi --query-gpu=temperature.gpu,temperature.memory --format=csv
nvidia-smi --query-gpu=power.draw,power.limit --format=csv
nvidia-smi --query-gpu=clocks.current.graphics,clocks.current.memory --format=csv
nvidia-smi pmon -c 1 -s mu

# GPU PCIe Link Information
# From sysfs (per DRM device)
cat /sys/class/drm/card*/device/current_link_speed
cat /sys/class/drm/card*/device/current_link_width
cat /sys/class/drm/card*/device/max_link_speed
cat /sys/class/drm/card*/device/max_link_width

# From lspci (detailed link status)
lspci -s <GPU_SLOT> -vvv | grep -iE "LnkSta|LnkCap|Speed|Width"

# NVIDIA GPU PCIe Link Information (if nvidia-smi available)
nvidia-smi --query-gpu=pci.bus_id,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv
nvidia-smi nvlink --status
nvidia-smi nvlink --capabilities

# GPU kernel messages
dmesg | grep -iE "nvidia|gpu|vga|graphics|amd.*gpu|intel.*graphics" | tail -100
journalctl -k --since "<hours> hours ago" | grep -iE "nvidia|gpu|vga|graphics|amd.*gpu|intel.*graphics" | tail -100
grep -iE "nvidia|gpu|vga|graphics|amd.*gpu|intel.*graphics" /var/log/kern.log | tail -100

# GPU driver information
ls -la /sys/class/drm/ 2>&1
cat /sys/class/drm/card*/device/vendor
cat /sys/class/drm/card*/device/device
lsmod | grep -iE "nvidia|amd|radeon|intel.*gpu|i915"

# GPU temperature
find /sys -path "*/hwmon*/temp*_input" -exec sh -c 'echo "{}: $(cat {} 2>/dev/null | awk "{print \$1/1000}")°C"' \; 2>/dev/null | grep -iE "gpu|nvidia|amd|radeon"

# GPU power management
find /sys -path "*/drm/card*/device/power*" -type f 2>/dev/null | head -10
```

### SFP+/Optics Diagnostics

```bash
# SFP+ module information
ethtool -m <iface>
mlx_eeprom -d <dev> --read_all 2>/dev/null
mlx_eeprom -d <dev> --dump_dom 2>/dev/null

# Optics from sysfs
find /sys -name "*sfp*" -o -name "*qsfp*" 2>/dev/null | head -20
```

### System Information

```bash
# Hardware information
dmidecode -t memory
dmidecode -t processor
dmidecode -t system

# System IPC
ipcs -l
ipcs -u
ipcs -q
ipcs -s
```

### Optional NVIDIA Support Tools

```bash
# NVIDIA support bundle (if available)
nvidia-support-bundle --output <dir>

# mlxdoctor (if available)
mlxdoctor -v
```

### Interface Auto-Detection

```bash
# Auto-detect interfaces
for iface in /sys/class/net/*; do
    cat "$iface/device/vendor" 2>/dev/null
done
```

## Command Logging

All commands executed by the script are logged to `commands_run.txt` in the output directory with:
- **Timestamp**: ISO 8601 format (`YYYY-MM-DDTHH:MM:SS+00:00`)
- **Command**: Full command line as executed
- **Exit codes**: Non-zero exit codes are logged for failed commands
- **Format**: `[timestamp] command`

Example:
```
==================================================
Commands Executed by collect_bf3_link_flap_logs.sh
Started: 2025-11-26T20:23:33+00:00
==================================================

[INFO] Collecting system-level information
[2025-11-26T20:23:33+00:00] uname -a
[2025-11-26T20:23:33+00:00] lspci -D
[2025-11-26T20:23:33+00:00] Exit code: 2
```

This comprehensive command log helps with:
- **Audit trail**: See exactly what was executed
- **Troubleshooting**: Identify which commands failed
- **Reproducibility**: Re-run specific commands manually if needed
- **Debugging**: Understand script execution flow

