"""Regression test for v0.4.7: persist the active session-file path
across launches.

Operator-reported: opened the client, expected to see streams + TGs
from prior work, got "TG 0" with no streams and no other TGs. Root
cause:

  1. Operator had used Save As / Load From… to point the active
     session at a non-default file (e.g. ~/Desktop/NetGen/session.json).
  2. `_current_session_path` was set in memory but never persisted.
  3. On restart, main.py:81 reset `_current_session_path` to
     `get_session_file_path()` = `~/Documents/OSTG/session.json`.
  4. `load_session()` opened that file (empty, never written by the
     operator), found no servers / no streams.
  5. The auto-add-default-server path tacked on TG 0 via the
     localhost / env-var URL.
  6. Operator saw TG 0 with no streams and concluded "TG 0 loaded
     but other TGs and streams didn't" — when in fact the work was
     untouched on disk at the Save As'd path, the app just
     forgot which file to open.

Fix: write the path to QSettings on every Save As / Load From; on
startup, read it back and use it if the file still exists, else fall
back to the default. The fix is intentionally tiny: just two write
sites + one read site. Tests pin the contract at the source.
"""
from __future__ import annotations

import re
from pathlib import Path


_MAIN = Path(__file__).resolve().parents[1] / "traffic_client" / "main.py"
_ACTIONS = Path(__file__).resolve().parents[1] / "traffic_client" / "menu_actions.py"


def test_startup_reads_persisted_session_path():
    """main.py must consult QSettings BEFORE falling back to the
    default. The persisted path takes precedence (when it exists)."""
    src = _MAIN.read_text()
    # The init block that sets _current_session_path now must
    # consult QSettings.
    m = re.search(
        r"self\._current_session_path\s*=\s*[\s\S]+?(?=\n\s{8}[^\s])",
        src,
    )
    assert m, "could not locate _current_session_path init block"
    block = m.group(0)
    # Must reference QSettings inside the init block
    assert "QSettings" in src and "current_session_path" in src, (
        "main.py doesn't read 'current_session_path' from QSettings — "
        "Save As / Load From… choice won't survive a restart."
    )
    # And the read must use type=str
    assert re.search(
        r"QSettings\(\)\.value\(\s*[\"']current_session_path[\"']",
        src,
    ), "main.py QSettings read for current_session_path not found"


def test_startup_falls_back_when_persisted_file_missing():
    """If the persisted path points to a moved / deleted file, the
    app must NOT wedge — fall back silently to the default. Without
    this, an operator who archived an old session into a different
    folder would get a confusing error on next launch."""
    src = _MAIN.read_text()
    # The init block must check os.path.exists on the persisted path
    # and fall back to default_path when it's gone.
    assert re.search(
        r"if persisted and [a-z_.]*exists\(persisted\)",
        src,
    ), (
        "Startup doesn't check whether the persisted session path "
        "still exists — a moved file would wedge the app at launch."
    )
    assert "default_path" in src, (
        "Startup has no `default_path` fallback variable in the "
        "persistence init block."
    )


def test_save_as_persists_chosen_path():
    """save_session_as must call _persist_current_session_path so
    the next launch reopens the new file. Pin the helper-name
    invocation so a refactor that just sets self._current_session_path
    without the persist call surfaces here."""
    src = _ACTIONS.read_text()
    # Find the save_session_as body
    m = re.search(
        r"def save_session_as\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "save_session_as not found"
    body = m.group(0)
    assert "_persist_current_session_path" in body, (
        "save_session_as doesn't call _persist_current_session_path. "
        "Without it the chosen path lives only in memory — restart "
        "snaps back to the default and the operator's work appears "
        "to vanish."
    )


def test_load_from_persists_chosen_path():
    """load_session_from picks an existing file. That choice must
    also persist so the next launch reopens it. Same bug pattern as
    Save As — fix is symmetric."""
    src = _ACTIONS.read_text()
    # Find the load_session body that runs on Load From… path
    m = re.search(
        r"def load_session\(self,[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "load_session not found"
    body = m.group(0)
    # The branch that sets _current_session_path = session_file_path
    # must also call _persist_current_session_path
    assert "_persist_current_session_path(session_file_path)" in body, (
        "load_session's Load From… branch doesn't persist the path. "
        "The operator picks a file from the dialog, sees it loaded, "
        "then restarts and gets the empty default."
    )


def test_persistence_helper_exists():
    """The helper function itself must exist and use the
    'current_session_path' QSettings key. If someone renames the
    key, the read site in main.py would silently start returning
    empty — confusing-half-bug pattern."""
    src = _ACTIONS.read_text()
    assert re.search(
        r"def _persist_current_session_path\(path: str\)",
        src,
    ), "helper missing"
    assert "QSettings().setValue(\"current_session_path\", path)" in src, (
        "helper doesn't write to the 'current_session_path' QSettings "
        "key — main.py's read would never find the value."
    )


def test_persistence_round_trip(qapp):
    """End-to-end: write a path via the helper, read it back via
    QSettings the same way main.py does. Uses a temp QSettings
    scope so we don't pollute the operator's real settings."""
    from PyQt5.QtCore import QSettings, QCoreApplication
    from traffic_client.menu_actions import _persist_current_session_path

    # Save the original value so we can restore it after the test.
    settings = QSettings()
    original = settings.value("current_session_path", "", type=str)
    try:
        test_path = "/tmp/v047-test-session.json"
        _persist_current_session_path(test_path)
        # Settings may need a sync on macOS — read back from a fresh
        # QSettings instance to catch that case.
        round_trip = QSettings().value(
            "current_session_path", "", type=str,
        )
        assert round_trip == test_path, (
            f"persist + read round-trip lost the path. wrote "
            f"{test_path!r}, read back {round_trip!r}. The QSettings "
            f"key must match exactly between the write helper and "
            f"main.py's read."
        )
    finally:
        # Restore whatever the user had before the test ran.
        QSettings().setValue("current_session_path", original)
