#!/bin/bash
# =============================================================================
# install_dpdk.sh - User-Friendly DPDK Installation Script
# =============================================================================
# This script provides an interactive, step-by-step DPDK installation process
# with automatic detection, progress indicators, and error handling.
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
cd "$SCRIPT_DIR"

# Default values
DPDK_DIR="${DPDK_DIR:-$HOME/SURAJ/dpdk}"
AUTO_MODE="${AUTO_MODE:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"

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

log_step() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Prompt for user input
prompt_yes_no() {
    local prompt="$1"
    local default="${2:-y}"
    local response
    
    if [[ "$AUTO_MODE" == "1" ]]; then
        echo "$default"
        return
    fi
    
    while true; do
        read -p "$prompt [Y/n]: " response
        response="${response:-$default}"
        case "$response" in
            [Yy]* ) echo "y"; return;;
            [Nn]* ) echo "n"; return;;
            * ) echo "Please answer yes or no.";;
        esac
    done
}

# Prompt for input with default
prompt_input() {
    local prompt="$1"
    local default="$2"
    local response
    
    if [[ "$AUTO_MODE" == "1" ]]; then
        echo "$default"
        return
    fi
    
    read -p "$prompt [$default]: " response
    echo "${response:-$default}"
}

# Detect DPDK source location
detect_dpdk_source() {
    local possible_locations=(
        "$HOME/SURAJ/dpdk"
        "$HOME/dpdk"
        "/opt/dpdk"
        "/usr/src/dpdk"
    )
    
    for loc in "${possible_locations[@]}"; do
        if [[ -d "$loc" && -f "$loc/meson.build" ]]; then
            echo "$loc"
            return 0
        fi
    done
    
    return 1
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

# Get DPDK binding recommendation
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

# Check if DPDK is already installed
check_dpdk_installed() {
    if pkg-config --exists libdpdk 2>/dev/null; then
        local version=$(pkg-config --modversion libdpdk)
        log_success "DPDK is already installed (version $version)"
        return 0
    fi
    return 1
}

# Step 1: Pre-flight checks
step_preflight() {
    log_step "Step 1: Pre-flight Checks"
    
    check_root
    
    log_info "Checking system requirements..."
    
    # Check disk space (need at least 2GB)
    local available=$(df -BG / | tail -1 | awk '{print $4}' | sed 's/G//')
    if [[ $available -lt 2 ]]; then
        log_warning "Low disk space: ${available}GB available (recommended: 2GB+)"
        if [[ $(prompt_yes_no "Continue anyway?") != "y" ]]; then
            exit 1
        fi
    else
        log_success "Disk space OK: ${available}GB available"
    fi
    
    # Check network connectivity
    if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
        log_success "Network connectivity OK"
    else
        log_warning "Network connectivity check failed"
        log_warning "Dependency installation may fail without internet"
    fi
    
    # Check if DPDK already installed
    if check_dpdk_installed; then
        if [[ $(prompt_yes_no "DPDK is already installed. Reinstall?") != "y" ]]; then
            log_info "Skipping installation"
            exit 0
        fi
    fi
}

# Step 2: Detect DPDK source
step_detect_dpdk_source() {
    log_step "Step 2: DPDK Source Detection"
    
    if [[ -n "${DPDK_DIR:-}" && -d "$DPDK_DIR" && -f "$DPDK_DIR/meson.build" ]]; then
        log_success "DPDK source found: $DPDK_DIR"
        return 0
    fi
    
    log_info "Searching for DPDK source..."
    local detected=$(detect_dpdk_source)
    
    if [[ -n "$detected" ]]; then
        log_success "DPDK source detected: $detected"
        DPDK_DIR="$detected"
    else
        log_warning "DPDK source not found"
        DPDK_DIR=$(prompt_input "Enter DPDK source directory" "$HOME/SURAJ/dpdk")
        
        if [[ ! -d "$DPDK_DIR" ]]; then
            log_error "Directory does not exist: $DPDK_DIR"
            if [[ $(prompt_yes_no "Clone DPDK from git?") == "y" ]]; then
                step_clone_dpdk
            else
                log_error "Cannot proceed without DPDK source"
                exit 1
            fi
        fi
    fi
}

# Step 3: Clone DPDK if needed
step_clone_dpdk() {
    log_step "Step 3: Cloning DPDK"
    
    local target_dir=$(dirname "$DPDK_DIR")
    local dpdk_name=$(basename "$DPDK_DIR")
    
    log_info "Cloning DPDK to: $DPDK_DIR"
    
    if command -v git >/dev/null 2>&1; then
        cd "$target_dir"
        git clone https://dpdk.org/git/dpdk "$dpdk_name" || {
            log_error "Failed to clone DPDK"
            exit 1
        }
        cd "$dpdk_name"
        git checkout v23.11 || git checkout main
        log_success "DPDK cloned successfully"
    else
        log_error "git not found. Please install git or provide DPDK source manually"
        exit 1
    fi
}

