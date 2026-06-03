"""Preflight findings bar — sits at the top of the Devices sub-tab.

Backend landed in 0.2.68; this is the GUI front end (0.2.70).

Layout: a thin horizontal strip showing colour-coded pills for the
finding counts ("● 2 errors · ● 1 warning · ● 5 OK") plus a "Details…"
button that opens a modal table with every finding. Polls
``/api/preflight/check`` on a slow timer (60 s) so the bar reflects
edits without requiring an explicit Apply, and is also driven by
external triggers (the Devices tab can call ``refresh()`` after an
Apply succeeds).

Defensively quiet: any HTTP failure leaves the previous pill values
visible and prints a debug log line — never a modal alert. Operators
shouldn't see "preflight server unreachable" pop-ups every minute on
a flaky link.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import requests
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)


logger = logging.getLogger(__name__)


# Poll cadence — 60 s is slow enough not to swamp the server but fast
# enough that an edit-and-wait operator sees the bar respond.
DEFAULT_POLL_INTERVAL_MS = 60_000


class PreflightBar(QFrame):
    """Self-contained widget — give it a way to resolve the server URL
    and it polls preflight on its own timer. Auto-stops the timer when
    hidden so it doesn't bang on offline servers in the background.

    :param server_url_provider: callable returning the current server
        URL (e.g. ``main_window.get_server_url``). Called on every
        refresh so the bar follows server selection changes without
        needing a signal connection.
    :param poll_interval_ms: how often to poll automatically.
        Set to 0 to disable auto-polling (tests + external-driven mode).
    """

    # v0.2.78: emitted on every successful refresh so the Devices
    # table can mirror per-device dots. Payload is the by_device dict
    # from the server: {device_name: {error: n, warning: n, ok: n}}.
    by_device_updated = pyqtSignal(dict)

    def __init__(self,
                 server_url_provider: Callable[[], Optional[str]],
                 parent: Optional[QWidget] = None,
                 *, poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS):
        super().__init__(parent)
        self._get_server_url = server_url_provider
        self._poll_interval_ms = int(poll_interval_ms)
        self._findings: List[Dict[str, Any]] = []
        # v0.2.78: cache of the most recent by_device snapshot so a
        # Devices-tab refresh can ask for it (e.g. after a table
        # rebuild) without waiting for the next signal emit.
        self._by_device: Dict[str, Dict[str, int]] = {}
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_interval_ms or 60_000)
        self._timer.timeout.connect(self.refresh)
        if self._poll_interval_ms > 0:
            self._timer.start()
        # Kick a first refresh shortly after construction so the bar
        # shows real numbers instead of placeholder dashes — singleShot
        # so it doesn't block the GUI startup.
        QTimer.singleShot(300, self.refresh)

    # ─────────────────────────────────────────────────────────── UI
    def _build_ui(self) -> None:
        self.setObjectName("preflightBar")
        self.setFrameShape(QFrame.NoFrame)
        # Default to neutral slate — `_apply_summary` recolours.
        self._set_bar_style(border_color="#e2e8f0",
                            background="#f8fafc")
        bar = QHBoxLayout(self)
        # v0.3.11: tightened vertical margins (4→2) so the bar consumes
        # less of the parent tab's vertical real estate. The Devices
        # tab was reporting only one table row visible because preflight
        # + filter chrome was eating ~65px combined.
        bar.setContentsMargins(8, 2, 8, 2)
        bar.setSpacing(8)
        # Keep a reference so callers can `add_inline_widget()` to fold
        # tab-scoped chrome (e.g. the Devices filter input) onto this
        # row instead of stacking another row beneath it.
        self._bar_layout = bar

        title = QLabel("Preflight:")
        title.setStyleSheet("color: #475569; font-size: 11px; "
                             "font-weight: 600;")
        bar.addWidget(title)

        # Three pills, all built the same way for visual consistency.
        self._error_pill   = _make_pill("—", _RED)
        self._warning_pill = _make_pill("—", _AMBER)
        self._ok_pill      = _make_pill("—", _GREEN)
        for pill in (self._error_pill, self._warning_pill, self._ok_pill):
            bar.addWidget(pill)
        # v0.2.78: pills are clickable shortcuts to a level-filtered
        # Details dialog. Click red → see only errors; click amber →
        # only warnings. OK pill is non-interactive (clean findings
        # have no Details rows to filter).
        self._error_pill.setCursor(Qt.PointingHandCursor)
        self._warning_pill.setCursor(Qt.PointingHandCursor)
        self._error_pill.mousePressEvent = (
            lambda ev: self._show_details_dialog(level_filter="error")
        )
        self._warning_pill.mousePressEvent = (
            lambda ev: self._show_details_dialog(level_filter="warning")
        )

        bar.addStretch(1)

        self.details_btn = QPushButton("Details…")
        self.details_btn.setCursor(Qt.PointingHandCursor)
        self.details_btn.setStyleSheet(
            "QPushButton { background: #ffffff; color: #334155; "
            "border: 1px solid #cbd5e1; padding: 3px 12px; "
            "border-radius: 4px; font-size: 11px; } "
            "QPushButton:hover { background: #f1f5f9; }"
        )
        self.details_btn.clicked.connect(self._show_details_dialog)
        self.details_btn.setEnabled(False)
        bar.addWidget(self.details_btn)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setToolTip("Re-run preflight checks now")
        self.refresh_btn.setStyleSheet(self.details_btn.styleSheet())
        self.refresh_btn.setFixedWidth(28)
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)

    def _set_bar_style(self, *, border_color: str, background: str) -> None:
        self.setStyleSheet(
            f"QFrame#preflightBar {{"
            f"  background: {background};"
            f"  border: 1px solid {border_color};"
            f"  border-radius: 6px;"
            f"}}"
        )

    # ─────────────────────────────────────────────── public API
    def refresh(self) -> None:
        """Fetch /api/preflight/check and update the pills + cached
        findings. Safe to call from any context — never raises."""
        url = self._get_server_url() or ""
        if not url:
            # No server selected yet. Leave the bar in its dash state
            # rather than zeroing the counts (the operator hasn't even
            # had a chance to misconfigure anything).
            return
        try:
            r = requests.get(
                f"{url.rstrip('/')}/api/preflight/check",
                headers=_auth_headers(), timeout=5,
            )
        except Exception as exc:
            logger.debug(f"[PREFLIGHT BAR] fetch failed: {exc}")
            return
        if r.status_code != 200:
            logger.debug(f"[PREFLIGHT BAR] HTTP {r.status_code}")
            return
        try:
            payload = r.json() or {}
        except Exception:
            return
        self._apply_summary(payload)

    def stop(self) -> None:
        """Stop the auto-refresh timer (cleanup on app close)."""
        try:
            self._timer.stop()
        except Exception:
            pass

    # ─────────────────────────────────────────────── internals
    def _apply_summary(self, payload: Dict[str, Any]) -> None:
        summary  = payload.get("summary") or {}
        findings = payload.get("findings") or []
        n_err  = int(summary.get("error", 0))
        n_warn = int(summary.get("warning", 0))
        n_ok   = int(summary.get("ok", 0))

        _set_pill_text(self._error_pill,
                       f"● {n_err} error{'' if n_err == 1 else 's'}",
                       muted=(n_err == 0), palette=_RED)
        _set_pill_text(self._warning_pill,
                       f"● {n_warn} warning{'' if n_warn == 1 else 's'}",
                       muted=(n_warn == 0), palette=_AMBER)
        _set_pill_text(self._ok_pill,
                       f"● {n_ok} OK",
                       muted=(n_ok == 0), palette=_GREEN)

        # Tint the bar background by worst severity. Errors > warnings >
        # neutral. Subtle — the bar shouldn't drown out the table.
        if n_err > 0:
            self._set_bar_style(border_color="#fca5a5", background="#fef2f2")
        elif n_warn > 0:
            self._set_bar_style(border_color="#fcd34d", background="#fffbeb")
        else:
            self._set_bar_style(border_color="#bbf7d0", background="#f0fdf4")

        self._findings = list(findings)
        self.details_btn.setEnabled(bool(findings))

        # v0.2.78: snapshot by_device + notify subscribers (the Devices
        # tab uses this to paint per-row dots). The cache lets a late
        # subscriber pull the current state via current_by_device()
        # without waiting for the next refresh.
        by_device = payload.get("by_device") or {}
        if isinstance(by_device, dict):
            self._by_device = dict(by_device)
            try:
                self.by_device_updated.emit(dict(self._by_device))
            except Exception:
                pass

    def _show_details_dialog(self, level_filter: Optional[str] = None) -> None:
        if not self._findings:
            return
        PreflightDetailsDialog(
            self._findings, parent=self, level_filter=level_filter,
        ).exec_()

    def current_by_device(self) -> Dict[str, Dict[str, int]]:
        """Snapshot of the last by_device payload — for late subscribers
        (e.g. Devices-table rebuild) that need the current state without
        waiting for the next refresh.

        Returns a DEEP copy so a caller mutating the result (rare but
        possible during dot painting) can't poison the bar's cache.
        """
        import copy
        return copy.deepcopy(self._by_device)

    def add_inline_widget(self, widget: QWidget, *, stretch: int = 0) -> None:
        """v0.3.11: insert a host-supplied widget into the bar's row,
        positioned between the pills' trailing stretch and the
        Details… button.

        Lets the parent tab piggy-back tab-scoped chrome (the Devices
        tab uses this for its filter input) onto the preflight bar
        instead of stacking another row beneath it. Saves ~28 px of
        vertical chrome on the Devices tab — operators were reporting
        only one device row visible before this change.

        :param widget: any QWidget; reparented to the bar.
        :param stretch: layout stretch factor — pass 1 to let the
            widget consume the bar's free horizontal space.
        """
        idx = self._bar_layout.indexOf(self.details_btn)
        if idx < 0:  # defensive — bar wasn't built normally
            self._bar_layout.addWidget(widget, stretch)
            return
        self._bar_layout.insertWidget(idx, widget, stretch)


class PreflightDetailsDialog(QDialog):
    """Modal listing every finding — sortable + color-coded by level."""

    LEVEL_COLORS = {"error": "#b91c1c", "warning": "#b45309"}

    def __init__(self, findings: List[Dict[str, Any]],
                 parent: Optional[QWidget] = None,
                 *, level_filter: Optional[str] = None):
        super().__init__(parent)
        # v0.2.78: optional level filter — populated by pill-click on the
        # bar. None = show everything; "error"/"warning" = subset only.
        # Title reflects the filter so the operator knows what they're
        # looking at.
        if level_filter:
            findings = [f for f in findings
                        if str(f.get("level", "")).strip().lower()
                        == level_filter.lower()]
            self.setWindowTitle(
                f"Preflight findings — {level_filter}s only"
            )
        else:
            self.setWindowTitle("Preflight findings")
        self.setMinimumWidth(720)
        self.resize(820, 460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        hint_text = (
            "These are pre-Apply sanity checks — issues that will keep "
            "BGP from forming, EVPN routes from advertising, or "
            "forwarding from working. Apply still runs whether you "
            "address them or not."
        )
        if level_filter:
            hint_text = (
                f"Filtered to {level_filter}s only — close and reopen "
                f"from the Details… button to see every finding.\n\n"
                + hint_text
            )
        hint = QLabel(hint_text)
        hint.setStyleSheet("color: #475569; font-size: 11px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.table = QTableWidget(len(findings), 5)
        self.table.setHorizontalHeaderLabels(
            ["Level", "Code", "Device", "Interface", "Message"]
        )
        self.table.verticalHeader().setVisible(False)
        # Header click sorts the column — operators routinely want to
        # group by Device (all R1's findings together) or by Level (all
        # errors first). Sorting MUST be off while we populate or the
        # rows land in the wrong cells; we re-enable after the loop.
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSortIndicatorShown(True)
        for row, f in enumerate(findings):
            level = str(f.get("level", "")).strip()
            level_item = QTableWidgetItem(level.upper())
            from PyQt5.QtGui import QColor
            color = self.LEVEL_COLORS.get(level, "#475569")
            level_item.setForeground(QColor(color))
            self.table.setItem(row, 0, level_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(f.get("code", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(f.get("device_name", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(str(f.get("interface") or "—")))
            self.table.setItem(row, 4, QTableWidgetItem(str(f.get("message", ""))))
        # All rows populated — safe to turn header-click sorting on now.
        # Explicitly sort by Level ascending so ERROR < WARNING
        # alphabetically puts errors on top (operators' first concern);
        # the implicit Qt default is descending and would land WARNING
        # before ERROR which is the wrong instinct.
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        outer.addWidget(self.table, 1)

        # Keep a reference to the raw findings list so Export… can
        # write the original payload, not whatever we coerced into the
        # table (level case-folded, missing iface rendered as em-dash,
        # etc.).
        self._findings: List[Dict[str, Any]] = list(findings)

        close_row = QHBoxLayout()
        export_csv_btn = QPushButton("Export CSV…")
        export_csv_btn.setToolTip(
            "Save the visible findings to a CSV file — paste into a "
            "ticket / spreadsheet without screenshotting."
        )
        export_csv_btn.clicked.connect(self._export_csv)
        close_row.addWidget(export_csv_btn)

        export_json_btn = QPushButton("Export JSON…")
        export_json_btn.setToolTip(
            "Save the raw findings list as JSON — for scripts that "
            "consume the preflight payload programmatically."
        )
        export_json_btn.clicked.connect(self._export_json)
        close_row.addWidget(export_json_btn)

        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        outer.addLayout(close_row)

    # ─────────────────────────────────────────────── export handlers
    def _default_filename(self, ext: str) -> str:
        """Timestamped default filename so an operator doesn't have to
        type one — date sorts naturally and disambiguates re-exports."""
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"preflight_findings_{stamp}.{ext}"

    def _export_csv(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export preflight findings (CSV)",
            self._default_filename("csv"),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["level", "code", "device_name",
                            "interface", "message"])
                for f in self._findings:
                    w.writerow([
                        str(f.get("level", "")),
                        str(f.get("code", "")),
                        str(f.get("device_name", "")),
                        str(f.get("interface") or ""),
                        str(f.get("message", "")),
                    ])
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _export_json(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export preflight findings (JSON)",
            self._default_filename("json"),
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._findings, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))


# ─────────────────────────────────────────────────────────── helpers
_RED = {
    "bg_active": "#fef2f2", "fg_active": "#b91c1c",
    "border_active": "#fca5a5",
    "bg_muted": "#f1f5f9",  "fg_muted": "#94a3b8",
    "border_muted": "#e2e8f0",
}
_AMBER = {
    "bg_active": "#fffbeb", "fg_active": "#b45309",
    "border_active": "#fcd34d",
    "bg_muted": "#f1f5f9",  "fg_muted": "#94a3b8",
    "border_muted": "#e2e8f0",
}
_GREEN = {
    "bg_active": "#f0fdf4", "fg_active": "#166534",
    "border_active": "#bbf7d0",
    "bg_muted": "#f1f5f9",  "fg_muted": "#94a3b8",
    "border_muted": "#e2e8f0",
}


def _make_pill(text: str, palette: Dict[str, str]) -> QLabel:
    """Build a coloured pill QLabel — starts in muted state."""
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    _set_pill_text(lbl, text, muted=True, palette=palette)
    return lbl


def _set_pill_text(lbl: QLabel, text: str, *,
                   muted: bool, palette: Dict[str, str]) -> None:
    bg, fg, border = (palette["bg_muted"], palette["fg_muted"],
                      palette["border_muted"]) if muted else (
        palette["bg_active"], palette["fg_active"], palette["border_active"])
    lbl.setText(text)
    lbl.setStyleSheet(
        f"background: {bg}; color: {fg}; border: 1px solid {border}; "
        f"padding: 2px 10px; border-radius: 10px; font-size: 11px;"
    )


def _auth_headers() -> Dict[str, str]:
    tok = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def kick_refresh(host: Any, attr: str = "preflight_bar") -> bool:
    """Trigger an immediate preflight refresh on ``host.<attr>``.

    Defensively no-ops when the bar isn't present (Devices tab failed
    to wire it up) or when the refresh itself raises (the bar already
    swallows its own HTTP errors, but belt-and-braces). Returns True
    iff ``refresh()`` was actually invoked — handy for tests, ignored
    by callers.

    Used by the Devices tab's Apply success paths to update the bar
    immediately after a config push instead of waiting up to 60 s for
    the next auto-poll. Operators care most about the post-Apply
    moment — "did the change I just made introduce a new finding?" —
    so the bar repainting on demand there is worth the one line of
    extra wiring.
    """
    bar = getattr(host, attr, None)
    if bar is None:
        return False
    refresh = getattr(bar, "refresh", None)
    if not callable(refresh):
        return False
    try:
        refresh()
        return True
    except Exception as exc:
        logger.debug(f"[PREFLIGHT BAR] kick_refresh suppressed: {exc}")
        return False
