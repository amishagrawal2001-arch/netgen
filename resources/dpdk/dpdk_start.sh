#!/usr/bin/env bash
# =======================================================================================
#  dpdk_start.sh  (formerly bnxt_dpdk_quickstart.sh)
#  ---------------------------------------------------------------------------------------
#  PURPOSE
#    End-to-end helper to bring up DPDK on common NICs:
#      1) Install build/runtime dependencies
#      2) Ensure a DPDK build tree (optionally auto-build if missing)
#      3) (Optional) Check IOMMU and load vfio-pci
#      4) Quiesce interface and unbind conflicting modules (bnxt_re)
#      5) Bind PCI device (BDF) to vfio-pci     <-- SKIPPED for Mellanox/NVIDIA (15b3)
#      6) Allocate/mount hugepages on the NIC√ï NUMA node (fallback node0)
#      7) Run a smoke test using dpdk-testpmd
#      8) Revert device back to kernel driver (auto-detect)
#
#  Mellanox/NVIDIA (vendor 15b3) special-case:
#    The DPDK mlx5 PMD works with the kernel driver (mlx5_core) and DOES NOT use vfio-pci.
#
#  EXIT CODES
#    0 OK | 1 usage | 2 environment/safety | 3 dpdk binary missing (--no-build) | 4 bind/revert failure
# =======================================================================================

set -euo pipefail

# ---------- Defaults ----------
DPDK_DIR="${DPDK_DIR:-$HOME/SURAJ/dpdk}"
BDF="${BDF:-}"
IFACE="${IFACE:-}"
HUGEP_2MB="${HUGEP_2MB:-1024}"
MOUNT_HP="${MOUNT_HP:-/mnt/huge}"
CORES="${CORES:-auto}"
RXQ="${RXQ:-2}"
TXQ="${TXQ:-2}"
NB_CORES="${NB_CORES:-2}"
TXPKTS="${TXPKTS:-1518}"
BURST="${BURST:-256}"
PORT_TOPO="${PORT_TOPO:-chained}"
BUILD_EXAMPLES="${BUILD_EXAMPLES:-all}"
DO_BUILD="${DO_BUILD:-1}"
VENDOR_MATCH="${VENDOR_MATCH:-14e4}"   # Broadcom
FORCE_MGMT="${FORCE_MGMT:-0}"
FORCE_BIND="${FORCE_BIND:-0}"
ACTION="${ACTION:-all}"
KERNEL_DRIVER="${KERNEL_DRIVER:-}"

# ---------- Usage ----------
usage() {
  cat <<EOF
Usage: sudo $(basename "$0") [options]

--dpdk-dir DIR         DPDK repo/build (default: $DPDK_DIR)
--bdf BDF              PCI address (e.g., 0000:37:00.0)
--iface IFACE          Interface name (e.g., enp55s0np0)
--cores LIST|auto      EAL core list (default: $CORES)
--rxq/--txq N          Rx/Tx queues (default: $RXQ/$TXQ)
--nb-cores N           testpmd workers (default: $NB_CORES)
--txpkts N             testpmd --txpkts (default: $TXPKTS)
--burst N              testpmd --burst (default: $BURST)
--hugepages-2mb N      2MB hugepages (default: $HUGEP_2MB)
--force-mgmt           Allow binding mgmt NIC (NOT recommended)
--force-bind           Force bind even if routes exist
--kernel-driver MOD    Revert driver (e.g., mlx5_core, bnxt_en)
--no-build             Skip DPDK build
--action X             all|deps|build|bind|huge|test|revert|show
--show                 Shorthand for --action show
-h, --help             Show help

Note (Mellanox/NVIDIA 15b3):
  vfio-pci is skipped on purpose. Keep 'mlx5_core' loaded.

Examples:
  SHOW:   sudo ./dpdk_start.sh --show
  DEPS:   sudo ./dpdk_start.sh --action deps
  BUILD:  sudo ./dpdk_start.sh --action build --dpdk-dir /root/SURAJ/dpdk
  BIND:   sudo ./dpdk_start.sh --action bind --bdf 0000:37:00.0
          sudo ./dpdk_start.sh --action bind --iface enp55s0np0
          # if routes exist, re-run with:
          sudo ./dpdk_start.sh --action bind --bdf 0000:37:00.0 --force-bind
  HUGE:   sudo ./dpdk_start.sh --action huge --bdf 0000:37:00.0 --hugepages-2mb 2048
  TEST:   sudo ./dpdk_start.sh --action test --bdf 0000:37:00.0 --txpkts 64 --burst 64 --cores 0,2
  REVERT: sudo ./dpdk_start.sh --action revert --bdf 0000:a0:00.0
          sudo ./dpdk_start.sh --action revert --bdf 0000:a0:00.0 --kernel-driver mlx5_core
  ALL:    sudo ./dpdk_start.sh --iface enp55s0np0
          sudo ./dpdk_start.sh --bdf 0000:37:00.0 --no-build


EOF
}

