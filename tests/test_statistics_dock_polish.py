"""Statistics dock — v0.2.99 polish + sort-state regression pins.

The dock class is a mixin used by the main window — instantiating
it cleanly headlessly is heavy (needs ConnectionManager, server
tree state, async fetch wiring). These tests pin the wiring via
source-grep + a tiny in-memory exercise of the pure-function
sort-state helper to confirm the import + call path are correct.

What's pinned:
  * `update_stream_statistics_table` captures sort state before
    `setRowCount(0)` and restores it after the rebuild loop —
    matches the v0.2.92 Devices-tab pattern.
  * Both `update_stream_statistics_table` and
    `update_statistics_table` bail at entry when
    `self._refresh_paused` is True — the operator-facing pause
    toggle freezes both tables together.
  * The action bar wires three new widgets: filter QLineEdit,
    pause QPushButton (checkable), last-refresh QLabel.
  * `_apply_stream_filter`, `_on_refresh_pause_toggled`, and
    `_update_last_refresh_chip` helper methods exist.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
STATS_FILE = REPO / "traffic_client" / "statistics_section.py"


@pytest.fixture(scope="module")
def src():
    return STATS_FILE.read_text()


# ─────────────────────────────────── sort-state capture/restore
def test_v0_2_99_imports_sort_state_helpers(src):
    assert "from utils.table_sort_state import" in src, (
        "statistics_section.py must import capture_sort_state / "
        "restore_sort_state from utils.table_sort_state — v0.2.99 "
        "wiring for the per-refresh sort preservation."
    )
    assert "capture_sort_state" in src
    assert "restore_sort_state" in src


def test_v0_2_99_update_stream_stats_captures_then_restores(src):
    """The rebuild path must capture sort state BEFORE setRowCount(0)
    and restore it AFTER the population loop. Without this Qt re-sorts
    on every setItem call (sorting was enabled at construction) and
    the operator's chosen column gets blown away on every 2 s
    refresh."""
    # Use Python-side find so we don't fall into the grep-cache trap.
    body = re.search(
        r"def update_stream_statistics_table\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None, "update_stream_statistics_table not found"
    text = body.group(0)
    # Capture happens before setRowCount(0).
    capture_idx = text.find("capture_sort_state")
    setrowcount_idx = text.find("setRowCount(0)")
    restore_idx = text.find("restore_sort_state")
    assert capture_idx != -1, "capture_sort_state not called"
    assert restore_idx != -1, "restore_sort_state not called"
    assert setrowcount_idx != -1, "setRowCount(0) missing — rebuild path changed?"
    assert capture_idx < setrowcount_idx < restore_idx, (
        "ordering must be capture → setRowCount(0) → ... → restore. "
        "Re-check the v0.2.99 wiring after the refactor."
    )


# ─────────────────────────────────── pause-refresh gate
def test_v0_2_99_stream_update_bails_when_paused(src):
    """First statement of update_stream_statistics_table (after the
    hasattr guard) must be the `_refresh_paused` check."""
    body = re.search(
        r"def update_stream_statistics_table\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    text = body.group(0)
    assert "_refresh_paused" in text, (
        "update_stream_statistics_table missing the _refresh_paused "
        "gate — v0.2.99 pause toggle won't freeze the stream table."
    )
    # Order check: the pause guard must come BEFORE the sort-state
    # capture, otherwise we'd capture/restore even on paused frames
    # (cheap, but wasteful + risks visual flicker).
    pause_idx = text.find("_refresh_paused")
    capture_idx = text.find("capture_sort_state")
    assert pause_idx < capture_idx, (
        "_refresh_paused check must precede capture_sort_state so a "
        "paused refresh doesn't even touch the table state"
    )


def test_v0_2_99_interface_update_bails_when_paused(src):
    """The Interface-stats path must honour the same pause flag so
    both tables freeze together — otherwise the operator sees a
    half-frozen dock during pause."""
    body = re.search(
        r"def update_statistics_table\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None, "update_statistics_table not found"
    text = body.group(0)
    assert "_refresh_paused" in text, (
        "update_statistics_table missing the _refresh_paused gate — "
        "v0.2.99 pause toggle should freeze BOTH stats tables"
    )


# ─────────────────────────────────── action-bar widgets
def test_v0_2_99_action_bar_has_filter_pause_chip_widgets(src):
    """The v0.2.99 trio (filter, pause, last-refresh) all exist.

    v0.3.11 split the trio: the substring filter moved out of the
    bottom action bar to sit ABOVE the Stream Statistics table (its
    real target), matching the "filter above table" convention of
    the Devices / L2 / Stateful TCP / Streams configuration tabs.
    Pause + last-refresh chip stayed in the bottom action bar — they
    affect the whole dock, not one table.
    """
    # Filter box still exists (placement changed in v0.3.11; existence
    # of the widget itself is what we care about for state continuity).
    assert "self.stream_filter_edit" in src, (
        "stream_filter_edit QLineEdit missing"
    )
    # Placeholder text — v0.3.11 reworded ("Filter streams…" was vague,
    # the new wording names the columns it matches against).
    assert "Stream Name / Interface / Engine" in src, (
        "stream_filter_edit placeholder text pinned to the v0.3.11 "
        "column-naming wording"
    )
    # Pause button
    assert "self.pause_refresh_button" in src
    assert "setCheckable(True)" in src, (
        "pause_refresh_button must be checkable (toggle button)"
    )
    # Last-refresh label
    assert "self.last_refresh_label" in src


def test_v0_2_99_helper_methods_defined(src):
    for method in (
        "_on_stream_filter_changed",
        "_apply_stream_filter",
        "_on_refresh_pause_toggled",
        "_update_last_refresh_chip",
    ):
        assert re.search(
            rf"^    def {method}\(self", src, flags=re.MULTILINE,
        ), f"helper method {method!r} not defined"


def test_v0_2_99_pause_toggle_flips_button_text(src):
    """When the operator clicks Pause, the button text should flip to
    Resume so the state is visually obvious. Pin both literal strings
    so a copy-edit can't accidentally drop the swap."""
    body = re.search(
        r"def _on_refresh_pause_toggled\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert '"Resume"' in text and '"Pause"' in text, (
        "_on_refresh_pause_toggled must flip the button between "
        "Pause / Resume so the state reads at a glance"
    )


def test_v0_2_99_filter_walks_name_iface_engine_columns(src):
    """The needle is matched against Stream Name (col 0), Interface
    (col 1), Engine (col 2). Pin the column indices so a future
    column-reorder is forced to update the filter wiring too."""
    body = re.search(
        r"def _apply_stream_filter\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    text = body.group(0)
    assert "(0, 1, 2)" in text, (
        "filter must walk the Stream Name (0) + Interface (1) + "
        "Engine (2) columns — if the column order ever changes, the "
        "filter logic needs the same change"
    )


# ─────────────────────────────────── sort-state helper sanity
def test_v0_2_99_sort_state_helpers_round_trip():
    """Pure-function check on the helper module itself — confirms
    capture_sort_state + restore_sort_state are import-safe and the
    -1 sentinel round-trips through restore as a no-op."""
    from utils.table_sort_state import (
        capture_sort_state, restore_sort_state,
    )
    from PyQt5.QtCore import Qt
    # Both helpers are tolerant of failures — passing a non-table
    # object should return the sentinel without raising.
    state = capture_sort_state(object())
    assert state == (-1, Qt.AscendingOrder)
    # Restoring None / sentinel must be a no-op (no exception).
    restore_sort_state(object(), None)
    restore_sort_state(object(), state)
