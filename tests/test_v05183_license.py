"""v0.5.183: client-side license as tlink-license-server JWT client.

Replaces the prior HMAC-key scheme. The client now:
  * verifies RS256 offline-code JWTs (minted by tlink-license-server's
    `mintOfflineCode()` at `src/services/offlineCode.js:53`) against a
    bundled RSA public key at `resources/license/tlink-public.pem`,
  * caches the raw JWT at `~/.netgen/license.jwt`,
  * shows a blocking activation dialog at startup when no valid
    license is loaded (cancel/X exits the app),
  * gates the same four flagship menu items (DPDK Blast, RDMA Blast,
    RDMA Topology, RFC 2544) — collapsed to a single "activated?"
    check since the server has no tier axis.

Fixtures generate a test keypair per test — no external server or
committed private key involved.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils import license as lic  # noqa: E402


# ─────────────── fixtures ───────────────

@pytest.fixture
def rsa_keypair(tmp_path):
    """Fresh RSA-2048 keypair per test + a monkey-patched
    NETGEN_LICENSE_PUBKEY_PATH so `utils.license` reads it."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    priv = rsa.generate_private_key(
        public_exponent=65537, key_size=2048)
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    pub_path = tmp_path / "tlink-public.pem"
    pub_path.write_text(pub_pem)
    import os
    old = os.environ.get(lic._BUNDLED_PUBKEY_ENV)
    os.environ[lic._BUNDLED_PUBKEY_ENV] = str(pub_path)
    try:
        yield {"priv_pem": priv_pem, "pub_pem": pub_pem,
               "pub_path": pub_path}
    finally:
        if old is None:
            os.environ.pop(lic._BUNDLED_PUBKEY_ENV, None)
        else:
            os.environ[lic._BUNDLED_PUBKEY_ENV] = old


def _mint_token(priv_pem: str, **overrides) -> str:
    """Mint an offline JWT with the same schema tlink-license-server
    uses — see `src/services/offlineCode.js:53-85`."""
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


# ─────────────── JWT verification ───────────────


def test_valid_netgen_offline_code_verifies(rsa_keypair):
    token = _mint_token(rsa_keypair["priv_pem"])
    result = lic.verify_jwt(token)
    assert result.is_valid
    assert result.reason == "ok"
    assert result.product_code == "netgen"
    assert result.email == "customer@example.com"
    assert result.billing_type == "PAID"
    assert result.end_date == _dt.date(2027, 12, 31)
    # All four gated features unlock.
    for feat in lic.GATED_FEATURES:
        assert result.is_feature_licensed(feat)


def test_empty_token_rejected_with_no_license_reason(rsa_keypair):
    result = lic.verify_jwt("")
    assert not result.is_valid
    assert result.reason == "no license"


def test_non_jwt_string_rejected(rsa_keypair):
    result = lic.verify_jwt("hello, world")
    assert not result.is_valid
    assert "JWT" in result.reason


def test_tampered_signature_rejected(rsa_keypair):
    token = _mint_token(rsa_keypair["priv_pem"])
    tampered = token[:-8] + "XXXXXXXX"
    result = lic.verify_jwt(tampered)
    assert not result.is_valid
    assert "signature" in result.reason


def test_wrong_product_code_rejected(rsa_keypair):
    """A JWT for tyllink_terminal cannot activate netgen."""
    token = _mint_token(rsa_keypair["priv_pem"],
                        product_code="tyllink_terminal")
    result = lic.verify_jwt(token)
    assert not result.is_valid
    assert "product" in result.reason
    assert "netgen" in result.reason


def test_expired_session_rejected(rsa_keypair):
    """exp is in the past."""
    now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    token = _mint_token(rsa_keypair["priv_pem"],
                        iat=now - 3600, exp=now - 10)
    result = lic.verify_jwt(token)
    assert not result.is_valid
    assert "expired" in result.reason


