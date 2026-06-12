/*
 * rx_worker — DPDK receive-side counter accumulator.
 *
 * Operator-driven backstory (srv06, Jun 12 2026):
 *   - tx_worker pumped 24 Mpps from ens2f0np0 via DPDK
 *   - Cable terminated at ens2f1np1 (kernel-bound, mlx5_core)
 *   - Kernel netdev's RX backlog (default 1000) overflowed instantly:
 *       rx_dropped: 250,166,030 packets
 *   - Scapy AF_PACKET sniffer never saw a single one → netgen's
 *     stream rx_count stayed at 0 despite ~250M packets actually
 *     reaching the wire.
 *
 *   The kernel just can't drain that volume. rx_worker bypasses the
 *   kernel and reads frames directly from the PMD's RX queues —
 *   line-rate accurate, zero netdev involvement, hardware-accurate
 *   counters.
 *
 * Design intentionally mirrors tx_worker.c:
 *   - same getopt_long argv parsing style
 *   - same EAL bring-up pattern
 *   - same signal-driven shutdown via volatile sig_atomic_t
 *   - same per-second stdout JSON heartbeats so the netgen launcher
 *     can stream-parse counter updates without IPC magic
 *
 * Filtering:
 *   Default: count EVERY frame the port receives. Optional
 *   --vlan-filter / --dst-port-filter / --src-ip-filter narrow the
 *   match. Filters are software-level (post-RX); for true hardware
 *   steering the Mellanox bifurcated rte_flow path is future work
 *   (v0.5.106+).
 *
 * Output (stdout, one line per second + final summary on exit):
 *
 *   {"ts":1781274320,"rx_pkts":119600000,"rx_bytes":119600000000,
 *    "rx_dropped":42,"rx_pps":24210812,"rx_bps":193686496000,
 *    "matched_pkts":119599958}
 *
 *   {"final":true,"rx_pkts":...,"rx_bytes":...,"duration_s":5.02}
 *
 * The netgen launcher (utils/dpdk_rx_worker.py) tails stdout and
 * surfaces these in /api/streams/stats so the existing UI works
 * unchanged — DPDK RX just becomes "another stats source".
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <getopt.h>
#include <time.h>
#include <inttypes.h>
#include <arpa/inet.h>

#include <rte_eal.h>
#include <rte_ethdev.h>
#include <rte_mbuf.h>
#include <rte_mempool.h>
#include <rte_cycles.h>
#include <rte_ether.h>
#include <rte_ip.h>
#include <rte_udp.h>
#include <rte_tcp.h>

#define MBUF_POOL_SIZE 8191
#define MBUF_CACHE_SIZE 250
#define RX_QUEUE_SIZE 4096
#define BURST_SIZE 64
#define MAX_RX_QUEUES 16

/* Signal-safe stop flag — same idiom as tx_worker. */
static volatile sig_atomic_t g_stop = 0;
static void on_sig(int sig) { (void)sig; g_stop = 1; }

/* Per-queue counters live in this struct; one per lcore. Aggregated
 * by the main thread for stdout reporting. */
struct rx_counters {
    uint64_t pkts;
    uint64_t bytes;
    uint64_t matched_pkts;   /* passes filter */
    uint64_t matched_bytes;
} __attribute__((aligned(64)));

static struct rx_counters g_counters[MAX_RX_QUEUES];

/* Filter spec (applied per-packet on hot path; keep cheap). */
struct filter {
    int      vlan_id;        /* -1 = any */
    int      dst_port;       /* -1 = any (UDP/TCP) */
    int      src_port;       /* -1 = any */
    uint32_t src_ip_be;      /* 0  = any (network order) */
    uint32_t dst_ip_be;      /* 0  = any (network order) */
};
static struct filter g_filter = {-1, -1, -1, 0, 0};

/* Stream id for log prefix — operator correlates with netgen UI. */
static char g_stream_id[64] = "rx";

static void usage(const char *p)
{
    fprintf(stderr,
        "Usage: %s EAL... -- --stream-id STR [--vlan VID] [--dst-port P]\n"
        "                 [--src-port P] [--src-ip A.B.C.D] [--dst-ip A.B.C.D]\n"
        "                 [--duration SEC] [--rx-queues N]\n", p);
}

