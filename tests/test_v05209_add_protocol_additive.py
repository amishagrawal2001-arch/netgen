"""v0.5.209: Add OSPF / Add IS-IS is additive — must not
silently disable an AF that was already up.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: existing device
had IPv4 OSPF running (green Full/Backup row). Operator opened
Add OSPF, unchecked the IPv4 checkbox and checked only IPv6
(intending "add IPv6 to this device"), clicked Add. The IPv4
row disappeared — the existing v4 OSPF got silently disabled.

Root cause: `_update_device_protocol` in
`widgets/devices_tab.py` merges the new config with the
existing one via `merged_config.update(config)`. Post-v0.5.205
the Add OSPF dialog always emits `ipv4_enabled` and
`ipv6_enabled` from its checkboxes — so the update() call
overwrites whatever was in existing_config with whatever the
user checked. The OSPF branch had preservation logic for
area_id_ipv4/ipv6, graceful_restart_ipv4/ipv6, route_pools,
and p2p_ipv4/ipv6 — but NOT for the enable flags. Same shape
in the IS-IS branch post-v0.5.207.

Fix: additive-preserve — if the existing config had an AF
enabled, it stays enabled regardless of what the dialog said.
To disable an AF, use the per-AF Delete button on that row
(the v0.5.205 / v0.5.207 fix). "Add" only adds.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05209_test_{os.getpid()}.db"),
)


# Simulate the merge logic in isolation (running the full
# _update_device_protocol needs the whole DevicesTab widget).
# The test locks in the exact behavior of the fix: if
# existing_config had the AF enabled, the merged config keeps
# it enabled regardless of what `config` says.
def _apply_merge_shape(existing_config: dict, config: dict, protocol: str) -> dict:
    """Re-implement the v0.5.209 merge shape for direct testing."""
    merged_config = existing_config.copy()
    merged_config.update(config)

    if protocol in ("IS-IS", "ISIS"):
        if "ipv4_enabled" not in config and "ipv4_enabled" in existing_config:
            merged_config["ipv4_enabled"] = existing_config["ipv4_enabled"]
        if "ipv6_enabled" not in config and "ipv6_enabled" in existing_config:
            merged_config["ipv6_enabled"] = existing_config["ipv6_enabled"]
        # v0.5.209 additive-preserve.
        if existing_config.get("ipv4_enabled"):
            merged_config["ipv4_enabled"] = True
        if existing_config.get("ipv6_enabled"):
            merged_config["ipv6_enabled"] = True
    elif protocol == "OSPF":
        # (omitting area_id/graceful_restart/route_pools/p2p
        # preservation — not what this test targets)
        if existing_config.get("ipv4_enabled"):
            merged_config["ipv4_enabled"] = True
        if existing_config.get("ipv6_enabled"):
            merged_config["ipv6_enabled"] = True
    return merged_config


# ─────────────────────────────────────────────────────────────────────
# OSPF
# ─────────────────────────────────────────────────────────────────────

def test_ospf_add_ipv6_preserves_existing_ipv4_enabled():
    """The exact operator report — existing IPv4 OSPF must
    survive an Add OSPF click that only checked the IPv6 box."""
    existing = {"ipv4_enabled": True, "ipv6_enabled": False,
                "area_id_ipv4": "0.0.0.0"}
    new_from_dialog = {"ipv4_enabled": False, "ipv6_enabled": True,
                       "area_id": "0.0.0.0", "router_id": ""}
    merged = _apply_merge_shape(existing, new_from_dialog, "OSPF")
    assert merged["ipv4_enabled"] is True, (
        "Adding IPv6 OSPF silently disabled the existing IPv4 side"
    )
    assert merged["ipv6_enabled"] is True


def test_ospf_add_both_afs_still_adds_both():
    """Default dialog state (both boxes checked) on a fresh
    device must enable both AFs."""
    existing = {}
    new_from_dialog = {"ipv4_enabled": True, "ipv6_enabled": True,
                       "area_id": "0.0.0.0"}
    merged = _apply_merge_shape(existing, new_from_dialog, "OSPF")
    assert merged["ipv4_enabled"] is True
    assert merged["ipv6_enabled"] is True


def test_ospf_add_v4_only_on_existing_v6_preserves_v6():
    """Symmetric to the operator report — Add v4 on an existing
    v6-only device must keep v6."""
    existing = {"ipv4_enabled": False, "ipv6_enabled": True}
    new_from_dialog = {"ipv4_enabled": True, "ipv6_enabled": False}
    merged = _apply_merge_shape(existing, new_from_dialog, "OSPF")
    assert merged["ipv4_enabled"] is True
    assert merged["ipv6_enabled"] is True


# ─────────────────────────────────────────────────────────────────────
# ISIS (same-class bug post-v0.5.207)
# ─────────────────────────────────────────────────────────────────────

def test_isis_add_ipv6_preserves_existing_ipv4_enabled():
    existing = {"ipv4_enabled": True, "ipv6_enabled": False,
                "area_id": "49.0001.0000.0000.0001.00"}
    new_from_dialog = {"ipv4_enabled": False, "ipv6_enabled": True,
                       "area_id": "49.0001.0000.0000.0001.00"}
    merged = _apply_merge_shape(existing, new_from_dialog, "IS-IS")
    assert merged["ipv4_enabled"] is True
    assert merged["ipv6_enabled"] is True


def test_isis_add_ipv4_preserves_existing_ipv6_enabled():
    existing = {"ipv4_enabled": False, "ipv6_enabled": True}
    new_from_dialog = {"ipv4_enabled": True, "ipv6_enabled": False}
    merged = _apply_merge_shape(existing, new_from_dialog, "ISIS")
    assert merged["ipv4_enabled"] is True
    assert merged["ipv6_enabled"] is True


# ─────────────────────────────────────────────────────────────────────
# Edit path — no regression
# ─────────────────────────────────────────────────────────────────────

def test_edit_dialog_omitted_af_flags_dont_disable_existing():
    """v0.5.207 Edit dialogs hide the AF group and get_values
    omits the flags. The pre-fix `if key not in config` branch
    already handled that. Verify the v0.5.209 additive-preserve
    doesn't break it (redundant, but harmless)."""
    existing = {"ipv4_enabled": True, "ipv6_enabled": True}
    new_from_edit = {"area_id": "0.0.0.1"}  # AF keys absent
    merged = _apply_merge_shape(existing, new_from_edit, "OSPF")
    assert merged["ipv4_enabled"] is True
    assert merged["ipv6_enabled"] is True


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-in — guard against a refactor that quietly
# removes the additive-preserve block
# ─────────────────────────────────────────────────────────────────────

