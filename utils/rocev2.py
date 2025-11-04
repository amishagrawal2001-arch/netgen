# utils/rocev2.py
from scapy.all import Ether, Dot1Q, IP, IPv6, UDP, Raw
import logging

def _tos_from_ipv4(ipv4_cfg):
    # DSCP/ECN handling compatible with your generic builder
    tos_mode = ipv4_cfg.get("tos_dscp_mode", "TOS")
    ecn_map = {"Not-ECT": 0b00, "ECT(1)": 0b01, "ECT(0)": 0b10, "CE": 0b11}
    ecn_bits = ecn_map.get(ipv4_cfg.get("ipv4_ecn", "Not-ECT"), 0)

    if tos_mode == "DSCP":
        try:
            dscp_val = int(ipv4_cfg.get("ipv4_dscp", 0))
        except Exception:
            dscp_val = 0
        return ((dscp_val & 0x3F) << 2) | (ecn_bits & 0x03)

    if tos_mode == "Custom":
        try:
            return int(ipv4_cfg.get("ipv4_custom_tos", 0)) & 0xFF
        except Exception:
            return ecn_bits

    prec_map = {
        "Routine": 0, "Priority": 1, "Immediate": 2, "Flash": 3,
        "Flash Override": 4, "Critical": 5, "Internetwork Control": 6, "Network Control": 7
    }
    prec = prec_map.get(ipv4_cfg.get("ipv4_tos", "Routine"), 0) & 0x07
    return (prec << 5) | ecn_bits

def build_rocev2_payload(stream_data):
    """
    Minimal 'BTH-like' payload. Real RoCEv2 stacks will ignore it,
    but it's valid UDP and perfect for RX tagging.
    """
    r = (stream_data.get("protocol_data", {}) or {}).get("rocev2", {}) or {}
    qp = int(r.get("rocev2_destination_qp", 0))
    op = (r.get("rocev2_opcode", "SendOnly") or "SendOnly").encode()
    return b"RCV2|" + op + b"|DQPN=" + str(qp).encode() + b"|"

def generate_rocev2_packet(stream_data):
    """
    Returns a list with a single valid Ethernet/IP/UDP(dport=4791) packet carrying a RoCEv2-like payload.
    VLAN / IPv4 / IPv6 are honored from stream_data.
    """
    ps  = stream_data.get("protocol_selection", {}) or {}
    pd  = stream_data.get("protocol_data", {}) or {}
    mac = pd.get("mac", {}) or {}
    vlan_cfg = pd.get("vlan", {}) or {}
    ipv4_cfg = pd.get("ipv4", {}) or {}
    ipv6_cfg = pd.get("ipv6", {}) or {}

    src_mac = mac.get("mac_source_address", "00:00:00:00:00:02")
    dst_mac = mac.get("mac_destination_address", "ff:ff:ff:ff:ff:ff")

    l3 = ps.get("L3", "IPv4")
    vlan_mode = ps.get("VLAN", "Untagged")
    vlan_id = int(vlan_cfg.get("vlan_id", 0) or 0)
    pcp = int(vlan_cfg.get("vlan_priority", 0) or 0) & 0x7
    dei = int(vlan_cfg.get("vlan_cfi_dei", 0) or 0) & 0x1

    # L2
    eth = Ether(src=src_mac, dst=dst_mac)
    if vlan_mode == "Tagged" and vlan_id > 0:
        eth /= Dot1Q(vlan=vlan_id, prio=pcp, id=dei)

    # L3
    if l3 == "IPv6":
        ip = IPv6(
            src=ipv6_cfg.get("ipv6_source", "2001:db8::1"),
            dst=ipv6_cfg.get("ipv6_destination", "2001:db8::2"),
            hlim=int(ipv6_cfg.get("ipv6_hop_limit", 64)),
            tc=int(ipv6_cfg.get("ipv6_traffic_class", 0)),
            fl=int(ipv6_cfg.get("ipv6_flow_label", 0))
        )
    else:
        flags = 0
        if ipv4_cfg.get("ipv4_df"): flags |= 0x2
        if ipv4_cfg.get("ipv4_mf"): flags |= 0x1
        ip = IP(
            src=ipv4_cfg.get("ipv4_source", "10.0.0.1"),
            dst=ipv4_cfg.get("ipv4_destination", "10.0.0.2"),
            ttl=int(ipv4_cfg.get("ipv4_ttl", 64)),
            tos=_tos_from_ipv4(ipv4_cfg),
            id=int(ipv4_cfg.get("ipv4_identification", 0)),
            flags=flags,
            frag=int(ipv4_cfg.get("ipv4_fragment_offset", 0))
        )

    # L4 (RoCEv2 runs over UDP/4791)
    sport = 4791  # keep simple; can be randomized if you like
    dport = 4791

    udp = UDP(sport=sport, dport=dport)

    # Minimal payload
    payload = build_rocev2_payload(stream_data)

    pkt = eth / ip / udp / Raw(load=payload)
    # Let Scapy recompute lengths/checksums
    try:
        if UDP in pkt:
            del pkt[UDP].len
            del pkt[UDP].chksum
    except Exception:
        pass
    try:
        if IP in pkt:
            del pkt[IP].len
            del pkt[IP].chksum
    except Exception:
        pass
    try:
        if IPv6 in pkt:
            del pkt[IPv6].plen
    except Exception:
        pass

    return [pkt]