/* Apply filter against a single mbuf; return 1 if it matches. Hot
 * path — keep branch count low. */
static inline int packet_matches(struct rte_mbuf *m)
{
    if (g_filter.vlan_id < 0 && g_filter.dst_port < 0 &&
        g_filter.src_port < 0 && g_filter.src_ip_be == 0 &&
        g_filter.dst_ip_be == 0) {
        return 1;   /* No filter set → match everything */
    }

    struct rte_ether_hdr *eth = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);
    uint16_t eth_type = rte_be_to_cpu_16(eth->ether_type);
    uint16_t l3_off = sizeof(struct rte_ether_hdr);

    /* VLAN unwrap */
    if (eth_type == RTE_ETHER_TYPE_VLAN) {
        if (g_filter.vlan_id >= 0) {
            struct rte_vlan_hdr *v = (void *)(eth + 1);
            uint16_t vid = rte_be_to_cpu_16(v->vlan_tci) & 0x0FFF;
            if (vid != (uint16_t)g_filter.vlan_id) return 0;
        }
        struct rte_vlan_hdr *v = (void *)(eth + 1);
        eth_type = rte_be_to_cpu_16(v->eth_proto);
        l3_off += sizeof(struct rte_vlan_hdr);
    } else if (g_filter.vlan_id >= 0) {
        /* Filter says VLAN=N but frame has no VLAN tag → no match. */
        return 0;
    }

    if (eth_type != RTE_ETHER_TYPE_IPV4) {
        return 0;   /* Filter checks below all need IPv4. */
    }

    if (m->pkt_len < l3_off + sizeof(struct rte_ipv4_hdr)) return 0;
    struct rte_ipv4_hdr *ip = rte_pktmbuf_mtod_offset(
        m, struct rte_ipv4_hdr *, l3_off);

    if (g_filter.src_ip_be && ip->src_addr != g_filter.src_ip_be) return 0;
    if (g_filter.dst_ip_be && ip->dst_addr != g_filter.dst_ip_be) return 0;

    if (g_filter.dst_port >= 0 || g_filter.src_port >= 0) {
        uint16_t l4_off = l3_off + ((ip->version_ihl & 0x0F) * 4);
        if (m->pkt_len < l4_off + 4) return 0;
        /* Both UDP and TCP have src(2) + dst(2) as the first 4 bytes. */
        uint16_t *ports = rte_pktmbuf_mtod_offset(m, uint16_t *, l4_off);
        uint16_t src = rte_be_to_cpu_16(ports[0]);
        uint16_t dst = rte_be_to_cpu_16(ports[1]);
        if (g_filter.src_port >= 0 && src != (uint16_t)g_filter.src_port) return 0;
        if (g_filter.dst_port >= 0 && dst != (uint16_t)g_filter.dst_port) return 0;
    }
    return 1;
}

/* Worker lcore — drains one RX queue continuously. */
static int rx_worker_main(void *arg)
{
    uintptr_t q_idx = (uintptr_t)arg;
    if (q_idx >= MAX_RX_QUEUES) return -1;
    struct rx_counters *c = &g_counters[q_idx];
    struct rte_mbuf *bufs[BURST_SIZE];

    while (!g_stop) {
        uint16_t n = rte_eth_rx_burst(0, q_idx, bufs, BURST_SIZE);
        if (n == 0) {
            /* No packets — yield a touch so we don't spin the
             * core uselessly on idle. Real load: burst returns
             * BURST_SIZE every call. */
            rte_pause();
            continue;
        }
        for (uint16_t i = 0; i < n; i++) {
            c->pkts++;
            c->bytes += bufs[i]->pkt_len;
            if (packet_matches(bufs[i])) {
                c->matched_pkts++;
                c->matched_bytes += bufs[i]->pkt_len;
            }
            rte_pktmbuf_free(bufs[i]);
        }
    }
    return 0;
}

/* Aggregate per-queue counters into one tuple. Called from main
 * lcore — readers don't need locks because workers only ever write
 * monotonically increasing values, and we accept eventual
 * consistency on the per-second heartbeat. */
