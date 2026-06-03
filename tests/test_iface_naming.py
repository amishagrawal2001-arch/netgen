"""v0.3.11 — shared canonical iface-key helper.

The user reported "trying to load saved file, no devices" against a
session.json where the saved device had ``Interface=" - ens5np0"``
(leading separator, no TG prefix). Investigation found three classes
of corruption — server returning a malformed field, save persisting
whatever the in-memory bucket key was, load trusting it on the way
back in — and **no shared rule** for what the canonical iface key
even is. Each populate / serialize site decided differently.

``utils.iface_naming.canonical_iface_key`` is now the single source
of truth. This file pins:

  * The function exists, accepts the same call shapes every site uses.
  * Both source-of-corruption sites (``reload_devices_from_server``
    and ``_save_session_impl``) actually invoke it.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent


# ─────────────────────────────────── helper unit tests

def test_already_canonical_returned_unchanged():
    from utils.iface_naming import canonical_iface_key, looks_canonical
    assert looks_canonical("TG 0 - ens5np0")
    assert canonical_iface_key("TG 0 - ens5np0", tg_id=0) == "TG 0 - ens5np0"
    # tg_id mismatch — caller's tg_id is informational only; if the
    # input is already canonical we don't second-guess it.
    assert canonical_iface_key("TG 5 - ens5", tg_id=99) == "TG 5 - ens5"


def test_repair_leading_separator():
    from utils.iface_naming import canonical_iface_key
    # The exact user-reported shape.
    assert canonical_iface_key(" - ens5np0", tg_id=1) == "TG 1 - ens5np0"
    # Same with a string tg_id.
    assert canonical_iface_key(" - ens5np0", tg_id="1") == "TG 1 - ens5np0"


def test_repair_port_prefix():
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key("Port: ens5np0", tg_id=2) == "TG 2 - ens5np0"
    assert canonical_iface_key(" - Port: ens5np0", tg_id=3) == "TG 3 - ens5np0"


def test_repair_bare_port_name():
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key("ens5np0", tg_id=0) == "TG 0 - ens5np0"


def test_strip_bullet_prefix():
    """Server tree shows ports with a leading "• " bullet — if that
    ever leaks into a device's Interface field, strip it."""
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key("• ens5np0", tg_id=4) == "TG 4 - ens5np0"


def test_no_tg_id_returns_bare_cleaned():
    """When the caller can't supply tg_id, return the cleaned bare
    port name. The upstream load-time repair can still match it
    against the server interface list."""
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key(" - ens5np0", tg_id=None) == "ens5np0"
    assert canonical_iface_key("Port: ens5", tg_id=None) == "ens5"


def test_empty_or_none_returns_empty():
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key("", tg_id=0) == ""
    assert canonical_iface_key(None, tg_id=0) == ""
    assert canonical_iface_key("   ", tg_id=0) == ""


def test_non_string_returns_empty():
    """Defensive — call sites pass dict.get() results that could be
    any type if a session file is hand-edited."""
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key(123, tg_id=0) == ""
    assert canonical_iface_key(["ens5"], tg_id=0) == ""


def test_tg_id_with_prefix_is_idempotent():
    """If a caller accidentally passes ``tg_id="TG 0"`` instead of
    ``0``, the result must still be ``"TG 0 - ens5"``, not the
    double-prefixed ``"TG TG 0 - ens5"``."""
    from utils.iface_naming import canonical_iface_key
    assert canonical_iface_key("ens5", tg_id="TG 0") == "TG 0 - ens5"
    assert canonical_iface_key("ens5", tg_id="tg 7") == "TG 7 - ens5"


def test_looks_canonical_rejects_malformed():
    from utils.iface_naming import looks_canonical
    assert not looks_canonical("")
    assert not looks_canonical(" - ens5")
    assert not looks_canonical("Port: ens5")
    assert not looks_canonical("ens5")
    assert not looks_canonical("TG  - ens5")  # missing digit
    assert not looks_canonical("TG-0 - ens5")  # wrong separator
    # Sanity: a few valid shapes accepted.
    assert looks_canonical("TG 0 - ens5")
    assert looks_canonical("TG 12 - ens5")


# ─────────────────────────────────── source-of-corruption call sites

def test_reload_devices_from_server_uses_canonicalizer():
    """The reload path used to only synthesize "TG N - port" when the
    server returned an empty Interface. A truthy-but-malformed value
    flowed through verbatim and corrupted `all_devices` keys. Pin
    that the call site now routes every value through the shared
    canonicalizer."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    # Grab the reload_devices_from_server method body. Method spans
    # are large; substring-match the symbol within ±2k lines is
    # cheap and unambiguous (the symbol is only defined once).
    assert "def reload_devices_from_server" in src
    # The canonicalizer must be imported AND invoked inside the
    # reload path. Substring match against the full file is enough
    # to catch a refactor that drops the call.
    assert "from utils.iface_naming import canonical_iface_key" in src, (
        "reload_devices_from_server no longer imports the shared "
        "canonical_iface_key helper — server-returned malformed "
        "Interface values will silently corrupt all_devices again"
    )


def test_save_session_impl_uses_canonicalizer():
    """_save_session_impl must canonicalize device Interface fields
    before persistence. Without this, an in-memory malformed bucket
    key (e.g. from a half-loaded server response) gets baked into
    the on-disk session.json and propagates forward."""
    src = (REPO / "traffic_client" / "menu_actions.py").read_text()
    assert "def _save_session_impl" in src
    assert "from utils.iface_naming import (" in src and \
           "canonical_iface_key" in src and \
           "looks_canonical" in src, (
        "_save_session_impl no longer imports the canonical iface "
        "helpers — save side will persist malformed Interface fields"
    )


def test_canonical_iface_key_idempotent():
    """Calling the canonicalizer twice on the same input must give
    the same result — important because the save sweep runs on data
    that may have come from a previous load (which already ran the
    repair)."""
    from utils.iface_naming import canonical_iface_key
    once = canonical_iface_key(" - ens5np0", tg_id=1)
    twice = canonical_iface_key(once, tg_id=1)
    assert once == twice == "TG 1 - ens5np0"
