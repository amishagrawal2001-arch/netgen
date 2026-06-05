"""Regression test for the install-log popout visibility bug.

Operator reported during a successful Fresh Install: "popout window
does not show the progression logs similar to log text area of
Fresh install" — i.e. the popout opened, the inline log filled
with progress, but the popout stayed mostly blank.

Root cause: the popout's QPlainTextEdit had a HARDCODED DARK
stylesheet (``background:#0f172a; color:#e2e8f0``). The inline
``self.log_view`` has NO color override — its
``appendPlainText(line)`` calls bake the inline view's default
foreground color (typically black on light hosts) into the shared
QTextDocument. The popout shares the document but renders against
its DARK background → all default-color info lines became
invisible black-on-dark-blue. Only the explicit
``appendHtml('<span style="color:#dc2626">...')`` ERROR/WARN/OK
lines survived because they carried their own colors.

Fix: the popout inherits the inline log_view's stylesheet (no
background override) and only layers cosmetic tweaks on top.

This test pins the structural invariant — popout MUST inherit the
inline view's stylesheet, NOT override it with a background-color
that breaks the document's foreground assumptions."""
from __future__ import annotations

import re

_DIALOG_PATH = "/Users/surajsharma/dev/netgen/widgets/install_server_dialog.py"


def _toggle_popout_body() -> str:
    """Return the source of _toggle_log_popout for inspection."""
    src = open(_DIALOG_PATH).read()
    m = re.search(
        r"def _toggle_log_popout\(self\).*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert m, "_toggle_log_popout method not found"
    return m.group(0)


def test_popout_inherits_inline_view_stylesheet():
    """The popout's QPlainTextEdit must inherit the inline log_view's
    stylesheet so default-color text from the inline view's
    appendPlainText calls stays readable in the popout."""
    body = _toggle_popout_body()
    assert "self.log_view.styleSheet()" in body, (
        "Popout must inherit the inline log_view's stylesheet — "
        "without this, default-color text (the bulk of install "
        "output) renders invisible when the popout's background "
        "differs from the inline view's."
    )


def test_popout_does_not_force_dark_background():
    """The pre-fix bug: popout hardcoded ``background:#0f172a``
    (slate-900). Any background override that contrasts with the
    document's foreground colors will silently hide content.

    Strategy: strip out comments + docstrings first, then assert
    on the remaining executable code. Otherwise the explanatory
    comment in _toggle_log_popout (which quotes the pre-fix hex
    string to explain the regression) trips this test."""
    body = _toggle_popout_body()
    code_lines = []
    in_docstring = False
    docstring_marker = None
    for line in body.split("\n"):
        stripped = line.strip()
        # Track triple-quoted docstrings (very rough but enough)
        for marker in ('"""', "'''"):
            count = stripped.count(marker)
            if count == 1:
                if not in_docstring:
                    in_docstring = True
                    docstring_marker = marker
                elif marker == docstring_marker:
                    in_docstring = False
                    docstring_marker = None
                continue
        if in_docstring:
            continue
        # Strip inline # comments and pure-comment lines
        if stripped.startswith("#"):
            continue
        if "#" in line:
            # Crude — if a # appears outside a string we'd want to
            # strip it. For this test, just keep everything before
            # the first # that's not inside a literal. Good enough.
            code_lines.append(line.split("#", 1)[0])
        else:
            code_lines.append(line)
    code_only = "\n".join(code_lines)

    # Now check: in the executable code, no dark background should
    # appear without a paired color: rule, AND specifically the
    # pre-fix slate-900 hex must not return.
    assert "#0f172a" not in code_only, (
        "Pre-fix dark background (#0f172a) re-introduced in executable "
        "code — this hides default-color text in the popout."
    )
    # background: rules in code must be paired with color:
    bg_in_code = re.findall(r"background\s*:\s*[^;\"\']+", code_only)
    if bg_in_code:
        assert "color:" in code_only, (
            f"Popout sets a background ({bg_in_code}) without a "
            f"paired color: rule — risk of invisible-text bug. Set "
            f"both background AND color, or inherit the inline view's "
            f"stylesheet."
        )


def test_popout_uses_setdocument_for_sync():
    """The popout must use ``setDocument(self.log_view.document())``
    to share the QTextDocument with the inline view — without this,
    the popout shows nothing OR the dialog has to mirror text
    manually (fragile + complicates the append paths)."""
    body = _toggle_popout_body()
    assert "setDocument(self.log_view.document())" in body, (
        "Popout must use document sharing via setDocument — without "
        "this the popout doesn't reflect new log lines in real time."
    )


def test_popout_subscribes_to_contents_changed_for_scroll():
    """The popout connects to ``contentsChanged`` for auto-scroll
    behavior. If this wiring breaks, the popout shows content but
    never scrolls to the latest line — operator sees a frozen
    snapshot."""
    body = _toggle_popout_body()
    assert "document().contentsChanged.connect" in body, (
        "Popout missing contentsChanged signal hookup — auto-scroll "
        "won't fire as new install log lines arrive."
    )


def test_popout_does_not_override_font_size():
    """Two QPlainTextEdits sharing a QTextDocument MUST render with
    the same font, or block heights computed against the FIRST
    view's font are wrong for the SECOND view → text overlaps into
    adjacent block space.

    The v0.3.16-a5e3961 fix appended ``font-size: 12px`` to the
    popout's stylesheet while the inline view stayed at 11px → the
    popout's lines rendered 1px taller than the document allocated
    per block → consecutive lines visibly overlapped. Operator
    reported "popout window is still not able to show the logs,
    seems overlapping inside the window".

    Guard: no font-size override should appear in the executable
    code of the popout. (Comments may mention 'font-size' as part
    of the regression-explanation.)"""
    body = _toggle_popout_body()
    # Strip docstrings + comments before checking
    code_lines = []
    in_docstring = False
    docstring_marker = None
    for line in body.split("\n"):
        stripped = line.strip()
        for marker in ('"""', "'''"):
            if stripped.count(marker) == 1:
                if not in_docstring:
                    in_docstring = True
                    docstring_marker = marker
                elif marker == docstring_marker:
                    in_docstring = False
                    docstring_marker = None
                continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        if "#" in line:
            code_lines.append(line.split("#", 1)[0])
        else:
            code_lines.append(line)
    code_only = "\n".join(code_lines)

    assert "font-size" not in code_only, (
        "Popout sets a font-size override in executable code — the "
        "shared QTextDocument's per-block line heights are computed "
        "against the inline view's font metrics. If the popout's "
        "font-size differs, text overlaps. Inherit the inline view's "
        "stylesheet + font instead."
    )


def test_popout_uses_setfont_for_font_inheritance():
    """Explicit ``view.setFont(self.log_view.font())`` ensures the
    popout's QFont object IS the inline view's font, not just
    similar-looking stylesheet font-family. setStyleSheet's
    font-family rule alone can produce different QFont objects with
    different metrics (especially around line-spacing), so we need
    setFont() to copy the exact instance."""
    body = _toggle_popout_body()
    assert "setFont(self.log_view.font())" in body, (
        "Popout must call setFont(self.log_view.font()) — without "
        "an explicit font copy, the popout may resolve a similar-but-"
        "different QFont that has slightly different line-height "
        "metrics, causing the same overlap bug."
    )
