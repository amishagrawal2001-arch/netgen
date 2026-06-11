"""v0.5.79 — toast queue, enriched bind confirm, IP modal a11y.

  - MEDIUM #5: toast() rewritten as a stacked queue (each call
    creates a child node in #toast-host). Sticky for failure
    messages (text starts with "Failed:" / "✗ " / "⚠ "),
    auto-dismissed after 3s for success.
  - MEDIUM #12: enriched bind confirm includes IP count + running
    stream count for the iface — pre-fix the operator only saw
    "It will become invisible to the kernel".
  - MEDIUM #7: IP modal a11y — Escape closes, Tab cycles
    inside, focus restored to the element that opened the
    modal on close, input auto-focused on open.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── Toast queue (MEDIUM #5) ───


def test_toast_uses_host_container():
    src = _src()
    # The new toast() must create/append into a host element.
    assert "'toast-host'" in src or "id = 'toast-host'" in src
    idx = src.find("const toast = (msg) =>")
    assert idx > 0
    # v0.5.94: window grown from 1500 → 2500 to accommodate the
    # comment block added to the sticky regex.
    body = src[idx:idx + 2500]
    assert "appendChild" in body
    assert "document.createElement" in body, (
        "toast() doesn't create individual child nodes"
    )


def test_toast_sticky_pattern_for_failures():
    src = _src()
    idx = src.find("const toast = (msg) =>")
    # v0.5.94: window grown from 1500 → 2500 to accommodate the
    # comment block added to the sticky regex.
    body = src[idx:idx + 2500]
    # Failures detected by the leading "Failed:" / "✗ " / "⚠ ".
    assert re.search(r"\^.*failed:.*\|", body, re.IGNORECASE), (
        "Toast doesn't detect failure-leading patterns"
    )
    assert "sticky" in body, "No sticky path for failure toasts"


def test_toast_auto_dismiss_for_success():
    src = _src()
    idx = src.find("const toast = (msg) =>")
    # v0.5.94: window grown from 1500 → 2500 to accommodate the
    # comment block added to the sticky regex.
    body = src[idx:idx + 2500]
    # 3s timeout on the non-sticky path.
    assert re.search(r"setTimeout\([^,]+,\s*3000\)", body), (
        "Success toasts don't auto-dismiss after 3s"
    )


# ─── Bind confirm enrichment (MEDIUM #12) ───


def test_bind_confirm_includes_ip_count():
    src = _src()
    # The confirm builder reads _ifaceIPs and counts.
    idx = src.find("if (action === 'bind')")
    assert idx > 0
    body = src[idx:idx + 2000]
    assert "_ifaceIPs" in body
    assert "ipv4" in body and "ipv6" in body
    assert "IP address" in body, (
        "Bind confirm doesn't mention IP addresses"
    )


def test_bind_confirm_includes_running_streams_count():
    src = _src()
    idx = src.find("if (action === 'bind')")
    body = src[idx:idx + 2000]
    assert "_streamSummary" in body or "streamSummary" in body
    assert "running stream" in body, (
        "Bind confirm doesn't mention running streams"
    )


def test_stream_summary_stashed_on_wrap():
    src = _src()
    # The refreshInterfaces() path must stash streamSummary on
    # wrap so the click handler can read it.
    assert "wrap._streamSummary" in src, (
        "streamSummary not stashed on wrap"
    )


# ─── IP modal a11y (MEDIUM #7) ───


def test_ip_modal_escape_closes():
    src = _src()
    idx = src.find("function openIpModal(")
    assert idx > 0
    body = src[idx:idx + 2500]
    assert "Escape" in body, (
        "IP modal doesn't handle Escape"
    )
    assert "closeIpModal()" in body


def test_ip_modal_focus_trap():
    src = _src()
    idx = src.find("function openIpModal(")
    body = src[idx:idx + 2500]
    assert "Tab" in body, "No Tab trap in IP modal"
    # Cycles between first + last focusable.
    assert "focusables" in body


def test_ip_modal_restores_focus_on_close():
    src = _src()
    assert "_modalReturnFocus" in src, (
        "Modal doesn't track focus to restore"
    )
    # close path calls .focus() on the saved element.
    idx = src.find("function closeIpModal(")
    # v0.5.94: window grown from 1500 → 2500 to accommodate the
    # comment block added to the sticky regex.
    body = src[idx:idx + 2500]
    assert "_modalReturnFocus" in body and "focus()" in body, (
        "closeIpModal doesn't restore focus"
    )


def test_ip_modal_input_auto_focused_on_open():
    src = _src()
    idx = src.find("function openIpModal(")
    body = src[idx:idx + 2500]
    assert re.search(
        r"\$\('ip-modal-input'\)\.focus\(\)",
        body,
    ), "ip-modal-input not auto-focused on modal open"


# ─── Version ───


def test_pyproject_version_at_least_0579():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 79)
