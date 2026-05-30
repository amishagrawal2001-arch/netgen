"""OSPF area-id validation + normalisation (v0.2.87).

RFC 2328 §6: the area ID is a 32-bit unsigned integer. It's
conventionally displayed as IPv4 dotted-decimal (``0.0.0.0`` for the
backbone, ``0.0.0.1`` for area 1) but routers and config files
historically also accept the plain integer form (``0`` for backbone,
``1`` for area 1). Cisco / FRR / Quagga all accept both.

This helper:

* Accepts either form on input.
* Validates the bounds: int form is 0–4294967295; dotted-decimal is
  0.0.0.0–255.255.255.255 (each octet 0–255).
* Normalises to dotted-decimal — the form FRR puts on the wire and
  what most operators expect to see in configs. ``1`` becomes
  ``0.0.0.1`` etc.

Pure function. No Qt. Returns ``(ok, normalised_dotted, error)`` so
callers can decide whether to use the normalised value or surface
the error string in a modal.
"""

from __future__ import annotations

from typing import Optional, Tuple


def validate_ospf_area_id(
    value: str,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validate + normalise an OSPF area-id input.

    Returns ``(ok, normalised, error)``:

    * ``ok`` — True iff the input parses; the caller may swap the
      raw input for ``normalised`` when storing.
    * ``normalised`` — dotted-decimal IPv4 string (e.g. ``"0.0.0.1"``)
      when ``ok``; None when not.
    * ``error`` — None when ``ok``; short human-readable reason when
      not (suitable for a QMessageBox body).
    """
    if value is None:
        return (False, None, "Area ID is empty")
    raw = str(value).strip()
    if not raw:
        return (False, None, "Area ID is empty")

    # Plain integer form first (no dots → treat as a 32-bit unsigned).
    if "." not in raw:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return (False, None,
                    f"'{raw}' is neither an integer nor a "
                    f"dotted-decimal area ID")
        if n < 0 or n > 0xFFFFFFFF:
            return (False, None,
                    f"Area ID {n} out of range — must be 0 to "
                    f"4294967295 (32-bit unsigned)")
        # Render n as dotted-decimal, network-byte-order.
        return (True, _int_to_dotted(n), None)

    # Dotted form. Strict 4-octet IPv4 shape.
    parts = raw.split(".")
    if len(parts) != 4:
        return (False, None,
                f"'{raw}' has {len(parts)} parts — dotted-decimal "
                f"area IDs must have exactly 4 (A.B.C.D)")
    octets = []
    for i, part in enumerate(parts, start=1):
        if not part or not part.isdigit():
            return (False, None,
                    f"octet #{i} ({part!r}) is not a non-negative "
                    f"integer")
        try:
            n = int(part)
        except ValueError:
            return (False, None,
                    f"octet #{i} ({part!r}) is not a valid integer")
        if n < 0 or n > 255:
            return (False, None,
                    f"octet #{i} ({n}) is out of range — each octet "
                    f"must be 0 to 255")
        octets.append(n)
    # Canonical form has no leading zeros (e.g. "0.0.0.1", not "00.00.00.01").
    return (True, ".".join(str(o) for o in octets), None)


def normalise_ospf_area_id(value: str) -> Optional[str]:
    """Convenience: return the dotted form if ``value`` validates,
    else None. For call sites that don't need the error string."""
    ok, normalised, _ = validate_ospf_area_id(value)
    return normalised if ok else None


def _int_to_dotted(n: int) -> str:
    """32-bit unsigned → dotted-decimal. ``1`` → ``"0.0.0.1"``."""
    return (
        f"{(n >> 24) & 0xff}.{(n >> 16) & 0xff}."
        f"{(n >> 8) & 0xff}.{n & 0xff}"
    )
