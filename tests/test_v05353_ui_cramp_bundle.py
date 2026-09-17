"""v0.5.353 — UI cramp bundle: six dialogs get roomier minimum widths.

Sweep-audit finding: multiple QDialog subclasses had explicit
`setMinimumWidth` / `setMinimumSize` values that were LESS than the
widest placeholder they carried, so Qt rendered them at the cramped
minimum and clipped the placeholder mid-word until the operator
dragged them wider by hand.

Fixes:
- DHCPPoolDialog (Add DHCP Pool) — no explicit width → 960
- _L2ConfigDialog (Start L2 emulation session) — 560 → 880
- AIChatDialog (NetGenAI Chat) — 520/600 → 780/820
- _StatefulTcpConfigDialog (Start stateful-TCP session) — 560 → 760
- AISettingsDialog (AI Settings) — 600/700 → 760/820
- AttachDHCPPoolsDialog gateway_override_edit — setFixedWidth(240) →
  setMinimumWidth(500) so the 72-char placeholder fits inside the
  already-900px dialog.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel):
    return (_REPO / rel).read_text()


def test_all_markers_present():
    """Every file touched by v0.5.353 must carry the audit marker
    so a future grep for `v0.5.353` finds them all."""
    assert "v0.5.353 (audit ui-cramp)" in _read("widgets/l2_emulation_tab.py")
    assert "v0.5.353 (audit ui-cramp)" in _read("widgets/ai_chat_dialog.py")
    assert "v0.5.353 (audit ui-cramp)" in _read("widgets/stateful_tcp_tab.py")
    assert "v0.5.353 (audit ui-cramp)" in _read("widgets/ai_settings_dialog.py")
    # devices_tab_dhcp.py carries TWO v0.5.353 markers (Add DHCP
    # Pool dialog + AttachDHCPPools gateway override field).
    assert _read("utils/devices_tab_dhcp.py").count("v0.5.353 (audit ui-cramp)") >= 2


# --- U1: L2 IGMP config dialog ---


def test_U1_l2_config_dialog_width_bumped_to_880():
    """_L2ConfigDialog was at 560 — the IGMP `_igmp_type_code`
    placeholder is 102 chars and clips at that width."""
    src = _read("widgets/l2_emulation_tab.py")
    cls_idx = src.index("class _L2ConfigDialog(QDialog)")
    init_body = src[cls_idx:cls_idx + 4000]
    assert "self.setMinimumWidth(880)" in init_body
    # And the old value is gone.
    assert "self.setMinimumWidth(560)" not in init_body


# --- U2: AI Chat dialog ---


def test_U2_ai_chat_dialog_widths_bumped():
    """AIChatDialog was 520 min / 600 resize — message_input
    placeholder is 85 chars."""
    src = _read("widgets/ai_chat_dialog.py")
    cls_idx = src.index("class AIChatDialog(QDialog)")
    init_body = src[cls_idx:cls_idx + 3000]
    # New minimum size (width bumped, height preserved).
    assert "self.setMinimumSize(780, 450)" in init_body
    # Default resize also bumped so the window opens roomy.
    assert "self.resize(820, 560)" in init_body
    # Old values gone.
    assert "self.setMinimumSize(520, 450)" not in init_body
    assert "self.resize(600, 520)" not in init_body


# --- U3: Stateful TCP config dialog ---


def test_U3_stateful_tcp_config_dialog_width_bumped_to_760():
    src = _read("widgets/stateful_tcp_tab.py")
    # Anchor on the window-title line so we look at the right
    # dialog (there is another _StatefulTcpConfigDialog-adjacent
    # class in the same file).
    title_idx = src.index('setWindowTitle("Start stateful-TCP session")')
    init_body = src[title_idx:title_idx + 1000]
    assert "self.setMinimumWidth(760)" in init_body
    assert "self.setMinimumWidth(560)" not in init_body


# --- U4: AI Settings dialog ---


def test_U4_ai_settings_dialog_widths_bumped():
    src = _read("widgets/ai_settings_dialog.py")
    cls_idx = src.index("class AISettingsDialog(QDialog)")
    init_body = src[cls_idx:cls_idx + 3000]
    assert "self.setMinimumSize(760, 500)" in init_body
    assert "self.resize(820, 600)" in init_body
    assert "self.setMinimumSize(600, 500)" not in init_body
    assert "self.resize(700, 600)" not in init_body


# --- U5: AttachDHCPPools gateway override field ---


def test_U5_attach_dhcp_pools_gateway_field_uses_minimum_not_fixed():
    """The gateway override field was pinned at 240px inside a
    900px dialog. Swap `setFixedWidth(240)` → `setMinimumWidth(500)`
    so the 72-char placeholder fits and Qt can grow the field."""
    src = _read("utils/devices_tab_dhcp.py")
    # The line-level assertion: the specific object no longer uses
    # setFixedWidth(240).
    _needle_old = "self.gateway_override_edit.setFixedWidth(240)"
    _needle_new = "self.gateway_override_edit.setMinimumWidth(500)"
    assert _needle_old not in src, (
        "gateway_override_edit must no longer be pinned at 240px"
    )
    assert _needle_new in src, (
        "gateway_override_edit must set a MINIMUM width, not a "
        "fixed one, so Qt can expand the field in a 900px dialog"
    )


# --- DHCP Pool dialog ---


def test_dhcp_pool_dialog_has_min_width_960():
    """DHCPPoolDialog had no explicit width — Qt auto-sized to ~700
    and clipped the 65-char Relay Return-Hop placeholder."""
    src = _read("utils/devices_tab_dhcp.py")
    cls_idx = src.index("class DHCPPoolDialog(QDialog)")
    build_ui_idx = src.index("def _build_ui(self):", cls_idx)
    body = src[build_ui_idx:build_ui_idx + 2000]
    assert "self.setMinimumWidth(960)" in body


# --- AST parse safety ---


def test_touched_files_ast_parse():
    """Every file we edited must still parse — a stray syntax slip
    in a width-tweak edit would break the whole client."""
    import ast
    for rel in (
        "widgets/l2_emulation_tab.py",
        "widgets/ai_chat_dialog.py",
        "widgets/stateful_tcp_tab.py",
        "widgets/ai_settings_dialog.py",
        "utils/devices_tab_dhcp.py",
    ):
        ast.parse(_read(rel))


# --- Regression: nothing else got shrunk ---


def test_no_dialog_got_shrunk():
    """A defensive check: no touched dialog has a smaller minimum
    than its pre-fix value. Catches a fat-finger typo like
    `setMinimumWidth(88)` (missing a 0)."""
    checks = [
        ("widgets/l2_emulation_tab.py", 880),
        ("widgets/stateful_tcp_tab.py", 760),
    ]
    for rel, expected in checks:
        src = _read(rel)
        widths = [int(m) for m in re.findall(
            r"setMinimumWidth\((\d{3,4})\)", src,
        )]
        assert expected in widths, (
            f"{rel} must include setMinimumWidth({expected}); found "
            f"widths: {widths}"
        )
