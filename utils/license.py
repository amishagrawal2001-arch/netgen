"""Netgen client-side license — offline RS256 JWT verification.

The tlink-license-server (`~/dev/tlink-license-server`) mints
"offline codes" that are RS256-signed JWTs. This module verifies
them locally against a bundled RSA public key (no network call
required at runtime).

Flow:

    1. Operator uses tlink-license-server's admin UI (or REST) to
       mint an offline code for a customer with product_code
       "netgen". The server signs a JWT with claims:
           typ = "offline"
           sub = user id
           email
           license_id
           product_code = "netgen"
           license_type = INDIVIDUAL | TEAM
           billing_type = PAID | TRIAL
           start_date, end_date              # entitlement bounds
           device_fingerprint_hash | null    # optional device lock
           iat, exp, jti
    2. Customer receives the JWT (email attachment, etc.).
    3. Customer opens the netgen client → activation dialog →
       pastes the JWT → this module verifies against the bundled
       public key, checks product_code + exp + optional
       fingerprint match, and writes the raw JWT to
       `~/.netgen/license.jwt`.
    4. On subsequent starts the client re-verifies the cached JWT
       and skips the activation dialog on success.

The bundled public key at `resources/license/tlink-public.pem` is
copied from the operator's tlink-license-server installation
(`data/offline-keys/offline-public.pem` on the server). Replace
that file + reship whenever the operator rotates the server
keypair.

Tier model (MVP): any valid netgen JWT unlocks every gated
feature. `billing_type` distinguishes PAID vs TRIAL for display
only — behaviour is identical.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import platform
import re
import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── constants ────────────────────────────────────────────────────

PRODUCT_CODE = "netgen"

LICENSE_DIR = Path.home() / ".netgen"
LICENSE_FILE = LICENSE_DIR / "license.jwt"

# v0.5.183: self-service trial. v0.5.196: extended 30 → 60 days
# so evaluators have a full test cycle before needing a paid JWT.
TRIAL_DAYS = 60
TRIAL_FILE = LICENSE_DIR / "trial.json"
TRIAL_USED_MARKER = LICENSE_DIR / "trial-used.marker"

# The bundled RSA public key. Ships with the wheel under
# `resources/license/tlink-public.pem`. Overridable via env var for
# tests + dev-mode key rotation.
_BUNDLED_PUBKEY_ENV = "NETGEN_LICENSE_PUBKEY_PATH"

# v0.5.183 alt license-file locations, in load-priority order:
#   1. NETGEN_LICENSE_TOKEN — the raw JWT itself (headless / CI)
#   2. NETGEN_LICENSE_FILE   — an explicit path override
#   3. ~/.netgen/license.jwt (LICENSE_FILE default)
#   4. /etc/netgen/license.jwt (system-wide install)
_LICENSE_TOKEN_ENV = "NETGEN_LICENSE_TOKEN"
_LICENSE_FILE_ENV = "NETGEN_LICENSE_FILE"
_SYSTEM_LICENSE_FILE = Path("/etc/netgen/license.jwt")

# v0.5.183 grace period: keep the client licensed for N days AFTER
# `end_date` has passed, but paint everything red so the operator
# knows to renew. Prevents lab outages from expiry-during-a-run.
GRACE_PERIOD_DAYS = 7

# Audit log — records activate/deactivate/renew events for support.
LICENSE_AUDIT_LOG = Path.home() / ".netgen" / "license-audit.log"


def _repo_root() -> Path:
    # utils/ → repo root. Kept for the dev-mode fallback in
    # `_load_bundled_pubkey`.
    return Path(__file__).resolve().parent.parent


BUNDLED_PUBKEY_PATH = (
    _repo_root() / "resources" / "license" / "tlink-public.pem"
)


# The licensed features. Any valid netgen JWT unlocks all four.
# See CHANGELOG for the tier-model tradeoff we chose (single tier).
GATED_FEATURES = (
    "dpdk_blast",
    "rdma_blast",
    "rdma_topology",
    "rfc2544",
)


BILLING_TRIAL = "TRIAL"
BILLING_PAID = "PAID"


# ── data class ───────────────────────────────────────────────────

@dataclass(frozen=True)
class License:
    """Resolved license state.

    `is_valid` == True iff the JWT verified against the bundled
    pubkey AND product_code == "netgen" AND has not expired AND
    (if the JWT binds a device_fingerprint_hash) the local machine
    fingerprint matches.
    """
    jwt_token: Optional[str] = None
    subject: str = ""
    email: str = ""
    license_id: str = ""
    product_code: str = ""
    license_type: str = ""       # INDIVIDUAL | TEAM
    billing_type: str = ""       # PAID | TRIAL
    start_date: Optional[_dt.date] = None
    end_date: Optional[_dt.date] = None
    expiry: Optional[_dt.datetime] = None   # exp claim (session lifetime)
    device_fingerprint_hash: Optional[str] = None
    is_valid: bool = False
    reason: str = "no license"
    # Any human-facing warnings we want to surface — e.g. "Trial"
    # vs "Paid", or "expires in 5 days".
    notes: List[str] = field(default_factory=list)

    def is_trial(self) -> bool:
        return self.billing_type == BILLING_TRIAL

    def in_grace_period(self) -> bool:
        return self.reason == "grace period"

    def days_until_expiry(self) -> Optional[int]:
        """Days until whichever end comes first — end_date or JWT
        exp — since either can lock the client out."""
        candidates = []
        if self.end_date is not None:
            candidates.append((self.end_date - _dt.date.today()).days)
        if self.expiry is not None:
            today = _dt.datetime.now(_dt.timezone.utc)
            candidates.append((self.expiry - today).days)
        return min(candidates) if candidates else None

    def is_feature_licensed(self, feature: str) -> bool:
        # Non-gated features are always allowed. Gated features
        # require a valid license.
        if feature not in GATED_FEATURES:
            return True
        return self.is_valid


# ── machine fingerprint ──────────────────────────────────────────

def machine_fingerprint() -> str:
    """Return a 64-char hex SHA-256 fingerprint of this machine.

    Server validates fingerprints against a 64-char hex regex only
    (users.js:359) — the CONTENT is entirely client-defined. This
    algorithm hashes over:
      * MAC address (stable within a boot; can change on re-image)
      * Hostname
      * Platform node (usually the same as hostname on modern OSes)
      * Machine architecture
      * `~/.netgen/fingerprint.salt` — persisted random bytes so a
        reinstall on the same box produces a stable fingerprint
        even if the MAC or hostname changes.

    Deliberately does NOT include disk serial / CPU serial — those
    require platform-specific ioctls / vendor tools that don't
    ship on stripped-down lab hosts.
    """
    parts: List[str] = [
        _stable_mac(),
        socket.gethostname(),
        platform.node(),
        platform.machine(),
        _persistent_salt(),
    ]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_mac() -> str:
    try:
        mac = uuid.getnode()
        # getnode() returns a random 48-bit int if it can't read a
        # real MAC. Detect that and fall back to a stable-ish
        # placeholder so we don't invalidate fingerprints on
        # every start.
        if (mac >> 40) & 0x01:  # locally-administered bit set
            return "no-mac"
        return f"{mac:012x}"
    except Exception:
        return "no-mac"


def _persistent_salt() -> str:
    """Return a per-install random salt, persisted in ~/.netgen
    so the fingerprint stays stable across reboots + package
    reinstalls."""
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    salt_path = LICENSE_DIR / "fingerprint.salt"
    try:
        if salt_path.exists():
            return salt_path.read_text(encoding="utf-8").strip()
        salt = os.urandom(16).hex()
        salt_path.write_text(salt, encoding="utf-8")
        try:
            salt_path.chmod(0o600)
        except OSError:
            pass
        return salt
    except OSError as exc:
        # Read-only home dir (edge case). Degrade to a static salt
        # so the fingerprint stays deterministic within this run,
        # even if not persistent across reboots.
        logger.warning("[license] fingerprint salt not persistable: %s",
                       exc)
        return "static-fallback"


# ── JWT verification ─────────────────────────────────────────────

_JWT_STRUCTURE_RE = re.compile(
    r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
)


class _KeyLoadError(RuntimeError):
    pass


def _load_bundled_pubkey() -> str:
    """Read the bundled PEM. Resolution order:

      1. `NETGEN_LICENSE_PUBKEY_PATH` env var override (tests +
         dev-mode key rotation)
      2. `importlib.resources.files("resources").joinpath(
             "license/tlink-public.pem")` — works in wheel installs
         AND in PyInstaller-bundled binaries via the resource
         reader; the "resources" package is declared in
         pyproject.toml's `[tool.setuptools.package-data]`.
      3. Filesystem fallback at `<repo>/resources/license/
         tlink-public.pem` — dev-mode.

    Raises `_KeyLoadError` with an operator-actionable message if
    nothing works."""
    override = os.environ.get(_BUNDLED_PUBKEY_ENV)
    if override:
        path = Path(override)
        if not path.exists():
            raise _KeyLoadError(
                f"NETGEN_LICENSE_PUBKEY_PATH={override} does not exist"
            )
        return path.read_text(encoding="utf-8")
    # Wheel / PyInstaller path via importlib.resources.
    try:
        from importlib import resources
        try:
            ref = resources.files("resources").joinpath(
                "license/tlink-public.pem")
            if ref.is_file():
                return ref.read_text(encoding="utf-8")
        except (ModuleNotFoundError, FileNotFoundError):
            pass
    except ImportError:
        pass
    # Dev-mode filesystem fallback — repo root.
    if BUNDLED_PUBKEY_PATH.exists():
        return BUNDLED_PUBKEY_PATH.read_text(encoding="utf-8")
    raise _KeyLoadError(
        "license public key not found. Netgen ships with the "
        "tlink-license-server public key at "
        "resources/license/tlink-public.pem. If you built from "
        "source, copy the PEM from your license server's "
        "data/offline-keys/ directory. See resources/license/"
        "README.md for details."
    )


def _parse_iso_date(v: Any) -> Optional[_dt.date]:
    if not isinstance(v, str) or not v:
        return None
    try:
        return _dt.date.fromisoformat(v)
    except ValueError:
        return None


def verify_jwt(token: str,
               pubkey_pem: Optional[str] = None,
               allow_fingerprint_mismatch: bool = False,
               ) -> License:
    """Verify a raw JWT string and return a resolved License.

    Never raises — bad tokens come back as `License(is_valid=False)`
    with a human-friendly `reason` the UI can show.

    Set `allow_fingerprint_mismatch=True` for diagnostics dialogs
    that want to display a JWT's contents even when it's bound to
    a different machine.
    """
    if not token:
        return License(reason="no license")
    token = token.strip()
    if not _JWT_STRUCTURE_RE.match(token):
        return License(reason="not a JWT (expected three dot-separated segments)")
    try:
        pem = pubkey_pem or _load_bundled_pubkey()
    except _KeyLoadError as exc:
        return License(
            jwt_token=token,
            reason=f"cannot load public key: {exc}",
        )
    try:
        import jwt as _jwt
    except ImportError:
        return License(
            jwt_token=token,
            reason="PyJWT is not installed",
        )
    try:
        # We do our own exp check to produce a friendlier reason,
        # so verify with the built-in exp checker DISABLED.
        payload = _jwt.decode(
            token, pem, algorithms=["RS256"],
            options={"verify_exp": False, "verify_aud": False},
        )
    except _jwt.InvalidSignatureError:
        return License(jwt_token=token, reason="invalid signature")
    except _jwt.DecodeError as exc:
        return License(jwt_token=token,
                       reason=f"malformed JWT: {exc}")
    except Exception as exc:  # noqa: BLE001
        return License(jwt_token=token,
                       reason=f"JWT decode failed: {exc}")

    reason = _check_payload(payload, allow_fingerprint_mismatch)
    # Extract common fields regardless of validity so the UI can
    # display "expired since X" or "wrong product" with context.
    exp_ts = payload.get("exp")
    exp_dt = None
    if isinstance(exp_ts, (int, float)):
        try:
            exp_dt = _dt.datetime.fromtimestamp(
                int(exp_ts), tz=_dt.timezone.utc)
        except (OSError, ValueError, OverflowError):
            exp_dt = None
    notes = []
    if payload.get("billing_type") == BILLING_TRIAL:
        notes.append("Trial license — behaviour identical to paid.")
    lic = License(
        jwt_token=token,
        subject=str(payload.get("sub", "")),
        email=str(payload.get("email", "")),
        license_id=str(payload.get("license_id", "")),
        product_code=str(payload.get("product_code", "")),
        license_type=str(payload.get("license_type", "")),
        billing_type=str(payload.get("billing_type", "")),
        start_date=_parse_iso_date(payload.get("start_date")),
        end_date=_parse_iso_date(payload.get("end_date")),
        expiry=exp_dt,
        device_fingerprint_hash=(
            payload.get("device_fingerprint_hash") or None),
        is_valid=(reason == "ok"),
        reason=reason,
        notes=notes,
    )
    return lic


def _check_payload(payload: Dict[str, Any],
                   allow_fingerprint_mismatch: bool) -> str:
    """Return "ok" if the payload passes every semantic check,
    otherwise a human-friendly reason string."""
    if payload.get("typ") != "offline":
        return f"unexpected token type ({payload.get('typ')!r})"
    if payload.get("product_code") != PRODUCT_CODE:
        got = payload.get("product_code")
        return (f"license is for product {got!r}, not {PRODUCT_CODE!r}. "
                f"Ask your issuer for a netgen license.")
    exp_ts = payload.get("exp")
    if isinstance(exp_ts, (int, float)):
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
        if now >= exp_ts:
            return "license expired — request a fresh one"
    # end_date is the ENTITLEMENT expiry; exp is the OFFLINE
    # SESSION expiry. Either enforces a hard stop.
    end_date = _parse_iso_date(payload.get("end_date"))
    if end_date is not None and end_date < _dt.date.today():
        return f"entitlement expired on {end_date.isoformat()}"
    fingerprint = payload.get("device_fingerprint_hash")
    if fingerprint and not allow_fingerprint_mismatch:
        local = machine_fingerprint()
        if fingerprint != local:
            return (
                "license is bound to a different machine — the "
                f"fingerprint in the token doesn't match this "
                f"host's (expected {fingerprint[:12]}…, got "
                f"{local[:12]}…). Ask your issuer to mint a "
                f"code for this device's fingerprint (Help → "
                f"License Status shows it)."
            )
    return "ok"


# ── trial ────────────────────────────────────────────────────────

def can_start_trial(
        used_marker: Optional[Path] = None) -> bool:
    """Return True iff no trial has been consumed on this
    installation. Trials are one-shot per `~/.netgen/` dir.

    A determined user can delete the marker to start again — this
    is deliberately soft. The B2B free-trial UX is more valuable
    than the tamper-resistance."""
    return not (used_marker or TRIAL_USED_MARKER).exists()


def _load_trial(
        trial_path: Optional[Path] = None) -> Optional[License]:
    """Return the `License` from a live trial file, `None` if the
    trial file is absent, malformed, or expired.

    An expired trial produces `License(is_valid=False, reason='trial
    expired')` so the UI can distinguish "never had a trial" from
    "trial ran out" — the caller upcasts it via `load()`."""
    path = trial_path or TRIAL_FILE
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[license] trial file unreadable: %s", exc)
        return None
    if not isinstance(doc, dict):
        return None
    started = doc.get("started_at")
    expires = doc.get("expires_at")
    if not isinstance(started, str) or not isinstance(expires, str):
        return None
    try:
        started_dt = _dt.datetime.fromisoformat(
            started.replace("Z", "+00:00"))
        expires_dt = _dt.datetime.fromisoformat(
            expires.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    if now >= expires_dt:
        return License(
            billing_type=BILLING_TRIAL,
            expiry=expires_dt,
            start_date=started_dt.date(),
            end_date=expires_dt.date(),
            is_valid=False,
            reason="trial expired — request a paid license",
            notes=[f"Your {TRIAL_DAYS}-day trial has ended."],
        )
    days_left = (expires_dt - now).days
    return License(
        billing_type=BILLING_TRIAL,
        license_type="INDIVIDUAL",
        product_code=PRODUCT_CODE,
        expiry=expires_dt,
        start_date=started_dt.date(),
        end_date=expires_dt.date(),
        is_valid=True,
        reason="ok",
        notes=[
            f"Trial license — {days_left + 1} day(s) remaining. "
            "Buy a license to keep the licensed features after the "
            "trial ends."
        ],
    )


def start_trial(
        trial_path: Optional[Path] = None,
        used_marker: Optional[Path] = None,
        days: int = TRIAL_DAYS) -> License:
    """Write the trial file + used marker. Returns the resulting
    License, or an invalid License with a reason if the trial has
    already been consumed on this install."""
    trial_path = trial_path or TRIAL_FILE
    used_marker = used_marker or TRIAL_USED_MARKER
    if not can_start_trial(used_marker):
        return License(
            reason="trial already used on this device",
            notes=["The trial has already been started once on "
                   "this device."],
        )
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    now = _dt.datetime.now(_dt.timezone.utc)
    end = now + _dt.timedelta(days=days)
    doc = {
        "version": 1,
        "started_at": now.isoformat(timespec="seconds"),
        "expires_at": end.isoformat(timespec="seconds"),
        "fingerprint": machine_fingerprint(),
    }
    try:
        tmp = trial_path.with_suffix(trial_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(doc, indent=2), encoding="utf-8")
        tmp.replace(trial_path)
        try:
            trial_path.chmod(0o600)
        except OSError:
            pass
        used_marker.touch()
        try:
            used_marker.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        return License(reason=f"could not persist trial: {exc}")
    loaded = _load_trial(trial_path)
    return loaded or License(reason="trial write succeeded but read back failed")


# ── load / save / remove ─────────────────────────────────────────

def _discover_license_source() -> Optional[str]:
    """Return the raw JWT string from the highest-priority source:

      1. `NETGEN_LICENSE_TOKEN` env var — the raw JWT
      2. `NETGEN_LICENSE_FILE`  env var — path override
      3. `~/.netgen/license.jwt` (default)
      4. `/etc/netgen/license.jwt` (system-wide install)

    Returns `None` if no source has a token."""
    tok = (os.environ.get(_LICENSE_TOKEN_ENV) or "").strip()
    if tok:
        return tok
    override = os.environ.get(_LICENSE_FILE_ENV)
    if override:
        try:
            return Path(override).read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("[license] %s=%s unreadable: %s",
                           _LICENSE_FILE_ENV, override, exc)
            return None
    for candidate in (LICENSE_FILE, _SYSTEM_LICENSE_FILE):
        if candidate.exists():
            try:
                return candidate.read_text(
                    encoding="utf-8").strip()
            except OSError as exc:
                logger.warning(
                    "[license] %s unreadable: %s", candidate, exc)
    return None


def load(path: Optional[Path] = None) -> License:
    """Load and verify the persisted JWT.

    Priority (v0.5.183 additions):
      1. If `path` is passed explicitly, read only from there
         (used by tests + unit tests).
      2. Otherwise consult sources in this order:
         a. NETGEN_LICENSE_TOKEN env var (headless / CI)
         b. NETGEN_LICENSE_FILE  env var (path override)
         c. ~/.netgen/license.jwt (default)
         d. /etc/netgen/license.jwt (system-wide install)
      3. If nothing found, fall through to trial file
      4. Otherwise: `License(is_valid=False, reason='no license')`

    Also applies the 7-day post-expiry grace period: an entitlement
    that ended less than GRACE_PERIOD_DAYS ago still validates but
    the License carries a `reason='grace period'` note so the UI
    can paint it red.
    """
    if path is not None:
        # Explicit path — test / unit-test entry point.
        if not path.exists():
            return License(reason="no license")
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return License(reason=f"unreadable: {exc}")
        return _maybe_grace(verify_jwt(token))
    # v0.5.195: an invalid `license.jwt` (expired past grace,
    # tampered, wrong fingerprint) must NOT shadow a still-live
    # trial. Pre-fix this function returned `_maybe_grace
    # (verify_jwt(token))` verbatim the moment any license.jwt
    # existed, so a stale paid-license file blocked startup even
    # though a valid trial.json sat next to it — operator report
    # 2026-07-20 on san-hp-srv06's client. Rule: fall through to
    # the trial when the JWT can't authorise the session on its
    # own, and only surface the JWT's reason if the trial can't
    # either.
    token = _discover_license_source()
    jwt_lic = _maybe_grace(verify_jwt(token)) if token else None
    if jwt_lic and jwt_lic.is_valid:
        return jwt_lic
    trial = _load_trial()
    if trial is not None and trial.is_valid:
        return trial
    # Neither can authorise. Prefer the JWT's error message (it's
    # more actionable — "expired 12 Jul" beats "no trial") when a
    # JWT was present; else the expired-trial License; else the
    # generic no-license default.
    if jwt_lic is not None:
        return jwt_lic
    if trial is not None:
        return trial
    return License(reason="no license")


def _maybe_grace(lic: License) -> License:
    """v0.5.183: re-mark a just-expired license as valid-in-grace
    if the entitlement end_date is within the past
    GRACE_PERIOD_DAYS days. The chip + banner paint amber/red so
    the operator sees "renew now" — but their lab run isn't
    interrupted mid-shift."""
    if lic.is_valid:
        return lic
    # Only entitlement-expired paths qualify. Signature failures,
    # wrong product, tampering, missing file etc. get NO grace.
    if "entitlement expired" not in (lic.reason or ""):
        return lic
    if lic.end_date is None:
        return lic
    days_over = (_dt.date.today() - lic.end_date).days
    if days_over < 0 or days_over > GRACE_PERIOD_DAYS:
        return lic
    grace_left = GRACE_PERIOD_DAYS - days_over
    return License(
        jwt_token=lic.jwt_token,
        subject=lic.subject,
        email=lic.email,
        license_id=lic.license_id,
        product_code=lic.product_code,
        license_type=lic.license_type,
        billing_type=lic.billing_type,
        start_date=lic.start_date,
        end_date=lic.end_date,
        expiry=lic.expiry,
        device_fingerprint_hash=lic.device_fingerprint_hash,
        is_valid=True,
        reason="grace period",
        notes=[
            f"⚠ License expired {days_over} day(s) ago. Running "
            f"under a {GRACE_PERIOD_DAYS}-day grace period — "
            f"renew within {grace_left} day(s) to avoid lockout."
        ],
    )


def save(token: str, path: Optional[Path] = None) -> License:
    """Persist the given JWT. Returns the resolved License so the
    caller can display "valid until X" without a second load."""
    path = path or LICENSE_FILE
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    token = token.strip()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token, encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    lic = load(path)
    _audit(
        "activate",
        (f"tier={lic.billing_type or '?'} "
         f"email={lic.email or '?'} "
         f"end_date={lic.end_date.isoformat() if lic.end_date else '?'} "
         f"valid={lic.is_valid}")
    )
    return lic


def remove(path: Optional[Path] = None) -> None:
    path = path or LICENSE_FILE
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _audit("deactivate", f"path={path}")


# ── audit log ────────────────────────────────────────────────────

def _audit(event: str, detail: str = "",
           log_path: Optional[Path] = None) -> None:
    """Best-effort structured event log at ~/.netgen/license-audit.log.
    Never raises — audit failures must not block the app."""
    log_path = log_path or LICENSE_AUDIT_LOG
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')} "
            f"event={event}"
            + (f" {detail}" if detail else "")
            + "\n"
        )
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        try:
            log_path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.debug("[license] audit log unavailable: %s", exc)


# ── convenience ──────────────────────────────────────────────────

def is_activated(license: Optional[License] = None) -> bool:
    """Return True iff the client should let the operator proceed."""
    if license is None:
        license = load()
    return license.is_valid


def is_feature_licensed(feature: str,
                        license: Optional[License] = None) -> bool:
    if license is None:
        license = load()
    return license.is_feature_licensed(feature)


def tooltip_for_locked_feature(feature: str,
                               license: Optional[License] = None
                               ) -> str:
    """Message shown when hovering a greyed-out menu item."""
    if license is None:
        license = load()
    if not license.jwt_token:
        return (
            "Not activated. Netgen was closed without a valid "
            "license — restart to activate."
        )
    if license.reason == "no license":
        return "Not activated. Help → License Status… to activate."
    return f"License problem: {license.reason}"


# ── file extraction helper (used by the load-from-file button) ──

_JWT_IN_TEXT_RE = re.compile(
    r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
)


def extract_jwt_from_text(text: str) -> Optional[str]:
    """Fish a JWT out of a file whose exact shape we don't know.
    Accepts:
      * A raw JWT on its own line
      * A JSON file with a `"token"` or `"jwt"` field (server's
        typical response envelope)
      * Any text containing exactly one JWT pattern
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            for k in ("token", "jwt", "offline_code",
                      "license", "license_token"):
                v = doc.get(k)
                if isinstance(v, str) and _JWT_STRUCTURE_RE.match(
                        v.strip()):
                    return v.strip()
    except ValueError:
        pass
    m = _JWT_IN_TEXT_RE.search(text)
    return m.group(0) if m else None