# ---------- Arg parsing ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dpdk-dir) DPDK_DIR="$2"; shift 2 ;;
    --bdf) BDF="$2"; shift 2 ;;
    --iface) IFACE="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --rxq) RXQ="$2"; shift 2 ;;
    --txq) TXQ="$2"; shift 2 ;;
    --nb-cores) NB_CORES="$2"; shift 2 ;;
    --txpkts) TXPKTS="$2"; shift 2 ;;
    --burst) BURST="$2"; shift 2 ;;
    --hugepages-2mb) HUGEP_2MB="$2"; shift 2 ;;
    --force-mgmt) FORCE_MGMT=1; shift ;;
    --force-bind) FORCE_BIND=1; shift ;;
    --kernel-driver) KERNEL_DRIVER="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --action) ACTION="$2"; shift 2 ;;
    --show) ACTION="show"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

# ---------- Helpers ----------
require_root() { [[ "$(id -u)" -eq 0 ]] || { echo "Run as root/sudo."; exit 1; }; }
cmd_exists() { command -v "$1" &>/dev/null; }
default_iface() { ip route show default 2>/dev/null | awk '/default/ {print $5; exit}'; }
iface_is_default() { local d; d="$(default_iface || true)"; [[ -n "$d" && "$d" == "$1" ]]; }
iface_to_bdf() { local p; p="$(readlink -f "/sys/class/net/$1/device" || true)"; [[ -n "$p" ]] || return 1; basename "$p"; }
bdf_to_iface() { local n; n=$(ls /sys/bus/pci/devices/"$1"/net 2>/dev/null | head -n1 || true); [[ -n "$n" ]] && echo "$n"; }
bdf_vendor() { [[ -f /sys/bus/pci/devices/"$1"/vendor ]] && awk '{print tolower($0)}' "/sys/bus/pci/devices/$1/vendor" | sed 's/0x//'; }
bdf_numa_node() { local f="/sys/bus/pci/devices/$1/numa_node"; [[ -f "$f" ]] && cat "$f" || echo "-1"; }
pick_two_cores_on_node() { local n="$1"; cmd_exists lscpu || { echo "0,1"; return; }; lscpu -e=CPU,NODE | awk -v n="$n" '$2==n{print $1}' | head -n2 | paste -sd, -; }
route_has_iface() { ip -o route show 2>/dev/null | awk -v d="$1" '$0 ~ (" dev " d "($| )") {found=1} END{exit !found}'; }

# ---------- Stages ----------
ensure_deps() {
  apt-get update -y
  apt-get install -y build-essential meson ninja-build pkg-config libnuma-dev libelf-dev libpcap-dev
  apt-get install -y rdma-core || true
}