def test_expired_entitlement_rejected(rsa_keypair):
    """end_date is in the past, even if exp is future."""
    token = _mint_token(rsa_keypair["priv_pem"],
                        end_date="2020-01-15")
    result = lic.verify_jwt(token)
    assert not result.is_valid
    assert "entitlement expired" in result.reason
    assert "2020-01-15" in result.reason


def test_wrong_typ_rejected(rsa_keypair):
    token = _mint_token(rsa_keypair["priv_pem"], typ="access")
    result = lic.verify_jwt(token)
    assert not result.is_valid
    assert "token type" in result.reason


def test_device_fingerprint_mismatch_rejected(rsa_keypair):
    token = _mint_token(
        rsa_keypair["priv_pem"],
        device_fingerprint_hash="0" * 64,
    )
    result = lic.verify_jwt(token)
    assert not result.is_valid
    assert "different machine" in result.reason


def test_device_fingerprint_match_accepted(rsa_keypair):
    local_fp = lic.machine_fingerprint()
    token = _mint_token(
        rsa_keypair["priv_pem"],
        device_fingerprint_hash=local_fp,
    )
    result = lic.verify_jwt(token)
    assert result.is_valid


def test_verify_jwt_with_allow_fingerprint_mismatch(rsa_keypair):
    """Diagnostic path — used by the Help → License Status dialog
    to inspect a JWT bound to a different machine without erroring
    out."""
    token = _mint_token(
        rsa_keypair["priv_pem"],
        device_fingerprint_hash="0" * 64,
    )
    result = lic.verify_jwt(
        token, allow_fingerprint_mismatch=True)
    assert result.is_valid
    assert result.device_fingerprint_hash == "0" * 64


def test_trial_billing_surfaces_note(rsa_keypair):
    token = _mint_token(
        rsa_keypair["priv_pem"], billing_type="TRIAL")
    result = lic.verify_jwt(token)
    assert result.is_valid
    assert result.is_trial()
    assert any("Trial" in n for n in result.notes)


def test_days_until_expiry_prefers_earliest_bound(rsa_keypair):
    """Client should count down to whichever comes first:
    end_date or exp."""
    now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    token = _mint_token(
        rsa_keypair["priv_pem"],
        # end_date 400 days out
        end_date=(_dt.date.today()
                  + _dt.timedelta(days=400)).isoformat(),
        # exp only 10 days out
        exp=now + 10 * 86400,
    )
    result = lic.verify_jwt(token)
    days = result.days_until_expiry()
    assert 9 <= days <= 10


# ─────────────── file load / save ───────────────