# Check if DPDK dependencies are installed
check_dpdk_dependencies() {
    local missing=0
    local deps=("meson" "ninja" "pkg-config" "gcc")
    local lib_deps=("libnuma-dev" "libelf-dev" "libpcap-dev")
    
    log_info "Checking DPDK build dependencies..."
    
    # Check binaries
    for dep in "${deps[@]}"; do
        if command -v "$dep" >/dev/null 2>&1; then
            log_success "$dep found"
        else
            log_warning "$dep not found"
            missing=1
        fi
    done
    
    # Check libraries (try pkg-config first, then dpkg)
    for lib in "${lib_deps[@]}"; do
        local lib_name=$(echo "$lib" | sed 's/-dev$//')
        if pkg-config --exists "$lib_name" 2>/dev/null || \
           dpkg -l | grep -q "^ii.*$lib "; then
            log_success "$lib found"
        else
            log_warning "$lib not found"
            missing=1
        fi
    done
    
    return $missing
}

# Step 4: Install dependencies
step_install_dependencies() {
    log_step "Step 4: Installing Build Dependencies"
    
    # Check if dependencies are already installed
    if check_dpdk_dependencies; then
        log_success "All DPDK dependencies are already installed"
        return 0
    fi
    
    log_info "Some DPDK dependencies are missing"
    log_info "This will install: build-essential, meson, ninja-build, pkg-config, libnuma-dev, libelf-dev, libpcap-dev"
    
    if [[ $(prompt_yes_no "Install missing dependencies?") != "y" ]]; then
        log_warning "Skipping dependency installation"
        if ! check_dpdk_dependencies; then
            log_error "Required dependencies are missing"
            if [[ $(prompt_yes_no "Continue anyway? (may fail later)") != "y" ]]; then
                exit 1
            fi
        fi
        return 0
    fi
    
    log_info "Running dependency installation..."
    
    # Install dependencies directly
    log_info "Updating package lists..."
    MAX_RETRIES=3
    RETRY_COUNT=0
    APT_UPDATE_SUCCESS=0
    
    while [[ $RETRY_COUNT -lt $MAX_RETRIES ]]; do
        if apt-get update -y --option Acquire::http::Timeout=30 --option Acquire::ftp::Timeout=30 2>&1 | grep -q "Reading package lists"; then
            APT_UPDATE_SUCCESS=1
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [[ $RETRY_COUNT -lt $MAX_RETRIES ]]; then
            log_warning "Update failed, retrying in 5 seconds..."
            sleep 5
        fi
    done
    
    if [[ $APT_UPDATE_SUCCESS -eq 0 ]]; then
        log_warning "apt-get update failed after $MAX_RETRIES attempts"
        log_warning "Continuing anyway (using cached package lists)..."
    fi
    
    log_info "Installing build dependencies..."
    local deps_install_cmd="apt-get install -y --option Acquire::http::Timeout=30 --option Acquire::ftp::Timeout=30 build-essential meson ninja-build pkg-config libnuma-dev libelf-dev libpcap-dev"
    
    # Try to install dependencies
    if eval "$deps_install_cmd" 2>&1 | tee /tmp/dpdk_deps_install.log; then
        log_success "Dependencies installed successfully"
    else
        # Check if it's just unrelated package issues (like NVIDIA drivers)
        if grep -q "nvidia\|unmet dependencies" /tmp/dpdk_deps_install.log 2>/dev/null; then
            log_warning "Installation reported errors (possibly unrelated package issues)"
            log_info "Checking if DPDK dependencies are actually installed..."
            
            if check_dpdk_dependencies; then
                log_success "DPDK dependencies are installed (ignoring unrelated package errors)"
                log_info "You may want to fix NVIDIA driver issues separately: apt --fix-broken install"
            else
                log_error "DPDK dependencies are still missing"
                if [[ $(prompt_yes_no "Continue anyway? (may fail during build)") != "y" ]]; then
                    exit 1
                fi
            fi
        else
            log_error "Dependency installation failed"
            if [[ $(prompt_yes_no "Continue anyway?") != "y" ]]; then
                exit 1
            fi
        fi
    fi
    
    rm -f /tmp/dpdk_deps_install.log
}

