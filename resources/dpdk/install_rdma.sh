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

# v0.5.28: comprehensive RDMA dep coverage. Operator request after
# v0.5.27 split: "make sure all the dependencies for rdma should
# be taken care during Setup RDMA". The v0.5.27 minimum-viable list
# missed several pieces that operators routinely need:
#
#   librdmacm-dev    — RDMA Connection Manager userspace headers.
#                      perftest binaries link librdmacm at runtime;
#                      the -dev variant lets the operator compile
#                      their own RDMA-CM-using code.
#   libibmad-dev     — InfiniBand MAD (Management Datagram) library.
#                      Required by ibdiagnet + many infiniband-diags
#                      utilities at compile time.
#   libibumad-dev    — userspace MAD interface (paired with libibmad).
#   libibnetdisc-dev — IB network discovery (used by ibnetdiscover,
#                      iblinkinfo for fabric topology).
#   rdmacm-utils     — rping / ucmatose / ucmd — RDMA-CM smoke test
#                      utilities. Operator's first "does my RDMA
#                      stack even work" tool, complementing
#                      ibv_devices for IB verbs.
#   python3-pyverbs  — Python ibv_* bindings. Used by some RDMA
#                      diagnostic scripts and lets operators script
#                      their own probes without writing C.
#   opensm           — OpenSM subnet manager. REQUIRED on native
#                      InfiniBand fabrics that don't have a
#                      switch-managed SM. Harmless on RoCE-only
#                      hosts (service stays disabled, no traffic).
#                      We explicitly disable the service so the
#                      install doesn't take over fabric management
#                      from any existing SM.
#   mstflint         — Mellanox firmware management tools (mstflint,
#                      mstconfig, mstfwreset). Used to query NIC
#                      firmware version, change port modes
#                      (Ethernet ↔ InfiniBand), and apply firmware
#                      updates. Operator-facing necessity on Mellanox
#                      hardware.
core_apt_cmd="DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    -o APT::Sandbox::User=root \
    --option Acquire::http::Timeout=30 \
    libibverbs-dev \
    librdmacm-dev \
    libibmad-dev \
    libibumad-dev \
    libibnetdisc-dev \
    rdma-core \
    perftest \
    ibverbs-utils \
    rdmacm-utils \
    infiniband-diags \
    python3-pyverbs \
    opensm \
    mstflint"

# Mellanox-only — may fail on hosts without MOFED. Failure here is
# logged but doesn't fail the script: hosts without Mellanox NICs
# don't need it, and hosts WITH Mellanox NICs but no MOFED repo
# might already have a working Mellanox stack via in-kernel mlx5.
#
# v0.5.28: added libmlx4-dev for ConnectX-3 / ConnectX-2 hardware.
# libmlx5-dev only covers ConnectX-4 and newer. Operators with
# older Mellanox NICs (still common in lab gear from the 2014-2018
# era) need the mlx4 headers separately.
mlx5_apt_cmd="DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    -o APT::Sandbox::User=root \
    --fix-missing \
    libmlx5-dev \
    libmlx4-dev"

log_info "Updating apt index..."
# v0.5.31: -o APT::Sandbox::User=root — see comment block above
# core_apt_cmd. Required when invoked from netgen-server.service
# (systemd RestrictSUIDSGID blocks apt's _apt-user privilege drop).
# v0.5.369 (audit rdma-install-must-not-fail): tee update output to
# /tmp so we can detect broken external repos below (NO_PUBKEY,
# 404, "no longer signed") and warn the operator specifically.
APT_UPDATE_LOG=/tmp/rdma_apt_update.log
set +e
apt-get update -o APT::Sandbox::User=root -o Acquire::http::Timeout=30 2>&1 | tee "$APT_UPDATE_LOG" | tail -3
_apt_update_rc=${PIPESTATUS[0]}
set -e
if [[ $_apt_update_rc -ne 0 ]]; then
    log_warning "apt-get update failed (rc=$_apt_update_rc) — continuing with cached index"
fi

