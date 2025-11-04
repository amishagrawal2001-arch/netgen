# utils/generic.py
import logging
import random

from scapy.all import (
    Ether, IP, IPv6, UDP, TCP, ICMP, Raw, Dot1Q, fragment
)
from scapy.contrib.igmp import IGMP
from scapy.contrib.mpls import MPLS

from utils.helpers import increment_ip, increment_ipv6, increment_mac
from utils.udp import build_udp_l4


# ---------- helpers ----------
def parse_dscp(value) -> int:
    """
    Accept DSCP as decimal/hex string or common names: cs0..cs7, ef, af11..af43.
    Returns an int in [0..63].
    """
    if value is None:
        return 0
    s = str(value).strip().lower()

    # numeric (dec or hex)
    try:
        if s.startswith("0x"):
            return int(s, 16) & 0x3F
        if s.isdigit():
            return int(s, 10) & 0x3F
    except Exception:
        pass

    # class selector: cs0..cs7 => (n << 3)
    if s.startswith("cs") and s[2:].isdigit():
        n = int(s[2:])
        if 0 <= n <= 7:
            return (n << 3) & 0x3F

    # expedited forwarding
    if s == "ef":
        return 46  # 0x2e

    # assured forwarding afXY, X=1..4, Y=1..3; DSCP=8*X + 2*Y
    if s.startswith("af") and len(s) == 4 and s[2].isdigit() and s[3].isdigit():
        x = int(s[2]); y = int(s[3])
        if 1 <= x <= 4 and 1 <= y <= 3:
            return (8 * x + 2 * y) & 0x3F

    return 0


def parse_tcp_flags(flag_input: str) -> str:
    valid = {
        "F": "F", "FIN": "F",
        "S": "S", "SYN": "S",
        "R": "R", "RST": "R",
        "P": "P", "PSH": "P",
        "A": "A", "ACK": "A",
        "U": "U", "URG": "U",
        "E": "E", "ECE": "E",
        "C": "C", "CWR": "C",
    }
    flags = "".join(valid.get(tok.strip().upper(), "") for tok in flag_input.replace("+", " ").split())
    return flags or "S"


