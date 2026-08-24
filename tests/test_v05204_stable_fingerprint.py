"""v0.5.204: machine_fingerprint stops mixing in the active-NIC
MAC — that's what caused paid JWTs to be silently invalidated
on every VPN toggle / WiFi switch / Ethernet plug on macOS.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: paid JWT bound
to `7c1e1671...` at activation; after a network state change,
`machine_fingerprint()` returned `54f7e766...` and the client
kept dropping to the activation screen. `~/.netgen/license.jwt`
was still on disk, still readable, still had a valid signature
— but the fingerprint-bind check treated it as "wrong machine"
and rejected it.

Fixes:

  1. Drop `_stable_mac()` from `machine_fingerprint()` inputs.
     Mix hostname + platform + persistent salt only. Salt was
     already load-bearing (16 random bytes in
     `~/.netgen/fingerprint.salt`), so uniqueness across
     installs is preserved without depending on network state.

  2. Add `_legacy_machine_fingerprint()` that preserves the
     old MAC-inclusive algorithm — verify_jwt accepts EITHER,
     so pre-v0.5.204 JWTs still work while the current MAC
     matches, and operators have time to re-issue against the
     new stable fingerprint without a hard cut-over.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05204_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# fingerprint stability
# ─────────────────────────────────────────────────────────────────────

def test_fingerprint_stable_across_mac_changes():
    """Simulating a VPN toggle: `_stable_mac` returns two
    different values. Old algo would flip fingerprints; new
    algo returns the same one both times."""
    from utils import license as lic

    with patch.object(lic, "_stable_mac", return_value="aabbccddeeff"):
        fp_wifi = lic.machine_fingerprint()
    with patch.object(lic, "_stable_mac", return_value="112233445566"):
        fp_ethernet = lic.machine_fingerprint()

    assert fp_wifi == fp_ethernet, (
        "machine_fingerprint drifted when _stable_mac changed — "
        "v0.5.204 was supposed to remove MAC from the inputs."
    )


def test_legacy_fingerprint_still_includes_mac():
    """The legacy variant is what verify_jwt uses to keep pre-
    v0.5.204 paid JWTs working. It must still mix in
    `_stable_mac`."""
    from utils import license as lic

    with patch.object(lic, "_stable_mac", return_value="aabbccddeeff"):
        fp_a = lic._legacy_machine_fingerprint()
    with patch.object(lic, "_stable_mac", return_value="112233445566"):
        fp_b = lic._legacy_machine_fingerprint()

    assert fp_a != fp_b, (
        "_legacy_machine_fingerprint no longer varies with the MAC — "
        "verify_jwt loses its backward-compat rung."
    )


def test_fingerprint_still_unique_per_install():
    """Salt is still the load-bearing piece — different salt =
    different fingerprint. Guards against a future refactor
    that drops the salt too and makes everyone with the same
    hostname share a fingerprint."""
    from utils import license as lic

    with patch.object(lic, "_persistent_salt", return_value="salt-A"):
        fp_a = lic.machine_fingerprint()
    with patch.object(lic, "_persistent_salt", return_value="salt-B"):
        fp_b = lic.machine_fingerprint()

    assert fp_a != fp_b, (
        "machine_fingerprint doesn't vary with the persistent salt — "
        "two installs on similar-hostname machines would collide."
    )


# ─────────────────────────────────────────────────────────────────────
# verify_jwt dual-fingerprint accept
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def rsa_keypair(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from utils import license as lic

    priv = rsa.generate_private_key(
        public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    pub_path = tmp_path / "tlink-public.pem"
    pub_path.write_text(pub_pem)
    monkeypatch.setenv(lic._BUNDLED_PUBKEY_ENV, str(pub_path))
    return priv_pem


def _mint(priv_pem, **overrides):
    import datetime as _dt
    import jwt
    now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    payload = {
        "typ": "offline", "sub": "1", "email": "u@example.com",
        "license_id": "1", "product_code": "netgen",
        "license_type": "INDIVIDUAL", "billing_type": "PAID",
        "start_date": "2026-01-01", "end_date": "2027-12-31",
        "device_fingerprint_hash": None,
        "iat": now, "exp": now + 30 * 86400, "jti": "t",
    }
    payload.update(overrides)
    return jwt.encode(payload, priv_pem, algorithm="RS256")


def test_verify_accepts_jwt_bound_to_new_fingerprint(rsa_keypair):
    from utils import license as lic
    tok = _mint(rsa_keypair, device_fingerprint_hash=lic.machine_fingerprint())
    result = lic.verify_jwt(tok)
    assert result.is_valid, result.reason


def test_verify_accepts_jwt_bound_to_legacy_fingerprint(rsa_keypair):
    """The whole point of v0.5.204's dual accept — a JWT minted
    before the algorithm change still verifies as long as the
    legacy fingerprint still matches."""
    from utils import license as lic
    tok = _mint(rsa_keypair, device_fingerprint_hash=lic._legacy_machine_fingerprint())
    result = lic.verify_jwt(tok)
    assert result.is_valid, result.reason


def test_verify_rejects_jwt_bound_to_random_fingerprint(rsa_keypair):
    from utils import license as lic
    tok = _mint(rsa_keypair, device_fingerprint_hash="0" * 64)
    result = lic.verify_jwt(tok)
    assert not result.is_valid
    assert "different machine" in result.reason.lower() or \
           "fingerprint" in result.reason.lower()