# Step 5: Build DPDK
step_build_dpdk() {
    log_step "Step 5: Building DPDK"
    
    if [[ "$SKIP_BUILD" == "1" ]]; then
        log_info "Skipping DPDK build (--skip-build flag)"
        return 0
    fi
    
    log_info "Building DPDK from: $DPDK_DIR"
    log_warning "This may take 10-30 minutes depending on your CPU..."
    
    if [[ $(prompt_yes_no "Continue with build?") != "y" ]]; then
        log_warning "Skipping DPDK build"
        SKIP_BUILD=1
        return 0
    fi
    
    log_info "Starting DPDK build..."
    
    # Build DPDK using meson and ninja
    cd "$DPDK_DIR"
    
    # Drivers we always disable:
    #   net/mana — Microsoft Azure NIC PMD; requires IBVERBS_PRIVATE_34 symbols
    #              which Ubuntu 22.04's libibverbs doesn't ship, so the link
    #              step fails with "undefined reference to ibv_cmd_query_*".
    #              No real Azure mana NIC = nothing lost by skipping.
    local meson_disable="net/mana"
    local meson_opts=("-Dexamples=all" "-Ddisable_drivers=${meson_disable}")

    if [[ ! -d "build" ]]; then
        log_info "Configuring DPDK build (disabling: ${meson_disable})..."
        meson setup build "${meson_opts[@]}" || {
            log_error "DPDK meson setup failed"
            exit 1
        }
    else
        log_info "DPDK build directory exists, wiping and reconfiguring..."
        # --wipe drops cached config from a previous failed run so the new
        # disable_drivers flag is actually applied (without --wipe meson
        # would honour the old options.txt and re-build the same broken set).
        meson setup build --wipe "${meson_opts[@]}" || {
            log_error "DPDK meson reconfigure failed"
            exit 1
        }
    fi
    
    log_info "Compiling DPDK (this may take 10-30 minutes)..."
    ninja -C build || {
        log_error "DPDK build failed"
        exit 1
    }
    
    log_info "Installing DPDK to /usr/local..."
    ninja -C build install || {
        log_error "DPDK installation failed"
        exit 1
    }
    
    cd "$SCRIPT_DIR"
    log_success "DPDK built and installed successfully"
}

