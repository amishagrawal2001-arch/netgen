#!/bin/bash
# =============================================================================
# dpdk_bind.sh - DPDK NIC Binding and Unbinding Script
# =============================================================================
# This script handles binding and unbinding NICs to/from DPDK drivers.
# It automatically detects NIC vendor and provides appropriate guidance.
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Detect NIC type from PCI address
detect_nic_type() {
    local pci="$1"
    local vendor=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
    
    case "$vendor" in
        15b3) echo "mellanox" ;;      # NVIDIA/Mellanox
        14e4) echo "broadcom" ;;     # Broadcom NetXtreme-E
        8086) echo "intel" ;;        # Intel
        1022|1023|1002) echo "amd" ;; # AMD (various vendor IDs)
        *) echo "unknown" ;;
    esac
}

# Get NIC vendor name
get_nic_vendor_name() {
    local vendor_id="$1"
    case "$vendor_id" in
        15b3) echo "NVIDIA/Mellanox" ;;
        14e4) echo "Broadcom" ;;
        8086) echo "Intel" ;;
        1022|1023|1002) echo "AMD" ;;
        *) echo "Unknown" ;;
    esac
}

# Get binding recommendation
get_binding_recommendation() {
    local nic_type="$1"
    case "$nic_type" in
        mellanox)
            echo "KEEP kernel driver (mlx5_core) - DPDK mlx5 PMD works with kernel driver"
            ;;
        broadcom)
            echo "Bind to vfio-pci for DPDK"
            ;;
        intel)
            echo "Bind to vfio-pci for DPDK (typical)"
            ;;
        amd)
            echo "Bind to vfio-pci for DPDK (typical)"
            ;;
        *)
            echo "Check DPDK documentation for this vendor"
            ;;
    esac
}

# Get interface name from PCI address
get_iface_from_pci() {
    local pci="$1"
    ls /sys/bus/pci/devices/"$pci"/net 2>/dev/null | head -n1 || echo ""
}

# Get kernel driver from PCI address
get_kernel_driver() {
    local pci="$1"
    # Try the standard path first
    local driver_link="/sys/bus/pci/devices/$pci/driver"
    if [[ -L "$driver_link" ]]; then
        # Read the symlink target (e.g., "../../../drivers/bnxt_en" or "/sys/bus/pci/drivers/bnxt_en")
        local target=$(readlink "$driver_link" 2>/dev/null)
        if [[ -n "$target" ]]; then
            # Extract driver name - handle both relative and absolute paths
            if [[ "$target" == *"/drivers/"* ]]; then
                # Extract everything after /drivers/
                echo "${target##*/drivers/}" | cut -d'/' -f1
                return 0
            else
                # Relative path like "../../../drivers/bnxt_en" - get basename
                basename "$target" 2>/dev/null && return 0
            fi
        fi
    fi
    # Try alternative path (device might be in /sys/devices)
    local alt_path=$(find /sys/devices -path "*/$pci/driver" -type l 2>/dev/null | head -1)
    if [[ -n "$alt_path" ]] && [[ -L "$alt_path" ]]; then
        local target=$(readlink "$alt_path" 2>/dev/null)
        if [[ -n "$target" ]]; then
            if [[ "$target" == *"/drivers/"* ]]; then
                echo "${target##*/drivers/}" | cut -d'/' -f1
                return 0
            else
                basename "$target" 2>/dev/null && return 0
            fi
        fi
    fi
    # No driver symlink found - device is unbound
    echo ""
}

# Check if interface has active routes
has_active_routes() {
    local iface="$1"
    [[ -z "$iface" ]] && return 1
    ip -o route show 2>/dev/null | grep -q " dev $iface " && return 0 || return 1
}

# v0.3.2: defence-in-depth PCI address validator. The Python layer
# (run_tgen_server.py) checks for `":" in pci` but nothing more
# rigorous; this script runs as root via sudo so a malformed PCI
# string (or one constructed to escape sysfs path resolution) would
# be free to do real damage. Standard format per the kernel:
#   <domain:4hex>:<bus:2hex>:<device:2hex>.<function:1hex>
# Examples that pass:  0000:00:1f.6   0000:c9:00.0   abcd:01:02.7
# Examples that fail:  garbage   "../etc"   ;rm   :00:00.0   0000:00:00
validate_pci_address() {
    local pci="$1"
    [[ -z "$pci" ]] && return 1
    # Bash regex — 4 hex : 2 hex : 2 hex . 1 hex, anchored.
    if [[ "$pci" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$ ]]; then
        return 0
    fi
    return 1
}

