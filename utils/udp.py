# utils/udp.py
import re
import random
import logging
from typing import Optional
from scapy.all import (
    Ether, Dot1Q, IP, IPv6, UDP, BOOTP, DHCP, DNS, DNSQR, Raw
)
from scapy.layers.dhcp6 import (
    DHCP6_Solicit, DHCP6OptClientId, DHCP6OptElapsedTime,
    DHCP6OptIA_NA, DHCP6OptOptReq, DHCP6OptUserClass
)

def _parse_int(val, default=None):
    try:
        return int(str(val), 0)
    except Exception:
        return default

def _mac_to_bytes(mac_str: str) -> bytes:
    mac_str = (mac_str or "").strip()
    parts = re.split(r"[:-]", mac_str) if mac_str else []
    try:
        return bytes(int(p, 16) for p in parts[:6])
    except Exception:
        return b"\x00\x00\x00\x00\x00\x00"

def _mac_to_chaddr(mac_str: str) -> bytes:
    mac6 = _mac_to_bytes(mac_str)
    if len(mac6) < 6:
        mac6 = mac6 + b"\x00" * (6 - len(mac6))
    return mac6 + b"\x00" * 10

def _eui64_from_mac(mac_str: str) -> str:
    b = bytearray(_mac_to_bytes(mac_str))
    if len(b) != 6:
        return "2001:db8::1"
    b[0] ^= 0x02  # flip U/L bit
    eui = b[:3] + b'\xff\xfe' + b[3:]
    words = [f"{(eui[i] << 8) | eui[i+1]:x}" for i in range(0, 8, 2)]
    return "2001:db8::" + ":".join(words)

def _choose_udp_ports(udp_pd: dict, overrides: dict, default_s: int, default_d: int):
    sport = default_s
    dport = default_d
    if overrides.get("override_source_udp_port"):
        sport = _parse_int(udp_pd.get("udp_source_port", default_s), default_s) or default_s
    if overrides.get("override_destination_udp_port"):
        dport = _parse_int(udp_pd.get("udp_destination_port", default_d), default_d) or default_d
    return int(sport), int(dport)

def _recompute_lengths_checksums(pkt):
    if UDP in pkt:
        for fld in ("len", "chksum"):
            try: delattr(pkt[UDP], fld)
            except Exception: pass
    if IP in pkt:
        for fld in ("len", "chksum"):
            try: delattr(pkt[IP], fld)
            except Exception: pass
    if IPv6 in pkt:
        try: del pkt[IPv6].plen
        except Exception: pass

# -------- DHCPv4 --------
_MSGTYPE_MAP = {
    "DHCPDISCOVER": "discover",
    "DHCPOFFER": "offer",
    "DHCPREQUEST": "request",
    "DHCPDECLINE": "decline",
    "DHCPACK": "ack",
    "DHCPNAK": "nak",
    "DHCPRELEASE": "release",
    "DHCPINFORM": "inform",
}

def _build_bootp_dhcpv4(udp_cfg: dict, sig_bytes: Optional[bytes] = None):
    msg_raw = (udp_cfg.get("bootp_msg_type") or "DHCPDISCOVER").upper()
    msg = _MSGTYPE_MAP.get(msg_raw, "discover")

    xid = _parse_int(udp_cfg.get("bootp_xid", "0x12345678"), 0x12345678)
    flags = _parse_int(udp_cfg.get("bootp_flags", "0x0000"), 0)
    chaddr = _mac_to_chaddr(udp_cfg.get("bootp_client_mac", ""))

    prl_str = (udp_cfg.get("bootp_prl") or "").strip()
    try:
        prl = [int(x) for x in prl_str.split(",") if x.strip() != ""]
    except Exception:
        prl = [1, 3, 6, 15, 28, 51, 58, 59]

    opts = [("message-type", msg), ("param_req_list", prl)]
    hostname = (udp_cfg.get("bootp_hostname") or "").strip()
    if hostname:
        opts.insert(1, ("hostname", hostname))

    # Add a private option (224) with our signature for RX correlation
    if sig_bytes:
        opts.append((224, sig_bytes))

    opts.append("end")

    bootp = BOOTP(op=1, htype=1, hlen=6, xid=xid, flags=flags,
                  ciaddr=udp_cfg.get("bootp_ciaddr", "0.0.0.0") or "0.0.0.0",
                  yiaddr=udp_cfg.get("bootp_yiaddr", "0.0.0.0") or "0.0.0.0",
                  siaddr=udp_cfg.get("bootp_siaddr", "0.0.0.0") or "0.0.0.0",
                  giaddr=udp_cfg.get("bootp_giaddr", "0.0.0.0") or "0.0.0.0",
                  chaddr=chaddr)
    return bootp / DHCP(options=opts)

# -------- DHCPv6 --------
def _duid_ll(mac_str: str) -> bytes:
    return b"\x00\x03\x00\x01" + _mac_to_bytes(mac_str)

def _build_dhcpv6_solicit(mac_src: str, sig_bytes: Optional[bytes] = None):
    trid = random.randrange(0, 1 << 24)
    iaid = random.randrange(0, 1 << 32)
    duid = _duid_ll(mac_src)

    sol = DHCP6_Solicit(trid=trid)
    sol /= DHCP6OptClientId(duid=duid)
    sol /= DHCP6OptElapsedTime(elapsedtime=0)
    sol /= DHCP6OptIA_NA(iaid=iaid, T1=0, T2=0)
    sol /= DHCP6OptOptReq(reqopts=[23, 24])  # DNS & search list

    # Add a User Class carrying our signature for RX correlation
    if sig_bytes:
        sol /= DHCP6OptUserClass(ucdata=[sig_bytes])

    return sol

