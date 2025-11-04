# multithreaded_traffic_gen.py
"""
Multithreaded traffic generator with optional DPDK/tx_worker backend.

- Keeps Scapy-based generators (Generic, RoCEv2, UEC) intact.
- Adds an early switch to a DPDK backend (tx_worker) when requested in stream_data.
  Set either `stream_data["engine"] == "dpdk"` or any of:
    stream_data["dpdk_enable"] / protocol_selection["dpdk_enable"] / "dpdk"/"use_dpdk"

- RX sniffer counts packets by embedded signature (Scapy path) OR by a tolerant
  L2/L3/L4 selector (DPDK path) so rx_count increases for both engines.
"""

import time
import logging
import threading
import uuid

from scapy.all import sendp

from utils.helpers import is_interface_up
from utils.rocev2 import generate_rocev2_packet
from utils.uec import generate_uec_rocev2_packet
from utils.generic import build_generic_packet, get_packet_config
from utils.rdma_perf import start_ibperf_server
from utils.rdma_perf import perf_stats
from utils.pcap import setup_interleaved_pcap
from scapy.layers.inet6 import IPv6
from utils.arp import generate_arp_packet

# ----- optional DPDK backend (utils/dpdk_tx_worker.py) -----
_DPDK_AVAILABLE = False
try:
    import utils.dpdk_tx_worker as _dpdk_backend  # drop-in sibling module
    _DPDK_AVAILABLE = hasattr(_dpdk_backend, "should_use_dpdk") and hasattr(_dpdk_backend, "run_stream")
except Exception as _e:
    _DPDK_AVAILABLE = False
    logging.debug("DPDK backend unavailable: %s", _e)


def _resolve_stream_name(stream_data, interface, stream_id):
    ps = (stream_data.get("protocol_selection") or {}) or {}

    def usable(v):
        if v is None:
            return False
        s = str(v).strip()
        # Treat placeholders as unset
        return s and s.lower() not in ("stream", "unnamed stream", "default")

    # explicit top-level fields (preferred)
    for k in ("name", "stream_name", "display_name", "title"):
        v = stream_data.get(k)
        if usable(v):
            return str(v)

    # UI-provided names inside protocol_selection
    for k in ("name", "stream_name"):
        v = ps.get(k)
        if usable(v):
            return str(v)

    # Fallback: Port / L4 [short-id]
    port = stream_data.get("port") or interface
    l4 = (stream_data.get("L4") or ps.get("L4") or "Any")
    sid = (str(stream_id) or "")[:8]
    return f"{port} / {l4} [{sid}]"




# ---------------------------
# Stream tracking
# ---------------------------
class StreamTracker:
    def __init__(self):
        self.active_streams = []
        self.lock = threading.Lock()
        self._sniffers = set()      # {(rx_interface, stream_id)}
        self.streams = {}           # quick RX lookups

    # ---- sniffer registry ----
    def register_sniffer(self, rx_interface, stream_id):
        with self.lock:
            key = (rx_interface, stream_id)
            if key in self._sniffers:
                return False
            self._sniffers.add(key)
            return True

    def unregister_sniffer(self, rx_interface, stream_id):
        with self.lock:
            self._sniffers.discard((rx_interface, stream_id))

    # ---- stream rows (de-dupe by interface+stream_id) ----
    def add_stream(self, stream):
        with self.lock:
            sid = stream.get("stream_id")
            iface = stream.get("interface")
            self.active_streams = [
                s for s in self.active_streams
                if not (s.get("interface") == iface and s.get("stream_id") == sid)
            ]
            self.active_streams.append({
                "stream_id": sid,
                "interface": iface,
                "stream_name": stream.get("stream_name"),
                "stop_event": stream.get("stop_event"),
                "rx_thread": stream.get("rx_thread"),
                "rx_interface": stream.get("rx_interface"),
                "flow_tracking_enabled": stream.get("flow_tracking_enabled", False),
                "tx_count": 0,
                "rx_count": 0
            })

    # ----- by-stream_id TX increment -----
    def update_tx_by_id(self, interface, stream_id):
        with self.lock:
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_id"] == stream_id:
                    s["tx_count"] += 1
                    return

    # ----- RX: count by stream_id (name only for UI map) -----
    def update_rx(self, rx_interface, stream_name, stream_id):
        with self.lock:
            for s in self.active_streams:
                if s["stream_id"] == stream_id:
                    s["rx_count"] += 1
                    self.streams.setdefault(rx_interface, {}).setdefault(stream_name, {})["rx_count"] = s["rx_count"]
                    break

    # ----- getters -----
    def get_tx_count_by_id(self, interface, stream_id):
        with self.lock:
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_id"] == stream_id:
                    return s.get("tx_count", 0)
            return 0

    def get_stream_stats(self):
        with self.lock:
            seen = set()
            out = []
            for s in self.active_streams:
                sid = s.get("stream_id")
                if sid in seen:
                    continue
                seen.add(sid)
                item = {
                    "stream_id": sid,
                    "interface": s.get("interface"),
                    "stream_name": s.get("stream_name"),
                    "rx_interface": s.get("rx_interface"),
                    "tx_count": s.get("tx_count", 0),
                    "rx_count": s.get("rx_count", 0),
                    "flow_tracking_enabled": s.get("flow_tracking_enabled", False),
                }
                if s["interface"] in perf_stats:
                    item.update({
                        "ibperf_rate": perf_stats[s["interface"]]["rate"],
                        "ibperf_unit": perf_stats[s["interface"]]["unit"],
                        "ibperf_timestamp": perf_stats[s["interface"]]["timestamp"],
                    })
                out.append(item)
            return out

    def find_stream_by_id(self, interface, stream_id):
        with self.lock:
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_id"] == stream_id:
                    return s
        return None

    def remove_stream_by_id(self, interface, stream_id):
        with self.lock:
            self.active_streams = [
                s for s in self.active_streams
                if not (s["interface"] == interface and s["stream_id"] == stream_id)
            ]



