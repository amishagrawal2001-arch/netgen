// tx_worker.c — DPDK UDP TX worker (multi-queue, fast, bnxt/mlx5 safe)
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
#include <rte_lcore.h>
#include <rte_launch.h>

#define MAX_TX_WORKERS 32

static volatile int keep_running = 1;
static void handle_sigint(int sig){ (void)sig; keep_running = 0; }

/* Per-worker context. One per TX queue. */
struct tx_worker_ctx {
    uint16_t port_id;
    uint16_t queue_id;
    uint64_t pps;             /* this worker's share (0 = flood) */
    uint32_t burst;
    uint32_t hdr_len;
    uint32_t payload_len;
    uint32_t l2_len, l3_len, l4_len;
    uint64_t tx_off;
    int      no_udp_csum;
    char     stream_id[64];
    const uint8_t *hdr_template;
    struct rte_mempool *mp;
    /* outputs (read by main, written by worker) */
    volatile uint64_t sent;
    volatile uint64_t dropped;
} __rte_cache_aligned;

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
      "                 --stream-id STR [--burst N] [--no-udp-csum] [--tx-cores N]\n", p);
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

/* Per-worker TX loop. Pinned to one lcore, drains one TX queue. */
static int tx_loop(void *arg){
    struct tx_worker_ctx *c = (struct tx_worker_ctx *)arg;

    const uint16_t port_id   = c->port_id;
    const uint16_t queue_id  = c->queue_id;
    const uint32_t burst     = c->burst;
    const uint32_t hdr_len   = c->hdr_len;
    const uint32_t payload_len = c->payload_len;
    const uint32_t l2_len    = c->l2_len;
    const uint32_t l3_len    = c->l3_len;
    const uint32_t l4_len    = c->l4_len;
    const uint64_t tx_off    = c->tx_off;
    const int no_udp_csum    = c->no_udp_csum;

    uint64_t tsc_hz = rte_get_timer_hz();
    if (tsc_hz == 0) tsc_hz = 1000000ULL;
    uint64_t cycles_per_burst = 0;
    if (c->pps > 0){
        double bursts_per_sec = (double)c->pps / (double)burst;
        if (bursts_per_sec < 1.0) bursts_per_sec = 1.0;
        cycles_per_burst = (uint64_t)((double)tsc_hz / bursts_per_sec);
        if (cycles_per_burst == 0) cycles_per_burst = 1;
    }

    uint64_t next_tsc = rte_get_timer_cycles();
    uint64_t seq = 0;
    uint64_t local_sent = 0, local_dropped = 0;

    while (keep_running){
        struct rte_mbuf *pkts[1024];
        uint16_t need = burst;
        if (rte_pktmbuf_alloc_bulk(c->mp, pkts, need) < 0){
            rte_delay_us_block(50);
            continue;
        }

        /* Build packets from template */
        for (uint16_t i=0; i<need; i++){
            struct rte_mbuf *m = pkts[i];
            uint8_t *p = (uint8_t*)rte_pktmbuf_append(m, hdr_len + payload_len);
            if (!p){ pkts[i]=NULL; continue; }

            rte_memcpy(p, c->hdr_template, hdr_len);

            /* payload signature — include queue id so each worker's pkts are unique */
            if (payload_len){
                uint8_t *pl = p + hdr_len;
                int n = snprintf((char*)pl, payload_len, "[%s/q%u#%" PRIu64 "]",
                                 c->stream_id, (unsigned)queue_id, seq++);
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

        /* Pacing — only if a target pps was set (pps=0 means line-rate flood) */
        if (c->pps > 0 && cycles_per_burst > 0){
            uint64_t now = rte_get_timer_cycles();
            if (now < next_tsc){
                uint64_t delta = next_tsc - now;
                uint64_t us = (tsc_hz ? (delta * 1000000ULL) / tsc_hz : 0);
                if (us > 0) rte_delay_us_block((unsigned)us);
            }
            next_tsc += cycles_per_burst;
        }

        /* Transmit */
        uint16_t nb = rte_eth_tx_burst(port_id, queue_id, pkts, need);
        local_sent += nb;
        for (uint16_t j=nb; j<need; j++){
            if (pkts[j]) rte_pktmbuf_free(pkts[j]);
            local_dropped++;
        }

        /* Publish counters (read by main thread for STAT lines) */
        c->sent = local_sent;
        c->dropped = local_dropped;
    }
    c->sent = local_sent;
    c->dropped = local_dropped;
    return 0;
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
    uint32_t tx_cores = 1;
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
        {"tx-cores",  required_argument, 0,14},
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
            case 14: tx_cores = (uint32_t)atoi(optarg);
                     if (tx_cores==0) tx_cores=1;
                     if (tx_cores>MAX_TX_WORKERS) tx_cores=MAX_TX_WORKERS;
                     break;
            default: usage(argv[0]); return 1;
        }
    }
    if (src_ip==0 || dst_ip==0){ usage("tx_worker"); return 1; }

    /* Clamp tx_cores to available worker lcores (main + N workers passed via -l) */
    uint32_t total_lcores = rte_lcore_count();
    if (total_lcores < 2){
        rte_exit(EXIT_FAILURE,
                 "Need at least 2 lcores via EAL -l (1 main + at least 1 worker), have %u\n",
                 total_lcores);
    }
    uint32_t worker_lcores = total_lcores - 1;
    if (tx_cores > worker_lcores){
        fprintf(stderr, "[tx_worker] tx-cores=%u clamped to %u available worker lcores\n",
                tx_cores, worker_lcores);
        tx_cores = worker_lcores;
    }

    /* Pick first available port (with EAL -a <BDF> you control which that is) */
    uint16_t port_id = RTE_MAX_ETHPORTS;
    RTE_ETH_FOREACH_DEV(port_id) { break; }
    if (port_id >= RTE_MAX_ETHPORTS || !rte_eth_dev_is_valid_port(port_id))
        rte_exit(EXIT_FAILURE, "No DPDK ports.\n");

    struct rte_eth_dev_info dev_info;
    rte_eth_dev_info_get(port_id, &dev_info);

    /* Clamp tx_cores to NIC's max TX queues */
    if (tx_cores > dev_info.max_tx_queues){
        fprintf(stderr, "[tx_worker] tx-cores=%u clamped to %u (NIC max_tx_queues)\n",
                tx_cores, dev_info.max_tx_queues);
        tx_cores = dev_info.max_tx_queues;
    }

    /* Enable HW checksum offloads if present */
    uint64_t want_tx_off = DEV_TX_OFFLOAD_IPV4_CKSUM | (no_udp_csum ? 0 : DEV_TX_OFFLOAD_UDP_CKSUM);
    uint64_t tx_off      = dev_info.tx_offload_capa & want_tx_off;

    struct rte_eth_conf port_conf;
    memset(&port_conf, 0, sizeof(port_conf));
    port_conf.txmode.offloads |= tx_off;

    const uint16_t nb_rxd = 1024, nb_txd = 1024;
    int rc = rte_eth_dev_configure(port_id, 1, (uint16_t)tx_cores, &port_conf);
    if (rc < 0) rte_exit(EXIT_FAILURE, "dev_configure: %d\n", rc);

    /* Mempool sized for all queues (per-lcore caches reduce contention) */
    unsigned mp_size = 8192U * tx_cores;
    if (mp_size < 16384U) mp_size = 16384U;
    struct rte_mempool *mp = rte_pktmbuf_pool_create("mbuf_pool",
        mp_size, 512, 0, RTE_MBUF_DEFAULT_BUF_SIZE, rte_eth_dev_socket_id(port_id));
    if (!mp) rte_exit(EXIT_FAILURE, "mbuf_pool\n");

    struct rte_eth_rxconf rxq_conf = dev_info.default_rxconf;
    rxq_conf.offloads &= dev_info.rx_offload_capa;
    rc = rte_eth_rx_queue_setup(port_id, 0, nb_rxd, rte_eth_dev_socket_id(port_id), &rxq_conf, mp);
    if (rc < 0) rte_exit(EXIT_FAILURE, "rxq_setup: %d\n", rc);

    struct rte_eth_txconf txq_conf = dev_info.default_txconf;
    txq_conf.offloads = (txq_conf.offloads | tx_off) & dev_info.tx_offload_capa;
    for (uint16_t q=0; q<(uint16_t)tx_cores; q++){
        rc = rte_eth_tx_queue_setup(port_id, q, nb_txd, rte_eth_dev_socket_id(port_id), &txq_conf);
        if (rc < 0) rte_exit(EXIT_FAILURE, "txq_setup q=%u: %d\n", (unsigned)q, rc);
    }

    rc = rte_eth_dev_start(port_id);
    if (rc < 0) rte_exit(EXIT_FAILURE, "dev_start: %d\n", rc);
    rte_eth_promiscuous_enable(port_id);

    /* Header lengths */
    const uint32_t l2_len = (vlan_id >= 0) ? sizeof(struct rte_ether_hdr)+sizeof(struct rte_vlan_hdr)
                                           : sizeof(struct rte_ether_hdr);
    const uint32_t l3_len = sizeof(struct rte_ipv4_hdr);
    const uint32_t l4_len = sizeof(struct rte_udp_hdr);
    uint32_t payload_len = (frame_size > (l2_len + l3_len + l4_len)) ?
                            frame_size - (l2_len + l3_len + l4_len) : 0;
    const uint32_t hdr_len = l2_len + l3_len + l4_len;

    /* Template buffer (read-only in workers) */
    static uint8_t hdr_template[256];
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

    /* Checksums (template). If HW offload, leave zero and set mbuf flags per packet. */
    if (tx_off & DEV_TX_OFFLOAD_IPV4_CKSUM) ip->hdr_checksum = 0; else ip->hdr_checksum = csum_ip4(ip);
    if (no_udp_csum) {
        udp->dgram_cksum = 0;
        tx_off &= ~DEV_TX_OFFLOAD_UDP_CKSUM;
    } else {
        if (tx_off & DEV_TX_OFFLOAD_UDP_CKSUM) udp->dgram_cksum = 0;
        else udp->dgram_cksum = csum_udp4(ip, udp);
    }

    signal(SIGINT, handle_sigint);

    /* Build per-worker contexts. Split pps target across workers; remainder spread to first few. */
    static struct tx_worker_ctx ctxs[MAX_TX_WORKERS];
    uint64_t pps_per       = (pps > 0) ? (pps / tx_cores) : 0;
    uint64_t pps_remainder = (pps > 0) ? (pps % tx_cores) : 0;

    for (uint32_t i=0; i<tx_cores; i++){
        struct tx_worker_ctx *c = &ctxs[i];
        memset(c, 0, sizeof(*c));
        c->port_id = port_id;
        c->queue_id = (uint16_t)i;
        c->pps = pps_per + (i < pps_remainder ? 1 : 0);
        c->burst = burst;
        c->hdr_len = hdr_len;
        c->payload_len = payload_len;
        c->l2_len = l2_len; c->l3_len = l3_len; c->l4_len = l4_len;
        c->tx_off = tx_off;
        c->no_udp_csum = no_udp_csum;
        snprintf(c->stream_id, sizeof(c->stream_id), "%s", stream_id);
        c->hdr_template = hdr_template;
        c->mp = mp;
    }

    /* Launch workers on worker lcores (skips main lcore) */
    uint32_t launched = 0;
    unsigned lcore_id;
    RTE_LCORE_FOREACH_WORKER(lcore_id){
        if (launched >= tx_cores) break;
        rc = rte_eal_remote_launch(tx_loop, &ctxs[launched], lcore_id);
        if (rc != 0){
            fprintf(stderr, "[tx_worker] remote_launch worker=%u lcore=%u rc=%d\n",
                    launched, lcore_id, rc);
            keep_running = 0;
            break;
        }
        launched++;
    }
    if (launched == 0) rte_exit(EXIT_FAILURE, "Failed to launch any worker\n");

    /* Main thread: aggregate stats, enforce duration */
    uint64_t tsc_hz = rte_get_timer_hz();
    if (tsc_hz == 0) tsc_hz = 1000000ULL;
    uint64_t start_tsc = rte_get_timer_cycles();
    uint64_t last_print = start_tsc;

    while (keep_running){
        if (duration_sec){
            uint64_t elapsed = rte_get_timer_cycles() - start_tsc;
            if (elapsed >= duration_sec * tsc_hz){
                keep_running = 0;
                break;
            }
        }
        rte_delay_us_block(100000); /* 100 ms — main thread is not on the data path */

        uint64_t now = rte_get_timer_cycles();
        if ((now - last_print) >= tsc_hz){
            uint64_t s = 0, d = 0;
            for (uint32_t i=0; i<launched; i++){
                s += ctxs[i].sent;
                d += ctxs[i].dropped;
            }
            printf("STAT stream=%s tx=%" PRIu64 " drop=%" PRIu64 " frame=%u pps_target=%" PRIu64 " burst=%u tx_cores=%u offload=0x%llx\n",
                   stream_id, s, d, frame_size, pps, burst, launched, (unsigned long long)tx_off);
            fflush(stdout);
            last_print = now;
        }
    }

    /* Wait for workers to drain */
    rte_eal_mp_wait_lcore();

    uint64_t total_sent = 0, total_dropped = 0;
    for (uint32_t i=0; i<launched; i++){
        total_sent += ctxs[i].sent;
        total_dropped += ctxs[i].dropped;
    }
    printf("STAT_FINAL stream=%s tx=%" PRIu64 " drop=%" PRIu64 " tx_cores=%u\n",
           stream_id, total_sent, total_dropped, launched);
    fflush(stdout);

    rte_eth_dev_stop(port_id);
    rte_eth_dev_close(port_id);
    return 0;
}
