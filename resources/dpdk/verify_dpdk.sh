#!/bin/bash
# DPDK Verification Script
# Checks DPDK installation, PCI bindings, and configuration

set -e

echo "=========================================="
echo "DPDK Verification Report"
echo "=========================================="
echo ""

# 1. Check DPDK library installation
echo "1. DPDK Library Status:"
if pkg-config --exists libdpdk 2>/dev/null; then
    DPDK_VERSION=$(pkg-config --modversion libdpdk)
    echo "   ✅ DPDK library found: version $DPDK_VERSION"
    pkg-config --cflags libdpdk >/dev/null 2>&1 && echo "   ✅ DPDK headers accessible"
else
    echo "   ❌ DPDK library not found"
fi
echo ""

# 2. Check DPDK tools
echo "2. DPDK Tools:"
if command -v dpdk-devbind.py >/dev/null 2>&1; then
    echo "   ✅ dpdk-devbind.py: $(which dpdk-devbind.py)"
else
    echo "   ❌ dpdk-devbind.py not found"
fi
if command -v dpdk-hugepages.py >/dev/null 2>&1; then
    echo "   ✅ dpdk-hugepages.py: $(which dpdk-hugepages.py)"
fi
echo ""

# 3. Check tx_worker binary
echo "3. DPDK Applications:"
TX_WORKER="/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker"
if [ -f "$TX_WORKER" ]; then
    echo "   ✅ tx_worker binary: $TX_WORKER"
    echo "   Size: $(ls -lh "$TX_WORKER" | awk '{print $5}')"
    # Check if it's linked to DPDK libraries
    if ldd "$TX_WORKER" 2>/dev/null | grep -q "librte_"; then
        echo "   ✅ Linked to DPDK libraries"
    else
        echo "   ⚠️  Not linked to DPDK libraries"
    fi
else
    echo "   ❌ tx_worker binary not found"
fi
echo ""