stream_tracker = StreamTracker()


# ---------------------------
# Signature helpers (unique per packet) — Scapy path only
# ---------------------------
def _append_sig_with_seq(pkt, stream_id: str, seq: int):
    from scapy.layers.inet import IP, UDP
    from scapy.layers.inet6 import IPv6
    from scapy.layers.l2 import Dot1Q
    from scapy.packet import Raw

    sig = f"[{stream_id}#{seq}]".encode()

    # Always embed a marker for any UDP payload; otherwise add/append Raw
    try:
        if UDP in pkt:
            up = bytes(getattr(pkt[UDP], "payload", b"") or b"")
            try:
                pkt[UDP].remove_payload()
            except Exception:
                pass
            pkt = pkt / Raw(load=(sig + up))
        else:
            if Raw in pkt and getattr(pkt[Raw], "load", None) is not None:
                try:
                    pkt[Raw].load = bytes(pkt[Raw].load) + sig
                except Exception:
                    pkt = pkt / Raw(load=sig)
            else:
                pkt = pkt / Raw(load=sig)
    except Exception:
        pkt = pkt / Raw(load=sig)

    # Recompute lengths/checksums
    if UDP in pkt:
        for fld in ("len", "chksum"):
            try: delattr(pkt[UDP], fld)
            except Exception: pass

    if IP in pkt:
        try: del pkt[IP].len
        except Exception: pass
        try: del pkt[IP].chksum
        except Exception: pass

    if IPv6 in pkt:
        try: del pkt[IPv6].plen
        except Exception: pass

    # Keep Dot1Q ethertype consistent with payload
    if Dot1Q in pkt:
        try:
            if IP in pkt: pkt[Dot1Q].type = 0x0800
            elif IPv6 in pkt: pkt[Dot1Q].type = 0x86DD
        except Exception:
            pass

    return pkt



# ---------------------------
# RX sniffer (signature + tuple) with single-instance guard
# ---------------------------

def _build_bpf(selector):
    """
    Build a compact BPF string.
    - Understands ipv4 + ipv6 (src_ip/dst_ip, src_ip6/dst_ip6)
    - If a VLAN is expected, widen to match both stripped and tagged paths:
        (<expr>) or (vlan and <expr>)
    """
    if not selector:
        return None

    # Inner expression first (no 'vlan' yet)
    terms = []

    # L3 (IPv4)
    ip4_src = selector.get("src_ip")
    ip4_dst = selector.get("dst_ip")
    if ip4_src and ip4_dst:
        terms.append(f"(host {ip4_src} or host {ip4_dst})")
    elif ip4_src:
        terms.append(f"host {ip4_src}")
    elif ip4_dst:
        terms.append(f"host {ip4_dst}")

    # L3 (IPv6)
    ip6_src = selector.get("src_ip6")
    ip6_dst = selector.get("dst_ip6")
    ip6_term = None
    if ip6_src and ip6_dst:
        ip6_term = f"(host {ip6_src} or host {ip6_dst})"
    elif ip6_src:
        ip6_term = f"host {ip6_src}"
    elif ip6_dst:
        ip6_term = f"host {ip6_dst}"
    if ip6_term:
        # Explicitly tag as ip6 to avoid ambiguity
        terms.append(f"(ip6 and {ip6_term})")

    # L4
    l4 = (selector.get("l4") or "").lower()
    sport = selector.get("sport")
    dport = selector.get("dport")

    if l4 == "icmp":
        terms.append("icmp or icmp6")
    elif l4 == "udp":
        if sport and dport:
            terms.append(f"udp and ((src port {sport} and dst port {dport}) or (src port {dport} and dst port {sport}))")
        elif sport:
            terms.append(f"udp and (src port {sport} or dst port {sport})")
        elif dport:
            terms.append(f"udp and (src port {dport} or dst port {dport})")
        else:
            terms.append("udp")
    elif l4 == "tcp":
        if sport and dport:
            terms.append(f"tcp and ((src port {sport} and dst port {dport}) or (src port {dport} and dst port {sport}))")
        elif sport:
            terms.append(f"tcp and (src port {sport} or dst port {sport})")
        elif dport:
            terms.append(f"tcp and (src port {dport} or dst port {dport})")
        else:
            terms.append("tcp")

    inner = " and ".join(terms).strip()

    # Nothing to filter on? fall back so lfilter() can do the work.
    if not inner:
        inner = "arp or udp or tcp or icmp or icmp6"

    # If VLAN is configured, widen to match both tagged and stripped paths.
    if selector.get("vlan_id"):
        inner = f"({inner}) or (vlan and {inner})"

    logging.info(f"[RX] BPF applied: {inner}")
    return inner




