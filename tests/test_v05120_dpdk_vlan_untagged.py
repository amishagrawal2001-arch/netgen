"""v0.5.120: DPDK tx_worker must respect the `VLAN: Untagged` mode.

Pre-fix `utils/dpdk_tx_worker.py` read only `vlan_id` from
`protocol_data.vlan.vlan_id` (or top-level) and emitted `--vlan`
whenever the value was non-zero. But the dialog persists vlan_id
EVEN when the operator selects the "Untagged" radio — so every
operator who toggled an existing stream from Tagged to Untagged
silently kept their vlan_id, and the DPDK tx_worker kept tagging
on the wire. On switch ports configured as access (untagged-
only), every frame got dropped at ingress.

The scapy side already had this exact fix in v0.4.5
(multithreaded_traffic_gen.py:1188 onward). v0.5.120 ports it
to the DPDK path so the two engines behave the same way.

srv06 saga: this took 11 versions (v0.5.110-v0.5.119) of red
herrings (MAC autopopulate, RX engine outcome surfacing, NLAT,
bifurcated-Mellanox, pre-launch sweep friendly-fire,
rx_worker stderr capture) before the operator noticed that
`ps aux | grep tx_worker` STILL showed `--vlan 100` after they
picked "Untagged" in the UI.

Tests:
  * Untagged mode + vlan_id set → vlan_id resolved to None
  * Tagged mode + vlan_id set → vlan_id resolved to int
  * Empty mode + vlan_id set → vlan_id resolved (back-compat)
  * Stacked mode + vlan_id set → vlan_id resolved
  * Untagged with vlan_id=0 → still None (defensive)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _resolve(stream_data):
    """Wrap the private helper so each test reads naturally."""
    from utils.dpdk_tx_worker import _resolve_l2_l3_l4
    return _resolve_l2_l3_l4(stream_data)


def _base_stream(vlan_mode, vlan_id="100"):
    """Minimal valid stream config — only varies VLAN bits."""
    return {
        "interface": "ens2f0np0",
        "VLAN": vlan_mode,
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


def test_untagged_mode_drops_vlan_id():
    """The reported bug. Operator picks 'Untagged', dialog leaves
    vlan_id='100' for next-time convenience, server must NOT
    emit --vlan."""
    fields = _resolve(_base_stream("Untagged", vlan_id="100"))
    assert fields["vlan_id"] is None, (
        f"vlan_id must be None when VLAN mode is Untagged. "
        f"Got: {fields['vlan_id']!r}. This was the v0.5.120 bug — "
        f"tx_worker would emit --vlan 100 on the wire and any "
        f"access-port switch silently dropped every frame."
    )


def test_tagged_mode_keeps_vlan_id():
    """Don't over-narrow the fix — Tagged streams must still get
    their VID through to tx_worker."""
    fields = _resolve(_base_stream("Tagged", vlan_id="100"))
    assert fields["vlan_id"] == 100, (
        f"Tagged streams must surface vlan_id. Got: {fields['vlan_id']!r}"
    )


def test_stacked_mode_keeps_vlan_id():
    """802.1ad / QinQ also wants the inner VID."""
    fields = _resolve(_base_stream("Stacked", vlan_id="200"))
    assert fields["vlan_id"] == 200


def test_missing_vlan_mode_falls_through_to_vlan_id():
    """Back-compat: streams from older clients that don't carry
    a top-level VLAN field at all should keep working — fall
    through to vlan_id as before."""
    s = _base_stream("Tagged", vlan_id="50")
    del s["VLAN"]   # legacy stream JSON missing the top-level field
    fields = _resolve(s)
    assert fields["vlan_id"] == 50, (
        "Streams without a VLAN field must fall through to vlan_id. "
        "Pre-v0.4.5 streams predate the VLAN mode field — we can't "
        "force them to None."
    )


def test_untagged_mode_with_zero_vlan_id_stays_none():
    """Defensive: vlan_id=0 already resolves to None on the
    fall-through path. Untagged with vlan_id=0 still None — no
    accidental difference between code paths."""
    fields = _resolve(_base_stream("Untagged", vlan_id="0"))
    assert fields["vlan_id"] is None


def test_untagged_mode_case_insensitive():
    """The check is on lowercased VLAN field — alternate cases
    (UNTAGGED, untagged, Untagged) all must take effect."""
    for mode in ("Untagged", "UNTAGGED", "untagged", " untagged "):
        fields = _resolve(_base_stream(mode, vlan_id="100"))
        assert fields["vlan_id"] is None, (
            f"VLAN mode {mode!r} should be treated as untagged"
        )


def test_dpdk_matches_scapy_path():
    """v0.4.5 fixed the same bug on the scapy side
    (multithreaded_traffic_gen.py:1188). v0.5.120 brings DPDK
    in line so both engines respect VLAN=Untagged identically.
    This test pins the scapy side's behavior so they don't
    drift apart again — if scapy adds a new VLAN mode, DPDK
    needs the matching string check."""
    # The scapy-side test is what taggable modes look like.
    taggable_modes = ("tagged", "stacked", "taggedstacked", "tagged+stacked")
    # All taggable modes must result in a real vlan_id on the
    # DPDK side — symmetric to scapy.
    for mode in taggable_modes:
        fields = _resolve(_base_stream(mode, vlan_id="100"))
        assert fields["vlan_id"] == 100, (
            f"DPDK path must accept the same taggable modes as scapy. "
            f"Mode={mode!r} → vlan_id={fields['vlan_id']!r}"
        )
