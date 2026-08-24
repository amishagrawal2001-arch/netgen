from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
                             QLineEdit, QCheckBox, QGroupBox, QDialogButtonBox,
                             QWidget, QMessageBox, QLabel)
from PyQt5.QtGui import QIntValidator
import ipaddress

# v0.2.95: live-validation styles. Red border = invalid, normal border
# = valid / empty. The accept-time validator (in `_validate`) is kept
# as a backstop so malformed values can't slip through if the operator
# ignores the visual cue (e.g. via paste).
_OK_QSS  = ""   # empty stylesheet → Qt default; respects platform theme
_BAD_QSS = "QLineEdit { border: 1px solid #dc2626; background: #fef2f2; }"


class AddOspfDialog(QDialog):
    def __init__(self, parent=None, device_name="", ospf_config=None):
        super().__init__(parent)
        self.device_name = device_name
        self.ospf_config = ospf_config or {}
        self.edit_mode = bool(ospf_config)
        
        # Set window title based on mode
        title = f"Edit OSPF Configuration - {device_name}" if self.edit_mode else f"Add OSPF Configuration - {device_name}"
        self.setWindowTitle(title)
        # v0.5.205: taller — needs room for the Address Families
        # group. Pre-fix the dialog was 300px tall; the extra AF
        # checkboxes push the total past that.
        self.setFixedSize(420, 380)

        self.layout = QVBoxLayout()
        self.setup_ospf_form()
        
        # Pre-populate fields if editing
        if self.edit_mode:
            self._populate_fields()
        
        self.button_box = QDialogButtonBox()
        button_text = "Update OSPF" if self.edit_mode else "Add OSPF"
        self.ok_button = self.button_box.addButton(button_text, QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.layout.addWidget(self.button_box)
        self.setLayout(self.layout)

    def setup_ospf_form(self):
        """Setup OSPF configuration form."""
        form_widget = QWidget()
        layout = QFormLayout(form_widget)

        # v0.5.205: Address Families group. Pre-fix the dialog
        # emitted no `ipv4_enabled`/`ipv6_enabled` at all, so the
        # OSPF table's fallback path (utils/devices_tab_ospf.py
        # around line 475) inferred BOTH AFs whenever the device
        # had both a v4 and a v6 address — the operator got two
        # rows per device after a single Add OSPF click, and the
        # v6 row said "No Neighbors" because it was never really
        # configured. Now the user picks the AFs up front and the
        # dialog carries that choice into ospf_config.
        # In edit mode the group is hidden — `prompt_edit_ospf`
        # scopes to one AF's fields (area_id, graceful_restart)
        # via the row's AF column, so re-toggling AFs from the
        # edit dialog would be surprising.
        self._af_group = QGroupBox("Address Families")
        af_layout = QVBoxLayout(self._af_group)
        self.enable_ipv4_checkbox = QCheckBox("Enable IPv4")
        self.enable_ipv4_checkbox.setChecked(True)
        self.enable_ipv4_checkbox.setToolTip(
            "Configure OSPFv2 for IPv4 on this device."
        )
        self.enable_ipv6_checkbox = QCheckBox("Enable IPv6")
        self.enable_ipv6_checkbox.setChecked(True)
        self.enable_ipv6_checkbox.setToolTip(
            "Configure OSPFv3 for IPv6 on this device."
        )
        af_layout.addWidget(self.enable_ipv4_checkbox)
        af_layout.addWidget(self.enable_ipv6_checkbox)
        self.layout.addWidget(self._af_group)
        if self.edit_mode:
            # Edit dialog scopes to one AF at a time; toggling AFs
            # here would fight the row-scoped edit path in
            # prompt_edit_ospf. Hide the group entirely rather than
            # leave it visible-but-ignored.
            self._af_group.hide()

        # Area ID
        self.area_id_input = QLineEdit("0.0.0.0")
        self.area_id_input.setPlaceholderText("e.g., 0.0.0.0  or  decimal 0–4294967295")
        self.area_id_input.setToolTip(
            "RFC 2328 §6 area identifier — accept either dotted-decimal "
            "(0.0.0.0) or a 32-bit unsigned integer (0–4294967295). "
            "Backbone area is 0 / 0.0.0.0."
        )
        # v0.2.95: live border colour driven by validate_ospf_area_id.
        self.area_id_input.textChanged.connect(self._validate_area_id_live)
        layout.addRow("Area ID:", self.area_id_input)

        # Graceful Restart
        self.graceful_restart_checkbox = QCheckBox("Enable Graceful Restart")
        layout.addRow("Graceful Restart:", self.graceful_restart_checkbox)

        # Additional OSPF options can be added here
        options_group = QGroupBox("Additional Options")
        options_layout = QFormLayout(options_group)

        # Router ID (optional)
        self.router_id_input = QLineEdit()
        self.router_id_input.setPlaceholderText("Auto-assigned if empty")
        self.router_id_input.setToolTip(
            "Optional IPv4 router-id. Leave blank to let the daemon "
            "auto-pick from an interface address."
        )
        # v0.2.95: live IPv4 validation. Empty stays valid (optional).
        self.router_id_input.textChanged.connect(self._validate_router_id_live)
        options_layout.addRow("Router ID:", self.router_id_input)

        # Hello interval
        self.hello_interval_input = QLineEdit("10")
        self.hello_interval_input.setPlaceholderText("seconds (1–65535)")
        # v0.2.95: QIntValidator + live border to flag negatives / overflow.
        self.hello_interval_input.setValidator(QIntValidator(1, 65535, self))
        self.hello_interval_input.textChanged.connect(self._validate_intervals_live)
        options_layout.addRow("Hello Interval:", self.hello_interval_input)

        # Dead interval
        self.dead_interval_input = QLineEdit("40")
        self.dead_interval_input.setPlaceholderText("seconds (1–65535, > hello)")
        self.dead_interval_input.setValidator(QIntValidator(1, 65535, self))
        self.dead_interval_input.textChanged.connect(self._validate_intervals_live)
        options_layout.addRow("Dead Interval:", self.dead_interval_input)

        layout.addRow(options_group)

        self.layout.addWidget(form_widget)

    # ─────────────────────────────────────── v0.2.95 live validators
    def _validate_area_id_live(self, _text: str = "") -> None:
        """Red border when the typed Area-ID parses as neither a
        dotted IPv4 nor an int in [0, 4294967295]. Empty stays
        neutral — the accept-time backstop fills in the default."""
        val = self.area_id_input.text().strip()
        if not val:
            self.area_id_input.setStyleSheet(_OK_QSS)
            return
        try:
            from utils.ospf_area import validate_ospf_area_id
            ok, _norm, _err = validate_ospf_area_id(val)
        except Exception:
            # Helper missing? Fall back to the raw parse.
            ok = False
            try:
                ipaddress.IPv4Address(val); ok = True
            except Exception:
                try:
                    n = int(val); ok = 0 <= n <= 4294967295
                except Exception:
                    pass
        self.area_id_input.setStyleSheet(_OK_QSS if ok else _BAD_QSS)

    def _validate_router_id_live(self, _text: str = "") -> None:
        """Router-ID is optional. Red border only when non-empty and
        not a valid IPv4."""
        val = self.router_id_input.text().strip()
        if not val:
            self.router_id_input.setStyleSheet(_OK_QSS)
            return
        try:
            ipaddress.IPv4Address(val)
            self.router_id_input.setStyleSheet(_OK_QSS)
        except Exception:
            self.router_id_input.setStyleSheet(_BAD_QSS)

    def _validate_intervals_live(self, _text: str = "") -> None:
        """Both intervals must be positive ints in QIntValidator's
        accepted range, and dead must be > hello. QIntValidator
        handles the per-field range; this catches the cross-field
        constraint."""
        try:
            hello = int(self.hello_interval_input.text() or "0")
            dead  = int(self.dead_interval_input.text() or "0")
        except ValueError:
            # In-progress typing — leave the per-field validator do it.
            return
        if hello > 0 and dead > 0 and dead <= hello:
            # Cross-field violation → red both fields so the operator
            # sees the relationship matters, not just the numbers.
            self.hello_interval_input.setStyleSheet(_BAD_QSS)
            self.dead_interval_input.setStyleSheet(_BAD_QSS)
            tip = (
                "Dead interval must be greater than Hello interval "
                "(RFC 2328 §10 recommends Dead = 4×Hello)."
            )
            self.hello_interval_input.setToolTip(tip)
            self.dead_interval_input.setToolTip(tip)
        else:
            self.hello_interval_input.setStyleSheet(_OK_QSS)
            self.dead_interval_input.setStyleSheet(_OK_QSS)
            self.hello_interval_input.setToolTip("")
            self.dead_interval_input.setToolTip("")
    
    def _populate_fields(self):
        """Pre-populate form fields with current OSPF configuration."""
        if not self.ospf_config:
            return
        
        # Area ID
        area_id = self.ospf_config.get("area_id", "0.0.0.0")
        if area_id:
            self.area_id_input.setText(str(area_id))
        
        # Graceful Restart
        graceful_restart = self.ospf_config.get("graceful_restart", False)
        self.graceful_restart_checkbox.setChecked(graceful_restart)
        
        # Router ID
        router_id = self.ospf_config.get("router_id", "")
        if router_id:
            self.router_id_input.setText(str(router_id))
        
        # Hello Interval
        hello_interval = self.ospf_config.get("hello_interval", "10")
        if hello_interval:
            self.hello_interval_input.setText(str(hello_interval))
        
        # Dead Interval
        dead_interval = self.ospf_config.get("dead_interval", "40")
        if dead_interval:
            self.dead_interval_input.setText(str(dead_interval))

    def get_values(self):
        """Get OSPF configuration values.

        v0.5.205: emit `ipv4_enabled`/`ipv6_enabled` from the
        Address Families group in Add mode. In Edit mode the group
        is hidden and both checkboxes stay at their default `True`
        — the merge in `_update_device_protocol` (OSPF branch)
        would then overwrite the caller's existing flags with
        `True`/`True`, which is exactly the bug we're fixing.
        So in edit mode we omit both keys and let the merge
        preserve whatever was already stored.
        """
        values = {
            "area_id": self.area_id_input.text().strip(),
            "graceful_restart": self.graceful_restart_checkbox.isChecked(),
            "router_id": self.router_id_input.text().strip(),
            "hello_interval": self.hello_interval_input.text().strip(),
            "dead_interval": self.dead_interval_input.text().strip(),
        }
        if not self.edit_mode:
            values["ipv4_enabled"] = self.enable_ipv4_checkbox.isChecked()
            values["ipv6_enabled"] = self.enable_ipv6_checkbox.isChecked()
        return values

    def accept(self):
        """Validate and accept the dialog."""
        if not self._validate():
            return
        super().accept()

    def _validate(self):
        """Validate OSPF configuration."""
        # v0.5.205: at least one address family must be enabled
        # when adding. Zero-AF OSPF configs are meaningless and
        # would produce an OSPF row that never rendered — and
        # since Apply is a no-op with no AFs, it'd feel like the
        # button was broken.
        if not self.edit_mode:
            if not (self.enable_ipv4_checkbox.isChecked() or
                    self.enable_ipv6_checkbox.isChecked()):
                QMessageBox.warning(
                    self, "No Address Family Selected",
                    "Select at least one address family "
                    "(IPv4 and/or IPv6) to enable OSPF for."
                )
                return False

        # Validate Area ID format
        area_id = self.area_id_input.text().strip()
        if area_id:
            try:
                # Check if it's a valid IP address format
                ipaddress.IPv4Address(area_id)
            except Exception:
                try:
                    # Check if it's a decimal number
                    area_num = int(area_id)
                    if area_num < 0 or area_num > 4294967295:
                        raise ValueError("Area ID out of range")
                except Exception:
                    QMessageBox.warning(self, "Invalid Area ID", "Area ID must be a valid IPv4 address or decimal number (0-4294967295).")
                    return False

        # Validate Router ID if provided
        router_id = self.router_id_input.text().strip()
        if router_id:
            try:
                ipaddress.IPv4Address(router_id)
            except Exception:
                QMessageBox.warning(self, "Invalid Router ID", "Router ID must be a valid IPv4 address.")
                return False

        # Validate intervals
        try:
            hello_interval = int(self.hello_interval_input.text() or "10")
            dead_interval = int(self.dead_interval_input.text() or "40")
            if hello_interval <= 0 or dead_interval <= 0:
                raise ValueError("Intervals must be positive")
            if dead_interval <= hello_interval:
                raise ValueError("Dead interval must be greater than hello interval")
        except Exception as e:
            QMessageBox.warning(self, "Invalid Intervals", f"Invalid interval values: {e}")
            return False

        return True
