"""Stateful TCP traffic-generator.

The legacy scapy-based generator throws raw packets onto the wire and
never completes a 3-way handshake — useful for line-rate forwarding
tests, useless for "does my middlebox actually proxy TCP correctly?".
This module is the parallel, *stateful* path: real Python sockets, the
OS kernel manages the connection state machine, and we measure things
that only make sense in a connected world (handshake latency, RTT,
graceful close, kernel-reported retransmits).

Capabilities
------------

  * **Client mode** — opens TCP connections to a target, sends a fixed
    payload, reads the response, closes cleanly. Loops for a configured
    duration. Concurrency knob = number of sender threads.
  * **Server mode** — listens on a port; for each connection, reads the
    full request and writes a configurable response back. Threaded
    accept so the listener doesn't serialize, with handler-thread
    tracking so `stop_session()` actually drains.
  * **Per-VRF binding** — on Linux, the worker binds the socket to the
    device's VRF interface via `SO_BINDTODEVICE` so traffic enters the
    expected routing table. Requires root. On non-Linux hosts (macOS
    laptops, mostly) the flag is silently a no-op — the caller's
    `src_ip` bind is still honoured.
  * **TLS** — opt-in TLS wrap on both sides (client uses an unverified
    context by default — these are test sessions, not production HTTPS;
    set `tls_verify=True` to enforce. Server requires `tls_cert`/
    `tls_key`).
  * **L7 framing** — `protocol="raw"` (default) sends `N` bytes of "N"s
    and counts the echo. `protocol="http"` sends an HTTP/1.1 GET and
    parses the status; the server responds 200 OK with a body of the
    configured size. Useful for testing reverse proxies / WAFs.
  * **Kernel stats** — after each connect, pulls `TCP_INFO` via
    getsockopt and aggregates total retransmits and kernel-reported
    smoothed RTT. Linux only; falls back cleanly elsewhere.
  * **Counters** — per-session aggregates (conns attempted / established
    / failed, bytes tx / rx, avg handshake ms, avg RTT both userspace-
    and kernel-measured, total retransmits, last error).
  * **Lifecycle** — sessions live in an in-process registry keyed by
    UUID; callers poll for stats and stop by ID.
"""

from __future__ import annotations

import logging
import socket
import ssl
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -------------------- platform feature detection (do once) ----------------

_IS_LINUX = sys.platform.startswith("linux")

# SO_BINDTODEVICE isn't exposed in the Python `socket` module on all
# builds (it's a Linux-only sockopt). Cache the numeric value so we
# don't have to keep guarding the import.
_SO_BINDTODEVICE: Optional[int] = getattr(socket, "SO_BINDTODEVICE", 25 if _IS_LINUX else None)

# TCP_INFO struct: stable layout in the kernel uapi. We only read the
# first few fields — that's enough for retransmits + smoothed RTT.
# Reference: include/uapi/linux/tcp.h `struct tcp_info`.
# Format chars: B=u8, I=u32. Linux pads/aligns the struct so reading
# just the first 7 bytes (state/ca_state/retransmits/probes/backoff/
# options/snd_rcv_wscale) is safe regardless of kernel version.
_TCP_INFO_FMT = "B" * 8 + "IIIIIIIIIIIIIIIIIIIIIIIIII"
_TCP_INFO_SIZE = struct.calcsize(_TCP_INFO_FMT)


def _read_tcp_info(sock: socket.socket) -> Optional[Dict[str, int]]:
    """Pull TCP_INFO from a connected socket. Returns None on any
    platform / kernel that doesn't support it, so callers can `if info`
    without exception handling."""
    if not _IS_LINUX:
        return None
    try:
        raw = sock.getsockopt(
            socket.IPPROTO_TCP,
            getattr(socket, "TCP_INFO", 11),
            _TCP_INFO_SIZE,
        )
    except (OSError, AttributeError):
        return None
    if not raw or len(raw) < _TCP_INFO_SIZE:
        return None
    try:
        fields = struct.unpack(_TCP_INFO_FMT, raw[:_TCP_INFO_SIZE])
    except struct.error:
        return None
    # struct tcp_info { u8 state; u8 ca_state; u8 retransmits;
    #                   u8 probes; u8 backoff; u8 options;
    #                   u8 snd_wscale:4, rcv_wscale:4;
    #                   u8 delivery_rate_app_limited:1, fastopen_client_fail:2;
    #                   u32 rto; u32 ato; u32 snd_mss; u32 rcv_mss;
    #                   u32 unacked; u32 sacked; u32 lost; u32 retrans;
    #                   u32 fackets; u32 last_data_sent; u32 last_ack_sent;
    #                   u32 last_data_recv; u32 last_ack_recv;
    #                   u32 pmtu; u32 rcv_ssthresh; u32 rtt; u32 rttvar; ... }
    # Indices below match the format string above (8 u8 + 26 u32).
    return {
        "state":         fields[0],
        "retransmits":   fields[2],   # cumulative coarse retransmit count
        "total_retrans": fields[8 + 15],
        "rtt_us":        fields[8 + 16],
        "rttvar_us":     fields[8 + 17],
    }


