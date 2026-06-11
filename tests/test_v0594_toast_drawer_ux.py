"""v0.5.94 — audit batch 3: toast + drawer UX.

  H9 — Sticky error-toast regex was `/^(failed:|✗ |⚠ )/i`.
        Most callers say `toast('X request failed: ' + e)` which
        starts with "X request failed:" not "Failed:" — so the
        sticky check missed and the error auto-dismissed in 3s.
        Operator walked away assuming success.

  H10 — Open ℹ️ Details drawer was destroyed on every refresh.
        `refreshInterfaces()` replaces `wrap.innerHTML` so the
        drawer sibling row vanishes 700ms after the operator
        clicks any lifecycle button — the diagnostic tool blew
        itself away during the diagnosis it was built for.

  M7 — Down/Reset confirm was bare `Bring down ens6np0?`. Bind
        already enriches with IP-count + stream-count summary;
        bringing down a NIC running streams is equally
        disruptive. Same shape now.

  M8 — Lifecycle buttons (↑/↓/↻/💡/ℹ️) had no `aria-label`.
        Screen readers announced "up arrow button" with no
        context.

  L5 — v0.5.88's `confirm(`${data.error}\\n\\nForce ${action}
        anyway?`)` used `\\n` which in the raw-string-served
        template is literal `\n` text, not a newline. Operator
        saw the warning + force-prompt on one squashed line.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── H9: sticky toast regex matches " failed:" anywhere ───


def test_sticky_regex_catches_x_failed_messages():
    src = _src()
    # Find the regex assignment.
    m = re.search(r"const sticky\s*=\s*(/[^/]+/[a-z]*)", src)
    assert m, "sticky regex line not found"
    pattern_text = m.group(1)
    # Must extend beyond the v0.5.93 prefix-only check.
    # Look for either the alternation (^pre|\sword:) or an "i"
    # flag on a regex that matches "failed" mid-string.
    assert "failed" in pattern_text or "(failed" in pattern_text
    # The fix uses `\s(failed|error)[:.]` or similar — verify
    # an explicit mid-string match exists.
    assert (
        re.search(r"\\s\(?failed", pattern_text)
        or re.search(r"\\sfailed", pattern_text)
    ), (
        "Sticky regex doesn't catch 'X failed: ...' style "
        "messages — operators will miss errors"
    )


# ─── H10: drawer restored after refresh ───


def test_open_drawers_tracked_in_set():
    src = _src()
    assert "_openIfaceDrawers" in src
    assert re.search(
        r"_openIfaceDrawers\s*=\s*new\s+Set\(\)",
        src,
    )


def test_drawer_toggle_adds_to_open_set():
    src = _src()
    idx = src.find("async function ifaceDetailsToggle(")
    assert idx > 0
    body = src[idx:idx + 3000]
    assert "_openIfaceDrawers.add(iface.name)" in body
    assert "_openIfaceDrawers.delete(iface.name)" in body


def test_refresh_interfaces_restores_open_drawers():
    src = _src()
    idx = src.find("async function refreshInterfaces(")
    assert idx > 0
    # refreshInterfaces is huge (the iface row render is inline).
    # Find the matching `}` that closes it by scanning balanced
    # braces — but simpler: locate the next top-level `async
    # function` or `function` after refreshInterfaces and use
    # that as the bound.
    rest = src[idx:]
    next_fn = re.search(
        r"\n    (async function|function) [a-zA-Z_]+\(",
        rest[200:],  # skip the opening declaration itself
    )
    end = (200 + next_fn.start()) if next_fn else len(rest)
    body = rest[:end]
    # After the table re-renders, the finally block iterates
    # _openIfaceDrawers and re-opens each.
    assert "_openIfaceDrawers" in body, (
        "refreshInterfaces doesn't reference _openIfaceDrawers"
    )
    assert "ifaceDetailsToggle" in body, (
        "refreshInterfaces doesn't re-open closed drawers"
    )


# ─── M7: enriched down/reset confirm ───


def test_down_reset_confirm_enriched_with_ips_and_streams():
    src = _src()
    # Find the click handler's confirm flow.
    m = re.search(
        r"realAction === 'down' \|\| realAction === 'reset'"
        r"[\s\S]+?if \(!confirm\([^)]+\)\) return;",
        src,
    )
    assert m, "down/reset confirm flow not found"
    body = m.group(0)
    # IP-count summary line.
    assert "ipCount" in body
    # Stream-count summary line.
    assert "sCount" in body or "summary" in body
    # Persistence hint.
    assert "reverts on reboot" in body


# ─── M8: aria-label on lifecycle buttons ───


def test_lifecycle_buttons_have_aria_label():
    src = _src()
    # All 5 lifecycle buttons must have aria-label.
    block_start = src.find("let lifecycleBtn = ''")
    assert block_start > 0
    block = src[block_start:block_start + 3000]
    for action in ("up", "down", "reset", "flash", "details"):
        m = re.search(
            rf'data-iface-action="{action}"[^>]+',
            block,
        )
        assert m, f"Button for {action} not found"
        assert "aria-label" in m.group(0), (
            f"Button for {action} missing aria-label — screen "
            f"reader users get no context"
        )


# ─── L5: literal \n confirm bug ───


def test_force_confirm_uses_real_newlines():
    src = _src()
    # The fixed line uses `\n\n` (single backslash). The pre-
    # fix `\\n\\n` (double backslash in source) rendered as
    # literal `\n` text. We want exactly one backslash before
    # each n.
    m = re.search(
        r'confirm\(`\${data\.error}[^`]*Force \${action} anyway',
        src,
    )
    assert m, "force-confirm prompt not found"
    snippet = m.group(0)
    assert r"\\n" not in snippet, (
        "Force-confirm still uses literal `\\n` — operator sees "
        "the message on one line"
    )
    assert r"\n" in snippet


# ─── Version ───


def test_pyproject_version_at_least_0594():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 94)
