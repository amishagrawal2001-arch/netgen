#!/usr/bin/env bash

#
# collect_bf3_link_flap_logs.sh
#
# Collects diagnostics for NVIDIA/Mellanox BF3 / BlueField-based NICs
# when links are flapping.
#
# Recommended: run as root or via sudo:
#   sudo ./collect_bf3_link_flap_logs.sh -i "ens5f0np0 ens5f1np1" -s "0000:8a:00.0"
#

set -u

SCRIPT_NAME="$(basename "$0")"

usage() {
    cat <<EOF
Usage: sudo $SCRIPT_NAME [options]

Options:
  -i "IFACES"   Space-separated list of interfaces (e.g. "ens5f0np0 ens5f1np1").
  -s "SLOTS"    Space-separated list of PCI slots (e.g. "0000:8a:00.0").
  -b IP         BlueField/DPU management IP (optional, for remote logs).
  -t HOURS      Journalctl time window (default: 4).
  -o DIR        Output directory (default: /tmp/bf3_diag_<timestamp>).
  -h            Show this help.

If interfaces or PCI slots are not given, the script will try to auto-detect
Mellanox/NVIDIA devices.

Note: For enhanced GPU diagnostics, nvidia-smi should be installed. If not found,
      the script will display installation instructions during execution.
EOF
}

# Defaults
IFACES=""
PCI_SLOTS=""
BF_IP=""
JOURNAL_HOURS=4
OUTDIR=""

while getopts "i:s:b:t:o:h" opt; do
    case "$opt" in
        i) IFACES="$OPTARG" ;;
        s) PCI_SLOTS="$OPTARG" ;;
        b) BF_IP="$OPTARG" ;;
        t) JOURNAL_HOURS="$OPTARG" ;;
        o) OUTDIR="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
if [ -z "$OUTDIR" ]; then
    # Default to /tmp to ensure output is in a predictable location
    OUTDIR="/tmp/bf3_diag_${TIMESTAMP}"
fi

mkdir -p "$OUTDIR"/{system,logs,pci,nvidia,dpu,gpu,interfaces}

LOG_SUMMARY="$OUTDIR/commands_run.txt"
{
    echo "=================================================="
    echo "Commands Executed by collect_bf3_link_flap_logs.sh"
    echo "Started: $(date -Iseconds)"
    echo "=================================================="
    echo ""
} > "$LOG_SUMMARY"

log_info() {
    echo "[INFO] $*" | tee -a "$LOG_SUMMARY"
}

log_cmd() {
    # Log a command to commands_run.txt (for commands executed directly, not via run_cmd)
    local cmd="$*"
    local timestamp
    timestamp=$(date -Iseconds)
    echo "[$timestamp] $cmd" >> "$LOG_SUMMARY"
}

run_cmd() {
    local outfile="$1"
    shift
    local cmd="$*"
    local timestamp
    timestamp=$(date -Iseconds)

    # Log command to commands_run.txt
    echo "[$timestamp] $cmd" >> "$LOG_SUMMARY"

    {
        echo "=================================================="
        echo "Command: $cmd"
        echo "Time: $timestamp"
        echo "=================================================="
    } >> "$outfile"

    if ! command -v "${1%% *}" >/dev/null 2>&1 && ! [[ "$1" =~ ^/ ]]; then
        echo "Command not found: $cmd" >> "$outfile"
        echo "[$timestamp] Command not found: $cmd" >> "$LOG_SUMMARY"
        return 0
    fi

    # shellcheck disable=SC2086
    $cmd >> "$outfile" 2>&1
    local exit_code=$?
    
    # Log exit code to commands_run.txt if non-zero
    if [ $exit_code -ne 0 ]; then
        echo "[$timestamp] Exit code: $exit_code" >> "$LOG_SUMMARY"
    fi
}

# Auto-detect Mellanox/NVIDIA PCI devices if not provided
auto_detect_pci() {
    if [ -n "$PCI_SLOTS" ]; then
        echo "$PCI_SLOTS"
        return
    fi

    local slots
    log_cmd "lspci -D | grep -Ei \"Mellanox|NVIDIA.*(ConnectX|BlueField)\" | awk '{print \$1}'"
    slots=$(lspci -D | grep -Ei "Mellanox|NVIDIA.*(ConnectX|BlueField)" | awk '{print $1}')
    echo "$slots"
}

