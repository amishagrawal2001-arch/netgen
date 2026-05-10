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

    # Base Ether - use provided MAC addresses (from increment lists) or fall back to lists/defaults
    # IMPORTANT: src_mac and dst_mac parameters take precedence - these are the incremented values
    if src_mac:
        mac_src = src_mac
    elif pkt_cfg.get("mac_src_list") and len(pkt_cfg["mac_src_list"]) > 0:
        mac_src = pkt_cfg["mac_src_list"][0]
    else:
        mac_src = "00:00:00:00:00:02"
    
    if dst_mac:
        mac_dst = dst_mac
    elif pkt_cfg.get("mac_dst_list") and len(pkt_cfg["mac_dst_list"]) > 0:
        mac_dst = pkt_cfg["mac_dst_list"][0]
    else:
        mac_dst = "00:00:00:00:00:01"
    
    # Reduced logging for performance - only log first packet
    if not hasattr(build_generic_packet, '_log_count'):
        build_generic_packet._log_count = 0
        logging.info(f"[MAC] build_generic_packet: using src={mac_src}, dst={mac_dst} (provided src_mac={src_mac}, dst_mac={dst_mac})")
    build_generic_packet._log_count += 1
    
    pkt = Ether(src=mac_src, dst=mac_dst)

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

        ipv4_src = src_ip or (pkt_cfg["ipv4_src_list"][0] if pkt_cfg.get("ipv4_src_list") and len(pkt_cfg["ipv4_src_list"]) > 0 else "10.0.0.1")
        ipv4_dst = dst_ip or (pkt_cfg["ipv4_dst_list"][0] if pkt_cfg.get("ipv4_dst_list") and len(pkt_cfg["ipv4_dst_list"]) > 0 else "10.0.0.2")
        pkt /= IP(
            src=ipv4_src,
            dst=ipv4_dst,
            ttl=int(ipv4.get("ipv4_ttl", 64)),
            tos=tos,
            id=int(ipv4.get("ipv4_identification", 0)),
            flags=flags,
            frag=int(ipv4.get("ipv4_fragment_offset", 0)),
        )

    elif l3 == "IPv6":
        ipv6 = protocol_data.get("ipv6", {}) or {}
        ipv6_src_list = pkt_cfg.get("ipv6_src_list", ["2001:db8::1"])
        ipv6_dst_list = pkt_cfg.get("ipv6_dst_list", ["2001:db8::2"])
        ipv6_src = src_ipv6 or (ipv6_src_list[0] if ipv6_src_list and len(ipv6_src_list) > 0 else "2001:db8::1")
        ipv6_dst = dst_ipv6 or (ipv6_dst_list[0] if ipv6_dst_list and len(ipv6_dst_list) > 0 else "2001:db8::2")
        pkt /= IPv6(
            src=ipv6_src,
            dst=ipv6_dst,
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

    # Apply frame size padding based on frame_type
    pkt = _apply_frame_size(pkt, stream_data)
    
    return pkt


def _apply_frame_size(pkt, stream_data):
    """
    Pad packet to target frame size based on frame_type (Fixed, Random, IMIX).
    Frame size is measured as total Ethernet frame length (including FCS, but we pad to L2 payload size).
    """
    protocol_selection = stream_data.get("protocol_selection", {}) or {}
    
    # Get frame type and size parameters - check both protocol_selection and top-level
    frame_type = (protocol_selection.get("frame_type") or 
                  stream_data.get("frame_type") or 
                  "Fixed")
    
    # Get frame size parameters - check both locations with error handling
    try:
        frame_size = int(protocol_selection.get("frame_size") or 
                        stream_data.get("frame_size") or 
                        64)
        frame_size = max(64, min(frame_size, 9216))  # Validate range
    except (ValueError, TypeError):
        frame_size = 64
    
    try:
        frame_min = int(protocol_selection.get("frame_min") or 
                       stream_data.get("frame_min") or 
                       64)
        frame_min = max(64, min(frame_min, 9216))  # Validate range
    except (ValueError, TypeError):
        frame_min = 64
    
    try:
        frame_max = int(protocol_selection.get("frame_max") or 
                       stream_data.get("frame_max") or 
                       1518)
        frame_max = max(64, min(frame_max, 9216))  # Validate range
    except (ValueError, TypeError):
        frame_max = 1518
    
    # Ensure frame_min <= frame_max for Random type
    if frame_type == "Random" and frame_min > frame_max:
        logging.warning(f"[FRAME SIZE] frame_min ({frame_min}) > frame_max ({frame_max}), swapping values")
        frame_min, frame_max = frame_max, frame_min
    
    # Calculate target frame size based on frame_type
    if frame_type == "Fixed":
        target_size = frame_size
    elif frame_type == "Random":
        # Ensure frame_min <= frame_max before random selection
        if frame_min > frame_max:
            frame_min, frame_max = frame_max, frame_min
        target_size = random.randint(frame_min, frame_max)
    elif frame_type == "IMIX":
        # Standard IMIX distribution: 58% 64B, 33% 576B, 9% 1518B
        rand = random.random()
        if rand < 0.58:
            target_size = 64
        elif rand < 0.91:
            target_size = 576
        else:
            target_size = 1518
    else:
        # Default to Fixed
        target_size = frame_size
    
    # Ensure target_size is within valid Ethernet range
    target_size = max(64, min(target_size, 9216))
    
    # Get current packet size (Ethernet frame size without FCS)
    # len(pkt) includes 14 bytes Ethernet header + payload
    current_size = len(pkt)
    
    # Calculate padding needed
    # Total Ethernet frame = 14 (Ethernet header) + payload + 4 (FCS)
    # If target_size = 64 bytes total, then: 14 + payload + 4 = 64, so payload = 46
    # Since len(pkt) = 14 + payload, we need len(pkt) = target_size - 4 (FCS)
    target_frame_size = target_size - 4  # Subtract FCS (4 bytes)
    
    if current_size < target_frame_size:
        padding_needed = target_frame_size - current_size
        if padding_needed > 0:
            # Add padding to the packet
            if Raw in pkt:
                # Append to existing Raw payload
                try:
                    pkt[Raw].load = bytes(pkt[Raw].load) + b'\x00' * padding_needed
                except Exception:
                    pkt = pkt / Raw(load=b'\x00' * padding_needed)
            else:
                # Add new Raw layer with padding
                pkt = pkt / Raw(load=b'\x00' * padding_needed)
    
    return pkt


# ---------- config expansion ----------
def get_packet_config(stream_data):
    protocol_data = stream_data.get("protocol_data", {}) or {}
    mac = protocol_data.get("mac", {}) or {}
    
    # Debug: Log what MAC data we received
    logging.info(f"[MAC] Received protocol_data.mac: {mac}")
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

    # MACs - with defaults and validation
    mac_src_default = mac.get("mac_source_address") or "00:00:00:00:00:02"
    mac_src_list = [mac_src_default]
    mac_src_mode = mac.get("mac_source_mode", "Fixed")
    logging.info(f"[MAC] Source MAC mode: {mac_src_mode}, address: {mac_src_default}")
    if mac_src_mode in ("Increment", "Decrement") and mac_src_default:
        try:
            step = int(mac.get("mac_source_step", 1))
            count = int(mac.get("mac_source_count", 1))
            logging.info(f"[MAC] Source increment: step={step}, count={count} (raw value from stream_data)")
            # For Decrement mode, use negative step
            if mac_src_mode == "Decrement":
                step = -step
            # If count is 1 or less, generate at least 2 addresses to show increment working
            # This ensures increment mode actually produces multiple addresses
            if count <= 1:
                logging.warning(f"[MAC] Source increment count is {count}, generating 2 addresses to show increment")
                count = 2
            else:
                logging.info(f"[MAC] Source increment: will generate {count} MAC addresses")
            if count > 0:
                mac_src_list = [increment_mac(mac_src_default, step * i) for i in range(count)]
                if len(mac_src_list) <= 20:
                    logging.info(f"[MAC] Source MAC list generated ({len(mac_src_list)} addresses): {mac_src_list}")
                else:
                    logging.info(f"[MAC] Source MAC list generated ({len(mac_src_list)} addresses): {mac_src_list[:10]}... (showing first 10 of {len(mac_src_list)})")
                    logging.info(f"[MAC] Source MAC list (last 10): ...{mac_src_list[-10:]}")
            else:
                mac_src_list = [mac_src_default]
                logging.warning(f"[MAC] Source increment count is 0, using fixed address")
        except Exception as e:
            logging.error(f"[MAC] Error processing source MAC increment: {e}")
            mac_src_list = [mac_src_default]
    else:
        logging.info(f"[MAC] Source MAC mode is '{mac_src_mode}', using fixed address: {mac_src_default}")

    mac_dst_default = mac.get("mac_destination_address") or "00:00:00:00:00:01"
    mac_dst_list = [mac_dst_default]
    mac_dst_mode = mac.get("mac_destination_mode", "Fixed")
    logging.info(f"[MAC] Destination MAC mode: {mac_dst_mode}, address: {mac_dst_default}")
    if mac_dst_mode in ("Increment", "Decrement") and mac_dst_default:
        try:
            step = int(mac.get("mac_destination_step", 1))
            count = int(mac.get("mac_destination_count", 1))
            logging.info(f"[MAC] Destination increment: step={step}, count={count} (raw value from stream_data)")
            # For Decrement mode, use negative step
            if mac_dst_mode == "Decrement":
                step = -step
            # If count is 1 or less, generate at least 2 addresses to show increment working
            # This ensures increment mode actually produces multiple addresses
            if count <= 1:
                logging.warning(f"[MAC] Destination increment count is {count}, generating 2 addresses to show increment")
                count = 2
            else:
                logging.info(f"[MAC] Destination increment: will generate {count} MAC addresses")
            if count > 0:
                mac_dst_list = [increment_mac(mac_dst_default, step * i) for i in range(count)]
                if len(mac_dst_list) <= 20:
                    logging.info(f"[MAC] Destination MAC list generated ({len(mac_dst_list)} addresses): {mac_dst_list}")
                else:
                    logging.info(f"[MAC] Destination MAC list generated ({len(mac_dst_list)} addresses): {mac_dst_list[:10]}... (showing first 10 of {len(mac_dst_list)})")
                    logging.info(f"[MAC] Destination MAC list (last 10): ...{mac_dst_list[-10:]}")
            else:
                mac_dst_list = [mac_dst_default]
                logging.warning(f"[MAC] Destination increment count is 0, using fixed address")
        except Exception as e:
            logging.error(f"[MAC] Error processing destination MAC increment: {e}")
            mac_dst_list = [mac_dst_default]
    else:
        logging.info(f"[MAC] Destination MAC mode is '{mac_dst_mode}', using fixed address: {mac_dst_default}")

    # IPv4 - with defaults and validation
    ipv4_src_default = ipv4.get("ipv4_source") or "10.0.0.1"
    ipv4_src_list = [ipv4_src_default]
    if ipv4.get("ipv4_source_mode") == "Increment" and ipv4_src_default:
        step = int(ipv4.get("ipv4_source_increment_step", 1)); count = int(ipv4.get("ipv4_source_increment_count", 1))
        if count > 0:
            ipv4_src_list = [increment_ip(ipv4_src_default, step * i) for i in range(count)]
        else:
            ipv4_src_list = [ipv4_src_default]

    ipv4_dst_default = ipv4.get("ipv4_destination") or "10.0.0.2"
    ipv4_dst_list = [ipv4_dst_default]
    if ipv4.get("ipv4_destination_mode") == "Increment" and ipv4_dst_default:
        step = int(ipv4.get("ipv4_destination_increment_step", 1)); count = int(ipv4.get("ipv4_destination_increment_count", 1))
        if count > 0:
            ipv4_dst_list = [increment_ip(ipv4_dst_default, step * i) for i in range(count)]
        else:
            ipv4_dst_list = [ipv4_dst_default]

    # IPv6 - with defaults and validation
    ipv6_src_default = ipv6.get("ipv6_source") or "2001:db8::1"
    ipv6_src_list = [ipv6_src_default]
    if ipv6.get("ipv6_source_mode") == "Increment" and ipv6_src_default:
        step = int(ipv6.get("ipv6_source_increment_step", 1)); count = int(ipv6.get("ipv6_source_increment_count", 1))
        if count > 0:
            ipv6_src_list = [increment_ipv6(ipv6_src_default, step * i) for i in range(count)]
        else:
            ipv6_src_list = [ipv6_src_default]

    ipv6_dst_default = ipv6.get("ipv6_destination") or "2001:db8::2"
    ipv6_dst_list = [ipv6_dst_default]
    if ipv6.get("ipv6_destination_mode") == "Increment" and ipv6_dst_default:
        step = int(ipv6.get("ipv6_destination_increment_step", 1)); count = int(ipv6.get("ipv6_destination_increment_count", 1))
        if count > 0:
            ipv6_dst_list = [increment_ipv6(ipv6_dst_default, step * i) for i in range(count)]
        else:
            ipv6_dst_list = [ipv6_dst_default]

    # TCP ports - ensure non-empty lists
    tcp_sport_list = [int(tcp.get("tcp_source_port", 12345))]
    if tcp.get("tcp_increment_source_port"):
        start = int(tcp.get("tcp_source_port", 12345)); step = int(tcp.get("tcp_source_port_step", 1)); count = int(tcp.get("tcp_source_port_count", 1))
        if count > 0:
            tcp_sport_list = [start + step * i for i in range(count)]
        else:
            tcp_sport_list = [start]

    tcp_dport_list = [int(tcp.get("tcp_destination_port", 80))]
    if tcp.get("tcp_increment_destination_port"):
        start = int(tcp.get("tcp_destination_port", 80)); step = int(tcp.get("tcp_destination_port_step", 1)); count = int(tcp.get("tcp_destination_port_count", 1))
        if count > 0:
            tcp_dport_list = [start + step * i for i in range(count)]
        else:
            tcp_dport_list = [start]

    # TCP sequence - ensure non-empty lists
    tcp_seq_list = [int(tcp.get("tcp_sequence_number", 0))]
    if tcp.get("tcp_sequence_count"):
        start = int(tcp.get("tcp_sequence_number", 0)); step = int(tcp.get("tcp_sequence_step", 1)); count = int(tcp.get("tcp_sequence_count", 1))
        if count > 0:
            tcp_seq_list = [start + step * i for i in range(count)]
        else:
            tcp_seq_list = [start]

    # UDP ports - ensure non-empty lists
    udp_sport_list = [int(udp.get("udp_source_port", 1234))]
    if udp.get("udp_increment_source_port"):
        start = int(udp.get("udp_source_port", 1234)); step = int(udp.get("udp_source_port_step", 1)); count = int(udp.get("udp_source_port_count", 1))
        if count > 0:
            udp_sport_list = [start + step * i for i in range(count)]
        else:
            udp_sport_list = [start]

    udp_dport_list = [int(udp.get("udp_destination_port", 80))]
    if udp.get("udp_increment_destination_port"):
        start = int(udp.get("udp_destination_port", 80)); step = int(udp.get("udp_destination_port_step", 1)); count = int(udp.get("udp_destination_port_count", 1))
        if count > 0:
            udp_dport_list = [start + step * i for i in range(count)]
        else:
            udp_dport_list = [start]

    # RoCEv2 GID and QP increments
    rocev2 = protocol_data.get("rocev2", {}) or {}
    
    # Source GID increment
    gid_src_default = rocev2.get("rocev2_source_gid", "0:0:0:0:0:ffff:192.168.0.2")
    gid_src_list = [gid_src_default]
    gid_src_mode = rocev2.get("rocev2_gid_source_mode", "Fixed")
    if gid_src_mode == "Increment" and gid_src_default:
        try:
            step = int(rocev2.get("rocev2_gid_source_step", 1))
            count = int(rocev2.get("rocev2_gid_source_count", 1))
            if count <= 1:
                logging.warning(f"[RoCEv2] Source GID increment count is {count}, generating 2 addresses to show increment")
                count = 2
            if count > 0:
                from utils.helpers import increment_gid
                gid_src_list = [increment_gid(gid_src_default, step * i) for i in range(count)]
                logging.info(f"[RoCEv2] Source GID list generated ({len(gid_src_list)} addresses): {gid_src_list[:5] if len(gid_src_list) > 5 else gid_src_list}")
        except Exception as e:
            logging.error(f"[RoCEv2] Error processing source GID increment: {e}")
            gid_src_list = [gid_src_default]
    
    # Destination GID increment
    gid_dst_default = rocev2.get("rocev2_destination_gid", "0:0:0:0:0:ffff:192.168.0.3")
    gid_dst_list = [gid_dst_default]
    gid_dst_mode = rocev2.get("rocev2_gid_destination_mode", "Fixed")
    if gid_dst_mode == "Increment" and gid_dst_default:
        try:
            step = int(rocev2.get("rocev2_gid_destination_step", 1))
            count = int(rocev2.get("rocev2_gid_destination_count", 1))
            if count <= 1:
                logging.warning(f"[RoCEv2] Destination GID increment count is {count}, generating 2 addresses to show increment")
                count = 2
            if count > 0:
                from utils.helpers import increment_gid
                gid_dst_list = [increment_gid(gid_dst_default, step * i) for i in range(count)]
                logging.info(f"[RoCEv2] Destination GID list generated ({len(gid_dst_list)} addresses): {gid_dst_list[:5] if len(gid_dst_list) > 5 else gid_dst_list}")
        except Exception as e:
            logging.error(f"[RoCEv2] Error processing destination GID increment: {e}")
            gid_dst_list = [gid_dst_default]
    
    # QP (Queue Pair) increment - Destination QP is used in BTH
    qp_dst_default = int(rocev2.get("rocev2_destination_qp", 0))
    qp_dst_list = [qp_dst_default]
    if rocev2.get("rocev2_qp_increment", False):
        try:
            start = qp_dst_default
            step = int(rocev2.get("rocev2_qp_increment_step", 1))
            count = int(rocev2.get("rocev2_qp_count", 1))
            if count <= 1:
                logging.warning(f"[RoCEv2] QP increment count is {count}, generating 2 QPs to show increment")
                count = 2
            if count > 0:
                qp_dst_list = [(start + step * i) & 0xFFFFFF for i in range(count)]  # 24-bit QP
                logging.info(f"[RoCEv2] Destination QP list generated ({len(qp_dst_list)} QPs): {qp_dst_list[:10] if len(qp_dst_list) > 10 else qp_dst_list}")
        except Exception as e:
            logging.error(f"[RoCEv2] Error processing QP increment: {e}")
            qp_dst_list = [qp_dst_default]
    
    # Source QP (not used in BTH but available for other purposes)
    qp_src_default = int(rocev2.get("rocev2_source_qp", 0))
    qp_src_list = [qp_src_default]
    # Note: Source QP increment uses same settings as destination QP increment
    if rocev2.get("rocev2_qp_increment", False):
        try:
            start = qp_src_default
            step = int(rocev2.get("rocev2_qp_increment_step", 1))
            count = int(rocev2.get("rocev2_qp_count", 1))
            if count <= 1:
                count = 2
            if count > 0:
                qp_src_list = [(start + step * i) & 0xFFFFFF for i in range(count)]
        except Exception as e:
            logging.error(f"[RoCEv2] Error processing source QP increment: {e}")
            qp_src_list = [qp_src_default]

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
        "gid_src_list": gid_src_list,
        "gid_dst_list": gid_dst_list,
        "qp_src_list": qp_src_list,
        "qp_dst_list": qp_dst_list,
    }
