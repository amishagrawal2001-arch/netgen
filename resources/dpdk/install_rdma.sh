#!/bin/bash
# =============================================================================
# install_rdma.sh — RDMA stack installer (separated from DPDK in v0.5.27)
# =============================================================================
# Operator request (v0.5.27):
#
#   "rdma install should be separate, it should not be part of dpdk install"
#
# Rationale:
#   - DPDK runs on any NIC (Intel, Broadcom, Mellanox, etc.); the RDMA
#     stack is only required for Mellanox PMDs in DPDK AND for netgen's
#     perftest orchestrator (ib_send_bw / ib_read_bw / ib_write_bw).
#   - An operator on Intel hardware shouldn't have to pull in
#     libibverbs + rdma-core to get DPDK working.
#   - An operator wanting JUST RDMA performance testing (no DPDK)
#     shouldn't have to invoke the multi-step DPDK build.
#
# What this script does:
#   1. Verify root
#   2. apt-install RDMA stack (libibverbs-dev, rdma-core, perftest,
#      ibverbs-utils, infiniband-diags)
#   3. apt-install Mellanox-specific libmlx5-dev in a SEPARATE batch
#      (tolerated to fail on hosts without MOFED apt repo)
#   4. Load kernel modules (ib_uverbs, rdma_cm, ib_umad)
#   5. Enable + start rdma-core service if present
#   6. Verify with `ibv_devices` (lists RDMA-capable interfaces)
#
# What this script does NOT do:
#   - Touch DPDK source / build / drivers (that's install_dpdk.sh)
#   - Touch hugepages or VFIO (DPDK runtime concerns, not RDMA)
#   - Configure IOMMU (DPDK + VFIO concern)
#
# Invoked by:
#   - GUI: Tools → RDMA → Setup RDMA... (POST /api/admin/install_rdma)
#   - CLI: sudo install_rdma.sh [--auto]
# =============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# v0.5.21 lesson — HOME may be unbound when spawned from systemd.
: "${HOME:=/root}"
AUTO_MODE="${AUTO_MODE:-0}"

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error()   { echo -e "${RED}[✗]${NC} $1"; }
log_step() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
}

# CLI flag parsing
for arg in "$@"; do
    case "$arg" in
        --auto) AUTO_MODE=1 ;;
        --help|-h)
            echo "Usage: $0 [--auto]"
            echo "  --auto   Non-interactive, no prompts"
            exit 0 ;;
    esac
done

# Step 0: root check
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Step 1: distro check (we currently support apt-based hosts only)
if ! command -v apt-get >/dev/null 2>&1; then
    log_error "apt-get not found — this script supports Debian/Ubuntu only."
    log_error "On RHEL/CentOS/Fedora install manually:"
    log_error "  dnf install -y libibverbs-devel rdma-core perftest \\"
    log_error "                 libibverbs-utils infiniband-diags"
    exit 1
fi

log_step "Step 1: Install RDMA stack (apt)"

# Core RDMA — always installable on Ubuntu/Debian. Splitting from
# the Mellanox-only libmlx5-dev mirrors install_dpdk.sh's pattern:
# Mellanox packages can fail independently on hosts without the
# MOFED apt repo, and we don't want that to poison the core batch.
core_apt_cmd="DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    --option Acquire::http::Timeout=30 \
    libibverbs-dev \
    rdma-core \
    perftest \
    ibverbs-utils \
    infiniband-diags"

# Mellanox-only — may fail on hosts without MOFED. Failure here is
# logged but doesn't fail the script: hosts without Mellanox NICs
# don't need it, and hosts WITH Mellanox NICs but no MOFED repo
# might already have a working Mellanox stack via in-kernel mlx5.
mlx5_apt_cmd="DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    libmlx5-dev"

log_info "Updating apt index..."
apt-get update -o Acquire::http::Timeout=30 2>&1 | tail -3 || {
    log_warning "apt-get update failed — continuing with cached index"
}

log_info "Installing core RDMA packages..."
log_info "  libibverbs-dev — InfiniBand verbs library"
log_info "  rdma-core      — Userspace RDMA stack (ib_uverbs etc.)"
log_info "  perftest       — RDMA perf tools (ib_send_bw / read_bw / write_bw)"
log_info "  ibverbs-utils  — ibv_devices, ibv_devinfo, ibv_rc_pingpong"
log_info "  infiniband-diags — ibstat, ibportstate, iblinkinfo"

if ! eval "$core_apt_cmd" 2>&1; then
    log_error "Core RDMA package install failed."
    log_error "Run \`apt-get install -f\` to repair broken deps, then retry."
    exit 2