def _ensure_ipv6_l3(base_pkt, ll_src: str, dst: str = "ff02::1:2", hlim: int = 1):
    if IP in base_pkt:
        under = base_pkt[IP].underlayer
        try: under.remove_payload()
        except Exception: pass
    if IPv6 not in base_pkt:
        base_pkt = base_pkt / IPv6(src=ll_src, dst=dst, hlim=hlim)
    else:
        base_pkt[IPv6].src = ll_src
        base_pkt[IPv6].dst = dst
        base_pkt[IPv6].hlim = hlim
    if Dot1Q in base_pkt:
        try: base_pkt[Dot1Q].type = 0x86DD
        except Exception: pass
    return base_pkt

# -------- Public entrypoint --------
def build_udp_l4(base_pkt, stream_data, pkt_cfg,
                 udp_sport=None, udp_dport=None,
                 sig_bytes: Optional[bytes] = None):
    """
    Append UDP (and DHCPv4/DHCPv6/DNS/payload) to `base_pkt`.
    If `sig_bytes` is provided, we add it in a protocol-safe way for RX tracking.
    """
    pdata = stream_data.get("protocol_data", {}) or {}
    udp_pd = pdata.get("udp", {}) or {}
    ipv4_pd = pdata.get("ipv4", {}) or {}
    mac_pd = pdata.get("mac", {}) or {}
    payload_pd = pdata.get("payload_data", {}) or {}
    overrides = stream_data.get("override_settings", {}) or {}

    preset = str(udp_pd.get("udp_preset", "")).strip().lower()
    want_dhcpv6 = "dhcpv6" in preset
    want_bootp = bool(udp_pd.get("udp_bootp_enabled")) or \
                 preset.startswith("bootp") or \
                 ("dhcpv4" in preset) or \
                 ("dhcp" in preset and "v6" not in preset)

    # DHCPv6
    if want_dhcpv6:
        if Ether in base_pkt:
            base_pkt[Ether].dst = "33:33:00:01:00:02"
            mac_src = (mac_pd.get("mac_source_address")
                       or base_pkt[Ether].src
                       or (pkt_cfg.get("mac_src_list") or ["00:00:00:00:00:02"])[0])
            base_pkt[Ether].src = mac_src
        else:
            mac_src = mac_pd.get("mac_source_address") or (pkt_cfg.get("mac_src_list") or ["00:00:00:00:00:02"])[0]

        ll_src = _eui64_from_mac(mac_src)
        base_pkt = _ensure_ipv6_l3(base_pkt, ll_src, dst="ff02::1:2", hlim=1)

        sport, dport = _choose_udp_ports(udp_pd, overrides, default_s=546, default_d=547)
        udp = UDP(sport=sport, dport=dport)

        if overrides.get("override_udp_checksum"):
            ch = _parse_int(udp_pd.get("udp_checksum"), None)
            if ch is not None:
                udp.chksum = ch

        sol = _build_dhcpv6_solicit(mac_src, sig_bytes=sig_bytes)
        pkt = base_pkt / udp / sol

        if Dot1Q in pkt:
            try: pkt[Dot1Q].type = 0x86DD
            except Exception: pass

        _recompute_lengths_checksums(pkt)
        return pkt

    # DHCPv4 / BOOTP
    if want_bootp:
        if Ether in base_pkt:
            base_pkt[Ether].dst = "ff:ff:ff:ff:ff:ff"

        if IP not in base_pkt:
            ttl = int(ipv4_pd.get("ipv4_ttl", 64) or 64)
            base_pkt = base_pkt / IP(src="0.0.0.0", dst="255.255.255.255", ttl=ttl)
        else:
            base_pkt[IP].src = "0.0.0.0"
            base_pkt[IP].dst = "255.255.255.255"

        if Dot1Q in base_pkt:
            try: base_pkt[Dot1Q].type = 0x0800
            except Exception: pass

        sport, dport = _choose_udp_ports(udp_pd, overrides, default_s=68, default_d=67)
        udp = UDP(sport=sport, dport=dport)

        if overrides.get("override_udp_checksum"):
            ch = _parse_int(udp_pd.get("udp_checksum"), None)
            if ch is not None:
                udp.chksum = ch

        bootp_dhcp = _build_bootp_dhcpv4(udp_pd, sig_bytes=sig_bytes)
        pkt = base_pkt / udp / bootp_dhcp
        _recompute_lengths_checksums(pkt)
        return pkt

    # Generic UDP
    sport = int(udp_sport if udp_sport is not None
                else udp_pd.get("udp_source_port", (pkt_cfg.get("udp_sport_list") or [1234])[0]))
    dport = int(udp_dport if udp_dport is not None
                else udp_pd.get("udp_destination_port", (pkt_cfg.get("udp_dport_list") or [80])[0]))
    udp = UDP(sport=sport, dport=dport)

    if overrides.get("override_udp_checksum"):
        ch = _parse_int(udp_pd.get("udp_checksum"), None)
        if ch is not None:
            udp.chksum = ch

    if stream_data.get("simulate_dns"):
        l4payload = DNS(rd=1, qd=DNSQR(qname="fuzzed.test"))
    else:
        payload_hex = (payload_pd.get("payload_data") or "").replace(" ", "")
        try:
            l4payload = Raw(bytes.fromhex(payload_hex)) if payload_hex else Raw(b"")
        except Exception:
            l4payload = Raw(b"")

    # Append signature safely
    if sig_bytes:
        l4payload = l4payload / Raw(sig_bytes)

    pkt = base_pkt / udp / l4payload

    if Dot1Q in pkt:
        try:
            if IP in pkt: pkt[Dot1Q].type = 0x0800
            elif IPv6 in pkt: pkt[Dot1Q].type = 0x86DD
        except Exception: pass

    _recompute_lengths_checksums(pkt)
    return pkt