# 4. Check PCI device bindings
echo "4. PCI Device Bindings:"
if command -v dpdk-devbind.py >/dev/null 2>&1; then
    echo ""
    echo "   Network devices:"
    
    # Track seen PCI IDs to avoid duplicates
    declare -A seen_pci
    
    # Counters for summary
    DPDK_BOUND_COUNT=0
    KERNEL_BOUND_COUNT=0
    UNBOUND_COUNT=0
    
    # Known non-network drivers to filter out (DMA, crypto, etc.)
    NON_NETWORK_DRIVERS="idxd"
    
    # Track which section we're in
    in_network_section=0
    
    # Parse all network device sections (both "Network devices using kernel driver" and "Other Network devices")
    # Use process substitution to avoid subshell issues
    while IFS= read -r line; do
        # Skip separator lines (========)
        if [[ "$line" =~ ^=+$ ]]; then
            continue
        fi
        
        # Detect section headers - start of network section
        # "Network devices using DPDK-compatible driver" - DPDK-bound devices (MUST check first!)
        if [[ "$line" =~ "Network devices using DPDK-compatible driver" ]]; then
            in_network_section=1
            continue
        # "Network devices using kernel driver" - kernel-bound network devices
        elif [[ "$line" =~ "Network devices using kernel driver" ]]; then
            in_network_section=1
            continue
        # "Other Network devices" - unbound network devices
        elif [[ "$line" =~ "Other Network devices" ]]; then
            in_network_section=1
            continue
        # Generic "Network devices" (fallback - but NOT DPDK-compatible)
        elif [[ "$line" =~ "Network devices" ]] && [[ ! "$line" =~ "DPDK-compatible\|Crypto|DMA|Eventdev|Mempool|Compress|Misc|Regex|ML" ]]; then
            in_network_section=1
            continue
        # End of network section - other device types (but NOT "Other Network devices")
        elif [[ "$line" =~ "Other.*devices" ]] && [[ ! "$line" =~ "Other Network devices" ]]; then
            in_network_section=0
            continue
        elif [[ "$line" =~ "Crypto|DMA|Eventdev|Mempool|Compress|Misc|Regex|ML|Baseband" ]]; then
            in_network_section=0
            continue
        fi
        
        # Only process lines when in network section
        if [ "$in_network_section" -eq 0 ]; then
            continue
        fi
        
        # Check if this is a PCI device line (starts with PCI address)
        if [[ "$line" =~ ^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f] ]]; then
            PCI_ID=$(echo "$line" | awk '{print $1}')
            
            # Skip if we've already seen this PCI ID
            if [ -n "${seen_pci[$PCI_ID]}" ]; then
                continue
            fi
            seen_pci[$PCI_ID]=1
            
            DRIVER=$(echo "$line" | grep -o "drv=[^ ]*" | cut -d= -f2 || echo "")
            INTERFACE=$(echo "$line" | grep -o "if=[^ ]*" | cut -d= -f2 || echo "")
            
            # Always check /sys filesystem for actual driver (most reliable, especially for unbound devices)
            sys_driver=""
            if [ -e "/sys/bus/pci/devices/$PCI_ID/driver" ]; then
                sys_driver=$(readlink -f "/sys/bus/pci/devices/$PCI_ID/driver" 2>/dev/null | xargs basename 2>/dev/null || echo "")
            fi
            
            # Use sys driver if available (most reliable), otherwise use parsed driver
            if [ -n "$sys_driver" ]; then
                DRIVER="$sys_driver"
            elif [ -z "$DRIVER" ] || [ "$DRIVER" = "" ]; then
                DRIVER="unknown"
            fi
            
            # Determine if bound to DPDK FIRST (always show DPDK-bound devices)
            if [ "$DRIVER" = "vfio-pci" ] || [ "$DRIVER" = "uio_pci_generic" ] || [ "$DRIVER" = "igb_uio" ]; then
                echo "   ✅ $PCI_ID → DPDK ($DRIVER) [if: ${INTERFACE:-none}]"
                DPDK_BOUND_COUNT=$((DPDK_BOUND_COUNT + 1))
                continue  # Skip further filtering for DPDK-bound devices
            fi
            
            # Filter out known non-network drivers (DMA devices like idxd)
            if [[ -n "$DRIVER" ]] && [[ "$DRIVER" == "idxd" ]]; then
                continue
            fi
            
            # Additional check: verify PCI device class is network (02xx) for devices without interface names
            # This is important for unbound devices
            if [ -z "$INTERFACE" ] && [ -n "$PCI_ID" ]; then
                device_class=$(lspci -n -s "$PCI_ID" 2>/dev/null | awk '{print $2}' | cut -d: -f1 || echo "")
                # Network controller class is 02xx (starts with 02), skip if not network
                if [ -n "$device_class" ] && [[ ! "$device_class" =~ ^02 ]]; then
                    continue
                fi
            fi
            
            # Show kernel devices or unbound network devices
            if [ -z "$DRIVER" ] || [ "$DRIVER" == "unknown" ] || [ "$DRIVER" == "" ] || [ "$DRIVER" == "none" ]; then
                # Device is unbound - show it if it's a network device
                echo "   ⚠️  $PCI_ID → Unbound [if: ${INTERFACE:-none}]"
                UNBOUND_COUNT=$((UNBOUND_COUNT + 1))
            else
                echo "   ⚠️  $PCI_ID → Kernel (${DRIVER}) [if: ${INTERFACE:-none}]"
                KERNEL_BOUND_COUNT=$((KERNEL_BOUND_COUNT + 1))
            fi
        fi
    done < <(dpdk-devbind.py --status 2>/dev/null)
    
    # Double-check DPDK-bound devices from /sys (for accuracy)
    for pci in /sys/bus/pci/devices/*; do
        if [ -L "$pci/driver" ]; then
            driver=$(readlink -f "$pci/driver" 2>/dev/null | xargs basename 2>/dev/null || echo "")
            if [ "$driver" = "vfio-pci" ] || [ "$driver" = "uio_pci_generic" ] || [ "$driver" = "igb_uio" ]; then
                pci_id=$(basename "$pci")
                # Only add if not already counted
                if [ -z "${seen_pci[$pci_id]}" ]; then
                    iface=$(ls "$pci/net/" 2>/dev/null | head -1 || echo "none")
                    echo "   ✅ $pci_id → DPDK ($driver) [if: $iface]"
                    DPDK_BOUND_COUNT=$((DPDK_BOUND_COUNT + 1))
                    seen_pci[$pci_id]=1
                fi
            fi
        fi
    done
    
    # Summary
    echo ""
    echo "   Summary:"
    if [ "$DPDK_BOUND_COUNT" -gt 0 ]; then
        echo "   ✅ $DPDK_BOUND_COUNT device(s) bound to DPDK"
    else
        echo "   ⚠️  No devices currently bound to DPDK"
    fi
    if [ "$KERNEL_BOUND_COUNT" -gt 0 ]; then
        echo "   ⚠️  $KERNEL_BOUND_COUNT device(s) bound to kernel drivers"
    fi
    if [ "$UNBOUND_COUNT" -gt 0 ]; then
        echo "   ⚠️  $UNBOUND_COUNT device(s) unbound (no driver)"
        echo "      → Use 'Unbind Interface from DPDK' in UI to restore unbound devices to kernel"
    fi
else
    echo "   ❌ Cannot check bindings (dpdk-devbind.py not found)"
fi
echo ""

# 5. Check DPDK kernel modules
echo "5. DPDK Kernel Modules:"
# Check modules - handle both hyphen and underscore variants, and builtin modules
check_module() {
    local mod_name="$1"
    local mod_display="$2"
    
    # Try lsmod first (for loaded modules)
    if lsmod | grep -qE "^${mod_name}[[:space:]]"; then
        echo "   ✅ ${mod_display}: loaded"
        return 0
    fi
    
    # Check if module is builtin (compiled into kernel)
    if modinfo "$mod_name" 2>/dev/null | grep -q "(builtin)"; then
        echo "   ✅ ${mod_display}: builtin (always available)"
        return 0
    fi
    
    # Check if module file exists (available but not loaded)
    if modinfo "$mod_name" >/dev/null 2>&1; then
        echo "   ⚠️  ${mod_display}: available but not loaded"
        echo "      Load with: sudo modprobe ${mod_display}"
    else
        echo "   ⚠️  ${mod_display}: not available"
    fi
    return 1
}

# Check VFIO modules (try both hyphen and underscore variants)
check_module "vfio-pci" "vfio-pci" || check_module "vfio_pci" "vfio-pci"
check_module "vfio" "vfio"
check_module "vfio_iommu_type1" "vfio_iommu_type1"
check_module "uio_pci_generic" "uio_pci_generic"
check_module "igb_uio" "igb_uio"
echo ""

# 6. Check hugepages
echo "6. Hugepages Configuration:"
HUGEPAGES=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
HUGEPAGES_FREE=$(grep HugePages_Free /proc/meminfo | awk '{print $2}')
HUGEPAGES_SIZE=$(grep Hugepagesize /proc/meminfo | awk '{print $2}')
if [ "$HUGEPAGES" -gt 0 ]; then
    echo "   ✅ Hugepages configured:"
    echo "      Total: $HUGEPAGES pages ($(($HUGEPAGES * $HUGEPAGES_SIZE / 1024)) MB)"
    echo "      Free:  $HUGEPAGES_FREE pages ($(($HUGEPAGES_FREE * $HUGEPAGES_SIZE / 1024)) MB)"
else
    echo "   ⚠️  Hugepages not configured (0 pages)"
    echo "      Required for DPDK operation"
fi
echo ""

# 7. Check DPDK PMD availability
echo "7. DPDK PMD Libraries:"
DPDK_LIB_DIR="/usr/local/lib/x86_64-linux-gnu"
if [ -d "$DPDK_LIB_DIR" ]; then
    PMD_COUNT=$(find "$DPDK_LIB_DIR" -name "librte_net_*.so*" 2>/dev/null | wc -l)
    if [ "$PMD_COUNT" -gt 0 ]; then
        echo "   ✅ Found $PMD_COUNT PMD library(ies)"
        echo "   Available PMDs:"
        find "$DPDK_LIB_DIR" -name "librte_net_*.so*" 2>/dev/null | sed 's|.*/librte_net_||; s|\.so.*||' | sort -u | head -10 | sed 's/^/      - /'
    else
        echo "   ⚠️  No PMD libraries found"
    fi
