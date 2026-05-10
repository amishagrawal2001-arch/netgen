# stream_control.py
import logging

logger = logging.getLogger(__name__)

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QComboBox, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QMessageBox, QDialog, QLabel
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtCore import QTimer
import uuid
import re
import requests

from widgets.stream_dialog import AddStreamDialog
from utils.qicon_loader import qicon, r_icon


class TrafficGenClientStreamControl:
    def __init__(self):
        pass

    def setup_stream_section(self, parent_widget):
        layout = QVBoxLayout(parent_widget)
        layout.setContentsMargins(4, 4, 4, 4)  # Balanced padding to match left side (TGEN)
        layout.setSpacing(10)  # Consistent spacing between elements

        # --- Stream Table ---
        self.stream_table = QTableWidget()
        # Cap icon size so the Status column's dot doesn't render as a
        # 22+px square block on high-DPI Macs. Matches the 12×12 pixmap
        # the per-cell setIcon callers explicitly produce.
        self.stream_table.setIconSize(QSize(14, 14))
        stream_column_labels = [
            "Status", "Interface", "Name", "Enabled", "Details", "Frame Type",
            "Min Size", "Max Size", "Fixed Size", "L1", "VLAN", "L2", "L3", "L4", "RX Port",
            "Flow Tracking",
        ]
        self.stream_table.setColumnCount(len(stream_column_labels))
        self.stream_table.setHorizontalHeaderLabels(stream_column_labels)
        # Hover tooltip on each column header — useful when the column gets narrow.
        for col, label in enumerate(stream_column_labels):
            header_item = self.stream_table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setToolTip(label)
        self.stream_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.stream_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)

        # ✅ ensure multi-select starts/stops work even if user clicks cells
        self.stream_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stream_table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Table styling for professional appearance (muted color scheme)
        self.stream_table.setAlternatingRowColors(True)  # Alternating row colors for better readability
        header = self.stream_table.horizontalHeader()
        header.setDefaultSectionSize(25)  # default per-column width hint
        header.setHighlightSections(False)  # don't highlight header sections on click
        # Fix the header bar height so font-metric quirks on different
        # platforms can't push it taller than the styled padding implies.
        # 22px = 11pt label + 3px top + 3px bottom + 5px line-height room.
        header.setFixedHeight(22)

        # Stream table styling — bumped for visibility, matches the
        # palette used in the stats dock and server pane:
        # - body 11px → 13px, padding 3 → 5/8 (more breathing room)
        # - selection blue brightened (#5b7fa8 → #2563eb) so the active
        #   row pops on screenshots / projector demos
        # - header bg/contrast strengthened
        self.stream_table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f5f7fa;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                font-size: 13px;
                outline: none;
                color: #111827;
                gridline-color: #e5e7eb;
            }
            QTableWidget::item {
                padding: 5px 8px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QTableWidget::item:hover:!selected {
                background-color: #eef2f7;
            }
            QTableWidget::item:selected:hover {
                background-color: #1d4ed8;
            }
            QHeaderView::section {
                background-color: #e5e7eb;
                padding: 3px 8px;
                border: 1px solid #cbd5e1;
                border-left: none;
                border-top: none;
                font-weight: 700;
                font-size: 11px;
                color: #1f2937;
                letter-spacing: 0.3px;
            }
            QHeaderView::section:first {
                border-left: 1px solid #cbd5e1;
            }
        """)

        self.stream_table.itemChanged.connect(self.handle_inline_edit)
        layout.addWidget(self.stream_table)

        # Empty-state overlay shown when there are no streams to display.
        # Parented to the table viewport so it floats above the empty grid.
        self._stream_empty_label = QLabel(
            "No streams configured.\nSelect a port on the left and click ➕ to add a stream.",
            self.stream_table.viewport(),
        )
        self._stream_empty_label.setAlignment(Qt.AlignCenter)
        self._stream_empty_label.setStyleSheet(
            "QLabel { color: #4b5563; font-size: 14px; padding: 32px; "
            "font-weight: 500; background-color: transparent; }"
        )
        self._stream_empty_label.hide()
        # Reposition the overlay whenever the viewport resizes
        self.stream_table.viewport().installEventFilter(self)

        # --- All Buttons in Same Row (Action buttons on left, Control buttons centered) ---
        # Single uniform palette — same border, same hover, same size for all
        # buttons in both action rows. The icons carry the semantic (▶ start,
        # ⬛ stop, etc.); colored fills were too noisy and looked unprofessional
        # next to the muted TGEN row. Only Apply gets a subtle blue accent
        # because it's the primary "commit" action and benefits from being
        # visually distinct from configuration and runtime-control buttons.
        button_layout = QHBoxLayout()
        button_layout.setAlignment(Qt.AlignLeft)
        button_layout.setSpacing(6)
        button_layout.setContentsMargins(0, 8, 0, 0)

        # Universal button style — same as the TGEN section's _tgen_btn so
        # both rows visually match. Neutral white background, thin gray
        # border, gray hover. No fill colors.
        BTN_BASE = (
            "QPushButton {"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }"
            "QPushButton:pressed { background-color: #e2e8f0; }"
            "QPushButton:disabled { background-color: #f9fafb; border-color: #e5e7eb; }"
        )

        # Apply gets a subtle blue accent — same neutral baseline, but the
        # border is blue and the hover deepens slightly. Reads as "primary"
        # without shouting like the previous all-blue fill did.
        BTN_APPLY = (
            "QPushButton {"
            "  border: 1px solid #2563eb;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #eff6ff; border-color: #1d4ed8; }"
            "QPushButton:pressed { background-color: #dbeafe; }"
        )

        # 34x30 with 18px icons — aligns with the TGEN section's icon row.
        BTN_W, BTN_H, ICON_PX = 34, 30, 18

        def _action_btn(icon_name, tooltip, slot, style=None):
            b = QPushButton()
            b.setIcon(QIcon(r_icon(f"icons/{icon_name}")))
            b.setIconSize(QSize(ICON_PX, ICON_PX))
            b.setFixedSize(BTN_W, BTN_H)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(style or BTN_BASE)
            b.clicked.connect(slot)
            return b

        # Configuration buttons (left): neutral border, gray hover.
        add_stream_button = _action_btn("add.png", "Add Stream", self.open_add_stream_dialog)
        button_layout.addWidget(add_stream_button)

        edit_stream_button = _action_btn("edit.png", "Edit Stream", self.edit_selected_stream)
        button_layout.addWidget(edit_stream_button)

        remove_stream_button = _action_btn("Trash.png", "Delete Stream", self.remove_selected_stream)
        button_layout.addWidget(remove_stream_button)

        # Vertical divider between configuration and runtime control groups
        # so the user reads "edit then control" rather than one undifferentiated
        # row of icons.
        sep = QLabel()
        sep.setFixedSize(1, BTN_H)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 0 6px;")
        button_layout.addSpacing(4)
        button_layout.addWidget(sep)
        button_layout.addSpacing(4)

        # Add stretch so control buttons don't crowd the configuration ones.
        button_layout.addStretch(1)

        # Runtime control buttons (centered) — same neutral baseline as the
        # configuration trio. Semantic comes from the icons (▶ vs ⬛), not
        # from button colors.
        self.start_stream_button = _action_btn(
            "start.png", "Start Selected streams", self.start_stream,
        )
        button_layout.addWidget(self.start_stream_button)

        self.stop_stream_button = _action_btn(
            "stop.png", "Stop Selected streams", self.stop_stream,
        )
        button_layout.addWidget(self.stop_stream_button)

        # Start/Stop ALL toggle — same neutral baseline as the rest. The
        # icon swap (start-all vs stop-all) tells the user what state we're in.
        self.all_streams_toggle_btn = QPushButton()
        self.all_streams_toggle_btn.setIconSize(QSize(ICON_PX, ICON_PX))
        self.all_streams_toggle_btn.setFixedSize(BTN_W + 6, BTN_H)
        self.all_streams_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.all_streams_toggle_btn.setToolTip("Start ALL enabled streams")
        self.all_streams_toggle_btn.setStyleSheet(BTN_BASE)
        self.all_streams_toggle_btn.clicked.connect(self._toggle_all_streams)
        # Both states use the same neutral style — kept as separate names
        # so update_all_streams_toggle_ui doesn't need to change shape.
        self._all_btn_start_style = BTN_BASE
        self._all_btn_stop_style = BTN_BASE

        # 👇 set a default icon right away (so it's visible at first paint)
        _default_icon = QIcon(r_icon("icons/startallstream.png"))
        if _default_icon.isNull():
            # fallback to text if the file isn't found (helps during dev)
            self.all_streams_toggle_btn.setText("Start All")
        else:
            self.all_streams_toggle_btn.setIcon(_default_icon)

        button_layout.addWidget(self.all_streams_toggle_btn)

        # Let the UI settle, then compute the real state (running/not running)
        QTimer.singleShot(0, self.update_all_streams_toggle_ui)

        self.apply_stream_button = _action_btn(
            "apply.png",
            # Clearer description of what Apply actually does (audit found the
            # previous tooltip only described one of three branches).
            "Sync your edits to the server. Restarts streams that are currently "
            "running and still enabled; stops streams you've just disabled.",
            self.apply_stream,
            BTN_APPLY,
        )
        # Track baseline style so the dirty-edit highlight can be reverted cleanly.
        self._apply_button_default_style = self.apply_stream_button.styleSheet()
        button_layout.addWidget(self.apply_stream_button)

        # Add stretch before search box
        button_layout.addStretch(1)

        # Search box (right side) — debounced so each keystroke doesn't trigger a
        # full table rebuild (audit flagged this as a perf wart with 100+ streams).
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search...")
        self.search_box.setFixedWidth(200)
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(200)
        self._search_debounce_timer.timeout.connect(self.update_stream_table)
        self.search_box.returnPressed.connect(self.update_stream_table)
        self.search_box.textChanged.connect(lambda _t: self._search_debounce_timer.start())
        button_layout.addWidget(self.search_box)

        # Neutral clear button — the previous "❌" emoji rendered bright red and
        # read as an error/destructive cue rather than a benign clear control.
        clear_search_btn = QPushButton("✕")
        clear_search_btn.setFixedWidth(30)
        clear_search_btn.setToolTip("Clear search")
        clear_search_btn.setStyleSheet(
            "QPushButton { color: #6b7280; font-size: 13px; }"
            "QPushButton:hover { color: #1f2937; }"
        )
        clear_search_btn.clicked.connect(lambda: self.search_box.setText(""))
        button_layout.addWidget(clear_search_btn)

        layout.addLayout(button_layout)

    def eventFilter(self, watched, event):
        # Keep the empty-state label centred over the table viewport.
        # Returning False lets the event continue to its normal handler.
        if (
            hasattr(self, "_stream_empty_label")
            and hasattr(self, "stream_table")
            and watched is self.stream_table.viewport()
            and event.type() == event.Resize
        ):
            self._stream_empty_label.resize(watched.size())
        return False

    def update_stream_empty_state(self):
        """Show or hide the empty-state placeholder based on the table row count."""
        if not hasattr(self, "_stream_empty_label"):
            return
        if self.stream_table.rowCount() == 0:
            self._stream_empty_label.resize(self.stream_table.viewport().size())
            self._stream_empty_label.raise_()
            self._stream_empty_label.show()
        else:
            self._stream_empty_label.hide()

    # ---------- dirty-edit tracking (#8) ----------

    def _dirty_streams(self) -> set:
        """Set of stream_ids with unapplied inline edits (lazy-init for mixin safety)."""
        if not hasattr(self, "_dirty_stream_ids"):
            self._dirty_stream_ids = set()
        return self._dirty_stream_ids

    def mark_stream_dirty(self, stream_id):
        """Flag a stream as having unapplied edits and refresh the Apply button cue."""
        if not stream_id:
            return
        self._dirty_streams().add(stream_id)
        self._refresh_apply_button_state()

    def clear_dirty_streams(self):
        """Called from apply_stream after a successful sync."""
        self._dirty_streams().clear()
        self._refresh_apply_button_state()

    def _refresh_apply_button_state(self):
        """Tint the Apply button when there are unapplied edits."""
        btn = getattr(self, "apply_stream_button", None)
        if btn is None:
            return
        if self._dirty_streams():
            # "Edits pending" — amber border (matches the minimal-accent
            # pattern used elsewhere in the action bar) + a faint amber
            # tint on hover. Border-radius / padding match the baseline so
            # toggling clean<->dirty doesn't shift the button size.
            btn.setStyleSheet(
                "QPushButton { background-color: #ffffff; border: 1px solid #d97706; "
                "border-radius: 5px; padding: 0px; }"
                "QPushButton:hover { background-color: #fffbeb; border-color: #b45309; }"
                "QPushButton:pressed { background-color: #fef3c7; }"
            )
            btn.setToolTip(
                f"You have {len(self._dirty_streams())} unapplied edit(s). "
                "Click to sync them to the server."
            )
        else:
            btn.setStyleSheet(getattr(self, "_apply_button_default_style", ""))
            btn.setToolTip(
                "Sync your edits to the server. Restarts streams that are currently "
                "running and still enabled; stops streams you've just disabled."
            )

    # NOTE: an old setup_stream_start_stop_buttons() method used to live
    # here, hand-rolling a Start/Stop/Apply button row. It was orphaned
    # when setup_stream_section() took over the styled action bar, and
    # if anyone ever called it again it would silently re-bind
    # self.start_stream_button / .stop_stream_button / .apply_stream_button
    # to fresh unstyled QPushButtons, clobbering the action bar's icons
    # and the Apply button's dirty-state tracking. Removed in audit
    # cleanup batch (LOW #12).

    # ---------- table edit handlers ----------
    def handle_inline_edit(self, item):
        """
        Reliable inline edit handler:
          - Locates the stream by stream_id stored on the Name cell (col 2, Qt.UserRole).
          - Falls back to (port, name) if stream_id isn't present.
          - Updates self.streams first (source of truth), then normalizes the cell UI.
          - Avoids re-entrancy with QSignalBlocker and self._populating_table flag.
        """
        # Ignore programmatic changes during table population
        if getattr(self, "_populating_table", False):
            logger.warning(
                f"[INLINE EDIT] Dropped edit at row={item.row()} col={item.column()} "
                f"text={item.text()!r} — table is being populated"
            )
            return

        from PyQt5.QtCore import QSignalBlocker

        row = item.row()
        col = item.column()

        # Retrieve the Name cell (col 2) where we stash stream_id
        name_item = self.stream_table.item(row, 2)
        if not name_item:
            logger.warning(f"[INLINE EDIT] No name_item at row={row}, dropping")
            return

        stream_id = name_item.data(Qt.UserRole)

        # Locate the stream
        port = None
        stream = None
        if stream_id:
            # Preferred: lookup by stream_id
            for p, lst in getattr(self, "streams", {}).items():
                for s in lst:
                    if s.get("stream_id") == stream_id:
                        port, stream = p, s
                        break
                if stream:
                    break
        if not stream:
            # Fallback: use (port, name) — but the table's port column may show
            # "↳" for continuation rows (visual grouping), so resolve it through
            # the canonical port-key normalizer rather than naive equality.
            port_item = self.stream_table.item(row, 1)
            if not port_item:
                logger.warning(f"[INLINE EDIT] row={row} stream_id={stream_id!r} not found, no port_item either")
                return
            from traffic_client.stream_logic import find_port_key
            port_text = port_item.text().strip()
            port = find_port_key(self.streams, port_text) or port_text
            current_name = name_item.text().strip()
            for s in self.streams.get(port, []):
                if s.get("protocol_selection", {}).get("name") == current_name or s.get("name") == current_name:
                    stream = s
                    break
            if not stream:
                logger.warning(
                    f"[INLINE EDIT] Could not locate stream at row={row} col={col} "
                    f"stream_id={stream_id!r} port={port_text!r} resolved_port={port!r} "
                    f"name={current_name!r}. self.streams keys: {list(self.streams.keys())}"
                )
                return

        ps = stream.setdefault("protocol_selection", {})

        # --- Column-specific updates ---
        if col == 2:
            # Name
            new_name = item.text().strip()
            if not new_name:
                # Revert to previous name if empty
                prev = ps.get("name", stream.get("name", ""))
                with QSignalBlocker(self.stream_table):
                    item.setText(prev)
                logger.info(f"[INLINE EDIT] Empty name rejected, reverted to {prev!r}")
                return
            old_name = stream.get("name") or ps.get("name") or ""
            # Update model first
            ps["name"] = new_name
            stream["name"] = new_name
            # Normalize UI text (no-op for valid name, but keeps things consistent)
            with QSignalBlocker(self.stream_table):
                item.setText(new_name)
            logger.info(
                f"[INLINE EDIT] Renamed stream on {port!r}: {old_name!r} -> {new_name!r} "
                f"(stream_id={stream.get('stream_id')!r})"
            )

        elif col == 3:
            # Enabled (typed Yes/No if not a combo)
            raw = item.text().strip().lower()
            val = raw in ("yes", "true", "1", "on", "y")
            ps["enabled"] = val
            stream["enabled"] = val
            # Normalize UI
            with QSignalBlocker(self.stream_table):
                item.setText("Yes" if val else "No")

        elif col == 8:
            # Fixed Size (must be positive integer)
            text = item.text().strip()
            try:
                size = int(text)
                if size <= 0:
                    raise ValueError
            except Exception:
                prev = int(ps.get("frame_size") or stream.get("frame_size") or 64)
                with QSignalBlocker(self.stream_table):
                    item.setText(str(prev))
                QMessageBox.warning(self, "Invalid Input", "Frame size must be a positive integer.")
                return
            # Update model first
            ps["frame_size"] = str(size)
            stream["frame_size"] = str(size)
            # Normalize UI
            with QSignalBlocker(self.stream_table):
                item.setText(str(size))

        elif col == 15:
            # Flow Tracking (typed Yes/No if not a combo)
            raw = item.text().strip().lower()
            val = raw in ("yes", "true", "1", "on", "y")
            ps["flow_tracking_enabled"] = val
            stream["flow_tracking_enabled"] = val
            # Normalize UI. Audit LOW #15: previous code was
            # `QSignalBlocker(self, )` — the trailing comma + `self`
            # blocked signals on the parent widget (probably the
            # main window) instead of the stream_table, so a Flow
            # Tracking inline edit could re-trigger the table's
            # itemChanged handler and recurse. Fixed to match the
            # other branches of this method (col 2/3/8/15) which
            # all correctly block self.stream_table.
            with QSignalBlocker(self.stream_table):
                item.setText("Yes" if val else "No")

        else:
            # Non-editable/unsupported column; ignore
            return

        # Optional: persist & notify server without repainting the whole table
        if hasattr(self, "send_inline_update_to_server") and port:
            try:
                self.send_inline_update_to_server(port, stream)
            except Exception as e:
                logger.warning(f"send_inline_update_to_server failed: {e}")

        # Flag the row as having unapplied edits so the Apply button highlights.
        if hasattr(self, "mark_stream_dirty"):
            self.mark_stream_dirty(stream.get("stream_id"))

        # Session save removed - only save on explicit user action (Save Session menu or Apply button)



    def handle_flow_tracking_change(self, value, row, port=None):
        """
        Flow Tracking combo change handler.
        Keeps model and UI in sync and updates both protocol_selection and top-level keys.
        """
        from PyQt5.QtCore import QSignalBlocker

        # Normalize input to boolean
        val = str(value).strip().lower() in ("yes", "true", "1", "on", "y")

        # Get stream_id from Name cell (col 2)
        name_item = self.stream_table.item(row, 2)
        if not name_item:
            return
        stream_id = name_item.data(Qt.UserRole)

        # Locate stream by ID (preferred)
        stream = None
        resolved_port = None
        if stream_id:
            for p, lst in getattr(self, "streams", {}).items():
                for s in lst:
                    if s.get("stream_id") == stream_id:
                        stream = s
                        resolved_port = p
                        break
                if stream:
                    break

        # Fallback: locate by (port, name) if no/unknown ID
        if not stream:
            if port is None:
                port_item = self.stream_table.item(row, 1)
                if not port_item:
                    return
                resolved_port = port_item.text().strip()
            else:
                resolved_port = port
            current_name = name_item.text().strip()
            for s in self.streams.get(resolved_port, []):
                if s.get("protocol_selection", {}).get("name") == current_name:
                    stream = s
                    break
            if not stream:
                return

        # Update both protocol_selection and top-level flags
        ps = stream.setdefault("protocol_selection", {})
        ps["flow_tracking_enabled"] = val
        stream["flow_tracking_enabled"] = val

        # Normalize the combo text without re-triggering
        combo = self.stream_table.cellWidget(row, 15)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentText("Yes" if val else "No")
            combo.blockSignals(False)

        # Persist / notify if hooks exist
        if hasattr(self, "send_inline_update_to_server") and resolved_port:
            try:
                self.send_inline_update_to_server(resolved_port, stream)
            except Exception as e:
                logger.warning(f"send_inline_update_to_server failed: {e}")

        # Session save removed - only save on explicit user action (Save Session menu or Apply button)

    def handle_enabled_combo_change(self, value, row):
        """Handle a state change on the row's Enabled checkbox.

        Despite the legacy method name, the cell widget is now a QCheckBox; `value`
        is a Qt.CheckState int from QCheckBox.stateChanged. Older Yes/No string values
        are still tolerated so any straggling combo cells don't crash the handler.
        """
        interface_item = self.stream_table.item(row, 1)
        name_item = self.stream_table.item(row, 2)
        if not interface_item or not name_item:
            return

        # Resolve the row's port via the canonical normalizer + stream_id
        # stash. The previous code did `self.streams.get(port, [])` with
        # the raw cell text — which is the bare iface name like
        # "ens5np0", never the canonical "TG 0 - Port: ens5np0" key. So
        # the loop body never matched any stream, the model was never
        # updated, and the inline-update POST never fired. Audit MED #7.
        from traffic_client.stream_logic import find_port_key
        stream_name = name_item.text().strip()
        stream_id = name_item.data(Qt.UserRole)
        port = None
        if stream_id:
            for p, lst in (self.streams or {}).items():
                if any(s.get("stream_id") == stream_id for s in lst):
                    port = p
                    break
        if not port:
            port = find_port_key(self.streams, interface_item.text().strip())
        if not port:
            logger.warning(
                f"[ENABLED-TOGGLE] Could not resolve port for row={row} "
                f"name={stream_name!r}; click ignored"
            )
            return

        if isinstance(value, str):
            new_enabled = value.strip().lower() in ("yes", "true", "1")
        else:
            new_enabled = bool(value)  # Qt.Checked == 2, Qt.PartiallyChecked == 1, Qt.Unchecked == 0

        for stream in self.streams.get(port, []):
            if stream.get("name") == stream_name or stream.get("protocol_selection", {}).get("name") == stream_name:
                stream["enabled"] = new_enabled
                # Keep protocol_selection.enabled in sync — the server's
                # /traffic/start reads from there too, and a half-synced
                # state was the cause of "Apply doesn't pick up the
                # checkbox change" reports.
                stream.setdefault("protocol_selection", {})["enabled"] = new_enabled
                logger.info(f"Stream '{stream_name}' on {port} enabled set to {new_enabled}")
                self.send_inline_update_to_server(port, stream)
                if hasattr(self, "mark_stream_dirty"):
                    self.mark_stream_dirty(stream.get("stream_id"))
                break

    def update_rx_port(self, port, stream, new_rx):
        """Update rx_port value for the stream."""
        stream["rx_port"] = new_rx.strip()
        logger.info(f"Updated rx_port for stream '{stream.get('name')}' on {port} to {new_rx}")

    def update_stream_status(self, row, color):
        """Update the stream status icon for a specific row.

        Icon scaled to 12x12 — without explicit pixmap scaling Qt's
        default QTableWidget iconSize renders the dot as a square
        block on high-DPI displays. Matches the initial-render path
        in server_section.py (~line 660).
        """
        raw_icon = QIcon(r_icon(f"icons/{color}_dot.png"))
        status_item = QTableWidgetItem()
        status_item.setIcon(QIcon(raw_icon.pixmap(12, 12)))
        status_item.setFlags(Qt.ItemIsEnabled)  # read-only
        self.stream_table.setItem(row, 0, status_item)

    # ---------- copy/paste & CRUD ----------

    def _get_stream_by_port_and_name(self, port: str, stream_name: str):
        """Return the stream dict under `port` whose protocol_selection.name == stream_name."""
        for s in self.streams.get(port, []):
            if s.get("protocol_selection", {}).get("name") == stream_name:
                return s
        return None

    def _collect_selected_table_rows(self):
        """Return list of distinct integer row indices currently selected in the table."""
        # selectedRows() is already row-based due to SelectRows mode,
        # but make it robust if someone changes selection behavior later.
        rows = {idx.row() for idx in self.stream_table.selectionModel().selectedRows()}
        if not rows:
            # Fallback in case selection behavior changes to cells
            rows = {i.row() for i in self.stream_table.selectionModel().selectedIndexes()}
        return sorted(rows)

    def _next_global_str_number(self, used_numbers: set) -> int:
        """Find the next available integer for names 'str<N>' across ALL ports."""
        n = 1
        while n in used_numbers:
            n += 1
        return n

    def _gather_used_str_numbers(self) -> set:
        """Scan all stream names across all ports to collect used numbers for 'str<N>'."""
        used = set()
        for stream_list in self.streams.values():
            for s in stream_list:
                nm = s.get("protocol_selection", {}).get("name", "")
                m = re.fullmatch(r"str(\d+)", nm)
                if m:
                    try:
                        used.add(int(m.group(1)))
                    except ValueError:
                        pass
        return used





    def copy_selected_stream(self):
        rows = self._collect_selected_table_rows()
        if not rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more streams to copy.")
            return

        copied = []
        import copy
        for r in rows:
            iface_item = self.stream_table.item(r, 1)
            name_item = self.stream_table.item(r, 2)
            if not iface_item or not name_item:
                continue
            port = iface_item.text().strip()
            stream_name = name_item.text().strip()
            src = self._get_stream_by_port_and_name(port, stream_name)
            if src:
                c = copy.deepcopy(src)
                # ✅ strip any existing ids to avoid accidental reuse
                c.pop("stream_id", None)
                ps = c.get("protocol_selection", {})
                ps.pop("stream_id", None)
                copied.append(c)

        if not copied:
            QMessageBox.warning(self, "Copy Streams", "Unable to resolve the selected streams to copy.")
            return

        self.copied_streams = copied
        if len(copied) == 1:
            self.copied_stream = copied[0]
        else:
            if hasattr(self, "copied_stream"):
                delattr(self, "copied_stream")

        logger.info(f"[COPY] Prepared {len(self.copied_streams)} stream(s) for paste.")

    def paste_stream_to_interface(self):
        # Accept legacy single-copy clipboard if multi-copy is not present
        if not hasattr(self, 'copied_streams') or not self.copied_streams:
            if hasattr(self, 'copied_stream') and self.copied_stream:
                self.copied_streams = [self.copied_stream]
            else:
                QMessageBox.warning(self, "No Stream Copied", "Please copy one or more streams first.")
                return

        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a TG port to paste the stream(s).")
            return

        selected_item = selected_items[0]
        parent_item = selected_item.parent()
        if parent_item is None:
            QMessageBox.warning(self, "Invalid Selection", "Please select a TG port, not a server.")
            return

        # Properly define all names used below.
        # Server tree port items hold the bare iface name in text(0) (no
        # "Port:" prefix; see server_section.py update_server_tree). Other
        # paths in this codebase key self.streams as "TG N - Port: iface"
        # WITH the prefix (Add Stream, the streams DB schema, etc.). The
        # old code built `full_port_name = f"{tg_id} - {port_label}"`
        # which produced "TG 0 - enp13s0f0np0" — a NEW, mismatched key —
        # so pasted streams ended up orphaned from the per-port group
        # the Streams table and stats lookups expected. Rebuild the same
        # canonical "TG N - Port: iface" form Add Stream uses.
        tg_id = parent_item.text(0).strip()  # e.g., "TG 0"
        raw_port_text = selected_item.text(0).strip()
        # Strip any incidental "Port: " prefix so tx_port_name is just iface
        tx_port_name = raw_port_text.replace("Port: ", "").strip()
        # Strip optional bullet prefix that some tree builds add
        if tx_port_name.startswith("• ") or tx_port_name.startswith("● "):
            tx_port_name = tx_port_name[2:].strip()
        # Sanity-guard: reject the streams-table continuation marker. It
        # should never appear in the server tree, but guarding here means
        # a regression elsewhere can't silently create a "TG 0 - Port: ↳"
        # ghost key in self.streams.
        if not tx_port_name or tx_port_name == "↳":
            QMessageBox.warning(
                self, "Invalid Port",
                f"Could not resolve a port name from the selected tree item "
                f"(got {raw_port_text!r}). Please pick a port from the server "
                f"tree, not a stream row.",
            )
            return
        full_port_name = f"{tg_id} - Port: {tx_port_name}"

        if full_port_name not in self.streams:
            self.streams[full_port_name] = []

        # Global name allocator for str<N> and a local set to prevent same-op ID collisions
        used_numbers = self._gather_used_str_numbers()
        local_used_ids = set()

        import copy

        pasted_count = 0
        for src in self.copied_streams:
            dst = copy.deepcopy(src)

            # Strip any stale IDs in payload
            dst.pop("stream_id", None)
            if "protocol_selection" in dst:
                dst["protocol_selection"].pop("stream_id", None)

            # Allocate a new unique display name (str<N>) across all ports
            n = self._next_global_str_number(used_numbers)
            used_numbers.add(n)
            new_name = f"str{n}"

            ps = dst.setdefault("protocol_selection", {})
            ps["name"] = new_name
            ps["enabled"] = True

            # Set RX port to the full "TG X - Port: iface" label for consistency
            rx_full = f"{tg_id} - Port: {tx_port_name}"
            ps["rx_port"] = rx_full

            # Top-level mirrors
            dst["name"] = new_name
            dst["enabled"] = True
            dst["status"] = "stopped"
            dst["rx_port"] = rx_full

            # Allocate a new stream_id with local collision guard
            new_id = self._alloc_stream_id(extra_used=local_used_ids) if hasattr(self, "_alloc_stream_id") else str(
                uuid.uuid4())
            dst["stream_id"] = new_id
            local_used_ids.add(new_id)

            self.streams[full_port_name].append(dst)
            pasted_count += 1
            logger.info(f"[PASTE] '{new_name}' -> {full_port_name}")

        # Clean up legacy single-copy to avoid stale state
        if hasattr(self, "copied_stream"):
            delattr(self, "copied_stream")

        # Final safety sweep and UI refresh
        if hasattr(self, "ensure_unique_stream_ids"):
            self.ensure_unique_stream_ids()
        self.update_stream_table()
        QMessageBox.information(self, "Paste Complete", f"Pasted {pasted_count} stream(s) to {full_port_name}.")

    def _all_stream_ids(self) -> set:
        ids = set()
        for lst in getattr(self, "streams", {}).values():
            for s in lst:
                sid = s.get("stream_id")
                if sid:
                    ids.add(sid)
        return ids
    def _alloc_stream_id(self, extra_used: set = None) -> str:
        """
        Return a new UUID string not present in current streams nor in extra_used.
        extra_used is a per-operation reservation set (e.g., within one multi-paste).
        """
        import uuid
        existing = set(self._all_stream_ids())
        if extra_used:
            existing |= set(extra_used)
        sid = str(uuid.uuid4())
        while sid in existing:
            sid = str(uuid.uuid4())
        return sid


    def ensure_unique_stream_ids(self, fix: bool = True) -> int:
        """
        Ensures every stream across all ports has a unique stream_id.
        Returns the count of IDs it created/repaired.
        """
        seen = set()
        repaired = 0
        for port, lst in getattr(self, "streams", {}).items():
            for s in lst:
                sid = s.get("stream_id")
                if (not sid) or (sid in seen):
                    if fix:
                        sid = self._alloc_stream_id(extra_used=seen)
                        s["stream_id"] = sid
                        repaired += 1
                seen.add(s.get("stream_id"))
        if repaired:
            logger.info(f"[STREAM-ID] Repaired {repaired} missing/duplicate stream_id(s).")
        return repaired

    def open_add_stream_dialog(self):
        logger.debug(f"Add stream dialog requested")
        logger.debug(f"Has server_tree: {hasattr(self, 'server_tree')}")
        if hasattr(self, 'server_tree'):
            logger.debug(f"server_tree is not None: {self.server_tree is not None}")
        
        if not hasattr(self, 'server_tree') or self.server_tree is None:
            QMessageBox.warning(self, "Server Tree Error", "Server tree is not available. Please restart the application.")
            return
            
        selected_items = self.server_tree.selectedItems()
        logger.debug(f"Selected items count: {len(selected_items)}")
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a TG port to add a stream.")
            return

        selected_item = selected_items[0]
        parent_item = selected_item.parent()

        if parent_item is None:
            QMessageBox.warning(self, "Invalid Selection", "Please select a TG port, not a server.")
            return

        # Extract TG ID from the custom widget in column 0 (not from item text)
        tg_id = ""
        tg_id_widget = self.server_tree.itemWidget(parent_item, 0)
        if tg_id_widget:
            # Find the QLabel containing the TG ID text
            from PyQt5.QtWidgets import QLabel
            tg_id_label = tg_id_widget.findChild(QLabel)
            if tg_id_label:
                tg_id_text = tg_id_label.text()
                tg_id = tg_id_text.replace("TG ", "").strip()
        
        # Fallback: try to get from item text if widget extraction failed
        if not tg_id:
            tg_id = parent_item.text(0).replace("TG ", "").strip()
        
        # If still no TG ID, try to find it from server_interfaces by matching server address
        if not tg_id:
            server_address = parent_item.text(1)  # Server address is in column 1
            for srv in self.server_interfaces:
                if srv.get("address") == server_address:
                    tg_id = str(srv.get("tg_id", "0"))
                    break
        
        port_name = selected_item.text(0).replace("Port: ", "").strip()
        # Remove radio symbol if present
        if port_name.startswith("• ") or port_name.startswith("● "):
            port_name = port_name[2:]  # Remove bullet prefix
        # Sanity-guard against bogus selections. The server tree never
        # holds "↳" — that's the streams-table continuation marker — but
        # reject it explicitly so a regression elsewhere can't silently
        # create a "TG 0 - Port: ↳" ghost key in self.streams. Same guard
        # for an empty selection (which would build "TG 0 - Port: ").
        if not port_name or port_name == "↳":
            QMessageBox.warning(
                self, "Invalid Port",
                f"Could not resolve a port name from the selected tree item "
                f"(got {selected_item.text(0)!r}). Please pick a port from "
                f"the server tree on the left.",
            )
            return
        full_port_name = f"TG {tg_id} - Port: {port_name}"
        logger.debug(f"Selected interface: {port_name}")
        logger.debug(f"Full port name: {full_port_name}")

        # Collect RX ports from online TGs (use cached interfaces to avoid blocking)
        server_interfaces = []
        for srv in self.server_interfaces:
            if not srv.get("online", True):
                continue
            tg = srv.get("tg_id", "0")
            
            # Use cached interfaces if available (fast, non-blocking)
            cached_interfaces = srv.get("interfaces", [])
            if cached_interfaces:
                ports = []
                for iface in cached_interfaces:
                    # Handle both dict and string formats
                    if isinstance(iface, dict):
                        name = iface.get("name", "")
                    else:
                        name = str(iface)
                    
                    if name == "lo":
                        logger.debug(f"Skipping loopback interface: {name}")
                    elif name == port_name:
                        logger.debug(f"Skipping selected TX interface: {name} (same as RX)")
                    else:
                        port_entry = f"TG {tg} - Port: {name}"
                        ports.append(port_entry)
                        logger.debug(f"Adding RX Port: {port_entry}")
                if ports:
                    server_interfaces.append({"tg_id": tg, "ports": ports})
            else:
                # No cached interfaces - try to fetch with short timeout (non-blocking)
                try:
                    # Use connection_manager if available for better timeout handling.
                    # Fall back to _get_async (QThread + local event loop)
                    # rather than bare requests.get so the UI stays
                    # responsive while we wait — same pattern as
                    # _post_traffic_async for /api/traffic/{start,stop}.
                    if hasattr(self, 'connection_manager') and self.connection_manager:
                        r = self.connection_manager.get(f"{srv['address']}/api/interfaces", timeout=1)
                    else:
                        r = self._get_async(f"{srv['address']}/api/interfaces", timeout=1)
                    if r.status_code == 200:
                        interfaces = r.json()
                        # Cache interfaces for future use
                        srv["interfaces"] = interfaces
                        ports = []
                        for iface in interfaces:
                            name = iface.get("name", "") if isinstance(iface, dict) else str(iface)
                            if name == "lo":
                                logger.debug(f"Skipping loopback interface: {name}")
                            elif name == port_name:
                                logger.debug(f"Skipping selected TX interface: {name} (same as RX)")
                            else:
                                port_entry = f"TG {tg} - Port: {name}"
                                ports.append(port_entry)
                                logger.debug(f"Adding RX Port: {port_entry}")
                        if ports:
                            server_interfaces.append({"tg_id": tg, "ports": ports})
                except requests.exceptions.Timeout:
                    logger.warning(f"Timeout fetching RX interfaces from {srv['address']} (using empty list)")
                except requests.exceptions.ConnectionError:
                    logger.warning(f"Connection error fetching RX interfaces from {srv['address']} (using empty list)")
                except Exception as e:
                    logger.warning(f"Failed to fetch RX interfaces from {srv['address']}: {e} (using empty list)")

        new_stream_id = str(uuid.uuid4())
        new_stream_data = {"stream_id": new_stream_id}
        logger.debug(f"Creating dialog for port: {full_port_name}")
        logger.debug(f"Server interfaces count: {len(server_interfaces)}")
        dialog = AddStreamDialog(self, full_port_name, server_interfaces=server_interfaces, stream_data=new_stream_data)
        logger.debug(f"Dialog created, about to show...")

        result = dialog.exec()
        logger.debug(f"Dialog result: {result} (QDialog.Accepted={QDialog.Accepted})")
        if result == QDialog.Accepted:
            stream_details = dialog.get_stream_details()
            if not stream_details.get("rx_port"):
                stream_details["rx_port"] = f"TG {tg_id} - Port: {port_name}"
            #stream_details["stream_id"] = stream_details.get("stream_id") or new_stream_id
            stream_details["stream_id"] = self._alloc_stream_id()

            if full_port_name not in self.streams:
                self.streams[full_port_name] = []

            protocol_section = stream_details.setdefault("protocol_selection", {})
            existing_names = [
                s.get("protocol_selection", {}).get("name", "") for s in self.streams[full_port_name]
            ]
            stream_name = protocol_section.get("name", "").strip()
            if not stream_name or stream_name in existing_names:
                base = "Stream"
                idx = 1
                while f"{base}_{idx}" in existing_names:
                    idx += 1
                stream_name = f"{base}_{idx}"

            protocol_section["name"] = stream_name
            stream_details["name"] = stream_name

            self.streams[full_port_name].append(stream_details)
            self.ensure_unique_stream_ids()
            logger.debug(f"Stream added for {full_port_name}: {stream_details}")
            logger.debug(f"Total streams in self.streams: {sum(len(streams) for streams in self.streams.values())}")
            logger.debug(f"Streams for this port: {len(self.streams[full_port_name])}")
            self.update_stream_table()

    def edit_selected_stream(self):
        selected_rows = self.stream_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select a stream to edit.")
            return

        try:
            row = selected_rows[0].row()
            interface_item = self.stream_table.item(row, 1)
            stream_name_item = self.stream_table.item(row, 2)
            if not interface_item or not stream_name_item:
                raise ValueError("The selected row does not contain valid stream data.")

            tx_port_text = interface_item.text().strip()
            stream_name = stream_name_item.text().strip()

            # Resolve the row to a port key in self.streams. The Streams table
            # collapses multiple streams sharing one port into a "header row"
            # with the iface name + a "↳" continuation row for each
            # subsequent stream — see server_section.py setItem(row, 1, "↳").
            # If the user picked a continuation row, interface_item.text() is
            # literally "↳" which never matches any key. Three-tier resolution:
            #
            #   1. Preferred: stream_id stashed on the name cell's UserRole,
            #      then walk self.streams to find which port owns it.
            #   2. Fallback A: continuation row → read iface from tooltip
            #      (server_section sets it to the bare iface name).
            #   3. Fallback B: header row → use the visible text directly.
            #
            # All three feed the canonical find_port_key normalizer instead
            # of reinventing the per-key matching loop.
            from traffic_client.stream_logic import find_port_key
            tx_port = None
            stream_id = stream_name_item.data(Qt.UserRole)
            original = None
            if stream_id:
                for p, lst in self.streams.items():
                    for s in lst:
                        if s.get("stream_id") == stream_id:
                            tx_port, original = p, s
                            break
                    if original:
                        break

            if not tx_port:
                resolve_text = tx_port_text
                if resolve_text == "↳":
                    # Continuation row: tooltip has the real iface name.
                    resolve_text = (interface_item.toolTip() or "").strip()
                tx_port = find_port_key(self.streams, resolve_text)

            if not tx_port:
                available_keys = list(self.streams.keys())
                raise KeyError(
                    f"TX Port '{tx_port_text}' could not be resolved to any "
                    f"stream port. Available keys: {available_keys}"
                )

            if original is None:
                original = next(
                    (s for s in self.streams[tx_port]
                     if s.get("protocol_selection", {}).get("name") == stream_name
                     or s.get("name") == stream_name),
                    None
                )
            if not original:
                raise KeyError(f"Stream '{stream_name}' not found under '{tx_port}'.")

            # Cancel any pending duration-expiry auto-stop timer for this
            # stream — its state is about to change (potentially a new
            # duration), and a stale timer firing later would issue an
            # orphan /stop for the old config.
            if hasattr(self, "_cancel_auto_stop_timer"):
                self._cancel_auto_stop_timer(original.get("stream_id"))

            import copy
            stream_data = copy.deepcopy(original)

            # flatten protocol_selection into top-level for dialog convenience
            protocol_section = stream_data.get("protocol_selection", {})
            for k, v in protocol_section.items():
                if k not in stream_data:
                    stream_data[k] = v

            tx_port_name = tx_port.split(" - Port:")[-1].strip()
            server_interfaces = []
            for srv in self.server_interfaces:
                if not srv.get("online", True):
                    continue
                tid = srv.get("tg_id", "0")
                try:
                    # Fast path: cached interfaces. Avoids the per-TG GET
                    # entirely when the server tree has already populated
                    # them — opens the dialog instantly in the common case.
                    cached = srv.get("interfaces") or []
                    if cached:
                        rx_ports = [
                            (iface.get("name") if isinstance(iface, dict) else str(iface))
                            for iface in cached
                        ]
                        rx_ports = [n for n in rx_ports if n and n != "lo" and n != tx_port_name]
                        server_interfaces.append({"tg_id": tid, "ports": rx_ports})
                        continue
                    # Cold path: fetch via _get_async (QThread + local
                    # event loop) so the UI keeps repainting while we
                    # wait. Previously this was a sync requests.get with
                    # timeout=5 — the dialog took up to 5s to open
                    # whenever any TG was unreachable.
                    r = self._get_async(f"{srv['address']}/api/interfaces", timeout=3)
                    rx_ports = []
                    for iface in r.json():
                        name = iface["name"]
                        if name != "lo" and name != tx_port_name:
                            rx_ports.append(name)
                    server_interfaces.append({"tg_id": tid, "ports": rx_ports})
                except Exception as e:
                    logger.error(f"Failed to fetch RX interfaces from {srv['address']}: {e}")

            dialog = AddStreamDialog(
                parent=self, interface=tx_port, stream_data=stream_data, server_interfaces=server_interfaces
            )

            if dialog.exec() == QDialog.Accepted:
                edited = dialog.get_stream_details()
                edited_rx = edited.get("rx_port")
                if not edited:
                    QMessageBox.warning(self, "Edit Stream", "No changes were made.")
                    return
                if not edited_rx or edited_rx == "Same as TX Port":
                    edited["rx_port"] = tx_port

                updated = {
                    "stream_id": original.get("stream_id"),
                    "status": original.get("status", "stopped"),
                    "rx_port": edited.get("rx_port", tx_port),
                    "flow_tracking_enabled": edited.get("flow_tracking_enabled", False),
                    "protocol_selection": {},
                    "protocol_data": edited.get("protocol_data", {}),
                    "rocev2": edited.get("rocev2", {}),
                    "uec": edited.get("uec", {}),
                    "override_settings": edited.get("override_settings", {}),
                    "stream_rate_control": edited.get("stream_rate_control", {})
                }

                for k, v in edited.items():
                    if k not in updated and k not in {
                        "protocol_data", "rocev2", "uec", "override_settings",
                        "stream_rate_control", "rx_port", "stream_id", "status", "flow_tracking_enabled"
                    }:
                        updated["protocol_selection"][k] = v

                # Preserve protocol_selection fields from the original
                # stream that the dialog didn't surface back. Without
                # this, fields the user inline-edited BEFORE opening
                # Edit Stream (frame_size most commonly) — but didn't
                # touch in the dialog — got silently overwritten by
                # whatever defaults the dialog produced. Audit fix:
                # only pull from original.protocol_selection for keys
                # not already set by the edited form, so the dialog's
                # explicit changes still win.
                original_ps = original.get("protocol_selection", {}) or {}
                for k, v in original_ps.items():
                    if k not in updated["protocol_selection"]:
                        updated["protocol_selection"][k] = v

                if "flow_tracking_enabled" in edited:
                    updated["protocol_selection"]["flow_tracking_enabled"] = edited["flow_tracking_enabled"]
                updated["flow_tracking_enabled"] = edited.get("flow_tracking_enabled", False)
                updated["protocol_selection"]["name"] = stream_name

                for i, s in enumerate(self.streams[tx_port]):
                    if s.get("protocol_selection", {}).get("name") == stream_name:
                        self.streams[tx_port][i] = updated
                        break

                self.update_stream_table()
                logger.info(f"Stream '{stream_name}' updated successfully.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to edit the stream: {e}")

    def remove_selected_stream(self):
        selected_rows = self.stream_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select a stream to remove.")
            return

        try:
            for row in selected_rows:
                r = row.row()
                interface_item = self.stream_table.item(r, 1)
                stream_name_item = self.stream_table.item(r, 2)
                if not interface_item or not stream_name_item:
                    QMessageBox.critical(self, "Error", "Invalid selection. Missing interface or stream name.")
                    continue

                interface_text = interface_item.text().strip()
                stream_name = stream_name_item.text().strip()

                # Resolve the row to a port key. Continuation rows show "↳"
                # and never match a key directly; prefer the stream_id
                # stashed on the name cell, then fall back to the tooltip
                # (which holds the real iface name on continuation rows),
                # then the visible text. See edit_selected_stream() for the
                # full rationale — same three-tier resolution.
                from traffic_client.stream_logic import find_port_key
                port_key = None
                stream_id = stream_name_item.data(Qt.UserRole)
                if stream_id:
                    for p, lst in self.streams.items():
                        if any(s.get("stream_id") == stream_id for s in lst):
                            port_key = p
                            break
                if not port_key:
                    resolve_text = interface_text
                    if resolve_text == "↳":
                        resolve_text = (interface_item.toolTip() or "").strip()
                    port_key = find_port_key(self.streams, resolve_text)

                if not port_key:
                    QMessageBox.warning(
                        self, "Error",
                        f"Interface '{interface_text}' not found in streams. "
                        f"Available: {list(self.streams.keys())[:3]}..."
                    )
                    continue

                logger.info(f"Removing stream '{stream_name}' from port '{port_key}'")

                # Cancel any pending auto-stop timer for the stream(s)
                # being removed — without this, the timer fires later
                # against a stream_id that no longer exists locally and
                # the server gets an orphan /stop POST.
                if hasattr(self, "_cancel_auto_stop_timer"):
                    for s in self.streams.get(port_key, []):
                        if s.get("protocol_selection", {}).get("name") == stream_name:
                            self._cancel_auto_stop_timer(s.get("stream_id"))

                self.streams[port_key] = [
                    s for s in self.streams[port_key]
                    if s.get("protocol_selection", {}).get("name") != stream_name
                ]
                
                # If no streams left for this port, remove the port key
                if not self.streams[port_key]:
                    del self.streams[port_key]

            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            self.update_stream_table()
            QMessageBox.information(self, "Stream Removed", "Selected streams have been removed.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while removing the stream: {e}")
