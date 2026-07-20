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
# v0.5.21: HOME may be unset when the script is spawned by the
# Flask server's /api/admin/install_dpdk endpoint — systemd
# services don't inherit a user shell environment. Combined with
# `set -u` at line 9, "$HOME" alone dies with "HOME: unbound
# variable". Use ${HOME:-/root} so the reference is safe even
# under strict mode.
# Also: the old default was "$HOME/SURAJ/dpdk" — a stale dev path
# from the original developer's machine. Replaced with /opt/dpdk-build
# which is a sane system-wide location and matches the OPT-prefix
# convention this project uses (/opt/netgen-server, /opt/OSTG).
: "${HOME:=/root}"
DPDK_DIR="${DPDK_DIR:-/opt/dpdk-build}"
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

# v0.3.11: distro support check. The package-install step uses apt
# exclusively (libdpdk-dev / libibverbs-dev / etc) and a wrong-distro
# run fails cryptically several steps deep with 'apt-get: command
# not found'. Detect upfront and either succeed (Ubuntu / Debian)
# or exit with a clear actionable message (RHEL / CentOS / Fedora /
# Alpine / Arch). RHEL-family support would need a parallel
# package-name map; out of scope for now.
check_supported_distro() {
    local id="" id_like=""
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        id="${ID:-}"
        id_like="${ID_LIKE:-}"
    fi
    case "$id" in
        ubuntu|debian)
            log_info "Detected ${PRETTY_NAME:-$id} — supported."
            return 0
            ;;
        "")
            log_warning "Could not read /etc/os-release. Proceeding on "
            log_warning "assumption this is an apt-based distro."
            return 0
            ;;
    esac
    # Some downstream Debian derivatives set ID_LIKE=debian.
    case "$id_like" in
        *debian*|*ubuntu*)
            log_info "Detected ${PRETTY_NAME:-$id} (Debian-family) — "
            log_info "proceeding via apt."
            return 0
            ;;
    esac
    log_error "Unsupported distro: ${PRETTY_NAME:-$id}"
    log_error ""
    log_error "install_dpdk.sh currently supports Ubuntu and Debian "
    log_error "(and derivatives). RHEL / CentOS / Fedora / Alpine / "
    log_error "Arch use different package names and aren't auto-"
    log_error "installed yet."
    log_error ""
    log_error "Manual install path: install DPDK + dependencies via "
    log_error "your distro's package manager, then build tx_worker:"
    log_error "  cd resources/dpdk/tx_worker && meson setup build && "
    log_error "  ninja -C build"
    log_error ""
    log_error "Required packages (RHEL/Fedora equivalents):"
    log_error "  dpdk-devel  pkgconf-pkg-config  meson  ninja-build"
    log_error "  numactl-devel  elfutils-libelf-devel  libpcap-devel"
    log_error "  rdma-core-devel  libmlx5  kernel-devel"
    exit 1
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
    # v0.5.21: removed "$HOME/SURAJ/dpdk" (stale dev path); added
    # "/opt/dpdk-build" (the new default DPDK_DIR). Search order:
    # new default first → legacy locations from older installs.
    local possible_locations=(
        "/opt/dpdk-build"
        "/opt/dpdk"
        "/usr/src/dpdk"
        "${HOME}/dpdk"
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
        # v0.5.61 (audit M8): warn loudly when the installed
        # version differs from what THIS install_dpdk.sh ships
        # (currently 23.11.x). Pre-fix the prompt just said
        # "reinstall?" with no version context — operators
        # answered yes/no without knowing they were about to
        # upgrade across an ABI boundary that would orphan
        # tx_worker until the wheel rebuild reran.
        local target_major_minor="23.11"
        if [[ -n "$version" ]] \
            && ! echo "$version" | grep -q "^${target_major_minor}"; then
            log_warning "Installed DPDK version $version differs from this script's target ($target_major_minor)."
            log_warning "tx_worker linked against the old ABI WILL break until rebuilt."
            log_warning "If reinstalling, ensure Step 6 (tx_worker rebuild) runs afterward."
        fi
        return 0
    fi
    return 1
}

