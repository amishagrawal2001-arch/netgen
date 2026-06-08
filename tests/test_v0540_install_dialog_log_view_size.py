"""v0.5.40 — install/upgrade dialog log_view is taller by default.

Operator request: "increase the log text area vertical size,
inside install/upgrade server dialog."

Pre-fix `self.log_view` (QPlainTextEdit) had no
`setMinimumHeight` — at the dialog's minimum size (820×600), the
form widgets above (auth picker, wheel chooser, mode tabs,
buttons) consumed ~340px of vertical space, leaving the log_view
with ~260px. Subtract group-box padding + status_lbl below and
operators saw maybe 12-15 lines of monospace text. Long install
runs (10-30 min DPDK builds, multi-screen apt output) require
scrolling constantly to see anything.

v0.5.40:
  - `setMinimumHeight(280)` on the log_view itself so the widget
    can't collapse below ~25 lines regardless of how the dialog
    is sized
  - `setSizePolicy(Expanding, Expanding)` so it grows when the
    dialog grows
  - `log_layout.addWidget(self.log_view, 1)` with stretch=1 so
    the log gets every extra pixel inside its group-box (status_lbl
    keeps its natural height)
  - Dialog `setMinimumSize(900, 780)` instead of 820×600 so the
    operator's default-opened dialog already shows ~36 visible
    log lines

These tests pin the four sizing settings. Anyone trimming them
without operator request earns a test failure here.
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


def test_dialog_minimum_size_bumped():
    """The dialog's setMinimumSize must give the log_view room to
    breathe. 820×600 is the pre-fix size; v0.5.40 expects ≥ 900
    wide and ≥ 720 tall (allowing some bottom growth for the
    Close button row)."""
    src = _src()
    m = re.search(
        r"self\.setMinimumSize\(\s*(\d+)\s*,\s*(\d+)\s*\)",
        src,
    )
    assert m, "self.setMinimumSize call not found in dialog"
    w, h = int(m.group(1)), int(m.group(2))
    assert w >= 900 and h >= 720, (
        f"Dialog minimum size is {w}×{h}; v0.5.40 requires "
        f"≥ 900×720 so the log_view shows enough lines out of "
        f"the box."
    )


def test_log_view_has_min_height():
    """log_view must have a setMinimumHeight floor so the widget
    can't collapse below a usable size if the dialog is resized
    small."""
    src = _src()
    # Find the log_view section.
    m = re.search(
        r"self\.log_view\s*=\s*QPlainTextEdit\(\)([\s\S]+?)log_layout\.addWidget\(self\.log_view",
        src,
    )
    assert m, "log_view construction block not found"
    block = m.group(0)
    height_m = re.search(
        r"self\.log_view\.setMinimumHeight\(\s*(\d+)\s*\)",
        block,
    )
    assert height_m, (
        "log_view has no setMinimumHeight floor. v0.5.40 fix "
        "isn't applied — widget can collapse to nothing if the "
        "dialog is resized small."
    )
    height = int(height_m.group(1))
    # 280px ≈ 25 lines at 11px monospace + line spacing. The fix
    # uses 280; allow ≥ 240 in case a future tweak trims it.
    assert height >= 240, (
        f"log_view setMinimumHeight={height} is too small. "
        f"~25 lines of monospace text needs ≥ 240px."
    )


def test_log_view_size_policy_expanding():
    """log_view must use QSizePolicy.Expanding so it grows when
    the dialog grows — without it the widget stays at minimum
    and a resized dialog has empty space below the log."""
    src = _src()
    m = re.search(
        r"self\.log_view\s*=\s*QPlainTextEdit\(\)([\s\S]+?)log_layout\.addWidget\(self\.log_view",
        src,
    )
    block = m.group(0)
    assert re.search(
        r"self\.log_view\.setSizePolicy\(\s*"
        r"QSizePolicy\.Expanding,\s*QSizePolicy\.Expanding",
        block,
    ), (
        "log_view doesn't use QSizePolicy.Expanding vertically. "
        "Widget won't grow when the operator enlarges the dialog "
        "— wastes vertical space."
    )


def test_log_view_added_with_stretch_in_inner_layout():
    """The inner log_layout.addWidget call must have a stretch
    factor ≥ 1 so log_view absorbs the extra pixels inside its
    group-box (instead of the status_lbl below getting any of it)."""
    src = _src()
    m = re.search(
        r"log_layout\.addWidget\(\s*self\.log_view\s*,\s*(\d+)\s*\)",
        src,
    )
    assert m, (
        "log_layout.addWidget(self.log_view, N) not found with a "
        "stretch factor. log_view added without stretch will sit "
        "at its minimum even when log_box has extra room."
    )
    stretch = int(m.group(1))
    assert stretch >= 1, (
        f"log_view added with stretch={stretch}. Use ≥1 so it "
        f"absorbs extra vertical space inside log_box."
    )


def test_qsizepolicy_imported():
    """Sanity — QSizePolicy must be in the PyQt5.QtWidgets import
    block. Without it the new code NameErrors at construction."""
    src = _src()
    imp_m = re.search(
        r"from\s+PyQt5\.QtWidgets\s+import\s*\(([\s\S]+?)\)",
        src,
    )
    assert imp_m, "PyQt5.QtWidgets import block not found"
    assert "QSizePolicy" in imp_m.group(1), (
        "QSizePolicy not imported. v0.5.40's setSizePolicy call "
        "would NameError at dialog construction."
    )


def test_pyproject_version_at_least_0540():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 40), (
        f"Version {m.group(1)} < 0.5.40"
    )