else
    echo "   ⚠️  DPDK library directory not found"
fi
echo ""

# 8. Summary
echo "=========================================="
echo "Summary:"
echo "=========================================="

# Check if DPDK is ready
DPDK_READY=0
if pkg-config --exists libdpdk 2>/dev/null && [ -f "$TX_WORKER" ]; then
    DPDK_READY=1
    echo "✅ DPDK is installed and ready"
else
    echo "❌ DPDK installation incomplete"
fi

# Check if devices are bound
if [ "$DPDK_BOUND" -gt 0 ] 2>/dev/null; then
    echo "✅ $DPDK_BOUND device(s) bound to DPDK"
else
    echo "⚠️  No devices bound to DPDK (using kernel drivers)"
    echo "   To bind a device: dpdk-devbind.py --bind=vfio-pci <PCI_ID>"
fi

# Check hugepages
if [ "$HUGEPAGES" -gt 0 ]; then
    echo "✅ Hugepages configured"
else
    echo "⚠️  Hugepages not configured"
    echo "   To configure: echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages"
fi

echo ""
echo "=========================================="
echo "Quick Commands:"
echo "=========================================="
echo "  Check bindings:    dpdk-devbind.py --status"
echo "  Bind device:       dpdk-devbind.py --bind=vfio-pci <PCI_ID>"
echo "  Unbind device:     dpdk-devbind.py --bind=<kernel_driver> <PCI_ID>"
echo "  Setup hugepages:  echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages"
echo "  Test tx_worker:    sudo $TX_WORKER -l 0-1 -n 4 -a <PCI_ID> -- --src-mac ... --dst-mac ..."
echo ""