# v0.5.369 (audit rdma-install-must-not-fail): surface stale/
# broken external apt repos. Operator on svl-d-ai-srv04 2026-09-20
# hit the Mellanox DOCA repo with an expired GPG key + 404s on
# aux package .debs; the pre-fix flow bailed at Step 1 with exit
# 2 even though rdma-core (the only essential) was installed.
if [[ -f "$APT_UPDATE_LOG" ]] && \
       grep -qE 'NO_PUBKEY|no longer signed|404 +Not Found' "$APT_UPDATE_LOG" 2>/dev/null; then
    log_warning "Detected broken external apt repo(s):"
    grep -E 'NO_PUBKEY|no longer signed|404 +Not Found' "$APT_UPDATE_LOG" 2>/dev/null | \
        head -5 | sed 's/^/  /' | while IFS= read -r _line; do
            log_warning "$_line"
        done
    log_warning "Install will proceed with --fix-missing so 404 fetches are skipped."
    log_warning "To silence future warnings, fix the repo's GPG key or disable it:"
    log_warning "  grep -rl <domain> /etc/apt/sources.list.d/"
    log_warning "  sudo mv <file>.list <file>.list.disabled"
fi

log_info "Installing core RDMA packages..."
log_info "  libibverbs-dev   — InfiniBand verbs library"
log_info "  librdmacm-dev    — RDMA Connection Manager library"
log_info "  libibmad-dev     — InfiniBand MAD library (mgmt datagrams)"
log_info "  libibumad-dev    — Userspace MAD interface"
log_info "  libibnetdisc-dev — IB network discovery (fabric topology)"
log_info "  rdma-core        — Userspace RDMA stack (ib_uverbs etc.)"
log_info "  perftest         — RDMA perf tools (ib_send_bw / read_bw / write_bw)"
log_info "  rdmacm-utils     — rping / ucmatose (RDMA-CM smoke tests)"
log_info "  ibverbs-utils    — ibv_devices, ibv_devinfo, ibv_rc_pingpong"
log_info "  infiniband-diags — ibstat, ibportstate, iblinkinfo"
log_info "  python3-pyverbs  — Python ibv_* bindings (diagnostic scripting)"
log_info "  opensm           — InfiniBand subnet manager (disabled by default)"
log_info "  mstflint         — Mellanox firmware tools (mstflint, mstconfig)"

# v0.5.55 (audit H7): mirror the install_dpdk.sh v0.5.30 lesson —
# preserve apt output to a file so the wizard / operator can grep
# the actual failure. Pre-fix the `2>&1` went to terminal only;
# the operator saw "exit 2" with no log to dig in.
RDMA_APT_LOG=/tmp/rdma_deps_install.log
log_info "apt log will be saved to: $RDMA_APT_LOG"

# v0.5.369 (audit rdma-install-must-not-fail): three-tier install
# so a stale external repo (Mellanox DOCA on srv04, expired GPG
# key + 404 aux .debs) cannot block the essential rdma-core
# package. rdma-core is the ONLY hard requirement — it ships the
# kernel modules Step 2 loads (ib_uverbs, ib_umad, rdma_cm,
# rdma_ucm, iw_cm). Everything else is nice-to-have userspace
# tooling.
#
#   Tier 1: full batch with --fix-missing (skips unfetchable pkgs)
#   Tier 2: per-package retry for the essentials only
#   Tier 3: dpkg-query post-check; if rdma-core is installed,
#           declare Step 1 done regardless of aux package status.
#
# All three tiers run under `set +e` so a non-zero apt exit
# doesn't abort the script (pre-fix `set -euo pipefail` at L38
# turned any pkg fetch 404 into an immediate exit).
core_apt_cmd_fix="${core_apt_cmd} --fix-missing"

set +e
(umask 077 && eval "$core_apt_cmd_fix" 2>&1 | tee "$RDMA_APT_LOG")
_core_rc=${PIPESTATUS[0]}
set -e

if [[ $_core_rc -ne 0 ]]; then
    log_warning "Batched core install returned rc=$_core_rc — retrying essentials individually"
    # Per-package fallback so one bad .deb doesn't block the rest.
    # Focused on the essentials the modprobe + module-persist steps
    # depend on. If the operator loses ibverbs-utils / perftest to
    # a broken repo they can still load kernel modules, still run
    # any Mellanox in-kernel driver, and the admin console will
    # still surface which utilities are missing.
    for pkg in rdma-core libibverbs-dev librdmacm-dev perftest rdmacm-utils; do
        set +e
        DEBIAN_FRONTEND=noninteractive apt-get install -y \
            -o Dpkg::Options::=--force-confdef \
            -o Dpkg::Options::=--force-confold \
            -o APT::Sandbox::User=root \
            --fix-missing --no-install-recommends \
            "$pkg" 2>&1 | tee -a "$RDMA_APT_LOG"
        _pkg_rc=${PIPESTATUS[0]}
        set -e
        if [[ $_pkg_rc -eq 0 ]]; then
            log_success "$pkg installed (or already at latest)"
        else
            log_warning "$pkg install returned rc=$_pkg_rc (see $RDMA_APT_LOG)"
        fi
    done