# Bind NIC to DPDK
bind_to_dpdk() {
    local pci="$1"
    local force="${2:-0}"

    # Handle --force flag if passed as separate argument
    if [[ "$force" == "--force" ]] || [[ "$force" == "1" ]] || [[ "$force" == "true" ]]; then
        force="1"
    else
        force="0"
    fi

    check_root

    if [[ -z "$pci" ]]; then
        log_error "PCI address required"
        return 1
    fi

    # v0.3.2: strict format check BEFORE any sysfs write. The Python
    # layer doesn't enforce this; we run as root so we must.
    if ! validate_pci_address "$pci"; then
        log_error "Invalid PCI address format: '$pci' (expected NNNN:NN:NN.N hex)"
        return 1
    fi
    
    # Detect NIC type
    local nic_type=$(detect_nic_type "$pci")
    local vendor_id=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
    local vendor_name=$(get_nic_vendor_name "$vendor_id")
    local iface=$(get_iface_from_pci "$pci")
    local current_driver=$(get_kernel_driver "$pci")
    
    log_info "Binding $pci ($vendor_name) to DPDK..."
    
    # Special handling for Mellanox
    if [[ "$nic_type" == "mellanox" ]]; then
        log_warning "$vendor_name NIC detected - DO NOT bind to vfio-pci"
        log_info "Mellanox mlx5 PMD works with kernel driver (mlx5_core)"
        log_info "No binding needed for Mellanox/NVIDIA NICs"
        log_success "NIC is ready for DPDK (using kernel driver)"
        return 0
    fi
    
    # Check for active routes
    if [[ -n "$iface" ]] && has_active_routes "$iface"; then
        if [[ "$force" != "1" ]]; then
            log_warning "Interface $iface has active routes"
            log_error "Binding would disrupt network connectivity"
            log_info "Use --force to override, or remove routes first"
            return 1
        else
            log_warning "Force binding despite active routes..."
        fi
    fi
    
    # Bring interface down
    if [[ -n "$iface" ]]; then
        log_info "Bringing interface $iface down..."
        ip link set "$iface" down 2>/dev/null || true
        ip addr flush dev "$iface" 2>/dev/null || true
    fi
    
    # Unbind from current driver (if bound)
    local current_bound_driver=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
    if [[ -n "$current_bound_driver" ]] && [[ "$current_bound_driver" != "vfio-pci" ]]; then
        log_info "Unbinding from $current_bound_driver..."
        
        # Clear driver_override first (critical for clean unbind)
        echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
        
        # Unbind from current driver
        echo "$pci" > /sys/bus/pci/drivers/"$current_bound_driver"/unbind 2>/dev/null || true
        
        # Wait longer for kernel to process unbind
        sleep 1
        
        # Verify unbind succeeded with retries and longer waits
        local verify_unbound=""
        local retry_count=0
        while [[ $retry_count -lt 10 ]]; do
            # Check if driver symlink still exists
            if [[ ! -L "/sys/bus/pci/devices/$pci/driver" ]]; then
                verify_unbound=""
                break  # Driver symlink doesn't exist - device is unbound
            fi
            
            verify_unbound=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
            if [[ -z "$verify_unbound" ]] || [[ "$verify_unbound" != "$current_bound_driver" ]]; then
                break  # Unbind succeeded
            fi
            
            # If still bound, try unbinding again after a few retries
            if [[ $retry_count -gt 2 ]]; then
                log_info "Retrying unbind (attempt $((retry_count + 1)))..."
                echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
                echo "$pci" > /sys/bus/pci/drivers/"$current_bound_driver"/unbind 2>/dev/null || true
                sleep 1
            fi
            
            sleep 0.5
            retry_count=$((retry_count + 1))
        done
        
        # Final check - verify driver symlink doesn't exist or points to different driver
        if [[ -L "/sys/bus/pci/devices/$pci/driver" ]]; then
            verify_unbound=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
            if [[ "$verify_unbound" == "$current_bound_driver" ]]; then
                log_error "Unbind failed - device still bound to $current_bound_driver after $retry_count retries"
                log_info "Device may be in use. Check: lsof | grep $pci || fuser /sys/bus/pci/devices/$pci"
                return 1
            fi
        fi
        log_info "Successfully unbound from $current_bound_driver"
    elif [[ -z "$current_bound_driver" ]]; then
        log_info "Device is already unbound"
    fi
    
    # Load vfio modules if needed (including dependencies)
    log_info "Checking vfio modules..."
    
    # Check if vfio_iommu_type1 is loaded (required for vfio-pci on Intel/AMD)
    if ! lsmod | grep -q "^vfio_iommu_type1"; then
        log_info "Loading vfio_iommu_type1 module..."
        modprobe vfio_iommu_type1 2>&1 || {
            log_warning "vfio_iommu_type1 not loaded (may be builtin or not needed)"
        }
    fi
    
    # Check if vfio-pci is loaded (or builtin)
    if ! lsmod | grep -q "^vfio_pci "; then
        # Check if it's builtin
        if modinfo vfio-pci 2>/dev/null | grep -q "(builtin)"; then
            log_info "vfio-pci is builtin (always available)"
        else
            log_info "Loading vfio-pci driver..."
            modprobe vfio-pci 2>&1 || {
                log_error "Failed to load vfio-pci driver"
                log_info "Check: modinfo vfio-pci"
                return 1
            }
        fi
    else
        log_info "vfio-pci module is already loaded"
    fi
    
    # Check if device is in an IOMMU group (required for vfio-pci)
    local iommu_group=""
    if [[ -e "/sys/bus/pci/devices/$pci/iommu_group" ]]; then
        iommu_group=$(readlink -f "/sys/bus/pci/devices/$pci/iommu_group" 2>/dev/null | xargs basename 2>/dev/null || echo "")
    fi
    
    if [[ -z "$iommu_group" ]]; then
        log_error "Device $pci is not in an IOMMU group"
        log_error "This usually means IOMMU is not properly enabled or device is not supported"
        log_info "Check IOMMU status: grep -i iommu /proc/cmdline"
        log_info "Check IOMMU groups: find /sys/kernel/iommu_groups/ -name \"$pci\""
        log_info "Trying to restore original driver..."
        if [[ -n "$current_driver" ]]; then
            echo "$pci" > /sys/bus/pci/drivers/"$current_driver"/bind 2>/dev/null || true
        fi
        return 1
    else
        log_info "Device $pci is in IOMMU group $iommu_group"
    fi
    
    # Set driver override before binding (helps with some devices)
    log_info "Setting driver override to vfio-pci..."
    echo "vfio-pci" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || log_warning "Could not set driver_override (may not be supported)"
    
    # Bind to vfio-pci
    log_info "Binding to vfio-pci..."
    
    # Clear any existing error messages
    dmesg -c > /dev/null 2>&1 || true
    
    # Verify device is unbound before binding (double-check with retries)
    # Wait a bit longer after unbinding to ensure kernel has processed it
    sleep 1
    
    local verify_bound=""
    local retry_count=0
    while [[ $retry_count -lt 10 ]]; do
        # Check if driver symlink exists
        if [[ ! -L "/sys/bus/pci/devices/$pci/driver" ]]; then
            verify_bound=""
            break  # Driver symlink doesn't exist - device is unbound
        fi
        
        verify_bound=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
        if [[ -z "$verify_bound" ]] || [[ "$verify_bound" == "vfio-pci" ]]; then
            break  # Device is unbound or already bound to vfio-pci
        fi
        
        # If still bound to something else, try to unbind it
        if [[ $retry_count -gt 2 ]] && [[ -n "$verify_bound" ]] && [[ "$verify_bound" != "vfio-pci" ]]; then
            log_info "Device still shows bound to $verify_bound, attempting to unbind..."
            echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
            echo "$pci" > /sys/bus/pci/drivers/"$verify_bound"/unbind 2>/dev/null || true
            sleep 1
        fi
        
        sleep 0.5
        retry_count=$((retry_count + 1))
    done
    
    # Final verification
    if [[ -L "/sys/bus/pci/devices/$pci/driver" ]]; then
        verify_bound=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
        if [[ -n "$verify_bound" ]] && [[ "$verify_bound" != "vfio-pci" ]]; then
            log_error "Device is still bound to $verify_bound after $retry_count retries. Cannot proceed with bind."
            log_info "Check if device is in use: lsof | grep $pci || fuser /sys/bus/pci/devices/$pci"
            log_info "Try manually: echo $pci > /sys/bus/pci/drivers/$verify_bound/unbind"
            return 1
        fi
    fi
    if [[ -z "$verify_bound" ]]; then
        log_info "Device is unbound, ready for vfio-pci binding"
    elif [[ "$verify_bound" == "vfio-pci" ]]; then
        log_info "Device is already bound to vfio-pci"
        return 0
    fi
    
    # Attempt bind and capture output
    local bind_output=""
    if ! bind_output=$(echo "$pci" > /sys/bus/pci/drivers/vfio-pci/bind 2>&1); then
        # Bind command failed
        log_error "Failed to bind to vfio-pci"
        if [[ -n "$bind_output" ]]; then
            log_error "Error output: $bind_output"
        fi
        
        # Check dmesg for specific errors
        local dmesg_error=$(dmesg | tail -10 | grep -i "vfio\|iommu\|$pci" | tail -3 || true)
        if [[ -n "$dmesg_error" ]]; then
            log_error "Recent kernel messages:"
            echo "$dmesg_error" | while read -r line; do
                log_error "  $line"
            done
        fi
        
        log_info "Troubleshooting steps:"
        log_info "  1. Verify IOMMU is enabled: grep -i iommu /proc/cmdline"
        log_info "  2. Check if vfio-pci module is available: modinfo vfio-pci"
        log_info "  3. Check IOMMU group: find /sys/kernel/iommu_groups/ -name \"$pci\""
        log_info "  4. Check dmesg for errors: dmesg | tail -30 | grep -i vfio"
        log_info "  5. Verify device is not in use: lsof | grep $pci"
        log_info "  6. Check if device supports vfio: lspci -vvv -s $pci | grep -i vfio"
        
        # Clear driver override on failure
        echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
        
        log_info "Trying to restore original driver..."
        if [[ -n "$current_driver" ]]; then
            echo "$pci" > /sys/bus/pci/drivers/"$current_driver"/bind 2>/dev/null || true
        fi
        return 1
    else
        # Bind command succeeded - verify it actually worked
        sleep 0.2  # Give kernel time to process
        local bound_driver=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
        if [[ "$bound_driver" == "vfio-pci" ]]; then
            # Clear driver override after successful bind
            echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
            log_success "NIC bound to vfio-pci successfully"
            return 0
        else
            # Bind command succeeded but device is not actually bound to vfio-pci
            log_error "Bind command succeeded but device is bound to: $bound_driver (expected vfio-pci)"
            if [[ -z "$bound_driver" ]]; then
                log_error "Device appears to be unbound (no driver)"
            fi
            
            # Clear driver override on failure
            echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
            
            log_info "Trying to restore original driver..."
            if [[ -n "$current_driver" ]]; then
                echo "$pci" > /sys/bus/pci/drivers/"$current_driver"/bind 2>/dev/null || true
            fi
            return 1
        fi
    fi
}

