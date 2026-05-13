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
        "duration_s": duration_s,
    }
    sess = _Session(session_id=sid, protocol="lacp", iface=iface, config=config)

    def _factory():
        from scapy.layers.l2 import Ether
        from scapy.contrib.lacp import SlowProtocol, LACP
        # Slow Protocols dest MAC + ethertype + subtype
        return (
            Ether(dst="01:80:c2:00:00:02", src=system_mac, type=0x8809)
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
    }
    sess = _Session(session_id=sid, protocol="lldp", iface=iface, config=config)

    def _factory():
        from scapy.layers.l2 import Ether
        from scapy.contrib.lldp import (
            LLDPDUChassisID, LLDPDUPortID, LLDPDUTimeToLive,
            LLDPDUSystemName, LLDPDUSystemDescription, LLDPDUEndOfLLDPDU,
        )
        # Scapy LLDP TLVs stack as layers via `/` — there's no
        # `LLDPDU(tlvlist=...)` constructor. Order matters: Chassis-ID,
        # Port-ID, TTL must come first per 802.1AB §8.6.
        return (
            Ether(dst="01:80:c2:00:00:0e", src=src_mac, type=0x88cc)
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
    src_mac: str = "00:11:22:33:44:03",
    family: str = "ipv4",   # "ipv4" or "ipv6" (v3 only)
) -> str:
    """Spawn a VRRP master advertisement emitter. Returns session_id.

    `version=2` is IPv4-only. `version=3` supports both AFs via
    `family`. `priority=255` means "owner of the virtual IP" (highest
    preemption); 100 is the default for non-owner masters.
    """
    sid = str(uuid.uuid4())
    virtual_ips = virtual_ips or ["192.168.1.254"]
    config = {
        "version": int(version),
        "vrid": int(vrid),
        "priority": int(priority),
        "virtual_ips": list(virtual_ips),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": src_ip, "src_mac": src_mac,
        "family": family.lower(),
    }
    sess = _Session(session_id=sid, protocol="vrrp", iface=iface, config=config)

    def _factory():
        from scapy.layers.l2 import Ether
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
                Ether(src=src_mac, dst="33:33:00:00:00:12")
                / ip_layer
                / VRRPv3(
                    version=3, vrid=vrid, priority=priority,
                    addrlist=virtual_ips, adv=int(interval_s * 100),
                )
            )
        ip_layer = IP(src=src_ip, dst="224.0.0.18", ttl=255, proto=112)
        if version == 2:
            return (
                Ether(src=src_mac, dst="01:00:5e:00:00:12")
                / ip_layer
                / VRRP(
                    version=2, vrid=vrid, priority=priority,
                    addrlist=virtual_ips, adv=int(interval_s),
                )
            )
        return (
            Ether(src=src_mac, dst="01:00:5e:00:00:12")
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
) -> str:
    """Spawn an IGMP membership-report emitter.

    `version=2` sends V2 Membership Reports (type 0x16).
    `version=3` sends V3 Membership Reports (type 0x22) with a
    single Mode-Is-Exclude record for `group`.

    Override `type_code` to send Leave (0x17 for v2) or Query (0x11)
    instead — useful for switch IGMP-snooping tests.
    """
    sid = str(uuid.uuid4())
    config = {
        "version": int(version),
        "group": group,
        "type_code": type_code,
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": src_ip, "src_mac": src_mac,
    }
    sess = _Session(session_id=sid, protocol="igmp", iface=iface, config=config)

    def _factory():
        from scapy.layers.l2 import Ether
        from scapy.layers.inet import IP
        # IGMP multicast frame: ether dst maps from the v4 group address,
        # IP dst = group, TTL must be 1 (per RFC 2236 §3).
        # Multicast MAC = 01:00:5e + low 23 bits of group.
        parts = [int(p) for p in group.split(".")]
        dst_mac = (
            f"01:00:5e:{parts[1] & 0x7f:02x}:"
            f"{parts[2]:02x}:{parts[3]:02x}"
        )
        ether = Ether(src=src_mac, dst=dst_mac)
        if version == 3:
            from scapy.contrib.igmpv3 import IGMPv3, IGMPv3mr, IGMPv3gr
            t = type_code if type_code is not None else 0x22
            rec = IGMPv3gr(rtype=2, maddr=group)  # MODE_IS_EXCLUDE
            return (
                ether
                / IP(src=src_ip, dst="224.0.0.22", ttl=1, options=[])
                / IGMPv3(type=t)
                / IGMPv3mr(numgrp=1, records=[rec])
            )
        from scapy.contrib.igmp import IGMP
        t = type_code if type_code is not None else 0x16
        return (
            ether
            / IP(src=src_ip, dst=group, ttl=1)
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
    }
    sess = _Session(session_id=sid, protocol="pim", iface=iface, config=config)

    def _factory():
        from scapy.layers.l2 import Ether
        from scapy.layers.inet import IP
        from scapy.contrib.pim import PIMv2Hdr, PIMv2Hello
        from scapy.contrib.pim import PIMv2HelloHoldtime, PIMv2HelloDRPriority
        from scapy.contrib.pim import PIMv2HelloGenerationID
        # scapy field names differ per option-record type — see
        # PIMv2HelloHoldtime.fields_desc etc.
        return (
            # PIM all-routers multicast: 224.0.0.13, MAC 01:00:5e:00:00:0d
            Ether(src=src_mac, dst="01:00:5e:00:00:0d")
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