fi

# Post-condition: rdma-core is the only true essential. Any other
# package (ibverbs-utils, infiniband-diags, python3-pyverbs,
# mstflint, opensm) can be missing — Step 2 modprobe + kernel
# functionality doesn't need them.
if dpkg-query -W -f='${Status}\n' rdma-core 2>/dev/null | \
       grep -q '^install ok installed'; then
    log_success "Core RDMA stack installed (log: $RDMA_APT_LOG)."
else
    log_warning "rdma-core not installed after all fallbacks — Step 2 modprobe may fail."
    log_warning "Broken external apt repo is the likely cause."
    log_warning "Recovery:"
    log_warning "  1) grep -rl <domain> /etc/apt/sources.list.d/"
    log_warning "  2) sudo mv <file>.list <file>.list.disabled"
    log_warning "  3) sudo apt-get update && retry Install RDMA"
    log_warning "Continuing anyway — modules may already be present in kernel."
fi

# v0.5.28: opensm ships with an enabled-by-default service on some
# distros. On RoCE-only / Soft-RoCE / no-RDMA-hardware hosts, an
# unwanted opensm daemon is at best wasted memory, at worst takes
# over fabric management from a switch-resident SM. Disable + stop
# it; operators with native IB fabrics that need OpenSM can
# explicitly `systemctl enable --now opensm`.
if systemctl list-unit-files 2>/dev/null | grep -q '^opensm\.service'; then
    if systemctl is-enabled opensm.service 2>/dev/null | grep -q 'enabled'; then
        log_info "Disabling opensm.service (operator must enable explicitly if needed)"
        systemctl disable --now opensm.service 2>&1 | tail -2 || true
    else
        log_info "opensm.service is installed but disabled (correct default)"
    fi
fi

# Mellanox-specific
# v0.5.369 (audit rdma-install-must-not-fail): also wrapped in
# `set +e` — Mellanox userspace headers are optional; failure
# here MUST not abort the script.
log_info "Installing Mellanox-specific libmlx5-dev (optional)..."
set +e
eval "$mlx5_apt_cmd" 2>&1
_mlx5_rc=$?
set -e
if [[ $_mlx5_rc -eq 0 ]]; then
    log_success "libmlx5-dev installed."
else
    log_warning "libmlx5-dev install failed (likely no MOFED apt repo)."
    log_warning "If you have Mellanox NICs and want full PMD support, install"
    log_warning "the Mellanox OFED bundle separately. Continuing — in-kernel"
    log_warning "mlx5 still works without libmlx5-dev for basic ib_verbs use."
fi

# Step 2: load kernel modules
log_step "Step 2: Load RDMA kernel modules"

