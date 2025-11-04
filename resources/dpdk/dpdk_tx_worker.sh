#!/usr/bin/env bash
# =============================================================================
# build_tx_worker.sh — DPDK UDP TX worker builder (fast path, mlx5/bnxt-safe)
#
#  • Emits/updates tx_worker.c (use --rewrite-src to overwrite an existing one)
#  • Meson+ninja build (and optional install)
#  • Optional BNXT-only PMD pack (to avoid mlx5 plugin tangles on mixed hosts)
#
#  TX worker highlights:
#    - Bulk mbuf alloc + header template memcpy
#    - Optional HW checksum offloads (IPv4 + UDP), or SW fallback
#    - Optional --no-udp-csum (legal for IPv4; sets UDP csum=0)
#    - Pacing by burst using TSC (target PPS) with partial-burst safety
#    - Signature in payload: "[<stream-id>#<seq>]" for your RX tracker
#
#  Quick build:
#    ./build_tx_worker.sh --install-deps --rewrite-src
#
#  Quick run example (mlx5, ConnectX):
#    sudo ./tx_worker/build/tx_worker -l 1-2 -n 4 -a 0000:b5:00.0 -- \
#      --src-mac 5C:25:73:3F:36:2E --dst-mac 02:00:00:00:00:00 \
#      --src-ip 192.168.100.10 --dst-ip 192.168.100.11 \
#      --src-port 1234 --dst-port 4791 --size 64 \
#      --pps 1000 --duration 30 --stream-id demo
# =============================================================================

set -euo pipefail

show_help() {
  cat <<'EOF'
Usage:
  build_tx_worker.sh [options]

Options:
  --out-dir <dir>         Working directory (default: ./tx_worker)
  --install-deps          Install build deps (meson, ninja, pkg-config, etc.)
  --dpdk-tree <path>      Path to a local DPDK build tree (find libdpdk.pc)
  --dpdk-pc-dir <dir>     Explicit directory containing libdpdk.pc
  --install               Run "ninja install" into --prefix (default /usr/local)
  --prefix <dir>          Install prefix (default /usr/local)
  --prepare-bnxt          Copy only BNXT PMD (librte_net_bnxt.so*) to a folder
  --bnxt-pmds-dir <dir>   BNXT PMD output dir (default: ~/dpdk_pmds_bnxt)
  --env-file <path>       Write env exports (TX_WORKER_BIN, RTE_EAL_PMD_PATH)
  --rewrite-src           Overwrite tx_worker.c even if it exists
  -h, --help              Show this help

Typical:
  ./build_tx_worker.sh --install-deps --rewrite-src
  ./build_tx_worker.sh --dpdk-tree ~/SURAJ/dpdk
EOF
}

# ---------- defaults ----------
OUT_DIR="./tx_worker"
PREFIX="/usr/local"
DPDK_TREE=""
DPDK_PC_DIR=""
DO_INSTALL_DEPS="0"
DO_INSTALL="0"
PREPARE_BNXT="0"
BNXT_PMDS_DIR="$HOME/dpdk_pmds_bnxt"
ENV_FILE=""
REWRITE_SRC="0"

# ---------- args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)        OUT_DIR="${2:?}"; shift 2 ;;
    --prefix)         PREFIX="${2:?}"; shift 2 ;;
    --dpdk-tree)      DPDK_TREE="${2:?}"; shift 2 ;;
    --dpdk-pc-dir)    DPDK_PC_DIR="${2:?}"; shift 2 ;;
    --install-deps)   DO_INSTALL_DEPS="1"; shift ;;
    --install)        DO_INSTALL="1"; shift ;;
    --prepare-bnxt|--isolate-bnxt) PREPARE_BNXT="1"; shift ;;
    --bnxt-pmds-dir)  BNXT_PMDS_DIR="${2:?}"; shift 2 ;;
    --env-file)       ENV_FILE="${2:?}"; shift 2 ;;
    --rewrite-src)    REWRITE_SRC="1"; shift ;;
    -h|--help)        show_help; exit 0 ;;
    *) echo "Unknown arg: $1"; echo; show_help; exit 1 ;;
  esac
done

log() { echo -e "\033[1;34m[build]\033[0m $*"; }
warn(){ echo -e "\033[1;33m[warn]\033[0m  $*"; }
err() { echo -e "\033[1;31m[err]\033[0m   $*" >&2; }

