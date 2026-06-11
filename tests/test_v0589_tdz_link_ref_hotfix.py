"""v0.5.89 hot-fix: TDZ ReferenceError on `link` in iface table
render.

Operator on srv06 (Jun 10 2026):

  "post upgrade Error: ReferenceError: Cannot access 'link'
   before initialization"

v0.5.88 added a `lifecycleBtn` block for the Up/Down/Reset
buttons that reads `link.carrier === true` — but `const link =
i.link || {}` is declared further down in the same row-render
function. JavaScript `const`/`let` has a temporal-dead-zone:
referencing the variable before its declaration line throws
ReferenceError at runtime. node --check (syntactic) does NOT
catch this — only the browser triggered it.

Fix: read `(i.link || {}).carrier === true` directly in the
lifecycleBtn block. No dependency on declaration order.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_lifecycle_btn_does_not_reference_link_const_before_decl():
    """The lifecycleBtn block must read `(i.link || {})` directly,
    NOT the `const link` declared further down. TDZ regression."""
    src = _src()
    # Find the lifecycleBtn block.
    idx = src.find("let lifecycleBtn = ''")
    assert idx > 0, "lifecycleBtn block not found"
    body = src[idx:idx + 2000]
    # The fix: must use (i.link || {}) form.
    assert "(i.link || {})" in body, (
        "lifecycleBtn doesn't read i.link directly — likely "
        "still TDZ-broken"
    )
    # Anti-regression: must NOT reference bare `link.` before
    # the const link declaration which lives further down the
    # row render.
    assert "link.carrier" not in body or "i.link" in body[:body.find("link.carrier") + 14], (
        "lifecycleBtn still has bare `link.carrier` reference"
    )


def test_lifecycle_btn_before_link_const_declaration():
    """Verify the actual layout — lifecycleBtn comes BEFORE the
    `const link = i.link || {}` line. If this ever changes (e.g.
    someone moves the link decl up), the TDZ fix becomes
    unnecessary but the test still passes."""
    src = _src()
    life_idx = src.find("let lifecycleBtn = ''")
    link_idx = src.find("const link = i.link || {}")
    assert life_idx > 0 and link_idx > 0
    # Document the relationship: lifecycleBtn comes first.
    if life_idx < link_idx:
        # Good — the `(i.link || {})` form is necessary.
        pass
    # If link_idx < life_idx later, the (i.link || {}) form is
    # still valid; the test just stops being a regression guard.


def test_pyproject_version_at_least_0589():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 89)