def test_load_missing_file_returns_no_license(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    # v0.5.183 trial: also isolate trial paths so a leftover
    # ~/.netgen/trial.json from real use doesn't leak in.
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    result = lic.load()
    assert not result.is_valid
    assert result.reason == "no license"


def test_save_writes_jwt_then_load_round_trips(
        rsa_keypair, tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    token = _mint_token(rsa_keypair["priv_pem"])
    saved = lic.save(token)
    assert saved.is_valid
    reloaded = lic.load()
    assert reloaded.is_valid
    assert reloaded.jwt_token == token


def test_save_normalizes_whitespace(rsa_keypair, tmp_path,
                                    monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    token = _mint_token(rsa_keypair["priv_pem"])
    padded = f"  \n{token}\n  "
    saved = lic.save(padded)
    assert saved.is_valid
    # File on disk has no wrapping whitespace.
    assert (tmp_path / "license.jwt").read_text().strip() == token


def test_remove_deletes_file(rsa_keypair, tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    assert (tmp_path / "license.jwt").exists()
    lic.remove()
    assert not (tmp_path / "license.jwt").exists()
    # Idempotent.
    lic.remove()


# ─────────────── activated?/gate ───────────────


def test_is_activated_true_for_valid(rsa_keypair, tmp_path,
                                     monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    assert lic.is_activated()


def test_is_activated_false_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    assert not lic.is_activated()


def test_non_gated_features_always_licensed():
    """scapy_streams / admin — not in GATED_FEATURES → always ok."""
    result = lic.License()  # no license loaded
    assert result.is_feature_licensed("scapy_streams")
    assert result.is_feature_licensed("admin_console")
    # Sanity: something we do gate is locked.
    assert not result.is_feature_licensed("rdma_blast")


# ─────────────── fingerprint ───────────────


def test_fingerprint_is_64_char_hex():
    fp = lic.machine_fingerprint()
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_stable_across_calls():
    a = lic.machine_fingerprint()
    b = lic.machine_fingerprint()
    assert a == b


# ─────────────── file extraction (Load-file button) ───────────────


def test_extract_jwt_from_raw_text(rsa_keypair):
    token = _mint_token(rsa_keypair["priv_pem"])
    assert lic.extract_jwt_from_text(token) == token
    assert lic.extract_jwt_from_text(f"here you go:\n{token}\n") == token


def test_extract_jwt_from_json_envelope(rsa_keypair):
    token = _mint_token(rsa_keypair["priv_pem"])
    doc = json.dumps({"token": token, "customer": "Acme"})
    assert lic.extract_jwt_from_text(doc) == token
    # Also under `jwt` and `offline_code` keys.
    for k in ("jwt", "offline_code", "license"):
        assert lic.extract_jwt_from_text(
            json.dumps({k: token})) == token


def test_extract_jwt_from_empty_returns_none():
    assert lic.extract_jwt_from_text("") is None
    assert lic.extract_jwt_from_text("nope") is None


# ─────────────── dialogs (headless smoke) ───────────────


def test_activation_dialog_opens_headless():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    dlg = LicenseActivationDialog()
    # UI elements the operator will interact with.
    assert dlg.windowTitle() == "Activate Netgen"
    assert dlg.isModal()
    assert dlg._key_edit.placeholderText().startswith("Paste")
    dlg.close()


def test_activation_dialog_activates_valid_key(
        rsa_keypair, tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    # Suppress the "activated" info box.
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **kw: None)
    token = _mint_token(rsa_keypair["priv_pem"])
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    dlg = LicenseActivationDialog()
    dlg._key_edit.setPlainText(token)
    dlg._on_activate()
    assert (tmp_path / "license.jwt").exists()
    # Loaded license verifies.
    assert lic.is_activated()


def test_activation_dialog_rejects_bad_token(
        rsa_keypair, tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    dlg = LicenseActivationDialog()
    dlg._key_edit.setPlainText(
        "eyJhbGciOiJSUzI1NiJ9.invalid.token")
    dlg._on_activate()
    assert not (tmp_path / "license.jwt").exists()
    assert "reject" in dlg._inline_error.text().lower()


def test_run_activation_gate_short_circuits_when_activated(
        rsa_keypair, tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    from widgets.license_activation_dialog import (
        run_activation_gate,
    )
    # Should return True immediately, no dialog shown.
    assert run_activation_gate() is True


def test_status_dialog_shows_activated_state(
        rsa_keypair, tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    from widgets.license_dialog import LicenseDialog
    dlg = LicenseDialog()
    assert "active" in dlg._status_label.text().lower()
    assert dlg._deactivate_btn.isEnabled()


# ─────────────── trial ───────────────


def test_can_start_trial_true_on_fresh_install(tmp_path,
                                               monkeypatch):
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    assert lic.can_start_trial()


def test_start_trial_persists_and_activates(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    result = lic.start_trial()
    assert result.is_valid
    assert result.billing_type == lic.BILLING_TRIAL
    assert result.days_until_expiry() >= lic.TRIAL_DAYS - 1
    # Files landed on disk.
    assert (tmp_path / "trial.json").exists()
    assert (tmp_path / "trial-used.marker").exists()
    # Loaded via the normal load() path.
    loaded = lic.load()
    assert loaded.is_valid
    assert loaded.billing_type == lic.BILLING_TRIAL
    # And unlocks every gated feature.
    for feat in lic.GATED_FEATURES:
        assert loaded.is_feature_licensed(feat)


def test_start_trial_refuses_after_first_use(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    lic.start_trial()
    # Even after deleting the trial file the marker persists.
    (tmp_path / "trial.json").unlink()
    assert not lic.can_start_trial()
    result = lic.start_trial()
    assert not result.is_valid
    assert "already used" in result.reason


def test_expired_trial_returns_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    # Write an already-expired trial by hand.
    started = _dt.datetime.now(_dt.timezone.utc) \
        - _dt.timedelta(days=60)
    (tmp_path / "trial.json").write_text(json.dumps({
        "version": 1,
        "started_at": started.isoformat(timespec="seconds"),
        "expires_at": (started + _dt.timedelta(
            days=lic.TRIAL_DAYS)).isoformat(timespec="seconds"),
        "fingerprint": lic.machine_fingerprint(),
    }))
    result = lic.load()
    assert not result.is_valid
    assert "trial expired" in result.reason


def test_valid_jwt_takes_priority_over_trial(
        rsa_keypair, tmp_path, monkeypatch):
    """If both a paid JWT and a live trial exist, the JWT wins."""
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    # Trial first.
    lic.start_trial()
    # Now a paid JWT.
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    result = lic.load()
    assert result.is_valid
    assert result.billing_type == "PAID"


def test_activation_dialog_start_trial_button_activates(
        tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **kw: None)
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    dlg = LicenseActivationDialog()
    assert dlg._trial_btn.isEnabled()
    dlg._on_start_trial()
    assert (tmp_path / "trial.json").exists()
    assert lic.is_activated()


def test_activation_dialog_trial_disabled_when_used(
        tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    (tmp_path / "trial-used.marker").touch()
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    dlg = LicenseActivationDialog()
    assert not dlg._trial_btn.isEnabled()
    assert "already been used" in dlg._trial_btn.toolTip()


# ─────────────── source-grep guards ───────────────


def test_run_tgen_client_installs_license_gate():
    src = (REPO / "run_tgen_client.py").read_text()
    assert "run_activation_gate" in src
    # Gate lives BEFORE the main window construction.
    idx_gate = src.index("run_activation_gate")
    idx_window = src.index("TrafficGeneratorClient(")
    assert idx_gate < idx_window, \
        "activation gate must fire before the main window builds"


def test_menu_help_wires_license_status_action():
    src = (REPO / "traffic_client" / "main.py").read_text()
    assert 'QAction("License Status' in src
    assert "show_license_dialog" in src


def test_menu_gates_four_flagship_actions():
    src = (REPO / "traffic_client" / "main.py").read_text()
    for attr in ("_dpdk_blast_action", "_rdma_blast_action",
                 "_rdma_topology_action", "_rfc2544_action"):
        assert f"self.{attr} = " in src


def test_hmac_scheme_fully_removed():
    """The HMAC scheme from the prior v0.5.183 iteration is gone —
    no `_LICENSE_SALT`, no `_hmac_signature`, no `build_key`."""
    src = (REPO / "utils" / "license.py").read_text()
    for banned in ("_LICENSE_SALT", "_hmac_signature", "def build_key"):
        assert banned not in src, (
            f"HMAC-scheme leftover {banned!r} in utils/license.py"
        )


def test_generator_script_removed():
    """The HMAC generator script must not ship in v0.5.183."""
    assert not (REPO / "scripts" / "generate_license.py").exists()


# ─────────────── v0.5.183 enhancement batch ───────────────


def test_pubkey_resource_bundled_in_repo():
    """The activation dialog reads this file via importlib.resources;
    it MUST be committed to the wheel/tarball."""
    assert (REPO / "resources" / "license" / "tlink-public.pem").exists()


def test_discovery_env_token_beats_disk(rsa_keypair, tmp_path,
                                        monkeypatch):
    """NETGEN_LICENSE_TOKEN in the env overrides ~/.netgen/license.jwt.
    Ops uses this in kiosk / CI deployments."""
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    env_token = _mint_token(rsa_keypair["priv_pem"],
                            email="env@example.com")
    disk_token = _mint_token(rsa_keypair["priv_pem"],
                             email="disk@example.com")
    lic.save(disk_token)
    monkeypatch.setenv(lic._LICENSE_TOKEN_ENV, env_token)
    result = lic.load()
    assert result.is_valid
    assert result.email == "env@example.com"


def test_discovery_env_file_beats_disk(rsa_keypair, tmp_path,
                                       monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    disk_token = _mint_token(rsa_keypair["priv_pem"],
                             email="disk@example.com")
    lic.save(disk_token)
    env_file = tmp_path / "env-license.jwt"
    env_file.write_text(_mint_token(rsa_keypair["priv_pem"],
                                    email="envfile@example.com"))
    monkeypatch.setenv(lic._LICENSE_FILE_ENV, str(env_file))
    result = lic.load()
    assert result.is_valid
    assert result.email == "envfile@example.com"


def test_grace_period_covers_expired_entitlement(
        rsa_keypair, tmp_path, monkeypatch):
    """A JWT whose end_date passed within the last 7 days still
    loads, but flagged as in-grace so the UI can nag."""
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    token = _mint_token(rsa_keypair["priv_pem"], end_date=yesterday)
    (tmp_path / "license.jwt").write_text(token)
    result = lic.load()
    assert result.is_valid, \
        "expected grace-period allow, got: {!r}".format(result.reason)
    assert result.in_grace_period()
    assert any("grace" in n.lower() for n in result.notes)


def test_grace_period_does_not_cover_ancient_expiry(
        rsa_keypair, tmp_path, monkeypatch):
    """> 7 days past end_date → hard-denied."""
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    long_ago = (_dt.date.today()
                - _dt.timedelta(days=100)).isoformat()
    token = _mint_token(rsa_keypair["priv_pem"], end_date=long_ago)
    (tmp_path / "license.jwt").write_text(token)
    result = lic.load()
    assert not result.is_valid
    assert "entitlement expired" in result.reason


def test_audit_log_records_save_and_remove(
        rsa_keypair, tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "LICENSE_AUDIT_LOG",
                        tmp_path / "license-audit.log")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    token = _mint_token(rsa_keypair["priv_pem"])
    lic.save(token)
    lic.remove()
    log = (tmp_path / "license-audit.log").read_text()
    assert "activate" in log
    assert "deactivate" in log


def test_license_chip_grace_state(rsa_keypair, tmp_path, monkeypatch):
    """Chip shows red '⛔ Grace period' when license is in-grace."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    token = _mint_token(rsa_keypair["priv_pem"], end_date=yesterday)
    (tmp_path / "license.jwt").write_text(token)
    from widgets.license_chip import LicenseChip
    chip = LicenseChip()
    assert "Grace" in chip.text()


def test_license_chip_trial_state(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    lic.start_trial()
    from widgets.license_chip import LicenseChip
    chip = LicenseChip()
    assert "Trial" in chip.text()


def test_license_banner_visible_when_close_to_expiry(
        rsa_keypair, tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    # Blank the per-day dismiss marker so the test is self-contained.
    QSettings("Netgen", "netgen-client").setValue(
        "license_banner_dismissed_date", "")
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    soon = (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
    token = _mint_token(rsa_keypair["priv_pem"], end_date=soon)
    lic.save(token)
    from widgets.license_banner import LicenseBanner
    banner = LicenseBanner()
    banner.refresh()
    assert banner.isVisible()
    assert "expires" in banner._text.text().lower()


def test_license_banner_hidden_when_healthy(
        rsa_keypair, tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    from widgets.license_banner import LicenseBanner
    banner = LicenseBanner()
    banner.refresh()
    assert not banner.isVisible()


def test_activation_dialog_qr_button_present():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    dlg = LicenseActivationDialog()
    assert hasattr(dlg, "_qr_btn")


def test_license_dialog_has_renew_button():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from widgets.license_dialog import LicenseDialog
    dlg = LicenseDialog()
    assert hasattr(dlg, "_renew_btn")
    assert "Renew" in dlg._renew_btn.text()


def test_auto_start_streams_gated_by_qsetting():
    """v0.5.183: streams no longer auto-start on launch by default
    per operator ask 2026-07."""
    src = (REPO / "traffic_client" / "menu_actions.py").read_text()
    assert "auto_start_streams_on_launch" in src


def test_cli_license_status_subcommand_present():
    src = (REPO / "netgen_cli.py").read_text()
    for tok in ("cmd_license_status", "cmd_license_activate",
                "cmd_license_deactivate", "cmd_license_trial",
                "cmd_license_fingerprint", '"license"'):
        assert tok in src, f"missing CLI wiring: {tok!r}"


def test_cli_license_fingerprint_prints_hex():
    """Smoke — run the CLI in-process, assert the fingerprint."""
    import io
    import contextlib
    import netgen_cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = netgen_cli.main(["license", "fingerprint"])
    assert rc == 0
    fp = buf.getvalue().strip()
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_license_dialog_activate_during_trial(
        rsa_keypair, tmp_path, monkeypatch):
    """v0.5.184: operator on trial can click Activate in the License
    Status dialog and paste a paid JWT; loaded license wins over
    trial without an app restart."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QMessageBox
    _app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    # Suppress the "activated" info box so exec_ returns.
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **kw: None)
    # Start the operator on trial.
    lic.start_trial()
    assert lic.load().billing_type == lic.BILLING_TRIAL
    from widgets.license_dialog import LicenseDialog
    dlg = LicenseDialog()
    # Activate button exists and is present as a widget.
    assert hasattr(dlg, "_activate_btn")
    # Simulate what _on_activate does: open the activation dialog,
    # paste a JWT, click Activate, then re-refresh.
    from widgets.license_activation_dialog import (
        LicenseActivationDialog,
    )
    subdlg = LicenseActivationDialog(dlg)
    subdlg._key_edit.setPlainText(_mint_token(rsa_keypair["priv_pem"]))
    subdlg._on_activate()
    # The trial-upgraded license now wins.
    dlg._license = lic.load()
    dlg._refresh()
    assert dlg._license.billing_type == "PAID"
    assert dlg._license.is_valid
    # Trial file may still exist on disk but load() prefers JWT —
    # that's the "test_valid_jwt_takes_priority_over_trial"
    # invariant re-asserted from the dialog's perspective.


def test_license_dialog_activate_button_urgent_in_trial(
        tmp_path, monkeypatch):
    """The Activate CTA gets urgent-blue styling in trial mode so
    the operator sees where to upgrade without hunting."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    lic.start_trial()
    from widgets.license_dialog import LicenseDialog
    dlg = LicenseDialog()
    # Urgent styling → contains the primary-blue background rule.
    assert "#1e40af" in dlg._activate_btn.styleSheet()
    # Tooltip mentions the trial → paid upgrade path.
    assert "trial" in dlg._activate_btn.toolTip().lower()


def test_license_dialog_activate_button_not_urgent_when_paid(
        rsa_keypair, tmp_path, monkeypatch):
    """A valid paid license leaves the Activate button in neutral
    styling — no urgency, but still clickable for key rotation."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    lic.save(_mint_token(rsa_keypair["priv_pem"]))
    from widgets.license_dialog import LicenseDialog
    dlg = LicenseDialog()
    assert dlg._activate_btn.styleSheet() == ""
    assert dlg._activate_btn.isEnabled()


def test_cli_license_status_prints_no_license(tmp_path, monkeypatch):
    """`netgen-cli license status` on a machine with no license
    returns non-zero and prints reason='no license'."""
    import io
    import contextlib
    import netgen_cli
    monkeypatch.setattr(lic, "LICENSE_FILE",
                        tmp_path / "license.jwt")
    monkeypatch.setattr(lic, "TRIAL_FILE",
                        tmp_path / "trial.json")
    monkeypatch.setattr(lic, "TRIAL_USED_MARKER",
                        tmp_path / "trial-used.marker")
    monkeypatch.delenv(lic._LICENSE_TOKEN_ENV, raising=False)
    monkeypatch.delenv(lic._LICENSE_FILE_ENV, raising=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = netgen_cli.main(["license", "status"])
    assert rc == 1
    out = buf.getvalue()
    assert "no license" in out