# ---------- deps ----------
if [[ "$DO_INSTALL_DEPS" == "1" ]]; then
  if command -v apt-get >/dev/null 2>&1; then
    log "Installing build deps (apt)…"
    sudo apt-get update -y
    sudo apt-get install -y build-essential meson ninja-build pkg-config \
                            libnuma-dev libelf-dev libpcap-dev
  elif command -v dnf >/dev/null 2>&1; then
    log "Installing build deps (dnf)…"
    sudo dnf install -y @development-tools meson ninja-build pkgconf-pkg-config \
                         numactl numactl-libs elfutils-libelf-devel libpcap-devel
  elif command -v yum >/dev/null 2>&1; then
    log "Installing build deps (yum)…"
    sudo yum groupinstall -y "Development Tools"
    sudo yum install -y meson ninja-build pkgconfig numactl numactl-libs \
                        elfutils-libelf-devel libpcap-devel
  else
    warn "Unknown package manager; install meson, ninja, pkg-config, libnuma-dev, libelf-dev, libpcap-dev"
  fi
fi
command -v meson >/dev/null || { err "meson not found. Use --install-deps."; exit 1; }
command -v ninja >/dev/null || { err "ninja not found. Use --install-deps."; exit 1; }
command -v pkg-config >/dev/null || { err "pkg-config not found. Use --install-deps."; exit 1; }

# ---------- workspace ----------
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

# ---------- write/overwrite tx_worker.c ----------
if [[ ! -f tx_worker.c || "$REWRITE_SRC" == "1" ]]; then
  log "Emitting tx_worker.c (fast path; HW cksum offloads when available)…"
  cat > tx_worker.c <<'EOF_C'
// tx_worker.c — DPDK UDP TX worker (fast, bnxt/mlx5 safe)
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <inttypes.h>
#include <getopt.h>
#include <arpa/inet.h>

#include <rte_eal.h>
#include <rte_ethdev.h>
#include <rte_mbuf.h>
#include <rte_cycles.h>
#include <rte_ether.h>
#include <rte_ip.h>
#include <rte_udp.h>
#include <rte_memcpy.h>

static volatile int keep_running = 1;
static void handle_sigint(int sig){ (void)sig; keep_running = 0; }

static int parse_mac(const char *s, struct rte_ether_addr *a){
    unsigned int b[6];
    if (sscanf(s, "%02x:%02x:%02x:%02x:%02x:%02x",
               &b[0],&b[1],&b[2],&b[3],&b[4],&b[5]) != 6) return -1;
    for (int i=0;i<6;i++) a->addr_bytes[i] = (uint8_t)b[i];
    return 0;
}

static void usage(const char *p){
    fprintf(stderr,
      "Usage: %s EAL... -- --src-mac XX:.. --dst-mac YY:.. --src-ip A.B.C.D --dst-ip E.F.G.H \n"
      "                 [--src-port P] [--dst-port P] [--vlan VID] --size BYTES --pps N --duration SEC \n"
      "                 --stream-id STR [--burst N] [--no-udp-csum]\n", p);
}

static uint16_t csum_ip4(void *iphdr){
    struct rte_ipv4_hdr *ip = (struct rte_ipv4_hdr*)iphdr;
    ip->hdr_checksum = 0;
    return rte_ipv4_cksum(ip);
}
static uint16_t csum_udp4(struct rte_ipv4_hdr *ip, struct rte_udp_hdr *udp){
    udp->dgram_cksum = 0;
    return rte_ipv4_udptcp_cksum(ip, (void*)udp);
}

