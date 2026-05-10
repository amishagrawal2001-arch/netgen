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

Same-host loopback (TX iface == RX iface or two ports of the same NIC)
gives accurate one-way latency directly. Cross-host requires PTP- or
NTP-synced clocks on both ends; without that, only relative drift is
meaningful.

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
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

LOG = logging.getLogger("latency_sampler")

# Wire format of the timestamp header. See tx_worker.c struct nlat_hdr.
NLAT_MAGIC = 0x4e4c4154  # "NLAT"
NLAT_HDR_LEN = 16
NLAT_STRUCT = ">IIQ"     # magic, reserved, tx_ns


@dataclass
class LatencyStats:
    """Rolling stats over the last `window_size` samples."""
    samples_seen: int = 0
    samples_decoded: int = 0     # frames with valid NLAT magic
    samples_skipped: int = 0     # non-NLAT or too-short payloads
    # Latencies in nanoseconds. deque(maxlen=window_size) for O(1) rolling.
    _latencies: deque = field(default_factory=lambda: deque(maxlen=10000))

    def add(self, latency_ns: int):
        self._latencies.append(latency_ns)
        self.samples_decoded += 1

    def snapshot(self) -> dict:
        n = len(self._latencies)
        if n == 0:
            return {
                "samples_seen": self.samples_seen,
                "samples_decoded": self.samples_decoded,
                "samples_skipped": self.samples_skipped,
                "window_samples": 0,
                "min_us": None, "avg_us": None, "p50_us": None,
                "p99_us": None, "max_us": None,
            }
        # Sort once for percentile reads — at window_size=10K this is cheap.
        sorted_ns = sorted(self._latencies)
        return {
            "samples_seen": self.samples_seen,
            "samples_decoded": self.samples_decoded,
            "samples_skipped": self.samples_skipped,
            "window_samples": n,
            "min_us":  sorted_ns[0] / 1000.0,
            "avg_us":  sum(sorted_ns) / n / 1000.0,
            "p50_us":  sorted_ns[n // 2] / 1000.0,
            "p99_us":  sorted_ns[min(n - 1, int(n * 0.99))] / 1000.0,
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
        self.stats_obj = LatencyStats()
        self.stats_obj._latencies = deque(maxlen=window_size)
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
        return self.stats_obj.snapshot()

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
        # Clamp negative latencies — they happen only with clock skew across
        # hosts; on same-host loopback they shouldn't occur. Treat negative
        # as "decode mismatch" rather than a real measurement.
        if latency_ns < 0 or latency_ns > 60 * 10**9:  # >60s = nonsense
            self.stats_obj.samples_skipped += 1
            return
        self.stats_obj.add(latency_ns)

    def _run(self):
        try:
            from scapy.all import sniff
        except Exception as e:
            LOG.error("scapy unavailable: %s", e)
            return
        bpf = f"udp and dst port {self.udp_port}" if self.udp_port else "udp"
        LOG.info(f"[lat] sniffing {self.iface}  filter='{bpf}'  window={self.stats_obj._latencies.maxlen}")
        # Sniff with stop_filter so we can shut down cleanly.
        sniff(
            iface=self.iface,
            filter=bpf,
            prn=self._on_packet,
            store=False,
            stop_filter=lambda _p: self._stop_event.is_set(),
        )


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
                    "streams and reports min/avg/p50/p99/max latency.",
    )
    parser.add_argument("--iface", required=True,
                        help="Interface to sniff (e.g. enp181s0f1np1)")
    parser.add_argument("--udp-port", type=int, default=4791,
                        help="UDP destination port to filter (default: 4791)")
    parser.add_argument("--window", type=int, default=10000,
                        help="Rolling sample window size (default: 10000)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Stats print interval, seconds (default: 1.0)")
    args = parser.parse_args(argv)

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
