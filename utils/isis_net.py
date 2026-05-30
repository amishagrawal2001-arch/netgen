"""ISIS Network Entity Title (NET) validation (v0.2.86).

Per RFC 1195 + ISO 8348, an ISIS NET is an OSI network address with
this shape:

    AFI (1 byte)  +  Area ID (1-13 bytes)  +  System ID (6 bytes)  +  NSEL (1 byte)

Total: 8-20 bytes. The NSEL byte (last byte) MUST be 0x00 for IS-IS
proper (a non-zero NSEL would indicate an OSI NSAP for a transport
service, not the routing protocol itself).

The dotted-hex display convention varies by vendor but typically
groups by 2 hex chars (1 byte) on the AFI + NSEL boundaries and
4 hex chars (2 bytes) in the middle:

    49.0001.0000.0000.0001.00          ← 10-byte NET (most common)
    49.0001.0002.0000.0000.0000.0001.00 ← 14-byte NET (longer area)

Our validator accepts any number of dots (it strips them before
counting bytes), so a paste from a router config in either grouping
style works.

Pure function — no Qt, no I/O. Easy to unit-test exhaustively.
"""

from __future__ import annotations

import re
from typing import Optional


_HEX_ONLY = re.compile(r"^[0-9A-Fa-f]+$")


def validate_isis_net(
    net_id: str,
    *,
    allow_short_area: bool = False,
) -> Optional[str]:
    """Return ``None`` if ``net_id`` is a valid ISIS NET, or a short
    human-readable reason string explaining why not.

    With ``allow_short_area=True`` the Add Device dialog's "AFI.Area"
    shortcut (e.g. ``"49.0001"``) is accepted — that dialog pads
    short input to a full 10-byte NET at submit time. The inline-edit
    path expects a full NET, so it leaves the default off.

    The check is bytewise: dots are stripped, the hex content is
    required to be all-hex-digits and an even number of chars, total
    byte length must be in [8, 20], and the last byte must be ``00``
    (NSEL — ISIS requires zero per RFC 1195 §3.1).
    """
    if net_id is None or not str(net_id).strip():
        return "NET is empty"
    raw = str(net_id).strip()

    # Strip dots — operators paste in either Cisco's 4-char-group form
    # or Juniper's nibble form; we don't care which.
    hex_part = raw.replace(".", "")
    if not hex_part:
        return "NET is just dots — no hex content"

    # Hex-only check.
    if not _HEX_ONLY.match(hex_part):
        # Find the first non-hex char to make the error actionable.
        for i, ch in enumerate(hex_part):
            if ch not in "0123456789ABCDEFabcdef":
                return (f"non-hex character {ch!r} at position "
                        f"{i + 1} of {raw!r}")
        return f"non-hex characters in {raw!r}"

    # Even number of hex chars (bytes don't split).
    if len(hex_part) % 2 != 0:
        return (f"odd hex-character count ({len(hex_part)}) in "
                f"{raw!r} — each byte is 2 hex chars")
    byte_len = len(hex_part) // 2

    # Short-area shortcut: the Add Device dialog allows "AFI.Area"
    # (1 byte AFI + 1-13 bytes area = 2-14 bytes total) and pads to a
    # full NET on submit. Min 2 bytes (1-byte AFI + 1-byte area), max
    # 14 bytes (1-byte AFI + 13-byte area). Anything in that range
    # bypasses the NSEL check.
    if allow_short_area:
        if 2 <= byte_len <= 14:
            return None  # short form OK
        # Otherwise fall through to the full-NET check.

    # Full NET: 8-20 bytes total (AFI=1 + Area=1-13 + SysID=6 + NSEL=1).
    if byte_len < 8:
        return (f"NET too short: {byte_len} bytes (minimum 8 — "
                f"AFI+Area(1+)+SysID(6)+NSEL)")
    if byte_len > 20:
        return (f"NET too long: {byte_len} bytes (maximum 20 — "
                f"AFI+Area(13)+SysID(6)+NSEL)")

    # NSEL — last byte must be 00 for IS-IS routing protocol per
    # RFC 1195 §3.1. A non-zero NSEL is an OSI NSAP for a transport
    # service (e.g. CLNS), not the routing protocol.
    nsel = hex_part[-2:].upper()
    if nsel != "00":
        return (f"NSEL must be 00 for IS-IS (got {nsel}); a non-zero "
                f"NSEL indicates an OSI transport service, not the "
                f"routing protocol")

    return None


def is_short_area_form(net_id: str) -> bool:
    """Helper for callers that want to know whether the user gave
    short "AFI.Area" form (which the Add Device dialog then pads
    to a full NET) vs the full NET. Doesn't validate — just classifies.
    Pure function."""
    if not net_id:
        return False
    hex_part = str(net_id).strip().replace(".", "")
    if not hex_part or len(hex_part) % 2 != 0:
        return False
    return (len(hex_part) // 2) <= 7  # less than the 8-byte full-NET minimum