# Step 1: Pre-flight checks
step_preflight() {
    log_step "Step 1: Pre-flight Checks"
    
    check_root
    # v0.3.11: bail early on non-apt distros with a clear actionable
    # message instead of failing deep in step 4 with 'apt-get not
    # found'. RPM-family operators get pointed at the manual path.
    check_supported_distro

    log_info "Checking system requirements..."
    
    # Check disk space (need at least 2GB).
    # v0.5.61 (audit M7): pre-fix only checked `/`. On lab boxes
    # where /opt (DPDK_DIR) and /usr/local (install target) are
    # separate mounts, the check was meaningless. Walk the set of
    # paths the install actually writes to.
    local low_space=0
    for path in / "/usr/local" "${DPDK_DIR:-/opt/dpdk-build}" "${DPDK_DIR:-/opt}"; do
        if [[ ! -d "$path" ]]; then
            # parent mount is what matters when the dir doesn't
            # exist yet; df handles non-existent paths by walking
            # up to the nearest mount point.
            :
        fi
        local avail
        avail=$(df -BG "$path" 2>/dev/null | tail -1 | awk '{print $4}' | sed 's/G//')
        if [[ -n "$avail" ]] && [[ "$avail" =~ ^[0-9]+$ ]] && [[ $avail -lt 2 ]]; then
            log_warning "Low disk space on $path: ${avail}GB available (recommended: 2GB+)"
            low_space=1
        fi
    done
    if [[ $low_space -eq 1 ]]; then
        if [[ $(prompt_yes_no "Continue anyway?") != "y" ]]; then
            exit 1
        fi
    else
        log_success "Disk space OK on /, /usr/local, and DPDK build dir"
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
        # v0.5.21: prompt default is the new /opt/dpdk-build path,
        # not the legacy "$HOME/SURAJ/dpdk" dev path.
        DPDK_DIR=$(prompt_input "Enter DPDK source directory" "/opt/dpdk-build")
        
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

    if ! command -v git >/dev/null 2>&1; then
        log_error "git not found. Please install git or provide DPDK source manually"
        exit 1
    fi

    # Make sure the parent dir exists before we cd into it. Default
    # DPDK_DIR is /opt/dpdk-build (v0.5.21 — was $HOME/SURAJ/dpdk
    # which was a stale dev path); on a fresh box with no parent
    # directory, the old `cd "$target_dir"` failed with
    # "No such file or directory" and the install died at Step 3
    # before git ever ran — operators hit this from the /admin
    # Install DPDK button on every brand-new box.
    if [[ ! -d "$target_dir" ]]; then
        log_info "Parent directory $target_dir doesn't exist — creating it"
        if ! mkdir -p "$target_dir"; then
            log_error "Failed to create $target_dir (permission denied?)"
            exit 1
        fi
    fi

    cd "$target_dir" || {
        log_error "cd $target_dir failed after mkdir — filesystem issue"
        exit 1
    }

    # If a partial clone is already there (previous attempt that died
    # mid-download), git clone refuses with "destination path already
    # exists and is not an empty directory". Detect + clean.
    if [[ -d "$dpdk_name" ]]; then
        if [[ -d "$dpdk_name/.git" ]]; then
            log_info "$DPDK_DIR already exists as a git repo — reusing"
            cd "$dpdk_name"
            git checkout v23.11 2>/dev/null || git checkout main 2>/dev/null || true
            log_success "DPDK source ready (reused existing clone)"
            return 0
        else
            log_warning "$DPDK_DIR exists but isn't a git repo — removing for fresh clone"
            rm -rf "$dpdk_name"
        fi
    fi

    git clone https://dpdk.org/git/dpdk "$dpdk_name" || {
        log_error "Failed to clone DPDK from https://dpdk.org/git/dpdk"
        log_error "If your lab firewalls dpdk.org, pre-stage the source manually:"
        log_error "  git clone -b v23.11 --depth 1 https://dpdk.org/git/dpdk $DPDK_DIR"
        log_error "Or set DPDK_DIR=/path/to/existing/dpdk before running this script."
        exit 1
    }
    cd "$dpdk_name"
    git checkout v23.11 || git checkout main

    # v0.3.2: fail-early sanity check on the clone. Pre-v0.3.2 a
    # corrupted clone (no meson.build) only surfaced ~60 s later
    # during the build step as a cryptic meson error. Confirm the
    # tree looks like a DPDK source checkout RIGHT NOW so the
    # operator gets an actionable message at the source step.
    if [[ ! -f meson.build ]] || [[ ! -d lib ]]; then
        log_error "DPDK source tree looks incomplete (missing meson.build or lib/)"
        log_error "The git clone may have been interrupted. Delete $DPDK_DIR and re-run."
        exit 1
    fi
    log_success "DPDK cloned successfully"
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

    # v0.5.29: also probe the Python elftools module that DPDK 23.11
    # meson hard-requires at buildtools/meson.build:58. Diagnostic
    # only — the apt install runs unconditionally now, so the result
    # doesn't gate anything, but the log line tells the operator
    # whether the package is actually present.
    if python3 -c "import elftools" 2>/dev/null; then
        log_success "python3-pyelftools found (elftools module importable)"
    else
        log_warning "python3-pyelftools not found (elftools module missing)"
        missing=1
    fi

    return $missing
}

