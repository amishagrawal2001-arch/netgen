"""L2 frame generators + multicast protocol emulators.

Periodic frame senders for the protocols every datacenter / enterprise
lab tests. All built on scapy's existing layer definitions so we
don't hand-pack bytes — we just compose layers + sendp on a timer.

Protocols supported today (each behind its own `start_<proto>()`):

  * **LACP** (802.3ad / 802.1AX) — Slow Protocol LACPDU. Useful for
    LAG / port-channel formation tests.
  * **LLDP** (802.1AB) — neighbour discovery. Drives "what does my
    switch think it's connected to?" verification.
  * **VRRP** v2 and v3 — first-hop redundancy advertisements. Drives
    failover testing on edge / TOR.
  * **IGMP** v2 and v3 — multicast group membership reports. Drives
    multicast pruning / fast-leave testing on switches.
  * **PIM Hello** — neighbour-discovery half of PIM-SM/SSM. Full PIM
    join/prune is on the roadmap; Hello alone proves adjacency.

Each session lives in an in-process registry (same pattern as
`utils/stateful_tcp.py`). Workers send on a configurable interval
until `stop_session()` is called or the duration elapses.

Cross-platform notes
--------------------
`scapy.sendp` needs raw-socket access on Linux (CAP_NET_RAW or root).
On macOS BSD raw sockets are root-only. The worker will surface the
permission error on `last_error` so the operator sees it instead of
silent failure.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- counters


@dataclass
class _Counters:
    started_at: float = field(default_factory=time.time)
    stopped_at: Optional[float] = None
    frames_sent: int = 0
    frames_failed: int = 0
    bytes_sent: int = 0
    last_send_at: Optional[float] = None
    last_error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "uptime_s": (self.stopped_at or time.time()) - self.started_at,
            "frames_sent": self.frames_sent,
            "frames_failed": self.frames_failed,
            "bytes_sent": self.bytes_sent,
            "last_send_at": self.last_send_at,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------- session


@dataclass
class _Session:
    session_id: str
    protocol: str           # "lacp" | "lldp" | "vrrp" | "igmp" | "pim"
    iface: str
    config: Dict[str, Any]
    counters: _Counters = field(default_factory=_Counters)
    thread: Optional[threading.Thread] = None
    stop_evt: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            running = (
                self.thread is not None
                and self.thread.is_alive()
                and not self.stop_evt.is_set()
            )
            return {
                "session_id": self.session_id,
                "protocol": self.protocol,
                "iface": self.iface,
                "config": dict(self.config),
                "running": running,
                "counters": self.counters.snapshot(),
            }


# ---------------------------------------------------------------- registry


_REG_LOCK = threading.Lock()
_SESSIONS: Dict[str, _Session] = {}


def list_sessions() -> List[Dict[str, Any]]:
    with _REG_LOCK:
        return [s.snapshot() for s in _SESSIONS.values()]


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _REG_LOCK:
        sess = _SESSIONS.get(session_id)
    return sess.snapshot() if sess else None


def stop_session(session_id: str) -> bool:
    with _REG_LOCK:
        sess = _SESSIONS.get(session_id)
    if not sess:
        return False
    sess.stop_evt.set()
    if sess.thread:
        sess.thread.join(timeout=3.0)
    with sess.lock:
        sess.counters.stopped_at = time.time()
    return True


def stop_all_sessions() -> int:
    with _REG_LOCK:
        ids = list(_SESSIONS.keys())
    n = 0
    for sid in ids:
        if stop_session(sid):
            n += 1
    return n


# ---------------------------------------------------------------- worker


def _run_periodic(sess: _Session, frame_factory, interval_s: float,
                  duration_s: Optional[float]):
    """Generic send loop. Each tick calls `frame_factory()` to (re)build
    the frame (so monotonic counter fields can advance), then sendp's
    it on the configured interface. Counts every successful send plus
    every exception.

    Why rebuild the frame per tick: protocols like VRRP carry a
    sequence number, and IGMPv3 reports flip flags between query
    responses. Cheaper to rebuild than to mutate.
    """
    from scapy.all import sendp
    deadline = (time.time() + duration_s) if duration_s else None
    while not sess.stop_evt.is_set():
        if deadline is not None and time.time() >= deadline:
            break
        try:
            frame = frame_factory()
            sendp(frame, iface=sess.iface, verbose=False)
            with sess.lock:
                sess.counters.frames_sent += 1
                sess.counters.bytes_sent += len(bytes(frame))
                sess.counters.last_send_at = time.time()
        except PermissionError as exc:
            with sess.lock:
                sess.counters.frames_failed += 1
                sess.counters.last_error = (
                    f"PermissionError (sendp needs CAP_NET_RAW / root): {exc}"
                )
            # Stop trying — root-only error doesn't recover by retrying.
            break
        except Exception as exc:
            with sess.lock:
                sess.counters.frames_failed += 1
                sess.counters.last_error = f"{type(exc).__name__}: {exc}"
        if sess.stop_evt.wait(interval_s):
            break
    with sess.lock:
        sess.counters.stopped_at = time.time()


def _register_and_start(sess: _Session, frame_factory, interval_s, duration_s):
    sess.thread = threading.Thread(
        target=_run_periodic,
        args=(sess, frame_factory, interval_s, duration_s),
        daemon=True,
        name=f"l2-{sess.protocol}-{sess.session_id[:8]}",
    )
    sess.thread.start()
    with _REG_LOCK:
        _SESSIONS[sess.session_id] = sess


def _l2_hdr(src: str, dst: str, ethertype: int,
            vlan_id: Optional[int] = None, vlan_pcp: int = 0,
            outer_vlan_id: Optional[int] = None, outer_vlan_pcp: int = 0):
    """Build the Ethernet header for an emulated L2 frame, with optional
    inline 802.1Q or 802.1ad (QinQ) tagging.

    Three encapsulations:

    * **Untagged** (both VLAN ids None/0):
      ``Ether(src, dst, type=ethertype)``.

    * **Single 802.1Q** (only `vlan_id`):
      ``Ether(type=0x8100) / Dot1Q(vlan_id, prio, type=ethertype)`` —
      Spirent-style inline tag; no pre-created ``vlanN`` subif needed.

    * **QinQ / 802.1ad** (both `outer_vlan_id` *and* `vlan_id`):
      ``Ether(type=0x88a8) / Dot1Q(outer, type=0x8100) / Dot1Q(inner,
      type=ethertype)``. Outer is the S-VLAN (service provider), inner
      is the C-VLAN (customer). 0x88a8 on the outer is the IEEE 802.1ad
      TPID; the inner uses the legacy 0x8100. The protocol's original
      ethertype rides on the inner Dot1Q so the upper layer parses
      correctly through the double tag.

    `ethertype` is the protocol's L2 type: 0x8809 (LACP), 0x88cc (LLDP),
    0x0800 (IPv4-carried: VRRPv2/v3-v4, IGMP, PIM), 0x86dd (IPv6 VRRPv3).

    An outer tag without an inner tag is invalid 802.1ad — passing
    `outer_vlan_id` without `vlan_id` raises ``ValueError`` rather than
    silently emitting a single-tagged frame the operator didn't ask for.
    """
    from scapy.layers.l2 import Ether, Dot1Q
    if outer_vlan_id and not vlan_id:
        raise ValueError(
            "QinQ requires both inner (vlan_id) and outer (outer_vlan_id) "
            "tags — set vlan_id too, or clear outer_vlan_id for an untagged "
            "frame."
        )
    if outer_vlan_id and vlan_id:
        # 802.1ad: outer S-Tag (0x88a8) → inner C-Tag (0x8100) → payload.
        return (
            Ether(src=src, dst=dst, type=0x88a8)
            / Dot1Q(vlan=int(outer_vlan_id),
                    prio=int(outer_vlan_pcp) & 0x7,
                    type=0x8100)
            / Dot1Q(vlan=int(vlan_id),
                    prio=int(vlan_pcp) & 0x7,
                    type=ethertype)
        )
    if vlan_id:
        return (
            Ether(src=src, dst=dst)
            / Dot1Q(vlan=int(vlan_id), prio=int(vlan_pcp) & 0x7, type=ethertype)
        )
    return Ether(src=src, dst=dst, type=ethertype)


# ====================================================================
# LACP (802.3ad / 802.1AX)
# ====================================================================
#
# Slow Protocols multicast (01:80:c2:00:00:02) at Ethertype 0x8809.
# A full LAG partner would also TX MarkerPDUs; for the generator role
# we just emit LACPDUs at the standard 1s ("short") or 30s ("long")
# timeout cadence. scapy.contrib.lacp builds the wire format.


def start_lacp(
    iface: str,
    *,
    system_priority: int = 32768,
    system_mac: str = "00:11:22:33:44:01",
    key: int = 1,
    port_priority: int = 32768,
    port_number: int = 1,
    state: int = 0x05,   # Activity | Aggregation
    fast: bool = False,  # True = 1s interval (PDU_FAST), False = 30s (PDU_SLOW)
    vlan_id: Optional[int] = None,   # 802.1Q inner tag; None/0 = untagged
    vlan_pcp: int = 0,               # 802.1p inner priority (0-7)
    outer_vlan_id: Optional[int] = None,  # 802.1ad outer (S-VLAN); None/0 = single-tagged
    outer_vlan_pcp: int = 0,              # outer priority (0-7); QinQ only
    duration_s: Optional[float] = None,
) -> str:
    """Spawn an LACPDU emitter. Returns session_id.

    `fast=True` uses the 1-second cadence (LACP_Short_Timeout); default
    is 30s (LACP_Long_Timeout). State bits per IEEE 802.1AX-2014
    §6.4.2.3: 0x01=Activity, 0x02=Timeout, 0x04=Aggregation,
    0x08=Synchronization, 0x10=Collecting, 0x20=Distributing,
    0x40=Defaulted, 0x80=Expired.
    """
    sid = str(uuid.uuid4())
    config = {
        "system_priority": int(system_priority),
        "system_mac": system_mac,
        "key": int(key),
        "port_priority": int(port_priority),
        "port_number": int(port_number),
        "state": int(state),
        "fast": bool(fast),
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
        "duration_s": duration_s,
    }
    sess = _Session(session_id=sid, protocol="lacp", iface=iface, config=config)

    def _factory():
        from scapy.contrib.lacp import SlowProtocol, LACP
        # Slow Protocols dest MAC + ethertype (0x8809) + subtype
        return (
            _l2_hdr(system_mac, "01:80:c2:00:00:02", 0x8809, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / SlowProtocol(subtype=0x01)
            / LACP(
                version=1,
                actor_system_priority=system_priority,
                actor_system=system_mac,
                actor_key=key,
                actor_port_priority=port_priority,
                actor_port_number=port_number,
                actor_state=state,
            )
        )

    interval = 1.0 if fast else 30.0
    _register_and_start(sess, _factory, interval, duration_s)
    logger.info(f"[L2] LACP started session={sid} iface={iface} fast={fast}")
    return sid


# ====================================================================
# LLDP (802.1AB)
# ====================================================================
#
# Standard 30-second TTL, sent every 30s by default. Operators
# typically want to assert their identity to the switch's LLDP
# database for cable-trace tests.


def start_lldp(
    iface: str,
    *,
    chassis_id: str = "netgen-host",
    port_id: str = "eth0",
    system_name: str = "netgen",
    system_description: str = "Netgen L2 emulator",
    ttl_s: int = 120,
    interval_s: float = 30.0,
    duration_s: Optional[float] = None,
    src_mac: str = "00:11:22:33:44:02",
    vlan_id: Optional[int] = None,   # 802.1Q inner tag; None/0 = untagged
    vlan_pcp: int = 0,               # 802.1p inner priority (0-7)
    outer_vlan_id: Optional[int] = None,  # 802.1ad outer (S-VLAN); None/0 = single-tagged
    outer_vlan_pcp: int = 0,              # outer priority (0-7); QinQ only
) -> str:
    """Spawn an LLDP advertiser. Returns session_id."""
    sid = str(uuid.uuid4())
    config = {
        "chassis_id": chassis_id, "port_id": port_id,
        "system_name": system_name,
        "system_description": system_description,
        "ttl_s": int(ttl_s),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_mac": src_mac,
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
    }
    sess = _Session(session_id=sid, protocol="lldp", iface=iface, config=config)

    def _factory():
        from scapy.contrib.lldp import (
            LLDPDUChassisID, LLDPDUPortID, LLDPDUTimeToLive,
            LLDPDUSystemName, LLDPDUSystemDescription, LLDPDUEndOfLLDPDU,
        )
        # Scapy LLDP TLVs stack as layers via `/` — there's no
        # `LLDPDU(tlvlist=...)` constructor. Order matters: Chassis-ID,
        # Port-ID, TTL must come first per 802.1AB §8.6.
        return (
            _l2_hdr(src_mac, "01:80:c2:00:00:0e", 0x88cc, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / LLDPDUChassisID(
                subtype="locally assigned",
                id=chassis_id.encode("ascii"),
            )
            / LLDPDUPortID(
                subtype="locally assigned",
                id=port_id.encode("ascii"),
            )
            / LLDPDUTimeToLive(ttl=ttl_s)
            / LLDPDUSystemName(system_name=system_name.encode("ascii"))
            / LLDPDUSystemDescription(
                description=system_description.encode("ascii"),
            )
            / LLDPDUEndOfLLDPDU()
        )

    _register_and_start(sess, _factory, interval_s, duration_s)
    logger.info(f"[L2] LLDP started session={sid} iface={iface}")
    return sid


# ====================================================================
# VRRP (RFC 5798)
# ====================================================================
#
# v2 (RFC 3768, IPv4 only) and v3 (RFC 5798, IPv4 + IPv6) advertisements.
# Default cadence is 1 second per spec.


def _vrrp_virtual_mac(vrid: int, family: str = "ipv4") -> str:
    """The VRRP virtual router MAC for a VRID (RFC 5798 §7.3).

    IPv4: 00:00:5e:00:01:{VRID}   IPv6: 00:00:5e:00:02:{VRID}
    A real VRRP master sources its advertisements FROM this address, so
    downstream switches learn the virtual MAC on the master's port. The
    emulator now does the same by default (was an arbitrary src MAC).
    """
    block = 0x02 if str(family).lower() == "ipv6" else 0x01
    return f"00:00:5e:00:{block:02x}:{int(vrid) & 0xff:02x}"


def start_vrrp(
    iface: str,
    *,
    version: int = 3,
    vrid: int = 1,
    priority: int = 100,
    virtual_ips: Optional[List[str]] = None,
    interval_s: float = 1.0,
    duration_s: Optional[float] = None,
    src_ip: str = "10.0.0.1",
    src_mac: Optional[str] = None,   # None/"" → derive the VRRP virtual MAC
    family: str = "ipv4",   # "ipv4" or "ipv6" (v3 only)
    vlan_id: Optional[int] = None,   # 802.1Q inner tag; None/0 = untagged
    vlan_pcp: int = 0,               # 802.1p inner priority (0-7)
    outer_vlan_id: Optional[int] = None,  # 802.1ad outer (S-VLAN); None/0 = single-tagged
    outer_vlan_pcp: int = 0,              # outer priority (0-7); QinQ only
) -> str:
    """Spawn a VRRP master advertisement emitter. Returns session_id.

    `version=2` is IPv4-only. `version=3` supports both AFs via
    `family`. `priority=255` means "owner of the virtual IP" (highest
    preemption); 100 is the default for non-owner masters.

    `src_mac` defaults to the RFC 5798 virtual router MAC for the VRID
    (00:00:5e:00:01:{VRID} for IPv4) — what a real master uses. Pass an
    explicit MAC only to override that behaviour.
    """
    sid = str(uuid.uuid4())
    virtual_ips = virtual_ips or ["192.168.1.254"]
    # Default to the virtual router MAC unless the caller forced one.
    eff_src_mac = (src_mac or "").strip() or _vrrp_virtual_mac(vrid, family)
    config = {
        "version": int(version),
        "vrid": int(vrid),
        "priority": int(priority),
        "virtual_ips": list(virtual_ips),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": src_ip, "src_mac": eff_src_mac,
        "family": family.lower(),
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
    }
    sess = _Session(session_id=sid, protocol="vrrp", iface=iface, config=config)

    def _factory():
        from scapy.layers.inet import IP
        from scapy.layers.vrrp import VRRP, VRRPv3
        # VRRP destination: 224.0.0.18 (IPv4) or ff02::12 (IPv6).
        # VRRPv3 IP-family is inferred from the encapsulating L3 layer
        # (IP for v4, IPv6 for v6) — scapy doesn't take an explicit
        # `addr_type` kwarg, the wire byte position is derived from
        # the parent layer's protocol number.
        if family.lower() == "ipv6" and version == 3:
            from scapy.layers.inet6 import IPv6
            ip_layer = IPv6(src=src_ip, dst="ff02::12", hlim=255, nh=112)
            return (
                _l2_hdr(eff_src_mac, "33:33:00:00:00:12", 0x86dd, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
                / ip_layer
                / VRRPv3(
                    version=3, vrid=vrid, priority=priority,
                    addrlist=virtual_ips, adv=int(interval_s * 100),
                )
            )
        ip_layer = IP(src=src_ip, dst="224.0.0.18", ttl=255, proto=112)
        if version == 2:
            return (
                _l2_hdr(eff_src_mac, "01:00:5e:00:00:12", 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
                / ip_layer
                / VRRP(
                    version=2, vrid=vrid, priority=priority,
                    addrlist=virtual_ips, adv=int(interval_s),
                )
            )
        return (
            _l2_hdr(eff_src_mac, "01:00:5e:00:00:12", 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / ip_layer
            / VRRPv3(
                version=3, vrid=vrid, priority=priority,
                addrlist=virtual_ips, adv=int(interval_s * 100),
            )
        )

    _register_and_start(sess, _factory, interval_s, duration_s)
    logger.info(
        f"[L2] VRRP started session={sid} iface={iface} "
        f"v{version} vrid={vrid} family={family}"
    )
    return sid


# ====================================================================
# IGMP (RFC 2236 v2, RFC 3376 v3) — multicast group reports
# ====================================================================


def _ipv4_mcast_mac(ip: str) -> str:
    """Map an IPv4 multicast address to its Ethernet multicast MAC.

    RFC 1112 §6.4: 01:00:5e + the low 23 bits of the group address.
    e.g. 239.1.1.1 → 01:00:5e:01:01:01, and 224.0.0.22 → 01:00:5e:00:00:16.
    """
    p = [int(x) for x in ip.split(".")]
    return f"01:00:5e:{p[1] & 0x7f:02x}:{p[2]:02x}:{p[3]:02x}"


def start_igmp(
    iface: str,
    *,
    version: int = 2,
    group: str = "239.1.1.1",
    type_code: Optional[int] = None,
    interval_s: float = 60.0,
    duration_s: Optional[float] = None,
    src_ip: str = "10.0.0.10",
    src_mac: str = "00:11:22:33:44:04",
    vlan_id: Optional[int] = None,   # 802.1Q inner tag; None/0 = untagged
    vlan_pcp: int = 0,               # 802.1p inner priority (0-7)
    outer_vlan_id: Optional[int] = None,  # 802.1ad outer (S-VLAN); None/0 = single-tagged
    outer_vlan_pcp: int = 0,              # outer priority (0-7); QinQ only
) -> str:
    """Spawn an IGMP membership-report emitter.

    ``version=1`` sends V1 Membership Reports (type 0x12) per RFC 1112.
    ``version=2`` sends V2 Membership Reports (type 0x16) per RFC 2236.
    ``version=3`` sends V3 Membership Reports (type 0x22) with a
    single Mode-Is-Exclude record for ``group`` (RFC 3376).

    Override ``type_code`` to send Leave (0x17 for v2) or Query (0x11)
    instead — useful for switch IGMP-snooping tests. For v1 Queries,
    set ``type_code=0x11`` AND ``group="0.0.0.0"`` (General Query);
    the destination is auto-set to ALL-SYSTEMS (224.0.0.1).
    """
    sid = str(uuid.uuid4())
    config = {
        "version": int(version),
        "group": group,
        "type_code": type_code,
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": src_ip, "src_mac": src_mac,
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
    }
    sess = _Session(session_id=sid, protocol="igmp", iface=iface, config=config)

    def _factory():
        from scapy.layers.inet import IP
        # IGMP multicast frame: TTL must be 1 (per RFC 2236 §3). The
        # Ethernet dst MUST track the IP dst (RFC 1112 §6.4 mapping):
        #   • v2 Membership Report → IP dst = group  → MAC = group's MAC
        #   • v3 Membership Report → IP dst = 224.0.0.22 → MAC = 01:00:5e:00:00:16
        # The old code derived the L2 dst from the GROUP for BOTH versions,
        # so v3 reports went out addressed at L2 to the group's MAC while
        # their IP said 224.0.0.22 — a mismatched frame that IGMP-snooping
        # switches process on the wrong multicast MAC (or drop). Bug fix:
        # match L2 to L3 per version.
        if version == 3:
            from scapy.contrib.igmpv3 import IGMPv3, IGMPv3mr, IGMPv3gr
            t = type_code if type_code is not None else 0x22
            rec = IGMPv3gr(rtype=2, maddr=group)  # MODE_IS_EXCLUDE
            return (
                _l2_hdr(src_mac, _ipv4_mcast_mac("224.0.0.22"), 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
                / IP(src=src_ip, dst="224.0.0.22", ttl=1, options=[])
                / IGMPv3(type=t)
                / IGMPv3mr(numgrp=1, records=[rec])
            )
        from scapy.contrib.igmp import IGMP
        if version == 1:
            # IGMPv1 (RFC 1112 §4):
            #   • Membership Query: type 0x11, dst = 224.0.0.1
            #     (ALL-SYSTEMS). Sent by routers.
            #   • Membership Report: type 0x12, dst = group. Sent by
            #     hosts in response to a Query.
            # The scapy IGMP layer's mrcode field is RFC-2236 v2
            # max-resp-time; v1 spec says it's reserved/zero, so we
            # force mrcode=0 to be RFC-conformant.
            t = type_code if type_code is not None else 0x12
            ip_dst = "224.0.0.1" if t == 0x11 else group
            return (
                _l2_hdr(src_mac, _ipv4_mcast_mac(ip_dst), 0x0800,
                        vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
                / IP(src=src_ip, dst=ip_dst, ttl=1)
                / IGMP(type=t, mrcode=0, gaddr=group)
            )
        # v2 (RFC 2236): the established default path.
        t = type_code if type_code is not None else 0x16
        # Leave Group (0x17) is sent to ALL-ROUTERS 224.0.0.2 (RFC 2236
        # §3) — NOT the group. Membership Reports (0x16) and group-specific
        # Queries (0x11) target the group itself. The L2 dst tracks the L3
        # dst via the same RFC 1112 mapping.
        ip_dst = "224.0.0.2" if t == 0x17 else group
        return (
            _l2_hdr(src_mac, _ipv4_mcast_mac(ip_dst), 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / IP(src=src_ip, dst=ip_dst, ttl=1)
            / IGMP(type=t, gaddr=group)
        )

    _register_and_start(sess, _factory, interval_s, duration_s)
    logger.info(
        f"[L2] IGMP started session={sid} iface={iface} "
        f"v{version} group={group}"
    )
    return sid


# ====================================================================
# PIM Hello (RFC 7761 §4.3) — adjacency only
# ====================================================================
#
# Full PIM-SM (Join/Prune state machine) is on the roadmap; Hello
# alone is enough to make a real PIM router think we're a neighbour,
# which is what most lab tests need first.


def start_pim_hello(
    iface: str,
    *,
    hold_time: int = 105,
    dr_priority: int = 1,
    generation_id: int = 0xABCDEF01,
    interval_s: float = 30.0,
    duration_s: Optional[float] = None,
    src_ip: str = "10.0.0.20",
    src_mac: str = "00:11:22:33:44:05",
    vlan_id: Optional[int] = None,   # 802.1Q inner tag; None/0 = untagged
    vlan_pcp: int = 0,               # 802.1p inner priority (0-7)
    outer_vlan_id: Optional[int] = None,  # 802.1ad outer (S-VLAN); None/0 = single-tagged
    outer_vlan_pcp: int = 0,              # outer priority (0-7); QinQ only
) -> str:
    """Spawn a PIM Hello emitter — registers us as a PIM neighbour
    on the segment without actually doing Join/Prune."""
    sid = str(uuid.uuid4())
    config = {
        "hold_time": int(hold_time),
        "dr_priority": int(dr_priority),
        "generation_id": int(generation_id),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": src_ip, "src_mac": src_mac,
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
    }
    sess = _Session(session_id=sid, protocol="pim", iface=iface, config=config)

    def _factory():
        from scapy.layers.inet import IP
        from scapy.contrib.pim import PIMv2Hdr, PIMv2Hello
        from scapy.contrib.pim import PIMv2HelloHoldtime, PIMv2HelloDRPriority
        from scapy.contrib.pim import PIMv2HelloGenerationID
        # scapy field names differ per option-record type — see
        # PIMv2HelloHoldtime.fields_desc etc.
        return (
            # PIM all-routers multicast: 224.0.0.13, MAC 01:00:5e:00:00:0d
            _l2_hdr(src_mac, "01:00:5e:00:00:0d", 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / IP(src=src_ip, dst="224.0.0.13", ttl=1, proto=103)
            / PIMv2Hdr(type=0)   # 0 = Hello
            / PIMv2Hello(
                option=[
                    PIMv2HelloHoldtime(holdtime=hold_time),
                    PIMv2HelloDRPriority(dr_priority=dr_priority),
                    PIMv2HelloGenerationID(generation_id=generation_id),
                ],
            )
        )

    _register_and_start(sess, _factory, interval_s, duration_s)
    logger.info(f"[L2] PIM Hello started session={sid} iface={iface}")
    return sid


# ====================================================================
# BFD — Bidirectional Forwarding Detection (RFC 5880 / 5881)
# ====================================================================
#
# Single-hop async-mode control packets. UDP/3784 (multi-hop is 4784,
# echo is 3785). RFC 5881 §5 mandates IP TTL=255 for single-hop so a
# receiver can verify the packet was originated on the directly
# connected link.
#
# We don't implement the full state machine (no Poll / Final sequence,
# no peer's discriminator learning, no demand-mode). The emitter sends
# a fixed-state control packet at the configured interval, which is
# enough to: (a) keep a peer's session Up by asserting our liveness,
# or (b) deliberately tear a session down by sending state=Down.
#
# scapy doesn't carry a stable BFD layer across versions, so we build
# the 24-byte control payload via struct.pack — the RFC bit layout is
# fully pinned by tests/test_bfd_l2.py.


def start_bfd(
    iface: str,
    *,
    src_ip: str = "10.0.0.1",
    dst_ip: str = "10.0.0.2",
    src_mac: str = "00:11:22:33:44:06",
    dst_mac: str = "00:11:22:33:44:07",
    my_discriminator: int = 0x11111111,
    your_discriminator: int = 0,
    state: int = 3,                       # 0=AdminDown 1=Down 2=Init 3=Up
    detect_mult: int = 3,
    diag: int = 0,
    desired_min_tx_us: int = 1_000_000,
    required_min_rx_us: int = 1_000_000,
    required_min_echo_rx_us: int = 0,
    dst_udp_port: int = 3784,             # 3784 single-hop / 4784 multi-hop
    interval_s: float = 1.0,
    duration_s: Optional[float] = None,
    vlan_id: Optional[int] = None,
    vlan_pcp: int = 0,
    outer_vlan_id: Optional[int] = None,
    outer_vlan_pcp: int = 0,
) -> str:
    """Spawn a BFD control-packet emitter (RFC 5880 async mode).

    Defaults to State=Up, detect_mult=3, 1 Hz, dst_udp_port=3784
    (single-hop), TTL=255. Override state=1 (Down) to simulate a peer
    going down; tweak intervals for sub-second BFD (e.g. interval_s=0.1
    + desired_min_tx_us=100000).

    Returns session_id.
    """
    import struct
    import uuid as _uuid

    sid = str(_uuid.uuid4())
    config = {
        "src_ip": src_ip, "dst_ip": dst_ip,
        "src_mac": src_mac, "dst_mac": dst_mac,
        "my_discriminator": int(my_discriminator),
        "your_discriminator": int(your_discriminator),
        "state": int(state),
        "detect_mult": int(detect_mult),
        "diag": int(diag),
        "desired_min_tx_us": int(desired_min_tx_us),
        "required_min_rx_us": int(required_min_rx_us),
        "required_min_echo_rx_us": int(required_min_echo_rx_us),
        "dst_udp_port": int(dst_udp_port),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
    }
    sess = _Session(session_id=sid, protocol="bfd", iface=iface, config=config)

    # Pre-compute the BFD payload once — fixed across the session.
    # Byte 0: Version (top 3 bits, =3) | Diag (bottom 5 bits).
    # Byte 1: State (top 2 bits) | Flags (bottom 6 bits — P/F/C/A/D/M).
    #         No flags by default (no Poll handshake, no auth).
    # Byte 2: Detect Multiplier.
    # Byte 3: Length (24 for no-auth).
    # Then four 32-bit big-endian fields:
    #   My Discriminator, Your Discriminator,
    #   Desired Min TX Interval (µs), Required Min RX Interval (µs),
    #   Required Min Echo RX Interval (µs).
    ver_diag = (3 << 5) | (int(diag) & 0x1f)
    state_flags = (int(state) & 0x3) << 6
    bfd_payload = struct.pack(
        ">BBBBIIIII",
        ver_diag,
        state_flags,
        int(detect_mult) & 0xff,
        24,
        int(my_discriminator) & 0xffffffff,
        int(your_discriminator) & 0xffffffff,
        int(desired_min_tx_us) & 0xffffffff,
        int(required_min_rx_us) & 0xffffffff,
        int(required_min_echo_rx_us) & 0xffffffff,
    )

    def _factory():
        from scapy.layers.inet import IP, UDP
        from scapy.packet import Raw
        # RFC 5881 §5: single-hop BFD MUST use TTL=255. The receiver
        # verifies TTL==255 to confirm the packet originated on the
        # directly-connected link (no router could have decremented it).
        # An ephemeral source port keeps the path through any stateful
        # NAT/conntrack stable for the session lifetime.
        return (
            _l2_hdr(src_mac, dst_mac, 0x0800,
                    vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / IP(src=src_ip, dst=dst_ip, ttl=255)
            / UDP(sport=49152, dport=int(dst_udp_port))
            / Raw(load=bfd_payload)
        )

    _register_and_start(sess, _factory, interval_s, duration_s)
    logger.info(
        f"[L2] BFD started session={sid} iface={iface} "
        f"state={state} my_disc=0x{int(my_discriminator):08x}"
    )
    return sid
