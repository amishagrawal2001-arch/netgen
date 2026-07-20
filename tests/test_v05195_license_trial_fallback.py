"""v0.5.195: `load()` must fall through to the trial file when a
present `license.jwt` fails to verify.

Bug: `load()` returned `_maybe_grace(verify_jwt(token))` verbatim
the moment any `license.jwt` existed. So a stale paid-license
file (expired past grace, tampered, bound to a different device
fingerprint) preempted the still-live trial and the client
blocked on the activation dialog after every restart — even
though `~/.netgen/trial.json` was perfectly valid.

Operator report 2026-07-20 on the client host: "even though
license is active for 30 days trial, after restart it is again
asking license activation and does not start the app".

Repro pattern:
  1. Activate a paid JWT (writes `license.jwt`).
  2. JWT expires or the device fingerprint drifts.
  3. Start a trial (writes `trial.json`).
  4. Restart client → activation dialog again.

Fix: the JWT authorises only when its verify comes back
`is_valid`. Otherwise fall through to the trial. Only when both
are unusable do we surface a reason.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05195_test_{os.getpid()}.db"),
)

from utils import license as lic  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def rsa_keypair(tmp_path, monkeypatch):
    """Fresh RSA-2048 keypair; patches the bundled pubkey env var
    so verify_jwt uses this test's public key."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

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
    return {"priv_pem": priv_pem, "pub_path": pub_path}


@pytest.fixture
def isolated_license_dir(tmp_path, monkeypatch):
    """Redirect LICENSE_FILE / TRIAL_FILE / TRIAL_USED_MARKER into
    a scratch dir. The env-var override `NETGEN_LICENSE_FILE`
    covers `_discover_license_source`; TRIAL_FILE gets replaced
    module-wide."""
    lic_file = tmp_path / "license.jwt"
    trial_file = tmp_path / "trial.json"
    marker = tmp_path / "trial-used.marker"
    monkeypatch.setenv(lic._LICENSE_FILE_ENV, str(lic_file))
    monkeypatch.setattr(lic, "LICENSE_FILE", lic_file)
    monkeypatch.setattr(lic, "TRIAL_FILE", trial_file)
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER", marker)
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    return {
        "dir": tmp_path,
        "license_file": lic_file,
        "trial_file": trial_file,
        "marker": marker,
    }


def _mint_token(priv_pem: str, **overrides) -> str:
    import jwt
    now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    payload = {
        "typ": "offline",
        "sub": "42",
        "email": "customer@example.com",
        "license_id": "99",
        "product_code": "netgen",
        "license_type": "INDIVIDUAL",
        "billing_type": "PAID",
        "start_date": "2026-01-01",
        "end_date": "2027-12-31",
        "device_fingerprint_hash": None,
        "iat": now,
        "exp": now + 30 * 86400,
        "jti": "test-token",
    }
    payload.update(overrides)
    return jwt.encode(payload, priv_pem, algorithm="RS256")


def _write_trial(trial_file: Path, days_left: int = 20) -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    doc = {
        "version": 1,
        "started_at": (now - _dt.timedelta(days=30 - days_left)).isoformat(
            timespec="seconds"),
        "expires_at": (now + _dt.timedelta(days=days_left)).isoformat(
            timespec="seconds"),
        "fingerprint": lic.machine_fingerprint(),
    }
    trial_file.write_text(json.dumps(doc, indent=2))


# ─────────────────────────────────────────────────────────────────────
# priority ladder
# ─────────────────────────────────────────────────────────────────────

def test_valid_jwt_wins_over_any_trial(
        rsa_keypair, isolated_license_dir):
    """Paid JWT that verifies should always be the one returned,
    even when a trial.json exists."""
    isolated_license_dir["license_file"].write_text(
        _mint_token(rsa_keypair["priv_pem"]))
    _write_trial(isolated_license_dir["trial_file"], days_left=15)
    result = lic.load()
    assert result.is_valid
    assert result.billing_type == "PAID"
    assert result.reason == "ok"


def test_invalid_jwt_falls_through_to_valid_trial(
        rsa_keypair, isolated_license_dir):
    """THE bug being fixed. An expired paid JWT must not shadow a
    still-live trial. Before v0.5.195 this returned the invalid
    JWT and the activation dialog blocked startup."""
    # Mint an already-past-entitlement JWT.
    expired_token = _mint_token(
        rsa_keypair["priv_pem"],
        end_date="2020-01-15",
    )
    isolated_license_dir["license_file"].write_text(expired_token)
    _write_trial(isolated_license_dir["trial_file"], days_left=15)

    result = lic.load()
    assert result.is_valid, (
        f"expired paid JWT shadowed live trial (reason={result.reason!r})"
    )
    assert result.billing_type == lic.BILLING_TRIAL
    assert result.reason == "ok"


def test_tampered_jwt_falls_through_to_valid_trial(
        rsa_keypair, isolated_license_dir):
    """Signature tamper = same-shape bug: must fall to trial."""
    token = _mint_token(rsa_keypair["priv_pem"])
    tampered = token[:-8] + "AAAAAAAA"
    isolated_license_dir["license_file"].write_text(tampered)
    _write_trial(isolated_license_dir["trial_file"], days_left=10)

    result = lic.load()
    assert result.is_valid
    assert result.billing_type == lic.BILLING_TRIAL


def test_no_jwt_valid_trial_returns_trial(isolated_license_dir):
    """Trial-only user (never activated paid) still authenticates."""
    _write_trial(isolated_license_dir["trial_file"], days_left=30)
    result = lic.load()
    assert result.is_valid
    assert result.billing_type == lic.BILLING_TRIAL


def test_invalid_jwt_expired_trial_returns_jwt_reason(
        rsa_keypair, isolated_license_dir):
    """Neither works → surface the JWT's more-actionable reason
    (e.g. 'entitlement expired 2020-01-15') rather than 'trial
    expired'. Both invalid, but the JWT tells the operator
    something they can act on."""
    expired_token = _mint_token(
        rsa_keypair["priv_pem"],
        end_date="2020-01-15",
    )
    isolated_license_dir["license_file"].write_text(expired_token)
    # Trial started 40 days ago, expired 10 days ago.
    _write_trial(isolated_license_dir["trial_file"], days_left=-10)

    result = lic.load()
    assert not result.is_valid
    assert "2020-01-15" in result.reason or "entitlement" in result.reason


def test_no_jwt_no_trial_returns_no_license(isolated_license_dir):
    result = lic.load()
    assert not result.is_valid
    assert result.reason == "no license"


def test_is_activated_true_when_falling_through_to_trial(
        rsa_keypair, isolated_license_dir):
    """End-to-end: the activation gate calls is_activated().
    Confirm the gate now lets the user in when a stale JWT sits
    next to a live trial."""
    expired_token = _mint_token(
        rsa_keypair["priv_pem"], end_date="2020-01-15")
    isolated_license_dir["license_file"].write_text(expired_token)
    _write_trial(isolated_license_dir["trial_file"], days_left=15)

    assert lic.is_activated() is True