# Step 4: Install dependencies
step_install_dependencies() {
    log_step "Step 4: Installing Build Dependencies"

    # v0.5.29: ALWAYS run apt install — do NOT early-return based on
    # check_dpdk_dependencies. The check function only verifies 7
    # packages (meson, ninja, pkg-config, gcc, libnuma-dev,
    # libelf-dev, libpcap-dev) — the actual apt batch installs
    # 16+ packages including python3-pyelftools (v0.5.25 add),
    # libssl-dev / libjansson-dev / libbpf-dev / libxdp-dev /
    # libbsd-dev / zlib1g-dev / libfdt-dev / libarchive-dev
    # (v0.5.26 adds). Pre-fix, an operator who'd run Setup DPDK
    # in v0.5.13 or earlier (which only installed the 7 checked
    # packages) would have the check PASS post-upgrade-to-v0.5.28
    # — apt-install would be SKIPPED — and the meson setup at
    # Step 5 would fail with "missing python module: elftools"
    # because python3-pyelftools never got installed.
    #
    # Real failure on srv06 (Jun 8 2026): Make DPDK Ready in
    # v0.5.28 → Step 5 elftools error → infinite loop unless
    # operator manually apt-installs.
    #
    # The fix: drop the early-return. apt-get install is idempotent
    # for already-installed packages (it logs "X is already the
    # newest version" and moves on), costs ~5-10s on subsequent
    # runs. That's a tiny price for the operator-trust invariant:
    # "Make DPDK Ready installs every dep the script knows about,
    # every time, regardless of host state."
    #
    # check_dpdk_dependencies stays — its diagnostic logging is
    # useful — but it no longer GATES the apt install.
    check_dpdk_dependencies || true

    log_info "Installing/refreshing all DPDK build dependencies (idempotent)..."

    if [[ "$AUTO_MODE" != "1" ]]; then
        if [[ $(prompt_yes_no "Run apt install for DPDK build dependencies?") != "y" ]]; then
            log_warning "Skipping dependency installation"
            if ! check_dpdk_dependencies; then
                log_error "Required dependencies are missing"
                if [[ $(prompt_yes_no "Continue anyway? (may fail later)") != "y" ]]; then
                    exit 1
                fi
            fi
            return 0
        fi
    fi

    log_info "Running dependency installation..."
    
    # Install dependencies directly
    log_info "Updating package lists..."
    MAX_RETRIES=3
    RETRY_COUNT=0
    APT_UPDATE_SUCCESS=0
    
    # v0.5.55 (audit H6): use exit code, NOT string-match. Pre-fix
    # `apt-get update | grep -q "Reading package lists"` would:
    #   (a) FALSE-positive when apt prints that line on stdout then
    #       fails for an unrelated reason (DNS, repo signature 404,
    #       Hash mismatch) — we mark APT_UPDATE_SUCCESS=1 anyway.
    #   (b) FALSE-negative when apt succeeds but the output format
    #       changes — newer apt versions sometimes omit the literal
    #       string on fully-cached refreshes.
    # With `set -o pipefail` (enabled in this script's header), the
    # actual apt-get exit code propagates through the tail pipeline;
    # check $? directly.
    while [[ $RETRY_COUNT -lt $MAX_RETRIES ]]; do
        if apt-get update -y \
            -o APT::Sandbox::User=root \
            --option Acquire::http::Timeout=30 \
            --option Acquire::ftp::Timeout=30 2>&1 \
            | tail -20 | sed 's/^/[apt-update] /'; then
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
    # linux-headers-$(uname -r): vfio-pci is in-tree on stock Ubuntu / Debian
    # kernels, but cloud / minimal images (Azure, GCP, container-optimized)
    # frequently strip the modules and need the matching headers to either
    # rebuild vfio-pci or to compile out-of-tree NIC PMD modules at runtime.
    # Cheap to install (~80 MB) and silent no-op when already present.
    local kernel_headers="linux-headers-$(uname -r 2>/dev/null || echo generic)"
    # v0.5.61 (audit M6): the precise `linux-headers-X.Y.Z-N-generic`
    # package may NOT exist in apt on hosts running an older or
    # out-of-band kernel (HWE rolled forward, custom kernel,
    # snapshot rollback). If apt-cache can't find it, fall back
    # to the meta-package `linux-headers-generic` which always
    # tracks the latest stable kernel from the host's repo. Worst
    # case: vfio-pci is in-tree on the running kernel and the
    # fallback installs headers for a different kernel — the apt
    # install still succeeds and the operator can manually
    # `apt-get install linux-headers-$(uname -r)` later when the
    # repo catches up.
    if ! apt-cache show "$kernel_headers" >/dev/null 2>&1; then
        log_warning "$kernel_headers not in apt repos; falling back to linux-headers-generic"
        kernel_headers="linux-headers-generic"
    fi
    # v0.3.16: pass dpkg --force-confdef + --force-confold so that an
    # apt-get install never hangs on a CONFFILE prompt for a package
    # whose default config differs from the system's existing copy.
    # Without these flags, packages like containerd.io (whose
    # /etc/containerd/config.toml conflicts with whatever the host
    # had before) trigger an interactive dpkg prompt that EOFs in
    # any nohup-detached install context. The DEBIAN_FRONTEND env
    # var alone does NOT cover dpkg's own conffile prompt; the
    # Dpkg::Options::= flags are mandatory for it.
    #
    # DPDK 23.11 apt dependency catalog (current as of v0.5.27):
    #
    # ─── v0.5.27 SPLIT ───────────────────────────────────────────────
    # Operator request: "rdma install should be separate, it should
    # not be part of dpdk install". RDMA stack (libibverbs-dev,
    # rdma-core, perftest, libmlx5-dev) moved to install_rdma.sh —
    # Tools → RDMA → Setup RDMA in the GUI, or sudo install_rdma.sh
    # on CLI.
    #
    # If you intend to use DPDK with Mellanox NICs (drivers/net/mlx5),
    # run install_rdma.sh BEFORE this script so the mlx5 PMD picks
    # up libibverbs at meson configure time. On Intel / Broadcom /
    # virtio hosts no RDMA stack is needed at all.
    # ──────────────────────────────────────────────────────────────────
    #
    #   MANDATORY (meson setup fails without them):
    #     build-essential       — gcc/g++/make + C11 toolchain
    #     meson, ninja-build    — DPDK's build system
    #     pkg-config            — library discovery
    #     libnuma-dev           — EAL hard-requires NUMA topology
    #     python3-pyelftools    — meson buildtools/check-symbols.sh
    #                             (v0.5.25 — see meson.build:58)
    #     ${kernel_headers}     — kmod / kni / KNI module build
    #
    #   STRONGLY RECOMMENDED (driver compile errors without them):
    #     libelf-dev            — BPF library
    #     libpcap-dev           — pcap PMD + pdump
    #
    #   OPTIONAL — added v0.5.26 because `-Dexamples=all` (line ~592)
    #   transitively pulls in every example's deps, and DPDK 23.11
    #   enables telemetry by default:
    #     libssl-dev            — crypto PMDs (drivers/crypto/openssl)
    #                             + crypto examples (l2fwd-crypto,
    #                             ipsec-secgw)
    #     libjansson-dev        — telemetry JSON encoding (DPDK 23.11
    #                             default-enables telemetry)
    #     libbpf-dev            — BPF library + AF_XDP PMD
    #     libxdp-dev            — AF_XDP PMD (paired with libbpf)
    #     libbsd-dev            — BSD-isms (strlcpy/etc.) used by
    #                             some examples + lib/eal portability
    #     zlib1g-dev            — compression PMDs (compress/zlib)
    #     libfdt-dev            — Flattened Device Tree config
    #                             (mostly ARM, harmless on x86)
    #     libarchive-dev        — resource-pack tests + some examples
    #
    # All optional deps are silently NO-OP'd by meson if the
    # corresponding feature isn't requested — adding them costs
    # ~20-40 MB of apt download/install but eliminates the "Make
    # DPDK Ready" surface for downstream feature requests (someone
    # wanting AF_XDP, telemetry export, etc. doesn't need a re-roll).
    # v0.5.31: -o APT::Sandbox::User=root disables apt's
    # unprivileged-download sandbox. Required because the
    # netgen-server systemd unit has RestrictSUIDSGID=true (and/or
    # a seccomp filter blocking setgroups). When apt tries to drop
    # to the `_apt` user for downloads, setgroups() returns EPERM
    # and apt fails the entire batch with:
    #
    #   E: setgroups 65534 failed - Operation not permitted
    #   E: Could not open file .../python3-pyelftools_0.30-1_all.deb
    #      - open (13: Permission denied)
    #
    # Real failure on srv06 (Jun 8 2026, post v0.5.29): the apt
    # batch failed silently → python3-pyelftools never installed →
    # Step 5 meson errored out THREE upgrades in a row.
    #
    # Telling apt to stay root for downloads sidesteps it cleanly.
    # Safe: netgen-server.service runs as root anyway.
    # v0.5.82: include lldpd in the apt-deps list so existing
    # netgen servers running "Make DPDK Ready" get LLDP installed
    # too (fresh installs get it via netgen-install's _setup_lldpd
    # step). Tiny package, no real cost; admin console iface table
    # surfaces neighbors as soon as lldpcli is on PATH.
    local deps_install_cmd="DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -o APT::Sandbox::User=root --option Acquire::http::Timeout=30 --option Acquire::ftp::Timeout=30 build-essential meson ninja-build pkg-config libnuma-dev libelf-dev libpcap-dev python3-pyelftools libssl-dev libjansson-dev libbpf-dev libxdp-dev libbsd-dev zlib1g-dev libfdt-dev libarchive-dev lldpd ${kernel_headers}"

    # v0.3.2: tighten umask around the temp-log tee so the file is
    # 0600 (owner-only) instead of the default 0644 (world-readable).
    # apt-get output isn't high-sensitivity, but there's no reason
    # to leak package names + error fragments to every user on the
    # host. The (subshell) keeps the umask change scoped — outer
    # process umask is untouched after the block exits.
    # Try to install dependencies
    if (umask 077 && eval "$deps_install_cmd" 2>&1 | tee /tmp/dpdk_deps_install.log); then
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

    # v0.5.30: HARD GATE on python3-pyelftools.
    #
    # srv06 trap (Jun 8 2026): even with v0.5.29's always-run apt
    # install, the `if ... else ...` block above falls into a
    # "Continue anyway?" prompt on apt failure. In AUTO_MODE that
    # prompt returns "y" automatically → script continues → Step 5
    # meson dies with the famous "missing python module: elftools"
    # and the operator has no signal that apt is the actual cause.
    #
    # Hard gate the elftools probe AFTER apt runs:
    #   - If the module is importable → proceed (apt did its job)
    #   - If still missing → exit 1 with a precise diagnostic that
    #     points at /tmp/dpdk_deps_install.log (PRESERVED, not rm'd)
    #     so the operator can see what apt actually did.
    #
    # This converts the failure mode from "silent continuation
    # → confusing meson error 100 lines later" to "loud,
    # actionable error in Step 4 with the apt log right there."
    if ! python3 -c "import elftools" 2>/dev/null; then
        log_error ""
        log_error "════════════════════════════════════════════════════════════"
        log_error "  CRITICAL: python3-pyelftools is NOT installed."
        log_error "  DPDK 23.11 meson setup hard-requires the `elftools`"
        log_error "  Python module. Step 5 will fail with:"
        log_error "    buildtools/meson.build:58:8: missing python module: elftools"
        log_error ""
        log_error "  Most likely cause: apt-install failed for pyelftools."
        log_error "  See the apt install log at:"
        log_error "    /tmp/dpdk_deps_install.log"
        log_error ""
        log_error "  Manual recovery:"
        log_error "    apt-get update && apt-get install -y python3-pyelftools"
        log_error ""
        log_error "  Then re-run Make DPDK Ready."
        log_error "════════════════════════════════════════════════════════════"
        log_error ""
        # Print the last 30 lines of the apt log inline so the GUI
        # log tail (also last 30 lines) surfaces them automatically.
        if [[ -r /tmp/dpdk_deps_install.log ]]; then
            log_error "Last 30 lines of apt install log:"
            tail -30 /tmp/dpdk_deps_install.log | while IFS= read -r line; do
                log_error "  | $line"
            done
        fi
        # Preserve the log — do NOT rm it.
        exit 1
    fi
    log_success "python3-pyelftools verified (elftools module importable)"

    rm -f /tmp/dpdk_deps_install.log

    # v0.5.27: libmlx5-dev (Mellanox PMD dev headers) and the full
    # RDMA stack (libibverbs-dev, rdma-core, perftest) moved to
    # install_rdma.sh. If you intend to drive Mellanox NICs with
    # DPDK's mlx5 PMD, run install_rdma.sh BEFORE this script so
    # the mlx5 PMD picks up libibverbs at meson configure time.
    #
    # Detect Mellanox hardware + warn if RDMA stack is missing —
    # this is the most common surprise that DPDK Mellanox-PMD
    # users hit.
    if command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qi mellanox; then
        if ! ldconfig -p 2>/dev/null | grep -q libibverbs; then
            log_warning ""
            log_warning "Mellanox NIC detected but libibverbs is NOT installed."
            log_warning "DPDK's mlx5 PMD will be silently skipped at meson"
            log_warning "configure time. To drive Mellanox NICs from DPDK:"
            log_warning ""
            log_warning "  1. Run install_rdma.sh first (sudo /opt/netgen-server/share/netgen/resources/dpdk/install_rdma.sh)"
            log_warning "  2. Or use the GUI: Tools → RDMA → Setup RDMA"
            log_warning "  3. Then re-run this script — mlx5 PMD will compile."
            log_warning ""
        else
            log_info "Mellanox NIC + libibverbs detected — mlx5 PMD will compile."
        fi
    fi
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
    
    # Drivers we always disable on x86_64:
    #   net/mana         — Microsoft Azure NIC PMD; requires IBVERBS_PRIVATE_34
    #                      symbols which Ubuntu 22.04's libibverbs doesn't ship,
    #                      so the link step fails with "undefined reference to
    #                      ibv_cmd_query_*". No real Azure mana NIC = nothing
    #                      lost by skipping.
    #   NXP fslmc / DPAA — DPAA2/QorIQ ARM SoC bus + drivers. Build fine on ARM
    #                      but on x86_64 pmdinfogen.py crashes parsing the
    #                      generated .o files (DPDK 23.11 + pyelftools 0.27 on
    #                      Ubuntu 22.04). Operator-reported on san-ft-ai-srv01
    #                      2026-07-19: ninja step 720/3719 failed at
    #                      `meson runpython pmdinfogen.py elf libtmp_rte_bus_fslmc.a.p/*.o`
    #                      with `subprocess exit 2 / Unhandled python exception
    #                      This is a Meson bug and should be reported!`.
    #                      Disable at meson configure time — these drivers
    #                      target NXP QorIQ ARM boards, useless on x86 anyway.
    #                      Full family (bus + net + mempool + event + crypto +
    #                      raw) all depend on bus/fslmc or bus/dpaa; disabling
    #                      those two would work but explicit is safer against
    #                      meson dependency-resolution changes.
    local meson_disable="net/mana"
    meson_disable+=",bus/fslmc,bus/dpaa"
    meson_disable+=",net/dpaa2,net/dpaa"
    meson_disable+=",mempool/dpaa2,mempool/dpaa"
    meson_disable+=",event/dpaa2,event/dpaa"
    meson_disable+=",crypto/dpaa2_sec,crypto/dpaa_sec"
    meson_disable+=",raw/dpaa2_qdma,raw/dpaa2_cmdif"
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

    # Step 6.2: Expose tx_worker at the paths the server's admin probe checks.
    #
    # install_dpdk.sh resolves SCRIPT_DIR from $BASH_SOURCE — wherever it was
    # invoked from. Operators often run it directly from a git checkout
    # (e.g. /root/netgen/resources/dpdk/install_dpdk.sh), so the binary lands
    # under /root/netgen/.../tx_worker/build/tx_worker. Meanwhile the
    # server's /api/admin/health probe (run_tgen_server.py ~L12960) only
    # checks /opt/netgen/.../, /opt/OSTG/.../, and /usr/local/bin/tx_worker.
    # Result: admin portal reports "tx_worker binary Not built" even though
    # the binary exists and works. Bridge the gap by symlinking into the
    # canonical roots and dropping a copy on PATH.
    local _txw="$SCRIPT_DIR/tx_worker/build/tx_worker"
    if [[ -x "$_txw" ]]; then
        for canon in /opt/netgen/resources/dpdk/tx_worker/build \
                     /opt/OSTG/resources/dpdk/tx_worker/build; do
            if [[ -d "$(dirname "$canon")" ]]; then
                mkdir -p "$canon"
                local _target="$canon/tx_worker"
                # CRITICAL: skip when source and target are the SAME
                # file. When /admin's Install DPDK button runs this
                # script, SCRIPT_DIR=/opt/netgen/resources/dpdk — so
                # _txw IS already at the canonical path, and
                # `ln -sfn X X` errors with "are the same file" under
                # `set -euo pipefail`. The whole install aborts here,
                # and the operator never gets past Step 6 to Step 7
                # (Configure Hugepages). That's exactly the failure
                # mode on svl-hp-ai-srv02: install completed enough
                # to build tx_worker but never allocated hugepages,
                # so subsequent DPDK streams started + exited in 1 s.
                #
                # Bash's `-ef` tests "same inode + device" — handles
                # both the direct-same-path case and the
                # symlink-already-points-here case. Returns false
                # when target doesn't exist yet (first install on a
                # fresh box), which is what we want — fall through to
                # `ln` and create it.
                if [[ "$_txw" -ef "$_target" ]]; then
                    log_info "tx_worker already at $_target — no symlink needed"
                    continue
                fi
                # Wrap in || true so any other ln failure (permission
                # denied on a read-only mount, etc.) doesn't abort the
                # install at Step 6. Log it and move on; hugepages +
                # subsequent steps are more important than this
                # convenience symlink.
                if ln -sfn "$_txw" "$_target" 2>/dev/null; then
                    log_success "Linked tx_worker → $_target"
                else
                    log_warning "Could not link $_target (skipping — non-fatal)"
                fi
            fi
        done
        # Stash a copy on PATH so ad-hoc invocations (`tx_worker --help`) work
        # without LD_LIBRARY_PATH gymnastics — the wrapper in
        # utils/dpdk_tx_worker.py handles env setup when launched via the API.
        if install -m755 "$_txw" /usr/local/bin/tx_worker 2>/dev/null; then
            log_success "Installed tx_worker to /usr/local/bin/tx_worker"
        fi
    fi
}

