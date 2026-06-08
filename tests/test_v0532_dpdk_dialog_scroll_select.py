"""v0.5.32 — Make DPDK Ready dialog log viewer must scroll + allow copy.

Operator-reported on srv06:
  "can see the full logs due to no scroll on make dpdk,
   also copy is not allowed, pls check."

(The reported sentence is missing a 'not' — "can NOT see the full
logs". The bug is real either way.)

The MakeDpdkReadyDialog used a QLabel for its `_detail` area —
which renders status text, action descriptions, AND the inline
v0.5.20 log tail when install_dpdk.sh fails. QLabel has two
operator-blocking UX bugs that compounded into "can't read logs,
can't copy them":

  1. NO SCROLL.  QLabel renders as a single block at whatever
     height the layout gives it. A 30-line log tail with a
     multi-line meson error and inlined apt failure output
     overflowed the label's area; operators saw only the top
     ~10 lines and the rest was cropped off.

  2. NO TEXT SELECTION / COPY.  By default QLabel doesn't allow
     text-select. `setTextInteractionFlags(Qt.TextSelectableByMouse)`
     enables click-drag selection but Ctrl+C and right-click-Copy
     still don't work — QLabel has no clipboard integration the
     way QTextEdit / QTextBrowser do. Operators couldn't paste
     the failed log into a chat / bug report.

v0.5.32 swaps QLabel → QTextBrowser:
  - Built-in scrollbars (configurable via setMinimum/MaximumHeight)
  - Text-selectable by default in read-only mode
  - Ctrl+C / right-click-Copy work out of the box
  - setText() still accepts the rich HTML the existing call sites
    send (no change to call-site code)
  - setReadOnly(True) prevents accidental editing

These tests pin the swap. Anyone reverting to QLabel for the
detail area, or removing the min-height that ensures enough
log lines are visible without scroll, earns a test failure.
"""
from __future__ import annotations

import re
from pathlib import Path


_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


def test_detail_widget_is_qtextbrowser_not_qlabel():
    """The _detail widget must be a QTextBrowser (or QTextEdit).
    A QLabel here is the v0.5.32 regression: no scroll, no copy."""
    src = _DIALOG.read_text()
    # Look for the _detail assignment.
    m = re.search(
        r"self\._detail\s*=\s*Q(TextBrowser|TextEdit)\(\)",
        src,
    )
    assert m, (
        "self._detail isn't a QTextBrowser/QTextEdit — operators "
        "can't scroll long log tails or copy text. Revert this and "
        "the v0.5.20 inline log surface becomes operator-hostile."
    )


def test_detail_widget_is_read_only():
    """The detail area is a display widget; it must be read-only
    so operators can't accidentally type into the log + then
    submit garbled-looking 'edited log' content in bug reports."""
    src = _DIALOG.read_text()
    # Find the block of code around the _detail assignment.
    m = re.search(
        r"self\._detail\s*=\s*Q(TextBrowser|TextEdit)\(\)([\s\S]+?)outer\.addWidget\(self\._detail",
        src,
    )
    assert m, "_detail construction block not found"
    block = m.group(0)
    assert "setReadOnly(True)" in block, (
        "_detail widget isn't setReadOnly(True) — operators could "
        "type into the log viewer."
    )


def test_detail_widget_has_minimum_height_for_log_tail():
    """The v0.5.20 inline log tail surfaces 30 lines + a header.
    With min-height too small, the operator gets a tiny widget
    that's technically scrollable but useless. Pin a reasonable
    floor."""
    src = _DIALOG.read_text()
    m = re.search(
        r"self\._detail\s*=\s*Q(TextBrowser|TextEdit)\(\)([\s\S]+?)outer\.addWidget\(self\._detail",
        src,
    )
    block = m.group(0)
    height_m = re.search(r"setMinimumHeight\((\d+)\)", block)
    assert height_m, (
        "_detail has no setMinimumHeight — could collapse to "
        "nothing on a tight layout."
    )
    px = int(height_m.group(1))
    # ~12 lines of 11px font + line spacing → roughly 160-200px.
    # 100 is a generous lower bound that still surfaces the bug.
    assert px >= 100, (
        f"_detail minimum height {px}px is too small — the log "
        f"tail would only show ~5 lines, defeating the v0.5.32 fix."
    )


def test_detail_widget_added_with_stretch():
    """The widget must be added to its layout with a non-zero
    stretch factor so it actually expands to fill the dialog as
    the operator resizes. Without stretch, the widget stays at
    its minimum and the rest of the dialog grows around empty
    space."""
    src = _DIALOG.read_text()
    # Look for addWidget(self._detail, N) where N > 0
    m = re.search(
        r"outer\.addWidget\(\s*self\._detail\s*,\s*(\d+)\s*\)",
        src,
    )
    assert m, (
        "self._detail isn't added with a stretch factor — won't "
        "expand on dialog resize."
    )
    stretch = int(m.group(1))
    assert stretch >= 1, (
        f"_detail stretch factor {stretch} is 0 — widget won't "
        f"grow with the dialog."
    )


def test_qtextbrowser_is_imported():
    """Sanity — the new widget class must be in the import block."""
    src = _DIALOG.read_text()
    # Find the PyQt5.QtWidgets import block.
    m = re.search(
        r"from\s+PyQt5\.QtWidgets\s+import\s*\(([\s\S]+?)\)",
        src,
    )
    assert m, "PyQt5.QtWidgets import block not found"
    imports = m.group(1)
    assert "QTextBrowser" in imports or "QTextEdit" in imports, (
        "QTextBrowser / QTextEdit not imported but used for "
        "self._detail — would NameError at dialog construction."
    )


def test_setup_rdma_dialog_still_uses_qtextedit():
    """SetupRdmaDialog (v0.5.27) already used QTextEdit correctly.
    This test confirms it wasn't accidentally regressed to QLabel
    when v0.5.32 made the DPDK dialog fix."""
    rdma_dialog = (
        Path(__file__).resolve().parents[1]
        / "widgets" / "setup_rdma_dialog.py"
    )
    src = rdma_dialog.read_text()
    m = re.search(
        r"self\.log_view\s*=\s*Q(TextBrowser|TextEdit)\(\)",
        src,
    )
    assert m, (
        "SetupRdmaDialog's log_view isn't QTextEdit/QTextBrowser "
        "— v0.5.32 must not have regressed this dialog while "
        "fixing the DPDK one."
    )


def test_pyproject_version_at_least_0532():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 32), (
        f"Version {m.group(1)} < 0.5.32"
    )
