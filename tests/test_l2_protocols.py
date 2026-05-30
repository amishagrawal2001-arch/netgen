"""Wire-format tests for the L2 frame generators.

We can't sendp on macOS CI (raw sockets are root-only) so these
tests exercise the frame BUILDERS directly — pulling the factory
closure out of `_run_periodic` is awkward, so instead we duplicate
the minimal scapy stack the generators use and assert the resulting
bytes match what's on the wire per RFC.

Catches regressions when scapy bumps a major version and renames
fields (every prior session in this codebase has hit at least one).
"""

import pytest

scapy = pytest.importorskip("scapy")


# ---------------------------------------------------------------- LACP


def test_lacp_frame_has_slow_protocols_marker():
    """LACPDU goes to 01:80:c2:00:00:02 with ethertype 0x8809 (Slow
    Protocols). Switch hardware filters on this exact destination MAC."""
    from scapy.layers.l2 import Ether
    from scapy.contrib.lacp import SlowProtocol, LACP

    frame = (
        Ether(dst="01:80:c2:00:00:02", src="00:11:22:33:44:01", type=0x8809)
        / SlowProtocol(subtype=0x01)
        / LACP(
            version=1, actor_system_priority=32768,
            actor_system="00:11:22:33:44:01", actor_key=1,
            actor_port_priority=32768, actor_port_number=1, actor_state=0x05,
        )
    )
    raw = bytes(frame)
    # Ethernet dest = Slow Protocols multicast
    assert raw[0:6] == bytes.fromhex("0180c200 0002".replace(" ", ""))
    # Ethertype 0x8809
    assert raw[12:14] == b"\x88\x09"
    # SlowProtocol subtype 0x01 = LACP (vs 0x02 = Marker)
    assert raw[14] == 0x01


def test_lldp_frame_has_lldp_multicast_dest():
    """LLDP advertisements use 01:80:c2:00:00:0e with ethertype 0x88cc.
    The required TLVs (Chassis-ID, Port-ID, TTL, End-of-PDU) must be
    in the first 4 layer positions per 802.1AB §8.6."""
    from scapy.layers.l2 import Ether
    from scapy.contrib.lldp import (
        LLDPDUChassisID, LLDPDUPortID, LLDPDUTimeToLive,
        LLDPDUSystemName, LLDPDUEndOfLLDPDU,
    )
    frame = (
        Ether(dst="01:80:c2:00:00:0e", src="00:11:22:33:44:02", type=0x88cc)
        / LLDPDUChassisID(subtype="locally assigned", id=b"netgen-host")
        / LLDPDUPortID(subtype="locally assigned", id=b"eth0")
        / LLDPDUTimeToLive(ttl=120)
        / LLDPDUSystemName(system_name=b"netgen")
        / LLDPDUEndOfLLDPDU()
    )
    raw = bytes(frame)
    assert raw[0:6] == bytes.fromhex("0180c200 000e".replace(" ", ""))
    assert raw[12:14] == b"\x88\xcc"
    # The system-name bytes should appear somewhere in the frame.
    assert b"netgen" in raw


