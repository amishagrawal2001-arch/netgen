"""v0.5.121: DPDK VLAN mode check must look in protocol_selection too.

v0.5.120 added a VLAN-mode check but only read `stream_data["VLAN"]`
at the top level. The actual Edit-Save path in
`traffic_client/stream_control.py` routes the dialog's VLAN field
into `stream_data["protocol_selection"]["VLAN"]` because "VLAN"
isn't in the `_TOP_LEVEL_ENGINE_KEYS` promotion list. Result:
the v0.5.120 check never fired for streams from the UI dialog,
and tx_worker kept emitting `--vlan 100` exactly as in v0.5.119
and earlier.

On srv06 with v0.5.120 installed and the operator's stream set to
"Untagged" in the dialog:

  ps aux | grep tx_worker
  → ... --vlan 100 ...   (still!)

v0.5.121 mirrors the scapy code at
`multithreaded_traffic_gen.py:1199` which already checks both
`ps.get("VLAN")` AND `stream_data.get("VLAN")`.

Tests pin the exact srv06 stream shape: VLAN mode lives at
`protocol_selection.VLAN`.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _resolve(stream_data):
    from utils.dpdk_tx_worker import _resolve_l2_l3_l4
    return _resolve_l2_l3_l4(stream_data)


def _ui_shaped_stream(vlan_mode, vlan_id="100"):
    """Stream JSON shaped exactly the way Edit-Save produces it
    — VLAN mode in protocol_selection, vlan_id in protocol_data.
    Pin the actual schema we observed on srv06 v0.5.120, not a
    cleaner hypothetical version."""
    return {
        "interface": "ens2f0np0",
        "protocol_selection": {
            "VLAN": vlan_mode,
            "L2": "Ethernet II",
            "L3": "IPv4",
            "L4": "UDP",
        },
        "protocol_data": {
            "ethernet": {
                "src_mac": "5c:25:73:3f:30:56",
                "dst_mac": "5c:25:73:3f:30:57",
            },
            "ipv4": {
                "ipv4_source_address": "10.0.0.1",
                "ipv4_destination_address": "10.0.0.2",
            },
            "udp": {
                "udp_source_port": "1234",
                "udp_destination_port": "4791",
            },
            "vlan": {"vlan_id": str(vlan_id)},
        },
        "frame_size": 512,
    }


def test_untagged_in_protocol_selection_drops_vlan_id():
    """The reported v0.5.120 regression: VLAN mode lives in
    protocol_selection, not at the top level. Fix must look
    there too."""
    fields = _resolve(_ui_shaped_stream("Untagged", vlan_id="100"))
    assert fields["vlan_id"] is None, (
        f"VLAN mode 'Untagged' in protocol_selection must drop "
        f"vlan_id. Got: {fields['vlan_id']!r}. This is the v0.5.121 "
        f"bug — v0.5.120 only checked top-level VLAN, but Edit-Save "
        f"routes it to protocol_selection.VLAN."
    )


def test_tagged_in_protocol_selection_keeps_vlan_id():
    fields = _resolve(_ui_shaped_stream("Tagged", vlan_id="100"))
    assert fields["vlan_id"] == 100


def test_top_level_vlan_still_works():
    """Don't regress the v0.5.120 top-level check while adding
    the protocol_selection one. Some streams (older session
    files, API direct callers) carry VLAN at top level."""
    s = _ui_shaped_stream("Tagged", vlan_id="100")
    # Move VLAN from protocol_selection to top level — old-style.
    del s["protocol_selection"]["VLAN"]
    s["VLAN"] = "Untagged"
    fields = _resolve(s)
    assert fields["vlan_id"] is None


def test_protocol_selection_takes_precedence_over_top_level():
    """When both are present (transitional state during a stream
    edit), the more-recent dialog state (protocol_selection)
    wins over a stale top-level value. Matches the scapy
    behavior at multithreaded_traffic_gen.py:1199 which uses
    `ps.get("VLAN") or stream_data.get("VLAN")`."""
    s = _ui_shaped_stream("Untagged", vlan_id="100")
    # Add a contradicting top-level value — protocol_selection
    # should still win because it's the live dialog state.
    s["VLAN"] = "Tagged"
    fields = _resolve(s)
    assert fields["vlan_id"] is None, (
        "protocol_selection.VLAN must take precedence over a "
        "stale top-level VLAN field"
    )


def test_missing_protocol_selection_falls_through():
    """Programmatic / API-direct streams that don't use the
    dialog still need a working VLAN check. Top-level only,
    no protocol_selection — should still respect VLAN
    field."""
    fields = _resolve({
        "interface": "ens2f0np0",
        "VLAN": "Untagged",
        "protocol_data": {
            "ethernet": {
                "src_mac": "5c:25:73:3f:30:56",
                "dst_mac": "5c:25:73:3f:30:57",
            },
            "ipv4": {
                "ipv4_source_address": "10.0.0.1",
                "ipv4_destination_address": "10.0.0.2",
            },
            "udp": {
                "udp_source_port": "1234",
                "udp_destination_port": "4791",
            },
            "vlan": {"vlan_id": "100"},
        },
        "frame_size": 512,
    })
    assert fields["vlan_id"] is None


def test_neither_location_has_vlan_falls_through_to_vlan_id():
    """Truly legacy streams with no VLAN mode field anywhere
    must still pick up vlan_id from protocol_data — back-compat
    for streams predating v0.4.5."""
    s = _ui_shaped_stream("Tagged", vlan_id="50")
    del s["protocol_selection"]["VLAN"]   # no VLAN key anywhere
    fields = _resolve(s)
    assert fields["vlan_id"] == 50