def test_source_ospf_branch_has_additive_preserve():
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    # Anchor on the OSPF branch marker in _update_device_protocol.
    idx = src.find('elif protocol == "OSPF":')
    assert idx >= 0, "OSPF merge branch marker moved"
    # Look at the OSPF branch body — 3500 chars is enough to
    # cover the preservation block + the additive-preserve add.
    body = src[idx:idx + 6000]
    assert re.search(
        r'if existing_config\.get\(["\']ipv4_enabled["\']\)\s*:\s*\n\s*merged_config\[["\']ipv4_enabled["\']\]\s*=\s*True',
        body,
    ), "OSPF branch no longer additive-preserves ipv4_enabled"
    assert re.search(
        r'if existing_config\.get\(["\']ipv6_enabled["\']\)\s*:\s*\n\s*merged_config\[["\']ipv6_enabled["\']\]\s*=\s*True',
        body,
    ), "OSPF branch no longer additive-preserves ipv6_enabled"


def test_source_isis_branch_has_additive_preserve():
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    idx = src.find('if protocol in ["IS-IS", "ISIS"]:')
    assert idx >= 0, "ISIS merge branch marker moved"
    body = src[idx:idx + 2500]
    assert re.search(
        r'if existing_config\.get\(["\']ipv4_enabled["\']\)\s*:\s*\n\s*merged_config\[["\']ipv4_enabled["\']\]\s*=\s*True',
        body,
    ), "ISIS branch no longer additive-preserves ipv4_enabled"
    assert re.search(
        r'if existing_config\.get\(["\']ipv6_enabled["\']\)\s*:\s*\n\s*merged_config\[["\']ipv6_enabled["\']\]\s*=\s*True',
        body,
    ), "ISIS branch no longer additive-preserves ipv6_enabled"
