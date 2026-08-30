"""v0.5.228 — DHCP subtab toolbar buttons get visible text labels.

Operator on srv06 2026-08-30: after seeing device3 in
State="No Pool", the tooltip said "attach a pool via the
Attach Route Pools button", but the operator couldn't find
any such button. Root cause: the DHCP subtab toolbar had four
28x24 ICON-ONLY buttons — Manage Pools, Attach Pool, Apply,
Refresh — with tooltips but no text. Discoverability was zero;
operators had to hover each icon to guess what it did, and in
practice missed "Attach Pool" entirely.

Also, the tooltip's phrase "Attach Route Pools button" didn't
match any actual UI label (that's the BGP wording, not DHCP).
This test pins:

- The four buttons carry the four visible text labels
  ("Manage Pools", "Attach Pool", "Apply", "Refresh").
- The last-error / tooltip message names the actual "Attach
  Pool" button (both from utils/dhcp.start_dhcp_server AND
  from the monitor at utils/dhcp_monitor._check_server_device).
- The "no named pools yet" MessageBox in AttachDHCPPoolsDialog
  refers to the correct sibling button "Manage Pools".
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DHCP_TAB_SRC = (REPO / "utils" / "devices_tab_dhcp.py").read_text()
DHCP_SRC     = (REPO / "utils" / "dhcp.py").read_text()
MONITOR_SRC  = (REPO / "utils" / "dhcp_monitor.py").read_text()


# --- Toolbar buttons carry text labels ------------------------------------

def test_manage_pools_button_has_label():
    """Was icon-only; now the operator sees "Manage Pools" on the button."""
    assert '"Manage Pools"' in DHCP_TAB_SRC


def test_attach_pool_button_has_label():
    """The main discoverability fix — this was the missing button."""
    assert '"Attach Pool"' in DHCP_TAB_SRC


def test_apply_button_has_label():
    assert '"Apply"' in DHCP_TAB_SRC


def test_refresh_button_has_label():
    assert '"Refresh"' in DHCP_TAB_SRC


def test_all_dhcp_buttons_use_labeled_helper():
    """The old signature was `_dhcp_btn(icon_name, tooltip, style)` —
    now `_dhcp_btn(icon_name, label, tooltip, style)`. Every call
    passes three positional args (or 3 + style=). This asserts each
    of the four buttons was updated in lockstep."""
    # A weak check: the new `_dhcp_btn(` signature is `(icon, label,
    # tooltip[, style])`. Count occurrences.
    assert DHCP_TAB_SRC.count("_dhcp_btn(") >= 5  # 4 calls + 1 def
    # No leftover icon-only forms with only two-arg calls.
    assert "_dhcp_btn(icon_name, tooltip" not in DHCP_TAB_SRC


# --- Tooltip text matches actual button label ------------------------------

def test_start_dhcp_server_error_names_attach_pool_button():
    """When start_dhcp_server writes dhcp_last_error, the message
    must reference the actual visible button label."""
    assert "'Attach Pool'" in DHCP_SRC
    assert "DHCP subtab toolbar" in DHCP_SRC
    # And the stale "Attach Route Pools" wording is gone.
    assert "Attach Route Pools" not in DHCP_SRC


def test_monitor_no_pool_error_matches_start_dhcp():
    """The monitor writes the SAME string when it flips a device to
    "No Pool" — mismatched tooltips would be confusing."""
    assert "'Attach Pool'" in MONITOR_SRC
    assert "DHCP subtab toolbar" in MONITOR_SRC
    assert "Attach Route Pools" not in MONITOR_SRC


def test_attach_dialog_directs_operator_to_manage_pools_button():
    """When Attach Pool opens against an empty pool inventory, the
    MessageBox now names the sibling toolbar button by its label."""
    assert "'Manage Pools'" in DHCP_TAB_SRC


# --- Guard against future drift --------------------------------------------

def test_no_undecorated_dhcp_pushbutton_calls():
    """A lazy re-introduction of an icon-only button would look like
    `QPushButton()` with no arg followed by `.setIcon(...)`. Keep an
    eye on it — if we add one again, add a label too."""
    # Weak check: there ARE other QPushButton() no-arg constructions
    # in this file (in the pool-management dialogs), so this can't
    # be a strict global ban. Instead, assert the SPECIFIC toolbar
    # button function `_dhcp_btn` uses QPushButton(label) not bare.
    idx = DHCP_TAB_SRC.find("def _dhcp_btn(")
    assert idx != -1
    body_end = DHCP_TAB_SRC.find("        # Config group", idx)
    assert body_end != -1
    body = DHCP_TAB_SRC[idx:body_end]
    assert "QPushButton(label)" in body


# --- Version bump -----------------------------------------------------------

def test_pyproject_version_at_or_beyond_228():
    src = (REPO / "pyproject.toml").read_text()
    import re
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 228)
