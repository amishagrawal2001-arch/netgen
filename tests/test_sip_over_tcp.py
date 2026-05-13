"""End-to-end smoke tests for the stateful-TCP SIP-over-TCP protocol.

Spins up a real SIP-over-TCP registrar simulator on loopback and
hammers it with a client, asserting status-code counters bin correctly.
Mirrors the structure of test_dns_over_tcp.py so a future operator
adding RADIUS / DIAMETER can clone the pattern.
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


def test_sip_register_2xx_counters_increment():
    """Default registrar simulator answers 200 OK. Every REGISTER
    should land in `sip_2xx`, nothing in other classes."""
    port = _free_port()
    srv = stateful_tcp.start_server(
        listen_port=port, listen_ip="127.0.0.1",
        protocol="sip",
        sip_response_status=200,
        sip_response_reason="OK",
    )
    try:
        time.sleep(0.1)
        cli = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            protocol="sip",
            duration_s=0.6, concurrency=1,
        )
        time.sleep(1.0)
        cc = stateful_tcp.get_session(cli)["counters"]
        assert cc["conns_established"] >= 1, cc
        assert cc["sip_2xx"] >= 1, cc
        assert cc["sip_4xx"] == 0, cc
        assert cc["sip_5xx"] == 0, cc
        assert cc["sip_other"] == 0, cc
        assert cc["bytes_tx"] > 0 and cc["bytes_rx"] > 0
        stateful_tcp.stop_session(cli)
    finally:
        stateful_tcp.stop_session(srv)


def test_sip_register_401_lands_in_4xx_bucket():
    """Configure the registrar to return 401 Unauthorized. The client
    must bin every response in `sip_4xx`, not 2xx.

    Note: we throttle the client with `interval_s` so we don't exhaust
    the kernel's ephemeral-port range — back-to-back loopback connects
    each leave a TIME_WAIT socket, and macOS's default range is small
    enough that an unthrottled 0.6s burst can blow through it. The
    counter check (sip_4xx >= 1) is the canonical assertion; we don't
    assert on `last_error` because that single slot gets overwritten
    by any later connect failure (port exhaustion looks like a SIP
    error to the operator-facing field)."""
    port = _free_port()
    srv = stateful_tcp.start_server(
        listen_port=port, listen_ip="127.0.0.1",
        protocol="sip",
        sip_response_status=401,
        sip_response_reason="Unauthorized",
    )
    try:
        time.sleep(0.1)
        cli = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            protocol="sip",
            duration_s=0.6, concurrency=1,
            interval_s=0.02,    # throttle to ~50/s; well under TW reuse window
        )
        time.sleep(1.0)
        cc = stateful_tcp.get_session(cli)["counters"]
        assert cc["sip_4xx"] >= 1, cc
        assert cc["sip_2xx"] == 0, cc
        stateful_tcp.stop_session(cli)
    finally:
        stateful_tcp.stop_session(srv)


def test_sip_register_builder_format():
    """Direct unit test on the wire-format builder: SIP REGISTER must
    have a SIP/2.0 request line, CRLF line terminators, the canonical
    headers, and a Content-Length that matches the body."""
    from utils.stateful_tcp import _build_sip_register
    msg = _build_sip_register("registrar.example.com", payload_bytes=128)
    assert msg.startswith(b"REGISTER sip:registrar.example.com SIP/2.0\r\n")
    assert b"\r\n\r\n" in msg, "headers must end with CRLF CRLF"
    # Body of 128 bytes per Content-Length: 128
    assert b"Content-Length: 128\r\n" in msg
    head_end = msg.find(b"\r\n\r\n")
    body = msg[head_end + 4:]
    assert len(body) == 128
    # The required SIP/RFC 3261 headers
    for header in (b"Via:", b"From:", b"To:", b"Call-ID:", b"CSeq:"):
        assert header in msg, f"missing header {header!r}"


def test_sip_response_mirrors_request_headers():
    """The server builds responses by mirroring Via/From/To/Call-ID/CSeq
    from the request — clients require that for transaction matching
    per RFC 3261 §8.2.6.2. Verify by feeding a request through the
    builder and checking the response carries the same headers."""
    from utils.stateful_tcp import _build_sip_register, _build_sip_response
    req = _build_sip_register("registrar.example.com")
    resp = _build_sip_response(req, status=200, reason="OK")
    assert resp.startswith(b"SIP/2.0 200 OK\r\n")
    # Every mirrored header should appear in the response.
    for header in (b"Via:", b"From:", b"To:", b"Call-ID:", b"CSeq:"):
        assert header in resp, f"response missing mirrored {header!r}"