int main(int argc, char **argv){
    int eal_argc = rte_eal_init(argc, argv);
    if (eal_argc < 0){ rte_exit(EXIT_FAILURE, "EAL init failed\n"); }
    argc -= eal_argc; argv += eal_argc;

    struct rte_ether_addr src_mac = {0}, dst_mac = {0};
    uint32_t src_ip = 0, dst_ip = 0;
    uint16_t src_port = 1234, dst_port = 4791;
    int vlan_id = -1, no_udp_csum = 0;
    uint32_t frame_size = 64, burst = 64;
    uint64_t pps = 0, duration_sec = 0;
    char stream_id[64] = "stream";

    static struct option long_opts[] = {
        {"src-mac",   required_argument, 0, 1},
        {"dst-mac",   required_argument, 0, 2},
        {"src-ip",    required_argument, 0, 3},
        {"dst-ip",    required_argument, 0, 4},
        {"src-port",  required_argument, 0, 5},
        {"dst-port",  required_argument, 0, 6},
        {"vlan",      required_argument, 0, 7},
        {"size",      required_argument, 0, 8},
        {"pps",       required_argument, 0, 9},
        {"duration",  required_argument, 0,10},
        {"stream-id", required_argument, 0,11},
        {"no-udp-csum", no_argument,     0,12},
        {"burst",     required_argument, 0,13},
        {0,0,0,0}
    };
    int opt, idx;
    while((opt=getopt_long(argc, argv, "", long_opts, &idx)) != -1){
        switch(opt){
            case 1: if(parse_mac(optarg,&src_mac)) {usage(argv[0]); return 1;} break;
            case 2: if(parse_mac(optarg,&dst_mac)) {usage(argv[0]); return 1;} break;
            case 3: if(inet_pton(AF_INET,optarg,&src_ip)!=1){usage(argv[0]); return 1;} break;
            case 4: if(inet_pton(AF_INET,optarg,&dst_ip)!=1){usage(argv[0]); return 1;} break;
            case 5: src_port = (uint16_t)atoi(optarg); break;
            case 6: dst_port = (uint16_t)atoi(optarg); break;
            case 7: vlan_id  = atoi(optarg); break;
            case 8: frame_size = (uint32_t)atoi(optarg); if(frame_size<60) frame_size=60; break;
            case 9: pps = strtoull(optarg,NULL,10); break;
            case 10: duration_sec = strtoull(optarg,NULL,10); break;
            case 11: snprintf(stream_id,sizeof(stream_id),"%s",optarg); break;
            case 12: no_udp_csum = 1; break;
            case 13: burst = (uint32_t)atoi(optarg); if (burst==0) burst=1; if (burst>1024) burst=1024; break;
            default: usage(argv[0]); return 1;
        }
    }
    if (src_ip==0 || dst_ip==0){ usage("tx_worker"); return 1; }

    // Pick first available port (with EAL -a <BDF> you control which that is)
    uint16_t port_id = RTE_MAX_ETHPORTS;
    RTE_ETH_FOREACH_DEV(port_id) { break; }
    if (port_id >= RTE_MAX_ETHPORTS || !rte_eth_dev_is_valid_port(port_id))
        rte_exit(EXIT_FAILURE, "No DPDK ports.\n");

    struct rte_eth_dev_info dev_info;
    rte_eth_dev_info_get(port_id, &dev_info);

    // Enable HW checksum offloads if present
    uint64_t want_tx_off = DEV_TX_OFFLOAD_IPV4_CKSUM | (no_udp_csum ? 0 : DEV_TX_OFFLOAD_UDP_CKSUM);
    uint64_t tx_off      = dev_info.tx_offload_capa & want_tx_off;

    struct rte_eth_conf port_conf;
    memset(&port_conf, 0, sizeof(port_conf));
    port_conf.txmode.offloads |= tx_off;

    const uint16_t nb_rxd = 1024, nb_txd = 1024;
    int rc = rte_eth_dev_configure(port_id, 1, 1, &port_conf);
    if (rc < 0) rte_exit(EXIT_FAILURE, "dev_configure: %d\n", rc);

    struct rte_mempool *mp = rte_pktmbuf_pool_create("mbuf_pool",
        16384, 512, 0, RTE_MBUF_DEFAULT_BUF_SIZE, rte_eth_dev_socket_id(port_id));
    if (!mp) rte_exit(EXIT_FAILURE, "mbuf_pool\n");

    struct rte_eth_rxconf rxq_conf = dev_info.default_rxconf;
    rxq_conf.offloads &= dev_info.rx_offload_capa;
    rc = rte_eth_rx_queue_setup(port_id, 0, nb_rxd, rte_eth_dev_socket_id(port_id), &rxq_conf, mp);
    if (rc < 0) rte_exit(EXIT_FAILURE, "rxq_setup: %d\n", rc);

    struct rte_eth_txconf txq_conf = dev_info.default_txconf;
    txq_conf.offloads = (txq_conf.offloads | tx_off) & dev_info.tx_offload_capa;
    rc = rte_eth_tx_queue_setup(port_id, 0, nb_txd, rte_eth_dev_socket_id(port_id), &txq_conf);
    if (rc < 0) rte_exit(EXIT_FAILURE, "txq_setup: %d\n", rc);

    rc = rte_eth_dev_start(port_id);
    if (rc < 0) rte_exit(EXIT_FAILURE, "dev_start: %d\n", rc);
    rte_eth_promiscuous_enable(port_id);

    // Header lengths
    const uint32_t l2_len = (vlan_id >= 0) ? sizeof(struct rte_ether_hdr)+sizeof(struct rte_vlan_hdr)
                                           : sizeof(struct rte_ether_hdr);
    const uint32_t l3_len = sizeof(struct rte_ipv4_hdr);
    const uint32_t l4_len = sizeof(struct rte_udp_hdr);
    uint32_t payload_len = (frame_size > (l2_len + l3_len + l4_len)) ?
                            frame_size - (l2_len + l3_len + l4_len) : 0;
    const uint32_t hdr_len = l2_len + l3_len + l4_len;

    // Template buffer
    uint8_t hdr_template[256];
    if (hdr_len > sizeof(hdr_template)) rte_exit(EXIT_FAILURE, "hdr too large\n");
    memset(hdr_template, 0, hdr_len);

    struct rte_ether_hdr *eth = (struct rte_ether_hdr*)hdr_template;
    eth->src_addr = src_mac;
    eth->dst_addr = dst_mac;
    if (vlan_id >= 0){
        eth->ether_type = rte_cpu_to_be_16(RTE_ETHER_TYPE_VLAN);
        struct rte_vlan_hdr *vh = (struct rte_vlan_hdr*)((uint8_t*)hdr_template + sizeof(*eth));
        vh->vlan_tci = rte_cpu_to_be_16((uint16_t)vlan_id);
        vh->eth_proto = rte_cpu_to_be_16(RTE_ETHER_TYPE_IPV4);
    } else {
        eth->ether_type = rte_cpu_to_be_16(RTE_ETHER_TYPE_IPV4);
    }

    struct rte_ipv4_hdr *ip = (struct rte_ipv4_hdr*)(hdr_template + l2_len);
    ip->version_ihl = (4 << 4) | (sizeof(*ip) / 4);
    ip->total_length = rte_cpu_to_be_16(l3_len + l4_len + payload_len);
    ip->time_to_live = 64;
    ip->next_proto_id = IPPROTO_UDP;
    ip->src_addr = src_ip;
    ip->dst_addr = dst_ip;

    struct rte_udp_hdr *udp = (struct rte_udp_hdr*)(hdr_template + l2_len + l3_len);
    udp->src_port = rte_cpu_to_be_16(src_port);
    udp->dst_port = rte_cpu_to_be_16(dst_port);
    udp->dgram_len = rte_cpu_to_be_16(l4_len + payload_len);

    // Checksums (template). If HW offload, leave zero and set mbuf flags per packet.
    if (tx_off & DEV_TX_OFFLOAD_IPV4_CKSUM) ip->hdr_checksum = 0; else ip->hdr_checksum = csum_ip4(ip);
    if (no_udp_csum) {
        udp->dgram_cksum = 0;
        // ensure we DO NOT set UDP_CKSUM offload in mbuf when no_udp_csum=1
        tx_off &= ~DEV_TX_OFFLOAD_UDP_CKSUM;
    } else {
        if (tx_off & DEV_TX_OFFLOAD_UDP_CKSUM) udp->dgram_cksum = 0;
        else udp->dgram_cksum = csum_udp4(ip, udp);
    }

    // Pace
    uint64_t tsc_hz = rte_get_timer_hz();
    if (tsc_hz == 0) tsc_hz = 1000000ULL;
    uint64_t cycles_per_burst = 0;
    if (pps > 0){
        double bursts_per_sec = (double)pps / (double)burst;
        if (bursts_per_sec < 1.0) bursts_per_sec = 1.0;
        cycles_per_burst = (uint64_t)((double)tsc_hz / bursts_per_sec);
        if (cycles_per_burst == 0) cycles_per_burst = 1;
    }

    signal(SIGINT, handle_sigint);

    uint64_t start_tsc = rte_get_timer_cycles();
    uint64_t next_tsc  = start_tsc;
    uint64_t sent = 0, dropped = 0, seq = 0, last_print = start_tsc;

    while (keep_running){
        if (duration_sec){
            uint64_t elapsed = rte_get_timer_cycles() - start_tsc;
            if (elapsed >= duration_sec * tsc_hz) break;
        }

        struct rte_mbuf *pkts[1024];
        uint16_t need = burst;
        if (rte_pktmbuf_alloc_bulk(mp, pkts, need) < 0){
            rte_delay_us_block(50);
            continue;
        }

        // Build packets from template
        for (uint16_t i=0; i<need; i++){
            struct rte_mbuf *m = pkts[i];
            uint8_t *p = (uint8_t*)rte_pktmbuf_append(m, hdr_len + payload_len);
            if (!p){ pkts[i]=NULL; continue; }

            rte_memcpy(p, hdr_template, hdr_len);

            // payload signature
            if (payload_len){
                uint8_t *pl = p + hdr_len;
                int n = snprintf((char*)pl, payload_len, "[%s#%" PRIu64 "]", stream_id, seq++);
                if (n < 0) n = 0;
                if ((uint32_t)n < payload_len) memset(pl + n, 0, payload_len - (uint32_t)n);
            }

            if (tx_off & DEV_TX_OFFLOAD_IPV4_CKSUM) {
                m->ol_flags |= RTE_MBUF_F_TX_IPV4 | RTE_MBUF_F_TX_IP_CKSUM;
                m->l2_len = l2_len; m->l3_len = l3_len; m->l4_len = l4_len;
            }
            if ((tx_off & DEV_TX_OFFLOAD_UDP_CKSUM) && !no_udp_csum) {
                m->ol_flags |= RTE_MBUF_F_TX_UDP_CKSUM;
                m->l2_len = l2_len; m->l3_len = l3_len; m->l4_len = l4_len;
            }
        }

        if (pps > 0 && cycles_per_burst > 0){
            uint64_t now = rte_get_timer_cycles();
            if (now < next_tsc){
                uint64_t delta = next_tsc - now;
                uint64_t us = (tsc_hz ? (delta * 1000000ULL) / tsc_hz : 0);
                if (us > 0) rte_delay_us_block((unsigned)us);
            }
            next_tsc += cycles_per_burst;
        }

        // Transmit
        uint16_t nb = rte_eth_tx_burst(port_id, 0, pkts, need);
        sent += nb;
        for (uint16_t j=nb; j<need; j++){
            if (pkts[j]) rte_pktmbuf_free(pkts[j]);
            dropped++;
        }

        uint64_t now = rte_get_timer_cycles();
        if ((now - last_print) >= tsc_hz){
            printf("STAT stream=%s tx=%" PRIu64 " drop=%" PRIu64 " frame=%u pps_target=%" PRIu64 " burst=%u offload=0x%llx\n",
                   stream_id, sent, dropped, frame_size, pps, burst, (unsigned long long)tx_off);
            fflush(stdout);
            last_print = now;
        }
    }

    printf("STAT_FINAL stream=%s tx=%" PRIu64 " drop=%" PRIu64 "\n", stream_id, sent, dropped);
    fflush(stdout);

    rte_eth_dev_stop(port_id);
    rte_eth_dev_close(port_id);
    return 0;
}
EOF_C
fi