# Auto-detect Mellanox/NVIDIA interfaces if not provided
auto_detect_ifaces() {
    if [ -n "$IFACES" ]; then
        echo "$IFACES"
        return
    fi

    log_cmd "Auto-detecting Mellanox/NVIDIA interfaces from /sys/class/net"
    local ifs=""
    for iface in /sys/class/net/*; do
        [ -e "$iface" ] || continue
        local name
        name=$(basename "$iface")
        # Skip loopback
        if [ "$name" = "lo" ]; then
            continue
        fi
        if [ -e "$iface/device/vendor" ]; then
            local vendor
            log_cmd "cat $iface/device/vendor"
            vendor=$(cat "$iface/device/vendor" 2>/dev/null || echo "")
            # Mellanox/NVIDIA PCI vendor ID is 0x15b3
            if [ "$vendor" = "0x15b3" ]; then
                ifs="$ifs $name"
            fi
        fi
    done
    echo "$ifs"
}

########################################
# 1. System-level information
########################################
log_info "Collecting system-level information"
SYS_OUT="$OUTDIR/system/system_info.txt"

run_cmd "$SYS_OUT" uname -a
run_cmd "$SYS_OUT" lsb_release -a
run_cmd "$SYS_OUT" cat /etc/os-release
run_cmd "$SYS_OUT" date
run_cmd "$SYS_OUT" uptime
run_cmd "$SYS_OUT" dmesg | tail -200
run_cmd "$SYS_OUT" lscpu
run_cmd "$SYS_OUT" numactl --hardware
run_cmd "$SYS_OUT" lsmod
run_cmd "$SYS_OUT" lsblk
run_cmd "$SYS_OUT" free -h

########################################
# 2. PCI / NVIDIA / Mellanox details
########################################
log_info "Collecting PCI / NVIDIA / Mellanox details"

PCI_OUT="$OUTDIR/pci/pci_info.txt"
mkdir -p "$OUTDIR/pci"

run_cmd "$PCI_OUT" lspci -D
run_cmd "$PCI_OUT" lspci -nn
run_cmd "$PCI_OUT" lspci -vvv

PCI_LIST=$(auto_detect_pci)
if [ -z "$PCI_LIST" ]; then
    log_info "No Mellanox/NVIDIA PCI devices auto-detected"
else
    log_info "Detected/Using PCI slots: $PCI_LIST"
fi

for slot in $PCI_LIST; do
    SLOT_SAFE="${slot//:/_}"
    SLOT_SAFE="${SLOT_SAFE//./_}"
    SLOT_OUT="$OUTDIR/pci/pci_${SLOT_SAFE}.txt"

    run_cmd "$SLOT_OUT" lspci -s "$slot" -vvv
    run_cmd "$SLOT_OUT" sudo grep . /sys/bus/pci/devices/"$slot"/aer/* 2>/dev/null
    run_cmd "$SLOT_OUT" sudo cat /sys/bus/pci/devices/"$slot"/current_link_speed 2>/dev/null
    run_cmd "$SLOT_OUT" sudo cat /sys/bus/pci/devices/"$slot"/current_link_width 2>/dev/null
    run_cmd "$SLOT_OUT" sudo cat /sys/bus/pci/devices/"$slot"/max_link_speed 2>/dev/null
    run_cmd "$SLOT_OUT" sudo cat /sys/bus/pci/devices/"$slot"/max_link_width 2>/dev/null
done

########################################
# 3. NVIDIA/Mellanox Firmware and Config
########################################
log_info "Collecting NVIDIA/Mellanox firmware/config (mlxconfig, mlxfwmanager, mst, mlxlink)"

# Additional temperature and thermal diagnostics
log_info "Collecting temperature and thermal diagnostics"
TEMP_OUT="$OUTDIR/system/temperature.txt"
run_cmd "$TEMP_OUT" sensors 2>/dev/null || echo "sensors command not available"
run_cmd "$TEMP_OUT" cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -20
# Find temperature files more safely
find /sys -name "*temp*" -path "*/pci*" -type f 2>/dev/null | head -20 | while read temp_file; do
    [ -f "$temp_file" ] && echo "$temp_file: $(cat "$temp_file" 2>/dev/null || echo N/A)"
done >> "$TEMP_OUT" 2>&1 || echo "Temperature sensors: N/A" >> "$TEMP_OUT" 2>&1

NVIDIA_OUT="$OUTDIR/nvidia/nvidia_info.txt"
mkdir -p "$OUTDIR/nvidia"

run_cmd "$NVIDIA_OUT" mst start
run_cmd "$NVIDIA_OUT" mst status
run_cmd "$NVIDIA_OUT" mlxfwmanager --version
run_cmd "$NVIDIA_OUT" mlxfwmanager --query
run_cmd "$NVIDIA_OUT" mlxfwmanager --log

# Detect mlxlink feature support (fec-histogram, counters, pmaos, stats, rx_fec_histogram)
supports_fec_histogram=false
supports_counters=false
supports_pmaos=false
supports_stats=false
supports_rx_fec_histogram=false
supports_show_histogram=false

if command -v mlxlink >/dev/null 2>&1; then
    mlxlink_help=$(mlxlink --help 2>&1 || true)
    if echo "$mlxlink_help" | grep -q -- "--fec-histogram"; then
        supports_fec_histogram=true
    fi
    if echo "$mlxlink_help" | grep -q -- "--counters"; then
        supports_counters=true
    fi
    if echo "$mlxlink_help" | grep -q -- "--pmaos"; then
        supports_pmaos=true
    fi
    if echo "$mlxlink_help" | grep -q -- "--stats"; then
        supports_stats=true
    fi
    if echo "$mlxlink_help" | grep -q -- "--rx_fec_histogram"; then
        supports_rx_fec_histogram=true
    fi
    if echo "$mlxlink_help" | grep -q -- "--show_histogram"; then
        supports_show_histogram=true
    fi
fi

# For each mst device, collect mlxconfig and mlxlink info (conditionally)
if command -v mst >/dev/null 2>&1; then
    log_cmd "mst status 2>/dev/null | awk '/^\/dev\/mst\// {print \$1}'"
    MST_DEVICES=$(mst status 2>/dev/null | awk '/^\/dev\/mst\// {print $1}')
    for dev in $MST_DEVICES; do
        DEV_SAFE="${dev////_}"
        DEV_OUT="$OUTDIR/nvidia/${DEV_SAFE}.txt"

        run_cmd "$DEV_OUT" mlxconfig -d "$dev" query

        # Device-level FEC histogram (rx_fec_histogram/show_histogram combo)
        if $supports_rx_fec_histogram && $supports_show_histogram; then
            run_cmd "$DEV_OUT" mlxlink -d "$dev" --rx_fec_histogram --show_histogram
        else
            run_cmd "$DEV_OUT" echo "mlxlink: rx_fec_histogram/show_histogram not supported by this version"
        fi

        # Try ports 1-4; some devices may only have 1-2
        for p in 1 2 3 4; do
            # Basic link info (always supported)
            run_cmd "$DEV_OUT" mlxlink -d "$dev" -p "$p"

            # FEC histogram (newer mlxlink only)
            if $supports_fec_histogram; then
                run_cmd "$DEV_OUT" mlxlink -d "$dev" -p "$p" --fec-histogram
            else
                run_cmd "$DEV_OUT" echo "mlxlink: --fec-histogram not supported by this version"
            fi

            # Extended counters:
            if $supports_counters; then
                run_cmd "$DEV_OUT" mlxlink -d "$dev" -p "$p" --counters
            elif $supports_stats; then
                run_cmd "$DEV_OUT" mlxlink -d "$dev" -p "$p" --stats
            else
                run_cmd "$DEV_OUT" echo "mlxlink: extended counters (counters/stats) not supported by this version"
            fi

            # PMA/PCS lane info (if supported)
            if $supports_pmaos; then
                run_cmd "$DEV_OUT" mlxlink -d "$dev" -p "$p" --pmaos
            else
                run_cmd "$DEV_OUT" echo "mlxlink: --pmaos not supported by this version"
            fi
            
            # Additional link down troubleshooting: BER, signal quality
            run_cmd "$DEV_OUT" echo "=== Port $p: Attempting additional diagnostics ==="
            # Try to get BER (Bit Error Rate) if available (suppress stderr to avoid noise)
            mlxlink -d "$dev" -p "$p" --ber >> "$DEV_OUT" 2>/dev/null || echo "BER information not available" >> "$DEV_OUT"
            # Try to get eye diagram info if available
            mlxlink -d "$dev" -p "$p" --eye >> "$DEV_OUT" 2>/dev/null || echo "Eye diagram not available" >> "$DEV_OUT"
            # Try to get PRBS (Pseudo-Random Binary Sequence) test results
            mlxlink -d "$dev" -p "$p" --prbs >> "$DEV_OUT" 2>/dev/null || echo "PRBS test not available" >> "$DEV_OUT"
        done
    done
fi

########################################
# 4. Interface-level information
########################################
IFACE_LIST=$(auto_detect_ifaces)
if [ -z "$IFACE_LIST" ]; then
    log_info "No Mellanox/NVIDIA interfaces auto-detected. Use -i \"iface1 iface2\" if needed."
else
    log_info "Detected/Using interfaces: $IFACE_LIST"
fi

for iface in $IFACE_LIST; do
    IF_OUT_DIR="$OUTDIR/interfaces/$iface"
    mkdir -p "$IF_OUT_DIR"
    IF_OUT="$IF_OUT_DIR/${iface}_info.txt"

    log_info "Collecting interface info for $iface"

    run_cmd "$IF_OUT" ip link show dev "$iface"
    run_cmd "$IF_OUT" ip -d link show dev "$iface"
    run_cmd "$IF_OUT" ethtool "$iface"
    run_cmd "$IF_OUT" ethtool -i "$iface"
    run_cmd "$IF_OUT" ethtool --show-eee "$iface"
    run_cmd "$IF_OUT" ethtool --phy-statistics "$iface"
    run_cmd "$IF_OUT" ethtool -S "$iface"
    run_cmd "$IF_OUT" ethtool -m "$iface"
    run_cmd "$IF_OUT" ip addr show dev "$iface"
    run_cmd "$IF_OUT" ip -s link show dev "$iface"
    
    # Additional link down troubleshooting diagnostics
    log_info "Collecting additional diagnostics for $iface (link down troubleshooting)"
    
    # Temperature monitoring (if supported)
    run_cmd "$IF_OUT" ethtool --show-priv-flags "$iface" 2>/dev/null || echo "ethtool --show-priv-flags not supported"
    
    # Module/Cable information (SFP+ diagnostics)
    run_cmd "$IF_OUT" ethtool -m "$iface" 2>/dev/null || echo "Module information not available"
    
    # Link settings and capabilities
    run_cmd "$IF_OUT" ethtool "$iface" | grep -E "Supported|Advertised|Link|Speed|Duplex"
    
    # PCIe link status for the interface
    # Initialize PCI_SLOT to empty string to avoid unbound variable error with set -u
    PCI_SLOT=""
    if [ -e "/sys/class/net/$iface/device" ]; then
        PCI_SLOT=$(readlink -f "/sys/class/net/$iface/device" | grep -oP 'pci[^/]+' | sed 's/pci//' | tr '/' ':')
        if [ -n "$PCI_SLOT" ]; then
            run_cmd "$IF_OUT" echo "=== PCIe Link Status for $iface (PCI $PCI_SLOT) ==="
            run_cmd "$IF_OUT" cat "/sys/class/net/$iface/device/current_link_speed" 2>/dev/null || echo "PCIe speed: N/A"
            run_cmd "$IF_OUT" cat "/sys/class/net/$iface/device/current_link_width" 2>/dev/null || echo "PCIe width: N/A"
            run_cmd "$IF_OUT" cat "/sys/class/net/$iface/device/max_link_speed" 2>/dev/null || echo "PCIe max speed: N/A"
            run_cmd "$IF_OUT" cat "/sys/class/net/$iface/device/max_link_width" 2>/dev/null || echo "PCIe max width: N/A"
        fi
    fi
    
    # Interrupt statistics
    IF_OUT_INTERRUPTS="$IF_OUT_DIR/${iface}_interrupts.txt"
    run_cmd "$IF_OUT_INTERRUPTS" cat /proc/interrupts | grep -i "$iface" || echo "No interrupt info for $iface"
    
    # Interface statistics over time (capture multiple samples)
    IF_OUT_STATS="$IF_OUT_DIR/${iface}_stats_history.txt"
    {
        echo "=== Interface Statistics - Sample 1 ==="
        ip -s link show dev "$iface"
        sleep 2
        echo ""
        echo "=== Interface Statistics - Sample 2 (after 2s) ==="
        ip -s link show dev "$iface"
        sleep 2
        echo ""
        echo "=== Interface Statistics - Sample 3 (after 4s) ==="
        ip -s link show dev "$iface"
    } >> "$IF_OUT_STATS" 2>&1
    
    # Power management settings
    IF_OUT_POWER="$IF_OUT_DIR/${iface}_power_management.txt"
    {
        echo "=== Power Management Settings ==="
        if [ -e "/sys/class/net/$iface/power" ]; then
            cat "/sys/class/net/$iface/power/control" 2>/dev/null || echo "Power control: N/A"
            cat "/sys/class/net/$iface/power/runtime_status" 2>/dev/null || echo "Runtime status: N/A"
        fi
        if [ -e "/sys/class/net/$iface/device/power" ]; then
            echo "Device power state:"
            cat "/sys/class/net/$iface/device/power/state" 2>/dev/null || echo "N/A"
            cat "/sys/class/net/$iface/device/power/runtime_status" 2>/dev/null || echo "N/A"
        fi
        echo ""
        echo "=== PCIe ASPM (Active State Power Management) ==="
        if [ -n "$PCI_SLOT" ] && [ -e "/sys/bus/pci/devices/$PCI_SLOT" ]; then
            cat "/sys/bus/pci/devices/$PCI_SLOT/power_state" 2>/dev/null || echo "N/A"
            for aspm_file in /sys/bus/pci/devices/$PCI_SLOT/pcie_aspm*; do
                [ -f "$aspm_file" ] && echo "$(basename $aspm_file): $(cat $aspm_file)" || true
            done
        fi
    } >> "$IF_OUT_POWER" 2>&1
    
    # Link state history from kernel ring buffer
    IF_OUT_LINKHIST="$IF_OUT_DIR/${iface}_link_history.txt"
    {
        echo "=== Link State History (from dmesg) ==="
        # Match patterns like "enp13s0f0np0: Link up/down" or "mlx5_core ... enp13s0f0np0: Link"
        # Also match driver names that might appear before interface name
        dmesg | grep -i "$iface" | grep -iE "[Ll]ink" | tail -50
        echo ""
        echo "=== Link State History (from journalctl) ==="
        journalctl -k --since "${JOURNAL_HOURS} hours ago" | grep -i "$iface" | grep -iE "[Ll]ink" | tail -50
        echo ""
        echo "=== Link State Timeline (with timestamps) ==="
        if command -v dmesg >/dev/null 2>&1 && dmesg -T >/dev/null 2>&1; then
            dmesg -T 2>/dev/null | grep -i "$iface" | grep -iE "[Ll]ink" | tail -30
        else
            dmesg | grep -i "$iface" | grep -iE "[Ll]ink" | tail -30
        fi
        echo ""
        echo "=== Link State History (from /var/log/kern.log if available) ==="
        if [ -f /var/log/kern.log ]; then
            grep -i "$iface" /var/log/kern.log | grep -iE "[Ll]ink" | tail -50
        fi
    } >> "$IF_OUT_LINKHIST" 2>&1
    
    # Driver-specific diagnostics
    IF_OUT_DRIVER="$IF_OUT_DIR/${iface}_driver_diagnostics.txt"
    {
        echo "=== Driver Information ==="
        ethtool -i "$iface" | grep -E "driver|version|firmware"
        echo ""
        echo "=== Driver Module Information ==="
        driver_name=$(ethtool -i "$iface" | grep "^driver:" | awk '{print $2}')
        if [ -n "$driver_name" ]; then
            modinfo "$driver_name" 2>/dev/null | head -20
            echo ""
            echo "=== Driver Module Parameters ==="
            ls -la /sys/module/"$driver_name"/parameters/ 2>/dev/null | head -20
            for param in /sys/module/"$driver_name"/parameters/*; do
                [ -f "$param" ] && echo "$(basename $param): $(cat $param 2>/dev/null || echo N/A)"
            done
        fi
        echo ""
        echo "=== Interface Driver Statistics ==="
        if [ -d "/sys/class/net/$iface/statistics" ]; then
            for stat in /sys/class/net/$iface/statistics/*; do
                [ -f "$stat" ] && echo "$(basename $stat): $(cat $stat 2>/dev/null || echo 0)"
            done | sort
        fi
    } >> "$IF_OUT_DRIVER" 2>&1
    
    # LLDP/CDP neighbor information (if available)
    IF_OUT_NEIGHBOR="$IF_OUT_DIR/${iface}_neighbor_info.txt"
    {
        echo "=== Link Partner Information ==="
        # Try lldpctl if available
        if command -v lldpctl >/dev/null 2>&1; then
            lldpctl show neighbors ports "$iface" 2>/dev/null || echo "LLDP neighbor info not available for $iface"
        fi
        # Try tcpdump to capture LLDP/CDP packets (non-intrusive, just check if available)
        echo ""
        echo "=== Link Partner Capabilities (from ethtool) ==="
        ethtool "$iface" | grep -A 20 "Advertised\|Supported\|Link partner"
    } >> "$IF_OUT_NEIGHBOR" 2>&1
done

########################################
# 5. Logs: dmesg, journalctl, kern.log
########################################
log_info "Collecting kernel and journal logs"

LOG_OUT="$OUTDIR/logs/logs.txt"

run_cmd "$LOG_OUT" dmesg
run_cmd "$LOG_OUT" dmesg | grep -i -e mlx -e nvidia -e bluefield -e bf3
run_cmd "$LOG_OUT" journalctl -k --since "${JOURNAL_HOURS} hours ago"
run_cmd "$LOG_OUT" journalctl -k --since "${JOURNAL_HOURS} hours ago" | grep -i -e mlx -e nvidia -e pci -e link
run_cmd "$LOG_OUT" journalctl -u NetworkManager --since "${JOURNAL_HOURS} hours ago"

if [ -f /var/log/kern.log ]; then
    run_cmd "$LOG_OUT" tail -500 /var/log/kern.log
    run_cmd "$LOG_OUT" grep -iE "link down|link up|mlx|nvidia|pci" /var/log/kern.log | tail -200
fi

# Additional link down troubleshooting: System load and resource usage
log_info "Collecting system load and resource usage (for link down correlation)"
LOAD_OUT="$OUTDIR/system/system_load.txt"
{
    echo "=== System Load Average ==="
    uptime
    echo ""
    echo "=== CPU Usage =="
    top -bn1 | head -20
    echo ""
    echo "=== Memory Usage ==="
    free -h
    echo ""
    echo "=== I/O Wait Statistics ==="
    iostat -x 1 3 2>/dev/null || echo "iostat not available"
    echo ""
    echo "=== Network Interface Statistics ==="
    cat /proc/net/dev
    echo ""
    echo "=== SoftIRQ Statistics (network-related) ==="
    cat /proc/softirqs | grep -E "NET|NET_RX|NET_TX"
} >> "$LOAD_OUT" 2>&1

# PCIe errors and link retrains
log_info "Collecting PCIe error logs"
PCIE_ERR_OUT="$OUTDIR/pci/pcie_errors.txt"
{
    echo "=== PCIe Error Logs ==="
    dmesg | grep -iE "pci.*error|pcie.*error|aer.*error" | tail -50
    echo ""
    echo "=== PCIe Link Retrains ==="
    dmesg | grep -iE "pci.*retrain|link.*retrain" | tail -50
    echo ""
    echo "=== PCIe Correctable/Uncorrectable Errors ==="
    find /sys -name "*aer*" -type f 2>/dev/null | head -30 | while read aer_file; do
        [ -f "$aer_file" ] && echo "$aer_file: $(cat "$aer_file" 2>/dev/null || echo N/A)"
    done
} >> "$PCIE_ERR_OUT" 2>&1

########################################
# 6. I2C / Optics diagnostics
########################################
log_info "Collecting I2C / optics diagnostics where possible"

I2C_OUT="$OUTDIR/system/i2c_optics.txt"
run_cmd "$I2C_OUT" i2cdetect -l

# Additional optics/SFP+ diagnostics for link down troubleshooting
log_info "Collecting SFP+/Optics diagnostics for link down troubleshooting"
OPTICS_OUT="$OUTDIR/system/optics_detailed.txt"
mkdir -p "$OUTDIR/system"

# Check for SFP+ module information via sysfs
run_cmd "$OPTICS_OUT" find /sys -path "*sfp*" -o -path "*qsfp*" -o -path "*optics*" 2>/dev/null | head -30
run_cmd "$OPTICS_OUT" find /sys -name "*module*" -path "*/net/*" 2>/dev/null | head -20

# Try to read SFP+ DOM (Digital Optical Monitoring) data if available
for iface in $IFACE_LIST; do
    if [ -d "/sys/class/net/$iface/device" ]; then
        SFP_PATH=$(find "/sys/class/net/$iface/device" -name "*sfp*" -o -name "*qsfp*" 2>/dev/null | head -1)
        if [ -n "$SFP_PATH" ]; then
            run_cmd "$OPTICS_OUT" echo "=== SFP+ Information for $iface ==="
            run_cmd "$OPTICS_OUT" find "$SFP_PATH" -type f -exec sh -c 'echo "{}: $(cat {} 2>/dev/null || echo N/A)"' \; 2>/dev/null | head -30
        fi
    fi
done

########################################
# 7. DPU / BlueField diagnostics (local and remote)
########################################
log_info "Collecting DPU/BlueField diagnostics"

DPU_DIR="$OUTDIR/dpu"
mkdir -p "$DPU_DIR"

# Detect if we're on a DPU or have DPU devices
DPU_DETECTED=false
log_cmd "lspci -D | grep -qi \"BlueField\|DPU\""
if lspci -D | grep -qi "BlueField\|DPU"; then
    DPU_DETECTED=true
    log_info "DPU device(s) detected on local system"
fi

# Local DPU diagnostics
DPU_LOCAL_OUT="$DPU_DIR/dpu_local.txt"
{
    echo "=== DPU Detection ==="
    echo "DPU Detected: $DPU_DETECTED"
    echo ""
    
    echo "=== BlueField/DPU PCI Devices ==="
    log_cmd "lspci -D | grep -iE \"BlueField|DPU|Mellanox.*ConnectX\""
    lspci -D | grep -iE "BlueField|DPU|Mellanox.*ConnectX" || echo "No DPU devices found in lspci"
    echo ""
    
    echo "=== DPU Kernel Messages ==="
    log_cmd "dmesg | grep -iE \"bluefield|dpu|bf[0-9]|bfb\" | tail -100"
    dmesg | grep -iE "bluefield|dpu|bf[0-9]|bfb" | tail -100
    echo ""
    
    echo "=== DPU Journal Logs ==="
    log_cmd "journalctl -k --since \"${JOURNAL_HOURS} hours ago\" | grep -iE \"bluefield|dpu|bf[0-9]|bfb\" | tail -100"
    journalctl -k --since "${JOURNAL_HOURS} hours ago" | grep -iE "bluefield|dpu|bf[0-9]|bfb" | tail -100
    echo ""
    
    echo "=== DPU System Logs (from /var/log/kern.log) ==="
    if [ -f /var/log/kern.log ]; then
        log_cmd "grep -iE \"bluefield|dpu|bf[0-9]|bfb\" /var/log/kern.log | tail -100"
        grep -iE "bluefield|dpu|bf[0-9]|bfb" /var/log/kern.log | tail -100
    fi
    echo ""
    
    echo "=== DPU Firmware Information ==="
    # Check for DPU firmware files
    find /sys -name "*bluefield*" -o -name "*dpu*" 2>/dev/null | head -20 | while read f; do
        [ -f "$f" ] && echo "$f: $(cat "$f" 2>/dev/null || echo N/A)"
    done
    echo ""
    
    echo "=== DPU Device Tree Information ==="
    if [ -d /sys/firmware/devicetree ]; then
        find /sys/firmware/devicetree -name "*bluefield*" -o -name "*dpu*" 2>/dev/null | head -20
    fi
    echo ""
    
    echo "=== DPU-specific Tools Availability ==="
    for tool in bfb bfshell bf-telemetry dpfctl bfconfig; do
        if command -v "$tool" >/dev/null 2>&1; then
            echo "$tool: Available"
            echo "  Location: $(command -v "$tool")"
            echo "  Version: $($tool --version 2>/dev/null || $tool -v 2>/dev/null || echo 'version info not available')"
        else
            echo "$tool: Not available"
        fi
        echo ""
    done
} >> "$DPU_LOCAL_OUT" 2>&1

# Collect DPU tool outputs if available
if command -v bfb >/dev/null 2>&1; then
    log_info "Collecting bfb (BlueField boot) information"
    DPU_BFB_OUT="$DPU_DIR/bfb_info.txt"
    {
        echo "=== BFB (BlueField Boot) Information ==="
        bfb --version 2>&1 || echo "bfb version not available"
        echo ""
        echo "=== BFB Status ==="
        bfb --status 2>&1 || echo "bfb status not available"
    } >> "$DPU_BFB_OUT" 2>&1
fi

if command -v bfshell >/dev/null 2>&1; then
    log_info "Collecting bfshell (BlueField shell) information"
    DPU_BFSHELL_OUT="$DPU_DIR/bfshell_info.txt"
    {
        echo "=== BFSHELL (BlueField Shell) Information ==="
        echo "=== Show Port Status ==="
        bfshell -c "show port" 2>&1 || echo "bfshell show port not available"
        echo ""
        echo "=== Show System Status ==="
        bfshell -c "show system" 2>&1 || echo "bfshell show system not available"
        echo ""
        echo "=== Show Firmware Version ==="
        bfshell -c "show version" 2>&1 || echo "bfshell show version not available"
    } >> "$DPU_BFSHELL_OUT" 2>&1
fi

if command -v bf-telemetry >/dev/null 2>&1; then
    log_info "Collecting bf-telemetry information"
    DPU_TELEMETRY_OUT="$DPU_DIR/bf_telemetry.txt"
    {
        echo "=== BF-TELEMETRY Information ==="
        bf-telemetry -a 2>&1 || echo "bf-telemetry not available"
        echo ""
        echo "=== BF-TELEMETRY Status ==="
        bf-telemetry --status 2>&1 || echo "bf-telemetry status not available"
    } >> "$DPU_TELEMETRY_OUT" 2>&1
fi

if command -v dpfctl >/dev/null 2>&1; then
    log_info "Collecting dpfctl (DOCA Platform Framework) information"
    DPU_DPFCTL_OUT="$DPU_DIR/dpfctl_info.txt"
    {
        echo "=== DPFCTL (DOCA Platform Framework) Information ==="
        dpfctl version 2>&1 || echo "dpfctl version not available"
        echo ""
        echo "=== DPFCTL Status ==="
        dpfctl status 2>&1 || echo "dpfctl status not available"
        echo ""
        echo "=== DPFCTL Resources ==="
        dpfctl show resources 2>&1 || echo "dpfctl show resources not available"
    } >> "$DPU_DPFCTL_OUT" 2>&1
fi

if command -v bfconfig >/dev/null 2>&1; then
    log_info "Collecting bfconfig information"
    DPU_BFCONFIG_OUT="$DPU_DIR/bfconfig_info.txt"
    {
        echo "=== BFCONFIG Information ==="
        bfconfig --version 2>&1 || echo "bfconfig version not available"
        echo ""
        echo "=== BFCONFIG Status ==="
        bfconfig --status 2>&1 || echo "bfconfig status not available"
    } >> "$DPU_BFCONFIG_OUT" 2>&1
fi

# Check for DPU log directories
if [ -d /run/log/dpulogs ]; then
    log_info "Collecting DPU logs from /run/log/dpulogs"
    DPU_LOGS_OUT="$DPU_DIR/dpu_system_logs.txt"
    {
        echo "=== DPU System Logs from /run/log/dpulogs ==="
        ls -lah /run/log/dpulogs/ 2>&1
        echo ""
        echo "=== Recent DPU Log Entries ==="
        find /run/log/dpulogs -type f -exec tail -50 {} \; 2>&1 | head -200
    } >> "$DPU_LOGS_OUT" 2>&1
fi

# Remote DPU diagnostics (if BF_IP provided)
if [ -n "$BF_IP" ]; then
    log_info "Collecting BlueField/DPU logs from $BF_IP (ssh)"

    DPU_REMOTE_OUT="$DPU_DIR/dpu_${BF_IP}.txt"
    mkdir -p "$DPU_DIR"

    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "uname -a"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "cat /etc/os-release"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "dmesg | grep -i -e mlx -e link -e pci | tail -200"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "journalctl -k --since '${JOURNAL_HOURS} hours ago'"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "ip link show"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "lspci -D"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "bfshell -c 'show port' 2>/dev/null || echo 'bfshell not available'"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "bf-telemetry -a 2>/dev/null || echo 'bf-telemetry not available'"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "dpfctl status 2>/dev/null || echo 'dpfctl not available'"
    run_cmd "$DPU_REMOTE_OUT" ssh "$BF_IP" "ls -lah /run/log/dpulogs/ 2>/dev/null || echo 'DPU logs directory not found'"
fi

########################################
# 8. GPU diagnostics
########################################
log_info "Collecting GPU diagnostics"

GPU_DIR="$OUTDIR/gpu"
mkdir -p "$GPU_DIR"

# Detect GPUs
GPU_DETECTED=false
log_cmd "lspci -D | grep -qiE \"VGA|3D|Display.*NVIDIA|AMD.*GPU|Intel.*Graphics\""
if lspci -D | grep -qiE "VGA|3D|Display.*NVIDIA|AMD.*GPU|Intel.*Graphics"; then
    GPU_DETECTED=true
    log_info "GPU device(s) detected on local system"
fi

# GPU diagnostics
GPU_OUT="$GPU_DIR/gpu_info.txt"
{
    echo "=== GPU Detection ==="
    echo "GPU Detected: $GPU_DETECTED"
    echo ""
    
    echo "=== GPU PCI Devices ==="
    lspci -D | grep -iE "VGA|3D|Display|Graphics|GPU" || echo "No GPU devices found in lspci"
    echo ""
    
    echo "=== NVIDIA GPUs (nvidia-smi) ==="
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi 2>&1 || echo "nvidia-smi failed"
        echo ""
        echo "=== NVIDIA GPU Details ==="
        nvidia-smi -q 2>&1 || echo "nvidia-smi -q failed"
        echo ""
        echo "=== NVIDIA GPU Topology ==="
        nvidia-smi topo -m 2>&1 || echo "nvidia-smi topo failed"
        echo ""
        echo "=== NVIDIA GPU Processes ==="
        nvidia-smi pmon -c 1 2>&1 || echo "nvidia-smi pmon failed"
    else
        echo "nvidia-smi not available"
        echo ""
        echo "⚠️  WARNING: nvidia-smi is not installed. GPU diagnostics will be limited."
        echo ""
        echo "To install nvidia-smi and NVIDIA drivers, use one of the following:"
        echo ""
        # Detect OS and provide appropriate installation instructions
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
                echo "For Ubuntu/Debian:"
                echo "  sudo apt update"
                echo "  sudo apt install -y nvidia-driver-<version>"
                echo "  # Or install from NVIDIA repository:"
                echo "  # wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb"
                echo "  # sudo dpkg -i cuda-keyring_1.1-1_all.deb"
                echo "  # sudo apt update"
                echo "  # sudo apt install -y cuda-drivers"
                echo ""
                echo "  # After installation, reboot the system:"
                echo "  # sudo reboot"
            elif [ "$ID" = "rhel" ] || [ "$ID" = "centos" ] || [ "$ID" = "rocky" ] || [ "$ID" = "almalinux" ]; then
                echo "For RHEL/CentOS/Rocky/AlmaLinux:"
                echo "  sudo dnf install -y epel-release"
                echo "  sudo dnf install -y nvidia-driver"
                echo "  # Or install from NVIDIA repository:"
                echo "  # sudo dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/rhel8/x86_64/cuda-rhel8.repo"
                echo "  # sudo dnf install -y cuda-drivers"
                echo ""
                echo "  # After installation, reboot the system:"
                echo "  # sudo reboot"
            else
                echo "For other Linux distributions:"
                echo "  Visit: https://www.nvidia.com/Download/index.aspx"
                echo "  Download and install the appropriate NVIDIA driver for your system"
            fi
        else
            echo "  Visit: https://www.nvidia.com/Download/index.aspx"
            echo "  Download and install the appropriate NVIDIA driver for your system"
        fi
        echo ""
        echo "Note: After installing NVIDIA drivers, nvidia-smi will be available"
        echo "      and the script will collect additional GPU diagnostics."
        echo ""
        # Also print to stderr so it's visible in console output
        echo "⚠️  WARNING: nvidia-smi not found. See installation instructions above." >&2
    fi
    echo ""
    
    echo "=== GPU Kernel Messages ==="
    dmesg | grep -iE "nvidia|gpu|vga|graphics|amd.*gpu|intel.*graphics" | tail -100
    echo ""
    
    echo "=== GPU Journal Logs ==="
    journalctl -k --since "${JOURNAL_HOURS} hours ago" | grep -iE "nvidia|gpu|vga|graphics|amd.*gpu|intel.*graphics" | tail -100
    echo ""
    
    echo "=== GPU System Logs (from /var/log/kern.log) ==="
    if [ -f /var/log/kern.log ]; then
        grep -iE "nvidia|gpu|vga|graphics|amd.*gpu|intel.*graphics" /var/log/kern.log | tail -100
    fi
    echo ""
    
    echo "=== GPU Driver Information ==="
    if [ -d /sys/class/drm ]; then
        echo "DRM devices:"
        ls -la /sys/class/drm/ 2>&1 | head -20
        echo ""
        for drm_dev in /sys/class/drm/card*; do
            if [ -d "$drm_dev" ]; then
                card_name=$(basename "$drm_dev")
                echo "=== $card_name ==="
                [ -f "$drm_dev/device/vendor" ] && echo "Vendor: $(cat "$drm_dev/device/vendor" 2>/dev/null || echo N/A)"
                [ -f "$drm_dev/device/device" ] && echo "Device: $(cat "$drm_dev/device/device" 2>/dev/null || echo N/A)"
                [ -f "$drm_dev/device/subsystem_vendor" ] && echo "Subsystem Vendor: $(cat "$drm_dev/device/subsystem_vendor" 2>/dev/null || echo N/A)"
                [ -f "$drm_dev/device/subsystem_device" ] && echo "Subsystem Device: $(cat "$drm_dev/device/subsystem_device" 2>/dev/null || echo N/A)"
                
                # GPU PCIe Link Information
                if [ -d "$drm_dev/device" ]; then
                    echo ""
                    echo "=== PCIe Link Status for $card_name ==="
                    [ -f "$drm_dev/device/current_link_speed" ] && echo "Current Link Speed: $(cat "$drm_dev/device/current_link_speed" 2>/dev/null || echo N/A)"
                    [ -f "$drm_dev/device/current_link_width" ] && echo "Current Link Width: $(cat "$drm_dev/device/current_link_width" 2>/dev/null || echo N/A)"
                    [ -f "$drm_dev/device/max_link_speed" ] && echo "Max Link Speed: $(cat "$drm_dev/device/max_link_speed" 2>/dev/null || echo N/A)"
                    [ -f "$drm_dev/device/max_link_width" ] && echo "Max Link Width: $(cat "$drm_dev/device/max_link_width" 2>/dev/null || echo N/A)"
                    
                    # Try to get PCI slot from device path
                    PCI_SLOT=$(readlink -f "$drm_dev/device" 2>/dev/null | grep -oP 'pci[^/]+' | sed 's/pci//' | tr '/' ':' | head -1)
                    if [ -n "$PCI_SLOT" ]; then
                        echo "PCI Slot: $PCI_SLOT"
                        # Additional PCIe link information from PCI device
                        if [ -d "/sys/bus/pci/devices/$PCI_SLOT" ]; then
                            [ -f "/sys/bus/pci/devices/$PCI_SLOT/current_link_speed" ] && echo "PCI Device Link Speed: $(cat "/sys/bus/pci/devices/$PCI_SLOT/current_link_speed" 2>/dev/null || echo N/A)"
                            [ -f "/sys/bus/pci/devices/$PCI_SLOT/current_link_width" ] && echo "PCI Device Link Width: $(cat "/sys/bus/pci/devices/$PCI_SLOT/current_link_width" 2>/dev/null || echo N/A)"
                            [ -f "/sys/bus/pci/devices/$PCI_SLOT/max_link_speed" ] && echo "PCI Device Max Speed: $(cat "/sys/bus/pci/devices/$PCI_SLOT/max_link_speed" 2>/dev/null || echo N/A)"
                            [ -f "/sys/bus/pci/devices/$PCI_SLOT/max_link_width" ] && echo "PCI Device Max Width: $(cat "/sys/bus/pci/devices/$PCI_SLOT/max_link_width" 2>/dev/null || echo N/A)"
                        fi
                    fi
                fi
                echo ""
            fi
        done
    fi
    echo ""
    
    echo "=== GPU Module Information ==="
    lsmod | grep -iE "nvidia|amd|radeon|intel.*gpu|i915" || echo "No GPU modules loaded"
    echo ""
    
    echo "=== GPU Temperature (if available) ==="
    find /sys -path "*/hwmon*/temp*_input" -exec sh -c 'echo "{}: $(cat {} 2>/dev/null | awk "{print \$1/1000}")°C"' \; 2>/dev/null | grep -iE "gpu|nvidia|amd|radeon" | head -20
    echo ""
    
    echo "=== GPU Power Management ==="
    find /sys -path "*/drm/card*/device/power*" -type f 2>/dev/null | head -10 | while read pm_file; do
        [ -f "$pm_file" ] && echo "$pm_file: $(cat "$pm_file" 2>/dev/null || echo N/A)"
    done
    echo ""
    
    echo "=== GPU-specific Tools Availability ==="
    for tool in nvidia-smi nvidia-ml-py nvidia-settings glxinfo; do
        if command -v "$tool" >/dev/null 2>&1; then
            echo "$tool: Available"
            echo "  Location: $(command -v "$tool")"
            if [ "$tool" = "nvidia-smi" ]; then
                echo "  Version: $(nvidia-smi --version 2>&1 | head -1 || echo 'version info not available')"
            fi
        else
            echo "$tool: Not available"
            if [ "$tool" = "nvidia-smi" ]; then
                echo "  ⚠️  Install nvidia-smi for enhanced GPU diagnostics"
                echo "  See installation instructions above in this file"
            fi
        fi
        echo ""
    done
    
    echo "=== GPU PCIe Link Information (from lspci) ==="
    # Get GPU PCI slots and check their link information
    for gpu_slot in $(lspci -D | grep -iE "VGA|3D|Display|Graphics|GPU" | awk '{print $1}'); do
        if [ -n "$gpu_slot" ]; then
            echo "=== GPU PCI Slot: $gpu_slot ==="
            lspci -s "$gpu_slot" -vvv 2>/dev/null | grep -iE "LnkSta|LnkCap|Speed|Width" | head -10 || echo "Link information not available"
            echo ""
        fi
    done
} >> "$GPU_OUT" 2>&1

# Collect detailed NVIDIA GPU information if available
if command -v nvidia-smi >/dev/null 2>&1; then
    log_info "Collecting detailed NVIDIA GPU information"
    GPU_NVIDIA_OUT="$GPU_DIR/nvidia_gpu_detailed.txt"
    {
        echo "=== NVIDIA GPU Query (nvidia-smi -q) ==="
        nvidia-smi -q 2>&1
        echo ""
        echo "=== NVIDIA GPU Topology ==="
        nvidia-smi topo -m 2>&1
        echo ""
        echo "=== NVIDIA GPU NVLink Information ==="
        nvidia-smi nvlink --status 2>&1 || echo "NVLink status not available"
        nvidia-smi nvlink --capabilities 2>&1 || echo "NVLink capabilities not available"
        echo ""
        echo "=== NVIDIA GPU PCIe Link Information ==="
        nvidia-smi --query-gpu=pci.bus_id,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv 2>&1 || echo "PCIe link information not available"
        echo ""
        echo "=== NVIDIA GPU Persistence Mode ==="
        nvidia-smi -pm 2>&1 || echo "Persistence mode query failed"
        echo ""
        echo "=== NVIDIA GPU Compute Capability ==="
        nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>&1
        echo ""
        echo "=== NVIDIA GPU Memory Info ==="
        nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv 2>&1
        echo ""
        echo "=== NVIDIA GPU Utilization ==="
        nvidia-smi --query-gpu=utilization.gpu,utilization.memory --format=csv 2>&1
        echo ""
        echo "=== NVIDIA GPU Temperature ==="
        nvidia-smi --query-gpu=temperature.gpu,temperature.memory --format=csv 2>&1
        echo ""
        echo "=== NVIDIA GPU Power ==="
        nvidia-smi --query-gpu=power.draw,power.limit --format=csv 2>&1
        echo ""
        echo "=== NVIDIA GPU Clock Speeds ==="
        nvidia-smi --query-gpu=clocks.current.graphics,clocks.current.memory --format=csv 2>&1
        echo ""
        echo "=== NVIDIA GPU Processes ==="
        nvidia-smi pmon -c 1 -s mu 2>&1
    } >> "$GPU_NVIDIA_OUT" 2>&1
fi

########################################
# 9. Optional NVIDIA support tools
########################################
log_info "Collecting optional NVIDIA support bundle if available"

if command -v nvidia-support-bundle >/dev/null 2>&1; then
    SUPPORT_OUT_DIR="$OUTDIR/nvidia/support_bundle"
    mkdir -p "$SUPPORT_OUT_DIR"
    run_cmd "$SUPPORT_OUT_DIR/support_bundle.log" nvidia-support-bundle --output "$SUPPORT_OUT_DIR"
fi

if command -v mlxdoctor >/dev/null 2>&1; then
    MLXDOC_OUT="$OUTDIR/nvidia/mlxdoctor.txt"
    run_cmd "$MLXDOC_OUT" mlxdoctor -v
fi

########################################
# 9. Analyze and report critical issues
########################################
log_info "Analyzing collected logs for critical issues..."

analyze_critical_issues() {
    local issues_found=0
    
    echo ""
    echo "=================================================="
    echo "CRITICAL ISSUES ANALYSIS"
    echo "=================================================="
    echo ""
    
    # Check for link down events in interface statistics
    if [ -d "$OUTDIR/interfaces" ]; then
        echo "=== LINK DOWN EVENTS ==="
        for iface_file in "$OUTDIR/interfaces"/*/*.txt; do
            if [ -f "$iface_file" ]; then
                link_down_count=$(grep -i "link_down_events" "$iface_file" | grep -oE "[0-9]+" | head -1)
                if [ -n "$link_down_count" ] && [ "$link_down_count" -gt 0 ]; then
                    iface_name=$(basename "$(dirname "$iface_file")")
                    echo "⚠️  CRITICAL: Interface $iface_name has $link_down_count physical link down event(s)"
                    issues_found=$((issues_found + 1))
                fi
            fi
        done
        echo ""
    fi
    
    # Check for transmission/reception errors
    if [ -d "$OUTDIR/interfaces" ]; then
        echo "=== TRANSMISSION/RECEPTION ERRORS ==="
        errors_found=false
        for iface_file in "$OUTDIR/interfaces"/*/*.txt; do
            if [ -f "$iface_file" ]; then
                iface_name=$(basename "$(dirname "$iface_file")")
                
                # Check for various error types
                while IFS= read -r error_line; do
                    error_count=$(echo "$error_line" | grep -oE "[0-9]+" | head -1)
                    error_type=$(echo "$error_line" | grep -oE "[a-z_]+_errors?" | head -1)
                    if [ -n "$error_count" ] && [ "$error_count" -gt 0 ]; then
                        if [ "$errors_found" = false ]; then
                            echo "⚠️  CRITICAL: Interface $iface_name has errors:"
                            errors_found=true
                            issues_found=$((issues_found + 1))
                        fi
                        echo "   - $error_type: $error_count"
                    fi
                done < <(grep -iE "(rx|tx).*error|_errors_phy|crc_error|dropped.*[1-9]" "$iface_file" 2>/dev/null | grep -v ": 0")
            fi
        done
        if [ "$errors_found" = false ]; then
            echo "✓ No transmission/reception errors detected"
        fi
        echo ""
    fi
    
    # Check for link flapping in kernel logs
    if [ -f "$OUTDIR/logs/logs.txt" ]; then
        echo "=== LINK FLAPPING DETECTED ==="
        link_flap_count=$(grep -iE "link.*down|link.*up" "$OUTDIR/logs/logs.txt" | wc -l)
        if [ "$link_flap_count" -gt 10 ]; then
            echo "⚠️  CRITICAL: Detected $link_flap_count link state changes (possible link flapping)"
            echo "   Recent link events:"
            grep -iE "link.*down|link.*up" "$OUTDIR/logs/logs.txt" | tail -5 | sed 's/^/   /'
            issues_found=$((issues_found + 1))
        elif [ "$link_flap_count" -gt 0 ]; then
            echo "⚠️  WARNING: Detected $link_flap_count link state change(s)"
            grep -iE "link.*down|link.*up" "$OUTDIR/logs/logs.txt" | tail -3 | sed 's/^/   /'
        else
            echo "✓ No link flapping detected in logs"
        fi
        echo ""
    fi
    
    # Check for PCIe link issues
    if [ -f "$OUTDIR/pci/pcie_errors.txt" ]; then
        echo "=== PCIe LINK ISSUES ==="
        # Only count actual errors, not section headers
        pcie_errors=$(grep -iE "pci.*error|pcie.*error|aer.*error|link.*retrain" "$OUTDIR/pci/pcie_errors.txt" | \
                     grep -vE "===|PCIe Error Logs|PCIe Link Retrains|N/A" | \
                     grep -iE "[0-9a-f]{4,}|retrain|error" | wc -l)
        if [ "$pcie_errors" -gt 0 ]; then
            echo "⚠️  WARNING: Detected PCIe errors or link retrains"
            grep -iE "pci.*error|pcie.*error|aer.*error|link.*retrain" "$OUTDIR/pci/pcie_errors.txt" | \
                grep -vE "===|PCIe Error Logs|PCIe Link Retrains|N/A" | \
                head -5 | sed 's/^/   /'
            issues_found=$((issues_found + 1))
        else
            echo "✓ No PCIe link issues detected"
        fi
        echo ""
    fi
    
    # Check for temperature issues
    if [ -f "$OUTDIR/system/temperature.txt" ]; then
        echo "=== TEMPERATURE ISSUES ==="
        # Look for actual temperature readings (temp*_input, not thresholds like temp*_crit, temp*_max)
        # Temperature values are typically in millidegrees (divide by 1000)
        high_temp=$(grep -E "temp.*_input|temperature.*input" "$OUTDIR/system/temperature.txt" | \
                    grep -oE "[0-9]{4,6}" | \
                    awk '{temp=$1/1000; if(temp > 80 && temp < 150) print temp}' | \
                    head -1)
        if [ -n "$high_temp" ]; then
            echo "⚠️  WARNING: High temperature detected: ${high_temp}°C"
            grep -E "temp.*_input|temperature.*input" "$OUTDIR/system/temperature.txt" | head -10 | sed 's/^/   /'
            issues_found=$((issues_found + 1))
        else
            echo "✓ No temperature issues detected"
        fi
        echo ""
    fi
    
    # Check for PCI AER errors
    if [ -d "$OUTDIR/pci" ]; then
        echo "=== PCI ADVANCED ERROR REPORTING (AER) ==="
        aer_errors=false
        for pci_file in "$OUTDIR/pci"/pci_*.txt; do
            if [ -f "$pci_file" ]; then
                slot=$(basename "$pci_file" .txt | sed 's/pci_//' | tr '_' ':')
                # Check for AER error source registers - look for non-zero error values
                # ErrorSrc format: ERR_COR: XXXX ERR_FATAL/NONFATAL: XXXX
                # Only flag if we see non-zero hex values (not 0000)
                error_src=$(grep -iE "ErrorSrc.*[1-9a-fA-F]{4}|ERR_FATAL.*[1-9a-fA-F]{4}|ERR_COR.*[1-9a-fA-F]{4}" "$pci_file" 2>/dev/null | grep -vE "0000|ERR_COR: 0000|ERR_FATAL.*0000")
                if [ -n "$error_src" ]; then
                    echo "⚠️  CRITICAL: PCI device $slot has AER errors"
                    echo "$error_src" | head -3 | sed 's/^/   /'
                    aer_errors=true
                    issues_found=$((issues_found + 1))
                fi
            fi
        done
        if [ "$aer_errors" = false ]; then
            echo "✓ No PCI AER errors detected"
        fi
        echo ""
    fi
    
    # Check for pause storm errors
    if [ -d "$OUTDIR/interfaces" ]; then
        echo "=== PAUSE STORM ERRORS ==="
        pause_storm_found=false
        for iface_file in "$OUTDIR/interfaces"/*/*.txt; do
            if [ -f "$iface_file" ]; then
                pause_errors=$(grep -i "pause_storm.*error" "$iface_file" | grep -oE "[0-9]+" | head -1)
                if [ -n "$pause_errors" ] && [ "$pause_errors" -gt 0 ]; then
                    iface_name=$(basename "$(dirname "$iface_file")")
                    echo "⚠️  CRITICAL: Interface $iface_name has $pause_errors pause storm error event(s)"
                    pause_storm_found=true
                    issues_found=$((issues_found + 1))
                fi
            fi
        done
        if [ "$pause_storm_found" = false ]; then
            echo "✓ No pause storm errors detected"
        fi
        echo ""
    fi
    
    # Check for dmesg errors related to Mellanox/NVIDIA
    if [ -f "$OUTDIR/logs/logs.txt" ]; then
        echo "=== KERNEL ERRORS (Mellanox/NVIDIA) ==="
        mlx_errors=$(grep -iE "mlx|mellanox|nvidia.*error|nvidia.*fail" "$OUTDIR/logs/logs.txt" | grep -iE "error|fail|warn" | head -10)
        if [ -n "$mlx_errors" ]; then
            echo "⚠️  WARNING: Found kernel errors/warnings related to Mellanox/NVIDIA:"
            echo "$mlx_errors" | sed 's/^/   /'
            issues_found=$((issues_found + 1))
        else
            echo "✓ No Mellanox/NVIDIA kernel errors detected"
        fi
        echo ""
    fi
    
    # Check for GPU-specific issues
    if [ -d "$OUTDIR/gpu" ]; then
        echo "=== GPU ISSUES ==="
        gpu_issues_found=false
        
        # Check for GPU errors in logs
        if [ -f "$OUTDIR/gpu/gpu_info.txt" ]; then
            gpu_errors=$(grep -iE "error|fail|fatal|critical|overheat|throttle" "$OUTDIR/gpu/gpu_info.txt" | grep -vE "not available|N/A|Command not found" | wc -l)
            if [ "$gpu_errors" -gt 0 ]; then
                echo "⚠️  WARNING: Detected $gpu_errors GPU-related error(s)"
                grep -iE "error|fail|fatal|critical|overheat|throttle" "$OUTDIR/gpu/gpu_info.txt" | grep -vE "not available|N/A|Command not found" | head -5 | sed 's/^/   /'
                gpu_issues_found=true
                issues_found=$((issues_found + 1))
            fi
        fi
        
        # Check for GPU kernel errors
        if [ -f "$OUTDIR/logs/logs.txt" ]; then
            gpu_kernel_errors=$(grep -iE "nvidia.*error|gpu.*error|vga.*error|graphics.*error" "$OUTDIR/logs/logs.txt" | grep -v "N/A" | wc -l)
            if [ "$gpu_kernel_errors" -gt 0 ]; then
                echo "⚠️  CRITICAL: Detected $gpu_kernel_errors GPU kernel error(s)"
                grep -iE "nvidia.*error|gpu.*error|vga.*error|graphics.*error" "$OUTDIR/logs/logs.txt" | grep -v "N/A" | tail -5 | sed 's/^/   /'
                gpu_issues_found=true
                issues_found=$((issues_found + 1))
            fi
        fi
        
        # Check for GPU temperature issues (if nvidia-smi available)
        if [ -f "$OUTDIR/gpu/nvidia_gpu_detailed.txt" ]; then
            gpu_temp=$(grep -iE "temperature" "$OUTDIR/gpu/nvidia_gpu_detailed.txt" | grep -oE "[0-9]+" | awk '$1 > 80' | head -1)
            if [ -n "$gpu_temp" ]; then
                echo "⚠️  WARNING: High GPU temperature detected: ${gpu_temp}°C"
                grep -iE "temperature" "$OUTDIR/gpu/nvidia_gpu_detailed.txt" | head -3 | sed 's/^/   /'
                gpu_issues_found=true
                issues_found=$((issues_found + 1))
            fi
        fi
        
        if [ "$gpu_issues_found" = false ]; then
            echo "✓ No GPU issues detected"
        fi
        echo ""
    fi
    
    # Check for DPU/BlueField specific issues
    if [ -d "$OUTDIR/dpu" ]; then
        echo "=== DPU / BLUEFIELD ISSUES ==="
        dpu_issues_found=false
        
        # Check for DPU errors in local logs
        if [ -f "$OUTDIR/dpu/dpu_local.txt" ]; then
            dpu_errors=$(grep -iE "error|fail|fatal|critical" "$OUTDIR/dpu/dpu_local.txt" | grep -vE "not available|N/A|Command not found" | wc -l)
            if [ "$dpu_errors" -gt 0 ]; then
                echo "⚠️  WARNING: Detected $dpu_errors DPU-related error(s) in local logs"
                grep -iE "error|fail|fatal|critical" "$OUTDIR/dpu/dpu_local.txt" | grep -vE "not available|N/A|Command not found" | head -5 | sed 's/^/   /'
                dpu_issues_found=true
                issues_found=$((issues_found + 1))
            fi
        fi
        
        # Check for DPU kernel errors
        if [ -f "$OUTDIR/logs/logs.txt" ]; then
            dpu_kernel_errors=$(grep -iE "bluefield.*error|dpu.*error|bf[0-9].*error|bfb.*error" "$OUTDIR/logs/logs.txt" | grep -v "N/A" | wc -l)
            if [ "$dpu_kernel_errors" -gt 0 ]; then
                echo "⚠️  CRITICAL: Detected $dpu_kernel_errors DPU kernel error(s)"
                grep -iE "bluefield.*error|dpu.*error|bf[0-9].*error|bfb.*error" "$OUTDIR/logs/logs.txt" | grep -v "N/A" | tail -5 | sed 's/^/   /'
                dpu_issues_found=true
                issues_found=$((issues_found + 1))
            fi
        fi
        
        # Check for DPU firmware/status issues
        for dpu_file in "$OUTDIR/dpu"/bf*.txt "$OUTDIR/dpu"/dpfctl*.txt "$OUTDIR/dpu"/dpu_system_logs.txt; do
            if [ -f "$dpu_file" ]; then
                dpu_file_errors=$(grep -iE "error|fail|fatal|critical|unhealthy|degraded" "$dpu_file" | grep -vE "not available|N/A|Command not found" | wc -l)
                if [ "$dpu_file_errors" -gt 0 ]; then
                    dpu_file_name=$(basename "$dpu_file")
                    echo "⚠️  WARNING: Detected issues in $dpu_file_name"
                    grep -iE "error|fail|fatal|critical|unhealthy|degraded" "$dpu_file" | grep -vE "not available|N/A|Command not found" | head -3 | sed 's/^/   /'
                    dpu_issues_found=true
                    issues_found=$((issues_found + 1))
                fi
            fi
        done
        
        if [ "$dpu_issues_found" = false ]; then
            echo "✓ No DPU/BlueField issues detected"
        fi
        echo ""
    fi
    
    # Summary
    echo "=================================================="
    if [ "$issues_found" -eq 0 ]; then
        echo "✓ NO CRITICAL ISSUES FOUND"
        echo "   All checks passed successfully"
    else
        echo "⚠️  FOUND $issues_found CRITICAL ISSUE(S)"
        echo "   Please review the details above"
    fi
    echo "=================================================="
    echo ""
    
    return $issues_found
}

# Run analysis
analyze_critical_issues
ANALYSIS_EXIT_CODE=$?

########################################
# 10. Package and finish
########################################
log_info "Packaging results"

TAR_NAME="$(basename "$OUTDIR").tar.gz"
tar -czf "$TAR_NAME" -C "$(dirname "$OUTDIR")" "$(basename "$OUTDIR")"

log_info "Done."
# Get absolute path of output directory
OUTDIR_ABS=$(cd "$OUTDIR" 2>/dev/null && pwd || echo "$OUTDIR")
TAR_ABS=$(cd "$(dirname "$TAR_NAME")" 2>/dev/null && echo "$(pwd)/$(basename "$TAR_NAME")" || echo "$TAR_NAME")
log_info "Output directory: $OUTDIR_ABS"
log_info "Tarball: $TAR_ABS"
echo ""
echo "=================================================="
echo "Logs collected successfully!"
echo "=================================================="
echo "Output directory: $OUTDIR_ABS"
echo "Tarball: $TAR_ABS"
echo "=================================================="

# Exit with non-zero code if critical issues found
if [ "$ANALYSIS_EXIT_CODE" -gt 0 ]; then
    exit 1
fi

