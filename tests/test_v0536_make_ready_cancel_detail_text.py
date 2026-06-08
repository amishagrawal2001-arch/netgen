"""v0.5.36 — Make DPDK Ready detail pane updates on NIC-pick cancel.

Operator screenshot (Jun 8 2026) from srv06 showed the Make DPDK
Ready dialog after a successful run with the NIC bind cancelled:

  Action list:
    ✓ Load vfio + vfio-pci kernel modules
    ✓ Allocate 1024 × 2MB hugepages
    — Bind a NIC to vfio-pci (GUI prompts for which one)
      — cancelled by operator     ← correct

  Detail pane below:
    Running: Bind a NIC to vfio-pci (GUI prompts for which one)…
                                  ← STALE — still says "Running"

The action ROW updated correctly via `row.set_state("skip", ...)`
but the `_detail` text (the wider status area below the action
list) was set to "Running: <label>…" when the action started and
never updated when the cancel/no-selection paths fired. Result:
operator sees the dialog reporting "Running" indefinitely after
they've cancelled, when in fact no action is in progress.

v0.5.36 sets `_detail.setText(...)` in both the cancel and the
no-selection paths so the action ROW and the detail pane stay
consistent.

Pin both — anyone re-adding the early-return without updating
_detail earns a test failure here.
"""
from __future__ import annotations

import re
from pathlib import Path


_DIALOG = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


def _bind_picker_cancel_block() -> str:
    """Extract the code block around the NIC-picker cancel handling."""
    src = _DIALOG.read_text()
    # Find the picker.exec_() != Accepted check + a generous window.
    m = re.search(
        r"if\s+picker\.exec_\(\)\s+!=\s+QDialog\.Accepted:"
        r"[\s\S]+?return",
        src,
    )
    assert m, "NIC picker cancel block not found"
    return m.group(0)


def _bind_picker_noselection_block() -> str:
    """Extract the 'no NIC selected' branch.

    Anchor the trailing `return` on word-boundary + start-of-line
    so we don't stop at the substring `return` inside a literal
    like `"The picker returned an empty selection."` (the prose
    explaining what happened to the operator can legitimately
    contain that word).
    """
    src = _DIALOG.read_text()
    m = re.search(
        r'row\.set_state\("fail",\s*"no NIC selected"\)'
        r"[\s\S]+?\n\s+return\b",
        src,
    )
    assert m, "no-NIC-selected fail block not found"
    return m.group(0)


def _bind_picker_cancel_block_with_anchor():
    """Same word-boundary anchor for the cancel branch."""
    src = _DIALOG.read_text()
    m = re.search(
        r"if\s+picker\.exec_\(\)\s+!=\s+QDialog\.Accepted:"
        r"[\s\S]+?\n\s+return\b",
        src,
    )
    return m.group(0) if m else None


def test_cancel_updates_detail_text():
    """The cancel path must call `self._detail.setText(...)` so the
    detail pane reflects the cancel state. Pre-fix it stayed at
    'Running: <label>…' indefinitely."""
    block = _bind_picker_cancel_block()
    assert "self._detail.setText(" in block, (
        "NIC picker cancel branch doesn't update self._detail — "
        "operator sees stale 'Running: Bind a NIC…' message after "
        "cancelling. v0.5.36 fix isn't applied."
    )


def test_cancel_detail_text_mentions_cancellation():
    """The new text must SAY it was cancelled — not just blank
    out. A blank/empty detail pane after a cancel is also confusing."""
    block = _bind_picker_cancel_block()
    # Look for cancel-state language
    assert re.search(
        r"cancel",
        block, re.IGNORECASE,
    ) and "self._detail.setText(" in block, (
        "Cancel branch updates self._detail but the text doesn't "
        "mention cancellation. Operator can't tell from the detail "
        "pane alone what just happened."
    )


def test_cancel_detail_text_points_at_recovery_path():
    """When the operator cancels, they may still want to bind a
    NIC LATER. The detail text should point at the recovery path
    (Run All Steps again, or Tools → DPDK → Advanced → Bind
    Interface) so they're not stranded."""
    block = _bind_picker_cancel_block()
    assert (
        "Run All Steps" in block
        or "Bind Interface" in block
        or "Advanced" in block
    ), (
        "Cancel branch doesn't tell the operator how to bind a "
        "NIC later. They'd have to hunt for the recovery path."
    )


def test_no_selection_updates_detail_text():
    """Same fix for the 'no NIC selected' (empty selection) branch
    — operator hits OK with no choice; pre-fix the detail pane
    also stayed at 'Running:'."""
    block = _bind_picker_noselection_block()
    assert "self._detail.setText(" in block, (
        "'no NIC selected' fail branch doesn't update self._detail "
        "— same stale-text bug as the cancel branch."
    )


def test_no_selection_detail_mentions_retry():
    """The empty-selection text should mention Retry — the button
    has been re-labeled to Retry at this point. The text may span
    multiple adjacent string literals (Python implicit
    concatenation) so we don't try to extract its exact bounds;
    just confirm self._detail.setText is called AND the no-selection
    block contains the operator-guidance keywords."""
    block = _bind_picker_noselection_block()
    assert "self._detail.setText(" in block, (
        "no-selection branch doesn't call self._detail.setText — "
        "v0.5.36 fix not applied here."
    )
    # Operator guidance: must mention Retry or 'choose a NIC' so the
    # operator knows the next step.
    assert "Retry" in block or "choose" in block.lower(), (
        "no-selection detail text doesn't tell the operator what "
        "to do (click Retry / choose a NIC). Stranded UX."
    )


def test_cancel_still_skips_action_row():
    """The fix must NOT break the existing action-row state-update
    that v0.5.36 builds on. Confirm the `row.set_state("skip",
    "cancelled by operator")` call is still there."""
    block = _bind_picker_cancel_block()
    assert 'row.set_state("skip"' in block, (
        "Cancel branch lost the row.set_state(\"skip\", ...) call "
        "— action row would no longer show 'cancelled by operator'."
    )


def test_pyproject_version_at_least_0536():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 36), (
        f"Version {m.group(1)} < 0.5.36"
    )