# ---------- meson.build ----------
if [[ ! -f meson.build ]]; then
  log "Creating meson.build…"
  cat > meson.build <<'EOF_M'
project('tx_worker', 'c', version: '0.2', default_options: ['c_std=c11'])
dpdk = dependency('libdpdk', required: false)
if not dpdk.found()
  dpdk = dependency('dpdk')
endif
executable('tx_worker', 'tx_worker.c', dependencies: dpdk, install: true)
EOF_M
fi

# ---------- locate libdpdk.pc ----------
FOUND_PC="0"
if pkg-config --exists libdpdk || pkg-config --exists dpdk; then
  FOUND_PC="1"
else
  if [[ -n "$DPDK_PC_DIR" ]]; then
    export PKG_CONFIG_PATH="$DPDK_PC_DIR:$PKG_CONFIG_PATH"
  elif [[ -n "$DPDK_TREE" ]]; then
    PC=$(find "$DPDK_TREE"/build -name 'libdpdk.pc' -print -quit || true)
    if [[ -n "$PC" ]]; then
      export PKG_CONFIG_PATH="$(dirname "$PC"):$PKG_CONFIG_PATH"
    fi
  fi
  if pkg-config --exists libdpdk || pkg-config --exists dpdk; then
    FOUND_PC="1"
  fi
