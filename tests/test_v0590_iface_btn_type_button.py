"""v0.5.90 hot-fix — `type="button"` on iface lifecycle buttons.

Operator on srv06:

  "seems entire page is getting refreshed, when trying interface
   related activity up/down/reset... etc."

A `<button>` with no explicit `type` defaults to `type="submit"`.
If any ancestor element is a `<form>` (or some browser quirk
treats the click as a navigation), the click triggers form
submission which navigates the browser to the action URL,
reloading the whole page.

Even though the admin console doesn't currently wrap the iface
table in a form, this is brittle — any future surface adding
a form for IP management, modal dialogs, etc. would re-trigger
the page-reload bug. Defensively set `type="button"` on every
lifecycle button + add `ev.preventDefault()` + `ev.stopPropagation()`
in the click handler.

Secondary fix: the v0.5.88 disabled-state code emitted a
DUPLICATE `style=""` attribute on the button. HTML5 parser
silently drops the second one, so dimmed/disabled buttons
weren't actually dimmed. Collapsed to a single style attr.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def _lifecycle_block() -> str:
    src = _src()
    idx = src.find("let lifecycleBtn = ''")
    assert idx > 0, "lifecycleBtn block not found"
    return src[idx:idx + 3000]


def test_all_lifecycle_buttons_have_type_button():
    """Every Up/Down/Reset button in the lifecycleBtn template
    must have `type="button"` to prevent default submit behavior."""
    block = _lifecycle_block()
    # Count <button> tags in the lifecycle template.
    button_tags = re.findall(r"<button\s+([^>]+)>", block)
    # Filter to just lifecycle ones (have data-iface-action).
    lifecycle = [b for b in button_tags if "data-iface-action" in b]
    assert len(lifecycle) >= 3, (
        f"Expected ≥3 lifecycle buttons, found {len(lifecycle)}"
    )
    for attrs in lifecycle:
        assert 'type="button"' in attrs, (
            f"Lifecycle button missing type=\"button\": {attrs[:80]}"
        )


def test_no_duplicate_style_attribute_in_lifecycle_template():
    """v0.5.88 emitted `style="..." disabled style="..."` —
    HTML5 parser drops the second style attr silently. Disabled
    buttons looked normal. Anti-regression for that pattern."""
    block = _lifecycle_block()
    # Look at the literal template (between the backticks).
    m = re.search(r'lifecycleBtn = `([\s\S]+?)`;', block)
    assert m, "Couldn't extract lifecycleBtn template literal"
    tmpl = m.group(1)
    # No <button ...style="..." ... style="..."> patterns. We
    # interpolate the dim/base style into a SINGLE style attr.
    for line in tmpl.split("\n"):
        if "<button" not in line:
            continue
        # Count quoted style="..." occurrences on the line.
        # Should be 0 or 1.
        n_style = line.count('style="')
        assert n_style <= 1, (
            f"Duplicate style attr on button line: {line.strip()}"
        )


def test_click_handler_calls_preventdefault():
    """The Up/Down/Reset click handler must call
    ev.preventDefault() + ev.stopPropagation() defensively."""
    src = _src()
    # Anchor on the unique closest('button[data-iface-action]')
    # selector in the new handler; back up to find the
    # enclosing addEventListener.
    needle = "closest('button[data-iface-action]')"
    pos = src.find(needle)
    assert pos > 0, "iface-action handler selector not found"
    # Walk backwards to the addEventListener('click' opener.
    start = src.rfind("document.addEventListener('click'", 0, pos)
    assert start > 0
    # Forward-scan for the matching closing `});`.
    end = src.find("});", pos)
    assert end > 0
    body = src[start:end + 3]
    assert "ev.preventDefault()" in body, (
        "Handler doesn't call ev.preventDefault()"
    )
    assert "ev.stopPropagation()" in body, (
        "Handler doesn't call ev.stopPropagation()"
    )


def test_dim_style_uses_cursor_not_allowed():
    """When a button is disabled (e.g. Up button on already-up
    iface), the dim style should include cursor: not-allowed
    so the UX matches the disabled state."""
    block = _lifecycle_block()
    assert "cursor: not-allowed" in block, (
        "Disabled lifecycle button doesn't show not-allowed cursor"
    )


def test_pyproject_version_at_least_0590():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 90)
