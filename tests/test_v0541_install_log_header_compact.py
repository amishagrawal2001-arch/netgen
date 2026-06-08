"""v0.5.41 — install/upgrade dialog log header is compact.

Operator-reported on the v0.5.40 dialog:
  "seems you are taking too much vertical space for pop out button,
   also i see Ready somewhere in the text area."

Two issues from the screenshot:

  1. log_header row (just the Pop out button) was rendering ~150px
     tall — QGroupBox's default 11px contentsMargins +
     QVBoxLayout's default 9px spacing + the button's natural
     ~30px height left a huge empty gray strip around the button.

  2. status_lbl ("Ready.") sat in its OWN row immediately below
     log_view's bottom border. Visually the gray text rendered AT
     the boundary between log_view's white background and the
     dialog's gray surround — operator read it as "text inside
     the log area" and was confused.

v0.5.41 collapses the layout:

  Before:                          After:
    log_header (just Pop out)        log_header [ status_lbl | Pop out ]
    log_view (white)                 log_view (white)
    status_lbl ("Ready.")            (no separate status row)

Both fixes are in one go because they share the same root cause
(the layout was using two rows where one would do).

These tests pin the new layout.
"""
from __future__ import annotations

import re
from pathlib import Path


_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "install_server_dialog.py"
)


def _src() -> str:
    return _DIALOG.read_text()


def test_log_layout_has_tight_contents_margins():
    """log_layout's setContentsMargins must NOT use the default
    11px-or-more padding. Pre-fix Qt added ~40px of gray above
    the Pop out button."""
    src = _src()
    # Find the log_layout creation block.
    m = re.search(
        r"log_layout\s*=\s*QVBoxLayout\(log_box\)([\s\S]+?)log_header",
        src,
    )
    assert m, "log_layout construction block not found"
    block = m.group(1)
    margin_m = re.search(
        r"log_layout\.setContentsMargins\(\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)",
        block,
    )
    assert margin_m, (
        "log_layout has no setContentsMargins call — would use "
        "Qt's default (11px) and waste vertical space."
    )
    margins = [int(x) for x in margin_m.groups()]
    # All four margins should be ≤ 14 (some top margin is fine for
    # the group-box title clearance).
    assert all(m <= 14 for m in margins), (
        f"log_layout margins {margins} include values > 14px — "
        f"would re-introduce the empty-strip-above-Pop-out look."
    )


def test_status_lbl_inside_log_header_not_separate_row():
    """The status_lbl must be inside log_header (same row as Pop
    out) — NOT in its own row below log_view. Match the order:
    status_lbl added to log_header BEFORE popout_btn."""
    src = _src()
    m = re.search(
        r"log_header\s*=\s*QHBoxLayout\(\)([\s\S]+?)log_layout\.addLayout\(log_header\)",
        src,
    )
    assert m, "log_header block not found"
    header_block = m.group(0)
    # status_lbl must be constructed AND added inside this block.
    assert "self.status_lbl = QLabel" in header_block, (
        "status_lbl isn't constructed inside the log_header block. "
        "Either it's still in a separate row below log_view, or "
        "the v0.5.41 layout collapse wasn't applied."
    )
    assert "log_header.addWidget(self.status_lbl" in header_block, (
        "status_lbl isn't added to log_header — would render in "
        "the wrong row."
    )
    # And status_lbl should be added BEFORE popout_btn so status
    # sits on the left, button on the right.
    status_pos = header_block.find("log_header.addWidget(self.status_lbl")
    popout_pos = header_block.find("log_header.addWidget(self.popout_btn")
    assert status_pos > 0 and popout_pos > 0
    assert status_pos < popout_pos, (
        "status_lbl is added AFTER popout_btn — they're flipped. "
        "Expected: status left, button right."
    )


def test_no_separate_status_lbl_row_below_log_view():
    """Pre-fix the dialog had a `log_layout.addWidget(self.status_lbl)`
    call AFTER `log_layout.addWidget(self.log_view, ...)`. The
    v0.5.41 fix removes that second call. There must be exactly
    ONE addWidget for status_lbl in the WHOLE dialog (the one
    inside log_header)."""
    src = _src()
    # Count occurrences of `log_layout.addWidget(self.status_lbl`.
    bad_calls = re.findall(
        r"log_layout\.addWidget\(\s*self\.status_lbl",
        src,
    )
    assert not bad_calls, (
        "log_layout.addWidget(self.status_lbl, ...) still exists "
        "→ status_lbl appears below log_view as before, rendering "
        "visually inside the white area's border."
    )


def test_popout_button_has_max_height():
    """The Pop out button must have a maximum height so it can't
    stretch vertically when given extra space in the header row.
    Pre-fix the button could grow to fill the row, exaggerating
    the empty space."""
    src = _src()
    assert re.search(
        r"self\.popout_btn\.setMaximumHeight\(\s*\d+\s*\)",
        src,
    ), (
        "popout_btn has no setMaximumHeight — can stretch when "
        "the header row gets extra vertical space."
    )


def test_status_lbl_stretches_to_fill_header():
    """The status_lbl must be added with stretch=1 in log_header so
    it grows horizontally to fill the row, pushing the Pop out
    button to the right edge. Otherwise the label sits in the
    middle with empty space on both sides."""
    src = _src()
    m = re.search(
        r"log_header\.addWidget\(\s*self\.status_lbl\s*,\s*(\d+)\s*\)",
        src,
    )
    assert m, (
        "status_lbl added to log_header without a stretch factor"
    )
    stretch = int(m.group(1))
    assert stretch >= 1, (
        f"status_lbl stretch={stretch} — Pop out button won't "
        f"reliably anchor right."
    )


def test_pyproject_version_at_least_0541():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 41), (
        f"Version {m.group(1)} < 0.5.41"
    )