fi
[[ "$FOUND_PC" == "1" ]] || { err "Could not locate libdpdk via pkg-config. Use --dpdk-tree or --dpdk-pc-dir."; exit 1; }

# ---------- build ----------
if [[ ! -d build ]]; then
  log "Meson setup (prefix=$PREFIX)…"
  meson setup build --prefix "$PREFIX"
else
  log "Meson reconfigure…"
  meson setup build --prefix "$PREFIX" --reconfigure
fi

log "Building…"
ninja -C build

if [[ "$DO_INSTALL" == "1" ]]; then
  log "Installing to $PREFIX…"
  sudo ninja -C build install
  sudo ldconfig || true
fi

# ---------- BNXT-only PMD pack (optional) ----------
if [[ "$PREPARE_BNXT" == "1" ]]; then
  log "Preparing BNXT-only PMD directory at: $BNXT_PMDS_DIR"
  mkdir -p "$BNXT_PMDS_DIR"
  FOUND="0"
  PMD_DIRS=$(ls -d /usr/local/lib*/x86_64-linux-gnu/dpdk/pmds-* /usr/local/lib*/dpdk/pmds-* 2>/dev/null | sort -V || true)
  for d in $PMD_DIRS; do
    if ls "$d"/librte_net_bnxt.so* >/dev/null 2>&1; then
      cp -v "$d"/librte_net_bnxt.so* "$BNXT_PMDS_DIR"/
      FOUND="1"; break
    fi
  done
  if [[ "$FOUND" != "1" ]]; then
    libdir="$(pkg-config --variable=libdir libdpdk 2>/dev/null || pkg-config --variable=libdir dpdk 2>/dev/null || true)"
    if [[ -n "${libdir:-}" ]]; then
      PC_DIRS=$(ls -d "$libdir"/dpdk/pmds-* 2>/dev/null | sort -V || true)
      for d in $PC_DIRS; do
        if ls "$d"/librte_net_bnxt.so* >/dev/null 2>&1; then
          cp -v "$d"/librte_net_bnxt.so* "$BNXT_PMDS_DIR"/
          FOUND="1"; break
        fi
      done
    fi
  fi
  if [[ "$FOUND" != "1" ]]; then
    BNXT=$(find /usr/local /usr -name 'librte_net_bnxt.so*' 2>/dev/null | head -n1 || true)
    if [[ -n "${BNXT:-}" ]]; then
      cp -v "$(dirname "$BNXT")"/librte_net_bnxt.so* "$BNXT_PMDS_DIR"/
      FOUND="1"
    fi
  fi
  if [[ "$FOUND" == "1" ]]; then
    log "BNXT PMD ready in $BNXT_PMDS_DIR"
  else
    warn "BNXT PMD not found on this host."
  fi
