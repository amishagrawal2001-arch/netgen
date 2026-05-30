"""Smoke tests for the stateful-TCP worker.

These run real listeners on 127.0.0.1 and connect to them, so they
genuinely exercise the kernel TCP stack (plus TLS and HTTP framing
in the new tests) — but they only touch loopback and short-lived
ephemeral ports, so they're safe in CI.

Run with: pytest -v tests/test_stateful_tcp.py
"""

import os
import socket
import ssl
import sys
import tempfile
import time

import pytest

from utils import stateful_tcp


def _free_port() -> int:
    """Grab an ephemeral port the OS just freed up — race-prone in
    theory, fine in practice for a single-test loopback bind."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_echo_round_trip_counts_bytes_both_ways():
    """Spin up an echo server, fire a single short-duration client at
    it, and assert that bytes_tx == bytes_rx on both sides — the most
    basic check that the handshake-send-recv-close loop actually does
    a full TCP round-trip and the counters are wired."""
    port = _free_port()
    server_id = stateful_tcp.start_server(listen_port=port, listen_ip="127.0.0.1")
    try:
        # Give the listener a beat to bind.
        time.sleep(0.1)

        client_id = stateful_tcp.start_client(
            dst_ip="127.0.0.1",
            dst_port=port,
            duration_s=1.0,
            payload_bytes=128,
            concurrency=1,
            expect_echo=True,
            # Throttle: the default interval_s=0 lets a single loopback
            # client burn through 5000+ connect()s/sec, each leaving a
            # TIME_WAIT, exhausting the macOS ephemeral-port range
            # mid-test (EADDRNOTAVAIL) and poisoning later TCP tests.
            interval_s=0.02,
        )
        # Let the client run its 1s window then a small grace period.
        time.sleep(1.4)

        cs = stateful_tcp.get_session(client_id)
        ss = stateful_tcp.get_session(server_id)

        assert cs is not None, "client session vanished"
        assert ss is not None, "server session vanished"

        cc = cs["counters"]
        sc = ss["counters"]

        # At least one connection completed end-to-end.
        assert cc["conns_established"] >= 1, f"no client conns: {cc}"
        # The client sent bytes, the server received them, the server
        # echoed back, the client received them. Numbers should match.
        assert cc["bytes_tx"] > 0
        assert cc["bytes_rx"] == cc["bytes_tx"], (
            f"echo lost bytes: tx={cc['bytes_tx']} rx={cc['bytes_rx']}"
        )
        assert sc["bytes_rx"] == cc["bytes_tx"]
        assert sc["bytes_tx"] == cc["bytes_rx"]
    finally:
        stateful_tcp.stop_session(client_id)
        stateful_tcp.stop_session(server_id)


def test_stop_session_idempotent():
    """Calling stop on a non-existent session returns False rather
    than raising — the CLI relies on this for clean error messages."""
    assert stateful_tcp.stop_session("does-not-exist") is False


def test_client_against_dead_target_records_failure():
    """When the target isn't listening, every connect() must fail and
    the failure counter has to increment — the operator's only signal
    that 'nothing is talking back' is this counter, so it must move."""
    # 0 = let the kernel pick; but we use a port we just freed and
    # never bind to, so connect() should hit ECONNREFUSED almost
    # immediately on loopback.
    dead_port = _free_port()
    sid = stateful_tcp.start_client(
        dst_ip="127.0.0.1",
        dst_port=dead_port,
        duration_s=0.5,
        payload_bytes=32,
        concurrency=1,
        connect_timeout_s=0.5,
        expect_echo=False,
    )
    try:
        time.sleep(0.9)
        snap = stateful_tcp.get_session(sid)
        cc = snap["counters"]
        assert cc["conns_attempted"] >= 1
        assert cc["conns_established"] == 0
        assert cc["conns_failed"] >= 1
        assert cc["last_error"], "expected last_error to be populated"
    finally:
        stateful_tcp.stop_session(sid)


# ---------------------------------------------------------------- HTTP framing


def test_http_protocol_status_2xx_counter_moves():
    """End-to-end HTTP test: client sends POST framed as HTTP/1.1,
    server replies with a 200 OK, and the http_status_2xx counter
    increments. The actual request/response body is irrelevant — the
    point is that the framing parser locks on to the status line."""
    port = _free_port()
    srv_id = stateful_tcp.start_server(
        listen_port=port, listen_ip="127.0.0.1",
        protocol="http", response_bytes=64,
    )
    try:
        time.sleep(0.1)
        cli_id = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            protocol="http", payload_bytes=32,
            duration_s=1.0, concurrency=1,
            interval_s=0.02,  # throttle vs ephemeral-port exhaustion
        )
        time.sleep(1.5)
        snap = stateful_tcp.get_session(cli_id)
        cc = snap["counters"]
        assert cc["conns_established"] >= 1
        assert cc["http_status_2xx"] >= 1, f"no 2xx tallied: {cc}"
        assert cc["http_status_other"] == 0, f"unexpected non-2xx: {cc}"
        # Some bytes flowed both ways
        assert cc["bytes_tx"] > 0
        assert cc["bytes_rx"] > 0
        stateful_tcp.stop_session(cli_id)
    finally:
        stateful_tcp.stop_session(srv_id)


# ---------------------------------------------------------------- TLS


def _gen_self_signed_cert():
    """Generate a throwaway self-signed cert for the TLS test. Skips
    the test cleanly if `cryptography` isn't installed in this venv —
    we don't want CI to fail just because an optional dep is missing."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime as _dt
    except ImportError:
        pytest.skip("cryptography not available — skipping TLS smoke test")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "netgen-test"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_dt.datetime.now(_dt.timezone.utc))
        .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_fd, cert_path = tempfile.mkstemp(suffix=".pem")
    key_fd, key_path = tempfile.mkstemp(suffix=".pem")
    os.close(cert_fd); os.close(key_fd)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    return cert_path, key_path


