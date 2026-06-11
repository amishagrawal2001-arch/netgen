"""Operator asked for the install guide to be slimmed. These
guards prevent future PRs from silently re-bloating it back to
the 508-line / 1313-HTML-line state that prompted the rewrite.

If you find yourself needing to bump these caps to land a real
fix (e.g. a critical new install step), bump them — but only
after asking whether the new content really belongs in the
quickstart vs a separate doc.
"""
from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_INSTALL_MD = _REPO / "INSTALL.md"
_DIALOG = _REPO / "widgets" / "stream_dialog.py"


# Slim caps — INSTALL.md and the in-app HTML guide must stay
# focused on the quickstart + one-screen reference. If either
# of these limits is breached, the guide has drifted back
# toward the verbose state the operator complained about.
MAX_INSTALL_MD_LINES = 250
MAX_INSTALL_GUIDE_HTML_LINES = 350


def test_install_md_under_line_cap():
    n = sum(1 for _ in _INSTALL_MD.read_text().splitlines())
    assert n <= MAX_INSTALL_MD_LINES, (
        f"INSTALL.md is {n} lines — over the slim cap of "
        f"{MAX_INSTALL_MD_LINES}. Either trim it or, if the new "
        f"content is genuinely warranted, bump this cap and "
        f"justify in the commit message."
    )


def test_install_guide_html_under_line_cap():
    src = _DIALOG.read_text()
    i = src.find('_INSTALL_GUIDE_HTML = r"""')
    j = src.find('"""\n\n\ndef show_install_guide', i)
    assert i > 0 and j > i
    block = src[i:j]
    n = block.count("\n")
    assert n <= MAX_INSTALL_GUIDE_HTML_LINES, (
        f"_INSTALL_GUIDE_HTML is {n} lines — over the slim cap "
        f"of {MAX_INSTALL_GUIDE_HTML_LINES}. The in-app guide "
        f"and INSTALL.md should stay 1:1; if new content is "
        f"needed, add to both and bump both caps."
    )


def test_install_md_leads_with_quickstart():
    """First non-title section heading should be the quickstart —
    operator-time-to-first-command matters most."""
    lines = _INSTALL_MD.read_text().splitlines()
    # Find the first ## heading after the # title.
    h2s = [ln for ln in lines if ln.startswith("## ")]
    assert h2s, "INSTALL.md has no ## headings"
    assert "Quickstart" in h2s[0] or "Quick start" in h2s[0], (
        f"First INSTALL.md ## is {h2s[0]!r} — should be the "
        f"quickstart so a new operator gets their command line "
        f"immediately"
    )


def test_install_guide_html_leads_with_quickstart():
    src = _DIALOG.read_text()
    i = src.find('_INSTALL_GUIDE_HTML = r"""')
    j = src.find('"""\n\n\ndef show_install_guide', i)
    block = src[i:j]
    # The first <h2> should mention Quickstart.
    import re
    h2_match = re.search(r"<h2>([^<]+)</h2>", block)
    assert h2_match
    first_h2 = h2_match.group(1)
    assert "Quickstart" in first_h2 or "Quick start" in first_h2, (
        f"First <h2> in install guide HTML is {first_h2!r} — "
        f"should be quickstart"
    )


def test_install_md_and_html_section_count_aligned():
    """The in-app guide and INSTALL.md should have the same
    top-level structure. If they drift, operators reading the
    repo on GitHub and operators reading Help → Install Guide
    in the client get different content — a maintenance trap."""
    import re
    md_h2 = [
        ln.lstrip("# ").strip()
        for ln in _INSTALL_MD.read_text().splitlines()
        if ln.startswith("## ")
    ]
    src = _DIALOG.read_text()
    i = src.find('_INSTALL_GUIDE_HTML = r"""')
    j = src.find('"""\n\n\ndef show_install_guide', i)
    block = src[i:j]
    html_h2 = re.findall(r"<h2>([^<]+)</h2>", block)
    # Count parity — exact text can differ (rendering nuances),
    # but the section count should match within ±2.
    diff = abs(len(md_h2) - len(html_h2))
    assert diff <= 2, (
        f"INSTALL.md has {len(md_h2)} sections, "
        f"_INSTALL_GUIDE_HTML has {len(html_h2)} — sections "
        f"have drifted (diff={diff}). The two should mirror "
        f"each other so operators get the same content "
        f"regardless of where they read."
    )