fi

# ---------- env file ----------
BIN="$(readlink -f build/tx_worker)"
if [[ -n "$ENV_FILE" ]]; then
  log "Writing env file: $ENV_FILE"
  {
    echo "export TX_WORKER_BIN=\"$BIN\""
    if [[ "$PREPARE_BNXT" == "1" ]]; then
      echo "export RTE_EAL_PMD_PATH=\"$BNXT_PMDS_DIR\""
    fi
  } > "$ENV_FILE"
  echo "source \"$ENV_FILE\"   # exports TX_WORKER_BIN${PREPARE_BNXT:+ and RTE_EAL_PMD_PATH}"
fi

log "Done. Binary: $BIN"
echo "Run examples:"
echo "  sudo $BIN -l 1-2 -n 4 -a 0000:b5:00.0 -- \\"
echo "    --src-mac 5C:25:73:3F:36:2E --dst-mac 02:00:00:00:00:00 --src-ip 192.168.100.10 --dst-ip 192.168.100.11 \\"
echo "    --src-port 1234 --dst-port 4791 --size 64 --pps 1000 --duration 30 --stream-id demo"
echo "  # Line-ish rate small frames:"
echo "  sudo $BIN -l 1-2 -n 4 -a 0000:b5:00.0 -- --src-mac 5C:25:73:3F:36:2E --dst-mac 02:00:00:00:00:00 \\"
echo "    --src-ip 192.168.100.10 --dst-ip 192.168.100.11 --src-port 1234 --dst-port 4791 --size 64 --pps 0 --stream-id flood"