def test_tls_handshake_completes_with_self_signed_cert():
    """Spin up a TLS-wrapped echo server with a self-signed cert; the
    client connects with tls_verify=False (test-environment default)
    and must complete the handshake — observable as conns_established
    incrementing on both sides."""
    cert_path, key_path = _gen_self_signed_cert()
    port = _free_port()
    try:
        srv_id = stateful_tcp.start_server(
            listen_port=port, listen_ip="127.0.0.1",
            tls=True, tls_cert=cert_path, tls_key=key_path,
        )
        time.sleep(0.2)
        cli_id = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            tls=True, tls_verify=False,
            duration_s=1.0, payload_bytes=64,
            # Throttle: preceding tests in the suite can leave the
            # kernel ephemeral-port range exhausted by TIME_WAITs.
            # Without this, the test sees OSError "can't assign
            # requested address" on macOS hosts.
            interval_s=0.02,
        )
        time.sleep(1.5)
        cs = stateful_tcp.get_session(cli_id)
        ss = stateful_tcp.get_session(srv_id)
        assert cs["counters"]["conns_established"] >= 1, cs["counters"]
        assert ss["counters"]["conns_established"] >= 1, ss["counters"]
        # Echo round-trip should still net out
        assert cs["counters"]["bytes_rx"] == cs["counters"]["bytes_tx"]
        stateful_tcp.stop_session(cli_id)
        stateful_tcp.stop_session(srv_id)
    finally:
        for p in (cert_path, key_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ---------------------------------------------------------------- VRF


def test_vrf_bind_no_op_on_non_linux():
    """On macOS / Windows, asking for `vrf="anything"` must NOT crash
    the session. The bind helper returns a warning string the worker
    stashes on last_error, but conns_established must still climb."""
    if sys.platform.startswith("linux") and os.geteuid() == 0:
        pytest.skip("on Linux as root SO_BINDTODEVICE actually applies — "
                    "this test only proves graceful no-op on other hosts")

    port = _free_port()
    srv_id = stateful_tcp.start_server(listen_port=port, listen_ip="127.0.0.1")
    try:
        time.sleep(0.1)
        cli_id = stateful_tcp.start_client(
            dst_ip="127.0.0.1", dst_port=port,
            vrf="this-vrf-does-not-exist",
            duration_s=0.6, payload_bytes=32,
            interval_s=0.02,  # throttle vs ephemeral-port exhaustion
        )
        time.sleep(1.0)
        cs = stateful_tcp.get_session(cli_id)
        cc = cs["counters"]
        # The bind warning lands in last_error, BUT traffic still flows.
        assert cc["conns_established"] >= 1, cc
        # On non-Linux the helper emits the "ignored" message; we
        # don't assert the exact string because Linux-non-root would
        # legitimately surface a permission-denied warning instead.
        stateful_tcp.stop_session(cli_id)
    finally:
        stateful_tcp.stop_session(srv_id)


def test_read_tcp_info_returns_none_off_linux():
    """The TCP_INFO scraper has to fail soft on non-Linux so callers
    can rely on `if info` without try/except."""
    if sys.platform.startswith("linux"):
        pytest.skip("TCP_INFO is supported on Linux — different invariant")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert stateful_tcp._read_tcp_info(s) is None
    finally:
        s.close()
