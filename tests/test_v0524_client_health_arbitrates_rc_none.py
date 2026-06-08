"""v0.5.24 — client treats rc=None+log_path=null as 'server lost
state, check /api/health' instead of 'pip failed'.

Operator-reported on srv06 (Jun 7 2026, second attempt at v0.5.23
upgrade from v0.5.21):

  Successfully installed Flask-3.1.3 ... ostg-trafficgen-0.5.23 ...
  [INFO] $ /opt/netgen-server/netgen-venv/bin/python -c import flask, ...
  [client] pip exited rc=None; aborting

But the install actually SUCCEEDED. The visible "Successfully
installed" line proves pip ran to completion. The 'pip exited
rc=None' is a CLIENT-side misreport: the v0.5.21 server (no
state persistence — that's v0.5.23+) got restarted somewhere
during the import check / netgen-upgrade restart, and the next
log-endpoint poll returned {running: false, log_path: null,
return_code: null}. The client treated rc=null identically to
rc=1 (real failure) and aborted, never checking /api/health to
see that the new server was actually up at the new version.

Two failure modes were getting collapsed:
  (a) rc=N (int)         → pip explicitly failed with exit code N
  (b) rc=None            → server forgot — could be success
                           (server-restart-after-upgrade) or
                           signal-kill mid-pip
And one differentiator exists:
  rc=None AND log_path=null → server has NO record of this upgrade
                              (cleared by restart, not by failure).
                              Almost always (a). Almost never (b).
  rc=None AND log_path=set  → server has the log file but proc died.
                              That's (b) — signal kill mid-flight.

v0.5.24:
  1. When rc=None AND log_path=null, fall through to /api/health
     polling instead of aborting.
  2. Parse the EXPECTED version from the uploaded wheel filename
     (PEP 427: name-version-...-platform.whl).
  3. /api/health polling now compares server's reported version
     against expected. Match → declare success. Different version
     → declare failure with actual vs expected (catches silent
     no-op upgrades and rollback scenarios).
  4. Tolerate /api/health payloads that don't expose version
     (very old servers) — fall back to "any 200 OK = success".
  5. Wheel filenames that don't parse → legacy "any 200 OK"
     behavior (don't false-fail on unusual filenames).
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest


_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "install_server_dialog.py"
)


@pytest.fixture(scope="module")
def src():
    return _DIALOG.read_text()


# ────────────────── 1. rc=None + log_path=null fall-through ─────────


def test_run_distinguishes_rc_none_from_rc_nonzero(src):
    """The client must treat rc=None separately from rc=N (int) when
    log_path is also null. Same treatment collapses 'lost state' into
    'failure' and operator sees the v0.5.23 srv06 false-fail."""
    # Find the WheelUploadWorker.run method.
    m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    assert m, "WheelUploadWorker.run not found"
    run_body = m.group(0)

    # The rc-decision block must reference both `rc is None` AND
    # `log_path` to distinguish the "lost state" case.
    assert "rc is None" in run_body, (
        "Client doesn't branch on `rc is None` — would collapse "
        "the 'server forgot state' case into the failure path."
    )
    assert 'log_path' in run_body and 'is None' in run_body, (
        "Client doesn't check log_path to distinguish lost-state "
        "from signal-killed-pip."
    )


def test_lost_state_path_does_not_abort(src):
    """The lost-state branch (rc=None AND log_path=null) must NOT
    call finished_ok.emit(False)/return — must fall through to the
    /api/health polling stage."""
    m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    assert m
    run_body = m.group(0)

    # Locate the elif branch handling rc is None + log_path is None.
    elif_m = re.search(
        r"elif\s+rc\s+is\s+None\s+and\s+lb\.get\(['\"]log_path['\"]\)\s+is\s+None\s*:"
        r"([\s\S]+?)(?=\s+else:\s*$|\s+elif\s)",
        run_body, re.MULTILINE,
    )
    assert elif_m, (
        "No `elif rc is None and lb.get('log_path') is None:` branch "
        "in the rc-decision block — fix wasn't applied."
    )
    branch = elif_m.group(1)
    # The branch must NOT call finished_ok.emit(False) and must NOT
    # `return` from the function — it should fall through to the
    # /api/health stage.
    assert "finished_ok.emit(False)" not in branch, (
        "Lost-state branch still emits finished_ok(False) — would "
        "skip /api/health check and abort. The whole point of v0.5.24 "
        "was to give /api/health a chance to arbitrate."
    )
    assert not re.search(r"\breturn\b", branch), (
        "Lost-state branch still returns — falls out of run() before "
        "the /api/health polling stage runs."
    )
    # And it must set restart_seen + break out of the poll loop.
    assert "restart_seen" in branch and "True" in branch, (
        "Lost-state branch doesn't set restart_seen=True — health "
        "stage doesn't know it's mid-restart."
    )
    assert "break" in branch, (
        "Lost-state branch doesn't `break` — would keep polling /log "
        "indefinitely, never reaching /api/health."
    )


def test_lost_state_path_logs_diagnostic(src):
    """The lost-state branch must emit a log line explaining what
    happened so the operator can tell this isn't a regression from
    the abort path — they shouldn't see `pip exited rc=None`."""
    m = re.search(
        r"elif\s+rc\s+is\s+None\s+and\s+lb\.get\(['\"]log_path['\"]\)\s+is\s+None\s*:"
        r"([\s\S]+?)(?=\s+else:)",
        src, re.MULTILINE,
    )
    assert m
    branch = m.group(1)
    assert "log_chunk.emit" in branch, (
        "Lost-state branch doesn't emit a diagnostic — operator "
        "sees nothing and assumes the previous 'aborting' message "
        "still applies."
    )
    # Pattern check: the message must mention /api/health (so the
    # operator knows what's happening next) and "lost"/"restart"
    # to identify the case.
    assert "/api/health" in branch, (
        "Diagnostic doesn't reference /api/health — operator "
        "doesn't know what we're falling through to."
    )