# ---------- packet builder ----------
def build_generic_packet(stream_data, pkt_cfg, vlan_id,
                         src_mac=None, dst_mac=None,
                         src_ip=None, dst_ip=None,
                         src_ipv6=None, dst_ipv6=None,
                         tcp_sport=None, tcp_dport=None,
                         tcp_seq=None,
                         udp_sport=None, udp_dport=None):
    protocol_selection = stream_data.get("protocol_selection", {}) or {}
    protocol_data = stream_data.get("protocol_data", {}) or {}

    l2 = protocol_selection.get("L2", "Ethernet II")
    l3 = protocol_selection.get("L3", "IPv4")
    l4 = protocol_selection.get("L4", "UDP")

    # Base Ether
    pkt = Ether(src=src_mac or pkt_cfg["mac_src_list"][0],
                dst=dst_mac or pkt_cfg["mac_dst_list"][0])

    # --- VLAN (802.1Q) with PCP/DEI and optional TPID override ---
    try:
        if vlan_id is not None and int(vlan_id) > 0:
            vlan_cfg = protocol_data.get("vlan", {}) or {}
            vlan_kwargs = {
                "vlan": int(vlan_id),
                "id":   int(vlan_id),                         # scapy synonym; harmless
                "prio": int(vlan_cfg.get("vlan_priority", 0)) & 0x7,
                "dei":  int(vlan_cfg.get("vlan_cfi_dei", 0)) & 0x1,
            }

            # Optional TPID override (e.g. 0x88A8)
            if stream_data.get("override_settings", {}).get("override_vlan_tpid"):
                tpid_str = vlan_cfg.get("vlan_tpid", "81 00")
                try:
                    tpid_val = int(tpid_str.replace(" ", ""), 16)
                    # rebuild Ether with explicit EtherType so Scapy keeps it
                    pkt = Ether(src=pkt.src, dst=pkt.dst, type=tpid_val)
                except Exception as e:
                    logging.warning(f"[VLAN] Invalid TPID '{tpid_str}', keeping default 0x8100: {e}")

            pkt /= Dot1Q(**vlan_kwargs)
    except Exception as e:
        logging.warning(f"[VLAN] Invalid VLAN ID '{vlan_id}', skipping tag: {e}")

    # --- MPLS (if selected) ---
    if l2 == "MPLS":
        mpls = protocol_data.get("mpls", {}) or {}
        pkt /= MPLS(
            label=int(mpls.get("mpls_label", 16)),
            ttl=int(mpls.get("mpls_ttl", 64)),
            cos=int(mpls.get("mpls_experimental", 0)),
        )

    # --- L3 ---
    if l3 == "IPv4":
        ipv4 = protocol_data.get("ipv4", {}) or {}
        tos_mode = ipv4.get("tos_dscp_mode", "TOS")
        ecn_bits = {"Not-ECT": 0b00, "ECT(1)": 0b01, "ECT(0)": 0b10, "CE": 0b11}.get(ipv4.get("ipv4_ecn", "Not-ECT"), 0)

        if tos_mode == "DSCP":
            dscp = parse_dscp(ipv4.get("ipv4_dscp", 0))
            tos = ((dscp & 0x3F) << 2) | (ecn_bits & 0x03)
        elif tos_mode == "Custom":
            tos = int(ipv4.get("ipv4_custom_tos", 0)) & 0xFF
        else:
            prec_map = {
                "Routine": 0, "Priority": 1, "Immediate": 2, "Flash": 3,
                "Flash Override": 4, "Critical": 5, "Internetwork Control": 6, "Network Control": 7
            }
            prec = prec_map.get(ipv4.get("ipv4_tos", "Routine"), 0) & 0x07
            tos = (prec << 5) | ecn_bits

        flags = 0
        if ipv4.get("ipv4_df"): flags |= 0x2
        if ipv4.get("ipv4_mf"): flags |= 0x1

        pkt /= IP(
            src=src_ip or pkt_cfg["ipv4_src_list"][0],
            dst=dst_ip or pkt_cfg["ipv4_dst_list"][0],
            ttl=int(ipv4.get("ipv4_ttl", 64)),
            tos=tos,
            id=int(ipv4.get("ipv4_identification", 0)),
            flags=flags,
            frag=int(ipv4.get("ipv4_fragment_offset", 0)),
        )

    elif l3 == "IPv6":
        ipv6 = protocol_data.get("ipv6", {}) or {}
        pkt /= IPv6(
            src=src_ipv6 or pkt_cfg.get("ipv6_src_list", ["2001:db8::1"])[0],
            dst=dst_ipv6 or pkt_cfg.get("ipv6_dst_list", ["2001:db8::2"])[0],
            hlim=int(ipv6.get("ipv6_hop_limit", 64)),
            tc=int(ipv6.get("ipv6_traffic_class", 0)),
            fl=int(ipv6.get("ipv6_flow_label", 0)),
        )

    # --- L4 ---
    if l4 == "UDP":
        # All UDP (including DHCPv4/v6 and DNS) is handled in utils.udp
        pkt = build_udp_l4(
            pkt, stream_data, pkt_cfg,
            udp_sport=udp_sport, udp_dport=udp_dport
        )

    elif l4 == "TCP":
        tcp = protocol_data.get("tcp", {}) or {}
        flags = parse_tcp_flags(tcp.get("tcp_flags", "SYN") or "SYN")
        try:
            pkt /= TCP(
                sport=int(tcp_sport or pkt_cfg.get("tcp_sport_list", [1234])[0]),
                dport=int(tcp_dport or pkt_cfg.get("tcp_dport_list", [80])[0]),
                flags=flags,
                seq=int(tcp_seq or pkt_cfg.get("tcp_seq_list", [0])[0]),
                ack=int(tcp.get("tcp_acknowledgement_number", 0)),
                window=int(tcp.get("tcp_window", 1024)),
            )
        except Exception as e:
            logging.warning(f"[TCP] Error building TCP layer: {e}")

    elif l4 == "ICMP":
        pkt /= ICMP()

    elif l4 == "IGMP":
        igmp = protocol_data.get("igmp", {}) or {}
        igmp_type = int(igmp.get("igmp_type", 0x16))
        igmp_maddr = dst_ip or igmp.get("igmp_group_address", "224.0.0.1")

        # ensure IPv4 w/ proto=IGMP and TTL=1 (overwrite any existing IP/IPv6)
        if IPv6 in pkt:
            try: pkt[IPv6].underlayer.remove_payload()
            except Exception: pass
        if IP in pkt:
            try: pkt[IP].underlayer.remove_payload()
            except Exception: pass

        pkt /= IP(src=src_ip or pkt_cfg["ipv4_src_list"][0], dst=igmp_maddr, ttl=1, proto=2) / IGMP(
            type=igmp_type, gaddr=igmp_maddr
        )

    # --- Payload/signature (non-UDP only; UDP payload is set in utils.udp) ---
    if l4 != "UDP":
        payload_hex = (protocol_data.get("payload_data", {}) or {}).get("payload_data", "")
        try:
            user_data = bytes.fromhex(payload_hex) if payload_hex else b""
        except Exception:
            user_data = b""

        if stream_data.get("flow_tracking_enabled"):
            sig = f"[{stream_data.get('stream_id')}]".encode()
            user_data = sig + user_data

        if user_data:
            pkt /= Raw(load=user_data)

    # Optional IPv4 fragmentation
    if stream_data.get("enable_fragmentation") and l3 == "IPv4":
        try:
            return fragment(pkt, fragsize=24)[0]
        except Exception as e:
            logging.warning(f"[IPv4] Fragmentation error: {e}")

    return pkt