# v0.5.28: expanded kernel module list.
#   ib_uverbs  — userspace verbs interface (libibverbs needs this)
#   rdma_cm    — kernel-side RDMA Connection Manager
#   rdma_ucm   — userspace bridge to rdma_cm (librdmacm needs this;
#                without it, ib_send_bw / rping / any RDMA-CM
#                client call fails with EBADF on /dev/infiniband/rdma_cm)
#   ib_umad    — userspace MAD interface (ibstat / ibportstate
#                use this)
#   iw_cm      — iWARP connection manager. Mostly harmless on hosts
#                without iWARP hardware (Chelsio, Intel-some); load
#                anyway so the userspace stack is universal.
rdma_modules=("ib_uverbs" "rdma_cm" "rdma_ucm" "ib_umad" "iw_cm")
# v0.5.369 (audit rdma-install-must-not-fail): track load count
# so we can report "N/M modules loaded" at the end — matches the
# summary shape the admin console's RDMA card renders.
_mods_loaded=0
_mods_total=${#rdma_modules[@]}
for mod in "${rdma_modules[@]}"; do
    if lsmod | awk '{print $1}' | grep -qx "$mod"; then
        log_success "$mod already loaded"
        _mods_loaded=$((_mods_loaded + 1))
    elif modprobe "$mod" 2>/dev/null; then
        log_success "$mod loaded"
        _mods_loaded=$((_mods_loaded + 1))
    else
        log_warning "modprobe $mod failed (kernel may not have it)"
    fi
done
log_info "RDMA kernel modules: ${_mods_loaded}/${_mods_total} loaded"

# Persist module loading across reboots.
# v0.5.62 (audit M9): pre-fix the script skipped this write
# entirely when the file already existed — so an upgrade from
# v0.5.27 (which had only "ib_uverbs rdma_cm ib_umad") to v0.5.28+
# (which added "rdma_ucm" + "iw_cm" to the array above) NEVER
# refreshed the boot-time module list. Step 2's modprobe loop
# still loaded everything for the current session, but on the
# next reboot rdma_ucm-needing tools (`ib_send_bw`, `rping`)
# failed with EBADF on /dev/infiniband/rdma_cm until the operator
# noticed and manually modprobe'd. Always rewrite the file —
# it's owned by us, ~80 bytes, and we know the canonical set.
modules_load_file="/etc/modules-load.d/netgen-rdma.conf"
desired_content=$( {
    echo "# netgen install_rdma.sh — auto-load RDMA modules at boot"
    printf '%s\n' "${rdma_modules[@]}"
} )
current_content=""
[[ -f "$modules_load_file" ]] && current_content=$(cat "$modules_load_file" 2>/dev/null || true)
if [[ "$desired_content" != "$current_content" ]]; then
    log_info "Updating $modules_load_file with current RDMA module set"
    printf '%s\n' "$desired_content" > "$modules_load_file"
    log_success "Wrote $modules_load_file"
else
    log_info "$modules_load_file already matches current module set"
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

# v0.5.369 (audit rdma-install-must-not-fail): ibv_devices missing
# is now a warning, not an exit. Pre-fix `exit 3` here bit any
# operator whose external repo 404'd ibverbs-utils — script died
# even though the kernel modules Step 2 loaded ARE the actual
# RDMA plane. Downgrade + continue.
if ! command -v ibv_devices >/dev/null 2>&1; then
    log_warning "ibv_devices not on PATH — ibverbs-utils probably 404'd during install."
    log_warning "Kernel modules loaded above still provide the RDMA plane."
    log_warning "Install ibverbs-utils separately to enable this diagnostic:"
    log_warning "  sudo apt-get install -y --fix-missing ibverbs-utils"
else
    log_info "Detected RDMA devices:"
    set +e
    ibv_devices 2>&1 | tee /tmp/netgen_ibv_devices.log
    _ibv_rc=${PIPESTATUS[0]}
    set -e
    if [[ $_ibv_rc -eq 0 ]]; then
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
fi

if command -v perftest >/dev/null 2>&1 || command -v ib_send_bw >/dev/null 2>&1; then
    log_success "perftest tools available (ib_send_bw / ib_read_bw / ib_write_bw)"
else
    log_warning "perftest binaries not on PATH — netgen RDMA tests will fail"
fi

log_step "RDMA install complete"
# v0.5.369 (audit rdma-install-must-not-fail): final summary so
# the admin console's log stream renders "N/M modules loaded"
# even when Step 1 hit --fix-missing skips. Operator can see at
# a glance whether the essential plane is up.
if [[ "${_mods_loaded:-0}" -eq "${_mods_total:-5}" ]]; then
    log_success "Summary: ${_mods_loaded}/${_mods_total} RDMA kernel modules loaded — plane is up."
else
    log_warning "Summary: ${_mods_loaded:-0}/${_mods_total:-5} RDMA kernel modules loaded"
    log_warning "         — some modules missing; check log above."
fi
log_success "Next: use the Tools → RDMA → Blast a RDMA Flow... wizard in netgen,"
log_success "or run ibv_rc_pingpong / ib_send_bw manually to validate."
log_info ""
log_info "Note: DPDK Mellanox PMD support (drivers/net/mlx5) ALSO requires this"
log_info "RDMA stack. If you intend to use DPDK with Mellanox NICs, run this"
log_info "BEFORE Setup DPDK so the mlx5 PMD picks up libibverbs at build time."
exit 0
