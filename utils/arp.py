# utils/arp.py
"""
ARP + IPv6 NDP packet builder for OSTG.

Reads ARP + MAC + VLAN fields from the stream_data dict produced by the GUI:
  - stream_data["protocol_data"]["arp"] = {
        "arp_operation": "Request" | "Reply",
        "arp_sender_mac": "00:11:22:33:44:55",
        "arp_sender_ip":  "10.0.0.1",
        "arp_target_mac": "ff:ff:ff:ff:ff:ff",
        "arp_target_ip":  "10.0.0.2",
    }
  - stream_data["protocol_data"]["mac"] = {
        "mac_source_address": "...",
        "mac_destination_address": "..."
    }
  - stream_data["protocol_data"]["vlan"] = {
        "vlan_tagged": True/False,
        "vlan_id": "100",
        "vlan_priority": "0",
        "vlan_tpid": "0x8100"
    }

Returns a Scapy packet: Ether(/Dot1Q)/ARP
"""

from scapy.layers.l2 import Ether, ARP, Dot1Q

# v0.5.262 (audit ARP-9): scapy imports for IPv6 NDP. Kept inside the
# module — the ARP-only path doesn't touch these classes.
try:
    from scapy.layers.inet6 import (
        IPv6, ICMPv6ND_NS, ICMPv6ND_NA, ICMPv6NDOptSrcLLAddr,
        ICMPv6NDOptDstLLAddr,
    )
    _NDP_AVAILABLE = True
except Exception:
    _NDP_AVAILABLE = False

def _pick(*vals, default=None):
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s != "":
            return s
    return default

def _pick_int(*vals, default=None):
    for v in vals:
        try:
            if v is None:
                continue
            s = str(v).strip()
            if s == "":
                continue
            return int(s, 0) if (isinstance(v, str) and v.lower().startswith("0x")) else int(float(s))
        except Exception:
            continue
    return default

