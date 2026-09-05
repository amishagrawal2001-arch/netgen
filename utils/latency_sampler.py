# utils/latency_sampler.py
"""
One-way latency sampler — RX side of the netgen latency story.

When a stream is sent with `--enable-timestamps` (or stream_data flag
`enable_timestamps: true`), tx_worker embeds a 16-byte NLAT header at
the start of each UDP payload:

    offset 0..3  : magic = 0x4e4c4154 ("NLAT", big-endian)
    offset 4..7  : reserved (zero)
    offset 8..15 : tx_ns   = sender's CLOCK_MONOTONIC nanoseconds
                             (big-endian uint64)

This module sniffs an interface, decodes the NLAT header on each UDP
packet, and computes
    latency_ns = time.monotonic_ns() - tx_ns

It maintains a rolling sample window and computes min / avg / p50 /
p99 / max + sample count + frames-with-magic / frames-without ratio.

v0.5.260 (audit latency-3): Same-host loopback (TX iface == RX iface
or two ports of the same NIC) gives accurate one-way latency directly.

Cross-host measurement is currently NOT SUPPORTED. `CLOCK_MONOTONIC`
is a per-host free-running counter since kernel boot; PTP4L /
phc2sys / ntpd never touch it. The delta between two hosts'
`CLOCK_MONOTONIC` reads is `(rx_boot_epoch - tx_boot_epoch) +
true_latency + drift` — the boot-epoch term is unbounded and
unfixable. A cross-host result carries no semantic meaning.
Switching TX + RX to `CLOCK_TAI` (PTP-syncable via `phc2sys -O -37`)
would enable it; that's a future ship.

Two usage modes:

1.  **Library**: `LatencySampler(iface=...).start()` runs a background
    thread; consumers call `.stats()` to read current aggregates.

2.  **CLI**: `python3 -m utils.latency_sampler --iface enp181s0f0np0`
    prints stats every second.

Implementation deliberately uses scapy + libpcap rather than DPDK
RX. That tops out around ~1 Mpps on a modern host but it's enough
for latency sampling — you don't need every frame, you need a
statistically meaningful sample.
"""
from __future__ import annotations

import argparse
import logging
import re
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Dict, Optional

LOG = logging.getLogger("latency_sampler")

# Wire format of the timestamp header. See tx_worker.c struct nlat_hdr.
NLAT_MAGIC = 0x4e4c4154  # "NLAT"
NLAT_HDR_LEN = 16
NLAT_STRUCT = ">IIQ"     # magic, reserved, tx_ns

# v0.3.5: per-stream attribution regex. The same signature the v0.3.4
# RX sniffer matches against (Scapy: `[<sid>#<seq>]`, DPDK:
# `[<sid>/q<queue>#<seq>]`) carries the stream-id we need to bucket
# latency samples per stream. Pre-v0.3.5 the sampler accumulated all
# NLAT-tagged packets into one histogram per-interface — two
# concurrent streams on the same iface produced one mixed histogram
# the operator couldn't separate.
#
# Capture group: the stream-id token between `[` and (`/q...#` or
# bare `#`). Non-greedy because stream IDs can contain anything but
# `[`, `]`, `/`, `#`, and whitespace.
_SIG_EXTRACT_RE = re.compile(rb"\[([^/\[\]#\s]+)(?:/q\d+)?#")


