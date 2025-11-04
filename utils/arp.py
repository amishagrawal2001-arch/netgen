# utils/arp.py
"""
ARP packet builder for OSTG.

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
    arp = ARP(
        op=op,                  # 1=request, 2=reply
        hwsrc=sender_mac,
        psrc=sender_ip,
        hwdst=target_mac,
        pdst=target_ip,
    )

    # Ether + optional Dot1Q
    if vlan_tagged and vlan_id not in (None, "", 0, "0"):
        pkt = Ether(src=eth_src, dst=eth_dst) / Dot1Q(vlan=int(vlan_id), prio=int(vlan_pcp), type=0x0806) / arp
    else:
        pkt = Ether(src=eth_src, dst=eth_dst, type=0x0806) / arp

    return pkt