# Step 6.5 (v0.5.105): Build rx_worker — symmetric companion to
# tx_worker, line-rate accurate RX counter without going through
# the kernel netdev. Built the simple way (direct meson) instead
# of using dpdk_tx_worker.sh's ABI-detection ceremony — rx_worker
# is brand new so there's no legacy ABI to navigate.
#
# Failure is non-fatal: rx_worker enables the DPDK RX engine; if
# the build fails, the Scapy RX engine still works. Operators
# can re-run install_dpdk.sh to retry.
step_build_rx_worker() {
    log_step "Step 6.5: Building rx_worker Application"

    local rx_src="$SCRIPT_DIR/rx_worker"
    if [[ ! -d "$rx_src" || ! -f "$rx_src/rx_worker.c" ]]; then
        log_warning "rx_worker sources not found at $rx_src — skipping."
        log_info "  (Pre-v0.5.105 layouts don't ship rx_worker; that's expected.)"
        return 0
    fi

    log_info "Building rx_worker from $rx_src..."
    export PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

    if [[ -d "$rx_src/build" ]]; then
        rm -rf "$rx_src/build"
    fi

    if ! meson setup "$rx_src/build" "$rx_src" 2>&1; then
        log_warning "rx_worker meson setup failed (DPDK RX feature unavailable; "
        log_warning "Scapy RX still works). Re-run install_dpdk.sh to retry."
        return 0
    fi
    if ! meson compile -C "$rx_src/build" 2>&1; then
        log_warning "rx_worker compile failed (DPDK RX feature unavailable)."
        return 0
    fi

    local rx_bin="$rx_src/build/rx_worker"
    if [[ ! -x "$rx_bin" ]]; then
        log_warning "rx_worker build reported success but $rx_bin missing."
        return 0
    fi

    # Install to /usr/local/bin/ — canonical resolver path used by
    # utils/dpdk_rx_worker.py:_resolve_rx_worker_bin (same pattern as
    # tx_worker).
    if install -m755 "$rx_bin" /usr/local/bin/rx_worker 2>/dev/null; then
        log_success "Installed rx_worker to /usr/local/bin/rx_worker"
    else
        log_warning "install to /usr/local/bin/ failed (non-fatal); the binary"
        log_warning "is at $rx_bin and can be copied manually."
    fi
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

    # Persist across reboots. Writing to /sys is volatile — reboot wipes
    # hugepages and the next netgen-server start fails to allocate any
    # mbufs. Drop a sysctl snippet so systemd-sysctl restores the
    # allocation on boot.
    local sysctl_conf="/etc/sysctl.d/99-netgen-hugepages.conf"
    log_info "Persisting hugepages config to $sysctl_conf ..."
    cat > "$sysctl_conf" <<EOF
# Written by Netgen install_dpdk.sh — DPDK 2 MB hugepages.
# Increase / decrease and run \`sysctl --system\` to apply without reboot.
vm.nr_hugepages = $pages
EOF
    log_success "Hugepages persisted: $sysctl_conf"

    # v0.5.51: apply the sysctl now so the running kernel reflects
    # the new value WITHOUT needing reboot. Pre-fix we only wrote
    # the file; the running kernel kept the old nr_hugepages value
    # until next reboot.
    if command -v sysctl >/dev/null 2>&1; then
        if sysctl --system >/dev/null 2>&1; then
            log_success "sysctl --system reloaded; running kernel updated"
        else
            log_warning "sysctl --system failed; reboot to apply"
            netgen_mark_reboot_required "sysctl --system reload failed"
        fi
    fi
}

