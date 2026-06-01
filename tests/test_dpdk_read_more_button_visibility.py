"""v0.3.6 — DPDK "Read More" button visibility regression pin.

Bug surfaced via user screenshot: the "Read More: DPDK Traffic
Blast Workflow" button in the Variable Fields / Runtime Engine
tab rendered as a solid blue bar with no visible label.

Root cause: stylesheet set `color: #3b82f6` (blue) but left
`background-color` unspecified, so the button inherited the
dialog-wide primary-button styling (also blue). Result: blue
text on blue background — invisible.

v0.3.6 fixes by setting an explicit white background + deeper
blue (#1d4ed8) for AA contrast + a thin blue border so the
button reads as a clearly-clickable affordance, matching the
"neutral white" button family used elsewhere (Stats dock
Clear/Export, DPDK Status Unbind, etc.).

The dialog class is heavy to construct (pulls in all of scapy
+ Qt), so this is a source-grep pin rather than a render test.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
STREAM_DIALOG = REPO / "widgets" / "stream_dialog.py"


@pytest.fixture(scope="module")
def src():
    return STREAM_DIALOG.read_text()


def test_v0_3_6_read_more_button_has_explicit_background(src):
    """The button's stylesheet MUST set a background-color so it
    doesn't inherit the dialog's blue primary-button background
    and end up blue-on-blue."""
    # Find the read_more_button stylesheet block.
    m = re.search(
        r'read_more_button\.setStyleSheet\((.*?)\)\s*\n',
        src, flags=re.DOTALL,
    )
    assert m is not None, "read_more_button setStyleSheet call not found"
    block = m.group(1)
    assert "background-color" in block, (
        "read_more_button stylesheet missing background-color — "
        "v0.3.6 invisible-blue-on-blue bug regressed. Inherited "
        "background is the dialog's primary blue."
    )
    # And the background must NOT be a blue (which would defeat
    # the fix).
    assert "background-color: #ffffff" in block or \
           "background-color: white" in block, (
        "background-color should be white (#ffffff) for the "
        "neutral-white button family. Anything else risks the "
        "same readability problem."
    )


def test_v0_3_6_read_more_button_uses_aa_contrast_blue(src):
    """The text color must be a deeper blue with sufficient AA
    contrast against the white background. #3b82f6 (the original
    color) fails AA on a non-white bg; #1d4ed8 is the
    Tailwind-blue-700 standard the rest of the app uses."""
    m = re.search(
        r'read_more_button\.setStyleSheet\((.*?)\)\s*\n',
        src, flags=re.DOTALL,
    )
    block = m.group(1)
    assert "#1d4ed8" in block, (
        "read_more_button text color should be #1d4ed8 (blue-700) "
        "for AA contrast on white. The earlier #3b82f6 was the "
        "invisible-on-blue color."
    )


def test_v0_3_6_read_more_button_has_hover_state(src):
    """Without a `:hover` state the button reads as static text
    rather than a clickable affordance — important since the
    original bug made operators not realise the bar was a
    button at all."""
    m = re.search(
        r'read_more_button\.setStyleSheet\((.*?)\)\s*\n',
        src, flags=re.DOTALL,
    )
    block = m.group(1)
    assert "QPushButton:hover" in block, (
        "read_more_button missing :hover state — without it the "
        "button doesn't visually respond to mouse-over, which "
        "compounds the original 'looks unclickable' bug."
    )


def test_v0_3_6_read_more_button_has_pointing_cursor(src):
    """`setCursor(Qt.PointingHandCursor)` is the standard "this is
    clickable" affordance the app uses on every other button."""
    # Look near the read_more_button definition.
    m = re.search(
        r'read_more_button = QPushButton.*?layout\.addWidget\(read_more_button\)',
        src, flags=re.DOTALL,
    )
    assert m is not None
    block = m.group(0)
    assert "setCursor" in block and "PointingHandCursor" in block, (
        "read_more_button missing setCursor(Qt.PointingHandCursor) — "
        "loses the visual cursor-changes-on-hover affordance"
    )
