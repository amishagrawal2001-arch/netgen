"""v0.5.34 — consolidate DPDK menu around Make DPDK Ready + add
persistent Reboot Server button.

Operator request after the v0.5.25-v0.5.33 install close-out:

  "i see you have configure hugepages, configure iommu as seprate
   task under Advance, make it part of same install process make
   dpdk ready, and allow user to reboot the server from same
   window."

Two changes:

  1. Configure Hugepages + Configure IOMMU items REMOVED from the
     DPDK Advanced submenu.

     Both are already handled by install_dpdk.sh — hugepages at
     Step 7, IOMMU at Step 7 (via /etc/default/grub edit + the
     v0.5.15 inline reboot prompt). The standalone menu actions
     were a divergent-paths trap: operators ran them out-of-band,
     IOMMU edited GRUB without prompting reboot, then Make DPDK
     Ready couldn't tell if the manual run had completed → state
     misreport.

     The standalone handler methods (configure_hugepages /
     configure_iommu) remain in the codebase for any external
     caller — only the menu wiring is removed. Diagnostics (which
     reads the same state the orchestrator does) still surfaces
     IOMMU/hugepages status.

  2. Persistent "Reboot Server…" button added to MakeDpdkReadyDialog
     footer.

     v0.5.15 added an inline reboot prompt that only appeared
     AFTER an IOMMU step succeeded. That covered the canonical
     path but missed several legitimate cases:
       - operator manually edited /etc/default/grub and wants to
         reboot to test
       - operator ran Setup DPDK earlier without IOMMU prompt
         (because it was already set) but needs to reboot for a
         different reason (kernel module sticky state etc.)
       - operator wants to verify DPDK survives a reboot

     The new button is visible at all times, fires a generic
     confirmation, then POSTs /api/system/reboot.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_MAIN = _REPO / "traffic_client" / "main.py"
_DIALOG = _REPO / "widgets" / "dpdk_make_ready_dialog.py"


# ────────────────── Menu items removed ──────────────────────────────


def test_configure_hugepages_action_not_added_to_menu():
    """The standalone 'Configure Hugepages...' menu item must be
    gone from the DPDK Advanced submenu — Make DPDK Ready handles
    it at Step 7."""
    src = _MAIN.read_text()
    # Pattern: `QAction("Configure Hugepages..."` followed within a
    # few lines by `dpdk_advanced_menu.addAction(`. If this pattern
    # exists, the menu item is still wired.
    m = re.search(
        r'QAction\("Configure Hugepages[^"]*"[\s\S]{0,400}?'
        r'dpdk_advanced_menu\.addAction\(',
        src,
    )
    assert not m, (
        "Configure Hugepages menu item is still added to the DPDK "
        "Advanced submenu. v0.5.34 consolidates hugepage management "
        "into Make DPDK Ready — the standalone action is a divergent-"
        "paths trap."
    )


def test_configure_iommu_action_not_added_to_menu():
    """Standalone 'Configure IOMMU...' menu item must be gone too."""
    src = _MAIN.read_text()
    m = re.search(
        r'QAction\("Configure IOMMU[^"]*"[\s\S]{0,400}?'
        r'dpdk_advanced_menu\.addAction\(',
        src,
    )
    assert not m, (
        "Configure IOMMU menu item is still added to the DPDK "
        "Advanced submenu. v0.5.34 consolidates IOMMU configuration "
        "into Make DPDK Ready (Step 7 + v0.5.15 reboot prompt)."
    )


def test_load_vfio_modules_action_still_present():
    """Load VFIO Modules stays — it's the one Advanced action that
    has a legitimate standalone use case (custom kernel without
    auto-load rules, post-reboot manual loading)."""
    src = _MAIN.read_text()
    # Wider window: there's a v0.5.34 explanatory comment block
    # between the QAction constructor and the addAction call.
    m = re.search(
        r'QAction\("Load VFIO Modules[^"]*"[\s\S]{0,1200}?'
        r'dpdk_advanced_menu\.addAction\(',
        src,
    )
    assert m, (
        "Load VFIO Modules action was accidentally removed from "
        "Advanced submenu. Keep this — it's the recovery hook for "
        "custom-kernel hosts where Make DPDK Ready's modprobe "
        "didn't auto-persist."
    )


def test_removal_comment_explains_rationale():
    """A future maintainer will look at the Advanced submenu and
    wonder where the Configure Hugepages / Configure IOMMU items
    went. The removal must be accompanied by a comment explaining
    the consolidation + v0.5.34 reference."""
    src = _MAIN.read_text()
    # Search for a comment block in the Advanced submenu region.
    advanced_block_m = re.search(
        r"dpdk_advanced_menu\s*=\s*QMenu\([\s\S]+?dpdk_load_modules_action",
        src,
    )
    assert advanced_block_m, "DPDK Advanced submenu block not found"
    block = advanced_block_m.group(0)
    assert "v0.5.34" in block and (
        "consolidat" in block.lower()
        or "Make DPDK Ready" in block
        or "removed" in block.lower()
    ), (
        "DPDK Advanced submenu has no comment explaining the v0.5.34 "
        "consolidation. Future maintainers will re-add the items "
        "without context."
    )


# ────────────────── Reboot Server button added ──────────────────────


def test_reboot_btn_widget_created_in_dialog():
    """MakeDpdkReadyDialog must construct a Reboot Server… button."""
    src = _DIALOG.read_text()
    assert re.search(
        r'self\._reboot_btn\s*=\s*QPushButton\("Reboot Server[^"]*"\)',
        src,
    ), (
        "self._reboot_btn QPushButton not constructed in "
        "MakeDpdkReadyDialog. v0.5.34 fix isn't wired."
    )


def test_reboot_btn_added_to_button_box():
    """The button must be added to the dialog's button row — not
    just constructed and orphaned. Match the
    `btns.addButton(self._reboot_btn, ...)` call."""
    src = _DIALOG.read_text()
    assert re.search(
        r'btns\.addButton\(\s*self\._reboot_btn\s*,',
        src,
    ), (
        "self._reboot_btn isn't added to the button box — would "
        "be a constructed-but-invisible widget."
    )


def test_reboot_btn_wired_to_reboot_request_handler():
    """The button's clicked signal must connect to a handler — not
    be left dangling."""
    src = _DIALOG.read_text()
    assert re.search(
        r'self\._reboot_btn\.clicked\.connect\(\s*self\._on_reboot_request\s*\)',
        src,
    ), (
        "self._reboot_btn.clicked not connected to "
        "_on_reboot_request — button click would no-op."
    )


def test_on_reboot_request_handler_exists():
    """The slot must be defined — not just referenced. Catches a
    refactor that orphans the connection."""
    src = _DIALOG.read_text()
    assert "def _on_reboot_request(self)" in src, (
        "_on_reboot_request slot is connected but not defined. "
        "Clicking the button would AttributeError."
    )


def test_on_reboot_request_calls_trigger_reboot_on_confirm():
    """The handler must call _trigger_reboot when the operator
    confirms — otherwise the button is purely cosmetic."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _on_reboot_request\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_on_reboot_request body not found"
    body = m.group(0)
    assert "self._trigger_reboot()" in body, (
        "_on_reboot_request doesn't invoke _trigger_reboot on "
        "confirm — button shows a dialog but never reboots."
    )