@dataclass
class LatencyStats:
    """Rolling stats over the last `window_size` samples."""
    samples_seen: int = 0
    samples_decoded: int = 0     # frames with valid NLAT magic
    samples_skipped: int = 0     # non-NLAT or too-short payloads
    # v0.5.260 (audit latency-4): separate "no NLAT magic" from
    # "NLAT valid but latency out of range" so operators can tell
    # clock skew from wrong-port / wrong-magic. Pre-fix both were
    # bucketed as samples_skipped — cross-host runs (bad clocks)
    # looked identical to "no NLAT frames arriving" (wrong port).
    samples_impossible_latency: int = 0
    _latencies: deque = field(default_factory=lambda: deque(maxlen=10000))

    def add(self, latency_ns: int):
        self._latencies.append(latency_ns)
        self.samples_decoded += 1

    def reset(self):
        """v0.5.260 (audit latency-6): clear the rolling window +
        counters so a caller (e.g. RFC 2544 per-iteration snapshot)
        can measure a fresh sample without the previous iteration's
        samples still in the 10K bucket. Preserves the deque's
        maxlen. Thread-safe via deque.clear() which is C-side
        atomic under the GIL."""
        self._latencies.clear()
        self.samples_seen = 0
        self.samples_decoded = 0
        self.samples_skipped = 0
        self.samples_impossible_latency = 0

    def snapshot(self) -> dict:
        # v0.5.260 (audit latency-2): snapshot the deque via .copy()
        # before iterating. Pre-fix `sorted(self._latencies)` raced
        # the sniff thread — once the deque was full every append
        # was a pop-left + push-right, mutating during iteration
        # → CPython's iterator raised
        # `RuntimeError: deque mutated during iteration` under
        # any real load. .copy() is C-side atomic under the GIL
        # so no lock is needed.
        snap = self._latencies.copy()
        n = len(snap)
        if n == 0:
            return {
                "samples_seen": self.samples_seen,
                "samples_decoded": self.samples_decoded,
                "samples_skipped": self.samples_skipped,
                "samples_impossible_latency": self.samples_impossible_latency,
                "window_samples": 0,
                "min_us": None, "avg_us": None, "p50_us": None,
                "p95_us": None, "p99_us": None, "max_us": None,
            }
        # Sort once for percentile reads — at window_size=10K this is cheap.
        sorted_ns = sorted(snap)
        # Percentile index — nearest-rank method. `min(n-1, ...)` guards
        # the empty/one-sample edge so we don't IndexError on n==1.
        def _pct(p: float) -> float:
            return sorted_ns[min(n - 1, int(n * p))] / 1000.0
        return {
            "samples_seen": self.samples_seen,
            "samples_decoded": self.samples_decoded,
            "samples_skipped": self.samples_skipped,
            "samples_impossible_latency": self.samples_impossible_latency,
            "window_samples": n,
            "min_us":  sorted_ns[0] / 1000.0,
            "avg_us":  sum(sorted_ns) / n / 1000.0,
            "p50_us":  sorted_ns[n // 2] / 1000.0,
            # p95 added in 0.2.58 — most SLAs are stated in p95, and
            # without it operators were eyeballing between p50 and p99.
            "p95_us":  _pct(0.95),
            "p99_us":  _pct(0.99),
            "max_us":  sorted_ns[-1] / 1000.0,
        }


class LatencySampler:
    """Background thread that sniffs `iface` and decodes NLAT headers.

    Usage:
        s = LatencySampler(iface="enp181s0f1np1", udp_port=4791)
        s.start()
        ...
        print(s.stats())
        s.stop()
    """

    def __init__(self, iface: str, udp_port: Optional[int] = 4791,
                 window_size: int = 10000):
        self.iface = iface
        self.udp_port = udp_port  # None = all UDP
        # v0.5.260 (audit latency-5): expose sampler lifecycle to
        # `.stats()` callers. Pre-fix a sampler that died on start
        # (missing iface, scapy import failure, permission denied)
        # returned clean zeros forever with no signal — operator
        # couldn't tell "sampler running, no NLAT frames arriving"
        # from "sampler already dead". Now `status` is one of
        # "starting" / "running" / "iface_not_found" / "scapy_missing"
        # / "crashed:<msg>", surfaced in every stats() response.
        self.status: str = "starting"
        self.stats_obj = LatencyStats()
        self.stats_obj._latencies = deque(maxlen=window_size)
        # v0.3.5: per-stream histograms keyed by extracted stream_id.
        # Populated lazily in `_on_packet` when the UDP payload past
        # the NLAT header contains the `[<sid>(/q<queue>)?#<seq>]`
        # signature. The aggregate `stats_obj` continues to be
        # updated unconditionally so backward-compatible callers see
        # the same numbers they did pre-v0.3.5.
        self._per_stream_stats: Dict[str, LatencyStats] = {}
        self._per_stream_lock = threading.Lock()
        self._window_size = window_size
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def stats(self) -> dict:
        # v0.5.260 (audit latency-5): fold `status` into the response
        # so callers can tell a healthy sampler with no traffic from
        # a dead sampler returning zeros.
        out = self.stats_obj.snapshot()
        out["status"] = self.status
        return out

    # v0.3.5: per-stream snapshot. Returns ``{stream_id: snapshot_dict}``
    # for every stream the sampler has decoded NLAT + signature for in
    # this iface's recent window. Callers that need the legacy
    # aggregate keep using ``.stats()``; new callers should prefer
    # this when the operator has flow_tracking AND capture_latency
    # both enabled (the only mode where signatures + NLAT headers
    # co-occur in the same packet).
    def stats_by_stream(self) -> Dict[str, dict]:
        # Snapshot the dict keys under the lock so a concurrent
        # `_on_packet` insertion can't mutate the dict mid-iter.
        # The individual `LatencyStats.snapshot()` calls are
        # GIL-safe (sorted() reads the deque atomically per call).
        with self._per_stream_lock:
            keys = list(self._per_stream_stats.keys())
        out: Dict[str, dict] = {}
        for sid in keys:
            ls = self._per_stream_stats.get(sid)
            if ls is not None:
                out[sid] = ls.snapshot()
        return out

    def _get_or_create_stream_stats(self, sid: str) -> LatencyStats:
        """First sample for a given stream_id lazily allocates a
        per-stream `LatencyStats` with the same window_size as the
        aggregate."""
        with self._per_stream_lock:
            ls = self._per_stream_stats.get(sid)
            if ls is None:
                ls = LatencyStats()
                ls._latencies = deque(maxlen=self._window_size)
                self._per_stream_stats[sid] = ls
            return ls

    # -------------------------------------------------------------- internals

    def _on_packet(self, pkt) -> None:
        self.stats_obj.samples_seen += 1
        # Extract UDP payload bytes. scapy parses lazily, so this is cheap.
        try:
            from scapy.layers.inet import UDP
        except Exception:
            return
        if not pkt.haslayer(UDP):
            self.stats_obj.samples_skipped += 1
            return
        payload = bytes(pkt[UDP].payload)
        if len(payload) < NLAT_HDR_LEN:
            self.stats_obj.samples_skipped += 1
            return
        magic, _reserved, tx_ns = struct.unpack(NLAT_STRUCT, payload[:NLAT_HDR_LEN])
        if magic != NLAT_MAGIC:
            self.stats_obj.samples_skipped += 1
            return
        rx_ns = time.monotonic_ns()
        latency_ns = rx_ns - tx_ns
        # v0.5.260 (audit latency-4): bucket clock-skew clamps in
        # `samples_impossible_latency`, NOT samples_skipped. Pre-fix
        # a cross-host run with unsynced clocks (see the module
        # docstring — CLOCK_MONOTONIC can't be PTP-synced) decoded
        # every packet fine but every latency was negative or >60s;
        # they got bucketed as "no NLAT magic" and the operator
        # reasonably concluded "wrong port / TX not emitting" when
        # the real cause was clock skew.
        if latency_ns < 0 or latency_ns > 60 * 10**9:  # >60s = nonsense
            self.stats_obj.samples_impossible_latency += 1
            # Log once every 1000 impossible latencies so the operator
            # sees WHY packets are landing in the impossible bucket.
            if self.stats_obj.samples_impossible_latency % 1000 == 1:
                LOG.warning(
                    "[lat] impossible latency %d ns for %s — likely "
                    "cross-host clock skew (see module docstring on "
                    "CLOCK_MONOTONIC limits)",
                    latency_ns, self.iface,
                )
            return
        self.stats_obj.add(latency_ns)

        # v0.3.5: attribute the sample to its stream when the signature
        # is present in the packet body past the NLAT header. Both
        # features (capture_latency + flow_tracking) are independently
        # toggled on the stream config; when both are on, the C-side
        # tx_worker / Scapy TX path emits the signature immediately
        # after the NLAT_HDR_LEN-byte header. Aggregate accumulation
        # above stays unchanged — backward compat for callers using
        # `.stats()` — and per-stream is additive via this dict.
        # If the signature isn't found (capture_latency=on but
        # flow_tracking=off), no per-stream bucket is touched and the
        # `stats_by_stream()` call later returns nothing for this
        # stream. That's the correct degradation: per-stream latency
        # requires both flags.
        m = _SIG_EXTRACT_RE.search(payload, NLAT_HDR_LEN)
        if m is not None:
            try:
                sid = m.group(1).decode("ascii", errors="replace")
            except Exception:
                sid = ""
            if sid:
                self._get_or_create_stream_stats(sid).add(latency_ns)

    def _run(self):
        # v0.5.260 (audit latency-5): every early-return path updates
        # self.status so callers see a specific reason instead of
        # opaque zeros.
        try:
            from scapy.all import sniff
        except Exception as e:
            self.status = f"crashed:scapy_missing:{e}"
            LOG.error("scapy unavailable: %s", e)
            return
        # Friendly check before scapy blows up with "Interface not found"
        if not _iface_exists(self.iface):
            self.status = "iface_not_found"
            available = _list_local_interfaces()
            LOG.error(
                "Interface %r not found on this host (%s).\n"
                "  Available interfaces here: %s\n"
                "  The latency sampler must run ON THE HOST that has the\n"
                "  RX-side NIC. For a netgen server-side test, SSH to the\n"
                "  server and run there:\n"
                "      ssh root@<server> 'python3 -m utils.latency_sampler --iface <iface>'\n"
                "  Use --list-interfaces to see what's reachable from here.",
                self.iface, _hostname(),
                ", ".join(available[:10]) if available else "(none)",
            )
            return
        bpf = f"udp and dst port {self.udp_port}" if self.udp_port else "udp"
        LOG.info(f"[lat] sniffing {self.iface}  filter='{bpf}'  window={self.stats_obj._latencies.maxlen}")
        self.status = "running"
        # Sniff with stop_filter so we can shut down cleanly.
        try:
            sniff(
                iface=self.iface,
                filter=bpf,
                prn=self._on_packet,
                store=False,
                stop_filter=lambda _p: self._stop_event.is_set(),
            )
        except Exception as e:
            self.status = f"crashed:{e}"
            LOG.error("[lat] sniff on %s died: %s", self.iface, e)
            return
        # Clean exit via stop_filter.
        self.status = "stopped"


# -------------------------------------------------------------- helpers


def _hostname():
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return "this host"


def _list_local_interfaces():
    """Return a list of network-interface names on this host. Tries scapy
    first (cross-platform), falls back to /sys/class/net (Linux only)."""
    try:
        from scapy.all import get_if_list
        return sorted(get_if_list())
    except Exception:
        pass
    try:
        import os
        return sorted(os.listdir("/sys/class/net"))
    except Exception:
        return []


def _iface_exists(name):
    return name in _list_local_interfaces()


# -------------------------------------------------------------------- CLI


def _human_ns(ns):
    if ns is None:
        return "—"
    us = ns / 1000.0
    if us >= 1000:
        return f"{us / 1000:.2f} ms"
    return f"{us:.2f} us"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="latency_sampler",
        description="One-way latency RX sampler. Decodes NLAT-tagged UDP "
                    "packets from netgen tx_worker --enable-timestamps "
                    "streams and reports min/avg/p50/p99/max latency.\n\n"
                    "MUST RUN ON THE HOST that owns the receiving NIC — for "
                    "a netgen test that's the server, not the client laptop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--iface",
                        help="Interface to sniff (e.g. enp181s0f1np1). "
                             "Use --list-interfaces to see what's available.")
    parser.add_argument("--udp-port", type=int, default=4791,
                        help="UDP destination port to filter (default: 4791)")
    parser.add_argument("--window", type=int, default=10000,
                        help="Rolling sample window size (default: 10000)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Stats print interval, seconds (default: 1.0)")
    parser.add_argument("--list-interfaces", action="store_true",
                        help="List network interfaces on this host and exit.")
    args = parser.parse_args(argv)

    if args.list_interfaces:
        ifaces = _list_local_interfaces()
        print(f"Interfaces on {_hostname()}:")
        for n in ifaces:
            print(f"  {n}")
        return 0

    if not args.iface:
        parser.error("--iface is required (or use --list-interfaces)")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    s = LatencySampler(iface=args.iface, udp_port=args.udp_port,
                       window_size=args.window)
    s.start()
    try:
        print("# time  samples  decoded  skipped  min  avg  p50  p99  max")
        while True:
            time.sleep(args.interval)
            st = s.stats()
            ts = time.strftime("%H:%M:%S")
            print(f"{ts}  {st['samples_seen']:>8}  {st['samples_decoded']:>8}  "
                  f"{st['samples_skipped']:>8}  "
                  f"min={_human_ns(st['min_us'] and st['min_us']*1000)}  "
                  f"avg={_human_ns(st['avg_us'] and st['avg_us']*1000)}  "
                  f"p50={_human_ns(st['p50_us'] and st['p50_us']*1000)}  "
                  f"p99={_human_ns(st['p99_us'] and st['p99_us']*1000)}  "
                  f"max={_human_ns(st['max_us'] and st['max_us']*1000)}")
    except KeyboardInterrupt:
        pass
    finally:
        s.stop()


if __name__ == "__main__":
    sys.exit(main())