ensure_dpdk_tree() {
  if [[ ! -x "$DPDK_DIR/build/app/dpdk-testpmd" ]]; then
    [[ "$DO_BUILD" -eq 1 ]] || { echo "[build] dpdk-testpmd missing and --no-build set."; exit 3; }
    if [[ ! -d "$DPDK_DIR/.git" ]]; then
      mkdir -p "$(dirname "$DPDK_DIR")"
      git clone https://github.com/DPDK/dpdk.git "$DPDK_DIR"
    fi
    cd "$DPDK_DIR"
    meson setup build -Dexamples="$BUILD_EXAMPLES" || true
    ninja -C build
  fi
}

ensure_iommu_hint() {
  if ! grep -Eq 'intel_iommu=on|amd_iommu=on' /proc/cmdline; then
    echo "[vfio] IOMMU not found. Enable it in GRUB (intel_iommu=on iommu=pt)."
  fi
  modprobe vfio-pci || true
}

quiesce_iface() {
  if [[ -n "$IFACE" ]]; then
    if iface_is_default "$IFACE" && [[ "$FORCE_MGMT" -ne 1 ]]; then
      echo "[safety] $IFACE is default-route. Use --force-mgmt if you really want to bind it."; exit 2;
    fi
    ip link set "$IFACE" down || true
    ip addr flush dev "$IFACE" || true
    lsmod | grep -q '^bnxt_re' && { rmmod bnxt_re || true; }
  fi
}
alloc_hugepages_for_node() {
  local node="$1" sysnode
  sysnode="/sys/devices/system/node/node${node}/hugepages/hugepages-2048kB/nr_hugepages"

  if [[ "$node" -lt 0 || ! -f "$sysnode" ]]; then
    node=0
    sysnode="/sys/devices/system/node/node0/hugepages/hugepages-2048kB/nr_hugepages"
  fi

  echo "[huge] Allocating $HUGEP_2MB x 2MB pages on node $node ..."
  echo "$HUGEP_2MB" > "$sysnode" || true
  mkdir -p "$MOUNT_HP"
  mountpoint -q "$MOUNT_HP" || mount -t hugetlbfs nodev "$MOUNT_HP"
}

bind_vfio() {
  [[ -z "$BDF" && -n "$IFACE" ]] && BDF="$(iface_to_bdf "$IFACE")"
  [[ -n "$BDF" ]] || { echo "[bind] Provide --bdf or --iface"; exit 1; }

  local vendor; vendor=$(bdf_vendor "$BDF")
  local ifn; ifn="$(bdf_to_iface "$BDF" || true)"

  echo "[bind] Preparing $BDF (iface=${ifn:-none}) ..."

  # Special handling for Mellanox
  if [[ "$vendor" == "15b3" ]]; then
    echo "[bind] $BDF is Mellanox/NVIDIA; keep mlx5_core (DPDK mlx5 PMD)."
    alloc_hugepages_for_node "$(bdf_numa_node "$BDF")" "${HUGEP_2MB:-1024}" "$BDF"
    return 0
  fi

  # Force interface down before unbinding
  if [[ -n "$ifn" ]]; then
    echo "[bind] Bringing $ifn down ..."
    ip link set "$ifn" down || true
  fi

  # Unbind from existing kernel driver
  if [[ -e /sys/bus/pci/devices/$BDF/driver/unbind ]]; then
    echo "[bind] Unbinding $BDF from current driver ..."
    echo "$BDF" > /sys/bus/pci/devices/$BDF/driver/unbind || {
      echo "[bind] ERROR: could not unbind $BDF"; exit 1;
    }
  fi

  # Set driver override
  echo vfio-pci > /sys/bus/pci/devices/$BDF/driver_override

  # Bind to vfio-pci
  echo "[bind] Binding $BDF to vfio-pci ..."
  echo "$BDF" > /sys/bus/pci/drivers/vfio-pci/bind || {
    echo "[bind] ERROR: bind to vfio-pci failed for $BDF"; exit 1;
  }

  # Clear override
  echo "" > /sys/bus/pci/devices/$BDF/driver_override

  echo "[bind] Successfully bound $BDF to vfio-pci"
}


