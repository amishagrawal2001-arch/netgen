"""MPLS / SR-MPLS label-stack builder for the scapy-path traffic generator.

The existing single-label MPLS branch in ``utils/generic.py`` was good
for plain MPLS but couldn't carry the Segment Identifier (SID) lists
that SR-MPLS needs (RFC 8660). This module adds the *stack* — N labels
concatenated, with the bottom-of-stack ``s`` bit set on the last label
only and a uniform Traffic Class + TTL across the stack.

Pure-function helper; the call site in ``utils/generic.py`` only needs
to detect the new ``mpls_labels`` list field and ``/``-chain the result
between the L2 header and the L3 payload. Existing
``mpls_label`` (singular) config keeps working — the call site routes
through the same helper with a one-element list.
"""

from __future__ import annotations

from typing import Sequence


# Public so callers can sanity-check ethertype against what they get on
# the wire. Set on the Ether layer in front of the MPLS stack.
ETHERTYPE_MPLS_UNICAST = 0x8847
ETHERTYPE_MPLS_MULTICAST = 0x8848


def build_mpls_stack(labels: Sequence[int], tc: int = 0, ttl: int = 64):
    """Build a chained MPLS label stack ready to ``/``-attach.

    Args:
      labels: ordered list of MPLS label values (SIDs for SR-MPLS).
        First label in the list = top of the stack (transmitted first).
        Must contain at least one entry — empty stacks are invalid
        MPLS (the kernel rejects them) and would silently send an
        ethertype-0x8847 frame with no labels.
      tc: 3-bit Traffic Class (formerly EXP). Applied to every label.
      ttl: 8-bit TTL applied to every label. Real SR-MPLS routers
        usually copy from the IP header; we set a flat value because
        the stack here is independent of payload.

    Returns:
      A scapy MPLS layer chain, with the bottom-of-stack ``s`` bit set
      on the last label only (scapy handles this automatically when
      MPLS layers are stacked via ``/``).

    Raises:
      ValueError: empty labels, or any label outside the 20-bit range,
        or tc / ttl out of range.

    The exact byte layout is regression-locked by
    ``tests/test_mpls_stack.py`` (label-shift / s-bit / TC / TTL / on-
    wire BOS placement).
    """
    if not labels:
        raise ValueError("labels must be a non-empty list of label values")
    if not 0 <= int(tc) <= 7:
        raise ValueError(f"tc must be 0..7 (3 bits), got {tc!r}")
    if not 0 <= int(ttl) <= 255:
        raise ValueError(f"ttl must be 0..255 (1 byte), got {ttl!r}")
    for lbl in labels:
        if not 0 <= int(lbl) <= 0xFFFFF:
            raise ValueError(
                f"label must be 0..0xFFFFF (20 bits), got {lbl!r}"
            )

    from scapy.contrib.mpls import MPLS

    head = None
    for lbl in labels:
        layer = MPLS(label=int(lbl), cos=int(tc) & 0x7,
                     ttl=int(ttl) & 0xff)
        head = layer if head is None else head / layer
    return head


def extract_mpls_labels(stream_config: dict) -> list:
    """Pull the label list out of a stream-config ``mpls`` dict.

    Supports both shapes:

      * **New (0.2.64+)**: ``{"mpls_labels": [100, 200, 300], ...}`` —
        the SR-MPLS label stack.
      * **Legacy (≤ 0.2.63)**: ``{"mpls_label": 100, ...}`` — a single
        scalar label. Returned as a one-element list so the call site
        only needs to think about stacks.

    Returns an empty list when neither key is set so the caller can
    skip the MPLS branch entirely (i.e. "no MPLS for this stream").
    """
    if not isinstance(stream_config, dict):
        return []
    raw = stream_config.get("mpls_labels")
    if raw:
        # Tolerate a comma-separated string ("100,200,300") since the
        # eventual GUI input is a single text field, not a Qt list.
        if isinstance(raw, str):
            return [int(x.strip(), 0) for x in raw.split(",") if x.strip()]
        return [int(x) for x in raw]
    single = stream_config.get("mpls_label")
    if single in (None, "", 0):
        return []
    try:
        return [int(single)]
    except (TypeError, ValueError):
        return []