def test_genuine_signal_kill_still_aborts(src):
    """The legacy rc=None case where log_path IS set (signal kill
    mid-pip, server tracked the upgrade then proc died without an
    exit code) must STILL abort. Without this guard, a genuine
    failure case gets papered over with a /api/health probe that
    succeeds because the OLD server is still running."""
    run_body_m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    run_body = run_body_m.group(0)
    # There must still be an `else:` arm after the elif that hits
    # finished_ok.emit(False) — covers rc=N (int) AND rc=None with
    # log_path set.
    else_m = re.search(
        r"elif\s+rc\s+is\s+None\s+and\s+lb\.get\(['\"]log_path['\"]\)\s+is\s+None\s*:"
        r"[\s\S]+?else:[\s\S]+?finished_ok\.emit\(False\)",
        run_body,
    )
    assert else_m, (
        "Else branch (rc != 0 AND not the lost-state case) lost "
        "its finished_ok(False) — real failures get silently "
        "treated as success."
    )


# ─────────────────── 2. Wheel version parser ────────────────────────


def test_parse_wheel_version_helper_exists(src):
    """Static helper to extract version from a wheel filename per
    PEP 427."""
    assert "_parse_wheel_version" in src, (
        "No _parse_wheel_version helper — /api/health stage can't "
        "tell what version it's expecting."
    )


def test_parse_wheel_version_handles_canonical_wheel():
    """Canonical wheel: name-version-pyver-abi-platform.whl."""
    from widgets.install_server_dialog import WheelUploadWorker
    assert WheelUploadWorker._parse_wheel_version(
        "ostg_trafficgen-0.5.23-py3-none-any.whl"
    ) == "0.5.23"


