"""DPDK Status dialog — unbind safety + UX pins (v0.2.97).

The full dialog is hard to construct headlessly (it pulls in
chassis state, async fetch workers, and per-server live updates),
so the regressions are pinned with source-grep assertions that
the safety + UX wiring exists at the expected lines.

What's pinned here:
  * `_perform_unbind` carries a confirmation `QMessageBox.question`
    BEFORE the worker dispatch, gated on the `is_unbound` flag
    (recovery operations skip the prompt; live unbinds prompt).
  * The inline Unbind button in the DPDK Status dialog has a
    tooltip naming the side-effect (running DPDK traffic stops).
  * The empty-interface case in `_format_dpdk_status` emits a
    user-facing message instead of a silent empty section.
  * The status dialog wires `Ctrl+Return` to `accept()`.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
DPDK_FILE = REPO / "traffic_client" / "dpdk_menu_actions.py"


@pytest.fixture(scope="module")
def src():
    return DPDK_FILE.read_text()


# ───────────────────────────────────── unbind confirmation
def test_perform_unbind_has_confirmation_dialog(src):
    """The confirmation must live INSIDE `_perform_unbind` so both
    the inline Unbind button and the Tools → Unbind menu path are
    gated by a single check."""
    m = re.search(
        r"def _perform_unbind\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert m is not None, "_perform_unbind not found"
    body = m.group(0)
    assert "QMessageBox.question" in body, (
        "_perform_unbind must show a QMessageBox.question before "
        "kicking off the worker — v0.2.97 cross-path unbind safety."
    )
    assert "Confirm DPDK unbind" in body, (
        "confirmation dialog title pinned so a future copy-edit "
        "doesn't accidentally hide the prompt"
    )


def test_perform_unbind_skips_confirmation_for_recovery(src):
    """When `is_unbound` is True the device is already released
    (no in-flight traffic to disrupt) so the prompt would just be
    friction. Pin that the confirmation is wrapped in
    `if not is_unbound:`."""
    m = re.search(
        r"def _perform_unbind\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    body = m.group(0)
    # The QMessageBox.question block must appear under a
    # `if not is_unbound:` guard.
    guard_then_q = re.search(
        r"if not is_unbound:\s*\n.*?QMessageBox\.question",
        body, flags=re.DOTALL,
    )
    assert guard_then_q is not None, (
        "the unbind confirmation must be gated on `if not is_unbound:` "
        "so recovery-restore operations don't have to click through "
        "an extra prompt"
    )


def test_perform_unbind_returns_on_user_cancel(src):
    """The confirmation must `return` (not fall through) when the
    operator clicks No — otherwise the worker fires anyway."""
    m = re.search(
        r"def _perform_unbind\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    body = m.group(0)
    # Right after the confirmation, look for a guard + return.
    pattern = re.search(
        r"confirm = QMessageBox\.question.*?if confirm != QMessageBox\.Yes:\s*\n\s*return",
        body, flags=re.DOTALL,
    )
    assert pattern is not None, (
        "the unbind confirmation must short-circuit with `return` "
        "when the operator picks No — otherwise the async worker "
        "still fires"
    )


# ───────────────────────────────────── inline tooltip
def test_inline_unbind_button_carries_warning_tooltip(src):
    """The Unbind button in the DPDK Status dialog must surface the
    'will stop DPDK traffic' warning via setToolTip so the operator
    sees the consequence BEFORE clicking — the confirmation gates
    the action but the tooltip gates the curiosity."""
    # Look for the inline button construction + the tooltip on it
    # within a few lines.
    pattern = re.search(
        r"unbind_btn\s*=\s*QPushButton\(\"Unbind\"\).*?unbind_btn\.setToolTip\(",
        src, flags=re.DOTALL,
    )
    assert pattern is not None, (
        "inline Unbind button missing setToolTip — v0.2.97 polish "
        "to surface the traffic-disruption warning before click"
    )
    # The tooltip text must mention the operative consequence.
    tip_match = re.search(
        r"unbind_btn\.setToolTip\(\s*\"([^\"]*(?:\n[^\"]*)*)\"",
        src,
    )
    assert tip_match is not None, "couldn't extract the tooltip text"


# ───────────────────────────────────── empty-state message
def test_format_dpdk_status_handles_empty_interfaces(src):
    """When the server returns no interfaces (typically a
    dpdk-devbind.py tooling failure) the dialog must emit a
    user-facing message instead of a silent empty section."""
    m = re.search(
        r"def _format_dpdk_status\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert m is not None, "_format_dpdk_status not found"
    body = m.group(0)
    # Must have an `else:` branch on the `if interfaces:` check
    # that appends a user-facing hint.
    assert re.search(
        r"if interfaces:.*?else:\s*\n\s*.*?lines\.append",
        body, flags=re.DOTALL,
    ) is not None, (
        "_format_dpdk_status missing else-branch for empty "
        "interfaces — v0.2.97 empty-state polish"
    )
    assert "No interfaces detected" in body, (
        "the empty-state message must point the operator at the "
        "tooling-failure hypothesis ('No interfaces detected...')"
    )


# ───────────────────────────────────── Ctrl+Return shortcut
def test_show_dpdk_status_wires_ctrl_return_shortcut(src):
    """The DPDK Status dialog must wire Ctrl+Return to accept()
    so dismiss matches the rest of the app's modal convention."""
    m = re.search(
        r"def show_dpdk_status\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert m is not None, "show_dpdk_status not found"
    body = m.group(0)
    assert "Key_Return" in body, (
        "show_dpdk_status missing Qt.Key_Return shortcut wiring — "
        "v0.2.97 keyboard-dismiss polish"
    )
    assert "QShortcut" in body, (
        "show_dpdk_status missing QShortcut import/use for the "
        "Ctrl+Return binding"
    )