revert_kernel() {
  local devbind="$DPDK_DIR/usertools/dpdk-devbind.py"
  [[ -x "$devbind" ]] || { echo "[revert] dpdk-devbind.py not found"; exit 4; }

  [[ -z "$BDF" && -n "$IFACE" ]] && BDF="$(iface_to_bdf "$IFACE")"
  [[ -n "$BDF" ]] || { echo "[revert] Provide --bdf or --iface"; exit 1; }

  # Detect kernel driver
  local drv="$KERNEL_DRIVER"
  if [[ -z "$drv" ]]; then
    if [[ "$(bdf_vendor "$BDF")" == "15b3" ]]; then
      drv="mlx5_core"
    else
      drv="$(detect_kernel_driver_from_devbind || true)"
    fi
  fi
  if [[ -z "$drv" ]]; then
    drv="$(fallback_kernel_driver_by_vendor || true)"
  fi
  if [[ -z "$drv" ]]; then
    echo "[revert] Could not determine kernel driver. Pass --kernel-driver <module> (e.g., mlx5_core, bnxt_en)."
    exit 4
  fi

  # If already bound to kernel driver
  local curdrv="$(readlink -f /sys/bus/pci/devices/$BDF/driver 2>/dev/null | xargs basename 2>/dev/null || true)"
  if [[ "$curdrv" == "$drv" ]]; then
    echo "[revert] $BDF is already bound to $drv."
  else
    echo "[revert] Rebinder: vfio-pci -> $drv on $BDF ..."
    modprobe "$drv" || true

    # clear driver_override
    echo "" > "/sys/bus/pci/devices/$BDF/driver_override" || true

    # unbind from vfio-pci if still bound
    if [[ -e /sys/bus/pci/drivers/vfio-pci/$BDF ]]; then
      echo "$BDF" > /sys/bus/pci/drivers/vfio-pci/unbind
    fi

    # bind to kernel driver
    echo "$BDF" > /sys/bus/pci/drivers/$drv/bind || { echo "[revert] Failed to bind $BDF to $drv"; exit 4; }
  fi

  # Wait for netdev to reappear and bring it up
  local ifn=""
  for i in {1..10}; do
    ifn="$(bdf_to_iface "$BDF" || true)"
    [[ -z "$ifn" ]] && ifn="$(basename $(readlink -f /sys/bus/pci/devices/$BDF/net/* 2>/dev/null) 2>/dev/null || true)"
    if [[ -n "$ifn" && -e "/sys/class/net/$ifn" ]]; then
      echo "[revert] Bringing $ifn up ..."
      ip link set "$ifn" up || true
      break
    fi
    sleep 0.5
  done

  # Show devbind info
  "$devbind" -s | sed -n "/$BDF/ p" || true

  # === Release hugepages if allocated for this device ===
  local markfile="/var/run/dpdk-hugepages.$BDF"
  if [[ -f "$markfile" ]]; then
    local node="$(bdf_numa_node "$BDF")"
    local sysnode="/sys/devices/system/node/node${node}/hugepages/hugepages-2048kB/nr_hugepages"
    local prev=$(cat "$sysnode")
    echo 0 > "$sysnode"
    local now=$(cat "$sysnode")
    echo "[revert] Hugepages on NUMA node $node: $prev -> $now"
    rm -f "$markfile"
    # If no hugepages left, unmount
    local total=$(awk '/HugePages_Total/ {print $2}' /proc/meminfo)
    if [[ "$total" -eq 0 ]]; then
      umount "$MOUNT_HP" 2>/dev/null || true
      echo "[revert] Unmounted $MOUNT_HP (no hugepages left)."
    fi
  fi
}