# Unbind NIC from DPDK (return to kernel driver)
unbind_from_dpdk() {
    local pci="$1"
    local kernel_driver="${2:-}"

    check_root

    if [[ -z "$pci" ]]; then
        log_error "PCI address required"
        return 1
    fi

    # v0.3.2: same defence-in-depth guard as bind_to_dpdk above.
    if ! validate_pci_address "$pci"; then
        log_error "Invalid PCI address format: '$pci' (expected NNNN:NN:NN.N hex)"
        return 1
    fi
    
    local vendor_id=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
    local vendor_name=$(get_nic_vendor_name "$vendor_id")
    local current_driver=$(get_kernel_driver "$pci")
    
    # Track if we were bound to DPDK before starting (for success determination)
    local was_dpdk_bound=false
    if [[ "$current_driver" == "vfio-pci" ]] || [[ "$current_driver" == "uio_pci_generic" ]] || [[ "$current_driver" == "igb_uio" ]]; then
        was_dpdk_bound=true
    fi
    
    log_info "Unbinding $pci ($vendor_name) from DPDK..."
    
    # If already using kernel driver, nothing to do
    if [[ "$current_driver" != "vfio-pci" ]] && [[ "$current_driver" != "uio_pci_generic" ]] && [[ "$current_driver" != "igb_uio" ]] && [[ -n "$current_driver" ]]; then
        log_info "NIC is already using kernel driver: $current_driver"
        log_success "No action needed"
        return 0
    fi
    
    # If device is already unbound, the main goal (unbind from DPDK) is already achieved
    # We'll still try to bind to kernel driver, but if it fails, we'll return success
    if [[ -z "$current_driver" ]]; then
        log_info "Device is currently unbound (not using DPDK), will attempt to bind to kernel driver"
    fi
    
    # Determine kernel driver
    if [[ -z "$kernel_driver" ]]; then
        # Auto-detect based on vendor
        case "$vendor_id" in
            15b3) kernel_driver="mlx5_core" ;;
            14e4) kernel_driver="bnxt_en" ;;
            8086) kernel_driver="ice" ;;  # Default for Intel, may need adjustment
            *) kernel_driver="" ;;
        esac
        
        # Try to get from dpdk-devbind.py if available
        if command -v dpdk-devbind.py >/dev/null 2>&1; then
            local unused=$(dpdk-devbind.py --status 2>/dev/null | \
                awk -v pci="$pci" '$1==pci {gsub(/.*unused=/, ""); gsub(/[^a-z_].*/, ""); print; exit}')
            if [[ -n "$unused" ]] && [[ "$unused" != "vfio-pci" ]]; then
                kernel_driver="$unused"
            fi
        fi
    fi
    
    if [[ -z "$kernel_driver" ]]; then
        log_error "Could not determine kernel driver"
        log_info "Please specify with: --kernel-driver <driver_name>"
        return 1
    fi
    
    # Clear driver_override FIRST (before unbinding) - this is critical!
    log_info "Clearing driver_override..."
    echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
    
    # Unbind from DPDK driver (vfio-pci, uio_pci_generic, or igb_uio)
    if [[ "$current_driver" == "vfio-pci" ]] || [[ "$current_driver" == "uio_pci_generic" ]] || [[ "$current_driver" == "igb_uio" ]]; then
        log_info "Unbinding from $current_driver..."
        echo "$pci" > /sys/bus/pci/drivers/"$current_driver"/unbind 2>/dev/null || true
        # Wait for unbind to complete
        sleep 0.5
        
        # Verify unbind succeeded
        local verify_unbound=$(readlink -f /sys/bus/pci/devices/"$pci"/driver 2>/dev/null | xargs basename 2>/dev/null || echo "")
        if [[ "$verify_unbound" == "$current_driver" ]]; then
            log_warning "Unbind may not have completed, device still shows $current_driver"
        fi
    fi
    
    # For Broadcom NICs (bnxt_en), try reloading driver first if binding fails
    # This helps with firmware initialization issues
    local is_broadcom=false
    if [[ "$kernel_driver" == "bnxt_en" ]] || [[ "$vendor_id" == "14e4" ]]; then
        is_broadcom=true
    fi
    
    # Load kernel driver if needed
    if ! lsmod | grep -q "^${kernel_driver} "; then
        log_info "Loading kernel driver: $kernel_driver"
        modprobe "$kernel_driver" || {
            log_error "Failed to load kernel driver: $kernel_driver"
            log_info "Try manually: modprobe $kernel_driver"
            return 1
        }
    fi
    
    # Ensure driver is ready (some drivers need a moment after modprobe)
    sleep 0.2
    
    # Wait a moment after unbinding to let kernel process
    sleep 0.5
    
    # Clear driver override if set (must be done before binding)
    echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
    
    # Try binding to kernel driver with retries
    log_info "Binding to kernel driver: $kernel_driver"
    local bind_output=""
    local bind_exit=1
    local bound_driver=""
    local bind_retry=0
    local max_bind_retries=3
    
    while [[ $bind_retry -lt $max_bind_retries ]]; do
        # Clear driver override before each attempt
        echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
        
        # Try binding
        bind_output=$(echo "$pci" > /sys/bus/pci/drivers/"$kernel_driver"/bind 2>&1) || true
        bind_exit=$?
        
        # Wait for bind to complete
        sleep 1
        
        # Verify bind succeeded
        bound_driver=$(get_kernel_driver "$pci")
        
        if [[ "$bound_driver" == "$kernel_driver" ]]; then
            log_info "Successfully bound to $kernel_driver on attempt $((bind_retry + 1))"
            break
        fi
        
        # If binding failed and this is a Broadcom NIC, try more aggressive recovery
        if [[ $bind_retry -eq 0 ]] && [[ "$is_broadcom" == true ]] && [[ -z "$bound_driver" ]]; then
            log_info "First bind attempt failed for Broadcom NIC, trying driver reload..."
            rmmod "$kernel_driver" 2>/dev/null || true
            sleep 2
            modprobe "$kernel_driver" || {
                log_warning "Failed to reload driver, continuing with next attempt..."
            }
            sleep 2
        elif [[ $bind_retry -eq 1 ]] && [[ "$is_broadcom" == true ]] && [[ -z "$bound_driver" ]]; then
            log_info "Second bind attempt failed for Broadcom NIC, trying PCI reset..."
            # Try PCI reset (remove and rescan) - this can help with firmware issues
            # First, ensure device is unbound
            echo "" > /sys/bus/pci/devices/"$pci"/driver_override 2>/dev/null || true
            
            # Try using setpci to reset the device's command register (more aggressive)
            if command -v setpci >/dev/null 2>&1; then
                log_info "Attempting PCI command register reset using setpci..."
                # Read current command register
                local cmd_reg=$(setpci -s "$pci" COMMAND 2>/dev/null || echo "")
                if [[ -n "$cmd_reg" ]]; then
                    # Disable device (clear bit 0)
                    setpci -s "$pci" COMMAND=0x0000 2>/dev/null || true
                    sleep 1
                    # Re-enable device (set bit 0)
                    setpci -s "$pci" COMMAND=0x0007 2>/dev/null || true
                    sleep 2
                    log_info "PCI command register reset completed"
                fi
            fi
            
            # Remove device from PCI bus
            if [[ -e /sys/bus/pci/devices/"$pci" ]]; then
                echo 1 > /sys/bus/pci/devices/"$pci"/remove 2>/dev/null || true
                log_info "Removed device from PCI bus, waiting for removal to complete..."
                sleep 3
            fi
            
            # Rescan PCI bus to reinitialize device
            echo 1 > /sys/bus/pci/rescan 2>/dev/null || true
            log_info "Rescanned PCI bus, waiting for device to reappear..."
            sleep 5  # Wait longer for device to reappear and firmware to initialize
            
            # Verify device reappeared
            if [[ ! -e /sys/bus/pci/devices/"$pci" ]]; then
                log_warning "Device did not reappear after PCI rescan - reboot may be required"
            else
                log_info "Device reappeared after PCI rescan"
            fi
            
            # Reload driver after PCI reset (give it more time)
            rmmod "$kernel_driver" 2>/dev/null || true
            sleep 2
            modprobe "$kernel_driver" || {
                log_warning "Failed to reload driver after PCI reset..."
            }
            sleep 3  # Wait longer for driver to be ready
            
            # Check dmesg for firmware errors before retrying
            local dmesg_check=$(dmesg | tail -5 | grep -i "firmware.*$pci\|$kernel_driver.*$pci" || true)
            if [[ -n "$dmesg_check" ]]; then
                log_warning "Firmware errors detected after PCI reset:"
                echo "$dmesg_check" | while read -r line; do
                    log_warning "  $line"
                done
                log_warning "This Broadcom firmware issue may require a server reboot to resolve"
                log_info "NOTE: After reboot, future unbind operations should work normally without reboot"
            fi
        fi
        
        bind_retry=$((bind_retry + 1))
        if [[ $bind_retry -lt $max_bind_retries ]]; then
            log_info "Bind attempt $bind_retry failed, retrying... (${bound_driver:-unbound})"
            sleep 1
        fi
    done
    
    # Debug: log the current state
    log_info "Bind attempt completed. Current driver: ${bound_driver:-unbound}, Expected: $kernel_driver, Was DPDK bound: $was_dpdk_bound"
    
    if [[ "$bound_driver" == "$kernel_driver" ]]; then
        log_success "NIC bound to kernel driver successfully"
        
        # Wait a bit more for interface to be created by the driver
        sleep 1
        
        # Get interface name (may take a moment to appear)
        local iface=""
        local retry_count=0
        while [[ $retry_count -lt 5 ]] && [[ -z "$iface" ]]; do
            iface=$(ls /sys/bus/pci/devices/"$pci"/net/ 2>/dev/null | head -1 || echo "")
            if [[ -z "$iface" ]]; then
                sleep 0.5
                retry_count=$((retry_count + 1))
            fi
        done
        
        if [[ -n "$iface" ]]; then
            log_info "Interface $iface detected"
            log_info "Bringing interface $iface up..."
            ip link set "$iface" up 2>/dev/null || true
            
            # Wait a moment for interface to initialize
            sleep 1
            
            # Verify interface is visible in ip link
            if ip link show "$iface" >/dev/null 2>&1; then
                log_success "Interface $iface is now available and visible in 'ip link show'"
                
                # Check link state (especially important for fiber interfaces)
                local link_state=$(ip link show "$iface" 2>/dev/null | grep -o "state [A-Z]*" | awk '{print $2}' || echo "")
                local has_carrier=$(ip link show "$iface" 2>/dev/null | grep -q "LOWER_UP" && echo "yes" || echo "no")
                
                if [[ "$has_carrier" == "no" ]]; then
                    log_warning "Interface $iface is UP but link is DOWN (NO-CARRIER)"
                    log_info "This is normal if no cable is connected"
                    log_info "For fiber interfaces, link negotiation may take a few seconds"
                    
                    # For fiber interfaces, try to trigger link negotiation
                    if command -v ethtool >/dev/null 2>&1; then
                        local port_type=$(ethtool "$iface" 2>/dev/null | grep -i "Supported ports" | grep -qi "FIBRE" && echo "fiber" || echo "")
                        if [[ "$port_type" == "fiber" ]]; then
                            log_info "Fiber interface detected - attempting to trigger link negotiation..."
                            # Bring interface down and up again to trigger link negotiation
                            ip link set "$iface" down 2>/dev/null || true
                            sleep 1
                            ip link set "$iface" up 2>/dev/null || true
                            sleep 2
                            
                            # Check again
                            has_carrier=$(ip link show "$iface" 2>/dev/null | grep -q "LOWER_UP" && echo "yes" || echo "no")
                            if [[ "$has_carrier" == "yes" ]]; then
                                log_success "Link came up after negotiation"
                            else
                                log_info "Link still down - this may require:"
                                log_info "  1. Physical cable connection"
                                log_info "  2. More time for link negotiation (fiber can take 5-10 seconds)"
                                log_info "  3. Server reboot if link doesn't come up (known Broadcom firmware issue)"
                            fi
                        fi
                    fi
                else
                    log_success "Interface $iface link is UP (LOWER_UP)"
                fi
            else
                log_warning "Interface $iface detected but not visible in 'ip link show' yet"
                log_info "It may appear shortly. Check with: ip link show"
            fi
        else
            log_warning "Device bound to $kernel_driver but interface not detected yet"
            log_info "Waiting longer for interface to appear..."
            
            # Wait longer with more retries for interface to appear
            local iface=""
            local iface_retry=0
            while [[ $iface_retry -lt 10 ]] && [[ -z "$iface" ]]; do
                sleep 1
                iface=$(ls /sys/bus/pci/devices/"$pci"/net/ 2>/dev/null | head -1 || echo "")
                if [[ -n "$iface" ]]; then
                    log_info "Interface $iface detected after $((iface_retry + 1)) second(s)"
                    ip link set "$iface" up 2>/dev/null || true
                    sleep 1
                    if ip link show "$iface" >/dev/null 2>&1; then
                        log_success "Interface $iface is now available and visible in 'ip link show'"
                        
                        # Check link state
                        local has_carrier=$(ip link show "$iface" 2>/dev/null | grep -q "LOWER_UP" && echo "yes" || echo "no")
                        if [[ "$has_carrier" == "yes" ]]; then
                            log_success "Link is UP (LOWER_UP)"
                        else
                            log_info "Link is DOWN (NO-CARRIER) - may need cable connection or link negotiation"
                        fi
                        break
                    fi
                fi
                iface_retry=$((iface_retry + 1))
            done
            
            if [[ -z "$iface" ]]; then
                log_warning "Interface still not detected after waiting"
                log_info "Check with: ip link show"
                
                # Check dmesg for firmware errors (common with Broadcom NICs)
                local dmesg_error=$(dmesg | tail -30 | grep -i "$kernel_driver.*$pci\|firmware.*$pci" | tail -5 || true)
                if [[ -n "$dmesg_error" ]]; then
                    log_warning "Firmware issue detected - this is a known Broadcom NIC issue:"
                    echo "$dmesg_error" | while read -r line; do
                        log_warning "  $line"
                    done
                    log_info "Device is unbound from DPDK and bound to kernel driver"
                    log_info "The interface may not appear due to firmware initialization failure"
                    log_info "Try these steps to recover:"
                    log_info "  1. Reload driver: rmmod $kernel_driver && modprobe $kernel_driver"
                    log_info "  2. Reset PCI device: echo 1 > /sys/bus/pci/devices/$pci/remove && echo 1 > /sys/bus/pci/rescan"
                    log_info "  3. If still not working, reboot may be required"
                fi
            fi
        fi
        
        return 0
    else
        # Bind failed or device not bound to expected driver
        # If we successfully unbound from DPDK, consider it a partial success
        if [[ "$was_dpdk_bound" == true ]] && [[ -z "$bound_driver" ]]; then
            log_warning "Successfully unbound from DPDK, but binding to kernel driver failed"
            log_info "Device is now unbound (not using DPDK anymore)"
            log_info "You can manually bind it later with: echo $pci > /sys/bus/pci/drivers/$kernel_driver/bind"
            
            # Check dmesg for errors
            local dmesg_error=$(dmesg | tail -10 | grep -i "$kernel_driver.*$pci\|firmware.*$pci" | tail -3 || true)
            if [[ -n "$dmesg_error" ]]; then
                log_warning "Recent kernel messages:"
                echo "$dmesg_error" | while read -r line; do
                    log_warning "  $line"
                done
            fi
            
            # Return success since unbind from DPDK succeeded (main goal achieved)
            return 0
        fi
        
        # Also check if device was already unbound (not bound to DPDK) and bind failed
        # In this case, if we tried to bind but it failed, we should still return success
        # because the device is not bound to DPDK (which is the goal of "unbind")
        if [[ "$was_dpdk_bound" == false ]] && [[ -z "$bound_driver" ]]; then
            log_warning "Device was already unbound from DPDK, but binding to kernel driver failed"
            log_info "Device remains unbound (not using DPDK) - main goal achieved"
            log_info "You can manually bind it later with: echo $pci > /sys/bus/pci/drivers/$kernel_driver/bind"
            
            # Check dmesg for errors (especially firmware issues with Broadcom)
            local dmesg_error=$(dmesg | tail -20 | grep -i "$kernel_driver.*$pci\|firmware.*$pci" | tail -5 || true)
            if [[ -n "$dmesg_error" ]]; then
                log_warning "Recent kernel messages:"
                echo "$dmesg_error" | while read -r line; do
                    log_warning "  $line"
                done
                log_info "This is a Broadcom firmware initialization issue. The device is unbound from DPDK."
                
                # If firmware errors persist after all retries, suggest reboot
                if echo "$dmesg_error" | grep -qi "firmware not responding"; then
                    log_warning "⚠️  FIRMWARE ISSUE DETECTED:"
                    log_warning "The Broadcom NIC firmware is not responding. This is a known hardware issue."
                    log_warning "All recovery attempts have been exhausted."
                    log_warning "RECOMMENDATION: A server reboot is required to reset the firmware."
                    log_info "After reboot, the interface should appear normally in 'ip link show'"
                fi
            fi
            
            # Return success since device is not bound to DPDK (main goal achieved)
            log_success "Unbind operation successful (device not bound to DPDK)"
            return 0
        fi
        
        # If device is still unbound (regardless of was_dpdk_bound), consider it success
        # The main goal of "unbind" is to ensure device is not bound to DPDK
        if [[ -z "$bound_driver" ]]; then
            log_warning "Binding to kernel driver failed, but device is unbound from DPDK"
            log_info "Device remains unbound (not using DPDK) - main goal achieved"
            log_info "You can manually bind it later with: echo $pci > /sys/bus/pci/drivers/$kernel_driver/bind"
            
            # Check dmesg for errors
            local dmesg_error=$(dmesg | tail -20 | grep -i "$kernel_driver.*$pci\|firmware.*$pci" | tail -5 || true)
            if [[ -n "$dmesg_error" ]]; then
                log_warning "Recent kernel messages:"
                echo "$dmesg_error" | while read -r line; do
                    log_warning "  $line"
                done
            fi
            
            log_success "Unbind operation successful (device not bound to DPDK)"
            return 0
        fi
        
        # Otherwise, it's a real failure (device is bound to something unexpected)
        if [[ $bind_exit -eq 0 ]] && [[ -z "$bind_output" ]]; then
            log_error "Bind command succeeded but device is bound to: $bound_driver (expected $kernel_driver)"
        else
            log_error "Failed to bind to kernel driver"
        fi
        if [[ -n "$bind_output" ]]; then
            log_error "Error output: $bind_output"
        fi
        
        # Check dmesg for errors
        local dmesg_error=$(dmesg | tail -10 | grep -i "$kernel_driver\|$pci" | tail -3 || true)
        if [[ -n "$dmesg_error" ]]; then
            log_error "Recent kernel messages:"
            echo "$dmesg_error" | while read -r line; do
                log_error "  $line"
            done
        fi
        
        log_info "Troubleshooting steps:"
        log_info "  1. Check if kernel driver module is loaded: lsmod | grep $kernel_driver"
        log_info "  2. Try loading the driver: modprobe $kernel_driver"
        log_info "  3. Check dmesg for errors: dmesg | tail -30 | grep -i \"$kernel_driver\|$pci\""
        log_info "  4. Verify device is not in use: lsof | grep $pci"
        log_info "  5. Try removing and rescanning PCI bus: echo 1 > /sys/bus/pci/rescan"
        
        return 1
    fi
}

