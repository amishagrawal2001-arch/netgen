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


# ---------------------------------------------------------------- iface auto-derive helpers
#
# v0.5.269: shared helpers so every emitter can accept blank src_mac /
# src_ip / dst_mac and back-fill from the real interface state, instead
# of hardcoded documentation values that (a) cause MAC-flap alarms when
# two netgen boxes share an L2 domain and (b) get silently dropped by
# real BFD/VRRP/PIM/IGMP daemons that compare the packet's source
# against what they've been configured to expect.
#
# All three helpers return None on failure so the caller can either
# fall back to a hardcoded value or surface a diagnostic — they never
# raise, so pre-existing test paths that pass explicit values still
# work unchanged.


def _iface_mac(iface: str) -> Optional[str]:
    """Return the MAC address of the given interface as a colon-
    separated lowercase string, or None if it can't be read.

    Prefers netifaces (installed as a scapy dep) over parsing
    /sys/class/net so the same code path works on macOS lab hosts
    where /sys doesn't exist."""
    try:
        import netifaces
        addrs = netifaces.ifaddresses(iface)
        link = addrs.get(netifaces.AF_LINK) or addrs.get(17) or []
        for entry in link:
            addr = (entry.get("addr") or "").strip().lower()
            if addr and addr.count(":") == 5:
                return addr
    except Exception as exc:
        logger.debug(f"[L2] _iface_mac({iface!r}) netifaces failed: {exc}")
    # Linux /sys fallback (macOS has no /sys/class/net).
    try:
        from pathlib import Path
        p = Path(f"/sys/class/net/{iface}/address")
        if p.exists():
            v = p.read_text().strip().lower()
            if v and v.count(":") == 5:
                return v
    except Exception:
        pass
    return None


def _iface_primary_ipv4(iface: str) -> Optional[str]:
    """Return the first non-loopback IPv4 address bound to `iface`, or
    None. Used to auto-derive `src_ip` for VRRP/IGMP/PIM/BFD when the
    operator left it blank — real daemons on the peer side compare
    the packet's source IP against their neighbor config and silently
    drop mismatches."""
    try:
        import netifaces
        addrs = netifaces.ifaddresses(iface)
        v4 = addrs.get(netifaces.AF_INET) or addrs.get(2) or []
        for entry in v4:
            addr = (entry.get("addr") or "").strip()
            if addr and not addr.startswith("127."):
                return addr
    except Exception as exc:
        logger.debug(
            f"[L2] _iface_primary_ipv4({iface!r}) netifaces failed: {exc}"
        )
    return None