static void aggregate(uint64_t *pkts, uint64_t *bytes,
                      uint64_t *matched_pkts, uint64_t *matched_bytes,
                      int num_queues)
{
    *pkts = *bytes = *matched_pkts = *matched_bytes = 0;
    for (int i = 0; i < num_queues; i++) {
        *pkts += g_counters[i].pkts;
        *bytes += g_counters[i].bytes;
        *matched_pkts += g_counters[i].matched_pkts;
        *matched_bytes += g_counters[i].matched_bytes;
    }
}

int main(int argc, char **argv)
{
    int eal_args = rte_eal_init(argc, argv);
    if (eal_args < 0) {
        fprintf(stderr, "EAL init failed\n");
        return 1;
    }
    argc -= eal_args;
    argv += eal_args;

    /* Skip the `--` separator if present. */
    if (argc > 1 && strcmp(argv[1], "--") == 0) {
        argc--;
        argv++;
    }

    uint32_t duration_s = 0;
    int num_queues = 1;

    static struct option opts[] = {
        {"stream-id",       required_argument, 0, 1},
        {"vlan",            required_argument, 0, 2},
        {"dst-port",        required_argument, 0, 3},
        {"src-port",        required_argument, 0, 4},
        {"src-ip",          required_argument, 0, 5},
        {"dst-ip",          required_argument, 0, 6},
        {"duration",        required_argument, 0, 7},
        {"rx-queues",       required_argument, 0, 8},
        {"help",            no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int c;
    while ((c = getopt_long(argc, argv, "h", opts, NULL)) != -1) {
        switch (c) {
        case 1: strncpy(g_stream_id, optarg, sizeof(g_stream_id) - 1); break;
        case 2: g_filter.vlan_id = atoi(optarg); break;
        case 3: g_filter.dst_port = atoi(optarg); break;
        case 4: g_filter.src_port = atoi(optarg); break;
        case 5:
            if (inet_pton(AF_INET, optarg, &g_filter.src_ip_be) != 1) {
                fprintf(stderr, "bad --src-ip: %s\n", optarg);
                return 1;
            }
            break;
        case 6:
            if (inet_pton(AF_INET, optarg, &g_filter.dst_ip_be) != 1) {
                fprintf(stderr, "bad --dst-ip: %s\n", optarg);
                return 1;
            }
            break;
        case 7: duration_s = (uint32_t)atoi(optarg); break;
        case 8: num_queues = atoi(optarg);
                if (num_queues < 1) num_queues = 1;
                if (num_queues > MAX_RX_QUEUES) num_queues = MAX_RX_QUEUES;
                break;
        case 'h':
        default: usage(argv[0]); return c == 'h' ? 0 : 1;
        }
    }

    uint16_t nb_ports = rte_eth_dev_count_avail();
    if (nb_ports == 0) {
        fprintf(stderr, "no DPDK-attached port available — "
                "is the iface vfio-bound / bifurcated mode set up?\n");
        return 1;
    }

    /* Mempool sized for the burst + queue depth. */
    struct rte_mempool *mp = rte_pktmbuf_pool_create(
        "rxmbuf", MBUF_POOL_SIZE * num_queues, MBUF_CACHE_SIZE,
        0, RTE_MBUF_DEFAULT_BUF_SIZE, rte_socket_id());
    if (!mp) {
        fprintf(stderr, "mempool create failed\n");
        return 1;
    }

    /* Port config — RX-only, promisc on so we see every frame
     * regardless of dst MAC. */
    struct rte_eth_conf port_conf = {0};
    port_conf.rxmode.mq_mode = RTE_ETH_MQ_RX_RSS;
    port_conf.rx_adv_conf.rss_conf.rss_hf =
        RTE_ETH_RSS_IP | RTE_ETH_RSS_UDP | RTE_ETH_RSS_TCP;

    if (rte_eth_dev_configure(0, num_queues, 0, &port_conf) < 0) {
        fprintf(stderr, "eth_dev_configure failed\n");
        return 1;
    }
    for (int q = 0; q < num_queues; q++) {
        if (rte_eth_rx_queue_setup(
                0, q, RX_QUEUE_SIZE, rte_socket_id(),
                NULL, mp) < 0) {
            fprintf(stderr, "rx_queue_setup(q=%d) failed\n", q);
            return 1;
        }
    }
    if (rte_eth_dev_start(0) < 0) {
        fprintf(stderr, "eth_dev_start failed\n");
        return 1;
    }
    rte_eth_promiscuous_enable(0);

    /* Signal handlers — SIGINT/SIGTERM stop cleanly so the launcher
     * gets the final summary line. */
    signal(SIGINT,  on_sig);
    signal(SIGTERM, on_sig);

    /* Launch worker per RX queue on dedicated lcores. */
    unsigned int lcore = rte_get_next_lcore(-1, 1, 0);
    int launched = 0;
    for (int q = 0; q < num_queues; q++) {
        if (lcore == RTE_MAX_LCORE) {
            fprintf(stderr, "ran out of lcores; got %d of %d\n",
                    launched, num_queues);
            num_queues = launched;
            break;
        }
        rte_eal_remote_launch(rx_worker_main, (void *)(uintptr_t)q, lcore);
        launched++;
        lcore = rte_get_next_lcore(lcore, 1, 0);
    }
    fprintf(stderr, "rx_worker[%s] launched %d queue worker(s) on port 0\n",
            g_stream_id, launched);

    /* Stdout heartbeat loop. Emit one JSON line per second; the
     * launcher tails this and updates stream stats. */
    uint64_t prev_pkts = 0, prev_bytes = 0;
    uint64_t start_tsc = rte_get_tsc_cycles();
    uint64_t tsc_hz = rte_get_tsc_hz();
    time_t start_t = time(NULL);

    while (!g_stop) {
        sleep(1);
        if (g_stop) break;
        uint64_t pkts, bytes, matched_pkts, matched_bytes;
        aggregate(&pkts, &bytes, &matched_pkts, &matched_bytes, num_queues);

        /* xstats: lift dropped/imissed for the operator. */
        struct rte_eth_stats es;
        memset(&es, 0, sizeof(es));
        rte_eth_stats_get(0, &es);

        uint64_t d_pkts = pkts - prev_pkts;
        uint64_t d_bytes = bytes - prev_bytes;
        prev_pkts = pkts; prev_bytes = bytes;

        printf("{\"ts\":%lld,\"stream_id\":\"%s\","
               "\"rx_pkts\":%" PRIu64 ","
               "\"rx_bytes\":%" PRIu64 ","
               "\"rx_pps\":%" PRIu64 ","
               "\"rx_bps\":%" PRIu64 ","
               "\"matched_pkts\":%" PRIu64 ","
               "\"matched_bytes\":%" PRIu64 ","
               "\"hw_imissed\":%" PRIu64 ","
               "\"hw_ierrors\":%" PRIu64 ","
               "\"hw_rx_nombuf\":%" PRIu64 "}\n",
               (long long)time(NULL), g_stream_id,
               pkts, bytes, d_pkts, d_bytes * 8,
               matched_pkts, matched_bytes,
               es.imissed, es.ierrors, es.rx_nombuf);
        fflush(stdout);

        if (duration_s > 0 && (time(NULL) - start_t) >= (time_t)duration_s) {
            g_stop = 1;
        }
    }

    /* Wait for workers to drain before computing the final. */
    rte_eal_mp_wait_lcore();

    uint64_t pkts, bytes, matched_pkts, matched_bytes;
    aggregate(&pkts, &bytes, &matched_pkts, &matched_bytes, num_queues);
    double elapsed = (double)(rte_get_tsc_cycles() - start_tsc) / tsc_hz;

    struct rte_eth_stats final_es;
    memset(&final_es, 0, sizeof(final_es));
    rte_eth_stats_get(0, &final_es);

    printf("{\"final\":true,\"stream_id\":\"%s\","
           "\"rx_pkts\":%" PRIu64 ","
           "\"rx_bytes\":%" PRIu64 ","
           "\"matched_pkts\":%" PRIu64 ","
           "\"matched_bytes\":%" PRIu64 ","
           "\"duration_s\":%.3f,"
           "\"hw_imissed\":%" PRIu64 ","
           "\"hw_ierrors\":%" PRIu64 "}\n",
           g_stream_id, pkts, bytes, matched_pkts, matched_bytes, elapsed,
           final_es.imissed, final_es.ierrors);
    fflush(stdout);

    rte_eth_dev_stop(0);
    rte_eth_dev_close(0);
    return 0;
}