def _bind_to_vrf(sock: socket.socket, vrf: str) -> Optional[str]:
    """Best-effort SO_BINDTODEVICE. Returns None on success, or a
    short reason string when the bind can't be applied — caller can
    surface this on the session's `last_error` so the operator notices
    a silent fall-back to the default routing table.

    Quietly skips on non-Linux (caller's src_ip bind is still used)."""
    if not vrf:
        return None
    if not _IS_LINUX or _SO_BINDTODEVICE is None:
        return f"vrf={vrf} ignored (SO_BINDTODEVICE only on Linux)"
    try:
        sock.setsockopt(
            socket.SOL_SOCKET, _SO_BINDTODEVICE,
            vrf.encode("ascii") + b"\0",
        )
        return None
    except PermissionError:
        return f"vrf={vrf} requires CAP_NET_RAW / root"
    except OSError as exc:
        return f"vrf={vrf} bind failed: {exc}"


# ---------------------------------------------------------------- counters


@dataclass
class _Counters:
    """Per-session running counters. Updated under the session lock."""
    started_at: float = field(default_factory=time.time)
    stopped_at: Optional[float] = None

    conns_attempted: int = 0
    conns_established: int = 0
    conns_failed: int = 0
    bytes_tx: int = 0
    bytes_rx: int = 0
    handshake_total_ms: float = 0.0   # cumulative; divide by established
    rtt_total_ms: float = 0.0         # userspace RTT (send→last-byte)
    rtt_samples: int = 0
    kernel_rtt_us_total: float = 0.0  # from TCP_INFO.rtt_us
    kernel_rtt_samples: int = 0
    retransmits_total: int = 0        # cumulative TCP_INFO.total_retrans
    http_status_2xx: int = 0
    http_status_other: int = 0
    # DNS-over-TCP counters — populated when `protocol="dns"`. We
    # tally by RCODE category: NOERROR (0), NXDOMAIN (3), SERVFAIL (2),
    # everything else lumped as "other". REFUSED / FORMERR show up in
    # the "other" bucket — operators chasing those should grep
    # last_error which carries the raw rcode.
    dns_noerror: int = 0
    dns_nxdomain: int = 0
    dns_servfail: int = 0
    dns_other: int = 0
    # SIP-over-TCP counters — populated when `protocol="sip"`. Bin by
    # status-class so the operator can spot regressions ("did the
    # registrar suddenly start 401-ing everyone?") at a glance.
    sip_2xx: int = 0   # 200 OK, 202 Accepted, ...
    sip_3xx: int = 0   # redirects
    sip_4xx: int = 0   # 401 Unauthorized, 403, 404, 486, ...
    sip_5xx: int = 0   # 500-599 server errors
    sip_other: int = 0
    last_error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        avg_hs = (self.handshake_total_ms / self.conns_established
                  if self.conns_established else 0.0)
        avg_rtt = (self.rtt_total_ms / self.rtt_samples
                   if self.rtt_samples else 0.0)
        avg_krtt_us = (self.kernel_rtt_us_total / self.kernel_rtt_samples
                       if self.kernel_rtt_samples else 0.0)
        return {
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "uptime_s": (self.stopped_at or time.time()) - self.started_at,
            "conns_attempted": self.conns_attempted,
            "conns_established": self.conns_established,
            "conns_failed": self.conns_failed,
            "bytes_tx": self.bytes_tx,
            "bytes_rx": self.bytes_rx,
            "avg_handshake_ms": round(avg_hs, 3),
            "avg_rtt_ms": round(avg_rtt, 3),
            "rtt_samples": self.rtt_samples,
            "avg_kernel_rtt_us": round(avg_krtt_us, 1),
            "kernel_rtt_samples": self.kernel_rtt_samples,
            "retransmits_total": self.retransmits_total,
            "http_status_2xx": self.http_status_2xx,
            "http_status_other": self.http_status_other,
            "dns_noerror": self.dns_noerror,
            "dns_nxdomain": self.dns_nxdomain,
            "dns_servfail": self.dns_servfail,
            "dns_other": self.dns_other,
            "sip_2xx": self.sip_2xx,
            "sip_3xx": self.sip_3xx,
            "sip_4xx": self.sip_4xx,
            "sip_5xx": self.sip_5xx,
            "sip_other": self.sip_other,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------- session


@dataclass
class _Session:
    """Bookkeeping for one start/stop cycle."""
    session_id: str
    role: str          # "client" or "server"
    config: Dict[str, Any]
    counters: _Counters = field(default_factory=_Counters)
    thread: Optional[threading.Thread] = None
    stop_evt: threading.Event = field(default_factory=threading.Event)
    sock: Optional[socket.socket] = None
    handlers: List[threading.Thread] = field(default_factory=list)
    handlers_lock: threading.Lock = field(default_factory=threading.Lock)
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
                "role": self.role,
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
    # Close server listening socket to unblock accept() immediately
    # rather than waiting out the 0.5s settimeout cycle.
    try:
        if sess.sock:
            try:
                sess.sock.close()
            except Exception:
                pass
    except Exception:
        pass
    if sess.thread:
        sess.thread.join(timeout=3.0)
    # Drain server handler threads with a bounded join so a misbehaving
    # peer can't stall the stop indefinitely.
    with sess.handlers_lock:
        handlers = list(sess.handlers)
    deadline = time.time() + 2.0
    for h in handlers:
        remaining = max(0.05, deadline - time.time())
        try:
            h.join(timeout=remaining)
        except Exception:
            pass
    with sess.lock:
        sess.counters.stopped_at = time.time()
    return True


def stop_all_sessions() -> int:
    """Stop every running session — used at server shutdown so we don't
    leak listening sockets / sender threads. Returns the count stopped."""
    with _REG_LOCK:
        ids = list(_SESSIONS.keys())
    n = 0
    for sid in ids:
        if stop_session(sid):
            n += 1
    return n


# ---------------------------------------------------------------- HTTP framing


def _build_http_get(host: str, payload_bytes: int) -> bytes:
    """Construct a minimal HTTP/1.1 GET with a Content-Length-padded
    request body (some middleboxes want a real body, not a bare GET)."""
    body = b""
    if payload_bytes > 0:
        body = b"N" * payload_bytes
    return (
        f"POST /netgen HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: netgen-stateful-tcp/1\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii") + body


def _read_http_message(sock: socket.socket, max_bytes: int = 1024 * 1024
                      ) -> Tuple[Optional[int], int]:
    """Read one HTTP/1.1 message (request OR response) off the socket;
    returns (status_or_None, bytes_read).

    For responses, status is the integer status code; for requests
    (which don't have a status line), status is None. Either way, the
    function returns *as soon as the message is complete* — it reads
    headers until the `\\r\\n\\r\\n` boundary, then exactly
    Content-Length body bytes, then returns. Without that, the server
    would block waiting for EOF and the client would block waiting for
    a response — classic half-close deadlock.

    Falls back to draining to EOF when Content-Length is missing
    (`Connection: close` semantics) — that's still bounded by
    max_bytes and the socket's own timeout.
    """
    buf = bytearray()
    header_end = -1
    while header_end < 0 and len(buf) < max_bytes:
        try:
            chunk = sock.recv(8192)
        except socket.timeout:
            break
        if not chunk:
            break
        buf.extend(chunk)
        header_end = buf.find(b"\r\n\r\n")

    if not buf:
        return None, 0

    # Parse the first line (status or request line)
    status: Optional[int] = None
    first_line_end = buf.find(b"\r\n")
    if first_line_end < 0:
        first_line_end = min(len(buf), 200)
    try:
        first_line = bytes(buf[:first_line_end])
        parts = first_line.split(b" ", 2)
        if len(parts) >= 2 and parts[0].startswith(b"HTTP/"):
            status = int(parts[1])
    except Exception:
        status = None

    # Honour Content-Length so we don't wait for a half-close that
    # won't arrive on a keep-alive-but-Connection-close conversation.
    content_length = None
    if header_end > 0:
        try:
            headers_blob = bytes(buf[first_line_end + 2:header_end])
            for hline in headers_blob.split(b"\r\n"):
                lower = hline.lower()
                if lower.startswith(b"content-length:"):
                    content_length = int(hline.split(b":", 1)[1].strip())
                    break
        except Exception:
            content_length = None

    body_start = header_end + 4 if header_end >= 0 else len(buf)
    if content_length is not None:
        needed = body_start + content_length
        while len(buf) < needed and len(buf) < max_bytes:
            try:
                chunk = sock.recv(min(8192, needed - len(buf)))
            except socket.timeout:
                break
            if not chunk:
                break
            buf.extend(chunk)
    # When no Content-Length, the message ends at EOF / connection
    # close — drain whatever's left (bounded).
    elif header_end >= 0:
        while len(buf) < max_bytes:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            buf.extend(chunk)

    return status, len(buf)


# Backwards-compat alias: older call sites used the response-specific
# name. New code should use _read_http_message.
_read_http_response = _read_http_message


# ---------------------------------------------------------------- DNS-over-TCP
#
# RFC 7766: DNS messages over TCP are prefixed with a 2-byte network-order
# length field, then the standard 12-byte DNS header (RFC 1035 §4.1.1)
# and a question section. We build a minimal A-record query / NXDOMAIN
# response — operators care about throughput / latency / failure rates,
# not RR data fidelity, so the wire format is tight but standards-compliant.


def _build_dns_query(qname: str = "netgen.test", payload_bytes: int = 0) -> bytes:
    """Minimal DNS-over-TCP query for an A record (qtype=1, qclass=IN).

    `payload_bytes` adds opaque trailer bytes inside the DNS message
    (encoded as an OPT/EDNS0 pseudo-RR padding option) so the test
    can flex large message sizes without rolling RRs by hand.
    """
    import struct, random
    txn_id = random.randint(0, 0xFFFF)
    # Flags: standard query, RD=1
    flags = 0x0100
    qdcount, ancount, nscount, arcount = 1, 0, 0, 0
    header = struct.pack("!HHHHHH", txn_id, flags, qdcount, ancount, nscount, arcount)
    # Encode qname as length-prefixed labels
    labels = b""
    for part in qname.strip(".").split("."):
        labels += bytes([len(part)]) + part.encode("ascii")
    labels += b"\x00"
    # qtype=1 (A), qclass=1 (IN)
    question = labels + struct.pack("!HH", 1, 1)
    body = header + question
    # Pad to payload_bytes if requested (use opaque tail; the resolver
    # would normally treat trailing junk as malformed — but our test
    # server is tolerant on read).
    if payload_bytes and len(body) < payload_bytes:
        body += b"\x00" * (payload_bytes - len(body))
    # 2-byte length prefix per RFC 7766
    return struct.pack("!H", len(body)) + body


def _build_dns_response(query_bytes: bytes, rcode: int = 3) -> bytes:
    """Echo back the client's transaction ID with an NXDOMAIN response
    (rcode=3) by default. Symmetric counter pattern — answer immediately,
    one response per request, just like a recursive resolver."""
    import struct
    if len(query_bytes) < 2 + 12:
        # Malformed; fall back to an empty length-prefixed message.
        return b"\x00\x00"
    body = query_bytes[2:]   # strip 2-byte length prefix
    if len(body) < 12:
        return b"\x00\x00"
    txn_id = body[:2]
    # Build response header: QR=1, RD copied from query, RA=1, RCODE=rcode
    flags = 0x8180 | (rcode & 0x0F)
    qdcount = int.from_bytes(body[4:6], "big")  # echo
    new_header = txn_id + flags.to_bytes(2, "big") + body[4:6] + b"\x00\x00\x00\x00\x00\x00"
    # Reuse the question section verbatim so a packet inspector sees
    # the same name we asked about.
    response = new_header + body[12:]
    return struct.pack("!H", len(response)) + response


def _read_dns_message(sock: socket.socket, max_bytes: int = 65535
                     ) -> Tuple[Optional[int], bytes, int]:
    """Read one DNS-over-TCP message. Returns (rcode_or_None, raw_bytes, n_read).

    Honors the 2-byte length prefix so we don't need an idle-close
    delimiter — RFC 7766 makes message boundaries explicit, which
    avoids the half-close deadlock the HTTP path also has to dodge.
    """
    # Need 2 bytes for the length prefix.
    pfx = bytearray()
    while len(pfx) < 2:
        try:
            chunk = sock.recv(2 - len(pfx))
        except socket.timeout:
            return None, b"", len(pfx)
        if not chunk:
            return None, b"", len(pfx)
        pfx.extend(chunk)
    msg_len = int.from_bytes(pfx, "big")
    if msg_len == 0 or msg_len > max_bytes:
        return None, bytes(pfx), len(pfx)
    body = bytearray()
    while len(body) < msg_len:
        try:
            chunk = sock.recv(min(8192, msg_len - len(body)))
        except socket.timeout:
            break
        if not chunk:
            break
        body.extend(chunk)
    if len(body) < 12:
        return None, bytes(pfx + body), len(pfx) + len(body)
    # rcode lives in the low nibble of byte 3 of the DNS header
    rcode = body[3] & 0x0F
    return rcode, bytes(pfx + body), len(pfx) + len(body)


# ---------------------------------------------------------------- SIP-over-TCP
#
# RFC 3261 — SIP is text-based, line-oriented, with a `Content-Length`
# header that explicitly delimits the message. That makes framing
# identical to HTTP/1.1: read until `\r\n\r\n` for the headers, then
# exactly N body bytes per Content-Length. We send REGISTER messages
# and parse the response status code (200 OK / 401 Unauthorized /
# 403 Forbidden / 5xx) to bin per-class.


def _build_sip_register(host: str, user: str = "netgen", payload_bytes: int = 0) -> bytes:
    """Construct a minimal SIP REGISTER per RFC 3261. The Call-ID is
    random so replays don't collide on a real registrar; Content-Length
    is honoured so the parser can frame cleanly."""
    import random
    branch = f"z9hG4bK{random.randint(0, 0xFFFFFFFF):08x}"
    call_id = f"{random.randint(0, 0xFFFFFFFF):08x}@netgen.test"
    body = b"X" * max(0, payload_bytes) if payload_bytes else b""
    msg = (
        f"REGISTER sip:{host} SIP/2.0\r\n"
        f"Via: SIP/2.0/TCP {host};branch={branch}\r\n"
        f"From: <sip:{user}@{host}>;tag=netgen\r\n"
        f"To: <sip:{user}@{host}>\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 REGISTER\r\n"
        f"Contact: <sip:{user}@{host}>\r\n"
        f"Max-Forwards: 70\r\n"
        f"User-Agent: netgen-stateful-tcp/1\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
    ).encode("ascii")
    return msg + body


def _build_sip_response(request_bytes: bytes, status: int = 200,
                       reason: str = "OK") -> bytes:
    """Build a SIP response that mirrors the Via / From / To / Call-ID
    / CSeq from the request — enough for a real client to accept it as
    a valid response to its own transaction. The test server is
    deliberately tolerant: any unparseable request just gets a generic
    200 OK with empty mirror headers."""
    via = call_id = cseq = b""
    from_ = to = b""
    try:
        head_end = request_bytes.find(b"\r\n\r\n")
        hdrs = request_bytes[: head_end if head_end >= 0 else len(request_bytes)]
        for ln in hdrs.split(b"\r\n"):
            low = ln.lower()
            if low.startswith(b"via:"):
                via = ln
            elif low.startswith(b"from:"):
                from_ = ln
            elif low.startswith(b"to:"):
                to = ln
            elif low.startswith(b"call-id:"):
                call_id = ln
            elif low.startswith(b"cseq:"):
                cseq = ln
    except Exception:
        pass
    parts = [
        f"SIP/2.0 {status} {reason}".encode("ascii"),
    ]
    for h in (via, from_, to, call_id, cseq):
        if h:
            parts.append(h)
    parts.append(b"Content-Length: 0")
    parts.append(b"")  # end-of-headers
    parts.append(b"")
    return b"\r\n".join(parts)


def _read_sip_message(sock: socket.socket, max_bytes: int = 65535
                     ) -> Tuple[Optional[int], int]:
    """Read one SIP message off a TCP socket. Returns (status_or_None,
    bytes_read). Mirrors _read_http_message — same framing model
    (headers terminator + Content-Length), just different status-line
    syntax (`SIP/2.0 200 OK` vs `HTTP/1.1 200 OK`)."""
    buf = bytearray()
    header_end = -1
    while header_end < 0 and len(buf) < max_bytes:
        try:
            chunk = sock.recv(8192)
        except socket.timeout:
            break
        if not chunk:
            break
        buf.extend(chunk)
        header_end = buf.find(b"\r\n\r\n")
    if not buf:
        return None, 0

    status: Optional[int] = None
    first_line_end = buf.find(b"\r\n")
    if first_line_end < 0:
        first_line_end = min(len(buf), 200)
    try:
        first_line = bytes(buf[:first_line_end])
        if first_line.startswith(b"SIP/2.0 "):
            parts = first_line.split(b" ", 2)
            if len(parts) >= 2:
                status = int(parts[1])
    except Exception:
        status = None

    content_length = None
    if header_end > 0:
        try:
            headers_blob = bytes(buf[first_line_end + 2:header_end])
            for hline in headers_blob.split(b"\r\n"):
                if hline.lower().startswith(b"content-length:"):
                    content_length = int(hline.split(b":", 1)[1].strip())
                    break
        except Exception:
            content_length = None

    body_start = header_end + 4 if header_end >= 0 else len(buf)
    if content_length is not None:
        needed = body_start + content_length
        while len(buf) < needed and len(buf) < max_bytes:
            try:
                chunk = sock.recv(min(8192, needed - len(buf)))
            except socket.timeout:
                break
            if not chunk:
                break
            buf.extend(chunk)
    return status, len(buf)


def _build_http_response(payload_bytes: int) -> bytes:
    body = b"N" * max(0, payload_bytes)
    return (
        f"HTTP/1.1 200 OK\r\n"
        f"Server: netgen-stateful-tcp/1\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii") + body


# ---------------------------------------------------------------- client


def start_client(
    dst_ip: str,
    dst_port: int,
    *,
    src_ip: Optional[str] = None,
    vrf: Optional[str] = None,
    duration_s: float = 30.0,
    payload_bytes: int = 1024,
    concurrency: int = 1,
    connect_timeout_s: float = 5.0,
    interval_s: float = 0.0,
    expect_echo: bool = True,
    protocol: str = "raw",          # "raw" | "http" | "dns" | "sip"
    tls: bool = False,
    tls_verify: bool = False,
    tls_server_hostname: Optional[str] = None,
    dns_qname: Optional[str] = None,
    sip_host: Optional[str] = None,
    sip_user: Optional[str] = None,
) -> str:
    """Spawn a stateful TCP client session. Returns the session_id (a
    UUID4 string). Use `get_session()` to poll stats and `stop_session()`
    to terminate before the duration elapses.

    Args:
      dst_ip / dst_port    – target.
      src_ip               – optional source-IP bind (must be a local IPv4).
      vrf                  – Linux interface name for SO_BINDTODEVICE
                             (typically a per-device VRF or master link).
      duration_s           – total run time before the worker exits.
      payload_bytes        – bytes sent per connection (or body size in HTTP).
      concurrency          – number of parallel sender threads.
      connect_timeout_s    – per-connection connect() timeout.
      interval_s           – sleep between connections per sender.
      expect_echo          – when True, recv() the echo and count it.
      protocol             – "raw" sends N bytes; "http" frames as HTTP/1.1.
      tls                  – when True, wrap the connected socket in TLS.
      tls_verify           – verify the server cert + hostname. Default
                             False because test environments use self-
                             signed certs.
      tls_server_hostname  – SNI hint; defaults to dst_ip.
    """
    sid = str(uuid.uuid4())
    config = {
        "dst_ip": dst_ip,
        "dst_port": int(dst_port),
        "src_ip": src_ip,
        "vrf": vrf,
        "duration_s": float(duration_s),
        "payload_bytes": int(payload_bytes),
        "concurrency": int(concurrency),
        "connect_timeout_s": float(connect_timeout_s),
        "interval_s": float(interval_s),
        "expect_echo": bool(expect_echo),
        "protocol": str(protocol).lower(),
        "tls": bool(tls),
        "tls_verify": bool(tls_verify),
        "tls_server_hostname": tls_server_hostname or dst_ip,
        "dns_qname": dns_qname or "netgen.test",
        "sip_host": sip_host or dst_ip,
        "sip_user": sip_user or "netgen",
    }
    sess = _Session(session_id=sid, role="client", config=config)

    # Build TLS context once per session — saves per-conn overhead.
    tls_ctx: Optional[ssl.SSLContext] = None
    if config["tls"]:
        tls_ctx = ssl.create_default_context()
        if not config["tls_verify"]:
            tls_ctx.check_hostname = False
            tls_ctx.verify_mode = ssl.CERT_NONE

    def _record_kernel_stats(sock: socket.socket):
        """Pull TCP_INFO once just before the connection is closed.
        Linux-only — silently skips on macOS/Windows."""
        info = _read_tcp_info(sock)
        if not info:
            return
        with sess.lock:
            sess.counters.retransmits_total += int(info.get("total_retrans", 0))
            rtt_us = int(info.get("rtt_us", 0))
            if rtt_us > 0:
                sess.counters.kernel_rtt_us_total += rtt_us
                sess.counters.kernel_rtt_samples += 1

    def _sender_thread():
        proto = config["protocol"]
        payload_n = config["payload_bytes"]
        raw_payload = (b"N" * payload_n) if payload_n > 0 else b""
        deadline = time.time() + config["duration_s"]

        while not sess.stop_evt.is_set() and time.time() < deadline:
            t_attempt = time.time()
            with sess.lock:
                sess.counters.conns_attempted += 1
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(config["connect_timeout_s"])

                # VRF (SO_BINDTODEVICE) — must be set BEFORE bind/connect.
                vrf_err = _bind_to_vrf(sock, config.get("vrf") or "")
                if vrf_err:
                    with sess.lock:
                        sess.counters.last_error = vrf_err

                if config["src_ip"]:
                    try:
                        sock.bind((config["src_ip"], 0))
                    except OSError as bind_err:
                        with sess.lock:
                            sess.counters.last_error = (
                                f"bind {config['src_ip']}: {bind_err}"
                            )
                            sess.counters.conns_failed += 1
                        # Respect stop_evt during backoff.
                        if sess.stop_evt.wait(0.1):
                            break
                        continue

                t_connect = time.time()
                sock.connect((config["dst_ip"], config["dst_port"]))
                hs_ms = (time.time() - t_connect) * 1000.0
                with sess.lock:
                    sess.counters.conns_established += 1
                    sess.counters.handshake_total_ms += hs_ms

                # Optional TLS handshake on top of the TCP handshake.
                if tls_ctx is not None:
                    sock = tls_ctx.wrap_socket(
                        sock, server_hostname=config["tls_server_hostname"],
                    )

                # Send + recv.
                if proto == "http":
                    request_bytes = _build_http_get(
                        config["tls_server_hostname"], payload_n,
                    )
                    sock.sendall(request_bytes)
                    with sess.lock:
                        sess.counters.bytes_tx += len(request_bytes)
                    if config["expect_echo"]:
                        status, n_read = _read_http_response(sock)
                        rtt_ms = (time.time() - t_attempt) * 1000.0
                        with sess.lock:
                            sess.counters.bytes_rx += n_read
                            sess.counters.rtt_total_ms += rtt_ms
                            sess.counters.rtt_samples += 1
                            if status and 200 <= status < 300:
                                sess.counters.http_status_2xx += 1
                            elif status is not None:
                                sess.counters.http_status_other += 1
                elif proto == "dns":
                    # DNS-over-TCP (RFC 7766). Build a query, send,
                    # wait for the framed response, bin the rcode.
                    qname = config.get("dns_qname") or "netgen.test"
                    request_bytes = _build_dns_query(qname, payload_n)
                    sock.sendall(request_bytes)
                    with sess.lock:
                        sess.counters.bytes_tx += len(request_bytes)
                    if config["expect_echo"]:
                        rcode, _raw, n_read = _read_dns_message(sock)
                        rtt_ms = (time.time() - t_attempt) * 1000.0
                        with sess.lock:
                            sess.counters.bytes_rx += n_read
                            sess.counters.rtt_total_ms += rtt_ms
                            sess.counters.rtt_samples += 1
                            if rcode == 0:
                                sess.counters.dns_noerror += 1
                            elif rcode == 3:
                                sess.counters.dns_nxdomain += 1
                            elif rcode == 2:
                                sess.counters.dns_servfail += 1
                            elif rcode is not None:
                                sess.counters.dns_other += 1
                                sess.counters.last_error = f"dns rcode={rcode}"
                elif proto == "sip":
                    # SIP-over-TCP (RFC 3261). Send a REGISTER, parse
                    # the status code, bin per response class. Useful
                    # for testing SBCs and SIP registrars under load.
                    host = config.get("sip_host") or config["tls_server_hostname"]
                    user = config.get("sip_user") or "netgen"
                    request_bytes = _build_sip_register(host, user, payload_n)
                    sock.sendall(request_bytes)
                    with sess.lock:
                        sess.counters.bytes_tx += len(request_bytes)
                    if config["expect_echo"]:
                        status, n_read = _read_sip_message(sock)
                        rtt_ms = (time.time() - t_attempt) * 1000.0
                        with sess.lock:
                            sess.counters.bytes_rx += n_read
                            sess.counters.rtt_total_ms += rtt_ms
                            sess.counters.rtt_samples += 1
                            if status is None:
                                sess.counters.sip_other += 1
                            elif 200 <= status < 300:
                                sess.counters.sip_2xx += 1
                            elif 300 <= status < 400:
                                sess.counters.sip_3xx += 1
                            elif 400 <= status < 500:
                                sess.counters.sip_4xx += 1
                                sess.counters.last_error = f"sip {status}"
                            elif 500 <= status < 600:
                                sess.counters.sip_5xx += 1
                                sess.counters.last_error = f"sip {status}"
                            else:
                                sess.counters.sip_other += 1
                else:
                    if raw_payload:
                        sock.sendall(raw_payload)
                        with sess.lock:
                            sess.counters.bytes_tx += len(raw_payload)
                    if config["expect_echo"]:
                        sock.settimeout(config["connect_timeout_s"])
                        got = 0
                        while got < len(raw_payload):
                            try:
                                chunk = sock.recv(8192)
                            except socket.timeout:
                                break
                            if not chunk:
                                break
                            got += len(chunk)
                        rtt_ms = (time.time() - t_attempt) * 1000.0
                        with sess.lock:
                            sess.counters.bytes_rx += got
                            sess.counters.rtt_total_ms += rtt_ms
                            sess.counters.rtt_samples += 1

                # Kernel stats just before close. ssl.SSLSocket
                # delegates getsockopt to the underlying TCP socket,
                # so passing the wrapper directly works.
                try:
                    _record_kernel_stats(sock)
                except Exception:
                    pass

            except Exception as exc:
                with sess.lock:
                    sess.counters.conns_failed += 1
                    sess.counters.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    try:
                        sock.close()
                    except Exception:
                        pass
            if config["interval_s"] > 0 and not sess.stop_evt.is_set():
                if sess.stop_evt.wait(config["interval_s"]):
                    break
        with sess.lock:
            sess.counters.stopped_at = time.time()

    def _supervisor():
        # Run `concurrency` sender threads in parallel; join them all
        # before the supervisor exits.
        workers = []
        for _ in range(max(1, config["concurrency"])):
            t = threading.Thread(target=_sender_thread, daemon=True)
            t.start()
            workers.append(t)
        for t in workers:
            t.join()

    sess.thread = threading.Thread(target=_supervisor, daemon=True,
                                   name=f"tcp-client-{sid[:8]}")
    sess.thread.start()
    with _REG_LOCK:
        _SESSIONS[sid] = sess
    logger.info(
        f"[STATEFUL TCP] client started session={sid} "
        f"target={dst_ip}:{dst_port} proto={config['protocol']} "
        f"tls={config['tls']} vrf={config.get('vrf')}"
    )
    return sid


# ---------------------------------------------------------------- server


def start_server(
    listen_port: int,
    *,
    listen_ip: str = "0.0.0.0",
    vrf: Optional[str] = None,
    mode: str = "echo",          # "echo" | "discard"
    protocol: str = "raw",       # "raw" | "http" | "dns" | "sip"
    response_bytes: int = 1024,  # HTTP body size when protocol="http"
    backlog: int = 128,
    tls: bool = False,
    tls_cert: Optional[str] = None,
    tls_key: Optional[str] = None,
    dns_response_rcode: int = 3,        # NXDOMAIN; 0=NOERROR for "everything resolves"
    sip_response_status: int = 200,     # SIP status code the registrar simulator returns
    sip_response_reason: str = "OK",
) -> str:
    """Spawn a stateful TCP server session.

    `mode="echo"` reads the full client message and writes it back —
    pairs with the client's `expect_echo=True`. `mode="discard"` just
    drains and closes; useful when the client is measuring tx
    throughput in isolation. When `protocol="http"`, the server
    answers each request with a fixed 200 OK regardless of `mode`.

    TLS: when `tls=True`, both `tls_cert` and `tls_key` (paths) are
    required; the server wraps each accepted connection in a TLS
    SSLContext built once at start.
    """
    sid = str(uuid.uuid4())
    config = {
        "listen_ip": listen_ip,
        "listen_port": int(listen_port),
        "vrf": vrf,
        "mode": mode,
        "protocol": str(protocol).lower(),
        "response_bytes": int(response_bytes),
        "backlog": int(backlog),
        "tls": bool(tls),
        "tls_cert": tls_cert,
        "tls_key": tls_key,
        "dns_response_rcode": int(dns_response_rcode),
        "sip_response_status": int(sip_response_status),
        "sip_response_reason": str(sip_response_reason),
    }
    sess = _Session(session_id=sid, role="server", config=config)

    tls_ctx: Optional[ssl.SSLContext] = None
    if config["tls"]:
        if not tls_cert or not tls_key:
            raise ValueError("tls=True requires both tls_cert and tls_key paths")
        tls_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls_ctx.load_cert_chain(certfile=tls_cert, keyfile=tls_key)

    def _handle_one(conn: socket.socket, addr):
        try:
            conn.settimeout(10.0)
            if config["protocol"] == "http":
                # Drain client request (the parser is tolerant — it
                # just reads until close/eof or it has enough bytes),
                # then write the canned 200 OK.
                _status, n_read = _read_http_response(conn)
                resp = _build_http_response(config["response_bytes"])
                conn.sendall(resp)
                with sess.lock:
                    sess.counters.bytes_rx += n_read
                    sess.counters.bytes_tx += len(resp)
            elif config["protocol"] == "dns":
                # DNS-over-TCP server: respond to each framed query
                # with the configured rcode. Loops so a single
                # connection can carry multiple queries (RFC 7766
                # §6.2.1 — connection reuse).
                rcode = int(config.get("dns_response_rcode", 3))  # NXDOMAIN default
                while True:
                    if sess.stop_evt.is_set():
                        break
                    rcode_in, raw, n_read = _read_dns_message(conn)
                    if n_read == 0:
                        break  # peer closed
                    with sess.lock:
                        sess.counters.bytes_rx += n_read
                    if rcode_in is None:
                        # Malformed query — keep the connection up but
                        # don't respond, mimicking dnsmasq.
                        continue
                    resp = _build_dns_response(raw, rcode=rcode)
                    conn.sendall(resp)
                    with sess.lock:
                        sess.counters.bytes_tx += len(resp)
            elif config["protocol"] == "sip":
                # SIP-over-TCP registrar simulator. Returns the
                # configured status/reason for every parseable
                # REGISTER. Use status=200/OK for success-path testing,
                # 401/Unauthorized to drive auth-retry flows, 503 to
                # simulate registrar overload.
                status = int(config.get("sip_response_status", 200))
                reason = str(config.get("sip_response_reason", "OK"))
                # Read until we get a complete message; loop until peer
                # closes for connection-reuse support.
                buf = bytearray()
                while True:
                    if sess.stop_evt.is_set():
                        break
                    try:
                        chunk = conn.recv(8192)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf.extend(chunk)
                    with sess.lock:
                        sess.counters.bytes_rx += len(chunk)
                    # Parse messages out of buf as they complete.
                    while True:
                        head_end = buf.find(b"\r\n\r\n")
                        if head_end < 0:
                            break
                        # Compute content-length to know body extent.
                        clen = 0
                        try:
                            hdrs = bytes(buf[:head_end])
                            for ln in hdrs.split(b"\r\n"):
                                if ln.lower().startswith(b"content-length:"):
                                    clen = int(ln.split(b":", 1)[1].strip())
                                    break
                        except Exception:
                            clen = 0
                        msg_end = head_end + 4 + clen
                        if len(buf) < msg_end:
                            break  # body not fully arrived yet
                        request_msg = bytes(buf[:msg_end])
                        del buf[:msg_end]
                        resp = _build_sip_response(request_msg, status=status, reason=reason)
                        conn.sendall(resp)
                        with sess.lock:
                            sess.counters.bytes_tx += len(resp)
            else:
                while True:
                    if sess.stop_evt.is_set():
                        break
                    try:
                        chunk = conn.recv(8192)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    with sess.lock:
                        sess.counters.bytes_rx += len(chunk)
                    if config["mode"] == "echo":
                        conn.sendall(chunk)
                        with sess.lock:
                            sess.counters.bytes_tx += len(chunk)
        except Exception as exc:
            with sess.lock:
                sess.counters.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def _accept_loop():
        srv = None
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # VRF bind before listen, just like the client.
            vrf_err = _bind_to_vrf(srv, config.get("vrf") or "")
            if vrf_err:
                with sess.lock:
                    sess.counters.last_error = vrf_err
            srv.bind((config["listen_ip"], config["listen_port"]))
            srv.listen(config["backlog"])
            srv.settimeout(0.5)
            sess.sock = srv
            while not sess.stop_evt.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    # Socket closed by stop_session(); bail cleanly.
                    break
                with sess.lock:
                    sess.counters.conns_attempted += 1
                # TLS-wrap *after* accept so handshake failures are
                # per-connection, not fatal to the listener.
                if tls_ctx is not None:
                    try:
                        conn = tls_ctx.wrap_socket(conn, server_side=True)
                    except Exception as exc:
                        with sess.lock:
                            sess.counters.conns_failed += 1
                            sess.counters.last_error = (
                                f"TLS handshake: {type(exc).__name__}: {exc}"
                            )
                        try:
                            conn.close()
                        except Exception:
                            pass
                        continue
                with sess.lock:
                    sess.counters.conns_established += 1
                h = threading.Thread(
                    target=_handle_one, args=(conn, addr), daemon=True,
                )
                with sess.handlers_lock:
                    # Prune finished handlers so this list doesn't grow
                    # unbounded over long-running servers.
                    sess.handlers = [t for t in sess.handlers if t.is_alive()]
                    sess.handlers.append(h)
                h.start()
        except Exception as exc:
            with sess.lock:
                sess.counters.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if srv:
                    srv.close()
            except Exception:
                pass
            with sess.lock:
                sess.counters.stopped_at = time.time()

    sess.thread = threading.Thread(target=_accept_loop, daemon=True,
                                   name=f"tcp-server-{sid[:8]}")
    sess.thread.start()
    with _REG_LOCK:
        _SESSIONS[sid] = sess
    logger.info(
        f"[STATEFUL TCP] server started session={sid} "
        f"{listen_ip}:{listen_port} proto={config['protocol']} "
        f"tls={config['tls']} vrf={config.get('vrf')}"
    )
    return sid