def _resolve_dst_mac(iface: str, dst_ip: str,
                     probe_timeout_s: float = 2.0) -> Optional[str]:
    """Resolve the peer's MAC for `dst_ip`, first consulting the
    kernel's ARP cache (`ip neigh get`) and falling back to a scapy
    ARP probe on `iface`. Returns lowercase colon MAC or None.

    v0.5.269: BFD's `start_bfd` accepts blank `dst_mac` and calls this
    helper — hardcoding a documentation MAC (00:11:22:33:44:07) meant
    the frame never reached the intended peer because the switch's
    MAC table had never learned that address on any port. Neighbor-
    resolution here is one-shot at session-start; if the peer's MAC
    changes mid-session the operator restarts the session.
    """
    if not dst_ip:
        return None
    # Kernel ARP cache first — cheap, no wire traffic.
    try:
        import subprocess
        out = subprocess.run(
            ["ip", "neigh", "get", str(dst_ip), "dev", iface],
            capture_output=True, text=True, timeout=1.5,
        )
        # Output format: "10.0.0.2 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE"
        for tok in (out.stdout or "").split():
            if tok.count(":") == 5 and all(len(x) == 2 for x in tok.split(":")):
                return tok.lower()
    except Exception as exc:
        logger.debug(f"[L2] `ip neigh get {dst_ip}` failed: {exc}")
    # Fallback: scapy ARP request (Linux only in practice; macOS
    # lab hosts should always have the neighbor in cache from the
    # first-connection PING the operator runs).
    try:
        from scapy.all import Ether, ARP, srp
        ans, _ = srp(
            Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=str(dst_ip)),
            iface=iface, timeout=probe_timeout_s, verbose=False,
        )
        for _, rcv in ans:
            hw = (rcv.hwsrc or "").strip().lower()
            if hw:
                return hw
    except Exception as exc:
        logger.debug(f"[L2] scapy ARP probe for {dst_ip} on {iface}: {exc}")
    return None


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
            # v0.5.252: getattr guard for thread-shaped mocks that
            # lack is_alive (existing test fixtures rely on this).
            _is_alive_fn = getattr(self.thread, "is_alive", None)
            _thread_alive = bool(callable(_is_alive_fn) and _is_alive_fn())
            running = (
                self.thread is not None
                and _thread_alive
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


def stop_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Stop a session and return its final counter snapshot, or None
    when the session_id doesn't exist.

    v0.5.252 (audit L2-5 + L2-8): two changes.

    1. **Return the snapshot.** Pre-fix returned bool; the eviction
       comment said "final counters are already returned in the
       /api/l2/<proto>/stop response body" — but no per-protocol stop
       endpoint exists (only the kind-agnostic /api/l2/stop) and
       server/l2_routes.py's stop returned {"stopped": bool} with no
       counters. The client's 3s poll then found the session gone
       from /api/l2/sessions, so the row disappeared with no chance
       to read frames_sent / bytes_sent / last_error post-mortem.
       Now the callers get the snapshot back and the HTTP layer
       includes it in the response body.

    2. **Only evict when the thread actually finished.** Pre-fix
       popped the entry after a bounded 3s join even when
       `thread.is_alive()` remained True — the worker (blocked in
       scapy sendp() or a slow driver TX ring) kept emitting frames
       with no session visible in list_sessions and no way to
       re-stop it via stop_all_sessions. Now, if the join times out,
       the entry stays in the registry with `stopped=True` set on
       the counters so the operator can see the zombie state and
       retry `stop_session` (which is idempotent — stop_evt is
       already set, we just re-join).
    """
    with _REG_LOCK:
        sess = _SESSIONS.get(session_id)
    if not sess:
        return None
    sess.stop_evt.set()
    _clean_exit = True
    if sess.thread:
        sess.thread.join(timeout=3.0)
        # v0.5.252: use getattr guard so a thread-shaped mock (or
        # any object that lacks is_alive) counts as clean-exit,
        # matching the pre-v0.5.252 behavior for those callers.
        _is_alive = getattr(sess.thread, "is_alive", lambda: False)
        if callable(_is_alive) and _is_alive():
            _clean_exit = False
            logger.warning(
                "[L2] stop_session %s: worker thread still alive after "
                "3s join — leaving in registry as zombie. Retry stop "
                "or restart netgen-server to reap.",
                session_id,
            )
    with sess.lock:
        sess.counters.stopped_at = time.time()
    snap = sess.snapshot()
    # v0.5.252: only evict on a clean thread exit. A stuck sendp()
    # worker keeps holding the interface; keeping it visible in
    # list_sessions() lets the operator diagnose + retry.
    if _clean_exit:
        with _REG_LOCK:
            _SESSIONS.pop(session_id, None)
    return snap


def stop_all_sessions() -> int:
    with _REG_LOCK:
        ids = list(_SESSIONS.keys())
    n = 0
    for sid in ids:
        # v0.5.252: stop_session now returns Optional[Dict] (the
        # snapshot) instead of bool; None = session_id vanished
        # between list and stop.
        if stop_session(sid) is not None:
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
    # v0.5.252 (audit L2-10): compensate for send-duration drift. Pre-
    # fix cycle time was send_duration + interval_s (the wait ran
    # AFTER sendp). For BFD sub-second modes (interval_s=0.1,
    # detect_mult=3, negotiated detection window ~300ms) a single
    # scapy sendp taking 30-50ms pushed the actual cadence to
    # ~130-150ms — enough to exceed the peer's detection window
    # and cause spurious "peer down". Now: schedule the NEXT tick
    # off `next_tick_at = last_tick_at + interval_s` and wait only
    # the residual (min 0). If we're behind (sendp took longer than
    # interval), fire immediately without waiting.
    next_tick_at = time.time()
    while not sess.stop_evt.is_set():
        if deadline is not None and time.time() >= deadline:
            break
        _cycle_started_at = time.time()
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
        next_tick_at += interval_s
        _residual = next_tick_at - time.time()
        if _residual > 0:
            if sess.stop_evt.wait(_residual):
                break
        else:
            # Behind schedule (sendp took longer than interval).
            # Skip the wait entirely so we don't fall further behind;
            # re-anchor next_tick_at to now so drift doesn't compound
            # across many slow cycles.
            next_tick_at = time.time()
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
    system_mac: str = "",   # v0.5.269: blank → auto-derive from iface MAC
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

    v0.5.269 (L2-C6): `fast=True` implicitly OR's the Timeout (0x02)
    bit into `state` if the caller didn't set it. Without it, the
    LAG partner reads Timeout=Long from our LACPDU and starts its
    Received-machine 90-second timer while we send at 1-second
    cadence — a mismatch that doesn't drop the LAG but wastes time
    on lab convergence tests. IEEE 802.1AX §6.4.4.2 says the sender
    must set Timeout=Short whenever it wants to be polled at PDU_FAST.

    v0.5.269 (L2-C7): blank `system_mac` → auto-derived from the
    interface's own MAC via `_iface_mac(iface)`. Two netgen boxes
    trunked into the same switch with the same hardcoded
    00:11:22:33:44:01 both looked like the same LACP Actor to the
    switch and the LAG never converged. Falls back to the pre-fix
    documentation MAC only if the interface can't be read.
    """
    # v0.5.269 (L2-C7): auto-derive system_mac when blank.
    eff_system_mac = (system_mac or "").strip().lower()
    if not eff_system_mac:
        eff_system_mac = _iface_mac(iface) or "00:11:22:33:44:01"
    # v0.5.269 (L2-C6): make sure Timeout=Short is set with fast=1s.
    eff_state = int(state)
    if fast:
        eff_state |= 0x02
    sid = str(uuid.uuid4())
    config = {
        "system_priority": int(system_priority),
        "system_mac": eff_system_mac,
        "key": int(key),
        "port_priority": int(port_priority),
        "port_number": int(port_number),
        "state": eff_state,
        "fast": bool(fast),
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
        "duration_s": duration_s,
    }
    sess = _Session(session_id=sid, protocol="lacp", iface=iface, config=config)

    def _factory():
        from scapy.contrib.lacp import SlowProtocol, LACP
        # Slow Protocols dest MAC + ethertype (0x8809) + subtype
        # v0.5.269: use eff_system_mac (iface-derived) + eff_state
        # (Timeout=Short OR'd if fast) — see docstring.
        return (
            _l2_hdr(eff_system_mac, "01:80:c2:00:00:02", 0x8809, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / SlowProtocol(subtype=0x01)
            / LACP(
                version=1,
                actor_system_priority=system_priority,
                actor_system=eff_system_mac,
                actor_key=key,
                actor_port_priority=port_priority,
                actor_port_number=port_number,
                actor_state=eff_state,
            )
        )

    interval = 1.0 if fast else 30.0
    _register_and_start(sess, _factory, interval, duration_s)
    logger.info(
        f"[L2] LACP started session={sid} iface={iface} fast={fast} "
        f"system_mac={eff_system_mac} state=0x{eff_state:02x}"
    )
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
        # v0.5.252 (audit L2-7): encode with `utf-8` + errors="replace"
        # (LLDP TLVs are 8-bit octet strings; 802.1AB doesn't restrict
        # to ASCII, and network names in the wild use UTF-8 —
        # 'switch-café', non-Latin hostnames, etc.). Pre-fix used
        # `.encode('ascii')` bare → any non-ASCII byte raised
        # UnicodeEncodeError inside _run_periodic, generic-except
        # bumped frames_failed + wrote to last_error, then the loop
        # kept spinning at 0 frames/sec on the wire forever.
        return (
            _l2_hdr(src_mac, "01:80:c2:00:00:0e", 0x88cc, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / LLDPDUChassisID(
                subtype="locally assigned",
                id=chassis_id.encode("utf-8", errors="replace"),
            )
            / LLDPDUPortID(
                subtype="locally assigned",
                id=port_id.encode("utf-8", errors="replace"),
            )
            / LLDPDUTimeToLive(ttl=ttl_s)
            / LLDPDUSystemName(
                system_name=system_name.encode("utf-8", errors="replace"),
            )
            / LLDPDUSystemDescription(
                description=system_description.encode("utf-8", errors="replace"),
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
    src_ip: str = "",   # v0.5.269 (L2-C4): blank → auto-derive from iface
    src_mac: Optional[str] = None,   # None/"" → derive the VRRP virtual MAC
    family: str = "ipv4",   # "ipv4" or "ipv6" (v3 only)
    auth_type: int = 0,    # RFC 3768 §5.3.6: 0=None, 1=Simple, 2=IPAH (v2 only)
    auth_data: str = "",   # up to 8 ASCII bytes for type=1; NUL-padded
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

    ``auth_type`` / ``auth_data`` apply ONLY to ``version=2`` (RFC 3768
    §5.3.6). Values:

    * 0 — No Authentication (default; matches the common case).
    * 1 — Simple Text Password. ``auth_data`` is packed into the 8-byte
      ``auth1+auth2`` field, NUL-padded if shorter, truncated if longer.
      Useful for lab tests of switch / peer behaviour when an auth-type
      or password mismatch should reject the advertisement.
    * 2 — IP Authentication Header (RFC 2402). Spec-defined but rarely
      used in practice; we wire the type byte but don't compute the AH
      payload (operators wanting AH should use IPsec end-to-end).

    For ``version=3`` (RFC 5798 §5.1) authentication has been removed
    from the protocol; these kwargs are silently ignored.
    """
    sid = str(uuid.uuid4())
    virtual_ips = virtual_ips or ["192.168.1.254"]
    # Default to the virtual router MAC unless the caller forced one.
    eff_src_mac = (src_mac or "").strip() or _vrrp_virtual_mac(vrid, family)
    # v0.5.269 (L2-C4): blank src_ip → auto-derive from the interface's
    # primary IPv4. RFC 5798 §5.2.4 says the source IP is the "physical
    # IP address" — a hardcoded 10.0.0.1 that doesn't match the iface
    # gets dropped by any FRR/keepalived peer that RPF-checks the
    # advertisement (or logs "VRRP_Instance: received advert from
    # unknown source"). Falls back to 10.0.0.1 for the pre-fix
    # behavior when the interface has no v4 address.
    eff_src_ip = (src_ip or "").strip()
    if not eff_src_ip:
        _fam = str(family).lower()
        if _fam == "ipv4":
            eff_src_ip = _iface_primary_ipv4(iface) or "10.0.0.1"
        else:
            # v6: no auto-derive yet — keep the previous behavior. A
            # follow-up can add _iface_primary_ipv6.
            eff_src_ip = "10.0.0.1"
    # v0.2.83: pack auth_data into the two 4-byte VRRPv2 auth fields.
    # NUL-pad if shorter than 8 bytes; truncate if longer. Encoded as
    # network-byte-order 32-bit integers since scapy's auth1/auth2
    # are IntField (4 bytes each).
    auth_bytes = (auth_data or "").encode("ascii", errors="replace")[:8]
    auth_bytes = auth_bytes.ljust(8, b"\x00")
    auth1_int = int.from_bytes(auth_bytes[:4], "big")
    auth2_int = int.from_bytes(auth_bytes[4:8], "big")

    config = {
        "version": int(version),
        "vrid": int(vrid),
        "priority": int(priority),
        "virtual_ips": list(virtual_ips),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": eff_src_ip, "src_mac": eff_src_mac,
        "family": family.lower(),
        "auth_type": int(auth_type),
        "auth_data": (auth_data or ""),  # store the operator's input
        "vlan_id": vlan_id, "vlan_pcp": int(vlan_pcp),
        "outer_vlan_id": outer_vlan_id, "outer_vlan_pcp": int(outer_vlan_pcp),
    }
    # Diagnostic: auth_type set with v3 is operator-confusable. Log so
    # the trail shows the v3 session ran auth-less by spec, not by bug.
    if version == 3 and int(auth_type) != 0:
        logger.debug(
            "[L2 VRRP] auth_type=%d ignored — VRRPv3 has no authentication "
            "(RFC 5798 §5.1 deprecated it; use IPsec at the IP layer)",
            auth_type,
        )
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
            ip_layer = IPv6(src=eff_src_ip, dst="ff02::12", hlim=255, nh=112)
            return (
                _l2_hdr(eff_src_mac, "33:33:00:00:00:12", 0x86dd, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
                / ip_layer
                / VRRPv3(
                    version=3, vrid=vrid, priority=priority,
                    addrlist=virtual_ips, adv=int(interval_s * 100),
                )
            )
        ip_layer = IP(src=eff_src_ip, dst="224.0.0.18", ttl=255, proto=112)
        if version == 2:
            # v0.2.83: RFC 3768 §5.3.6 authentication. auth_type 0 (None)
            # zeroes auth1+auth2 — what scapy does anyway. auth_type 1
            # (Simple Text Password) stuffs the 8 packed bytes; type 2
            # (IPAH) just sets the type byte (the AH payload itself is
            # IPsec's responsibility, not VRRP's).
            return (
                _l2_hdr(eff_src_mac, "01:00:5e:00:00:12", 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
                / ip_layer
                / VRRP(
                    version=2, vrid=vrid, priority=priority,
                    addrlist=virtual_ips, adv=int(interval_s),
                    authtype=int(auth_type),
                    auth1=auth1_int,
                    auth2=auth2_int,
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
    src_ip: str = "",   # v0.5.269 (L2-C4): blank → iface primary IPv4
    src_mac: str = "",  # v0.5.269 (L2-C5): blank → iface MAC
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

    v0.5.269 (L2-C1): every IGMP frame — v1, v2 AND v3 — now carries
    the IP Router Alert option (RFC 2236 §2, RFC 3376 §4). Pre-fix
    only v3 set it; v1/v2 reports without RA are silently dropped by
    every switch running `ip igmp snooping router-alert-check`
    (Cisco default) or `igmp-snooping router-alert-check` (Junos).
    v0.5.269 (L2-C4/C5): `src_ip` / `src_mac` default to blank —
    server auto-derives from the interface's primary IPv4 + MAC so
    the emitted report actually looks like it came from this host.
    """
    # v0.5.269 (L2-C4/C5): auto-derive src_ip and src_mac from the
    # interface when the operator leaves them blank.
    eff_src_ip = (src_ip or "").strip() or (
        _iface_primary_ipv4(iface) or "10.0.0.10"
    )
    eff_src_mac = (src_mac or "").strip().lower() or (
        _iface_mac(iface) or "00:11:22:33:44:04"
    )
    sid = str(uuid.uuid4())
    config = {
        "version": int(version),
        "group": group,
        "type_code": type_code,
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": eff_src_ip, "src_mac": eff_src_mac,
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
        # v0.5.269 (L2-C1): every IGMP message (v1/v2/v3) MUST carry
        # the IP Router Alert option — RFC 2236 §2 for v1/v2, RFC 3376
        # §4 for v3. Load once at the top so all three branches use
        # the same option list.
        from scapy.layers.inet import IPOption_Router_Alert
        _ra = [IPOption_Router_Alert()]
        if version == 3:
            from scapy.contrib.igmpv3 import IGMPv3, IGMPv3mr, IGMPv3gr
            t = type_code if type_code is not None else 0x22
            rec = IGMPv3gr(rtype=2, maddr=group)  # MODE_IS_EXCLUDE
            # v0.5.252 (audit L2-4): RFC 3376 §4 REQUIRES the IP
            # Router Alert option on every IGMPv3 message.
            return (
                _l2_hdr(eff_src_mac, _ipv4_mcast_mac("224.0.0.22"), 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
                / IP(src=eff_src_ip, dst="224.0.0.22", ttl=1, options=_ra)
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
            # v0.5.269 (L2-C1): RA option needed even for v1 —
            # snooping switches that hold v1 hosts also enforce
            # router-alert-check on the IPv4 header.
            t = type_code if type_code is not None else 0x12
            ip_dst = "224.0.0.1" if t == 0x11 else group
            return (
                _l2_hdr(eff_src_mac, _ipv4_mcast_mac(ip_dst), 0x0800,
                        vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
                / IP(src=eff_src_ip, dst=ip_dst, ttl=1, options=_ra)
                / IGMP(type=t, mrcode=0, gaddr=group)
            )
        # v2 (RFC 2236): the established default path.
        t = type_code if type_code is not None else 0x16
        # Leave Group (0x17) is sent to ALL-ROUTERS 224.0.0.2 (RFC 2236
        # §3) — NOT the group. Membership Reports (0x16) target the
        # group itself.
        # v0.5.252 (audit L2-3): General Queries (type 0x11, group
        # 0.0.0.0) go to ALL-HOSTS 224.0.0.1 per RFC 2236 §3 — pre-
        # fix, we used `group` verbatim which produced IP dst=0.0.0.0
        # and L2 dst=01:00:5e:00:00:00 (both invalid). Switches
        # silently dropped it and IGMP-snooping tests produced zero
        # host responses. Group-specific queries (0x11 with a real
        # group) still target that group. The v1 branch above
        # already had this special-case.
        if t == 0x17:
            ip_dst = "224.0.0.2"
        elif t == 0x11 and (not group or group == "0.0.0.0"):
            ip_dst = "224.0.0.1"
        else:
            ip_dst = group
        # v0.5.269 (L2-C1): RFC 2236 §2 requires the IP Router Alert
        # option on every IGMP v2 report / query. Snooping switches
        # with `router-alert-check` (Cisco default) drop reports that
        # lack it. This was the root cause of "reports emit but the
        # switch's IGMP-snooping table never populates".
        return (
            _l2_hdr(eff_src_mac, _ipv4_mcast_mac(ip_dst), 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / IP(src=eff_src_ip, dst=ip_dst, ttl=1, options=_ra)
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
    src_ip: str = "",   # v0.5.269 (L2-C4): blank → iface primary IPv4
    src_mac: str = "",  # v0.5.269 (L2-C5): blank → iface MAC
    vlan_id: Optional[int] = None,   # 802.1Q inner tag; None/0 = untagged
    vlan_pcp: int = 0,               # 802.1p inner priority (0-7)
    outer_vlan_id: Optional[int] = None,  # 802.1ad outer (S-VLAN); None/0 = single-tagged
    outer_vlan_pcp: int = 0,              # outer priority (0-7); QinQ only
) -> str:
    """Spawn a PIM Hello emitter — registers us as a PIM neighbour
    on the segment without actually doing Join/Prune.

    v0.5.269 (L2-C4/C5): `src_ip` / `src_mac` default to blank; server
    auto-derives from the interface. A hardcoded 10.0.0.20 source made
    the peer PIM router log "PIM Hello from non-directly-connected
    neighbor" and refuse to form adjacency when the interface's
    subnet didn't overlap.
    """
    eff_src_ip = (src_ip or "").strip() or (
        _iface_primary_ipv4(iface) or "10.0.0.20"
    )
    eff_src_mac = (src_mac or "").strip().lower() or (
        _iface_mac(iface) or "00:11:22:33:44:05"
    )
    sid = str(uuid.uuid4())
    config = {
        "hold_time": int(hold_time),
        "dr_priority": int(dr_priority),
        "generation_id": int(generation_id),
        "interval_s": float(interval_s),
        "duration_s": duration_s,
        "src_ip": eff_src_ip, "src_mac": eff_src_mac,
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
            _l2_hdr(eff_src_mac, "01:00:5e:00:00:0d", 0x0800, vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / IP(src=eff_src_ip, dst="224.0.0.13", ttl=1, proto=103)
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
    src_ip: str = "",   # v0.5.269 (L2-C2): blank → iface primary IPv4
    dst_ip: str = "10.0.0.2",
    src_mac: str = "",  # v0.5.269 (L2-C5): blank → iface MAC
    dst_mac: str = "",  # v0.5.269 (L2-C3): blank → ARP-resolve dst_ip
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

    v0.5.269 (L2-C2/C3/C5): auto-derive addressing when the caller
    leaves fields blank:

    * ``src_ip=""`` — read the interface's primary IPv4 via
      ``_iface_primary_ipv4``. Real BFD daemons (FRR bfdd, Cisco IOS
      XR, JunOS) verify the packet's source matches the configured
      peer address, so a hardcoded 10.0.0.1 that doesn't live on the
      iface got dropped BEFORE the session ever came Up.
    * ``src_mac=""`` — read the iface hardware MAC. Prevents MAC-flap
      alarms when multiple netgen hosts on the same L2 share the
      hardcoded 00:11:22:33:44:06.
    * ``dst_mac=""`` — ARP-resolve ``dst_ip`` (kernel `ip neigh`
      cache first, scapy ARP probe as fallback). The pre-fix default
      00:11:22:33:44:07 was a documentation MAC never in any real
      switch's MAC table, so the frame egressed the iface, hit the
      switch, and got flood-forwarded to every port (or dropped if
      the switch enforced ingress ACLs).

    Returns session_id.
    """
    import struct
    import uuid as _uuid

    # v0.5.269 (L2-C2/C3/C5): auto-derive addressing from the iface.
    eff_src_ip = (src_ip or "").strip() or (
        _iface_primary_ipv4(iface) or "10.0.0.1"
    )
    eff_src_mac = (src_mac or "").strip().lower() or (
        _iface_mac(iface) or "00:11:22:33:44:06"
    )
    eff_dst_mac = (dst_mac or "").strip().lower()
    if not eff_dst_mac:
        eff_dst_mac = _resolve_dst_mac(iface, dst_ip) or "00:11:22:33:44:07"
        if eff_dst_mac == "00:11:22:33:44:07":
            logger.warning(
                "[L2 BFD] could not resolve dst_mac for %s on %s "
                "(ARP miss); falling back to documentation MAC — "
                "the peer will not see the frame. Bring up the "
                "peer's IP + run a ping/ARP first, then restart "
                "the session.",
                dst_ip, iface,
            )

    sid = str(_uuid.uuid4())
    config = {
        "src_ip": eff_src_ip, "dst_ip": dst_ip,
        "src_mac": eff_src_mac, "dst_mac": eff_dst_mac,
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
    # v0.5.252 (audit L2-1): version MUST be 1 per RFC 5880 §4.1
    # ("The Version Number field is the version number of the
    # protocol.  This document defines protocol version 1"). Pre-fix
    # used version 3, so every RFC 5880 §6.8.6-compliant peer
    # ("If the version number is not correct (1), the packet MUST be
    # discarded") dropped every BFD frame we emitted. Emitter climbed
    # frames_sent but no BFD session ever came Up.
    ver_diag = (1 << 5) | (int(diag) & 0x1f)
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

    # v0.5.269 (L2-C8): RFC 5881 §4 requires the src UDP port to be
    # "unique" per session-pair. Hardcoded 49152 collided when two
    # BFD emitters ran to the same peer. Use a session-stable random
    # source port from the ephemeral range [49152, 65535].
    import random as _random
    _sport = _random.SystemRandom().randint(49152, 65535)

    def _factory():
        from scapy.layers.inet import IP, UDP
        from scapy.packet import Raw
        # RFC 5881 §5: single-hop BFD MUST use TTL=255. The receiver
        # verifies TTL==255 to confirm the packet originated on the
        # directly-connected link (no router could have decremented it).
        # An ephemeral source port keeps the path through any stateful
        # NAT/conntrack stable for the session lifetime.
        # v0.5.269: use auto-derived src_mac / dst_mac / src_ip.
        return (
            _l2_hdr(eff_src_mac, eff_dst_mac, 0x0800,
                    vlan_id, vlan_pcp,
                    outer_vlan_id, outer_vlan_pcp)
            / IP(src=eff_src_ip, dst=dst_ip, ttl=255)
            / UDP(sport=_sport, dport=int(dst_udp_port))
            / Raw(load=bfd_payload)
        )

    _register_and_start(sess, _factory, interval_s, duration_s)
    logger.info(
        f"[L2] BFD started session={sid} iface={iface} "
        f"state={state} my_disc=0x{int(my_discriminator):08x} "
        f"src_ip={eff_src_ip} src_mac={eff_src_mac} dst_mac={eff_dst_mac} "
        f"sport={_sport}"
    )
    return sid


# ====================================================================
# Frame preview (v0.2.84) — pure synchronous frame-build for the GUI
# ====================================================================
#
# The L2 dialog's "Preview frame" button needs to show the operator
# what's about to go on the wire WITHOUT spawning a worker thread or
# touching any state. We re-use the per-protocol factories' building
# blocks (_l2_hdr + the same scapy layers + the same RFC mappings)
# but in a pure pass-the-body function. Returns raw bytes ready for
# scapy.hexdump() / Packet.summary().

def build_preview_frame(protocol: str, body: Dict[str, Any]) -> "Optional[Any]":
    """Build the first frame the named protocol would emit, given the
    same body dict the REST endpoint receives. Returns a scapy Packet
    or None if the protocol is unrecognised. Pure — no threading, no
    session registration.

    ``protocol`` ∈ {"lacp", "lldp", "vrrp", "igmp", "pim", "bfd"}.
    The body keys are the same the REST endpoint accepts (validated
    by ``server/l2_routes.py``'s allow-list); missing keys take the
    factory defaults. Lifted defaults are deliberately permissive
    here so the preview works even on a partially-filled dialog.
    """
    proto = (protocol or "").lower().strip()
    b = dict(body or {})
    vlan_id = b.get("vlan_id") or None
    vlan_pcp = int(b.get("vlan_pcp") or 0)
    outer_vlan_id = b.get("outer_vlan_id") or None
    outer_vlan_pcp = int(b.get("outer_vlan_pcp") or 0)

    if proto == "lacp":
        system_mac = b.get("system_mac") or "00:11:22:33:44:01"
        return _l2_hdr(
            system_mac, "01:80:c2:00:00:02", 0x8809,
            vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp,
        ) / _lacpdu(b)

    if proto == "lldp":
        src_mac = b.get("src_mac") or "00:11:22:33:44:02"
        return _l2_hdr(
            src_mac, "01:80:c2:00:00:0e", 0x88cc,
            vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp,
        ) / _lldpdu(b)

    if proto == "vrrp":
        return _vrrp_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)

    if proto == "igmp":
        return _igmp_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)

    if proto == "pim":
        return _pim_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)

    if proto == "bfd":
        return _bfd_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)

    return None


def _lacpdu(b):
    from scapy.contrib.lacp import LACP, SlowProtocol
    return SlowProtocol(subtype=0x01) / LACP(
        actor_system_priority=int(b.get("system_priority") or 32768),
        actor_system=b.get("system_mac") or "00:11:22:33:44:01",
        actor_key=int(b.get("key") or 1),
        actor_port_priority=int(b.get("port_priority") or 32768),
        actor_port_number=int(b.get("port_number") or 1),
        # v0.5.252 (audit L2-9): default matches start_lacp's live
        # emitter (0x05 = Activity | Aggregation). Pre-fix used 0x3d
        # (adds Sync | Collecting | Distributing) so preview and
        # wire showed different actor_state bytes when the body
        # dict omitted `state` — the "preview matches wire"
        # invariant was violated on any partial-body call.
        # v0.5.269 (L2-C6): mirror the live path — with fast=True the
        # Timeout=Short bit (0x02) is OR'd into state so preview
        # matches the wire's actor_state byte.
        actor_state=(int(b.get("state") or 0x05) | (0x02 if b.get("fast") else 0)),
    )


def _lldpdu(b):
    """Match the live LLDP factory's stacking: TLVs chain via `/`
    (there is no `LLDPDU(tlvlist=...)` constructor in scapy)."""
    from scapy.contrib.lldp import (
        LLDPDUChassisID, LLDPDUPortID, LLDPDUTimeToLive,
        LLDPDUSystemName, LLDPDUSystemDescription, LLDPDUEndOfLLDPDU,
    )
    chassis_id = (b.get("chassis_id") or "netgen-host")
    port_id = (b.get("port_id") or "eth0")
    system_name = (b.get("system_name") or "netgen")
    system_description = (b.get("system_description") or "")
    # v0.5.252 (audit L2-7): utf-8 + errors="replace" — see live
    # LLDP factory above for full rationale.
    return (
        LLDPDUChassisID(subtype="locally assigned",
                        id=chassis_id.encode("utf-8", errors="replace"))
        / LLDPDUPortID(subtype="locally assigned",
                       id=port_id.encode("utf-8", errors="replace"))
        / LLDPDUTimeToLive(ttl=int(b.get("ttl_s") or 120))
        / LLDPDUSystemName(system_name=system_name.encode("utf-8", errors="replace"))
        / LLDPDUSystemDescription(
            description=system_description.encode("utf-8", errors="replace"))
        / LLDPDUEndOfLLDPDU()
    )


def _vrrp_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp):
    from scapy.layers.inet import IP
    from scapy.layers.vrrp import VRRP, VRRPv3
    version = int(b.get("version") or 3)
    vrid = int(b.get("vrid") or 1)
    priority = int(b.get("priority") or 100)
    family = str(b.get("family") or "ipv4").lower()
    src_ip = b.get("src_ip") or "10.0.0.1"
    src_mac = b.get("src_mac") or _vrrp_virtual_mac(vrid, family)
    virtual_ips = b.get("virtual_ips") or ["192.168.1.254"]
    interval_s = float(b.get("interval_s") or 1.0)
    if family == "ipv6" and version == 3:
        from scapy.layers.inet6 import IPv6
        return (
            _l2_hdr(src_mac, "33:33:00:00:00:12", 0x86dd,
                    vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
            / IPv6(src=src_ip, dst="ff02::12", hlim=255, nh=112)
            / VRRPv3(version=3, vrid=vrid, priority=priority,
                     addrlist=virtual_ips, adv=int(interval_s * 100))
        )
    ip = IP(src=src_ip, dst="224.0.0.18", ttl=255, proto=112)
    if version == 2:
        # Mirror the auth wiring v0.2.83 added.
        auth_bytes = (b.get("auth_data") or "").encode(
            "ascii", errors="replace")[:8].ljust(8, b"\x00")
        return (
            _l2_hdr(src_mac, "01:00:5e:00:00:12", 0x0800,
                    vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
            / ip
            / VRRP(version=2, vrid=vrid, priority=priority,
                   addrlist=virtual_ips, adv=int(interval_s),
                   authtype=int(b.get("auth_type") or 0),
                   auth1=int.from_bytes(auth_bytes[:4], "big"),
                   auth2=int.from_bytes(auth_bytes[4:8], "big"))
        )
    return (
        _l2_hdr(src_mac, "01:00:5e:00:00:12", 0x0800,
                vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
        / ip
        / VRRPv3(version=3, vrid=vrid, priority=priority,
                 addrlist=virtual_ips, adv=int(interval_s * 100))
    )


def _igmp_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp):
    from scapy.layers.inet import IP, IPOption_Router_Alert
    # v0.5.269 (L2-C1): preview must match the live IGMP emitter,
    # which now attaches the IP Router Alert option on every version
    # (RFC 2236 §2 for v1/v2, RFC 3376 §4 for v3). Pre-fix preview
    # matched the pre-v0.5.269 live path: RA on v3 only, none on v2.
    _ra = [IPOption_Router_Alert()]
    version = int(b.get("version") or 2)
    group = b.get("group") or "239.1.1.1"
    src_ip = b.get("src_ip") or "10.0.0.10"
    src_mac = b.get("src_mac") or "00:11:22:33:44:04"
    type_code = b.get("type_code")
    if version == 3:
        from scapy.contrib.igmpv3 import IGMPv3, IGMPv3mr, IGMPv3gr
        t = type_code if type_code is not None else 0x22
        rec = IGMPv3gr(rtype=2, maddr=group)
        return (
            _l2_hdr(src_mac, _ipv4_mcast_mac("224.0.0.22"), 0x0800,
                    vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
            / IP(src=src_ip, dst="224.0.0.22", ttl=1, options=_ra)
            / IGMPv3(type=int(t))
            / IGMPv3mr(numgrp=1, records=[rec])
        )
    from scapy.contrib.igmp import IGMP
    if version == 1:
        t = type_code if type_code is not None else 0x12
        ip_dst = "224.0.0.1" if int(t) == 0x11 else group
        return (
            _l2_hdr(src_mac, _ipv4_mcast_mac(ip_dst), 0x0800,
                    vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
            / IP(src=src_ip, dst=ip_dst, ttl=1, options=_ra)
            / IGMP(type=int(t), mrcode=0, gaddr=group)
        )
    t = type_code if type_code is not None else 0x16
    ip_dst = "224.0.0.2" if int(t) == 0x17 else group
    return (
        _l2_hdr(src_mac, _ipv4_mcast_mac(ip_dst), 0x0800,
                vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
        / IP(src=src_ip, dst=ip_dst, ttl=1, options=_ra)
        / IGMP(type=int(t), gaddr=group)
    )


def _pim_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp):
    """Mirror the live PIM factory's PIMv2Hdr + PIMv2Hello option list
    (scapy class names are PIMv2*, not bare PIM*)."""
    from scapy.layers.inet import IP
    from scapy.contrib.pim import (
        PIMv2Hdr, PIMv2Hello,
        PIMv2HelloHoldtime, PIMv2HelloDRPriority,
        PIMv2HelloGenerationID,
    )
    src_ip = b.get("src_ip") or "10.0.0.20"
    src_mac = b.get("src_mac") or "00:11:22:33:44:05"
    return (
        _l2_hdr(src_mac, "01:00:5e:00:00:0d", 0x0800,
                vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
        / IP(src=src_ip, dst="224.0.0.13", ttl=1, proto=103)
        / PIMv2Hdr(type=0)  # 0 = Hello
        / PIMv2Hello(option=[
            PIMv2HelloHoldtime(holdtime=int(b.get("hold_time") or 105)),
            PIMv2HelloDRPriority(dr_priority=int(b.get("dr_priority") or 1)),
            PIMv2HelloGenerationID(
                generation_id=int(b.get("generation_id") or 0xABCDEF01)
            ),
        ])
    )


def _bfd_preview(b, vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp):
    from scapy.layers.inet import IP, UDP
    from scapy.packet import Raw
    import struct
    src_ip = b.get("src_ip") or "10.0.0.1"
    dst_ip = b.get("dst_ip") or "10.0.0.2"
    src_mac = b.get("src_mac") or "00:11:22:33:44:06"
    dst_mac = b.get("dst_mac") or "00:11:22:33:44:07"
    my_disc = int(b.get("my_discriminator") or 0x11111111)
    your_disc = int(b.get("your_discriminator") or 0)
    state = int(b.get("state") or 3)  # Up
    detect_mult = int(b.get("detect_mult") or 3)
    diag = int(b.get("diag") or 0)
    tx_us = int(b.get("desired_min_tx_us") or 1_000_000)
    rx_us = int(b.get("required_min_rx_us") or 1_000_000)
    echo_us = int(b.get("required_min_echo_rx_us") or 0)
    # v0.5.252 (audit L2-1): version=1 per RFC 5880 §4.1 — same bug
    # as start_bfd's live emitter above; preview must match wire.
    ver_diag = (1 << 5) | (diag & 0x1f)
    sta_flags = (state & 0x3) << 6
    payload = struct.pack(
        "!BBBBII III",
        ver_diag, sta_flags, detect_mult, 24,
        my_disc & 0xffffffff, your_disc & 0xffffffff,
        tx_us & 0xffffffff, rx_us & 0xffffffff,
        echo_us & 0xffffffff,
    )
    return (
        _l2_hdr(src_mac, dst_mac, 0x0800,
                vlan_id, vlan_pcp, outer_vlan_id, outer_vlan_pcp)
        / IP(src=src_ip, dst=dst_ip, ttl=255)
        / UDP(sport=49152, dport=int(b.get("dst_udp_port") or 3784))
        / Raw(load=payload)
    )