def test_parse_wheel_version_handles_build_tag():
    """PEP 427 allows an optional build tag: name-version-build-..."""
    from widgets.install_server_dialog import WheelUploadWorker
    # 0.5.24+1 with build tag '1' — the version we want is 0.5.24.
    v = WheelUploadWorker._parse_wheel_version(
        "ostg_trafficgen-0.5.24-1-py3-none-any.whl"
    )
    assert v == "0.5.24", (
        f"Build tag broke version parse: got {v!r} from "
        f"'ostg_trafficgen-0.5.24-1-py3-none-any.whl'"
    )


def test_parse_wheel_version_returns_none_on_garbage():
    """When the filename doesn't follow PEP 427, return None so the
    caller falls back to the 'any 200 OK = success' legacy path
    instead of false-failing."""
    from widgets.install_server_dialog import WheelUploadWorker
    assert WheelUploadWorker._parse_wheel_version("garbage.whl") is None
    assert WheelUploadWorker._parse_wheel_version("no-extension") is None
    assert WheelUploadWorker._parse_wheel_version("") is None


# ────────────── 3. /api/health version verification ────────────────


def test_health_stage_parses_expected_version_from_wheel(src):
    """The health-polling stage must call _parse_wheel_version on
    the uploaded wheel filename so it knows what the new server
    should report."""
    m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    run_body = m.group(0)
    # Anywhere between the rc-decision block and the health-deadline
    # loop, we should call _parse_wheel_version.
    assert "_parse_wheel_version(" in run_body, (
        "Health stage doesn't call _parse_wheel_version — has no "
        "way to verify the new version actually took effect."
    )


def test_health_stage_compares_server_version_against_expected(src):
    """When both an expected version and a server-reported version
    are known, they must be compared. Matching → success. Different
    → keep polling (server may still be mid-restart)."""
    m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    run_body = m.group(0)
    # Must read at least one of the common version-payload keys.
    assert "netgen_version" in run_body or "ostg_version" in run_body, (
        "Health stage doesn't read any version field from "
        "/api/health response. Can't verify upgrade took effect."
    )
    # Must compare the parsed server version against expected.
    assert re.search(
        r"server_version\s*==\s*expected_version"
        r"|expected_version\s*==\s*server_version",
        run_body,
    ), (
        "No equality check between server_version and "
        "expected_version — upgrade verification missing."
    )


def test_health_stage_tolerates_missing_version_field(src):
    """Very old servers' /api/health doesn't include a version key.
    In that case we trust the 200 OK as success — don't false-fail."""
    m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    run_body = m.group(0)
    # Must have a branch for: expected_version set but server_version
    # is None (or empty). Should declare success in that case.
    assert re.search(
        r"if\s+expected_version\s+and\s+not\s+server_version",
        run_body,
    ), (
        "No fallback for servers whose /api/health doesn't expose "
        "version — would never declare success on those hosts."
    )


def test_health_stage_reports_version_mismatch_on_failure(src):
    """When the deadline passes and the server is up but on the
    WRONG version (no-op upgrade, rollback, wrong wheel landed),
    the failure message must explicitly say so — 'server still on
    vX.Y.Z' — so the operator can act."""
    m = re.search(
        r"class WheelUploadWorker[\s\S]+?def run\(self\)[\s\S]+?(?=\n    @staticmethod|\n    def [^_]|\nclass )",
        src,
    )
    run_body = m.group(0)
    # Must track the last-seen version so we can include it in the
    # failure message.
    assert "last_seen_version" in run_body, (
        "Health stage doesn't track last-seen version — can't tell "
        "the operator what version the server is on when the "
        "expected version didn't match."
    )
    # And the failure message must include both versions.
    assert re.search(
        r"last_seen_version\s+and\s+expected_version\s+and"
        r"[\s\S]+?last_seen_version\s*!=\s*expected_version",
        run_body,
    ), (
        "No 'expected vs actual' mismatch reporting — operator "
        "won't know whether the upgrade no-op'd vs failed entirely."
    )


def test_pyproject_version_at_least_0524():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 24), (
        f"Version {m.group(1)} < 0.5.24"
    )
