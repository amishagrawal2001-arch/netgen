"""v0.5.185: Install / Upgrade Server dialog — Fresh install tab
layout regression.

Operator screenshot (2026-07-12) showed the Fresh install via SSH
tab painting rows on top of each other: Wheel / tarball, Installer,
and the flags QGroupBox visually overlapped. Root causes:

  * The QGroupBox for install_ostg_complete.py flags had
    `padding-top:6px` — too small for the title glyph, which
    rendered on top of the first checkbox.
  * The QFormLayout had no explicit vertical spacing set.
  * There was no scroll fallback, so a user-resized (narrow)
    dialog clipped the buttons at the bottom instead of scrolling.

The Qt/pytest combo on macOS + Python 3.14 SIGABRTs on direct
QDialog construction inside pytest. We run the widget assertions
in a fresh subprocess (same as the operator's real launch path) to
sidestep that flake. Source-level assertions run inline.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ── source-level guards (fast, no Qt) ─────────────────────────────


def test_flags_groupbox_stylesheet_has_clearance():
    """Bug root cause: `padding-top:6px` overlapped the first
    checkbox with the title glyph. Enforce ≥12."""
    import re
    src = (REPO / "widgets" / "install_server_dialog.py").read_text()
    # Grab the flags_box.setStyleSheet call.
    m = re.search(
        r'flags_box\.setStyleSheet\(\s*"([^"]+)"\s*"([^"]+)"',
        src)
    assert m, "flags_box setStyleSheet block not found"
    sheet = m.group(1) + m.group(2)
    for prop in ("margin-top", "padding-top"):
        mm = re.search(prop + r":\s*(\d+)px", sheet)
        assert mm, f"{prop} missing from flags stylesheet"
        assert int(mm.group(1)) >= 12, \
            f"{prop}={mm.group(1)}px too tight (need ≥12)"


def test_form_has_explicit_vertical_spacing():
    src = (REPO / "widgets" / "install_server_dialog.py").read_text()
    # Look inside _build_fresh_install_tab for setVerticalSpacing.
    body = src.split("def _build_fresh_install_tab", 1)[1]
    body = body.split("\n    def ", 1)[0]
    assert "setVerticalSpacing" in body, \
        "form vertical spacing not set on Fresh install tab"
    assert "setHorizontalSpacing" in body, \
        "form horizontal spacing not set on Fresh install tab"


def test_fresh_install_tab_wrapped_in_scrollarea():
    """The Fresh install tab must be a QScrollArea so a narrow /
    resized dialog scrolls instead of clipping the fields."""
    src = (REPO / "widgets" / "install_server_dialog.py").read_text()
    body = src.split("def _build_fresh_install_tab", 1)[1]
    body = body.split("\n    def ", 1)[0]
    assert "QScrollArea" in body, \
        "Fresh install tab is not wrapped in a QScrollArea"
    assert "setWidgetResizable(True)" in body, \
        "scroll area must be widget-resizable"
    # Return statement must return the scroll area, not the raw form.
    assert "return scroll" in body, \
        "Fresh install tab must return the scroll area"


# ── live widget check (subprocess) ─────────────────────────────────


def test_dialog_constructs_and_wires_scrollarea_live():
    """End-to-end: construct the dialog with an offscreen QApplication
    and assert the Fresh install tab is a QScrollArea with the
    expected form widgets and unclipped buttons."""
    script = r"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, %r)
from PyQt5.QtWidgets import (
    QApplication, QScrollArea, QFormLayout, QGroupBox,
)
app = QApplication(sys.argv)
from widgets.install_server_dialog import InstallServerDialog
dlg = InstallServerDialog(default_server_url='http://x:5050')
dlg.tabs.setCurrentIndex(1)
scroll = dlg.tabs.currentWidget()
assert isinstance(scroll, QScrollArea), 'not QScrollArea'
assert scroll.widgetResizable() is True
form_w = scroll.widget()
layout = form_w.layout()
assert isinstance(layout, QFormLayout)
assert layout.verticalSpacing() >= 8, layout.verticalSpacing()
assert layout.horizontalSpacing() >= 8, layout.horizontalSpacing()
boxes = [g for g in form_w.findChildren(QGroupBox) if 'flags' in g.title()]
assert boxes, 'flags QGroupBox missing'
assert dlg.ssh_test_btn.sizeHint().height() > 0
assert dlg.ssh_btn.sizeHint().height() > 0
print('OK')
""" % str(REPO)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, \
        f"subprocess crashed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK" in proc.stdout, proc.stdout
