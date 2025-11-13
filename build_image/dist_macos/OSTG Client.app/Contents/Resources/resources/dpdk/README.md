# DPDK Quickstart & UDP TX Worker (mlx5/bnxt) — **Thor2 Ready**

This repo contains two helper scripts that get you from **bare host → line‑rate packets** quickly on Mellanox/NVIDIA (mlx5) and Broadcom NetXtreme‑E/**Thor2** (bnxt) NICs:

- **`dpdk_start.sh`** — bring‑up helper: deps, build, (safe) bind, hugepages, and `testpmd` smoke test.  
  *Special case:* it **skips vfio** automatically for Mellanox (vendor **15b3**) because DPDK’s `mlx5` PMD works **with the kernel driver** (`mlx5_core`).  
  For Broadcom/Thor2 (vendor **14e4**), it binds to **`vfio-pci`** (with safety checks).
- **`build_tx_worker.sh`** — emits and builds a fast-path **UDP traffic generator** (`tx_worker`) against your system DPDK or a provided DPDK build tree.
  - Bulk mbuf alloc, header‑template copy
  - HW checksum offloads when available (IPv4 + UDP), automatic SW fallback
  - `--no-udp-csum` (legal on IPv4)
  - Burst‑paced PPS targeting with partial‑burst safety
  - Payload tag `"[<stream-id>#<seq>]"` for easy RX correlation

Both scripts are **Thor2‑aware**. Thor/Thor2 == Broadcom NetXtreme‑E (bnxt).

---

## Supported NIC Families

| Vendor | Vendor ID | Kernel Driver | DPDK PMD | Bind Mode |
|-------:|:---------:|---------------|----------|-----------|
| Mellanox / NVIDIA ConnectX‑5/6/7 | **15b3** | `mlx5_core` | `mlx5` | **Kernel** (no vfio) |
| Broadcom NetXtreme‑E (**Thor / Thor2**) | **14e4** | `bnxt_en` | `bnxt` | **vfio-pci** |
| Intel (modern) | **8086** | `ice` (or `ixgbe`/`i40e`) | `ice`/`ixgbe`/`i40e` | vfio‑pci (typical) |

> The script autodetects by **PCI vendor ID** and prints the right guidance.

---

## What the scripts install (and what they don’t)

### Automatically installed (when you pass `--install-deps` to the builder; or `--action deps` on the starter)

**Base build & runtime**
- Build tools: `build-essential` (or distro “Development Tools”), `meson`, `ninja-build`, `pkg-config`
- Libraries: `libnuma-dev`/`numactl-libs`, `libelf-dev`/`elfutils-libelf-devel`, `libpcap-dev`
- Utilities: `pciutils`, `ethtool` (via distro base; handy for debugging)

**mlx5 / Mellanox (vendor 15b3)**
- Runtime user-space: `rdma-core` (ibverbs/mlx5 runtime; safe to install on any host)
- No vfio bind. The script keeps **`mlx5_core`** loaded and tells you to run with **`-a <BDF>`**.

**bnxt / Broadcom Thor/Thor2 (vendor 14e4)**
- Uses **`vfio-pci`** for DPDK. Script ensures IOMMU hint and unloads conflicting `bnxt_re` (RoCE) if present.
- (Optional) BNXT‑only PMD pack via `build_tx_worker.sh --prepare-bnxt` to avoid mlx5 plugin tangles on mixed hosts.

### **Not** installed/changed automatically (you might need to do this once)
- **IOMMU kernel flags** for vfio: add `intel_iommu=on iommu=pt` (Intel) or `amd_iommu=on iommu=pt` (AMD) to GRUB and reboot.
- **Firmware** updates for NICs (mlx5/Thor2) — use vendor tools if your environment requires newer FW.
- Persistent **hugepage** configuration (the script allocates on demand but does not edit `/etc/fstab`).

> TL;DR: after a first reboot with IOMMU on (for vfio NICs) you can rely on these scripts to handle the rest per run.

---

## Requirements

- Linux with sudo/root
- **Hugepages** available (script can allocate/mount per‑run)
- For vfio paths (Broadcom/Thor2/Intel): **IOMMU enabled**
- DPDK source tree (the script can clone/build) or system DPDK install

---

## Layout

```
dpdk_start.sh        # DPDK bring-up & test helper (mlx5/bnxt-safe)
build_tx_worker.sh   # Emits + builds ./tx_worker/tx_worker (fast UDP generator)
tx_worker/           # Created on first build; contains source, meson.build, build/
```

---

## Quickstarts

### A) Mellanox/NVIDIA (mlx5, vendor 15b3) — **NO vfio**
```bash
# Show status
sudo ./dpdk_start.sh --show

# Allocate hugepages on the NIC’s NUMA node and print a testpmd line
sudo ./dpdk_start.sh --action bind --bdf 0000:b5:00.0   # will SKIP vfio, prep hugepages

# Interactive testpmd
sudo ~/SURAJ/dpdk/build/app/dpdk-testpmd -l 1-2 -n 4 -a 0000:b5:00.0 -- --interactive
testpmd> show device info all
testpmd> start tx_first 1
```

### B) Broadcom NetXtreme‑E / **Thor2** (bnxt, vendor 14e4) — **vfio-pci**
```bash
# Bind to vfio-pci + hugepages + suggested testpmd line
sudo ./dpdk_start.sh --action bind --bdf 0000:22:00.0

# Smoke test (non-interactive txonly)
sudo ~/SURAJ/dpdk/build/app/dpdk-testpmd -l 1-2 -n 4 -a 0000:22:00.0 -- \
  --port-topology=chained --rxd=2048 --txd=2048 --rxq=2 --txq=2 --nb-cores=2 \
  --forward-mode=txonly --auto-start --txpkts=64 --burst=256
```

---

## `dpdk_start.sh` — Usage

```bash
sudo ./dpdk_start.sh --help

--action X             all|deps|build|bind|huge|test|revert|show (default: all)
--dpdk-dir DIR         DPDK repo/build (default: ~/SURAJ/dpdk)
--bdf BDF              PCI address (0000:BB:DD.F)   # or --iface IFACE
--cores LIST|auto      EAL core list (testpmd)      # default auto
--rxq/--txq N          Rx/Tx queues (testpmd)       # default 2/2
--nb-cores N           testpmd workers              # default 2
--txpkts N             testpmd --txpkts             # default 1518
--burst N              testpmd --burst              # default 256
--hugepages-2mb N      Allocate 2MB pages (default 1024)
--force-mgmt           Allow binding default-route NIC (not recommended)
--force-bind           Force vfio bind if routes still reference the iface
--kernel-driver MOD    Revert: explicitly pick module (mlx5_core, bnxt_en, ...)
--no-build             Skip auto-build of DPDK
--show                 Show device lists (kernel, DPDK-bound, mlx5-usable)
```

**Special-casing**  
- Vendor **15b3** (mlx5): **skips vfio**, prints “use `-a <BDF>`”, and (optionally) preps hugepages.
- Vendor **14e4** (bnxt/Thor2): **binds to `vfio-pci`**, unloads `bnxt_re`, and warns if the interface still has routes.

**Handy views**  
```bash
sudo ./dpdk_start.sh --show        # Kernel devices + sysfs summary
sudo ./dpdk_start.sh --show-bound  # Explicit list of vfio-bound and mlx5-usable ports
```

---

## `build_tx_worker.sh` — Usage

```bash
./build_tx_worker.sh --help

--out-dir DIR          Working dir (default: ./tx_worker)
--install-deps         Install meson/ninja/pkg-config/numa/etc
--dpdk-tree PATH       Point to a local DPDK build to find libdpdk.pc
--dpdk-pc-dir DIR      Alternatively, point directly to dir with libdpdk.pc
--install              Run “ninja install” into --prefix
--prefix DIR           Install prefix (default: /usr/local)
--prepare-bnxt         Copy only bnxt PMD (librte_net_bnxt.so*) to a folder
--bnxt-pmds-dir DIR    Output dir for the BNXT-only PMD pack
--env-file PATH        Write exports (TX_WORKER_BIN, RTE_EAL_PMD_PATH)
--rewrite-src          Overwrite tx_worker.c even if it exists
```

**Build examples**
```bash
# One-liner on a fresh host (system DPDK or your DPDK tree)
./build_tx_worker.sh --install-deps --rewrite-src

# or, use your local DPDK
./build_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk
```

**Run examples**
```bash
# mlx5 (no vfio)
sudo ./tx_worker/build/tx_worker -l 1-2 -n 4 -a 0000:b5:00.0 -- \
  --src-mac 5C:25:73:3F:36:2E --dst-mac 02:00:00:00:00:00 \
  --src-ip 192.168.100.10 --dst-ip 192.168.100.11 \
  --src-port 1234 --dst-port 4791 --size 64 \
  --pps 0 --stream-id flood

# bnxt / Thor2 (vfio)
sudo ./tx_worker/build/tx_worker -l 1-2 -n 4 -a 0000:22:00.0 -- \
  --src-mac 7C:C2:55:BD:7B:30 --dst-mac 02:00:00:00:00:00 \
  --src-ip 192.168.1.10 --dst-ip 192.168.1.11 \
  --src-port 1234 --dst-port 4791 \
  --size 64 --pps 2000000 --duration 10 --stream-id demo
```
> `--pps 0` = run as fast as the NIC/PCIe can. Use `--burst N` to tune pacing and descriptor pressure.

---

## Building DPDK (if you don’t have it yet)

`dpdk_start.sh --action build` can clone + build into `~/SURAJ/dpdk`.

Manual:
```bash
git clone https://github.com/DPDK/dpdk.git ~/SURAJ/dpdk
cd ~/SURAJ/dpdk
meson setup build -Dexamples=all
ninja -C build
```
Tip (optionally static): `meson setup build -Ddefault_library=static` (you’ll need to copy PMDs too).

---

## Packaging & Shipping to Other Servers

### Option 1 — Ship your **app + tx_worker** and depend on system DPDK
- Build once, keep **runtime dependencies** consistent across hosts (same DPDK version).
- For mixed clusters, consider `--prepare-bnxt` and set `RTE_EAL_PMD_PATH` on bnxt/Thor2 hosts.

### Option 2 — Ship a **portable bundle** with DPDK `.so` + PMDs
Create a tarball with your app, `tx_worker`, and the needed PMD `.so` files:
```bash
# From the machine where you built
APP_DIR=/opt/myapp
OUT=myapp-dpdk-bundle.tar.gz
mkdir -p bundle/bin bundle/lib/dpdk bundle/scripts

# App + worker
cp -v $APP_DIR/myapp bundle/bin/
cp -v ./tx_worker/build/tx_worker bundle/bin/

# DPDK libs & PMDs (adjust libdir as needed)
LIBDIR=$(pkg-config --variable=libdir libdpdk 2>/dev/null || pkg-config --variable=libdir dpdk)
cp -v $LIBDIR/libdpdk*.so* bundle/lib/
cp -v $LIBDIR/dpdk/pmds-*/librte_net_mlx5.so* bundle/lib/dpdk/ 2>/dev/null || true
cp -v $LIBDIR/dpdk/pmds-*/librte_net_bnxt.so* bundle/lib/dpdk/ 2>/dev/null || true

