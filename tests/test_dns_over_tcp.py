"""End-to-end smoke test for the stateful-TCP DNS-over-TCP protocol.

Spins up a real DNS-over-TCP server on loopback and a real client at
it, asserting the rcode counters land in the right buckets. Tests the
RFC 7766 length-prefix framing too: if the framing were broken, the
server would hang waiting for more bytes and the client's expect_echo
recv would time out → conns_established=1 but http_*=0 / dns_*=0.
"""

import socket
import time

import pytest

from utils import stateful_tcp


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_dns_nxdomain_counters_increment():
    """Default server answers NXDOMAIN (rcode=3). Client should tally
    every response in `dns_nxdomain` and zero in the other buckets."""
    port = _free_port()
    srv = stateful_tcp.start_server(
        listen_port=port, listen_ip="127.0.0.1",
        protocol="dns",
        dns_response_rcode=3,   # NXDOMAIN
    )
    try:
        time.sleep(0.1)
        cli = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            protocol="dns",
            duration_s=0.6, payload_bytes=0, concurrency=1,
            interval_s=0.02,  # throttle vs ephemeral-port exhaustion
        )
        time.sleep(1.0)
        snap = stateful_tcp.get_session(cli)
        cc = snap["counters"]
        assert cc["conns_established"] >= 1, cc
        # The whole point: rcode 3 lands in dns_nxdomain, not other buckets.
        assert cc["dns_nxdomain"] >= 1, cc
        assert cc["dns_noerror"] == 0, cc
        assert cc["dns_servfail"] == 0, cc
        # Bytes flowed both ways (length prefix + 12 header + qname + qtype/class).
        assert cc["bytes_tx"] > 0 and cc["bytes_rx"] > 0
        stateful_tcp.stop_session(cli)
    finally:
        stateful_tcp.stop_session(srv)


def test_dns_noerror_when_server_configured():
    """Configure the server with rcode=0 (NOERROR) and verify the
    client tallies in `dns_noerror`. Confirms the rcode propagates
    through the wire format intact, not just defaulting to 3."""
    port = _free_port()
    srv = stateful_tcp.start_server(
        listen_port=port, listen_ip="127.0.0.1",
        protocol="dns",
        dns_response_rcode=0,   # NOERROR
    )
    try:
        time.sleep(0.1)
        cli = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            protocol="dns",
            duration_s=0.6, concurrency=1,
            interval_s=0.02,  # throttle vs ephemeral-port exhaustion
        )
        time.sleep(1.0)
        cc = stateful_tcp.get_session(cli)["counters"]
        assert cc["dns_noerror"] >= 1, cc
        assert cc["dns_nxdomain"] == 0, cc
        stateful_tcp.stop_session(cli)
    finally:
        stateful_tcp.stop_session(srv)


def test_dns_query_builder_framing():
    """Direct unit test on the wire-format builder — catches regressions
    in the length-prefix / header packing without spinning up sockets."""
    from utils.stateful_tcp import _build_dns_query
    msg = _build_dns_query("example.com")
    # 2-byte length prefix
    msg_len = int.from_bytes(msg[:2], "big")
    assert msg_len == len(msg) - 2, "length-prefix must equal body length"
    # 12-byte DNS header
    assert len(msg) >= 2 + 12 + len(b"\x07example\x03com\x00") + 4
    # Question section: 7 'example' 3 'com' 0
    assert b"\x07example\x03com\x00" in msg
    # qtype=A (1), qclass=IN (1) at the end
    assert msg[-4:] == b"\x00\x01\x00\x01"
