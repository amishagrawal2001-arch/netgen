"""v0.5.224 — OSPF network-type defaults to point-to-point.

Regression driver (same bugfix as the loopback collision detection —
part of the same shipped patch): on srv06 the sole device1's OSPFv4
and OSPFv6 sessions stayed stuck at Init/DROther even after removing
device2. tcpdump verified Hellos going bidirectionally on the wire —
peer's Hello had an empty neighbor list, ours listed peer. Root cause
was network-type mismatch: peer (QFX5130 uplink) uses OSPF network
point-to-point; netgen's server default was broadcast. Peer discards
our broadcast-format Hellos before it puts us in its neighbor list;
we sit in Init forever.

Fix: default `p2p_ipv4`/`p2p_ipv6`/`p2p` to True in
`utils/ospf._configure_ospf` (matching ISIS's unconditional
p2p-on-VLAN-subif precedent in `utils/isis.py`). Verified live on
srv06: after `ip ospf network point-to-point` + `ipv6 ospf6 network
point-to-point`, both v4 and v6 flipped to Full in ~60s.

These tests pin the resolved p2p_ipv4/p2p_ipv6 boolean the server
uses given various shapes of `ospf_config`. The actual vtysh
emission is guarded by the same p2p_ipv4/p2p_ipv6 flags at line
558/622 of utils/ospf.py, so pinning the resolution is enough to
catch a regression where somebody flips the default back.
"""

import re
from pathlib import Path


OSPF_MODULE = Path("utils/ospf.py").read_text()


def _extract_default_for(field: str) -> str:
    """The resolution block in utils/ospf.py picks the default via
    a pair of `.get(field, DEFAULT)` calls (one inside the `if
    field in ospf_config` branch, one in the fallback). Both must
    agree for the default to hold on new-config paths. This helper
    returns whatever literal the LOOKUP branch uses so the test can
    assert both are True.
    """
    # Match ".get(\"p2p_ipv4\", True)" style — capture literal after comma.
    pattern = rf'"{re.escape(field)}"[^)]+get\("{re.escape(field)}",\s*([A-Za-z]+)\)'
    matches = re.findall(pattern, OSPF_MODULE)
    return matches[0] if matches else ""


def test_ospf_p2p_ipv4_default_is_true():
    """p2p_ipv4 must default True. srv06 QFX5130 uplink is P2P
    (verified 2026-08-30); False would stick OSPFv4 at Init/DROther."""
    assert _extract_default_for("p2p_ipv4") == "True", (
        "utils/ospf.py must default p2p_ipv4 to True — "
        "peer switches on VLAN subifs default to P2P and "
        "would reject broadcast-format Hellos."
    )


def test_ospf_p2p_ipv6_default_is_true():
    """Same for OSPFv6."""
    assert _extract_default_for("p2p_ipv6") == "True", (
        "utils/ospf.py must default p2p_ipv6 to True — "
        "OSPFv6 hits the same P2P/broadcast mismatch as OSPFv4."
    )


def test_ospf_p2p_generic_fallback_is_true():
    """The generic `p2p` fallback path (backward-compat: old configs
    only set p2p, not p2p_ipv4/p2p_ipv6) must also default True.
    Otherwise a config with `p2p_ipv4` absent but `p2p_ipv6` present
    would silently deploy OSPFv4 as broadcast."""
    # Grep for `.get("p2p", DEFAULT)` in the module.
    matches = re.findall(r'\.get\("p2p",\s*([A-Za-z]+)\)', OSPF_MODULE)
    # Should appear twice (once each in the ipv4 and ipv6 fallback).
    assert len(matches) >= 2, "expected two p2p-fallback .get() calls"
    for m in matches:
        assert m == "True", (
            f"utils/ospf.py p2p generic fallback resolves to {m!r} — "
            "must be True so backward-compat configs still get P2P."
        )


def test_ospf_p2p_ui_default_matches():
    """The OSPF subtab checkbox must render pre-ticked when the DB
    doesn't yet have a p2p_ipv4/p2p_ipv6 explicit value — otherwise
    the operator sees "unchecked" and thinks P2P is off, even though
    the server actually deploys P2P. Server and UI must agree."""
    ui_module = Path("utils/devices_tab_ospf.py").read_text()
    # IPv6 branch: .get("p2p_ipv6", True) — should be True.
    m = re.search(r'\.get\("p2p_ipv6",\s*([A-Za-z]+)\)', ui_module)
    assert m and m.group(1) == "True", (
        "devices_tab_ospf.py P2P checkbox must default True for IPv6 "
        "so the UI mirrors the server default."
    )
    # IPv4 branch uses a nested .get() for backward compat with old
    # generic "p2p" key. The innermost default is what matters.
    m = re.search(r'"p2p_ipv4",\s*ospf_config\.get\("p2p",\s*([A-Za-z]+)\)', ui_module)
    assert m and m.group(1) == "True", (
        "devices_tab_ospf.py P2P checkbox IPv4 default must be True."
    )


def test_ospf_explicit_false_still_honored():
    """The default flip must not clobber explicit False from a config
    that deliberately requests broadcast. The resolution logic is
    `if 'p2p_ipv4' in ospf_config: ...get(..., True)` — the .get()'s
    fallback only fires when the key is missing. When present with
    False, the resolved value is False. This test verifies the branch
    structure is unchanged so False configs still land as broadcast."""
    # The `if "p2p_ipv4" in ospf_config:` guard must still exist.
    assert 'if "p2p_ipv4" in ospf_config:' in OSPF_MODULE
    assert 'if "p2p_ipv6" in ospf_config:' in OSPF_MODULE
    # Sanity: an inline .get on p2p_ipv4/p2p_ipv6 with False as default
    # inside the guarded branch would resolve to False — so those
    # branches must use True as the .get() default (they only fire
    # when the key IS in the config, so the default is irrelevant to
    # behavior; but if we ever refactor away the guard, keeping True
    # matches everything else and prevents a regression).
    assert 'get("p2p_ipv4", False)' not in OSPF_MODULE
    assert 'get("p2p_ipv6", False)' not in OSPF_MODULE
    assert 'get("p2p", False)' not in OSPF_MODULE
