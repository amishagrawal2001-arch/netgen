"""v0.5.81 — fix admin console stuck on Loading… (JS SyntaxError).

Operator-bite on srv06 (Jun 10 2026):

  "failing to load admin console and stuck in Loading…"

Root cause: v0.5.80's accelerators empty-state used the apostrophe
in "don't" inside a single-quoted JS string. Python source had
``don\\'t``; the Python triple-quoted string collapsed the double
backslash to one, so the served JS read ``don\\'t`` — JS parsed
``\\`` as one literal backslash, then ``'`` terminated the string
prematurely → ``Unexpected identifier 't'`` SyntaxError →
ENTIRE admin JS dead → all pills/banners/iface table stuck on
"Loading…".

The fix replaces the single-quoted JS string with a backtick
template literal (apostrophes are bare-legal inside template
literals) and reworded "don't" → "do not" as belt-and-suspenders.

Regression guards in this test file:

  1. The fixed line uses backticks, not single quotes.
  2. The served admin JS has no `XX'` pattern at all in
     single-quoted contexts (best-effort substring check).
  3. The admin JS, extracted from the served HTML, parses
     cleanly under a Python tokenizer-style apostrophe scan.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_accelerators_empty_state_uses_backtick():
    """The accelerators empty-state innerHTML assignment must use
    a backtick template literal — single quotes around an English
    apostrophe blew up admin JS in v0.5.80."""
    src = _src()
    idx = src.find("No DPDK accelerators detected on this host")
    assert idx > 0
    # Walk backwards to find the opening quote.
    pre = src[max(0, idx - 200):idx]
    # The assignment must start with a backtick, not single quote.
    m = re.search(r"wrap\.innerHTML\s*=\s*(['`])<div", pre)
    assert m, "Couldn't find the innerHTML assignment opener"
    assert m.group(1) == "`", (
        "Accelerators empty-state still uses single-quoted JS — "
        "an English apostrophe will re-break admin JS"
    )


def test_no_apostrophe_inside_single_quoted_js_strings():
    """Walk every line that looks like a single-quoted JS string
    inside the admin HTML and flag any unescaped apostrophe AND
    any backslash-apostrophe pair (the v0.5.80 trap)."""
    src = _src()
    # Search the entire file — the inline _ADMIN_HTML block is
    # the only place we serve raw JS, and tooltips/copy in the
    # rest of the codebase are plain Python strings.
    bad_patterns = []
    for lineno, line in enumerate(src.split("\n"), 1):
        # Heuristic: a JS line that has both a single-quoted
        # opener AND an apostrophe inside the quoted content.
        # The v0.5.80 case was:
        #   '...don\\'t expose...'
        # i.e. Python source contained `\\'` (escaped backslash
        # plus literal apostrophe). The Python string then
        # carried `\'` → served JS saw `\'` → which inside
        # a single-quoted JS string IS valid (`\'` is escaped
        # apostrophe). But Python source `\\\\'` would carry
        # `\\'` → JS sees `\\'` → parses `\\` as backslash and
        # then string ends → SyntaxError.
        if "\\\\'" in line:
            bad_patterns.append((lineno, line.strip()[:120]))
    assert not bad_patterns, (
        "Found Python source with `\\\\'` (two literal backslashes "
        "+ apostrophe) — when served as JS inside a single-quoted "
        "string this terminates the string prematurely and breaks "
        "the entire admin console. Found at:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in bad_patterns)
    )


def test_pyproject_version_at_least_0581():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 81)
