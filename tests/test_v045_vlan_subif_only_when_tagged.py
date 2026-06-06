"""Regression test for v0.4.5: don't create a VLAN sub-interface for
"VLAN: Untagged" streams.

Operator-reported bug from svl-d-ai-srv04: started a Scapy stream with
flow tracking on RX iface enp181s0f0np0 and a phantom `enp181s0f0np0.1`
sub-interface appeared in the Interface Statistics table. The stream's
VLAN field was "Untagged" but the GUI defaults `protocol_data.vlan.vlan_id`
to "1", and the pre-fix `_build_rx_selector_for_stream` pulled that
value into the selector regardless of the top-level VLAN field.
`start_rx_counter` then saw `selector["vlan_id"]=1`, called
`_ensure_vlan_rx_visible`, and created `enp181s0f0np0.1`.

Fix: in `_build_rx_selector_for_stream`, only set `vlan_id` when the
stream's top-level VLAN field explicitly says Tagged / Stacked. For
Untagged streams (or missing field), vlan_id=None → no sub-iface."""
from __future__ import annotations


def test_untagged_stream_has_no_vlan_id_in_selector():
    """Pre-fix: vlan_id=1 leaked into the selector even when VLAN was
    Untagged → phantom sub-iface. Now: vlan_id=None for Untagged."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    stream_data = {
        "VLAN": "Untagged",
        "protocol_selection": {"VLAN": "Untagged", "L4": "UDP"},
        "protocol_data": {
            "vlan": {"vlan_id": "1"},   # GUI default — was leaking through
            "mac": {"mac_source_address": "02:00:00:00:00:01",
                    "mac_destination_address": "02:00:00:00:00:02"},
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
            "udp": {"udp_source_port": "1234",
                    "udp_destination_port": "4791"},
        },
    }
    sel = _build_rx_selector_for_stream(stream_data)
    assert sel.get("vlan_id") is None, (
        f"Untagged stream had vlan_id={sel.get('vlan_id')} in selector — "
        f"would trigger phantom sub-interface creation. Pre-fix bug "
        f"from svl-d-ai-srv04."
    )


def test_tagged_stream_keeps_vlan_id():
    """Streams with VLAN: Tagged should still get vlan_id in the
    selector so the sub-iface gets created (the legitimate case)."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    stream_data = {
        "VLAN": "Tagged",
        "protocol_selection": {"VLAN": "Tagged", "L4": "UDP"},
        "protocol_data": {
            "vlan": {"vlan_id": "10"},
            "mac": {"mac_source_address": "02:00:00:00:00:01",
                    "mac_destination_address": "02:00:00:00:00:02"},
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
            "udp": {"udp_source_port": "1234",
                    "udp_destination_port": "4791"},
        },
    }
    sel = _build_rx_selector_for_stream(stream_data)
    assert sel.get("vlan_id") == 10, (
        f"Tagged stream lost vlan_id from selector — sub-iface won't "
        f"be created and the RX sniffer will miss tagged frames. "
        f"sel.get('vlan_id')={sel.get('vlan_id')}"
    )


def test_missing_vlan_field_treated_as_untagged():
    """Defensive: stream_data with no top-level VLAN field at all
    (e.g. older client schema). Should NOT create a sub-iface."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    stream_data = {
        "protocol_data": {
            "vlan": {"vlan_id": "1"},
            "mac": {"mac_source_address": "02:00:00:00:00:01",
                    "mac_destination_address": "02:00:00:00:00:02"},
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
            "udp": {"udp_source_port": "1234",
                    "udp_destination_port": "4791"},
        },
    }
    sel = _build_rx_selector_for_stream(stream_data)
    assert sel.get("vlan_id") is None, (
        "Missing VLAN field should default to untagged behaviour; "
        "selector should NOT carry vlan_id"
    )


def test_vlan_field_case_insensitive():
    """Robustness: 'tagged', 'Tagged', 'TAGGED' all mean the same."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    for case_variant in ("Tagged", "tagged", "TAGGED"):
        stream_data = {
            "VLAN": case_variant,
            "protocol_data": {
                "vlan": {"vlan_id": "10"},
                "mac": {"mac_source_address": "02:00:00:00:00:01",
                        "mac_destination_address": "02:00:00:00:00:02"},
                "ipv4": {"ipv4_source": "10.0.0.1",
                         "ipv4_destination": "10.0.0.2"},
                "udp": {"udp_source_port": "1234",
                        "udp_destination_port": "4791"},
            },
        }
        sel = _build_rx_selector_for_stream(stream_data)
        assert sel.get("vlan_id") == 10, (
            f"VLAN={case_variant!r} should match the tagged case "
            f"(case-insensitive); got vlan_id={sel.get('vlan_id')}"
        )


def test_protocol_selection_vlan_field_also_honored():
    """Some client schemas put VLAN at top level, others inside
    protocol_selection. Both should be honored."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    stream_data = {
        "protocol_selection": {"VLAN": "Tagged", "L4": "UDP"},
        "protocol_data": {
            "vlan": {"vlan_id": "20"},
            "mac": {"mac_source_address": "02:00:00:00:00:01",
                    "mac_destination_address": "02:00:00:00:00:02"},
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
        },
    }
    sel = _build_rx_selector_for_stream(stream_data)
    assert sel.get("vlan_id") == 20, (
        "VLAN field inside protocol_selection should also be honored, "
        f"got vlan_id={sel.get('vlan_id')}"
    )