def start_rx_counter(rx_interface, stream_name, stream_id, tracker: StreamTracker, stop_event, selector=None):
    """
    Single-instance RX sniffer with robust VLAN handling:
      - Only one sniffer per (rx_interface, stream_id).
      - If a VLAN is configured, create a temp VLAN sub-interface and SNIFF ON IT.
      - BPF widened automatically for VLAN.
      - Signature-first matching; tuple fallback.
    """
    from scapy.all import AsyncSniffer, Ether, Raw, get_if_hwaddr
    from scapy.layers.inet import UDP, TCP, IP, ICMP
    from scapy.layers.inet6 import IPv6
    from scapy.layers.l2 import Dot1Q
    from scapy.config import conf
    import subprocess, time, logging
    from scapy.layers.l2 import ARP
    conf.use_pcap = True  # libpcap for proper BPF

    # ---- single-instance guard ----
    if not tracker.register_sniffer(rx_interface, stream_id):
        logging.info(f"[RX] sniffer already running on {rx_interface} for stream_id={stream_id}; skipping duplicate")
        return None

    # ---- selector ----
    sel = (selector or {}).copy()
    vlan_id = sel.get("vlan_id")

    # ---- ensure VLAN frames reach kernel, and sniff ON the sub-if if created ----
    created_vlan_subif = None
    sniff_iface = rx_interface
    if vlan_id:
        try:
            created_vlan_subif = _ensure_vlan_rx_visible(rx_interface, vlan_id)
            if created_vlan_subif:
                sniff_iface = created_vlan_subif
                logging.info(f"[RX] VLAN {vlan_id} visible via sub-interface '{created_vlan_subif}' (sniffing here)")
        except Exception:
            created_vlan_subif = None

    # ---- BPF (IPv4/IPv6 OR, VLAN-widened inside) ----
    final_bpf = _build_bpf(sel)
    if not final_bpf:
        final_bpf = "udp or tcp or icmp or icmp6"
    logging.info(f"[RX] BPF applied on {sniff_iface}: {final_bpf}")

    # ---- local state ----
    sig_prefix = f"[{stream_id}#".encode()
    try:
        local_mac = (get_if_hwaddr(sniff_iface) or "").lower()
    except Exception:
        local_mac = None

    seen_total = matched = sig_hits = tuple_hits = 0
    last_dbg = time.time()
    first_seen_ts = None
    relaxed_now = False
    auto_relax = bool(sel.pop("_auto_relax", True))
    enforce_inbound_only = bool(sel.pop("_enforce_inbound_only", False))

    # ---- helpers ----
    def _sig_present(pkt) -> bool:
        try:
            if Raw in pkt and getattr(pkt[Raw], "load", None) is not None:
                if sig_prefix in bytes(pkt[Raw].load):
                    return True
        except Exception:
            pass
        try:
            raw_frame = bytes(getattr(pkt, "original", None) or pkt)
            return sig_prefix in raw_frame
        except Exception:
            return False

    def _macs_match(pkt, src_mac_sel, dst_mac_sel, enforce_mac: bool, direction: str) -> bool:
        if not enforce_mac or not pkt.haslayer(Ether):
            return True
        ps, pd = (pkt[Ether].src or "").lower(), (pkt[Ether].dst or "").lower()
        if direction == "either":
            fwd_ok = ((src_mac_sel is None or ps == src_mac_sel) and (dst_mac_sel is None or pd == dst_mac_sel))
            rev_ok = ((src_mac_sel is None or pd == src_mac_sel) and (dst_mac_sel is None or ps == dst_mac_sel))
            return fwd_ok or rev_ok
        return ((src_mac_sel is None or ps == src_mac_sel) and (dst_mac_sel is None or pd == dst_mac_sel))

    def _ips_match(pkt, src_ip_sel, dst_ip_sel, direction: str) -> bool:
        if src_ip_sel is None and dst_ip_sel is None:
            return True
        if IP in pkt:
            ps, pd = pkt[IP].src, pkt[IP].dst
        elif IPv6 in pkt:
            ps, pd = pkt[IPv6].src, pkt[IPv6].dst
        else:
            return False
        if direction == "either":
            fwd_ok = ((src_ip_sel is None or ps == src_ip_sel) and (dst_ip_sel is None or pd == dst_ip_sel))
            rev_ok = ((src_ip_sel is None or pd == src_ip_sel) and (dst_ip_sel is None or ps == dst_ip_sel))
            return fwd_ok or rev_ok
        return ((src_ip_sel is None or ps == src_ip_sel) and (dst_ip_sel is None or pd == dst_ip_sel))

    def _ports_match_udp(pkt, sport_sel, dport_sel, relaxed: bool, direction: str) -> bool:
        if UDP not in pkt:
            return False
        ps, pd = int(pkt[UDP].sport), int(pkt[UDP].dport)
        if sport_sel is None and dport_sel is None:
            return True
        if direction == "either":
            fwd_ok = ((sport_sel is None or ps == sport_sel) and (dport_sel is None or pd == dport_sel))
            rev_ok = ((sport_sel is None or pd == sport_sel) and (dport_sel is None or ps == dport_sel))
            if relaxed:
                if sport_sel is not None and (ps == sport_sel or pd == sport_sel): return True
                if dport_sel is not None and (ps == dport_sel or pd == dport_sel): return True
            return fwd_ok or rev_ok
        if relaxed:
            if sport_sel is not None and ps == sport_sel: return True
            if dport_sel is not None and pd == dport_sel: return True
        return ((sport_sel is None or ps == sport_sel) and (dport_sel is None or pd == dport_sel))

    def _tuple_match(pkt) -> bool:
        nonlocal relaxed_now
        if not sel and not relaxed_now:
            return False

        # Optional inbound-only drop
        if enforce_inbound_only and local_mac and pkt.haslayer(Ether):
            try:
                if (pkt[Ether].dst or "").lower() != local_mac:
                    return False
            except Exception:
                pass

        # VLAN tolerant: if Dot1Q present AND selector has vlan_id, require match
        exp_vlan = sel.get("vlan_id")
        if exp_vlan is not None and Dot1Q in pkt:
            try:
                if int(pkt[Dot1Q].vlan) != int(exp_vlan):
                    return False
            except Exception:
                return False

        if not _macs_match(pkt, sel.get("src_mac"), sel.get("dst_mac"),
                           bool(sel.get("enforce_mac", False)), sel.get("direction", "either")):
            return False

        if any(k in sel and sel[k] for k in ("src_ip", "dst_ip", "src_ip6", "dst_ip6")):
            if not _ips_match(pkt, sel.get("src_ip"), sel.get("dst_ip"), sel.get("direction", "either")):
                return False
            if not _ips_match(pkt, sel.get("src_ip6"), sel.get("dst_ip6"), sel.get("direction", "either")):
                return False

        l4 = (sel.get("l4") or "").lower()
        if l4 == "udp" or relaxed_now:
            return UDP in pkt if relaxed_now else _ports_match_udp(
                pkt, sel.get("sport"), sel.get("dport"),
                bool(sel.get("relaxed", False)), sel.get("direction", "either")
            )
        elif l4 == "tcp":
            if TCP not in pkt:
                return False
            ps, pd = int(pkt[TCP].sport), int(pkt[TCP].dport)
            ssel, dsel = sel.get("sport"), sel.get("dport")
            if ssel is None and dsel is None:
                return True
            if sel.get("direction", "either") == "either":
                fwd_ok = ((ssel is None or ps == ssel) and (dsel is None or pd == dsel))
                rev_ok = ((ssel is None or pd == ssel) and (dsel is None or ps == dsel))
                return fwd_ok or rev_ok
            return ((ssel is None or ps == ssel) and (dsel is None or pd == dsel))
        elif l4 == "icmp":
            return ICMP in pkt
        if ARP in pkt:
            return True
        return True

    # ---- lfilter + callback ----
    def lfilter(pkt):
        nonlocal seen_total, matched, sig_hits, tuple_hits, last_dbg, first_seen_ts, relaxed_now
        seen_total += 1
        if first_seen_ts is None:
            first_seen_ts = time.time()

        # 1) signature path first (Scapy TX embeds tag)
        if _sig_present(pkt):
            sig_hits += 1
            matched += 1
            if matched <= 5:
                logging.info(f"[RX-MATCH] signature on {sniff_iface} (seen={seen_total}, sig={sig_hits}, tuple={tuple_hits})")
            return True

        # 2) tuple path
        if _tuple_match(pkt):
            tuple_hits += 1
            matched += 1
            if matched <= 5:
                logging.info(f"[RX-MATCH] tuple on {sniff_iface} (seen={seen_total}, sig={sig_hits}, tuple={tuple_hits}, relaxed={relaxed_now})")
            return True

        # 3) after 2s without matches, relax to any UDP (useful for RoCEv2)
        if auto_relax and not relaxed_now and first_seen_ts and (time.time() - first_seen_ts) >= 2.0 and matched == 0:
            relaxed_now = True
            logging.warning(f"[RX] auto-relax enabled on {sniff_iface}: counting any UDP frames (no signature)")

        # periodic debug
        now = time.time()
        if seen_total in (1, 100, 1000) or (now - last_dbg) >= 2.0:
            last_dbg = now
            logging.info(f"[RX-DBG] {sniff_iface}: seen={seen_total} matched={matched} sig={sig_hits} tuple={tuple_hits} relaxed={relaxed_now}")
        return False

    def on_pkt(_pkt):
        # Count against the original rx_interface key to keep UI stable
        tracker.update_rx(rx_interface, stream_name, stream_id)

    # ---- start sniffer ----
    sniffer = AsyncSniffer(
        iface=sniff_iface,
        prn=on_pkt,
        store=False,
        filter=final_bpf,
        lfilter=lfilter,
        promisc=True
    )
    sniffer.start()
    logging.info(f"RX sniffer started on {sniff_iface} for stream '{stream_name}' (stream_id={stream_id})")

    # ---- stopper cleanup ----
    def stopper():
        stop_event.wait()
        try:
            sniffer.stop()
        except Exception as e:
            logging.warning(f"[RX Sniffer] stop() error on {sniff_iface}: {e}")
        finally:
            tracker.unregister_sniffer(rx_interface, stream_id)
            if created_vlan_subif:
                try:
                    subprocess.run(["ip", "link", "delete", created_vlan_subif],
                                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    logging.info(f"[RX] Removed temporary VLAN sub-interface '{created_vlan_subif}'")
                except Exception:
                    pass
            logging.info(f"RX sniffer stopped on {sniff_iface} for '{stream_name}'")

    threading.Thread(target=stopper, daemon=True).start()
    return sniffer


# ---------------------------
# Lifecycle
# ---------------------------
def on_stream_stopped(interface, stream_id, reason="manual"):
    stream = stream_tracker.find_stream_by_id(interface, stream_id)
    if not stream:
        return

    stream_name = stream.get("stream_name")
    rx_interface = stream.get("rx_interface")
    flow_tracking_enabled = stream.get("flow_tracking_enabled", False)

    if flow_tracking_enabled and rx_interface != interface:
        logging.info(f"RX grace period 2s for stream '{stream_name}' on {rx_interface}")
        time.sleep(2)
        logging.info(f"RX sniffer fully stopped for stream '{stream_name}' on {rx_interface}")

    logging.info(f"Stream stopped: {stream_id} on {interface} (reason={reason})")
    stream_tracker.remove_stream_by_id(interface, stream_id)


# ---------------------------
# Rate calc
# ---------------------------
def calculate_interval(rate_type, stream_data, default_size=1000):
    """
    Returns (interval_seconds_between_batches, batch_size).
    Robustly resolves rate from stream_rate_control, top-level, protocol_selection, or tx_rate.
    Accepts 'Packets Per Second (PPS)', 'Bit Rate', 'Bit Rate (bps)', 'Bit Rate (Mbps)', 'Load (%)', 'Line Rate'.
    """
    try:
        rate_control = stream_data.get("stream_rate_control", {}) or {}
        ps = stream_data.get("protocol_selection", {}) or {}
        tx_rate = stream_data.get("tx_rate", {}) or {}

        def pick_int(*vals, default=0):
            for v in vals:
                if v is None:
                    continue
                s = str(v).strip()
                if s == "":
                    continue
                try:
                    return int(float(s))
                except Exception:
                    continue
            return default

        rt = (rate_type or "").strip()
        if rt == "Bit Rate":
            rt = "Bit Rate (Mbps)"

        pps = None

        if rt == "Packets Per Second (PPS)":
            pps = pick_int(
                rate_control.get("stream_pps_rate"),
                stream_data.get("stream_pps_rate"),
                ps.get("stream_pps_rate"),
                tx_rate.get("pps"),
                default=0
            )

        elif rt in ("Bit Rate (bps)", "Bit Rate (Mbps)"):
            br = pick_int(
                rate_control.get("stream_bit_rate"),
                stream_data.get("stream_bit_rate"),
                ps.get("stream_bit_rate"),
                tx_rate.get("bps") if rt == "Bit Rate (bps)" else tx_rate.get("mbps"),
                default=0
            )
            frame_size = pick_int(stream_data.get("frame_size"), ps.get("frame_size"), default=default_size)
            bytes_per_packet = max(frame_size + 20, 1)
            bit_rate_bps = br * 1_000_000 if rt == "Bit Rate (Mbps)" else br
            pps = int(bit_rate_bps / (bytes_per_packet * 8))

        elif rt == "Load (%)":
            load = pick_int(
                rate_control.get("stream_load_percentage"),
                stream_data.get("stream_load_percentage"),
                ps.get("stream_load_percentage"),
                tx_rate.get("percent"),
                default=0
            )
            pps = int((125_000 * load) / 100)  # ~12.5 Mpps @ 100% on 1GbE → 125k per 1%

        elif rt == "Line Rate":
            pps = None

        if pps is None:
            return 0.0, 1

        if pps <= 0:
            logging.warning(f"[Rate] PPS is zero or invalid: {pps}")
            return 0.0, 1

        if pps <= 100: batch_size = 2
        elif pps <= 500: batch_size = 10
        elif pps <= 1_000: batch_size = 20
        elif pps <= 10_000: batch_size = 100
        elif pps <= 100_000: batch_size = 500
        elif pps <= 1_000_000: batch_size = 2_000
        else: batch_size = 10_000

        batch_size = min(batch_size, pps)
        interval = 1 / max(pps / batch_size, 1e-6)
        logging.info(f"[Rate] resolved_pps={pps}, batch_size={batch_size}, interval={interval:.6f}s")
        return interval, batch_size

    except Exception as e:
        logging.warning(f"[Rate] interval calc error: {e}")
        return 0.0, 1


# ---------------------------
# RX selector builder (for DPDK path / no signature)
# ---------------------------
def _ensure_vlan_rx_visible(rx_iface: str, vlan_id: int) -> str | None:
    """
    For drivers that drop VLAN-tagged frames unless the VLAN is configured, create
    an ephemeral VLAN sub-interface (rx_iface.<vlan_id>) and bring it up.
    Returns the created subif name or None.
    """
    import subprocess, os
    if vlan_id is None:
        return None
    sub = f"{rx_iface}.{int(vlan_id)}"
    try:
        # If it already exists, just bring it up
        rc = subprocess.run(["ip", "link", "show", sub], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc.returncode != 0:
            subprocess.run(["ip", "link", "add", "link", rx_iface, "name", sub, "type", "vlan", "id", str(int(vlan_id))],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ip", "link", "set", sub, "up"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return sub
    except Exception:
        return None

def _kernel_driver_name(iface: str) -> str | None:
    import os
    p = f"/sys/class/net/{iface}/device/driver"
    if not os.path.exists(p):
        return None
    try:
        return os.path.basename(os.path.realpath(p))
    except Exception:
        return None

def _build_rx_selector_for_stream(stream_data, force_udp=False, dpdk_hint=False):
    ps = (stream_data.get("protocol_selection") or {}) or {}
    pd = (stream_data.get("protocol_data") or {}) or {}
    mac_pd  = (pd.get("mac")  or {}) or {}
    ipv4_pd = (pd.get("ipv4") or {}) or {}
    ipv6_pd = (pd.get("ipv6") or {}) or {}
    udp_pd  = (pd.get("udp")  or {}) or {}
    vlan_pd = (pd.get("vlan") or {}) or {}

    def _pick(*vals, default=None):
        for v in vals:
            if v is None: continue
            s = str(v).strip()
            if s != "": return s
        return default

    def _pick_int(*vals):
        for v in vals:
            try:
                if v is None: continue
                s = str(v).strip()
                if s == "": continue
                return int(float(s))
            except Exception:
                continue
        return None

    src_mac = _pick(mac_pd.get("mac_source_address"),
                    stream_data.get("mac_source_address"),
                    stream_data.get("mac_src"), stream_data.get("mac_source"))
    dst_mac = _pick(mac_pd.get("mac_destination_address"),
                    stream_data.get("mac_destination_address"),
                    stream_data.get("mac_dst"), stream_data.get("mac_destination"))

    vlan_id = _pick(vlan_pd.get("vlan_id"), stream_data.get("vlan_id"))
    try:
        vlan_id = int(vlan_id) if vlan_id not in (None, "", "0") else None
    except Exception:
        vlan_id = None

    # IPv4 + IPv6 candidates
    src_ip  = _pick(ipv4_pd.get("ipv4_source"), stream_data.get("src_ip"))
    dst_ip  = _pick(ipv4_pd.get("ipv4_destination"), stream_data.get("dst_ip"))
    src_ip6 = _pick(ipv6_pd.get("ipv6_source"), stream_data.get("src_ipv6"))
    dst_ip6 = _pick(ipv6_pd.get("ipv6_destination"), stream_data.get("dst_ipv6"))

    l4 = _pick(stream_data.get("L4"), ps.get("L4"))
    l4 = (l4 or "").strip().lower()

    # Treat RoCEv2/UEC as UDP for the sniffer path
    if l4 in ("rocev2", "uec"):
        l4 = "udp"
    if force_udp or dpdk_hint:
        l4 = "udp"

    sport = _pick_int(udp_pd.get("udp_source_port"),
                      stream_data.get("udp_source_port"),
                      stream_data.get("udp_sport"))
    dport = _pick_int(udp_pd.get("udp_destination_port"),
                      stream_data.get("udp_destination_port"),
                      stream_data.get("udp_dport"))

    # Reasonable defaults for RDMA over UDP if ports unset
    if l4 == "udp":
        if sport == 0: sport = None
        if dport == 0: dport = None
        if dport is None and ((stream_data.get("L4") or "").lower() in ("rocev2", "uec")):
            dport = 4791  # RoCEv2 default

    # For DPDK hint we ensure a narrow tuple
    if dpdk_hint and l4 == "udp":
        sport = sport or 1234
        dport = dport or 4791

    return {
        "src_mac": (src_mac or "").lower() or None,
        "dst_mac": (dst_mac or "").lower() or None,
        "vlan_id": vlan_id,
        "src_ip": src_ip, "dst_ip": dst_ip,
        "src_ip6": src_ip6, "dst_ip6": dst_ip6,
        "l4": l4 if l4 in ("udp", "tcp", "icmp") else None,
        "sport": sport, "dport": dport,
        "direction": "either",
        "relaxed": True,
        "enforce_mac": False,
        # robust defaults for both kernel and DPDK cases
        "ignore_vlan_in_bpf": False,
        "prefer_simple_bpf": bool(dpdk_hint),
    }




# ---------------------------
# Generator
# ---------------------------
def generate_packets(stream_data, interface, stop_event):
    """
    Generate packets for a single stream on `interface`, with optional
    interleaved PCAP replay (non-blocking). Works for Generic, RoCEv2, UEC.
    If the stream requests DPDK (engine="dpdk" or dpdk_enable=True) and the
    dpdk_tx_worker backend is available, this will hand off to tx_worker.
    """
    logging.info(f"[TX] Starting packet generation for interface '{interface}'")
    # Ensure ID & a stable, non-placeholder name
    stream_id = stream_data.get("stream_id", str(uuid.uuid4()))
    stream_name = _resolve_stream_name(stream_data, interface, stream_id)
    stream_data["stream_id"] = stream_id
    stream_data["name"] = stream_name
    stream_data["stream_name"] = stream_name

    flow_tracking_enabled = bool(stream_data.get("flow_tracking_enabled", False))
    max_packets = int(stream_data.get("max_packets", 0))  # 0 => unlimited

    rx_port = stream_data.get("rx_port") or interface
    rx_interface = str(rx_port).split("Port:")[-1].strip()
    stream_data["rx_interface"] = rx_interface

    protocol_selection = stream_data.get("protocol_selection", {}) or {}
    protocol_data = stream_data.get("protocol_data", {}) or {}
    l4_sel = (stream_data.get("L4") or protocol_selection.get("L4") or "").strip()

    # ---- register stream row (before sniffer) ----
    rx_thread = None
    stream_tracker.add_stream({
        "stream_id": stream_id,
        "interface": interface,
        "stream_name": stream_name,
        "stop_event": stop_event,
        "rx_thread": rx_thread,
        "rx_interface": rx_interface,
        "flow_tracking_enabled": flow_tracking_enabled
    })

    # ---- RX sniffer (if enabled) ----
    if flow_tracking_enabled:
        if rx_interface == interface:
            logging.warning(f"[RX] RX interface equals TX ('{interface}'); disabling flow tracking")
        elif is_interface_up(rx_interface):
            # Build a tolerant selector; enable DPDK hints if backend will be used
            use_dpdk = False
            try:
                use_dpdk = bool(_DPDK_AVAILABLE and _dpdk_backend.should_use_dpdk(stream_data))
            except Exception:
                use_dpdk = False

            rx_selector = _build_rx_selector_for_stream(
                stream_data,
                force_udp=use_dpdk,      # DPDK is UDP-only in your worker
                dpdk_hint=use_dpdk       # ignore VLAN in BPF + default ports
            )

            logging.info(f"[RX] Starting sniffer on '{rx_interface}' for stream '{stream_name}' (dpdk_hint={use_dpdk})")
            rx_thread = start_rx_counter(
                rx_interface, stream_name, stream_id, stream_tracker, stop_event,
                selector=rx_selector
            )


    # ---- DPDK/tx_worker branch (if requested) ----
    try:
        if _DPDK_AVAILABLE and _dpdk_backend.should_use_dpdk(stream_data):
            logging.info("[DPDK] using tx_worker backend for '%s' on %s", stream_name, interface)
            stream_data["stream_id"] = stream_id  # ensure ID is set
            rc = _dpdk_backend.run_stream(stream_data, interface, stop_event, stream_tracker)
            reason = "complete" if rc == 0 else f"dpdk_rc={rc}"
            on_stream_stopped(interface, stream_id, reason=reason)
            return
        elif (_dpdk_backend if _DPDK_AVAILABLE else None) and not _dpdk_backend.should_use_dpdk(stream_data):
            logging.debug("[DPDK] backend available but stream not requesting it (engine/scalar flags).")
        else:
            if not _DPDK_AVAILABLE:
                logging.debug("[DPDK] backend not available; falling back to Scapy.")
    except Exception as e:
        logging.warning("[DPDK] handoff failed (%s); falling back to Scapy path.", e)

    # ---- per-packet signature helper (Scapy only) ----
    seq = 0
    def add_sig(pkt):
        """No-op unless flow tracking is enabled (Scapy path)."""
        nonlocal seq
        if not flow_tracking_enabled:
            return pkt
        try:
            return _append_sig_with_seq(pkt, stream_id, seq)
        finally:
            seq += 1

    # ---- PCAP interleaver (no-op if invalid/disabled) ----
    pcap_cfg = (stream_data.get("pcap_stream", {})
                or protocol_selection.get("pcap_stream", {})
                or {})
    try:
        send_pcap = setup_interleaved_pcap(
            pcap_cfg=pcap_cfg,
            interface=interface,
            stop_event=stop_event,
            append_sig=add_sig,
            update_tx=lambda: stream_tracker.update_tx_by_id(interface, stream_id),
        )
        if not callable(send_pcap):
            send_pcap = lambda: None
        logging.info("[PCAP] Interleaver ready")
    except Exception as e:
        logging.warning(f"[PCAP] Interleaver disabled: {e}")
        send_pcap = lambda: None

    # ---- Rate / Duration ----
    src_rc = stream_data.get("stream_rate_control", {}) or {}
    stream_rate_type = (
            src_rc.get("stream_rate_type")
            or stream_data.get("stream_rate_type")
            or stream_data.get("protocol_selection", {}).get("stream_rate_type")
            or ("Packets Per Second (PPS)")
    )

    # Normalize duration from tx_duration or legacy fields
    tx_duration = stream_data.get("tx_duration", {}) or {}
    if tx_duration.get("mode"):
        duration_mode = tx_duration.get("mode")
        duration_seconds = int(tx_duration.get("seconds") or 0) if duration_mode == "Seconds" else None
    else:
        duration_mode = stream_data.get("stream_duration_mode", "Continuous")
        duration_seconds = int(stream_data.get("stream_duration_seconds") or 0) if duration_mode == "Seconds" else None

    if str(stream_rate_type).strip().lower() == "line rate":
        requested = int(stream_data.get("batch_size", 512) or 512)
        cap = int(stream_data.get("batch_size_cap", 256) or 256)  # safe default
        batch_size = max(64, min(requested, cap))
        interval = 0.000001
        logging.info(f"[Rate] line_rate interval={interval:.6f}, batch_size={batch_size} (requested={requested}, cap={cap})")
    else:
        interval, batch_size = calculate_interval(stream_rate_type, stream_data)


    start_time = time.time()

    def _maybe_stop_on_max():
        if max_packets and stream_tracker.get_tx_count_by_id(interface, stream_id) >= max_packets:
            stop_event.set()
            on_stream_stopped(interface, stream_id, reason="max_packets")
            return True
        return False

    # ---- RoCEv2 + ibperf ----
    if l4_sel == "RoCEv2" and stream_data.get("use_ibperf", False):
        start_ibperf_server(stream_data, stop_event)
        return

    # ---- RoCEv2 ----
    if l4_sel == "RoCEv2":
        try:
            pkts = generate_rocev2_packet(stream_data)
        except Exception as e:
            logging.error(f"[RoCEv2] generate_rocev2_packet() error: {e}")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        if pkts is None:
            logging.error("[RoCEv2] packet generator returned None")
            on_stream_stopped(interface, stream_id, reason="error")
            return
        if not isinstance(pkts, (list, tuple)):
            pkts = [pkts]

        logging.info("[RoCEv2] TX loop enter")
        while not stop_event.is_set():
            try:
                send_pcap()
            except Exception as e:
                logging.debug(f"[PCAP interleave] skipped: {e}")

            try:
                to_send = []
                for pkt in pkts:
                    for _ in range(batch_size):
                        to_send.append(add_sig(pkt.copy()))
                if to_send:
                    sendp(to_send, iface=interface, verbose=False)
                    for _ in range(len(to_send)):
                        stream_tracker.update_tx_by_id(interface, stream_id)
            except Exception as e:
                logging.warning(f"[RoCEv2] send failed: {e}")

            if _maybe_stop_on_max():
                return
            if interval > 0:
                time.sleep(interval)
            if duration_mode == "Seconds" and time.time() - start_time >= duration_seconds:
                stop_event.set()
                break

        on_stream_stopped(interface, stream_id, reason="complete")
        return

    # ---- UEC ----
    if l4_sel == "UEC":
        mac = protocol_data.get("mac", {}) or {}
        src_mac = mac.get("mac_source_address") or stream_data.get("mac_source_address")
        dst_mac = mac.get("mac_destination_address") or stream_data.get("mac_destination_address")

        if not src_mac or not dst_mac:
            logging.error("[UEC] missing MAC addresses (src/dst). Check protocol_data.mac.* fields.")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        uec = stream_data.get("uec", {}) or protocol_data.get("uec", {}) or {}
        qp_start = int(uec.get("qp_start", 1000))
        qp_end   = int(uec.get("qp_end", qp_start))
        pasid_start = int(uec.get("pasid_start", 5000))
        pasid_end   = int(uec.get("pasid_end", pasid_start))
        qp_range = range(qp_start, qp_end + 1)
        pasid_range = range(pasid_start, pasid_end + 1)

        idx = 0
        logging.info("[UEC] TX loop enter")
        while not stop_event.is_set():
            try:
                send_pcap()
            except Exception as e:
                logging.debug(f"[PCAP interleave] skipped: {e}")

            qp = qp_range[idx % len(qp_range)]
            pasid = pasid_range[idx % len(pasid_range)]

            try:
                pkt = generate_uec_rocev2_packet(src_mac, dst_mac, qp, pasid, stream_data)
                if pkt is None:
                    logging.error("[UEC] packet generator returned None")
                    on_stream_stopped(interface, stream_id, reason="error")
                    return

                to_send = []
                for _ in range(batch_size):
                    to_send.append(add_sig(pkt.copy()))

                sendp(to_send, iface=interface, verbose=False)
                for _ in range(len(to_send)):
                    stream_tracker.update_tx_by_id(interface, stream_id)
            except Exception as e:
                logging.warning(f"[UEC] send failed: {e}")

            if _maybe_stop_on_max():
                return
            if interval > 0:
                time.sleep(interval)
            if duration_mode == "Seconds" and time.time() - start_time >= duration_seconds:
                stop_event.set()
                break
            idx += 1

        on_stream_stopped(interface, stream_id, reason="complete")
        return

    # ---- ARP (L3) ----
    if (stream_data.get("L3") or protocol_selection.get("L3") or "").strip() == "ARP":
        try:
            pkt = generate_arp_packet(stream_data)
        except Exception as e:
            logging.error(f"[ARP] generate_arp_packet() error: {e}")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        logging.info("[ARP] TX loop enter")
        while not stop_event.is_set():
            try:
                # Interleave PCAP if configured
                send_pcap()
            except Exception as e:
                logging.debug(f"[PCAP interleave] skipped: {e}")

            try:
                to_send = []
                for _ in range(batch_size):
                    to_send.append(add_sig(pkt.copy()))
                if to_send:
                    sendp(to_send, iface=interface, verbose=False)
                    for _ in range(len(to_send)):
                        stream_tracker.update_tx_by_id(interface, stream_id)
            except Exception as e:
                logging.warning(f"[ARP] send failed: {e}")

            if _maybe_stop_on_max():
                return
            if interval > 0:
                time.sleep(interval)

            if duration_mode == "Seconds" and time.time() - start_time >= duration_seconds:
                stop_event.set()
                break

        on_stream_stopped(interface, stream_id, reason="complete")
        return

    # ---- Generic ----
    try:
        pkt_cfg = get_packet_config(stream_data)
    except Exception as e:
        logging.error(f"[Generic] get_packet_config() error: {e}")
        on_stream_stopped(interface, stream_id, reason="error")
        return

    logging.info("[Generic] TX loop enter")
    index = 0
    while not stop_event.is_set():
        try:
            send_pcap()
        except Exception as e:
            logging.debug(f"[PCAP interleave] skipped: {e}")

        try:
            pkt = build_generic_packet(
                stream_data, pkt_cfg,
                vlan_id=pkt_cfg["vlan_ids"][index % len(pkt_cfg["vlan_ids"])],
                src_mac=pkt_cfg["mac_src_list"][index % len(pkt_cfg["mac_src_list"])],
                dst_mac=pkt_cfg["mac_dst_list"][index % len(pkt_cfg["mac_dst_list"])],
                src_ip=pkt_cfg["ipv4_src_list"][index % len(pkt_cfg["ipv4_src_list"])],
                dst_ip=pkt_cfg["ipv4_dst_list"][index % len(pkt_cfg["ipv4_dst_list"])],
                src_ipv6=pkt_cfg["ipv6_src_list"][index % len(pkt_cfg["ipv6_src_list"])],
                dst_ipv6=pkt_cfg["ipv6_dst_list"][index % len(pkt_cfg["ipv6_dst_list"])],
                tcp_sport=pkt_cfg["tcp_sport_list"][index % len(pkt_cfg["tcp_sport_list"])],
                tcp_dport=pkt_cfg["tcp_dport_list"][index % len(pkt_cfg["tcp_dport_list"])],
                tcp_seq=pkt_cfg["tcp_seq_list"][index % len(pkt_cfg["tcp_seq_list"])],
                udp_sport=pkt_cfg["udp_sport_list"][index % len(pkt_cfg["udp_sport_list"])],
                udp_dport=pkt_cfg["udp_dport_list"][index % len(pkt_cfg["udp_dport_list"])]
            )
        except Exception as e:
            logging.error(f"[Generic] build_generic_packet() error: {e}")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        try:
            to_send = []
            for _ in range(batch_size):
                to_send.append(add_sig(pkt.copy()))
            if to_send:
                sendp(to_send, iface=interface, verbose=False)
                for _ in range(len(to_send)):
                    stream_tracker.update_tx_by_id(interface, stream_id)
        except Exception as e:
            logging.warning(f"[Generic] send failed: {e}")

        if _maybe_stop_on_max():
            return
        if interval > 0:
            time.sleep(interval)
        index += 1

        if duration_mode == "Seconds" and time.time() - start_time >= duration_seconds:
            stop_event.set()
            break

    on_stream_stopped(interface, stream_id, reason="complete")