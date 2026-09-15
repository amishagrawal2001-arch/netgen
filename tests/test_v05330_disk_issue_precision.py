"""v0.5.330 — disk-critical issue formatter shows KB/bytes when
free < 1 MB, instead of always rounding to 0 MB.

Operator on srv06 got:
    "Disk critical: tmp only 0 MB free"
    "Disk critical: opt_netgen only 0 MB free"

with no way to tell if:
  (a) the disk is genuinely at 0 bytes
  (b) 100 KB free (still critical, but not literally 0)
  (c) some systemd sandbox artifact making the process see a tiny
      view of the filesystem
  (d) a units bug in the check

All four render the same "0 MB free" string. Fix: keep the alert
threshold on MB (unchanged firing conditions), but drop the DISPLAY
to KB or bytes when free < 1 MB so the message is unambiguous.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def test_marker_present():
    src = _server_src()
    assert "v0.5.330 (audit disk-issue-precision)" in src


def test_disk_dict_stashes_free_bytes():
    """The per-path disk dict must now carry raw `free_bytes`
    alongside the pre-computed `free_mb`, so the formatter can
    drop to KB/bytes precision when free is very low."""
    src = _server_src()
    # Look at the disk[_label] assignment.
    idx = src.index('"free_mb": _u.free // (1024 * 1024)')
    body = src[max(0, idx - 500):idx + 500]
    assert '"free_bytes": _u.free' in body


def test_formatter_helper_defined():
    """`_fmt_free_bytes` is the KB/MB/bytes formatter."""
    src = _server_src()
    assert "def _fmt_free_bytes(free_bytes: int) -> str:" in src


def test_formatter_uses_mb_when_ge_one_mb():
    """The formatter still shows MB when free >= 1 MB (unchanged
    behavior for the common case)."""
    # Extract and eval the formatter directly.
    src = _server_src()
    idx = src.index("def _fmt_free_bytes(free_bytes: int) -> str:")
    end = src.index("\n    _disk_issues = []", idx)
    fn_src = src[idx:end].strip()
    # Dedent so it evals in a module-level namespace.
    import textwrap
    fn_src = textwrap.dedent(fn_src)
    ns = {}
    exec(fn_src, ns)
    fmt = ns["_fmt_free_bytes"]
    assert fmt(1024 * 1024) == "1 MB"
    assert fmt(500 * 1024 * 1024) == "500 MB"


def test_formatter_uses_kb_when_lt_one_mb():
    """The whole point — free in (1 KB, 1 MB) shows as KB instead
    of rounding down to 0 MB."""
    src = _server_src()
    idx = src.index("def _fmt_free_bytes(free_bytes: int) -> str:")
    end = src.index("\n    _disk_issues = []", idx)
    fn_src = src[idx:end].strip()
    import textwrap
    fn_src = textwrap.dedent(fn_src)
    ns = {}
    exec(fn_src, ns)
    fmt = ns["_fmt_free_bytes"]
    # 900 KB = 921_600 bytes → "900 KB"
    assert fmt(900 * 1024) == "900 KB"
    # 1 KB exactly
    assert fmt(1024) == "1 KB"


def test_formatter_uses_bytes_when_lt_one_kb():
    """Genuinely near-zero → bytes."""
    src = _server_src()
    idx = src.index("def _fmt_free_bytes(free_bytes: int) -> str:")
    end = src.index("\n    _disk_issues = []", idx)
    fn_src = src[idx:end].strip()
    import textwrap
    fn_src = textwrap.dedent(fn_src)
    ns = {}
    exec(fn_src, ns)
    fmt = ns["_fmt_free_bytes"]
    assert fmt(500) == "500 bytes"
    assert fmt(0) == "0 bytes"


def test_critical_alert_uses_display_string_not_zero_mb():
    """The `Disk critical:` alert now uses `_free_display` (which
    drops to KB/bytes), NOT the raw `free_mb` integer that was
    always 0 for sub-1-MB values."""
    src = _server_src()
    # Locate the critical-alert line specifically.
    idx = src.index('"Disk critical: {_label} only')
    # End the slice at the closing paren of THIS f-string
    # (before we walk into the sibling "Disk low:" branch).
    end = src.index(")", idx)
    line = src[idx:end]
    assert "{_free_display}" in line
    # Critical line MUST NOT use the pre-v0.5.330 form.
    assert "{_info['free_mb']}" not in line


def test_threshold_logic_unchanged():
    """Alert firing conditions (which paths trigger critical vs low)
    stay on free_mb, per the pre-v0.5.330 semantics — v0.5.330
    only changes the DISPLAY, not the trigger. Critical fires at
    free_mb < 100; low fires at free_mb < 1024."""
    src = _server_src()
    # Both `< 100` and `< 1024` thresholds still present.
    critical_idx = src.index('_info["free_mb"] < 100')
    low_idx = src.index('_info["free_mb"] < 1024', critical_idx)
    assert critical_idx > 0
    assert low_idx > critical_idx


def test_fallback_when_free_bytes_missing():
    """Older code paths may store a dict without free_bytes.
    Formatter falls back to `free_mb * 1024 * 1024` — safe
    upper-bound (loses sub-MB precision but doesn't crash)."""
    src = _server_src()
    idx = src.index("_free_bytes = _info.get(")
    body = src[idx:idx + 300]
    assert '_free_bytes = _info["free_mb"] * 1024 * 1024' in body


def test_server_ast_parses():
    import ast
    ast.parse(_server_src())
