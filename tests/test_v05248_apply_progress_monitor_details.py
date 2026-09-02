"""v0.5.248 — Apply progress shows elapsed time; monitor pill click
opens a details dialog.

Operator on 2026-09-02 reported two UI ergonomics issues:

1. "Applying 0/1" progress bar sat frozen for the whole apply — the
   label only changed when a device COMPLETED, so an apply that took
   40s (server-side DHCP restart, FRR spin-up, etc.) looked identical
   to a hard hang. No feedback that anything was happening.

2. The "monitors: ⚠ 2" pill showed there were 2 monitor warnings but
   clicking silently re-polled — the ACTUAL warnings only lived in
   the tooltip. Easy to miss when the operator asks "what's wrong?".

Fixes:

- **_show_apply_progress + _on_apply_elapsed_tick** — starts a 1Hz
  QTimer that repaints the label as `Applying N/M (Ss)` while the
  batch runs. The per-device completion tick also stamps elapsed
  so the label stays consistent. Ticker stops in _hide_apply_progress.

- **_show_monitor_health_details** — new method opens a QMessageBox
  with per-monitor state (UP / DOWN / STALE + stale_secs + last
  tick) drawn from a snapshot cached by _refresh_monitor_health.
  Includes a "Re-poll" button that triggers a fresh HTTP round-trip.
  Detail box auto-expands so the operator sees the state without
  having to click "Show Details…". Click handler on the pill now
  invokes THIS instead of a silent re-poll.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
TAB = (REPO / "widgets" / "devices_tab.py").read_text()


# --- Apply progress: elapsed-time display ---------------------------


def test_show_apply_progress_starts_elapsed_ticker():
    idx = TAB.find("def _show_apply_progress(")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 3000]
    assert "v0.5.248 (audit U apply-elapsed)" in body
    # Records the start time so subsequent ticks can compute elapsed.
    assert "self._apply_progress_started_at = _t.monotonic()" in body
    # Creates a 1Hz QTimer.
    assert "self._apply_progress_ticker = QTimer(self)" in body
    assert "self._apply_progress_ticker.setInterval(1000)" in body
    assert "self._apply_progress_ticker.timeout.connect(self._on_apply_elapsed_tick)" in body


def test_elapsed_tick_handler_defined_and_writes_label():
    """The 1Hz callback exists and computes elapsed = now - started_at,
    then writes 'Applying N/M (Ss)' to the label."""
    assert "def _on_apply_elapsed_tick(self)" in TAB
    idx = TAB.find("def _on_apply_elapsed_tick(self)")
    body = TAB[idx:idx + 2000]
    assert "_elapsed = int(_t.monotonic() - getattr(self, \"_apply_progress_started_at\"" in body
    assert 'f"Applying {_done}/{_total} ({_elapsed}s)"' in body


def test_per_device_tick_also_shows_elapsed():
    """When a device completes, _tick_apply_progress must ALSO
    write the elapsed-time suffix so the label doesn't briefly
    flicker back to the no-elapsed form."""
    idx = TAB.find("def _tick_apply_progress(self)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 2000]
    assert "v0.5.248" in body
    assert "_elapsed = int(_t.monotonic()" in body
    assert '({_elapsed}s)' in body


def test_hide_apply_progress_stops_ticker():
    """Stop the ticker when the apply batch finishes — otherwise
    it keeps firing forever, repainting a hidden label."""
    idx = TAB.find("def _hide_apply_progress(self)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 1500]
    assert "self._apply_progress_ticker.stop()" in body


def test_elapsed_ticker_bails_when_bar_hidden():
    """Safety: if the ticker fires after the bar is already hidden,
    it stops itself and does NOT try to repaint."""
    idx = TAB.find("def _on_apply_elapsed_tick(self)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 2000]
    assert "if not self._apply_progress_bar.isVisible():" in body
    assert "self._apply_progress_ticker.stop()" in body


# --- Monitor pill: click opens details dialog -----------------------


def test_monitor_pill_click_opens_details_dialog():
    """Pre-fix, click just fired _refresh_monitor_health silently.
    Now it opens the details dialog (which offers a Re-poll button)."""
    idx = TAB.find('self._monitor_health_label.setCursor(Qt.PointingHandCursor)')
    body = TAB[idx:idx + 1500]
    assert "v0.5.248 (audit U monitor-details)" in body
    assert "self._show_monitor_health_details()" in body


def test_show_monitor_health_details_defined():
    assert "def _show_monitor_health_details(self)" in TAB
    idx = TAB.find("def _show_monitor_health_details(self)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 4000]
    # Uses cached snapshot from _refresh_monitor_health.
    assert '_snap = getattr(self, "_monitor_health_last_snapshot", None)' in body
    # Renders per-monitor state.
    assert '"✗ DOWN"' in body
    assert '⚠ STALE' in body
    assert '"✓ OK"' in body
    # QMessageBox with a Re-poll button.
    assert "QMessageBox as _QMB" in body
    assert 'Re-poll' in body
    # Re-poll button click triggers a fresh HTTP round-trip.
    assert "self._refresh_monitor_health()" in body


def test_snapshot_cached_by_refresh_handler():
    """The refresh handler must populate _monitor_health_last_snapshot
    with parsed offenders + raw payload so details opens instantly."""
    idx = TAB.find("v0.5.248: cache the raw snapshot")
    assert idx > 0
    body = TAB[idx:idx + 1500]
    assert "self._monitor_health_last_snapshot = {" in body
    for _k in ('"overall_ok"', '"monitors"', '"offenders"', '"raw"'):
        assert _k in body


def test_pill_tooltip_updated_to_say_click_for_details():
    """Tooltip should tell the operator that click reveals details."""
    idx = TAB.find('self._monitor_health_last_snapshot = {')
    body = TAB[idx:idx + 2000]
    assert "Click for details." in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 248)
