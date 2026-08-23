"""v0.5.196: default trial extended from 30 → 60 days.

Operator ask: "create a master license key using which can activate
the client for 60 days". After weighing the four shapes (paid JWT,
client-side master code, bumped default trial, in-repo JWT minter),
they picked the simplest: just bump the default trial. Everyone
who clicks 'Start trial' now gets 60 days on first use instead of
30. The trial-used marker still enforces one-per-install.

These tests lock the new constant in and guard against a future
patch silently rolling it back.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05196_test_{os.getpid()}.db"),
)

from utils import license as lic  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# constant lock-in
# ─────────────────────────────────────────────────────────────────────

def test_trial_days_is_sixty():
    """Regression guard — if this fails, someone lowered TRIAL_DAYS
    below the 60-day commitment made in v0.5.196."""
    assert lic.TRIAL_DAYS == 60


# ─────────────────────────────────────────────────────────────────────
# start_trial writes the new duration
# ─────────────────────────────────────────────────────────────────────

def test_start_trial_expires_60_days_out(tmp_path):
    trial_path = tmp_path / "trial.json"
    marker = tmp_path / "trial-used.marker"
    result = lic.start_trial(trial_path=trial_path, used_marker=marker)
    assert result.is_valid

    doc = json.loads(trial_path.read_text())
    started = _dt.datetime.fromisoformat(
        doc["started_at"].replace("Z", "+00:00"))
    expires = _dt.datetime.fromisoformat(
        doc["expires_at"].replace("Z", "+00:00"))
    span = (expires - started).days
    # 59 or 60 depending on whether the boundary tick crosses midnight.
    assert span in (59, 60), f"trial span was {span} days, expected ~60"

    # days_until_expiry reports at least 59 (rounds down from 59.99…)
    assert (result.days_until_expiry() or 0) >= 59


# ─────────────────────────────────────────────────────────────────────
# expired-trial message references the new constant, not "30-day"
# ─────────────────────────────────────────────────────────────────────

def test_expired_trial_message_uses_current_trial_days(tmp_path):
    """Regression: the 'Your N-day trial has ended' note must
    interpolate TRIAL_DAYS, not hardcode 30."""
    trial_path = tmp_path / "trial.json"
    now = _dt.datetime.now(_dt.timezone.utc)
    doc = {
        "version": 1,
        "started_at": (now - _dt.timedelta(days=90)).isoformat(
            timespec="seconds"),
        "expires_at": (now - _dt.timedelta(days=10)).isoformat(
            timespec="seconds"),
        "fingerprint": lic.machine_fingerprint(),
    }
    trial_path.write_text(json.dumps(doc))
    result = lic._load_trial(trial_path=trial_path)
    assert result is not None
    assert not result.is_valid
    assert any("60-day trial has ended" in n for n in result.notes), (
        f"expired-trial notes referenced the old duration: "
        f"{result.notes!r}"
    )
    assert not any("30-day trial has ended" in n for n in result.notes)


# ─────────────────────────────────────────────────────────────────────
# activation dialog / CLI text no longer hardcode "30-day"
# ─────────────────────────────────────────────────────────────────────

def test_no_user_facing_source_hardcodes_30_day():
    """Sweep the three user-facing modules for any residual
    '30-day' strings that would render in the GUI or CLI help."""
    for rel in ("utils/license.py",
                "netgen_cli.py",
                "widgets/license_activation_dialog.py"):
        src = (REPO / rel).read_text()
        assert "30-day" not in src, (
            f"{rel} still hardcodes '30-day' — should reference "
            f"TRIAL_DAYS instead."
        )
