"""v0.5.208: OSPF Interface column no longer reads "Unknown"
when the adjacency is up.

Operator report on JNPR-MAC-HWXVX1 2026-08-23 (following
v0.5.207 upgrade): OSPF row rendered green/Full but the
Interface column said "Unknown". Root cause: the Add OSPF
dialog never sets an `interface` key in ospf_config (see
widgets/add_ospf_dialog.py:get_values — it emits area_id,
graceful_restart, router_id, hello/dead intervals, and post-
v0.5.205 the AF flags — no interface). And the per-neighbor
render loop in utils/devices_tab_ospf.py:517-556 used to
compute `ospf_interface` ONCE per device from ospf_config
alone (line ~453 pre-fix); the FRR neighbor dict's `interface`
field (parsed at utils/ospf.py:1105-1114 from
`show ip ospf neighbor`) was thrown away.

Fix: rename the pre-loop compute to
`ospf_interface_fallback` and inside the per-neighbor loop
prefer `neighbor.get("interface")` when a live neighbor is
present. Also improved the fallback so a config without an
`interface` key still derives something useful from the
device's VLAN / physical interface string rather than
defaulting to "Unknown".
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05208_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-ins — the render loop lives inside a huge
# nested method (~280 lines); running it end-to-end would need a
# lot of Qt scaffolding. Anchor the fix at the source level
# instead so a refactor can't quietly revert it.
# ─────────────────────────────────────────────────────────────────────

def _update_ospf_table_body() -> str:
    """Extract the body of update_ospf_table for pattern checks."""
    src = (REPO / "utils" / "devices_tab_ospf.py").read_text()
    idx = src.find("def update_ospf_table")
    assert idx >= 0, "update_ospf_table moved"
    # Roughly the whole method — until the next top-level def
    # (dedented `def` at 4-space indent inside the class).
    tail = src[idx:]
    end = tail.find("\n    def ", 100)
    return tail[:end] if end > 0 else tail


def test_per_row_interface_prefers_live_neighbor():
    """The render loop must read `neighbor.get("interface")`
    inside the per-neighbor branch and assign it to
    `ospf_interface` — so FRR's own view of which interface
    the adjacency formed on wins over any pre-computed
    fallback."""
    body = _update_ospf_table_body()
    # The live-preference block must appear inside the loop.
    assert 'neighbor.get("interface"' in body, \
        "per-neighbor loop no longer reads neighbor.interface"
    # And it must feed back into ospf_interface (the variable
    # the setItem uses).
    assert re.search(r"ospf_interface\s*=\s*live_iface", body), \
        "live neighbor interface not routed into ospf_interface"


def test_fallback_variable_exists_and_starts_unknown():
    """Rename lock: pre-loop compute uses `_fallback` suffix so
    a future reader can't confuse it with the per-row value
    and re-introduce the 'once per device' bug."""
    body = _update_ospf_table_body()
    assert "ospf_interface_fallback" in body, (
        "renamed fallback variable missing — per-row override may "
        "be reading from the wrong local"
    )
    assert re.search(
        r'ospf_interface_fallback\s*=\s*["\']Unknown["\']', body
    ), "fallback no longer initialized to Unknown"


def test_fallback_derives_from_iface_when_config_lacks_interface():
    """Add OSPF dialog doesn't set an interface key. The
    fallback must still produce something sensible from the
    device's VLAN + physical iface — not just 'Unknown'."""
    body = _update_ospf_table_body()
    # else-branch of the config check must reach into vlan/iface.
    assert re.search(r'ospf_interface_fallback\s*=\s*f?["\']vlan\{vlan_id\}["\']', body) \
        or re.search(r'ospf_interface_fallback\s*=\s*iface_parts\[1\]', body), \
        "fallback no longer derives from device VLAN / iface string when config lacks 'interface'"


def test_setitem_row_4_uses_ospf_interface_not_fallback():
    """Ensure the column 4 setItem consumes the per-row
    `ospf_interface` variable (which the fix now populates),
    not the fallback directly (which would silently regress
    the live-neighbor preference)."""
    body = _update_ospf_table_body()
    m = re.search(r"setItem\(row,\s*4,\s*QTableWidgetItem\((\w+)\)", body)
    assert m, "column 4 (Interface) setItem line moved or reshaped"
    assert m.group(1) == "ospf_interface", (
        f"column 4 setItem now reads from `{m.group(1)}`; must stay "
        "`ospf_interface` so the per-row live-neighbor override wins"
    )
