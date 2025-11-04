# utils/pcap.py
import os
import time
import logging
from typing import Callable, Optional, List, Tuple

from scapy.all import rdpcap, sendp, Ether
from scapy.packet import Packet


__all__ = ["setup_interleaved_pcap"]


def _load_pcap(path: str) -> Tuple[List[Packet], List[float]]:
    """
    Load PCAP and return (packets, inter_packet_gaps_seconds).
    gaps[i] is the delay *after* sending packet i-1 before sending packet i.
    For the first packet, gap is 0.0.
    """
    pkts = rdpcap(path)
    if not pkts:
        raise ValueError("PCAP has 0 packets")

    # Build gaps from timestamp deltas (fallback to 0.0 if missing)
    gaps: List[float] = [0.0]
    prev_ts = float(getattr(pkts[0], "time", 0.0) or 0.0)
    for i in range(1, len(pkts)):
        ts = float(getattr(pkts[i], "time", prev_ts) or prev_ts)
        delta = ts - prev_ts
        if delta < 0:
            delta = 0.0
        gaps.append(float(delta))
        prev_ts = ts

    return list(pkts), gaps


def _safe_append_sig(pkt: Packet, append_sig: Optional[Callable[[Packet], Packet]]) -> Packet:
    """
    Try to append the stream signature to the packet, but never break replay
    if something goes wrong. If append_sig is None, return original pkt.
    """
    if append_sig is None:
        return pkt
    try:
        # Only try to sign IP/UDP frames; raw L2 frames can get malformed otherwise.
        if pkt.haslayer("IP") or pkt.haslayer("IPv6"):
            return append_sig(pkt)
        return pkt
    except Exception as e:
        logging.debug(f"[PCAP] append_sig skipped: {e}")
        return pkt


def setup_interleaved_pcap(
    pcap_cfg: dict,
    interface: str,
    stop_event,
    append_sig: Optional[Callable[[Packet], Packet]] = None,
    update_tx: Optional[Callable[[], None]] = None,
) -> Callable[[], None]:
    """
    Prepare an interleaved PCAP sender. Returns a callable `send_pcap()`
    that you can call frequently inside your TX loop; it will send zero or
    one PCAP frame when due (based on selected timing mode).

    - pcap_cfg: {
        "pcap_enabled": bool,
        "pcap_file_path": str,
        "pcap_loop_count": int,     # 1 = once; 0 or <0 = infinite
        "pcap_rate_mode": "Original Timing" | "Inter-Packet Gap" | "Line Rate"
      }
    - interface: TX interface
    - stop_event: threading.Event to halt replay
    - append_sig: optional function to append per-packet signature (only for IP/IPv6)
    - update_tx: optional callback to bump TX counters per packet sent
    """
    try:
        enabled = bool(
            pcap_cfg.get("pcap_enabled")
            or pcap_cfg.get("enabled")  # tolerate alt key
        )
        path = (pcap_cfg.get("pcap_file_path") or "").strip()
        loop_count = int(pcap_cfg.get("pcap_loop_count", 1))
        rate_mode = (pcap_cfg.get("pcap_rate_mode") or "Original Timing").strip()

        if not enabled:
            logging.info("[PCAP] disabled; interleaver is a no-op.")
            return lambda: None

        if not path:
            logging.warning("[PCAP] enabled but no file path provided; interleaver disabled.")
            return lambda: None

        if not os.path.isfile(path):
            logging.warning(f"[PCAP] file does not exist: {path}; interleaver disabled.")
            return lambda: None

        try:
            pkts, gaps = _load_pcap(path)
        except Exception as e:
            logging.warning(f"[PCAP] failed to load '{path}': {e}; interleaver disabled.")
            return lambda: None

        n = len(pkts)
        logging.info(f"[PCAP] Loaded {n} packets from {path} (mode='{rate_mode}', loops={loop_count}).")

        # State captured by the closure
        idx = 0
        loops_done = 0
        # Use monotonic for scheduling
        next_due = time.monotonic()

        # For "Line Rate" we ignore recorded gaps and blast one pkt per call
        use_gaps = rate_mode.lower() in ("original timing", "inter-packet gap")

        infinite_loops = (loop_count <= 0)

        def send_pcap():
            nonlocal idx, loops_done, next_due
            if stop_event.is_set():
                return

            if n == 0:
                return

            now = time.monotonic()

            # Respect timing only for timing modes; otherwise try to send every call
            if use_gaps and now < next_due:
                return  # not yet time for next packet

            # If we've reached end of PCAP, handle looping
            if idx >= n:
                loops_done += 1
                if not infinite_loops and loops_done >= loop_count:
                    return  # finished all loops
                idx = 0
                # when wrapping, schedule the first packet immediately
                next_due = now

            pkt = pkts[idx]

            # OPTIONAL: sign only for IP/IPv6 packets to avoid mangling L2-only frames
            pkt_to_send = _safe_append_sig(pkt, append_sig)

            try:
                sendp(pkt_to_send, iface=interface, verbose=False)
                if update_tx:
                    update_tx()
            except Exception as e:
                logging.debug(f"[PCAP] send error at idx={idx}: {e}")

            # compute next due time
            if use_gaps:
                gap = float(gaps[idx]) if idx < len(gaps) else 0.0
                # guardrails: clamp absurd gaps (optional)
                if gap < 0:
                    gap = 0.0
                next_due = now + gap
            else:
                # "Line Rate" / best-effort: eligible again immediately
                next_due = now

            idx += 1

        return send_pcap

    except Exception as e:
        logging.warning(f"[PCAP] interleaver setup failed: {e}")
        return lambda: None
