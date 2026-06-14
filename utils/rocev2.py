# utils/rocev2.py
from scapy.all import Ether, Dot1Q, IP, IPv6, UDP, Raw
import logging
import struct

# Try to import Scapy's BTH class if available
try:
    from scapy.contrib.roce import BTH
    BTH_AVAILABLE = True
except ImportError:
    BTH_AVAILABLE = False
    logging.debug("[RoCEv2] Scapy BTH class not available, using custom BTH builder")

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

def build_rocev2_bth(stream_data):
    """
    Build a proper RoCEv2 Base Transport Header (BTH).
    BTH is 12 bytes: opcode(1) + flags(1) + pkey(2) + reserved(1) + dqpn(3) + ackreq(1) + psn(3)
    """
    r = (stream_data.get("protocol_data", {}) or {}).get("rocev2", {}) or {}
    
    # Opcode mapping (IBTA spec)
    # UD transport base: 0x60, opcodes: 0x64 = SEND_ONLY, 0x65 = SEND_ONLY_WITH_IMMEDIATE
    # Note: Some opcodes are transport-specific, but we default to UD (0x60 base)
    opcode_map = {
        # Send operations (UD transport: 0x60 base)
        "SendFirst": 0x60,
        "SendMiddle": 0x61,
        "SendLast": 0x62,
        "SendLastWithImmediate": 0x63,
        "SendOnly": 0x64,
        "SendOnlyWithImmediate": 0x65,
        "SendOnlySolicited": 0x64,  # Same opcode, but solicited flag = 1
        "SendLastSolicited": 0x62,  # Same opcode, but solicited flag = 1
        # RDMA Write operations (UD transport: 0x60 base)
        "RDMAWrite": 0x6A,  # UD_RDMA_WRITE_ONLY (0x60 + 0x0A)
        "RDMAWriteFirst": 0x66,  # UD_RDMA_WRITE_FIRST (0x60 + 0x06)
        "RDMAWriteMiddle": 0x67,  # UD_RDMA_WRITE_MIDDLE (0x60 + 0x07)
        "RDMAWriteLast": 0x68,  # UD_RDMA_WRITE_LAST (0x60 + 0x08)
        "RDMAWriteLastWithImmediate": 0x69,  # UD_RDMA_WRITE_LAST_WITH_IMMEDIATE (0x60 + 0x09)
        "RDMAWriteOnly": 0x6A,  # UD_RDMA_WRITE_ONLY (0x60 + 0x0A) - alias for RDMAWrite
        "RDMAWriteOnlyImm": 0x6B,  # RDMA_WRITE_ONLY_WITH_IMMEDIATE
        "RDMAWriteWithImmediate": 0x6B,
        # RDMA Read operations
        "RDMAReadRequest": 0x0C,  # UD_RDMA_READ_REQUEST
        "RDMAReadResponse": 0x70,  # Default to RDMA_READ_RESPONSE_ONLY
        "RDMAReadResponseOnly": 0x70,
        "RDMAReadResponseFirst": 0x6D,
        "RDMAReadResponseMiddle": 0x6E,
        "RDMAReadResponseLast": 0x6F,
        # Acknowledge operations
        "Acknowledge": 0x71,
        "AtomicAcknowledge": 0x72,
        # Atomic operations
        "CompareSwap": 0x73,
        "FetchAdd": 0x74,
        "AtomicCompareSwap": 0x73,  # Alias
        "AtomicFetchAdd": 0x74,     # Alias
        # Congestion notification
        "CNP": 0x81,
    }
    opcode_str = r.get("rocev2_opcode", "SendOnly") or "SendOnly"
    opcode = opcode_map.get(opcode_str, 0x64)  # Default to SEND_ONLY
    
    # Flags byte: SE(1) + M(1) + PadCnt(2) + Tver(4)
    # Handle "Solicited" variants by setting solicited flag
    solicited = 1 if (r.get("rocev2_solicited_event", False) or "Solicited" in opcode_str) else 0
    mig_req = 1 if r.get("rocev2_migration_req", False) else 0
    pad_count = 0  # Pad count (0-3)
    tver = 0  # Transport version (0)
    flags_byte = (solicited << 7) | (mig_req << 6) | ((pad_count & 0x3) << 4) | (tver & 0xF)
    
    # Partition Key (P_Key) - default 0xFFFF (full membership)
    pkey = 0xFFFF
    
    # Reserved byte
    reserved1 = 0
    
    # Destination QP (24 bits) - can be passed directly or from increment list
    # If dqpn is provided as parameter, use it; otherwise read from stream_data
    dqpn = r.get("_dqpn")  # Check if passed as parameter (from increment list)
    if dqpn is None:
        dqpn = int(r.get("rocev2_destination_qp", 0)) & 0xFFFFFF
    else:
        dqpn = int(dqpn) & 0xFFFFFF
    
    # ACK request + reserved (1 bit + 7 bits)
    ack_req = 0  # ACK request flag
    reserved2 = 0
    ack_byte = (ack_req << 7) | (reserved2 & 0x7F)
    
    # PSN (Packet Sequence Number) - 24 bits, start at 0
    psn = int(r.get("rocev2_psn", 0)) & 0xFFFFFF
    
    # Build BTH as binary (12 bytes total)
    # Byte order: network (big-endian) for multi-byte fields
    bth = struct.pack("!B", opcode)  # 1 byte: opcode
    bth += struct.pack("!B", flags_byte)  # 1 byte: flags
    bth += struct.pack("!H", pkey)  # 2 bytes: P_Key
    bth += struct.pack("!B", reserved1)  # 1 byte: reserved
    bth += struct.pack("!I", dqpn)[1:]  # 3 bytes: DQPN (take last 3 bytes of 4-byte int)
    bth += struct.pack("!B", ack_byte)  # 1 byte: ACK req + reserved
    bth += struct.pack("!I", psn)[1:]  # 3 bytes: PSN (take last 3 bytes of 4-byte int)
    
    return bth