# ---------- config expansion ----------
def get_packet_config(stream_data):
    protocol_data = stream_data.get("protocol_data", {}) or {}
    mac = protocol_data.get("mac", {}) or {}
    vlan = protocol_data.get("vlan", {}) or {}
    ipv4 = protocol_data.get("ipv4", {}) or {}
    ipv6 = protocol_data.get("ipv6", {}) or {}
    tcp  = protocol_data.get("tcp", {})  or {}
    udp  = protocol_data.get("udp", {})  or {}

    # VLANs
    vlan_id_str = str(vlan.get("vlan_id", "")).strip()
    vlan_id = int(vlan_id_str) if vlan_id_str.isdigit() else 1
    vlan_count = int(vlan.get("vlan_increment_count", 1))
    vlan_step  = int(vlan.get("vlan_increment_value", 1))
    vlan_increment = bool(vlan.get("vlan_increment", False))
    vlan_ids = [vlan_id + i * vlan_step for i in range(vlan_count)] if vlan_increment else [vlan_id]

    # MACs
    mac_src_list = [mac.get("mac_source_address")]
    if mac.get("mac_source_mode") == "Increment":
        step = int(mac.get("mac_source_step", 1)); count = int(mac.get("mac_source_count", 1))
        mac_src_list = [increment_mac(mac_src_list[0], step * i) for i in range(count)]

    mac_dst_list = [mac.get("mac_destination_address")]
    if mac.get("mac_destination_mode") == "Increment":
        step = int(mac.get("mac_destination_step", 1)); count = int(mac.get("mac_destination_count", 1))
        mac_dst_list = [increment_mac(mac_dst_list[0], step * i) for i in range(count)]

    # IPv4
    ipv4_src_list = [ipv4.get("ipv4_source")]
    if ipv4.get("ipv4_source_mode") == "Increment":
        step = int(ipv4.get("ipv4_source_increment_step", 1)); count = int(ipv4.get("ipv4_source_increment_count", 1))
        ipv4_src_list = [increment_ip(ipv4_src_list[0], step * i) for i in range(count)]

    ipv4_dst_list = [ipv4.get("ipv4_destination")]
    if ipv4.get("ipv4_destination_mode") == "Increment":
        step = int(ipv4.get("ipv4_destination_increment_step", 1)); count = int(ipv4.get("ipv4_destination_increment_count", 1))
        ipv4_dst_list = [increment_ip(ipv4_dst_list[0], step * i) for i in range(count)]

    # IPv6
    ipv6_src_list = [ipv6.get("ipv6_source")]
    if ipv6.get("ipv6_source_mode") == "Increment":
        step = int(ipv6.get("ipv6_source_increment_step", 1)); count = int(ipv6.get("ipv6_source_increment_count", 1))
        ipv6_src_list = [increment_ipv6(ipv6_src_list[0], step * i) for i in range(count)]

    ipv6_dst_list = [ipv6.get("ipv6_destination")]
    if ipv6.get("ipv6_destination_mode") == "Increment":
        step = int(ipv6.get("ipv6_destination_increment_step", 1)); count = int(ipv6.get("ipv6_destination_increment_count", 1))
        ipv6_dst_list = [increment_ipv6(ipv6_dst_list[0], step * i) for i in range(count)]

    # TCP ports
    tcp_sport_list = [int(tcp.get("tcp_source_port", 12345))]
    if tcp.get("tcp_increment_source_port"):
        start = int(tcp.get("tcp_source_port", 12345)); step = int(tcp.get("tcp_source_port_step", 1)); count = int(tcp.get("tcp_source_port_count", 1))
        tcp_sport_list = [start + step * i for i in range(count)]

    tcp_dport_list = [int(tcp.get("tcp_destination_port", 80))]
    if tcp.get("tcp_increment_destination_port"):
        start = int(tcp.get("tcp_destination_port", 80)); step = int(tcp.get("tcp_destination_port_step", 1)); count = int(tcp.get("tcp_destination_port_count", 1))
        tcp_dport_list = [start + step * i for i in range(count)]

    # TCP sequence
    tcp_seq_list = [int(tcp.get("tcp_sequence_number", 0))]
    if tcp.get("tcp_sequence_count"):
        start = int(tcp.get("tcp_sequence_number", 0)); step = int(tcp.get("tcp_sequence_step", 1)); count = int(tcp.get("tcp_sequence_count", 1))
        tcp_seq_list = [start + step * i for i in range(count)]

    # UDP ports
    udp_sport_list = [int(udp.get("udp_source_port", 1234))]
    if udp.get("udp_increment_source_port"):
        start = int(udp.get("udp_source_port", 1234)); step = int(udp.get("udp_source_port_step", 1)); count = int(udp.get("udp_source_port_count", 1))
        udp_sport_list = [start + step * i for i in range(count)]

    udp_dport_list = [int(udp.get("udp_destination_port", 80))]
    if udp.get("udp_increment_destination_port"):
        start = int(udp.get("udp_destination_port", 80)); step = int(udp.get("udp_destination_port_step", 1)); count = int(udp.get("udp_destination_port_count", 1))
        udp_dport_list = [start + step * i for i in range(count)]

    return {
        "vlan_ids": vlan_ids,
        "mac_src_list": mac_src_list,
        "mac_dst_list": mac_dst_list,
        "ipv4_src_list": ipv4_src_list,
        "ipv4_dst_list": ipv4_dst_list,
        "ipv6_src_list": ipv6_src_list,
        "ipv6_dst_list": ipv6_dst_list,
        "tcp_sport_list": tcp_sport_list,
        "tcp_dport_list": tcp_dport_list,
        "tcp_seq_list": tcp_seq_list,
        "udp_sport_list": udp_sport_list,
        "udp_dport_list": udp_dport_list,
        "tcp_flag_string": parse_tcp_flags(tcp.get("tcp_flags", "")),
    }