def generate_arp_packet(stream_data):
    """
    Build a single ARP Request/Reply packet from stream_data.
    """
    pd = (stream_data.get("protocol_data") or {}) or {}
    arp_pd = (pd.get("arp") or {}) or {}
    mac_pd = (pd.get("mac") or {}) or {}
    vlan_pd = (pd.get("vlan") or {}) or {}

    # ARP fields
    op_str = _pick(arp_pd.get("arp_operation"), default="Request")
    op = 1 if str(op_str).lower().startswith("req") else 2

    sender_mac = _pick(arp_pd.get("arp_sender_mac"), mac_pd.get("mac_source_address"),
                       stream_data.get("mac_source_address"), default="00:11:22:33:44:55")
    target_mac = _pick(arp_pd.get("arp_target_mac"), mac_pd.get("mac_destination_address"),
                       stream_data.get("mac_destination_address"), default="ff:ff:ff:ff:ff:ff")
    sender_ip  = _pick(arp_pd.get("arp_sender_ip"),  stream_data.get("src_ip"), default="0.0.0.0")
    target_ip  = _pick(arp_pd.get("arp_target_ip"),  stream_data.get("dst_ip"), default="0.0.0.0")

    # L2 envelope
    eth_src = _pick(mac_pd.get("mac_source_address"), stream_data.get("mac_source_address"),
                    default=sender_mac)
    # Default dst MAC:
    # - For ARP Request, dst is broadcast unless explicitly set
    # - For ARP Reply, dst is target_mac (unicast) unless overridden
    if op == 1:
        eth_dst_default = "ff:ff:ff:ff:ff:ff"
    else:
        eth_dst_default = target_mac or "ff:ff:ff:ff:ff:ff"

    eth_dst = _pick(mac_pd.get("mac_destination_address"), stream_data.get("mac_destination_address"),
                    default=eth_dst_default)

    # Optional VLAN
    vlan_tagged = str(vlan_pd.get("vlan_tagged", "False")).lower() in ("1", "true", "yes", "on")
    vlan_id     = _pick_int(vlan_pd.get("vlan_id"), default=None)
    vlan_pcp    = _pick_int(vlan_pd.get("vlan_priority"), default=0)
    vlan_tpid   = _pick(vlan_pd.get("vlan_tpid"), default="0x8100")
    try:
        vlan_tpid = int(vlan_tpid, 0)
    except Exception:
        vlan_tpid = 0x8100

    # Build ARP
    # v0.5.258 (audit ARP-3): RFC 826 defines the ARP Request
    # payload's target hardware address as "not known" → all zeros.
    # Pre-fix we wrote target_mac (default ff:ff:ff:ff:ff:ff) into
    # both Ether.dst (correct: L2 broadcast) AND ARP.hwdst
    # (wrong: payload should be zeros). Some IDS/IPS classify
    # `hwdst == ffffff…` in a Request as "malformed / ARP scan"
    # and either drop the frame or generate an alert. Only clobber
    # payload hwdst to zeros when the caller left it at the
    # broadcast default; an explicitly-supplied hwdst wins.
    arp_hwdst = target_mac
    if op == 1 and not (arp_pd.get("arp_target_mac") or "").strip():
        arp_hwdst = "00:00:00:00:00:00"
    arp = ARP(
        op=op,                  # 1=request, 2=reply
        hwsrc=sender_mac,
        psrc=sender_ip,
        hwdst=arp_hwdst,
        pdst=target_ip,
    )

    # Ether + optional Dot1Q
    # v0.5.258 (audit ARP-2): apply vlan_tpid to the outer Ether
    # type field. Pre-fix vlan_tpid was parsed but never used — a
    # GUI-supplied 0x88A8 (802.1ad S-VLAN) or 0x9100 was silently
    # dropped, so QinQ / provider-bridge switches configured to
    # strip the outer 0x88A8 tag discarded the frame at ingress
    # and ARP appeared to fail with no diagnostic.
    if vlan_tagged and vlan_id not in (None, "", 0, "0"):
        pkt = Ether(src=eth_src, dst=eth_dst, type=vlan_tpid) / \
              Dot1Q(vlan=int(vlan_id), prio=int(vlan_pcp), type=0x0806) / arp
    else:
        pkt = Ether(src=eth_src, dst=eth_dst, type=0x0806) / arp

    return pkt


def _ipv6_solicited_node_multicast(addr: str) -> str:
    """Return the solicited-node multicast address ff02::1:ff00:0/104
    with the last 24 bits of the target IPv6 address. Per RFC 4861 §7.2.1,
    NS packets are sent to the SN multicast address, not the target
    itself, so the target need not have installed a listen for its own
    unicast address before the NS arrives."""
    import ipaddress
    try:
        n = int(ipaddress.IPv6Address(addr))
    except Exception:
        return "ff02::1:ff00:0"
    low24 = n & 0xFFFFFF
    # ff02::1:ff00:0000/104 → ff02:0:0:0:0:1:ff:<low24>
    return f"ff02::1:ff{low24 >> 16:02x}:{low24 & 0xFFFF:04x}"


def _ipv6_solicited_node_multicast_mac(ipv6: str) -> str:
    """RFC 2464 §7 mapping: `33:33:ff:xx:xx:xx` where xxxxxx is the
    last 24 bits of the solicited-node group (== last 24 bits of the
    target IPv6). L2 destination for NS frames."""
    import ipaddress
    try:
        n = int(ipaddress.IPv6Address(ipv6))
    except Exception:
        return "33:33:ff:00:00:00"
    low24 = n & 0xFFFFFF
    return (
        f"33:33:ff:{(low24 >> 16) & 0xff:02x}"
        f":{(low24 >> 8) & 0xff:02x}:{low24 & 0xff:02x}"
    )