# Helper wrapper to set RTE_EAL_PMD_PATH to this folder at runtime
cat > bundle/scripts/env.sh <<'EOS'
#!/usr/bin/env bash
THIS_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
export LD_LIBRARY_PATH="$THIS_DIR/../lib:$LD_LIBRARY_PATH"
export RTE_EAL_PMD_PATH="$THIS_DIR/../lib/dpdk"
EOS
chmod +x bundle/scripts/env.sh

tar -C bundle -czf "$OUT" .
echo "Created $OUT"
```

On the target server:
```bash
tar -xzf myapp-dpdk-bundle.tar.gz
source scripts/env.sh
# Run your app / tx_worker; for mlx5 use '-a <BDF>' without vfio
```

> For fully static bins you can explore `-Ddefault_library=static` and LTO, but PMDs are usually plugins (`.so`).

---

## Troubleshooting

- **“No DPDK ports.”**  
  For mlx5, ensure the NIC is present and **do not** bind to vfio. Run with `-a <BDF>`.  
  For bnxt/Thor2, ensure **IOMMU on** and device **bound to vfio-pci** (`--show-bound`).

- **Interface is still routing.**  
  `dpdk_start.sh` refuses to bind if routes reference the iface (safety). Re‑run with `--force-bind` **or** clear routes.

- **Hugepages**  
  If allocation fails, pre‑create and mount:
  ```bash
  sudo mkdir -p /mnt/huge
  echo 4096 | sudo tee /sys/devices/system/node/node0/hugepages/hugepages-2048kB/nr_hugepages
  sudo mount -t hugetlbfs nodev /mnt/huge
  ```

- **PPS lower than expected**  
  - Use `--pps 0` (flood) to test max path
  - Increase `--burst`, `--txd`, and pin lcores to same NUMA as the NIC
  - Consider `--no-udp-csum` (IPv4) and enable HW offloads
  - Check PCIe link width/speed and firmware offload settings

- **bnxt/Thor2 plugin tangles on mixed mlx5 hosts**  
  Build `tx_worker` and run with **BNXT‑only PMD** pack:  
  ```bash
  ./build_tx_worker.sh --prepare-bnxt --env-file ~/tx_env.sh
  source ~/tx_env.sh
  sudo "$TX_WORKER_BIN" -l 1-2 -n 4 -a <BDF> -- ...
  ```

---

## FAQ

**Q: What’s the difference between `dpdk_start.sh` and `build_tx_worker.sh`?**  
- `dpdk_start.sh` = **environment bring‑up** (deps, DPDK build, safe bind policy, hugepages, `testpmd`).  
- `build_tx_worker.sh` = **build the traffic generator** (`tx_worker`) and optional BNXT‑only PMD pack.

**Q: Can I reuse a prebuilt DPDK and `tx_worker` on all servers?**  
Yes, if the runtime environment (glibc, kernel, drivers, firmware) is compatible. Otherwise, ship a bundle with the `.so` files and PMDs (see packaging section).

**Q: Does this support Broadcom **Thor2**?**  
**Yes.** Thor/Thor2 are NetXtreme‑E (vendor **14e4**): the script binds them to **vfio‑pci** and the worker uses the **bnxt** PMD.

---

## License

Apache‑2.0 (or your project’s license).

---

## Credits

Thanks to the DPDK community and vendor PMD maintainers (mlx5, bnxt, ice, …).
