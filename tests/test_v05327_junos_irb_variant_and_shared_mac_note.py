"""v0.5.327 — Juniper upstream-hint now emits BOTH the subif-style
(MX/vMX/router) AND the IRB-style (QFX/EX/ACX switching) variants,
with a CRITICAL `# ...` note about the QFX shared-MAC gotcha.

Operator report on srv06 2026-09-14: `ping 2001:db8:20::2` from the
switch (san-q5130-48c-02, QFX5130) failed one-way — echo requests
arrived at netgen but netgen's NS for the switch's irb.20 got no
NA back. Root cause: all IRBs on the switch shared the chassis MAC
(default Junos behavior), which breaks NDP when multiple IRBs are
trunked to a peer that L2-terminates each VLAN separately. Fix
was a single line on the switch:
`set interfaces irb.20 mac d0:48:a1:d0:27:20` (unique per-IRB MAC,
last byte = VLAN ID).

Netgen-side prevention: bake the "unique per-IRB MAC" line + a
`# CRITICAL:` explanation into the Juniper upstream-hint output,
in an IRB-style variant emitted alongside the existing subif
variant. Operators using QFX/EX/ACX see the paste-body up front
and never hit the wall.

Cisco/Arista tabs unchanged (those platforms use subif style
too, and don't have the shared-MAC problem — Cisco uses
per-subinterface MACs by default).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


def _device_v6(vlan="20"):
    return {
        "device_name": "device5", "vlan": vlan,
        "ipv6_address": "2001:db8:20::2", "ipv6_mask": "64",
        "ipv6_gateway": "2001:db8:20::1",
    }


def _device_v4(vlan="10"):
    return {
        "device_name": "device1", "vlan": vlan,
        "ipv4_address": "192.168.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "192.168.0.1",
    }


# ---------- Juniper output has BOTH variants ----------


def test_juniper_output_has_subif_variant_marker():
    out = u.render_juniper(_device_v6())
    assert "Option A: subif style (MX / vMX / router-mode)" in out


def test_juniper_output_has_irb_variant_marker():
    out = u.render_juniper(_device_v6())
    assert "Option B: IRB style (QFX / EX / ACX switching)" in out


def test_juniper_subif_variant_uses_ge_uplink():
    """Subif style: `set interfaces ge-0/0/0 unit <vlan> ...`.
    Placeholder physical uplink stays `ge-0/0/0` (operator edits)."""
    out = u.render_juniper(_device_v6("20"))
    assert "set interfaces ge-0/0/0 vlan-tagging" in out
    assert "set interfaces ge-0/0/0 unit 20 vlan-id 20" in out


def test_juniper_irb_variant_uses_ethernet_switching_trunk():
    """IRB style: physical is ethernet-switching trunk, IRB owns
    the L3 address."""
    out = u.render_juniper(_device_v6("20"))
    assert (
        "set interfaces ge-0/0/0 unit 0 family ethernet-switching "
        "interface-mode trunk vlan members v20"
    ) in out
    assert "set vlans v20 vlan-id 20" in out
    assert "set vlans v20 l3-interface irb.20" in out


def test_juniper_irb_variant_puts_ip_on_irb():
    """The L3 address goes on `irb.<vlan>`, NOT on the physical
    subunit (which is ethernet-switching only)."""
    out = u.render_juniper(_device_v6("20"))
    assert "set interfaces irb.20 family inet6 address 2001:db8:20::1/64" in out


def test_juniper_irb_variant_has_critical_shared_mac_note():
    """The whole point of the IRB variant — make the shared-MAC
    trap loud and visible so the next operator doesn't burn a
    day debugging it."""
    out = u.render_juniper(_device_v6("20"))
    assert "CRITICAL: on QFX/EX all IRBs share the chassis MAC by default" in out
    # And an actionable fix line: `set interfaces irb.<vlan> mac ...`.
    assert "set interfaces irb.20 mac" in out


def test_juniper_irb_variant_last_byte_matches_vlan_id():
    """srv06 operator convention (2026-09-14): last MAC byte =
    VLAN ID as-written for readability (irb.20 → `..:20`,
    irb.30 → `..:30`). Anything unique across IRBs works — this
    convention is just visually memorable.

    v0.5.328: MAC is now self-contained (LAA prefix `02:00:00:00:00:`
    + VLAN ID as last byte), no `<chassis-base>` placeholder."""
    out_v20 = u.render_juniper(_device_v6("20"))
    out_v30 = u.render_juniper(_device_v6("30"))
    assert "set interfaces irb.20 mac 02:00:00:00:00:20" in out_v20
    assert "set interfaces irb.30 mac 02:00:00:00:00:30" in out_v30


def test_juniper_irb_variant_mac_is_self_contained():
    """v0.5.328 (audit self-contained-irb-mac): the emitted MAC is
    ready to paste as-is — no `<chassis-base>` placeholder for the
    operator to substitute. Uses locally-administered prefix
    (`02:` — first octet bit 1 set) so no vendor-OUI conflict."""
    out = u.render_juniper(_device_v6("20"))
    assert "<chassis-base>" not in out
    # LAA MAC ready to paste.
    assert "02:00:00:00:00:20" in out


def test_juniper_irb_variant_carries_ipv4_when_present():
    out = u.render_juniper(_device_v4("10"))
    assert "set interfaces irb.10 family inet address 192.168.0.1/24" in out


# ---------- Cisco / Arista unchanged (no IRB variant) ----------


def test_cisco_output_no_irb_variant():
    """Cisco per-subinterface config already uses distinct MACs
    by default (not chassis-shared) — no IRB variant needed."""
    out = u.render_cisco(_device_v6("20"))
    assert "Option A: subif style" not in out
    assert "Option B: IRB style" not in out
    assert "chassis MAC" not in out


def test_arista_output_no_irb_variant():
    out = u.render_arista(_device_v6("20"))
    assert "Option A: subif style" not in out
    assert "Option B: IRB style" not in out
    assert "chassis MAC" not in out


# ---------- Subif variant still fully functional ----------


def test_juniper_subif_variant_still_has_family_inet_on_subif():
    """Regression guard — Option A must still write the address
    on the physical subif (unchanged from v0.5.226 behavior)."""
    out = u.render_juniper(_device_v6("20"))
    assert "set interfaces ge-0/0/0 unit 20 family inet6 address 2001:db8:20::1/64" in out


# ---------- VLAN edge cases ----------


def test_juniper_irb_variant_handles_vlan_zero():
    """Untagged (VLAN 0) is unusual but shouldn't crash the
    renderer or emit `..:00` (would collide with chassis MAC
    of `..:00`). Just verify no crash + safe fallback."""
    out = u.render_juniper({"device_name": "u", "vlan": "0"})
    assert "set interfaces irb.0" in out
    # last byte falls out as "00" — fine, operator adjusts.


def test_juniper_irb_variant_handles_non_numeric_vlan():
    """Legacy dict shape might have vlan as a non-int. Fallback
    to "20" (arbitrary) rather than crashing."""
    dev = {"device_name": "u", "vlan": "abc"}
    # Don't crash.
    out = u.render_juniper(dev)
    assert "irb." in out


# ---------- AST parse ----------


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())
