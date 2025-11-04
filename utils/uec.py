#uec.py

from utils.rocev2 import build_rocev2_payload  # ✅ Required import
from scapy.all import Ether, Dot1Q, IP, IPv6, UDP, Raw

def generate_uec_rocev2_packet(src_mac, dst_mac, qp, pasid, stream_data):
    protocol_sel  = stream_data.get("protocol_selection", {}) or {}
    protocol_data = stream_data.get("protocol_data", {}) or {}
    mac_cfg       = protocol_data.get("mac", {}) or {}
    vlan_cfg      = protocol_data.get("vlan", {}) or {}
    ipv4_cfg      = protocol_data.get("ipv4", {}) or {}
    ipv6_cfg      = protocol_data.get("ipv6", {}) or {}
    uec           = stream_data.get("uec", {}) or protocol_data.get("uec", {}) or {}
    roce_cfg      = stream_data.get("rocev2", {}) or protocol_data.get("rocev2", {}) or {}

    # Resolve L3
    l3_protocol = (stream_data.get("L3")
                   or protocol_sel.get("L3")
                   or "IPv4")

    # MACs (prefer args, then protocol_data.mac)
    src_mac = src_mac or mac_cfg.get("mac_source_address") or "00:00:00:00:00:02"
    dst_mac = dst_mac or mac_cfg.get("mac_destination_address") or "00:00:00:00:00:01"

    # VLAN
    vlan_id_str = str(vlan_cfg.get("vlan_id", "")).strip()
    vlan_id = int(vlan_id_str) if vlan_id_str.isdigit() else 0
    pcp = int(vlan_cfg.get("vlan_priority", 0)) & 0x7
    dei = int(vlan_cfg.get("vlan_cfi_dei", 0)) & 0x1
    tpid_override = bool(stream_data.get("override_settings", {}).get("override_vlan_tpid", False))
    tpid_val = None
    if tpid_override:
        try:
            tpid_val = int(str(vlan_cfg.get("vlan_tpid", "81 00")).replace(" ", ""), 16)
        except Exception:
            tpid_val = 0x8100

    # ECN handling (lowest 2 bits of IPv4 TOS / IPv6 TC)
    ecn_map = {"Not-ECT": 0b00, "ECT(1)": 0b01, "ECT(0)": 0b10, "CE": 0b11}
    ecn = ecn_map.get(uec.get("ecn", "Not-ECT"), 0)

    # Base payload
    base_payload = f"UEC QP={qp} PASID={pasid}".encode()

    # Optional RoCEv2 “embedding” inside payload (your earlier behavior)
    if uec.get("enable_rocev2", False):
        opcode     = roce_cfg.get("rocev2_opcode", "SendOnly")
        solicited  = bool(roce_cfg.get("rocev2_solicited_event", False))
        mig_req    = bool(roce_cfg.get("rocev2_migration_req", False))
        rocev2_blob = build_rocev2_payload(opcode, qp, solicited, mig_req)
        base_payload += b"\n" + rocev2_blob

    # Build L2
    if vlan_id > 0:
        if tpid_override and tpid_val:
            pkt = Ether(src=src_mac, dst=dst_mac, type=tpid_val) / Dot1Q(prio=pcp, dei=dei, vlan=vlan_id)
        else:
            pkt = Ether(src=src_mac, dst=dst_mac) / Dot1Q(prio=pcp, dei=dei, vlan=vlan_id)
    else:
        pkt = Ether(src=src_mac, dst=dst_mac)

    # L3/L4 (choose IPv4 or IPv6 properly)
    if l3_protocol == "IPv6":
        ip_src = ipv6_cfg.get("ipv6_source", "2001:db8::1")
        ip_dst = ipv6_cfg.get("ipv6_destination", "2001:db8::2")
        tc     = int(ipv6_cfg.get("ipv6_traffic_class", 0)) & 0xFC  # clear ECN bits
        tc    |= (ecn & 0x3)
        pkt  /= IPv6(src=ip_src, dst=ip_dst,
                     hlim=int(ipv6_cfg.get("ipv6_hop_limit", 64)),
                     tc=tc,
                     fl=int(ipv6_cfg.get("ipv6_flow_label", 0)))
        pkt  /= UDP(sport=12345, dport=4791)
    else:
        ip_src = ipv4_cfg.get("ipv4_source", "10.0.0.1")
        ip_dst = ipv4_cfg.get("ipv4_destination", "11.0.0.2")
        # Set DSCP=0 for now; put ECN in lowest 2 bits
        tos = (0 << 2) | (ecn & 0x3)
        pkt /= IP(src=ip_src, dst=ip_dst,
                  ttl=int(ipv4_cfg.get("ipv4_ttl", 64)),
                  tos=tos)
        pkt /= UDP(sport=12345, dport=4791)

    # Append signature (if your RX counter relies on it)
    sig = f"[{stream_data.get('stream_id')}]".encode()
    pkt /= Raw(load=base_payload + sig)

    return pkt