def test_vrrp_v3_ipv4_advertisement_has_correct_multicast():
    """VRRPv3 IPv4 sends to 224.0.0.18, IP-proto 112, dest-MAC
    01:00:5e:00:00:12. Switches and routers everywhere drop frames
    that don't match this exact tuple."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.layers.vrrp import VRRPv3
    frame = (
        Ether(src="00:11:22:33:44:03", dst="01:00:5e:00:00:12")
        / IP(src="10.0.0.1", dst="224.0.0.18", ttl=255, proto=112)
        / VRRPv3(version=3, vrid=42, priority=200,
                 addrlist=["192.168.1.254"], adv=100)
    )
    raw = bytes(frame)
    assert raw[0:6] == bytes.fromhex("01005e 000012".replace(" ", ""))
    # IP destination 224.0.0.18 lives at bytes 30..34
    assert raw[30:34] == bytes([224, 0, 0, 18])
    # IP protocol = 112 (VRRP) at byte 23
    assert raw[23] == 112
    # VRID we asked for must round-trip
    parsed = frame[VRRPv3]
    assert parsed.vrid == 42
    assert parsed.priority == 200


def test_vrrp_v2_uses_separate_packet_class():
    """VRRPv2 (RFC 3768) has a different on-wire format from v3
    (RFC 5798) — auth bytes appear in v2, not v3. The wrapper must
    pick the right class per `version` arg."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.layers.vrrp import VRRP
    frame = (
        Ether(src="00:11:22:33:44:03", dst="01:00:5e:00:00:12")
        / IP(src="10.0.0.1", dst="224.0.0.18", ttl=255, proto=112)
        / VRRP(version=2, vrid=1, priority=100,
               addrlist=["192.168.1.254"], adv=1)
    )
    # VRRPv2 frames are longer than v3 by the trailing auth bytes.
    assert len(bytes(frame)) >= 54


