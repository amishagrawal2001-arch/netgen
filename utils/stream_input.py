"""Stream-dialog input validators (v0.2.96).

The Stream / Add-stream dialog is the most-touched dialog in the
app — every traffic-generation flow starts there. Numeric fields
(ports, counts, MPLS labels, VLAN, TTL) are guarded at type-time
by `QIntValidator`, but the v0.2.96 audit caught three submit-time
gaps:

  * MAC fields (`mac_source_address`, `mac_destination_address`)
    were plain `QLineEdit("00:00:00:00:00:00")` with no validator —
    unicode, garbage, the empty string, all pass through to the
    server which rejects later with a less-friendly error.
  * IPv4 fields (`source_field`, `destination_field`) and IPv6
    fields (`ipv6_source_field`, `ipv6_destination_field`) were
    plain `QLineEdit` too. Defaults nudge toward `0.0.0.0` /
    `2001:db8::1` which the operator can clobber to `999.999.999.999`.
  * No custom `def accept(self)` override on the dialog itself,
    so even if individual fields were validated nothing forced a
    final pre-submit check.

This module ships pure-function validators the dialog wires both
live (textChanged → red border) and at submit-time
(accept-override → blocking QMessageBox). Pure functions also
let the test suite cover the parsing without spinning up Qt.

Returns convention matches `utils/isis_net.py` and
`utils/ospf_area.py`:
  * `validate_*(value) -> Optional[str]`
    Returns `None` when valid, an explanatory error string when not.
  * Empty / None input is rejected (required fields).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Optional, Tuple


# Standard colon-separated MAC. We allow `:` and `-` separators
# (Cisco-style XXXX.XXXX.XXXX is intentionally NOT accepted — the
# stream-dialog code formats everything as colon-separated and the
# server-side parser is colon-only).
_MAC_RE = re.compile(r"^([0-9a-fA-F]{2})([:\-]([0-9a-fA-F]{2})){5}$")


def validate_mac(value: str) -> Optional[str]:
    """Return None when ``value`` is a colon/dash-separated MAC.

    Rejects:
      * empty / None
      * non-string
      * unicode, mixed garbage
      * Cisco dotted form (XXXX.XXXX.XXXX) — server expects colons
      * the all-zero MAC (00:00:00:00:00:00) is accepted but flagged
        below at the call site via a separate ``is_zero_mac`` helper
        so the dialog can warn without blocking — some operators
        genuinely need a wildcard source MAC for capture testing.
    """
    if value is None:
        return "MAC address is empty"
    if not isinstance(value, str):
        return f"MAC address must be a string (got {type(value).__name__})"
    v = value.strip()
    if not v:
        return "MAC address is empty"
    if not _MAC_RE.match(v):
        return (
            f"'{value}' isn't a valid MAC — expected 6 hex octets "
            f"separated by ':' or '-' (e.g. 00:11:22:33:44:55)."
        )
    return None


def is_zero_mac(value: str) -> bool:
    """True when the MAC parses as 00:00:00:00:00:00. Used by the
    dialog to amber-warn (not block) — wildcard source MACs are
    occasionally legitimate for capture/sink scenarios."""
    if validate_mac(value) is not None:
        return False
    # Strip separators, lowercase, check all-zeros.
    hex_only = re.sub(r"[:\-]", "", value).lower()
    return hex_only == "0" * 12


def validate_ipv4(value: str) -> Optional[str]:
    """Return None when ``value`` parses as a dotted-quad IPv4.

    Accepts the unspecified address (0.0.0.0) — it's the dialog's
    factory default and rejecting it here would block every fresh
    Open. The dialog amber-warns separately when src == dst.
    """
    if value is None:
        return "IPv4 address is empty"
    if not isinstance(value, str):
        return f"IPv4 address must be a string (got {type(value).__name__})"
    v = value.strip()
    if not v:
        return "IPv4 address is empty"
    try:
        ipaddress.IPv4Address(v)
    except (ipaddress.AddressValueError, ValueError) as exc:
        return f"'{value}' isn't a valid IPv4 address — {exc}"
    return None


def validate_ipv6(value: str) -> Optional[str]:
    """Return None when ``value`` parses as a colon-hex IPv6.

    Accepts the unspecified address (::) and the documentation
    range (2001:db8::/32) — defaults live in the documentation
    range so rejecting them would break the fresh-open path.
    """
    if value is None:
        return "IPv6 address is empty"
    if not isinstance(value, str):
        return f"IPv6 address must be a string (got {type(value).__name__})"
    v = value.strip()
    if not v:
        return "IPv6 address is empty"
    try:
        ipaddress.IPv6Address(v)
    except (ipaddress.AddressValueError, ValueError) as exc:
        return f"'{value}' isn't a valid IPv6 address — {exc}"
    return None


def validate_frame_sizes(
    fixed: Optional[int],
    minimum: Optional[int],
    maximum: Optional[int],
    *,
    frame_type: str = "fixed",
) -> Optional[str]:
    """Cross-field check for the Frame Size group.

    ``frame_type`` is the operator's combo selection — one of
    ``fixed``, ``random``, ``imix``. When ``fixed`` we only need
    ``fixed`` in [64, 1518]; when ``random``/``imix`` we need
    both ``min`` and ``max`` in [64, 1518] AND ``min <= max``.
    The Stream-dialog fixed/min/max QLineEdits already carry
    ``QIntValidator(64, 1518)`` so per-field bounds are type-time;
    this helper catches the cross-field relationship.
    """
    ft = (frame_type or "fixed").lower()
    # Treat anything other than the two explicit ranged modes as
    # fixed — least-surprise fallback if a future combo entry slips
    # through with a name this helper hasn't been taught.
    if ft not in ("random", "imix"):
        if fixed is None:
            return "Frame size is empty"
        if not (64 <= fixed <= 1518):
            return f"Frame size {fixed} out of range — must be 64–1518."
        return None
    # random / imix path: need both min and max, ordered.
    if minimum is None or maximum is None:
        return "Frame min and max are both required for random/IMIX frames"
    if not (64 <= minimum <= 1518):
        return f"Frame min {minimum} out of range — must be 64–1518."
    if not (64 <= maximum <= 1518):
        return f"Frame max {maximum} out of range — must be 64–1518."
    if minimum > maximum:
        return (
            f"Frame min ({minimum}) must be <= max ({maximum}). "
            "Swap the values or pick Fixed frame size."
        )
    return None


# ─────────────────────────────────────────── batch helpers
# The dialog's accept() override walks every required field and
# collects errors so the operator sees the full picture instead
# of dismissing one QMessageBox at a time. The batch helper
# below assembles a (field_label, error) list which the dialog
# turns into a single multi-line dialog.

def collect_errors(
    pairs: list,
) -> list:
    """Each ``pair`` is ``(label, value, validator)``.

    Returns a list of ``(label, error_string)`` for the invalid
    entries — empty list when everything checks out.
    """
    errors = []
    for label, value, validator in pairs:
        err = validator(value)
        if err is not None:
            errors.append((label, err))
    return errors