# Show NIC status
show_nic_status() {
    log_info "Available Network Interfaces:"
    echo ""
    printf "%-15s %-20s %-15s %-12s %-10s %s\n" "PCI Address" "Interface" "Driver" "NUMA" "Type" "Status"
    echo "────────────────────────────────────────────────────────────────────────────────────────────"
    
    if ! command -v dpdk-devbind.py >/dev/null 2>&1; then
        log_warning "dpdk-devbind.py not found, using sysfs method"
        return 0
    fi
    
    local nic_list=()
    while IFS= read -r line; do
        if [[ "$line" =~ ^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f] ]]; then
            local pci=$(echo "$line" | awk '{print $1}')
            local desc=$(echo "$line" | sed "s/^$pci //" | sed "s/ numa_node=.*//")
            local numa=$(echo "$line" | grep -o "numa_node=[0-9-]*" | cut -d= -f2)
            local iface=$(echo "$line" | grep -o "if=[^ ]*" | cut -d= -f2)
            local driver=$(echo "$line" | grep -o "drv=[^ ]*" | cut -d= -f2)
            local active=$(echo "$line" | grep -q "*Active*" && echo "Active" || echo "")
            
            # Detect NIC type
            local nic_type=$(detect_nic_type "$pci")
            local vendor_id=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
            local vendor_name=$(get_nic_vendor_name "$vendor_id")
            
            # Shorten description if too long
            if [[ ${#desc} -gt 50 ]]; then
                desc="${desc:0:47}..."
            fi
            
            printf "%-15s %-20s %-15s %-12s %-10s %s\n" "$pci" "${iface:-N/A}" "${driver:-N/A}" "Node $numa" "$vendor_name" "$active"
            
            # Store for summary
            nic_list+=("$pci|$iface|$driver|$nic_type|$desc|$vendor_id")
        fi
    done < <(dpdk-devbind.py --status 2>/dev/null | grep -A 100 "Network devices" | grep -E "^[0-9a-f]{4}:" || true)
    
    echo ""
    
    if [[ ${#nic_list[@]} -gt 0 ]]; then
        log_info "NIC to Interface Mapping Summary:"
        echo ""
        for nic_info in "${nic_list[@]}"; do
            IFS='|' read -r pci iface driver nic_type desc vendor_id <<< "$nic_info"
            local vendor_name=$(get_nic_vendor_name "$vendor_id")
            local recommendation=$(get_binding_recommendation "$nic_type")
            
            echo "  • $pci → $iface ($driver) - $desc"
            echo "    Vendor: $vendor_name (ID: $vendor_id)"
            case "$nic_type" in
                mellanox)
                    echo "    ⚠️  $recommendation"
                    ;;
                broadcom|intel|amd)
                    echo "    ✅ $recommendation"
                    ;;
                *)
                    echo "    ⚠️  $recommendation"
                    ;;
            esac
        done
        echo ""
    fi
}

# Usage
usage() {
    cat <<EOF
Usage: sudo $0 [command] [options]

Commands:
  bind <PCI>              Bind NIC to DPDK (vfio-pci)
  unbind <PCI>            Unbind NIC from DPDK (return to kernel driver)
  status                  Show NIC status and bindings
  show                    Alias for status

Options:
  --force                 Force binding even if routes exist
  --kernel-driver <drv>   Specify kernel driver for unbind (auto-detected if not specified)
  --help, -h              Show this help

Examples:
  # Show NIC status
  sudo $0 status

  # Bind NIC to DPDK
  sudo $0 bind 0000:8a:00.0

  # Bind with force (ignore active routes)
  sudo $0 bind 0000:8a:00.0 --force

  # Unbind NIC (return to kernel driver)
  sudo $0 unbind 0000:8a:00.0

  # Unbind with specific kernel driver
  sudo $0 unbind 0000:8a:00.0 --kernel-driver bnxt_en

EOF
}

# Main
main() {
    local command="${1:-}"
    
    case "$command" in
        bind)
            shift
            local pci=""
            local force=0
            
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --force)
                        force=1
                        shift
                        ;;
                    --help|-h)
                        usage
                        exit 0
                        ;;
                    *)
                        if [[ -z "$pci" ]]; then
                            pci="$1"
                        fi
                        shift
                        ;;
                esac
            done
            
            if [[ -z "$pci" ]]; then
                log_error "PCI address required"
                usage
                exit 1
            fi
            
            bind_to_dpdk "$pci" "$force"
            ;;
            
        unbind)
            shift
            local pci=""
            local kernel_driver=""
            
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --kernel-driver)
                        kernel_driver="$2"
                        shift 2
                        ;;
                    --help|-h)
                        usage
                        exit 0
                        ;;
                    *)
                        if [[ -z "$pci" ]]; then
                            pci="$1"
                        fi
                        shift
                        ;;
                esac
            done
            
            if [[ -z "$pci" ]]; then
                log_error "PCI address required"
                usage
                exit 1
            fi
            
            unbind_from_dpdk "$pci" "$kernel_driver"
            ;;
            
        status|show)
            show_nic_status
            ;;
            
        --help|-h|"")
            usage
            exit 0
            ;;
            
        *)
            log_error "Unknown command: $command"
            usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"

