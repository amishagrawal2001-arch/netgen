"""v0.5.123: scapy TX must respect VLAN:Untagged mode.

The third surface of the same bug shape. Already fixed:
  * v0.4.5  — scapy RX sub-iface creator (multithreaded_traffic_gen.py:1188)
  * v0.5.120 — DPDK tx_worker (utils/dpdk_tx_worker.py)
  * v0.5.121 — DPDK look in protocol_selection
  * v0.5.123 — scapy TX packet builder (utils/generic.py:get_packet_config)

Pre-fix `get_packet_config()` read `vlan_id` from
`protocol_data.vlan.vlan_id` with a default of 1, never checked
the top-level `VLAN` mode field, and unconditionally built
`vlan_ids=[1]`. The TX loop then handed `vlan_id=1` to
`build_generic_packet()` which added a Dot1Q layer with vlan=1.
On srv06's QFX5130 access port every tagged frame was dropped at
ingress; rx_count stayed at 0.

Captured on the wire via tcpdump (`ether src ...`):
```
ethertype 802.1Q (0x8100), length 105: vlan 1, p 0, DEI,
ethertype IPv4 (0x0800), 10.0.0.1.0 > 10.0.0.2.0: UDP
```

Fix: check `protocol_selection.VLAN` first, then top-level
`VLAN`. If "untagged" → vlan_ids=[None]. build_generic_packet()
already skips Dot1Q when vlan_id is None — only the upstream
list builder needed the mode check.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _cfg(stream_data):
    from utils.generic import get_packet_config
    return get_packet_config(stream_data)


def _stream(vlan_mode, vlan_id="100", at_top_level=False):
    """Stream JSON shaped the way the UI Edit-Save produces it.
    Operator's Untagged choice lands in protocol_selection.VLAN,
    not at top level — same as the DPDK side bug. at_top_level
    is for back-compat / API-direct callers."""
    stream = {
        "protocol_data": {
            "mac": {
                "mac_source_address": "5c:25:73:3f:30:56",
                "mac_destination_address": "5c:25:73:3f:30:57",
            },
            "ipv4": {
                "ipv4_source": "10.0.0.1",
                "ipv4_destination": "10.0.0.2",
            },
            "vlan": {"vlan_id": str(vlan_id)},
        },
    }
    if at_top_level:
        stream["VLAN"] = vlan_mode
    else:
        stream["protocol_selection"] = {"VLAN": vlan_mode, "L4": "UDP"}
    return stream


def test_untagged_in_protocol_selection_drops_tag():
    """The reported srv06 bug. operator picked Untagged; vlan_id
    was left at 100 in protocol_data; pre-fix vlan_ids=[100]
    and the packet went out tagged. Post-fix vlan_ids=[None] →
    no Dot1Q ever attached."""
    cfg = _cfg(_stream("Untagged", vlan_id="100"))
    assert cfg["vlan_ids"] == [None], (
        f"Untagged mode must yield vlan_ids=[None]. Got: "
        f"{cfg['vlan_ids']!r}. Pre-fix this was [100] and "
        f"every frame went out with a Dot1Q tag."
    )


def test_untagged_with_default_vlan_id_one_also_drops_tag():
    """The actual srv06 trip: dialog vlan_id defaulted to '1' and
    pre-fix the packet went out with vlan=1, DEI=1 (because
    cfi_dei was '0' but Scapy's default DEI rendering when vlan=1
    showed as set). Post-fix the tag is dropped entirely."""
    cfg = _cfg(_stream("Untagged", vlan_id="1"))
    assert cfg["vlan_ids"] == [None]


def test_tagged_in_protocol_selection_keeps_vlan_id():
    """Tagged mode must still surface vlan_id so frames carry
    the configured VID. Don't over-narrow the fix."""
    cfg = _cfg(_stream("Tagged", vlan_id="100"))
    assert cfg["vlan_ids"] == [100]


def test_stacked_keeps_vlan_id():
    """Stacked (QinQ) inner VID must survive too."""
    cfg = _cfg(_stream("Stacked", vlan_id="200"))
    assert cfg["vlan_ids"] == [200]


def test_top_level_vlan_field_also_respected():
    """API-direct callers that put VLAN at top level should
    still get the fix. Matches the DPDK side's behavior."""
    cfg = _cfg(_stream("Untagged", vlan_id="100", at_top_level=True))
    assert cfg["vlan_ids"] == [None]


def test_protocol_selection_precedence_over_top_level():
    """When both are present (mid-edit transitional state),
    protocol_selection wins. Matches the DPDK semantics."""
    s = _stream("Untagged", vlan_id="100")
    s["VLAN"] = "Tagged"   # stale top-level
    cfg = _cfg(s)
    assert cfg["vlan_ids"] == [None]


def test_missing_vlan_field_falls_through():
    """Legacy streams predating v0.4.5 (no VLAN mode field
    anywhere) must keep building tags — back-compat."""
    s = _stream("Tagged", vlan_id="50")
    del s["protocol_selection"]["VLAN"]
    cfg = _cfg(s)
    assert cfg["vlan_ids"] == [50]


def test_tagged_with_increment_expands():
    """Regression guard: VLAN increment expansion still works
    for Tagged streams. v0.5.123 only touches the Untagged
    short-circuit; the existing Tagged path is unchanged."""
    s = _stream("Tagged", vlan_id="100")
    s["protocol_data"]["vlan"].update({
        "vlan_increment": True,
        "vlan_increment_count": "3",
        "vlan_increment_value": "1",
    })
    cfg = _cfg(s)
    assert cfg["vlan_ids"] == [100, 101, 102]


def test_build_generic_packet_skips_dot1q_for_none_vlan_id():
    """End-to-end check: build_generic_packet() must not attach
    Dot1Q when vlan_id=None. The existing code (line 109)
    already guards on `vlan_id is not None and int(vlan_id) > 0`
    — this pins that contract."""
    from utils.generic import build_generic_packet, get_packet_config
    try:
        from scapy.layers.l2 import Dot1Q
    except Exception:
        import pytest
        pytest.skip("scapy not available in this env")
    s = _stream("Untagged", vlan_id="100")
    pkt_cfg = get_packet_config(s)
    pkt = build_generic_packet(
        s, pkt_cfg,
        vlan_id=pkt_cfg["vlan_ids"][0],   # None
        src_mac=pkt_cfg["mac_src_list"][0],
        dst_mac=pkt_cfg["mac_dst_list"][0],
        src_ip=pkt_cfg["ipv4_src_list"][0],
        dst_ip=pkt_cfg["ipv4_dst_list"][0],
    )
    assert Dot1Q not in pkt, (
        "Untagged stream must not have a Dot1Q layer on the wire"
    )