def build_rocev2_payload(stream_data):
    """
    Build RoCEv2 payload with proper BTH header (12 bytes binary).
    This is used as fallback when Scapy's BTH class is not available.
    The signature for RX tracking will be appended separately by _append_sig_with_seq().
    """
    # Build proper BTH header (12 bytes binary)
    bth = build_rocev2_bth(stream_data)
    
    # Return just the BTH - signature will be appended by _append_sig_with_seq()
    return bth

def generate_rocev2_packet(stream_data, pkt_cfg=None, index=0):
    """
    Returns a list with a single valid Ethernet/IP/UDP(dport=4791) packet carrying a RoCEv2-like payload.
    VLAN / IPv4 / IPv6 are honored from stream_data.
    
    Args:
        stream_data: Stream configuration dictionary
        pkt_cfg: Packet configuration with increment lists (from get_packet_config)
        index: Index into increment lists (default: 0)
    """
    ps  = stream_data.get("protocol_selection", {}) or {}
    pd  = stream_data.get("protocol_data", {}) or {}
    mac = pd.get("mac", {}) or {}
    vlan_cfg = pd.get("vlan", {}) or {}
    ipv4_cfg = pd.get("ipv4", {}) or {}
    ipv6_cfg = pd.get("ipv6", {}) or {}
    rocev2_cfg = pd.get("rocev2", {}) or {}

    # Use increment lists if available, otherwise fall back to direct values
    gid_src_list = []
    gid_dst_list = []
    qp_dst_list = []
    
    if pkt_cfg:
        mac_src_list = pkt_cfg.get("mac_src_list", [])
        mac_dst_list = pkt_cfg.get("mac_dst_list", [])
        src_mac = mac_src_list[index % len(mac_src_list)] if mac_src_list else mac.get("mac_source_address", "00:00:00:00:00:02")
        dst_mac = mac_dst_list[index % len(mac_dst_list)] if mac_dst_list else mac.get("mac_destination_address", "ff:ff:ff:ff:ff:ff")
        
        # Use GID from increment lists if available
        gid_src_list = pkt_cfg.get("gid_src_list", [])
        gid_dst_list = pkt_cfg.get("gid_dst_list", [])
        qp_dst_list = pkt_cfg.get("qp_dst_list", [])
        
        # Update rocev2_cfg with incremented values
        if gid_src_list:
            rocev2_cfg["_gid_src"] = gid_src_list[index % len(gid_src_list)]
        if gid_dst_list:
            rocev2_cfg["_gid_dst"] = gid_dst_list[index % len(gid_dst_list)]
        if qp_dst_list:
            rocev2_cfg["_dqpn"] = qp_dst_list[index % len(qp_dst_list)]
    else:
        src_mac = mac.get("mac_source_address", "00:00:00:00:00:02")
        dst_mac = mac.get("mac_destination_address", "ff:ff:ff:ff:ff:ff")

    l3 = ps.get("L3", "IPv4")
    # v0.5.124: use the shared resolver. Pre-fix this site only
    # accepted mode == "Tagged" (case-sensitive!), silently
    # dropping the Dot1Q tag for "Stacked" / "TaggedStacked"
    # variants. Also missed top-level VLAN field for API-direct
    # callers. Shared helper handles all three correctly.
    from utils.vlan_helpers import resolve_tx_vlan_id
    _vid = resolve_tx_vlan_id(stream_data)
    vlan_id = int(_vid) if _vid is not None else 0
    pcp = int(vlan_cfg.get("vlan_priority", 0) or 0) & 0x7
    dei = int(vlan_cfg.get("vlan_cfi_dei", 0) or 0) & 0x1

    # L2
    eth = Ether(src=src_mac, dst=dst_mac)
    if vlan_id > 0:
        eth /= Dot1Q(vlan=vlan_id, prio=pcp, id=dei)

    # L3 - use increment lists if available
    if l3 == "IPv6":
        if pkt_cfg:
            ipv6_src = pkt_cfg.get("ipv6_src_list", [ipv6_cfg.get("ipv6_source", "2001:db8::1")])[index % len(pkt_cfg.get("ipv6_src_list", [""]))]
            ipv6_dst = pkt_cfg.get("ipv6_dst_list", [ipv6_cfg.get("ipv6_destination", "2001:db8::2")])[index % len(pkt_cfg.get("ipv6_dst_list", [""]))]
        else:
            ipv6_src = ipv6_cfg.get("ipv6_source", "2001:db8::1")
            ipv6_dst = ipv6_cfg.get("ipv6_destination", "2001:db8::2")
        
        # Use GID for IPv6 destination if GID increment is enabled
        if pkt_cfg and gid_dst_list:
            ipv6_dst = gid_dst_list[index % len(gid_dst_list)]
        
        ip = IPv6(
            src=ipv6_src,
            dst=ipv6_dst,
            hlim=int(ipv6_cfg.get("ipv6_hop_limit", 64)),
            tc=int(ipv6_cfg.get("ipv6_traffic_class", 0)),
            fl=int(ipv6_cfg.get("ipv6_flow_label", 0))
        )
    else:
        if pkt_cfg:
            ipv4_src = pkt_cfg.get("ipv4_src_list", [ipv4_cfg.get("ipv4_source", "10.0.0.1")])[index % len(pkt_cfg.get("ipv4_src_list", [""]))]
            ipv4_dst = pkt_cfg.get("ipv4_dst_list", [ipv4_cfg.get("ipv4_destination", "10.0.0.2")])[index % len(pkt_cfg.get("ipv4_dst_list", [""]))]
        else:
            ipv4_src = ipv4_cfg.get("ipv4_source", "10.0.0.1")
            ipv4_dst = ipv4_cfg.get("ipv4_destination", "10.0.0.2")
        
        # Use GID for IPv4 destination if GID increment is enabled (IPv4-mapped IPv6 format)
        if pkt_cfg and gid_dst_list:
            gid_dst = gid_dst_list[index % len(gid_dst_list)]
            # Extract IPv4 from GID format: 0:0:0:0:0:ffff:192.168.0.2
            if '::ffff:' in gid_dst or ':ffff:' in gid_dst:
                parts = gid_dst.split(':')
                for i, part in enumerate(parts):
                    if part == 'ffff' and i + 1 < len(parts):
                        ipv4_dst = parts[i + 1]
                        break
        
        flags = 0
        if ipv4_cfg.get("ipv4_df"): flags |= 0x2
        if ipv4_cfg.get("ipv4_mf"): flags |= 0x1
        ip = IP(
            src=ipv4_src,
            dst=ipv4_dst,
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

    # Build proper RoCEv2 BTH header
    # Create a temporary stream_data copy with updated rocev2_cfg for BTH building
    temp_stream_data = stream_data.copy()
    temp_stream_data["protocol_data"] = pd.copy()
    temp_stream_data["protocol_data"]["rocev2"] = rocev2_cfg
    
    if BTH_AVAILABLE:
        # Use Scapy's BTH class for proper RoCEv2 packet
        opcode_str = rocev2_cfg.get("rocev2_opcode", "SendOnly") or "SendOnly"
        # Map to Scapy's opcode format (UD transport: 0x60 base)
        opcode_map = {
            "SendOnly": 0x64,  # UD_SEND_ONLY
            "SendOnlyWithImmediate": 0x65,  # UD_SEND_ONLY_WITH_IMMEDIATE
            "SendOnlySolicited": 0x64,  # Same opcode, solicited flag = 1
            "SendLast": 0x62,  # UD_SEND_LAST
            "SendLastSolicited": 0x62,  # Same opcode, solicited flag = 1
            "RDMAWrite": 0x6A,  # UD_RDMA_WRITE_ONLY (0x60 + 0x0A)
            "RDMAWriteOnlyImm": 0x6B,  # UD_RDMA_WRITE_ONLY_WITH_IMMEDIATE (0x60 + 0x0B)
            "RDMAReadRequest": 0x6C,  # UD_RDMA_READ_REQUEST (0x60 + 0x0C)
            "RDMAReadResponse": 0x70,  # UD_RDMA_READ_RESPONSE_ONLY (0x60 + 0x10)
            "RDMAReadResponseOnly": 0x70,
            "RDMAReadResponseFirst": 0x6D,  # UD_RDMA_READ_RESPONSE_FIRST (0x60 + 0x0D)
            "RDMAReadResponseLast": 0x6F,  # UD_RDMA_READ_RESPONSE_LAST (0x60 + 0x0F)
            "RDMAReadResponseMiddle": 0x6E,  # UD_RDMA_READ_RESPONSE_MIDDLE (0x60 + 0x0E)
            "Acknowledge": 0x71,  # UD_ACKNOWLEDGE (0x60 + 0x11)
            "AtomicAcknowledge": 0x72,  # UD_ATOMIC_ACKNOWLEDGE (0x60 + 0x12)
            "CompareSwap": 0x73,  # UD_COMPARE_SWAP (0x60 + 0x13)
            "FetchAdd": 0x74,  # UD_FETCH_ADD (0x60 + 0x14)
            "AtomicCompareSwap": 0x73,  # Alias
            "AtomicFetchAdd": 0x74,  # Alias
            "CNP": 0x81,  # Congestion Notification Packet (special opcode)
        }
        bth_opcode = opcode_map.get(opcode_str, 0x64)
        
        solicited = 1 if (rocev2_cfg.get("rocev2_solicited_event", False) or "Solicited" in opcode_str) else 0
        mig_req = 1 if rocev2_cfg.get("rocev2_migration_req", False) else 0
        dqpn = rocev2_cfg.get("_dqpn")
        if dqpn is None:
            dqpn = int(rocev2_cfg.get("rocev2_destination_qp", 0)) & 0xFFFFFF
        else:
            dqpn = int(dqpn) & 0xFFFFFF
        psn = int(rocev2_cfg.get("rocev2_psn", 0)) & 0xFFFFFF
        
        bth = BTH(
            opcode=bth_opcode,
            solicited=solicited,
            migreq=mig_req,
            dqpn=dqpn,
            psn=psn
        )
        pkt = eth / ip / udp / bth
    else:
        # Fallback: use custom BTH builder (with updated rocev2_cfg)
        payload = build_rocev2_payload(temp_stream_data)
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
