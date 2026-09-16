"""v0.5.343 — `_snapshot_for_upstream_hint` forwards the dialog's
ipv4/ipv6 checkbox state into `dhcp_config` so v0.5.341's
`_dhcp_relay_stanza` can emit the correct per-family relay blocks.

Operator on srv06 2026-09-15: pasted a hint for a "netgen-device
on VLAN 10" that was a v6-only DHCP client, expected v0.5.341's
Junos `dhcp-relay dhcpv6` block, got only the v4 block.

Root cause: `_snapshot_for_upstream_hint` at
`widgets/add_device_dialog.py::2418+` built `dhcp_config` with
ONLY `mode`. My v0.5.341 stanza defaults `_v4_on=True`,
`_v6_on=False` (preserves pre-v0.5.341 behavior for callers that
don't pass the flags). So every hint emitted v4-only regardless of
what the dialog checkboxes said.

Fix: propagate `ipv4_on`/`ipv6_on` from the dialog into
`dhcp_config["ipv4_enabled"]`/`["ipv6_enabled"]`.
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
    assert "v0.5.343 (audit dhcpv6-relay-hint-flag-propagation)" in _dialog_src()


def test_snapshot_populates_ipv4_enabled_and_ipv6_enabled():
    """The dhcp_config snapshot built by _snapshot_for_upstream_hint
    must include the ipv4/ipv6 checkbox state so the renderer emits
    the correct per-family relay blocks.

    v0.5.345 refined the flag source: separate DHCP-scoped checkboxes
    (`dhcp_ipv4_enabled_checkbox` / `dhcp_ipv6_enabled_checkbox`)
    hoisted into `_dhcp_v4_on` / `_dhcp_v6_on`, with fallback to the
    top-level `ipv4_on` / `ipv6_on` when both DHCP checkboxes are
    unset. Assertion updated to match the post-v0.5.345 shape."""
    src = _dialog_src()
    fn_idx = src.index("def _snapshot_for_upstream_hint(")
    body = src[fn_idx:fn_idx + 8000]
    # The dhcp_config dict must include both flag entries — post-
    # v0.5.345 these come from `_dhcp_v4_on` / `_dhcp_v6_on`.
    assert '"ipv4_enabled": _dhcp_v4_on' in body
    assert '"ipv6_enabled": _dhcp_v6_on' in body


def test_flags_use_the_dialogs_checkbox_state():
    """The values come from `checked("ipv4_checkbox")` / `checked(
    "ipv6_checkbox")` which are hoisted into `ipv4_on` / `ipv6_on`
    at the top of the snapshot method. Regression guard against
    reading them from somewhere else (device_data, etc.)."""
    src = _dialog_src()
    fn_idx = src.index("def _snapshot_for_upstream_hint(")
    body = src[fn_idx:fn_idx + 8000]
    # ipv4_on / ipv6_on hoisted from checkbox reads:
    assert 'ipv4_on = checked("ipv4_checkbox")' in body
    assert 'ipv6_on = checked("ipv6_checkbox")' in body


def test_v0_5_341_marker_still_intact():
    """v0.5.341 shipped the renderer-side fix that this dialog-side
    flag propagation feeds. Regression guard."""
    hints_src = (_REPO / "utils" / "upstream_hints.py").read_text()
    assert "v0.5.341 (audit dhcpv6-relay-hint-parity)" in hints_src


def test_add_device_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())


def test_end_to_end_v6_only_client_now_emits_v6_relay_block():
    """Integration test: build the shape `_snapshot_for_upstream_hint`
    would emit for a v6-only DHCP-client device and confirm the
    renderer emits the Junos dhcpv6 sub-hierarchy."""
    from utils.upstream_hints import _dhcp_relay_stanza
    # Shape matching the fixed _snapshot_for_upstream_hint output
    # for a v6-only DHCP-client on vlan10.
    dhcp_config = {
        "mode": "client",
        "ipv4_enabled": False,
        "ipv6_enabled": True,
    }
    stanza = _dhcp_relay_stanza("juniper", "10", dhcp_config)
    # v6 block must be present.
    assert "dhcp-relay dhcpv6" in stanza
    assert "set forwarding-options dhcp-relay dhcpv6 group CLIENTS-V6 interface irb.10" in stanza
    # v4 block must be absent — this is a v6-only client.
    assert "set forwarding-options dhcp-relay group CLIENTS interface" not in stanza
