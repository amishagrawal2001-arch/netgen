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
from utils.generic import build_generic_packet, get_packet_config, _apply_frame_size
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
                "future": stream.get("future"),  # Track Future object to wait for thread completion
                "frame_size": stream.get("frame_size", 64),  # Store frame_size for statistics
                "tx_count": 0,
                "rx_count": 0
            })

    # ----- by-stream_id TX increment -----
    def update_tx_by_id(self, interface, stream_id, count=1):
        """Increment TX counter by count (default 1). Optimized for batch updates."""
        with self.lock:
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_id"] == stream_id:
                    s["tx_count"] += count
                    return

    # ----- runtime engine + fallback markers (v0.2.77) -----
    def mark_runtime_engine(self, interface, stream_id, *,
                            runtime_engine=None, fallback_reason=None):
        """Annotate a tracked stream with its actual runtime engine
        and (when fallback happened) a one-line reason.

        Called from the launcher when the worker had to swap engines
        mid-flight — e.g. tx_worker exits with rc=100 (Broadcom ULP
        error) and we fall back to Scapy. The stats endpoint surfaces
        these so the GUI can show "Scapy (was DPDK)" in the Engine
        column and the operator stops wondering why throughput is lower.
        """
        with self.lock:
            for s in self.active_streams:
                if (s.get("interface") == interface
                        and s.get("stream_id") == stream_id):
                    if runtime_engine:
                        s["runtime_engine"] = runtime_engine
                    if fallback_reason:
                        s["runtime_fallback_reason"] = fallback_reason
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
                    "frame_size": s.get("frame_size", 64),  # Include frame_size in stats
                }
                # v0.2.77: surface runtime engine + runtime fallback reason
                # when the launcher had to swap engines mid-flight (e.g.
                # tx_worker rc=100 → Scapy). Optional; omitted from the
                # JSON when never set so the legacy shape stays clean.
                if s.get("runtime_engine"):
                    item["runtime_engine"] = s["runtime_engine"]
                if s.get("runtime_fallback_reason"):
                    item["runtime_fallback_reason"] = s["runtime_fallback_reason"]
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

    def find_streams_by_name(self, interface, stream_name):
        """Find all streams with matching name on the given interface."""
        with self.lock:
            matches = []
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_name"] == stream_name:
                    matches.append(s)
            return matches

    def remove_stream_by_id(self, interface, stream_id):
        with self.lock:
            # Find the stream to get rx_interface for sniffer unregistration
            stream_to_remove = None
            for s in self.active_streams:
                if s["interface"] == interface and s["stream_id"] == stream_id:
                    stream_to_remove = s
                    break
            
            # Unregister sniffer if flow tracking was enabled
            if stream_to_remove and stream_to_remove.get("flow_tracking_enabled"):
                rx_interface = stream_to_remove.get("rx_interface")
                if rx_interface:
                    self._sniffers.discard((rx_interface, stream_id))
            
            # Remove from active_streams
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

    # Check if packet has BTH (RoCEv2) - if so, append signature after BTH
    try:
        from scapy.contrib.roce import BTH
        if BTH in pkt:
            # For RoCEv2 with BTH, append signature as Raw payload after BTH
            # This preserves the BTH structure while still allowing RX tracking
            pkt = pkt / Raw(load=sig)
            return pkt
    except ImportError:
        pass  # BTH not available, continue with normal handling

    # Always embed a marker for any UDP payload; otherwise add/append Raw
    try:
        if UDP in pkt:
            up = bytes(getattr(pkt[UDP], "payload", b"") or b"")
            try:
                pkt[UDP].remove_payload()
            except Exception:
                pass
            # For RoCEv2, if payload starts with BTH-like binary data, append sig after it
            # Otherwise prepend sig (for backward compatibility)
            if len(up) >= 12 and up[:5] != b"RCV2|":  # Likely a BTH (12 bytes), not text
                pkt = pkt / Raw(load=(up + sig))  # Append signature after BTH
            else:
                pkt = pkt / Raw(load=(sig + up))  # Prepend signature (backward compat)
        else:
            if Raw in pkt and getattr(pkt[Raw], "load", None) is not None:
                try:
                    raw_load = bytes(pkt[Raw].load)
                    # If it looks like BTH (12+ bytes, not starting with text), append sig
                    if len(raw_load) >= 12 and raw_load[:5] != b"RCV2|":
                        pkt[Raw].load = raw_load + sig
                    else:
                        pkt[Raw].load = raw_load + sig  # Append for consistency
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

    # Build separate IPv4 and IPv6 expressions (they can't both be true)
    ip4_expr = None
    ip6_expr = None

    # L3 (IPv4)
    ip4_src = selector.get("src_ip")
    ip4_dst = selector.get("dst_ip")
    if ip4_src and ip4_dst:
        ip4_expr = f"(host {ip4_src} or host {ip4_dst})"
    elif ip4_src:
        ip4_expr = f"host {ip4_src}"
    elif ip4_dst:
        ip4_expr = f"host {ip4_dst}"

    # L3 (IPv6)
    ip6_src = selector.get("src_ip6")
    ip6_dst = selector.get("dst_ip6")
    if ip6_src and ip6_dst:
        ip6_expr = f"(host {ip6_src} or host {ip6_dst})"
    elif ip6_src:
        ip6_expr = f"host {ip6_src}"
    elif ip6_dst:
        ip6_expr = f"host {ip6_dst}"

    # Combine IPv4 and IPv6 with OR (packet can be either IPv4 OR IPv6, not both)
    l3_terms = []
    if ip4_expr:
        l3_terms.append(ip4_expr)
    if ip6_expr:
        l3_terms.append(f"(ip6 and {ip6_expr})")
    
    l3_expr = None
    if len(l3_terms) == 2:
        l3_expr = f"({l3_terms[0]} or {l3_terms[1]})"
    elif len(l3_terms) == 1:
        l3_expr = l3_terms[0]

    # L4
    l4 = (selector.get("l4") or "").lower()
    sport = selector.get("sport")
    dport = selector.get("dport")

    l4_expr = None
    if l4 == "icmp":
        l4_expr = "icmp or icmp6"
    elif l4 == "udp":
        if sport and dport:
            l4_expr = f"udp and ((src port {sport} and dst port {dport}) or (src port {dport} and dst port {sport}))"
        elif sport:
            l4_expr = f"udp and (src port {sport} or dst port {sport})"
        elif dport:
            l4_expr = f"udp and (src port {dport} or dst port {dport})"
        else:
            l4_expr = "udp"
    elif l4 == "tcp":
        if sport and dport:
            l4_expr = f"tcp and ((src port {sport} and dst port {dport}) or (src port {dport} and dst port {sport}))"
        elif sport:
            l4_expr = f"tcp and (src port {sport} or dst port {sport})"
        elif dport:
            l4_expr = f"tcp and (src port {dport} or dst port {dport})"
        else:
            l4_expr = "tcp"

    # Combine L3 and L4
    # Need to properly parenthesize L4 if it contains 'or' to avoid operator precedence issues
    inner_parts = []
    if l3_expr:
        inner_parts.append(f"({l3_expr})")
    if l4_expr:
        # Wrap L4 in parentheses if it contains 'or' to ensure proper precedence
        if " or " in l4_expr:
            inner_parts.append(f"({l4_expr})")
        else:
            inner_parts.append(l4_expr)
    
    if inner_parts:
        inner = " and ".join(inner_parts)
    else:
        # Nothing to filter on? fall back so lfilter() can do the work.
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
def calculate_interval(rate_type, stream_data, default_size=64):
    """
    Returns (interval_seconds_between_batches, batch_size).
    Robustly resolves rate from stream_rate_control, top-level, protocol_selection, or tx_rate.
    Accepts 'Packets Per Second (PPS)', 'Bit Rate', 'Bit Rate (bps)', 'Bit Rate (Mbps)', 'Load (%)', 'Line Rate'.
    
    Note: For Random/IMIX frame types, rate calculations use the fixed frame_size value.
    Actual bit rates may vary due to variable frame sizes.
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
            # Check normalized values first (from stream_rate_control), then fall back to tx_rate
            pps = pick_int(
                rate_control.get("stream_pps_rate"),
                stream_data.get("stream_pps_rate"),
                ps.get("stream_pps_rate"),
                tx_rate.get("pps") if isinstance(tx_rate, dict) else None,
                default=0
            )
            logging.info(f"[Rate Calc] PPS mode - rate_control={rate_control.get('stream_pps_rate')}, stream_data={stream_data.get('stream_pps_rate')}, ps={ps.get('stream_pps_rate')}, tx_rate={tx_rate.get('pps') if isinstance(tx_rate, dict) else None}, resolved={pps}")

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

        # Optimized batch sizes for better performance
        # Larger batches reduce loop overhead and improve throughput
        # For all rates, use larger batch sizes to minimize overhead from packet copying
        # and system calls. This trades latency for throughput.
        if pps <= 100: batch_size = 25  # Small batches for low rates
        elif pps <= 500: batch_size = 100  # Medium batches
        elif pps <= 1_000: batch_size = 200  # Larger batches - we build 1 packet then copy it, so this is fast
        elif pps <= 5_000: batch_size = 500  # Increased from 200 to 500
        elif pps <= 10_000: batch_size = 1_000  # Increased from 500 to 1000
        elif pps <= 50_000: batch_size = 2_000  # Increased from 1000 to 2000
        elif pps <= 100_000: batch_size = 5_000  # Increased from 2000 to 5000
        elif pps <= 1_000_000: batch_size = 10_000  # Increased from 5_000 to 10_000
        else: batch_size = 20_000

        # Ensure batch_size doesn't exceed pps (for very low rates)
        batch_size = min(batch_size, max(pps, 1))
        # Calculate interval: time between batches to achieve target pps
        # For 1000 pps with batch_size=200: interval = 200/1000 = 0.2 seconds
        # This means send 200 packets every 0.2s = 1000 pps
        interval = batch_size / max(pps, 1)
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
    
    # Log frame_size for debugging
    protocol_selection = stream_data.get("protocol_selection", {}) or {}
    frame_size = (protocol_selection.get("frame_size") or 
                 stream_data.get("frame_size") or 
                 64)
    frame_type = (protocol_selection.get("frame_type") or 
                 stream_data.get("frame_type") or 
                 "Fixed")
    try:
        frame_size = int(frame_size)
    except (ValueError, TypeError):
        frame_size = 64
    logging.info(f"[TX] Stream '{stream_name}' frame_type={frame_type}, frame_size={frame_size} bytes")

    flow_tracking_enabled = bool(stream_data.get("flow_tracking_enabled", False))
    max_packets = int(stream_data.get("max_packets", 0))  # 0 => unlimited

    # Normalize rx_port - handle "Same as TX Port" and various formats
    rx_port = stream_data.get("rx_port") or interface
    if isinstance(rx_port, str):
        # Handle "Same as TX Port" - use TX interface
        if "Same as TX Port" in rx_port or rx_port.strip() == "Same as TX Port":
            rx_interface = interface
        else:
            # Extract interface name from various formats: "TG X - Port: interface", "Port: interface", "interface"
            rx_interface = str(rx_port).split("Port:")[-1].strip()
            if not rx_interface or rx_interface == rx_port:
                # If no "Port:" found, use the whole string as interface name
                rx_interface = str(rx_port).strip()
    else:
        rx_interface = interface
    stream_data["rx_interface"] = rx_interface

    protocol_selection = stream_data.get("protocol_selection", {}) or {}
    protocol_data = stream_data.get("protocol_data", {}) or {}
    l4_sel = (stream_data.get("L4") or protocol_selection.get("L4") or "").strip()

    # ---- register stream row (before sniffer) ----
    # NOTE: Stream is already registered in stream_tracker by launch_single_stream()
    # This is just to ensure it exists if called directly (defensive programming)
    rx_thread = None
    # Only add if not already present (avoid duplicate registration)
    existing = stream_tracker.find_stream_by_id(interface, stream_id)
    if not existing:
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
    # This is the ONLY place where RX sniffer should be started (with proper selector)
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
            
            # Update the stream_tracker entry with the rx_thread (for both existing and newly added streams)
            if rx_thread:
                if existing:
                    existing["rx_thread"] = rx_thread
                else:
                    # Find the stream we just added and update it
                    stream_entry = stream_tracker.find_stream_by_id(interface, stream_id)
                    if stream_entry:
                        stream_entry["rx_thread"] = rx_thread
        else:
            logging.warning(f"[RX] RX interface '{rx_interface}' is DOWN, skipping RX sniffer")


    # ---- DPDK/tx_worker branch (if requested) ----
    try:
        if _DPDK_AVAILABLE and _dpdk_backend.should_use_dpdk(stream_data):
            # Check if multi-instance mode is requested (for high rates: 100Gbps+)
            use_multi_instance = stream_data.get("dpdk_multi_instance", False)
            target_pps = None
            try:
                from utils.dpdk_tx_worker import _resolve_target_pps
                target_pps = _resolve_target_pps(stream_data)
            except Exception:
                pass
            
            # Auto-enable multi-instance for high rates or line rate
            if not use_multi_instance and (target_pps == 0 or (target_pps and target_pps > 50_000_000)):
                # Line rate or > 50M pps - use multi-instance
                use_multi_instance = True
                logging.info("[DPDK] Auto-enabling multi-instance mode for high-rate stream (target_pps=%s)", target_pps)
            
            if use_multi_instance:
                # Try to use multi-instance backend
                try:
                    import utils.dpdk_tx_worker_multi as _dpdk_multi_backend
                    logging.info("[DPDK] using multi-instance tx_worker backend for '%s' on %s", stream_name, interface)
                    stream_data["stream_id"] = stream_id  # ensure ID is set
                    rc = _dpdk_multi_backend.run_stream_multi_instance(stream_data, interface, stop_event, stream_tracker)
                    reason = "complete" if rc == 0 else f"dpdk_multi_rc={rc}"
                    on_stream_stopped(interface, stream_id, reason=reason)
                    return
                except ImportError:
                    logging.warning("[DPDK] Multi-instance backend not available, falling back to single-instance")
                except Exception as e:
                    logging.warning("[DPDK] Multi-instance handoff failed (%s); falling back to single-instance", e)
            
            # Single-instance DPDK backend
            logging.info("[DPDK] using tx_worker backend for '%s' on %s", stream_name, interface)
            stream_data["stream_id"] = stream_id  # ensure ID is set
            rc = _dpdk_backend.run_stream(stream_data, interface, stop_event, stream_tracker)
            
            # Check for Broadcom ULP error (exit code 100) - automatically fallback to Scapy
            if rc == 100:
                logging.warning("[DPDK] Broadcom ULP initialization error detected for stream '%s'", stream_name)
                
                # Check if interface is bound to DPDK - if so, Scapy won't work
                import os
                is_bound_to_dpdk = False
                try:
                    # Get PCI address from interface
                    pci_path = f"/sys/class/net/{interface}/device"
                    if os.path.exists(pci_path):
                        pci = os.path.basename(os.path.realpath(pci_path))
                        driver_path = f"/sys/bus/pci/devices/{pci}/driver"
                        if os.path.exists(driver_path):
                            driver = os.path.basename(os.path.realpath(driver_path))
                            if driver == "vfio-pci":
                                is_bound_to_dpdk = True
                except Exception:
                    pass
                
                if is_bound_to_dpdk:
                    logging.error("[DPDK] Interface '%s' is bound to DPDK (vfio-pci). Cannot fallback to Scapy.", interface)
                    logging.error("[DPDK] To use Scapy backend, please unbind the interface from DPDK first:")
                    logging.error("[DPDK]   Use 'Unbind Interface from DPDK' in the DPDK menu")
                    logging.error("[DPDK]   Or manually: /opt/OSTG/resources/dpdk/dpdk_bind.sh unbind <PCI_ADDRESS>")
                    reason = "dpdk_ulp_error_interface_bound"
                    on_stream_stopped(interface, stream_id, reason=reason)
                    return
                
                logging.warning("[DPDK] Automatically falling back to Scapy backend...")
                # Disable DPDK for this stream and continue with Scapy path
                # Remove DPDK flags to prevent retry
                if "protocol_selection" in stream_data:
                    stream_data["protocol_selection"]["dpdk_enable"] = False
                    stream_data["protocol_selection"]["use_dpdk"] = False
                stream_data["dpdk_enable"] = False
                stream_data["use_dpdk"] = False
                stream_data["engine"] = "scapy"
                # v0.2.77: mark the runtime fallback on the tracker so
                # the stats endpoint surfaces it back to the GUI. The
                # operator sees "Scapy (was DPDK: Broadcom ULP init
                # error)" in the Engine column instead of wondering
                # why throughput dropped.
                try:
                    stream_tracker.mark_runtime_engine(
                        interface, stream_id,
                        runtime_engine="scapy",
                        fallback_reason=(
                            "tx_worker hit Broadcom ULP initialization "
                            "error (rc=100); fell back to Scapy at runtime"
                        ),
                    )
                except Exception:
                    pass
                # Continue to Scapy path below
            elif rc != 0:
                logging.error("[DPDK] tx_worker failed for stream '%s' (id=%s) with exit code %d", stream_name, stream_id, rc)
                logging.error("[DPDK] Check server logs for detailed error messages")
                reason = f"dpdk_rc={rc}"
                on_stream_stopped(interface, stream_id, reason=reason)
                return
            else:
                # DPDK succeeded
                reason = "complete"
                on_stream_stopped(interface, stream_id, reason=reason)
                return
        elif (_dpdk_backend if _DPDK_AVAILABLE else None) and not _dpdk_backend.should_use_dpdk(stream_data):
            logging.debug("[DPDK] backend available but stream not requesting it (engine/scalar flags).")
        else:
            if not _DPDK_AVAILABLE:
                logging.debug("[DPDK] backend not available; falling back to Scapy.")
    except Exception as e:
        logging.warning("[DPDK] handoff failed (%s); falling back to Scapy path.", e)
        # v0.2.77: also mark this catch-all fallback so the stats
        # endpoint surfaces it. Any exception caught here means the
        # DPDK launcher tripped before completing handoff — operator
        # deserves to know without reading server logs.
        try:
            stream_tracker.mark_runtime_engine(
                interface, stream_id,
                runtime_engine="scapy",
                fallback_reason=f"DPDK handoff failed at runtime: {e}",
            )
        except Exception:
            pass

    # ---- Check if interface is available for Scapy (not bound to DPDK) ----
    # If DPDK handoff didn't happen, we need to ensure the interface is available for Scapy
    try:
        import subprocess
        # Check if interface exists in kernel
        result = subprocess.run(
            ["ip", "link", "show", interface],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode != 0:
            # Interface doesn't exist - check if it's bound to DPDK
            try:
                # Get PCI address from interface name
                pci_path = f"/sys/class/net/{interface}/device"
                import os
                if os.path.exists(pci_path):
                    pci = os.path.basename(os.path.realpath(pci_path))
                    # Check if bound to vfio-pci
                    driver_path = f"/sys/bus/pci/devices/{pci}/driver"
                    if os.path.exists(driver_path):
                        driver = os.path.basename(os.path.realpath(driver_path))
                        if driver == "vfio-pci":
                            logging.error(
                                f"[Scapy] Interface '{interface}' (PCI: {pci}) is bound to DPDK (vfio-pci). "
                                f"Cannot use Scapy backend. Please unbind it from DPDK first using: "
                                f"DPDK menu -> Unbind Interface, or use DPDK backend instead."
                            )
                            return  # Exit early - can't generate packets
            except Exception:
                pass
            # If we can't determine the reason, log a generic error
            logging.error(
                f"[Scapy] Interface '{interface}' not found. "
                f"It may be bound to DPDK. Please unbind it first or use DPDK backend."
            )
            return  # Exit early - can't generate packets
    except Exception as e:
        logging.debug(f"[Scapy] Interface check failed: {e}")

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
    # Normalize tx_rate from client format to server format
    tx_rate = stream_data.get("tx_rate", {}) or {}
    logging.info(f"[Rate Normalization] Received tx_rate: {tx_rate}")
    if isinstance(tx_rate, dict) and tx_rate.get("mode"):
        # Client sends: {"mode": "pps", "pps": 100} or {"mode": "bps", "bps": 1000000}, etc.
        mode = tx_rate.get("mode", "").lower()
        logging.info(f"[Rate Normalization] Mode: {mode}, pps value: {tx_rate.get('pps')}")
        if mode == "pps":
            # Convert to server format
            pps_value = tx_rate.get("pps", 0)
            if "stream_rate_control" not in stream_data:
                stream_data["stream_rate_control"] = {}
            stream_data["stream_rate_control"]["stream_rate_type"] = "Packets Per Second (PPS)"
            stream_data["stream_rate_control"]["stream_pps_rate"] = str(pps_value)
            stream_data["stream_pps_rate"] = str(pps_value)
            logging.info(f"[Rate Normalization] Set stream_pps_rate to {pps_value} in stream_rate_control and top-level")
        elif mode == "bps":
            if "stream_rate_control" not in stream_data:
                stream_data["stream_rate_control"] = {}
            # Determine if it's Mbps or bps based on value
            bps_value = tx_rate.get("bps", 0)
            if bps_value >= 1_000_000:
                stream_data["stream_rate_control"]["stream_rate_type"] = "Bit Rate (Mbps)"
                stream_data["stream_rate_control"]["stream_bit_rate"] = str(bps_value / 1_000_000)
            else:
                stream_data["stream_rate_control"]["stream_rate_type"] = "Bit Rate (bps)"
                stream_data["stream_rate_control"]["stream_bit_rate"] = str(bps_value)
        elif mode == "load":
            if "stream_rate_control" not in stream_data:
                stream_data["stream_rate_control"] = {}
            stream_data["stream_rate_control"]["stream_rate_type"] = "Load (%)"
            stream_data["stream_rate_control"]["stream_load_percentage"] = str(tx_rate.get("percent", 0))
        elif mode == "line":
            if "stream_rate_control" not in stream_data:
                stream_data["stream_rate_control"] = {}
            stream_data["stream_rate_control"]["stream_rate_type"] = "Line Rate"
    
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


    # Use perf_counter for better timing precision (monotonic, higher resolution)
    start_time = time.perf_counter()

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
        # Get packet config with increment lists (for GID, QP, MAC, IP increments)
        try:
            pkt_cfg = get_packet_config(stream_data)
        except Exception as e:
            logging.error(f"[RoCEv2] get_packet_config() error: {e}")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        logging.info("[RoCEv2] TX loop enter")
        # Use precise timing: track target time instead of fixed sleep
        next_batch_time = start_time
        index = 0  # Index for cycling through increment lists
        
        while not stop_event.is_set():
            try:
                send_pcap()
            except Exception as e:
                logging.debug(f"[PCAP interleave] skipped: {e}")

            try:
                # Generate packet with current index (for GID/QP/MAC/IP increments)
                pkts = generate_rocev2_packet(stream_data, pkt_cfg=pkt_cfg, index=index)
                if pkts is None:
                    logging.error("[RoCEv2] packet generator returned None")
                    break
                if not isinstance(pkts, (list, tuple)):
                    pkts = [pkts]
                
                to_send = []
                for pkt in pkts:
                    # Apply frame size padding
                    pkt = _apply_frame_size(pkt, stream_data)
                    for _ in range(batch_size):
                        to_send.append(add_sig(pkt.copy()))
                
                if to_send:
                    sendp(to_send, iface=interface, verbose=False)
                    # Optimize: increment counter by batch_size instead of per-packet loop
                    stream_tracker.update_tx_by_id(interface, stream_id, count=len(to_send))
                
                # Increment index for next iteration (cycles through increment lists)
                index += 1
            except Exception as e:
                logging.warning(f"[RoCEv2] send failed: {e}")

            if _maybe_stop_on_max():
                return
            
            # Precise timing: calculate when next batch should be sent
            if interval > 0:
                next_batch_time += interval
                current_time = time.perf_counter()
                sleep_time = next_batch_time - current_time
                if sleep_time > 0:
                    # Only sleep if we're ahead of schedule
                    time.sleep(sleep_time)
                # If we're behind schedule, continue immediately (don't sleep negative time)
            
            if duration_mode == "Seconds" and time.perf_counter() - start_time >= duration_seconds:
                stop_event.set()
                break

        on_stream_stopped(interface, stream_id, reason="complete")
        return

    # ---- UEC ----
    if l4_sel == "UEC":
        # Get packet config with increment lists (for MAC, IP, GID increments)
        try:
            pkt_cfg = get_packet_config(stream_data)
        except Exception as e:
            logging.error(f"[UEC] get_packet_config() error: {e}")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        mac = protocol_data.get("mac", {}) or {}
        src_mac_default = mac.get("mac_source_address") or stream_data.get("mac_source_address")
        dst_mac_default = mac.get("mac_destination_address") or stream_data.get("mac_destination_address")

        if not src_mac_default or not dst_mac_default:
            logging.error("[UEC] missing MAC addresses (src/dst). Check protocol_data.mac.* fields.")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        uec = stream_data.get("uec", {}) or protocol_data.get("uec", {}) or {}
        qp_start = int(uec.get("qp_start", 1000))
        qp_end   = int(uec.get("qp_end", qp_start))
        pasid_start = int(uec.get("pasid_start", 5000))
        pasid_end   = int(uec.get("pasid_end", pasid_start))
        enable_spray = bool(uec.get("enable_spray", False))
        
        # Build QP and PASID ranges/lists
        if enable_spray:
            qp_range = list(range(qp_start, qp_end + 1))
            pasid_range = list(range(pasid_start, pasid_end + 1))
            if not qp_range:
                qp_range = [qp_start]
            if not pasid_range:
                pasid_range = [pasid_start]
        else:
            # Fixed values when spray is disabled
            qp_range = [qp_start]
            pasid_range = [pasid_start]

        index = 0  # Index for cycling through increment lists
        logging.info(f"[UEC] TX loop enter - QP range: {qp_start}-{qp_end}, PASID range: {pasid_start}-{pasid_end}, Spray: {enable_spray}")
        # Use precise timing: track target time instead of fixed sleep
        # Initialize next_batch_time to start_time so first batch is sent immediately
        next_batch_time = start_time
        
        while not stop_event.is_set():
            # Wait until it's time to start the next batch
            if interval > 0:
                current_time = time.perf_counter()
                sleep_time = next_batch_time - current_time
                if sleep_time > 0:
                    time.sleep(sleep_time)
                # If we're behind schedule, don't reset - just continue immediately
                # This maintains the correct interval spacing even if we fall behind
            
            # Record batch start time for accurate interval calculation
            batch_start_time = time.perf_counter()
            
            try:
                send_pcap()
            except Exception as e:
                logging.debug(f"[PCAP interleave] skipped: {e}")

            # Get incremented MAC addresses from pkt_cfg
            src_mac = pkt_cfg.get("mac_src_list", [src_mac_default])[index % len(pkt_cfg.get("mac_src_list", [""]))]
            dst_mac = pkt_cfg.get("mac_dst_list", [dst_mac_default])[index % len(pkt_cfg.get("mac_dst_list", [""]))]
            
            # Get incremented IP addresses from pkt_cfg (for IPv4/IPv6 increment support)
            src_ip = pkt_cfg.get("ipv4_src_list", [None])[index % len(pkt_cfg.get("ipv4_src_list", [None]))]
            dst_ip = pkt_cfg.get("ipv4_dst_list", [None])[index % len(pkt_cfg.get("ipv4_dst_list", [None]))]
            src_ipv6 = pkt_cfg.get("ipv6_src_list", [None])[index % len(pkt_cfg.get("ipv6_src_list", [None]))]
            dst_ipv6 = pkt_cfg.get("ipv6_dst_list", [None])[index % len(pkt_cfg.get("ipv6_dst_list", [None]))]
            
            # Get QP and PASID (cycle through ranges if spray enabled)
            qp = qp_range[index % len(qp_range)]
            pasid = pasid_range[index % len(pasid_range)]

            try:
                pkt = generate_uec_rocev2_packet(src_mac, dst_mac, qp, pasid, stream_data, 
                                                 src_ip=src_ip, dst_ip=dst_ip, 
                                                 src_ipv6=src_ipv6, dst_ipv6=dst_ipv6)
                if pkt is None:
                    logging.error("[UEC] packet generator returned None")
                    on_stream_stopped(interface, stream_id, reason="error")
                    return

                # Apply frame size padding
                pkt = _apply_frame_size(pkt, stream_data)

                to_send = []
                for _ in range(batch_size):
                    to_send.append(add_sig(pkt.copy()))

                sendp(to_send, iface=interface, verbose=False)
                # Optimize: increment counter by batch_size instead of per-packet loop
                stream_tracker.update_tx_by_id(interface, stream_id, count=len(to_send))
            except Exception as e:
                logging.warning(f"[UEC] send failed: {e}")

            if _maybe_stop_on_max():
                return
            
            # Update next_batch_time for the next batch (after sending this one)
            # The interval is the time between batch starts, so we add it to the time when this batch started
            if interval > 0:
                next_batch_time = batch_start_time + interval
            
            if duration_mode == "Seconds" and time.perf_counter() - start_time >= duration_seconds:
                stop_event.set()
                break
            
            # Increment index for next iteration (cycles through increment lists and QP/PASID ranges)
            index += 1

        on_stream_stopped(interface, stream_id, reason="complete")
        return

    # ---- ARP (L3) ----
    if (stream_data.get("L3") or protocol_selection.get("L3") or "").strip() == "ARP":
        try:
            pkt = generate_arp_packet(stream_data)
            # Apply frame size padding
            pkt = _apply_frame_size(pkt, stream_data)
        except Exception as e:
            logging.error(f"[ARP] generate_arp_packet() error: {e}")
            on_stream_stopped(interface, stream_id, reason="error")
            return

        logging.info("[ARP] TX loop enter")
        # Use precise timing: track target time instead of fixed sleep
        next_batch_time = start_time
        
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
                    # Optimize: increment counter by batch_size instead of per-packet loop
                    stream_tracker.update_tx_by_id(interface, stream_id, count=len(to_send))
            except Exception as e:
                logging.warning(f"[ARP] send failed: {e}")

            if _maybe_stop_on_max():
                return
            
            # Precise timing: calculate when next batch should be sent
            if interval > 0:
                next_batch_time += interval
                current_time = time.perf_counter()
                sleep_time = next_batch_time - current_time
                if sleep_time > 0:
                    # Only sleep if we're ahead of schedule
                    time.sleep(sleep_time)
                # If we're behind schedule, continue immediately (don't sleep negative time)

            if duration_mode == "Seconds" and time.perf_counter() - start_time >= duration_seconds:
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
    
    # Use precise timing: track target time instead of fixed sleep
    # This compensates for overhead and improves rate accuracy
    # The interval is the time between when one batch STARTS and when the next batch STARTS
    # Initialize next_batch_time to start_time so first batch is sent immediately
    next_batch_time = start_time
    
    while not stop_event.is_set():
        # Wait until it's time to start the next batch
        if interval > 0:
            current_time = time.perf_counter()
            sleep_time = next_batch_time - current_time
            if sleep_time > 0:
                # Sleep until it's time to start the batch
                time.sleep(sleep_time)
            # If we're behind schedule, don't reset - just continue immediately
            # This maintains the correct interval spacing even if we fall behind
            # The next_batch_time will be updated after sending to maintain spacing
        
        # Record when this batch actually starts (after any sleep)
        batch_start_time = time.perf_counter()
        
        try:
            send_pcap()
        except Exception as e:
            logging.debug(f"[PCAP interleave] skipped: {e}")

        try:
            # Build ONE packet per iteration (using index to select from increment lists)
            # Then copy it batch_size times - this is much faster than building 25 different packets
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
            # Copy the packet batch_size times - this is fast (just memory copy)
            to_send = []
            for _ in range(batch_size):
                to_send.append(add_sig(pkt.copy()))
            if to_send:
                sendp(to_send, iface=interface, verbose=False)
                # Increment counter by actual number of packets sent
                stream_tracker.update_tx_by_id(interface, stream_id, count=len(to_send))
        except Exception as e:
            logging.warning(f"[Generic] send failed: {e}")

        if _maybe_stop_on_max():
            return
        
        # Update next_batch_time for the next batch (after sending this one)
        # The interval is the time between batch starts, so we add it to the time when this batch started
        if interval > 0:
            batch_end_time = time.perf_counter()
            batch_duration = batch_end_time - batch_start_time
            next_batch_time = batch_start_time + interval
            
            # Log timing info for debugging (only occasionally to avoid spam)
            if index % (batch_size * 10) == 0:  # Log every 10 batches
                logging.info(f"[Rate Timing] Batch {index//batch_size}: duration={batch_duration*1000:.2f}ms, target_interval={interval*1000:.2f}ms")
                if batch_duration > interval:
                    logging.warning(f"[Rate Timing] Batch took {batch_duration*1000:.2f}ms which is LONGER than interval {interval*1000:.2f}ms - rate will be lower than target!")
                # Calculate effective rate
                if batch_duration > 0:
                    effective_pps = batch_size / batch_duration
                    logging.info(f"[Rate Timing] Effective rate for this batch: {effective_pps:.1f} pps (target: {batch_size/interval:.1f} pps)")

        # Increment index AFTER sending the batch (so next iteration uses next MAC/IP/port)
        index += 1

        if duration_mode == "Seconds" and time.perf_counter() - start_time >= duration_seconds:
            stop_event.set()
            break

    on_stream_stopped(interface, stream_id, reason="complete")