# Step 6: Build tx_worker
step_build_tx_worker() {
    log_step "Step 6: Building tx_worker Application"
    
    log_info "Building tx_worker application..."
    
    # Ensure we use the correct DPDK installation (from /usr/local)
    # Set PKG_CONFIG_PATH to prioritize /usr/local installation
    export PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
    
    # Verify DPDK libraries are accessible
    local dpdk_libdir=$(PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" pkg-config --variable=libdir libdpdk 2>/dev/null || echo "")
    
    if [[ -z "$dpdk_libdir" ]]; then
        log_warning "Could not detect DPDK library directory via pkg-config"
        log_info "Trying to find DPDK libraries..."
        
        if [[ -d "/usr/local/lib/x86_64-linux-gnu" ]] && \
           ls /usr/local/lib/x86_64-linux-gnu/librte_*.so* >/dev/null 2>&1; then
            log_success "Found DPDK libraries in /usr/local/lib/x86_64-linux-gnu"
            dpdk_libdir="/usr/local/lib/x86_64-linux-gnu"
        else
            log_error "DPDK libraries not found. Make sure DPDK was installed correctly."
            exit 1
        fi
    else
        log_info "Using DPDK libraries from: $dpdk_libdir"
    fi
    
    # Clean build directory to remove cached meson configuration
    log_info "Cleaning build directory to remove cached configuration..."
    if [[ -d "$SCRIPT_DIR/tx_worker/build" ]]; then
        rm -rf "$SCRIPT_DIR/tx_worker/build"
        log_success "Build directory cleaned"
    fi
    
    # Build with explicit DPDK tree to ensure correct paths
    log_info "Building with DPDK from: $DPDK_DIR"
    
    # Set PKG_CONFIG_PATH to prioritize /usr/local installation
    export PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
    
    # Verify pkg-config finds the correct DPDK
    local dpdk_version=$(PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" pkg-config --modversion libdpdk 2>/dev/null || echo "")
    if [[ -n "$dpdk_version" ]]; then
        log_info "Detected DPDK version: $dpdk_version"
        local dpdk_libdir=$(PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" pkg-config --variable=libdir libdpdk 2>/dev/null || echo "")
        if [[ "$dpdk_libdir" != "/usr/local/lib/x86_64-linux-gnu" ]] && [[ "$dpdk_libdir" != "/usr/local/lib" ]]; then
            log_warning "pkg-config reports libdir as: $dpdk_libdir (expected /usr/local/lib/x86_64-linux-gnu)"
            log_info "Forcing PKG_CONFIG_PATH to use /usr/local installation"
        fi
    fi
    
    # Build with explicit PKG_CONFIG_PATH
    if PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" \
       ./dpdk_tx_worker.sh --dpdk-tree "$DPDK_DIR" --rewrite-src; then
        log_success "tx_worker built successfully"
    else
        log_error "tx_worker build failed"
        log_info "Troubleshooting steps:"
        echo ""
        log_info "1. Check DPDK installation:"
        echo "   ls -la /usr/local/lib/x86_64-linux-gnu/librte_*.so* | head -5"
        echo ""
        log_info "2. Verify pkg-config finds correct DPDK:"
        echo "   PKG_CONFIG_PATH=/usr/local/lib/x86_64-linux-gnu/pkgconfig pkg-config --modversion libdpdk"
        echo "   PKG_CONFIG_PATH=/usr/local/lib/x86_64-linux-gnu/pkgconfig pkg-config --variable=libdir libdpdk"
        echo ""
        log_info "3. Manual build (clean and rebuild):"
        echo "   cd tx_worker"
        echo "   rm -rf build"
        echo "   PKG_CONFIG_PATH=/usr/local/lib/x86_64-linux-gnu/pkgconfig meson setup build"
        echo "   PKG_CONFIG_PATH=/usr/local/lib/x86_64-linux-gnu/pkgconfig ninja -C build"
        echo ""
        exit 1
    fi
    
    # Step 6.1: Verify library compatibility and configure library paths
    log_info "Verifying library compatibility..."
    
    local tx_worker_bin="$SCRIPT_DIR/tx_worker/build/tx_worker"
    if [[ ! -f "$tx_worker_bin" ]]; then
        log_error "tx_worker binary not found at $tx_worker_bin"
        exit 1
    fi
    
    # Check for missing libraries
    local missing_libs=$(LD_LIBRARY_PATH="/usr/local/lib/x86_64-linux-gnu:/usr/local/lib:/usr/lib/x86_64-linux-gnu:/usr/lib" \
                         ldd "$tx_worker_bin" 2>&1 | grep "not found" || true)
    
    if [[ -n "$missing_libs" ]]; then
        log_warning "Missing libraries detected:"
        echo "$missing_libs"
        echo ""
        
        # Check for version mismatch (e.g., .so.22 vs .so.26)
        local version_mismatch=$(echo "$missing_libs" | grep -oE "librte_[^.]*\.so\.[0-9]+" | head -1 || true)
        if [[ -n "$version_mismatch" ]]; then
            local required_version=$(echo "$version_mismatch" | grep -oE "[0-9]+$")
            log_warning "Version mismatch detected: binary requires DPDK $required_version"
            
            # Find installed DPDK version
            local installed_libs=$(find /usr/local/lib/x86_64-linux-gnu -name "librte_eal.so.*" 2>/dev/null | head -1 || true)
            if [[ -n "$installed_libs" ]]; then
                local installed_version=$(echo "$installed_libs" | grep -oE "[0-9]+$" || echo "unknown")
                log_warning "Installed DPDK version: $installed_version"
                
                if [[ "$required_version" != "$installed_version" ]]; then
                    log_error "Version mismatch: binary requires DPDK $required_version but $installed_version is installed"
                    log_info "Rebuilding tx_worker against installed DPDK version..."
                    
                    # Clean and rebuild
                    if [[ -d "$SCRIPT_DIR/tx_worker/build" ]]; then
                        rm -rf "$SCRIPT_DIR/tx_worker/build"
                    fi
                    
                    if PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" \
                       ./dpdk_tx_worker.sh --dpdk-tree "$DPDK_DIR" --rewrite-src; then
                        log_success "tx_worker rebuilt successfully"
                    else
                        log_error "Failed to rebuild tx_worker"
                        exit 1
                    fi
                fi
            fi
        fi
        
        # Update ldconfig to ensure libraries are found
        log_info "Updating library cache (ldconfig)..."
        if ldconfig 2>/dev/null; then
            log_success "Library cache updated"
        else
            log_warning "ldconfig failed (may need root privileges)"
        fi
    else
        log_success "All required libraries found"
    fi
    
    # Verify binary can find libraries with proper LD_LIBRARY_PATH
    log_info "Verifying library path configuration..."
    local test_ld_path="/usr/local/lib/x86_64-linux-gnu:/usr/local/lib:/usr/lib/x86_64-linux-gnu:/usr/lib"
    local test_result=$(LD_LIBRARY_PATH="$test_ld_path" ldd "$tx_worker_bin" 2>&1 | grep -c "not found" || echo "0")
    
    if [[ "$test_result" == "0" ]]; then
        log_success "Library path configuration verified"
    else
        log_warning "Some libraries still not found even with LD_LIBRARY_PATH set"
        log_info "The application will set LD_LIBRARY_PATH automatically at runtime"
    fi
    
    # Create a note about LD_LIBRARY_PATH for systemd service
    log_info "Creating library path configuration note..."
    cat > "$SCRIPT_DIR/.ld_library_path_note.txt" <<EOF
# DPDK Library Path Configuration
# 
# The tx_worker binary requires DPDK libraries from:
#   /usr/local/lib/x86_64-linux-gnu
#   /usr/local/lib
#
# The application (utils/dpdk_tx_worker.py) automatically sets LD_LIBRARY_PATH
# at runtime to include these paths.
#
# If running tx_worker manually, set:
#   export LD_LIBRARY_PATH="/usr/local/lib/x86_64-linux-gnu:/usr/local/lib:/usr/lib/x86_64-linux-gnu:/usr/lib"
#
# Or update system-wide library cache:
#   echo "/usr/local/lib/x86_64-linux-gnu" > /etc/ld.so.conf.d/dpdk.conf
#   ldconfig
EOF
    log_success "Library path configuration documented"
}

# Step 7: Configure hugepages
step_configure_hugepages() {
    log_step "Step 7: Configuring Hugepages"
    
    local current=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
    
    if [[ $current -gt 0 ]]; then
        log_success "Hugepages already configured: $current pages"
        if [[ $(prompt_yes_no "Reconfigure hugepages?") != "y" ]]; then
            return 0
        fi
    fi
    
    log_info "DPDK requires hugepages to function"
    log_info "Recommended: 1024 pages (2GB) for normal use"
    log_info "For high-speed traffic: 2048+ pages (4GB+)"
    
    # Prompt for hugepages with validation
    local pages=""
    while [[ -z "$pages" ]] || ! [[ "$pages" =~ ^[0-9]+$ ]]; do
        local input=$(prompt_input "Enter number of 2MB hugepages" "1024")
        input=${input:-1024}
        
        # Validate input is numeric
        if [[ "$input" =~ ^[0-9]+$ ]]; then
            pages="$input"
        else
            log_error "Invalid input: '$input'. Please enter a number (e.g., 1024)"
            log_info "If you meant to answer 'yes', please enter a number instead (default: 1024)"
        fi
    done
    
    log_info "Setting $pages hugepages..."
    echo "$pages" > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
    
    # Verify
    local new_total=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
    if [[ $new_total -ge $pages ]]; then
        log_success "Hugepages configured: $new_total pages"
    else
        log_warning "Requested $pages pages but got $new_total (may need more memory)"
    fi
}

# Step 8: NIC binding (optional)
step_nic_binding() {
    log_step "Step 8: NIC Binding (Optional)"
    
    log_info "Checking available network interfaces..."
    
    if ! command -v dpdk-devbind.py >/dev/null 2>&1; then
        log_warning "dpdk-devbind.py not found, skipping NIC binding"
        return 0
    fi
    
    # Parse and display NIC information in a user-friendly format
    echo ""
    log_info "Available Network Interfaces:"
    echo ""
    printf "%-15s %-20s %-15s %-12s %-10s %s\n" "PCI Address" "Interface" "Driver" "NUMA" "Type" "Status"
    echo "────────────────────────────────────────────────────────────────────────────────────────────"
    
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
            local type_label=$(get_nic_vendor_name "$vendor_id")
            
            # Shorten description if too long
            if [[ ${#desc} -gt 50 ]]; then
                desc="${desc:0:47}..."
            fi
            
            printf "%-15s %-20s %-15s %-12s %-10s %s\n" "$pci" "${iface:-N/A}" "${driver:-N/A}" "Node $numa" "$type_label" "$active"
            
            # Store for later use
            nic_list+=("$pci|$iface|$driver|$nic_type|$desc")
        fi
    done < <(dpdk-devbind.py --status 2>/dev/null | grep -A 100 "Network devices" | grep -E "^[0-9a-f]{4}:" || true)
    
    echo ""
    
    if [[ ${#nic_list[@]} -eq 0 ]]; then
        log_warning "No network interfaces found"
        return 0
    fi
    
    log_info "NIC to Interface Mapping Summary:"
    echo ""
    for nic_info in "${nic_list[@]}"; do
        IFS='|' read -r pci iface driver nic_type desc <<< "$nic_info"
        local vendor_id=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
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
    
    if [[ $(prompt_yes_no "Bind a NIC to DPDK?") != "y" ]]; then
        log_info "Skipping NIC binding"
        return 0
    fi
    
    echo ""
    log_info "Available NICs for binding:"
    local index=1
    local pci_choices=()
    for nic_info in "${nic_list[@]}"; do
        IFS='|' read -r pci iface driver nic_type desc <<< "$nic_info"
        local vendor_id=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
        local vendor_name=$(get_nic_vendor_name "$vendor_id")
        
        if [[ "$nic_type" == "mellanox" ]]; then
            echo "  [$index] $pci → $iface ($driver) [$vendor_name - Skip binding]"
        else
            echo "  [$index] $pci → $iface ($driver) [$vendor_name]"
            pci_choices+=("$pci")
        fi
        ((index++))
    done
    echo "  [0] Enter PCI address manually"
    echo ""
    
    local choice=$(prompt_input "Select NIC number or enter PCI address" "")
    
    local pci=""
    if [[ "$choice" =~ ^[0-9]+$ ]] && [[ $choice -gt 0 ]] && [[ $choice -le ${#nic_list[@]} ]]; then
        local selected_info="${nic_list[$((choice-1))]}"
        IFS='|' read -r pci iface driver nic_type desc <<< "$selected_info"
        
        if [[ "$nic_type" == "mellanox" ]]; then
            log_warning "Mellanox NIC selected - DO NOT bind to vfio-pci"
            log_info "Mellanox mlx5 PMD works with kernel driver (mlx5_core)"
            log_info "No binding needed for Mellanox NICs"
            return 0
        fi
    elif [[ "$choice" == "0" ]] || [[ -z "$choice" ]]; then
        pci=$(prompt_input "Enter PCI address (e.g., 0000:8a:00.0)" "")
    else
        # Assume it's a PCI address
        pci="$choice"
    fi
    
    if [[ -z "$pci" ]]; then
        log_warning "No PCI address provided, skipping"
        return 0
    fi
    
    # Detect NIC type
    local nic_type=$(detect_nic_type "$pci")
    
    # Get vendor info
    local vendor_id=$(lspci -n -s "$pci" 2>/dev/null | awk '{print $3}' | cut -d: -f1)
    local vendor_name=$(get_nic_vendor_name "$vendor_id")
    local recommendation=$(get_binding_recommendation "$nic_type")
    
    if [[ "$nic_type" == "mellanox" ]]; then
        log_warning "$vendor_name NIC detected - DO NOT bind to vfio-pci"
        log_info "$recommendation"
        log_info "No binding needed for Mellanox/NVIDIA NICs"
        return 0
    fi
    
    log_info "Binding $pci ($vendor_name) to DPDK..."
    log_info "Recommendation: $recommendation"
    
    # Use dpdk_bind.sh for binding (if available)
    if [[ -f "$SCRIPT_DIR/dpdk_bind.sh" ]]; then
        if ./dpdk_bind.sh bind "$pci"; then
            log_success "NIC bound successfully"
        else
            log_warning "Binding failed or device already bound"
            if [[ $(prompt_yes_no "Try with --force?") == "y" ]]; then
                ./dpdk_bind.sh bind "$pci" --force || true
            fi
        fi
    else
        log_warning "dpdk_bind.sh not found, using fallback method"
        # Fallback: use dpdk-devbind.py directly
        if command -v dpdk-devbind.py >/dev/null 2>&1; then
            dpdk-devbind.py --bind=vfio-pci "$pci" || {
                log_error "Binding failed"
                return 1
            }
            log_success "NIC bound successfully"
        else
            log_error "dpdk-devbind.py not found"
            return 1
        fi
    fi
}

# Step 9: Configure library paths
step_configure_library_paths() {
    log_step "Step 9: Configuring Library Paths"
    
    log_info "Ensuring DPDK libraries are accessible..."
    
    # Check if /etc/ld.so.conf.d/dpdk.conf exists
    local ldconf_file="/etc/ld.so.conf.d/dpdk.conf"
    local dpdk_lib_path="/usr/local/lib/x86_64-linux-gnu"
    
    if [[ -d "$dpdk_lib_path" ]] && ls "$dpdk_lib_path"/librte_*.so* >/dev/null 2>&1; then
        log_info "DPDK libraries found in: $dpdk_lib_path"
        
        # Check if already configured
        if [[ -f "$ldconf_file" ]] && grep -q "$dpdk_lib_path" "$ldconf_file" 2>/dev/null; then
            log_success "Library path already configured in $ldconf_file"
        else
            log_info "Adding DPDK library path to system configuration..."
            
            if [[ $(prompt_yes_no "Update system library cache? (recommended)") == "y" ]]; then
                echo "$dpdk_lib_path" > "$ldconf_file"
                if ldconfig; then
                    log_success "Library cache updated successfully"
                    log_info "DPDK libraries are now available system-wide"
                else
                    log_warning "ldconfig failed (may need to run manually as root)"
                    log_info "Run manually: sudo ldconfig"
                fi
            else
                log_info "Skipping system library cache update"
                log_info "The application will set LD_LIBRARY_PATH automatically at runtime"
            fi
        fi
    else
        log_warning "DPDK libraries not found in $dpdk_lib_path"
        log_info "Make sure DPDK was installed correctly"
    fi
    
    # Verify tx_worker can find libraries
    local tx_worker_bin="$SCRIPT_DIR/tx_worker/build/tx_worker"
    if [[ -f "$tx_worker_bin" ]]; then
        log_info "Verifying tx_worker library dependencies..."
        local missing=$(LD_LIBRARY_PATH="/usr/local/lib/x86_64-linux-gnu:/usr/local/lib:/usr/lib/x86_64-linux-gnu:/usr/lib" \
                        ldd "$tx_worker_bin" 2>&1 | grep "not found" || true)
        
        if [[ -z "$missing" ]]; then
            log_success "tx_worker library dependencies verified"
        else
            log_warning "Some libraries still missing:"
            echo "$missing"
            log_info "The application will set LD_LIBRARY_PATH at runtime to resolve this"
        fi
    fi
}

# Step 10: Verification
step_verification() {
    log_step "Step 10: Verification"
    
    log_info "Running verification checks..."
    echo ""
    
    if bash verify_dpdk.sh; then
        log_success "Verification completed"
    else
        log_warning "Some verification checks failed (see output above)"
    fi
}

# Step 11: Summary and next steps
step_summary() {
    log_step "Installation Complete!"
    
    echo ""
    log_success "DPDK installation completed successfully"
    echo ""
    
    echo "Next steps:"
    echo "  1. Verify installation:"
    echo "     cd /opt/OSTG/resources/dpdk"
    echo "     sudo bash verify_dpdk.sh"
    echo ""
    echo "  2. Library paths are configured:"
    echo "     - DPDK libraries are in: /usr/local/lib/x86_64-linux-gnu"
    echo "     - The application automatically sets LD_LIBRARY_PATH at runtime"
    echo "     - If needed, library cache was updated with: sudo ldconfig"
    echo ""
    echo "  3. Bind NICs to DPDK (if not done):"
    echo "     sudo ./dpdk_bind.sh bind <PCI_ADDRESS>"
    echo "     sudo ./dpdk_bind.sh status  # Check status"
    echo ""
    echo "  4. Unbind NICs (return to kernel driver):"
    echo "     sudo ./dpdk_bind.sh unbind <PCI_ADDRESS>"
    echo ""
    echo "  5. Enable DPDK in UI when creating streams"
    echo ""
    echo "Note: If you encounter 'command not found' (exit code 127) errors:"
    echo "  - The script automatically rebuilds tx_worker if library versions mismatch"
    echo "  - Library paths are configured automatically"
    echo "  - Check .ld_library_path_note.txt for manual configuration if needed"
    echo ""
    
    log_info "For more information, see:"
    echo "  - README_ALL/DPDK_FIRST_TIME_INSTALLATION.md"
    echo "  - README_ALL/DPDK_SCRIPTS_USAGE_GUIDE.md"
    echo ""
}

# Main installation flow
main() {
    clear
    echo -e "${BLUE}"
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║         DPDK Installation Wizard                          ║"
    echo "║         User-Friendly Installation Script                 ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --auto)
                AUTO_MODE=1
                shift
                ;;
            --skip-build)
                SKIP_BUILD=1
                shift
                ;;
            --dpdk-dir)
                DPDK_DIR="$2"
                shift 2
                ;;
            --help|-h)
                echo "Usage: $0 [options]"
                echo ""
                echo "Options:"
                echo "  --auto              Run in non-interactive mode (use defaults)"
                echo "  --skip-build        Skip DPDK build step"
                echo "  --dpdk-dir DIR      Specify DPDK source directory"
                echo "  --help, -h          Show this help"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
    
    # Run installation steps
    step_preflight
    step_detect_dpdk_source
    step_install_dependencies
    step_build_dpdk
    step_build_tx_worker
    step_configure_hugepages
    step_nic_binding
    step_configure_library_paths
    step_verification
    step_summary
}

# Run main function
main "$@"

