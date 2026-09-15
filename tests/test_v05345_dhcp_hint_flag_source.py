"""v0.5.345 — `_snapshot_for_upstream_hint` reads the DHCP-specific
`dhcp_ipv4_enabled_checkbox` / `dhcp_ipv6_enabled_checkbox`, NOT
the device-level `ipv4_checkbox` / `ipv6_checkbox`.

v0.5.343 propagated the wrong flag: for a DHCP-client device, the
device-level checkboxes are unrelated to which families the client
should solicit — a DHCP client can leave both device-level static
IP boxes off (DHCP is the source of truth for its addresses).

Operator on srv06 2026-09-15 (post-v0.5.343 upgrade): pasted a
hint for a DHCP-client device and NO relay stanza appeared —
neither v4 nor v6. Before v0.5.343 the same device produced the
v4 relay block. Root cause: `ipv4_on = checked("ipv4_checkbox")`
returned False because the operator hadn't checked the static v4
IP box; v0.5.341's stanza then saw `_v4_on=False, _v6_on=False`
and returned "".

Fix: read `dhcp_ipv4_enabled_checkbox` / `dhcp_ipv6_enabled_checkbox`
which are DHCP-specific enables (default True/False respectively
in the DHCP config section). Backward-compat fallback to the
device-level flags when the DHCP-specific widgets are absent
(pre-v0.5.231 dialogs).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dialog_src():
    return (_REPO / "widgets" / "add_device_dialog.py").read_text()


def test_marker_present():
    assert "v0.5.345 (audit dhcp-hint-flag-source-fix)" in _dialog_src()


def test_snapshot_reads_dhcp_specific_v4_checkbox():
    src = _dialog_src()
    fn_idx = src.index("def _snapshot_for_upstream_hint(")
    body = src[fn_idx:]
    assert '_dhcp_v4_on = checked("dhcp_ipv4_enabled_checkbox")' in body


def test_snapshot_reads_dhcp_specific_v6_checkbox():
    src = _dialog_src()
    fn_idx = src.index("def _snapshot_for_upstream_hint(")
    body = src[fn_idx:]
    assert '_dhcp_v6_on = checked("dhcp_ipv6_enabled_checkbox")' in body


def test_dhcp_config_ipv4_enabled_reads_new_flag():
    """The dhcp_config dict's `ipv4_enabled` must come from
    `_dhcp_v4_on`, not the device-level `ipv4_on` we used in
    v0.5.343."""
    src = _dialog_src()
    fn_idx = src.index("def _snapshot_for_upstream_hint(")
    body = src[fn_idx:]
    # dhcp_config uses the new local variable:
    assert '"ipv4_enabled": _dhcp_v4_on' in body
    assert '"ipv6_enabled": _dhcp_v6_on' in body


def test_backcompat_fallback_to_device_level_when_dhcp_widgets_absent():
    """Pre-v0.5.231 dialogs had no DHCP-specific v4/v6 checkboxes.
    Fallback: when both DHCP-specific reads return False (widget
    absent), use the device-level ipv4_on/ipv6_on so legacy
    dialogs don't emit blank stanzas."""
    src = _dialog_src()
    fn_idx = src.index("def _snapshot_for_upstream_hint(")
    body = src[fn_idx:]
    assert "if not _dhcp_v4_on and not _dhcp_v6_on:" in body
    # Fallback assignments:
    assert "_dhcp_v4_on = ipv4_on" in body
    assert "_dhcp_v6_on = ipv6_on" in body


def test_v0_5_343_marker_still_present():
    """v0.5.343 is the wrong-flag fix that v0.5.345 corrects; the
    comment must still exist so a future reader sees the full
    story."""
    assert "v0.5.343 (audit dhcpv6-relay-hint-flag-propagation)" in _dialog_src()


def test_add_device_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())