fi
log_success "Core RDMA stack installed."

# Mellanox-specific
log_info "Installing Mellanox-specific libmlx5-dev (optional)..."
if eval "$mlx5_apt_cmd" 2>&1; then
    log_success "libmlx5-dev installed."
else
    log_warning "libmlx5-dev install failed (likely no MOFED apt repo)."
    log_warning "If you have Mellanox NICs and want full PMD support, install"
    log_warning "the Mellanox OFED bundle separately. Continuing — in-kernel"
    log_warning "mlx5 still works without libmlx5-dev for basic ib_verbs use."
fi

# Step 2: load kernel modules
log_step "Step 2: Load RDMA kernel modules"

rdma_modules=("ib_uverbs" "rdma_cm" "ib_umad")
for mod in "${rdma_modules[@]}"; do
    if lsmod | awk '{print $1}' | grep -qx "$mod"; then
        log_success "$mod already loaded"
    elif modprobe "$mod" 2>/dev/null; then
        log_success "$mod loaded"
    else
        log_warning "modprobe $mod failed (kernel may not have it)"
    fi
done

# Persist module loading across reboots
modules_load_file="/etc/modules-load.d/netgen-rdma.conf"
if [[ ! -f "$modules_load_file" ]]; then
    log_info "Persisting RDMA modules across reboots: $modules_load_file"
    {
        echo "# netgen install_rdma.sh — auto-load RDMA modules at boot"
        printf '%s\n' "${rdma_modules[@]}"
    } > "$modules_load_file"
    log_success "Wrote $modules_load_file"
else
    log_info "$modules_load_file already exists; leaving unchanged"
fi

# Step 3: enable rdma-core service
log_step "Step 3: Enable rdma-core service"

if systemctl list-unit-files 2>/dev/null | grep -q '^rdma-hw\.target'; then
    if systemctl enable rdma-hw.target 2>&1 | tail -3; then
        log_success "rdma-hw.target enabled"
    else
        log_warning "Failed to enable rdma-hw.target (rdma-core may not be systemd-managed)"
    fi
elif systemctl list-unit-files 2>/dev/null | grep -q '^rdma\.service'; then
    if systemctl enable --now rdma.service 2>&1 | tail -3; then
        log_success "rdma.service enabled + started"
    else
        log_warning "Failed to enable rdma.service"
    fi
else
    log_info "No rdma systemd unit found (Ubuntu 24.04 doesn't ship one)"
    log_info "Userspace ib_verbs works via the kernel modules loaded above."
fi

# Step 4: verify
log_step "Step 4: Verify RDMA stack"

if ! command -v ibv_devices >/dev/null 2>&1; then
    log_error "ibv_devices not found despite ibverbs-utils install."
    log_error "Something is wrong with the apt cache or package set."
    exit 3
fi

log_info "Detected RDMA devices:"
if ibv_devices 2>&1 | tee /tmp/netgen_ibv_devices.log; then
    dev_count=$(ibv_devices 2>/dev/null | awk 'NR>2 && /[a-z_]/' | wc -l | tr -d ' ')
    if [[ "$dev_count" -gt 0 ]]; then
        log_success "Found $dev_count RDMA device(s). Stack is functional."
    else
        log_warning "No RDMA devices detected. This is expected if:"
        log_warning "  - No RDMA-capable hardware is present"
        log_warning "  - Mellanox NICs need MOFED + Mellanox firmware bound to interfaces"
        log_warning "  - Soft RoCE (rxe) is not yet configured"
        log_warning "Run \`lspci | grep -i mellanox\` to check for hardware."
    fi
else
    log_warning "ibv_devices returned nonzero — RDMA kernel state may be incomplete."
fi

if command -v perftest >/dev/null 2>&1 || command -v ib_send_bw >/dev/null 2>&1; then
    log_success "perftest tools available (ib_send_bw / ib_read_bw / ib_write_bw)"
else
    log_warning "perftest binaries not on PATH — netgen RDMA tests will fail"
fi

log_step "RDMA install complete"
log_success "Next: use the Tools → RDMA → Blast a RDMA Flow... wizard in netgen,"
log_success "or run ibv_rc_pingpong / ib_send_bw manually to validate."
log_info ""
log_info "Note: DPDK Mellanox PMD support (drivers/net/mlx5) ALSO requires this"
log_info "RDMA stack. If you intend to use DPDK with Mellanox NICs, run this"
log_info "BEFORE Setup DPDK so the mlx5 PMD picks up libibverbs at build time."
exit 0
