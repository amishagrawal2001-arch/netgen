"""Shared VLAN-mode resolution for TX packet builders.

The dialog persists `vlan_id` even when the operator picks
"Untagged" so toggling Tagged↔Untagged doesn't lose the VID.
Pre-v0.5.124 every TX-side packet builder had its own version
of "should I attach a Dot1Q?", and most of them got the check
wrong:

* `utils/generic.py` read vlan_id unconditionally
  (fixed v0.5.123)
* `utils/uec.py` checked only `vlan_id > 0`
  (fixed v0.5.124 — uses this helper)
* `utils/rocev2.py` checked `mode == "Tagged"` but missed
  "Stacked" (fixed v0.5.124 — uses this helper)
* `utils/dpdk_tx_worker.py` checked `stream_data["VLAN"]` at
  top level but Edit-Save routes it into protocol_selection
  (fixed v0.5.121)

Every "same shape, different surface" instance cost us at least
one release. This helper makes the check impossible to get
wrong: call `resolve_tx_vlan_id(stream_data)` and respect the
result.

Mirrors the scapy RX side's lookup at
`multithreaded_traffic_gen.py:1188` (v0.4.5) so TX and RX agree
on which streams are tagged.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


_TAGGABLE_MODES = frozenset({
    "tagged", "stacked", "taggedstacked", "tagged+stacked",
})


def resolve_tx_vlan_id(stream_data: Dict[str, Any]) -> Optional[int]:
    """Return the VLAN VID the TX path should put on the wire, or
    None for untagged.

    Resolution order, mirroring the scapy RX side at
    multithreaded_traffic_gen.py:1188:

    1. `protocol_selection.VLAN` — the dialog's live state (winner)
    2. top-level `VLAN` — back-compat for older streams + API
       direct callers
    3. neither present → fall through to vlan_id (legacy back-compat
       for streams predating v0.4.5)

    If the resolved mode is "Untagged" (or any non-taggable value),
    return None even when `protocol_data.vlan.vlan_id` carries a
    positive value — that's the dialog persisting the last VID for
    next time, NOT a request to tag.

    If the mode is missing entirely, fall through to vlan_id as
    legacy streams predating v0.4.5 had no mode field.

    Returns:
        int VID (1..4094) when the stream should be tagged.
        None when untagged (or vlan_id is 0/empty/invalid).
    """
    if not isinstance(stream_data, dict):
        return None

    ps = stream_data.get("protocol_selection") or {}
    mode_raw = ps.get("VLAN") or stream_data.get("VLAN") or ""
    mode = str(mode_raw).strip().lower()

    # Mode explicitly says untagged — drop tag even if vlan_id is set.
    if mode == "untagged":
        return None

    # Mode is one of the taggable strings — resolve vlan_id.
    # If mode is empty (legacy back-compat), also fall through.
    if mode in _TAGGABLE_MODES or mode == "":
        pd = stream_data.get("protocol_data") or {}
        vlan_pd = pd.get("vlan") or {}
        raw = vlan_pd.get("vlan_id")
        if raw is None:
            raw = stream_data.get("vlan_id")
        if raw in (None, "", "0", 0):
            return None
        try:
            vid = int(raw)
        except (TypeError, ValueError):
            return None
        if not (1 <= vid <= 4094):
            return None
        return vid

    # Unrecognized mode string → conservative: no tag. Safer than
    # silently tagging on garbage.
    return None