detect_kernel_driver_from_devbind() {
  local devbind="$DPDK_DIR/usertools/dpdk-devbind.py"
  local line unused
  line="$("$devbind" -s | awk -v b="$BDF" '$1==b{print; exit}')" || true
  [[ -z "$line" ]] && return 1
  unused="$(awk -F"unused=" '{print $2}' <<<"$line" | tr -d "'" | tr -d '\r')"
  for mod in ${unused//,/ }; do
    [[ "$mod" != "vfio-pci" && -n "$mod" ]] && echo "$mod" && return 0
  done
  return 1
}

fallback_kernel_driver_by_vendor() {
  case "$(bdf_vendor "$BDF" || echo "")" in
    14e4) echo "bnxt_en" ;;
    15b3) echo "mlx5_core" ;;
    8086) echo "ice" ;;
    *)    echo "" ;;
  esac
}

show_dpdk_nics() {
  local devbind="$DPDK_DIR/usertools/dpdk-devbind.py"
  echo "[show] DPDK-devbind status (network devices):"
  if [[ -x "$devbind" ]]; then
    "$devbind" --status-dev net 2>/dev/null || "$devbind" -s
  else
    echo "[show] dpdk-devbind.py not found at $devbind"
  fi

  echo
  echo "[show] Sysfs summary (* = vendor commonly supported by a DPDK PMD):"
  local CAP="14e4 15b3 8086 1425 19ee 177d 1077 1924 1af4 10df 10ee 10ec 1d6a"
  for p in /sys/class/net/*; do
    local ifn; ifn="$(basename "$p")"
    [[ "$ifn" == "lo" ]] && continue
    local b; b="$(iface_to_bdf "$ifn" || true)"
    [[ -z "$b" ]] && continue
    local ven; ven="$(bdf_vendor "$b" || echo "?")"
    local drv="-"
    [[ -e /sys/bus/pci/devices/$b/driver ]] && drv="$(basename "$(readlink -f /sys/bus/pci/devices/$b/driver)")"
    local numa; numa="$(bdf_numa_node "$b")"
    local mark=""
    for v in $CAP; do
      [[ "$ven" == "$v" ]] && mark="*" && break
    done
    printf "%s  if=%-12s vendor=%s driver=%-12s numa=%s %s\n" "$b" "$ifn" "$ven" "$drv" "$numa" "$mark"
  done

  echo
  echo "[show] Hugepages summary:"
  for nodepath in /sys/devices/system/node/node*/hugepages/hugepages-2048kB; do
    [[ -d "$nodepath" ]] || continue
    local node; node=$(basename "$(dirname "$(dirname "$nodepath")")" | sed 's/node//')
    local count; count=$(cat "$nodepath/nr_hugepages")

    # Attribution lookup
    local attrib=""
    for f in /var/run/dpdk-hugepages.*; do
      [[ -f "$f" ]] || continue
      source "$f" 2>/dev/null || continue
      if [[ "$NUMA" == "$node" ]]; then
        attrib+=" (BDF=$BDF, ${PAGES} pages)"
      fi
    done

    printf "  Node%-2s : %s x 2MB%s\n" "$node" "$count" "$attrib"
  done

  grep -i HugePages_ /proc/meminfo | sed 's/^/  /'
}






# ---------- Main ----------
main() {
  require_root
  [[ -z "$BDF" && -n "$IFACE" ]] && BDF="$(iface_to_bdf "$IFACE")" || true
  case "$ACTION" in
    show)   show_dpdk_nics ;;
    deps)   ensure_deps ;;
    build)  ensure_dpdk_tree ;;
    bind)   ensure_iommu_hint; bind_vfio ;;
    huge)   [[ -n "$BDF" ]] || { echo "[huge] Need --bdf or --iface"; exit 1; }; alloc_hugepages_for_node "$(bdf_numa_node "$BDF")" ;;
    test)   [[ -n "$BDF" ]] || { echo "[test] Need --bdf or --iface"; exit 1; }; ensure_dpdk_tree; run_testpmd ;;
    revert) revert_kernel ;;
    all)    ensure_deps; ensure_dpdk_tree; ensure_iommu_hint; bind_vfio; alloc_hugepages_for_node "$(bdf_numa_node "$BDF")"; run_testpmd ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"