def test_on_reboot_request_confirms_before_rebooting():
    """The handler must show a confirmation dialog (QMessageBox)
    BEFORE the reboot fires. A one-click full-host reboot is too
    destructive without confirmation."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _on_reboot_request\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    assert "QMessageBox" in body, (
        "_on_reboot_request doesn't construct a QMessageBox — "
        "the operator would fire a full-host reboot with no confirm."
    )
    # And the trigger must be gated on the affirmative button.
    assert re.search(
        r"clickedButton\(\)\s+is\s+reboot_btn",
        body,
    ), (
        "_on_reboot_request fires _trigger_reboot unconditionally "
        "(not gated on the Reboot Now click). The Cancel button "
        "would still reboot."
    )


def test_reboot_btn_distinct_from_iommu_inline_prompt():
    """v0.5.15's _prompt_reboot was the IOMMU-step-success prompt.
    v0.5.34's _on_reboot_request must be a SEPARATE method — they
    have different messages, different triggers, and different
    contexts (manual click vs. step-success). Confirm BOTH exist."""
    src = _DIALOG.read_text()
    assert "def _prompt_reboot(" in src, (
        "v0.5.15 _prompt_reboot was accidentally removed — IOMMU "
        "step success would no longer prompt for reboot."
    )
    assert "def _on_reboot_request(" in src, (
        "v0.5.34 _on_reboot_request missing — manual button has "
        "no handler."
    )


def test_pyproject_version_at_least_0534():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 34), (
        f"Version {m.group(1)} < 0.5.34"
    )
