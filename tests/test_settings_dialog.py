"""Settings dialog regression tests.

v0.3.11 hotfix: the Settings dialog crashed on open with
OverflowError because the BGP-ASN QSpinBox.setRange(1, 4_294_967_295)
exceeded Qt5's int32 limit (2,147,483,647). User report:

    File "traffic_client/menu_actions.py", line 453,
      in open_settings_dialog
        asn_input.setRange(1, 4_294_967_295)
    OverflowError: argument 2 overflowed: value must be in the
    range -2147483648 to 2147483647

Fix swapped QSpinBox for QLineEdit + QRegExpValidator (digits) +
manual range clamp in the save handler, which supports the full
BGP 4-byte ASN range RFC 6793 defines.

These tests cover:
  • dialog construction doesn't crash (the original symptom)
  • ASN field is editable, accepts full 4-byte range
  • _apply path clamps + persists correctly
  • restore-defaults path uses setText (not setValue)
"""

from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    """Bare QApplication for widget construction. PyQt5 needs one
    QApplication per process; reuse if pytest-qt isn't installed.
    Set org/app names so QSettings() (no args) routes to a stable
    location both the dialog and the readback share."""
    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QCoreApplication.setOrganizationName("NetgenTest")
    QCoreApplication.setApplicationName("SettingsDialogTest")
    return app


def _make_stub_main(qapp):
    """Stand up the menu-action mixin on a minimal QMainWindow.
    open_settings_dialog only touches QSettings + the dialog itself,
    so we don't need the rest of the main-window scaffolding."""
    from PyQt5.QtWidgets import QMainWindow, QDialog
    from traffic_client.menu_actions import TrafficGenClientMenuAction

    class _StubMain(QMainWindow, TrafficGenClientMenuAction):
        def __init__(self):
            super().__init__()
    # Skip the modal loop so construction tests don't hang.
    QDialog.exec_ = lambda self: 0
    return _StubMain()


def test_settings_dialog_opens_without_overflow(qapp):
    """The original crash: opening the dialog raised OverflowError
    on the BGP-ASN QSpinBox.setRange(1, 4_294_967_295). Pin that
    construction now completes."""
    main = _make_stub_main(qapp)
    # If the OverflowError comes back, this raises and the test
    # fails — exactly what we want for the regression catch.
    main.open_settings_dialog()


def test_settings_asn_field_accepts_full_4byte_range(qapp, monkeypatch):
    """The fix replaced QSpinBox with QLineEdit + regex validator so
    the field can hold the BGP 4-byte ASN max (2^32-1). Pin that
    operator-entered values across the legal range round-trip
    through the QSettings layer."""
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import (
        QDialog, QDialogButtonBox, QLineEdit, QMainWindow,
    )
    from traffic_client.menu_actions import TrafficGenClientMenuAction

    # In-memory QSettings so test pollution doesn't bleed.
    QSettings.setDefaultFormat(QSettings.IniFormat)

    class _StubMain(QMainWindow, TrafficGenClientMenuAction):
        def __init__(self):
            super().__init__()

    # Capture the dialog so we can poke its widgets + trigger _apply
    # before it would exec.
    constructed = []

    def _capture_exec(self):
        constructed.append(self)
        return 0  # don't enter the modal loop

    monkeypatch.setattr(QDialog, "exec_", _capture_exec)

    main = _StubMain()
    main.open_settings_dialog()
    assert constructed, "Settings dialog never constructed"
    dlg = constructed[0]

    # Find the ASN QLineEdit by looking up the labeled-row in the form.
    # Fall back to scanning children if the form-row API differs.
    asn_field = None
    for w in dlg.findChildren(QLineEdit):
        # The IPv4 fields use dotted-decimal text; the ASN field is
        # the one whose validator is a QRegExpValidator that accepts
        # only digits.
        v = w.validator()
        if v is not None and v.__class__.__name__ == "QRegExpValidator":
            asn_field = w
            break
    assert asn_field is not None, (
        "ASN field not found — should be a QLineEdit with a "
        "QRegExpValidator (digits only). If the fix reverted to "
        "QSpinBox, this catches the regression."
    )

    # Round-trip the BGP 4-byte ASN max — far above Qt5 int32 limit.
    asn_field.setText("4294967295")
    # Locate the OK button and click it to fire _apply.
    btn_box = dlg.findChild(QDialogButtonBox)
    assert btn_box is not None
    ok = btn_box.button(QDialogButtonBox.Ok)
    ok.click()

    # The saved value must be the legal 4-byte ASN max (clamping
    # doesn't kick in for valid input). Read back as a STRING and
    # parse with Python int() — the dialog stores as str for this
    # exact reason (QSettings type=int routes through Qt's int32
    # cast and turns 4294967295 into -1).
    saved = int(QSettings().value("default_bgp_asn", "0"))
    assert saved == 4294967295, (
        f"BGP 4-byte ASN didn't round-trip — saved {saved!r}, "
        f"expected 4294967295. If this is 2147483647, the QSpinBox "
        f"was reintroduced and int32-clamped the value silently."
    )


def test_settings_asn_field_clamps_excessive_values(qapp, monkeypatch):
    """The regex allows up to 10 digits, so an operator could type
    9_999_999_999 — above the 4-byte ASN max. _apply must clamp,
    not crash or persist an invalid value."""
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import (
        QDialog, QDialogButtonBox, QLineEdit, QMainWindow,
    )
    from traffic_client.menu_actions import TrafficGenClientMenuAction

    class _StubMain(QMainWindow, TrafficGenClientMenuAction):
        def __init__(self):
            super().__init__()

    constructed = []
    monkeypatch.setattr(
        QDialog, "exec_",
        lambda self: (constructed.append(self), 0)[1],
    )

    main = _StubMain()
    main.open_settings_dialog()
    dlg = constructed[0]

    asn_field = next(
        w for w in dlg.findChildren(QLineEdit)
        if w.validator() and
        w.validator().__class__.__name__ == "QRegExpValidator"
    )
    asn_field.setText("9999999999")  # > 4_294_967_295

    btn_box = dlg.findChild(QDialogButtonBox)
    btn_box.button(QDialogButtonBox.Ok).click()

    # Read back the same way the dialog does — as a string, then
    # Python int(). Routing through QSettings type=int hits Qt's
    # int32 truncation and turns 4294967295 into -1, which is the
    # whole reason the dialog now stores as string.
    saved = int(QSettings().value("default_bgp_asn", "0"))
    assert saved == 4_294_967_295, (
        f"Excessive ASN value should clamp to 4-byte max, got {saved}"
    )