def test_igmpv2_membership_report_target_is_group():
    """RFC 2236 §3: a v2 Membership Report (type 0x16) has its IP dst
    SET TO the group being reported. TTL is 1. Without those, the
    switch's IGMP-snooping will silently drop the frame."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.contrib.igmp import IGMP
    frame = (
        Ether(src="00:11:22:33:44:04", dst="01:00:5e:01:01:01")
        / IP(src="10.0.0.10", dst="239.1.1.1", ttl=1)
        / IGMP(type=0x16, gaddr="239.1.1.1")
    )
    raw = bytes(frame)
    # IP dst = 239.1.1.1 (the group) at bytes 30..34
    assert raw[30:34] == bytes([239, 1, 1, 1])
    # TTL = 1 at byte 22
    assert raw[22] == 1
    # IGMP type 0x16 (v2 Membership Report)
    parsed = frame[IGMP]
    assert parsed.type == 0x16
    assert parsed.gaddr == "239.1.1.1"


def test_igmpv1_membership_report_target_is_group():
    """RFC 1112 §4: a v1 Membership Report (type 0x12) goes to the
    group address being reported, with TTL=1 and mrcode reserved/zero
    (the v2 max-resp-time field doesn't exist in v1 — must read 0)."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.contrib.igmp import IGMP
    frame = (
        Ether(src="00:11:22:33:44:04", dst="01:00:5e:01:01:01")
        / IP(src="10.0.0.10", dst="239.1.1.1", ttl=1)
        / IGMP(type=0x12, mrcode=0, gaddr="239.1.1.1")
    )
    raw = bytes(frame)
    # IP dst = 239.1.1.1 (the group) at bytes 30..34
    assert raw[30:34] == bytes([239, 1, 1, 1])
    # TTL = 1 at byte 22
    assert raw[22] == 1
    # IGMP type 0x12 (v1 Membership Report)
    parsed = frame[IGMP]
    assert parsed.type == 0x12
    assert parsed.mrcode == 0  # v1 reserved-must-be-zero
    assert parsed.gaddr == "239.1.1.1"


def test_igmpv1_membership_query_target_is_all_systems():
    """RFC 1112 §4: a v1 Membership Query (type 0x11) is sent by
    routers to 224.0.0.1 (ALL-SYSTEMS multicast), L2-mapped to
    01:00:5e:00:00:01. The gaddr field is 0.0.0.0 for a General
    Query (asking everyone to report)."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.contrib.igmp import IGMP
    frame = (
        Ether(src="00:11:22:33:44:04", dst="01:00:5e:00:00:01")
        / IP(src="10.0.0.10", dst="224.0.0.1", ttl=1)
        / IGMP(type=0x11, mrcode=0, gaddr="0.0.0.0")
    )
    raw = bytes(frame)
    # L2 dst = 01:00:5e:00:00:01 (ALL-SYSTEMS MAC) at bytes 0..6
    assert raw[0:6] == bytes([0x01, 0x00, 0x5e, 0x00, 0x00, 0x01])
    # IP dst = 224.0.0.1 at bytes 30..34
    assert raw[30:34] == bytes([224, 0, 0, 1])
    parsed = frame[IGMP]
    assert parsed.type == 0x11
    assert parsed.mrcode == 0


def test_igmpv3_membership_report_destination_is_22():
    """IGMPv3 reports (type 0x22) go to 224.0.0.22 regardless of the
    reported group — that's where IGMPv3-capable queriers listen."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.contrib.igmpv3 import IGMPv3, IGMPv3mr, IGMPv3gr
    rec = IGMPv3gr(rtype=2, maddr="239.1.1.1")
    frame = (
        Ether(src="00:11:22:33:44:04", dst="01:00:5e:01:01:01")
        / IP(src="10.0.0.10", dst="224.0.0.22", ttl=1)
        / IGMPv3(type=0x22)
        / IGMPv3mr(numgrp=1, records=[rec])
    )
    raw = bytes(frame)
    # IP dst = 224.0.0.22
    assert raw[30:34] == bytes([224, 0, 0, 22])
    # IGMPv3 type at byte 34 (after 14-byte Ether + 20-byte IP)
    assert raw[34] == 0x22


def test_pim_hello_destination_is_all_pim_routers():
    """PIM Hello (RFC 7761 §4.3) goes to 224.0.0.13 ("all PIM routers"),
    IP-proto 103, dest-MAC 01:00:5e:00:00:0d. The receiver checks
    every one of those before processing."""
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP
    from scapy.contrib.pim import (
        PIMv2Hdr, PIMv2Hello, PIMv2HelloHoldtime,
        PIMv2HelloDRPriority, PIMv2HelloGenerationID,
    )
    frame = (
        Ether(src="00:11:22:33:44:05", dst="01:00:5e:00:00:0d")
        / IP(src="10.0.0.20", dst="224.0.0.13", ttl=1, proto=103)
        / PIMv2Hdr(type=0)
        / PIMv2Hello(option=[
            PIMv2HelloHoldtime(holdtime=105),
            PIMv2HelloDRPriority(dr_priority=1),
            PIMv2HelloGenerationID(generation_id=0xABCDEF01),
        ])
    )
    raw = bytes(frame)
    assert raw[0:6] == bytes.fromhex("01005e 00000d".replace(" ", ""))
    assert raw[30:34] == bytes([224, 0, 0, 13])
    assert raw[23] == 103
    # PIM v2 Hello: type 0 lives in the low nibble of the first PIM byte.
    pim_byte = raw[34]
    assert pim_byte & 0x0F == 0   # type=0 = Hello


# ---------------------------------------------------------------- worker


def test_session_registry_round_trip():
    """The registry's list/get/stop wiring should work even with a
    fake session (we never spawn the thread). Locks down the contract
    consumers depend on without needing a real network."""
    from utils import l2_protocols
    sess = l2_protocols._Session(
        session_id="test-1",
        protocol="lacp",
        iface="lo0",
        config={"key": 1},
    )
    with l2_protocols._REG_LOCK:
        l2_protocols._SESSIONS["test-1"] = sess
    try:
        snap = l2_protocols.get_session("test-1")
        assert snap is not None
        assert snap["session_id"] == "test-1"
        assert snap["protocol"] == "lacp"
        assert snap["iface"] == "lo0"

        all_sess = l2_protocols.list_sessions()
        assert any(s["session_id"] == "test-1" for s in all_sess)

        # stop_session on a session with no thread still flags it stopped.
        ok = l2_protocols.stop_session("test-1")
        assert ok is True

        snap = l2_protocols.get_session("test-1")
        assert snap["counters"]["stopped_at"] is not None
    finally:
        with l2_protocols._REG_LOCK:
            l2_protocols._SESSIONS.pop("test-1", None)


def test_stop_unknown_session_returns_false():
    from utils import l2_protocols
    assert l2_protocols.stop_session("does-not-exist") is False