def generate_ndp_packet(stream_data):
    """v0.5.262 (audit ARP-9): build an IPv6 Neighbor Solicitation
    (op=NS) or Neighbor Advertisement (op=NA) packet. Same
    stream_data shape as `generate_arp_packet` — reads the ARP
    dict for `arp_operation` / `arp_sender_ip` / `arp_target_ip`
    / MACs, plus optional VLAN.

    NS is the IPv6 equivalent of ARP Request: sent to the
    solicited-node multicast (`ff02::1:ff<low-24>`) with the
    target address in the ICMPv6 payload. NA is the reply:
    unicast back to the requester with the resolved MAC in an
    NDOptDstLLAddr TLV.

    Raises RuntimeError if scapy's inet6 module isn't importable
    (should never happen on a supported host, but we surface it
    instead of dying later).
    """
    if not _NDP_AVAILABLE:
        raise RuntimeError(
            "scapy.layers.inet6 unavailable — NDP builder cannot run"
        )
    pd = (stream_data.get("protocol_data") or {}) or {}
    arp_pd = (pd.get("arp") or {}) or {}
    mac_pd = (pd.get("mac") or {}) or {}
    vlan_pd = (pd.get("vlan") or {}) or {}

    op_str = _pick(arp_pd.get("arp_operation"), default="Request")
    is_ns = str(op_str).lower().startswith("req")  # "Request" → NS, "Reply" → NA

    sender_mac = _pick(
        arp_pd.get("arp_sender_mac"),
        mac_pd.get("mac_source_address"),
        stream_data.get("mac_source_address"),
        default="00:11:22:33:44:55",
    )
    sender_ip = _pick(
        arp_pd.get("arp_sender_ip"),
        stream_data.get("src_ip"),
        default="::",
    )
    target_ip = _pick(
        arp_pd.get("arp_target_ip"),
        stream_data.get("dst_ip"),
        default="::",
    )
    explicit_target_mac = _pick(arp_pd.get("arp_target_mac"), default=None)

    # L3 + L2 destination.
    if is_ns:
        # Neighbor Solicitation → solicited-node multicast.
        ip_dst = _ipv6_solicited_node_multicast(target_ip)
        eth_dst_default = _ipv6_solicited_node_multicast_mac(target_ip)
        icmp = ICMPv6ND_NS(tgt=target_ip) / ICMPv6NDOptSrcLLAddr(lladdr=sender_mac)
    else:
        # Neighbor Advertisement → unicast to requester.
        ip_dst = target_ip
        eth_dst_default = explicit_target_mac or "33:33:00:00:00:01"  # all-nodes fallback
        icmp = ICMPv6ND_NA(tgt=sender_ip, R=0, S=1, O=1) / \
               ICMPv6NDOptDstLLAddr(lladdr=sender_mac)

    eth_src = _pick(
        mac_pd.get("mac_source_address"),
        stream_data.get("mac_source_address"),
        default=sender_mac,
    )
    eth_dst = _pick(
        mac_pd.get("mac_destination_address"),
        stream_data.get("mac_destination_address"),
        default=eth_dst_default,
    )

    # Optional VLAN — same treatment as ARP.
    vlan_tagged = str(vlan_pd.get("vlan_tagged", "False")).lower() in ("1", "true", "yes", "on")
    vlan_id = _pick_int(vlan_pd.get("vlan_id"), default=None)
    vlan_pcp = _pick_int(vlan_pd.get("vlan_priority"), default=0)
    vlan_tpid = _pick(vlan_pd.get("vlan_tpid"), default="0x8100")
    try:
        vlan_tpid = int(vlan_tpid, 0)
    except Exception:
        vlan_tpid = 0x8100

    ipv6 = IPv6(src=sender_ip, dst=ip_dst, hlim=255)  # hlim=255 mandatory per RFC 4861 §7.1.1
    if vlan_tagged and vlan_id not in (None, "", 0, "0"):
        pkt = Ether(src=eth_src, dst=eth_dst, type=vlan_tpid) / \
              Dot1Q(vlan=int(vlan_id), prio=int(vlan_pcp), type=0x86dd) / \
              ipv6 / icmp
    else:
        pkt = Ether(src=eth_src, dst=eth_dst, type=0x86dd) / ipv6 / icmp

    return pkt