# v0.5.51: track when the operator MUST reboot for changes to take
# effect. step_summary surfaces the requirement; also touches
# /var/run/netgen-reboot-required so /api/dpdk/status can pick it
# up without re-scanning the install log.
REBOOT_REQUIRED=0
REBOOT_REASONS=()

netgen_mark_reboot_required() {
    local reason="$1"
    REBOOT_REQUIRED=1
    REBOOT_REASONS+=("$reason")
    local marker=""
    if [[ -d /run && -w /run ]]; then
        marker="/run/netgen-reboot-required"
    elif [[ -d /var/run && -w /var/run ]]; then
        marker="/var/run/netgen-reboot-required"
    fi
    if [[ -n "$marker" ]]; then
        {
            echo "# Marker written by netgen install_dpdk.sh"
            echo "# Reboot is required for these changes to take effect:"
            for r in "${REBOOT_REASONS[@]}"; do
                echo "#   - $r"
            done
        } > "$marker" 2>/dev/null || true
    fi
}

# v0.5.51: enable IOMMU for vfio-pci by editing /etc/default/grub.
# Pre-fix install_dpdk.sh bound NICs to vfio-pci (step 8) on hosts
# where IOMMU was OFF, the kernel refused to attach vfio-pci to
# any device ("No IOMMU support" or noiommu fallback), and DPDK
# apps failed 1 second after "bind success". This step adds the
# right kernel cmdline for the detected CPU vendor and surfaces a
# reboot requirement when changes are made.
step_configure_iommu() {
    log_step "Step 7b: Configuring IOMMU (kernel cmdline)"

    local vendor=""
    if [[ -r /proc/cpuinfo ]]; then
        if grep -qi "GenuineIntel" /proc/cpuinfo; then
            vendor="intel"
        elif grep -qi "AuthenticAMD" /proc/cpuinfo; then
            vendor="amd"
        fi
    fi
    if [[ -z "$vendor" ]]; then
        log_warning "Could not detect CPU vendor; skipping IOMMU config"
        return 0
    fi
    log_info "CPU vendor: $vendor"

    local needed_param=""
    case "$vendor" in
        intel) needed_param="intel_iommu=on" ;;
        amd)   needed_param="amd_iommu=on"   ;;
    esac

    # Already-on check via /proc/cmdline (live kernel).
    local live_cmdline=""
    [[ -r /proc/cmdline ]] && live_cmdline=$(cat /proc/cmdline 2>/dev/null)
    if echo "$live_cmdline" | grep -qE "(^| )${needed_param}( |$)" \
       && echo "$live_cmdline" | grep -qE "(^| )iommu=pt( |$)"; then
        log_success "IOMMU already active in running kernel"
        return 0
    fi

    local grub_file="/etc/default/grub"
    if [[ ! -w "$grub_file" ]]; then
        log_warning "$grub_file not writable; skipping IOMMU config"
        log_warning "Add manually: GRUB_CMDLINE_LINUX_DEFAULT=\"... $needed_param iommu=pt\""
        return 0
    fi

    # Anchored idempotency check against the file — covers the
    # case where GRUB has the params but kernel hasn't been
    # rebooted yet.
    if grep -qE "GRUB_CMDLINE_LINUX[A-Z_]*=.*${needed_param}" "$grub_file" \
       && grep -qE "GRUB_CMDLINE_LINUX[A-Z_]*=.*iommu=pt" "$grub_file"; then
        log_warning "GRUB already has IOMMU params; reboot required to activate"
        netgen_mark_reboot_required "IOMMU enabled in GRUB but not yet active"
        return 0
    fi

    # Timestamped backup before edit. Damage to /etc/default/grub
    # would leave the box unbootable; the backup gives the
    # operator a known-good version to restore.
    local backup="${grub_file}.netgen-bak.$(date +%s)"
    if cp "$grub_file" "$backup" 2>/dev/null; then
        log_info "Backed up $grub_file → $backup"
    else
        log_warning "Could not back up $grub_file; aborting GRUB edit"
        return 0
    fi

    # Append to GRUB_CMDLINE_LINUX_DEFAULT (preferred) if present,
    # else to GRUB_CMDLINE_LINUX. Anchored sed leaves unrelated
    # lines untouched.
    local applied=0
    if grep -qE '^GRUB_CMDLINE_LINUX_DEFAULT=' "$grub_file"; then
        sed -i.netgen-sed -E \
            "s|^(GRUB_CMDLINE_LINUX_DEFAULT=\")(.*)\"|\1\2 ${needed_param} iommu=pt\"|" \
            "$grub_file"
        applied=1
    elif grep -qE '^GRUB_CMDLINE_LINUX=' "$grub_file"; then
        sed -i.netgen-sed -E \
            "s|^(GRUB_CMDLINE_LINUX=\")(.*)\"|\1\2 ${needed_param} iommu=pt\"|" \
            "$grub_file"
        applied=1
    fi
    rm -f "${grub_file}.netgen-sed"

    if [[ $applied -eq 1 ]]; then
        log_success "Added '$needed_param iommu=pt' to GRUB cmdline"
    else
        log_warning "No GRUB_CMDLINE_LINUX[_DEFAULT] line in $grub_file"
        log_warning "Restoring backup; configure IOMMU manually"
        cp "$backup" "$grub_file"
        return 0
    fi

    # update-grub on Debian/Ubuntu; grub2-mkconfig on RHEL.
    local rebuilt=0
    if command -v update-grub >/dev/null 2>&1; then
        if update-grub 2>&1 | tail -5 | sed 's/^/[grub] /'; then
            rebuilt=1
        fi
    elif command -v grub2-mkconfig >/dev/null 2>&1; then
        local cfg="/boot/grub2/grub.cfg"
        if [[ -d /boot/efi/EFI ]]; then
            local efi_dir
            efi_dir=$(ls /boot/efi/EFI 2>/dev/null | head -1)
            [[ -n "$efi_dir" ]] && cfg="/boot/efi/EFI/${efi_dir}/grub.cfg"
        fi
        if grub2-mkconfig -o "$cfg" 2>&1 | tail -5 | sed 's/^/[grub] /'; then
            rebuilt=1
        fi
    else
        log_warning "No update-grub / grub2-mkconfig found; manual rebuild required"
    fi

    if [[ $rebuilt -eq 1 ]]; then
        log_success "GRUB config rebuilt"
        netgen_mark_reboot_required "IOMMU $needed_param + iommu=pt added to GRUB"
    else
        log_warning "GRUB config NOT rebuilt; restoring backup"
        cp "$backup" "$grub_file"
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

    # v0.5.51: if any step set REBOOT_REQUIRED, surface it loudly
    # at the end so the operator (or the wizard parsing this log)
    # doesn't miss it. The /var/run/netgen-reboot-required marker
    # was already written by netgen_mark_reboot_required.
    if [[ ${REBOOT_REQUIRED:-0} -eq 1 ]]; then
        echo ""
        echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${YELLOW}║                  REBOOT REQUIRED                          ║${NC}"
        echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "The following changes only take effect after reboot:"
        for r in "${REBOOT_REASONS[@]}"; do
            echo "  - $r"
        done
        echo ""
        echo "Reboot with: sudo systemctl reboot"
        echo ""
        # Exit code 75 = EX_TEMPFAIL (sysexits.h). Convention used
        # for "operation succeeded but needs a follow-up". The
        # /api/admin/install_dpdk endpoint parses this to surface
        # "reboot needed" in the response.
        exit 75
    fi
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
    
    # Run installation steps. v0.5.51: step_configure_iommu runs
    # between hugepages and NIC binding — binding to vfio-pci is
    # the operation that needs IOMMU active, so the cmdline must
    # be enqueued before we touch sysfs.
    step_preflight
    step_detect_dpdk_source
    step_install_dependencies
    step_build_dpdk
    step_build_tx_worker
    # v0.5.105: rx_worker is the companion to tx_worker. Failure is
    # non-fatal (DPDK RX engine becomes unavailable; Scapy RX still
    # works), so we don't gate Step 7+ on its success.
    step_build_rx_worker
    step_configure_hugepages
    step_configure_iommu
    step_nic_binding
    step_configure_library_paths
    step_verification
    step_summary
}

# Run main function
main "$@"

