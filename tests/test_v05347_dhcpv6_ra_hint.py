"""v0.5.347 — Upstream Config Hint appends the Router Advertisement
block whenever a DHCP client is IPv6-enabled. RA is REQUIRED for
DHCPv6-leased clients to get the on-link /64 route + default; the
DHCPv6 lease itself is only the /128 host address.

Operator on srv06 2026-09-15 asked to add this to the hint template
after confirming end-to-end DHCPv6 needed both the relay stanza
AND `protocols router-advertisement` on the QFX irb.40.

Rendering:
- Junos: `set protocols router-advertisement interface <svi> ...`
  with M-bit, O-bit, and a `<CLIENT-VLAN-PREFIX>` placeholder for
  the /64 on the SVI.
- Cisco IOS: `ipv6 nd managed-config-flag / other-config-flag /
  prefix <CLIENT-VLAN-PREFIX>` on the SVI interface block.
- Arista EOS: same shape as IOS with EOS indentation.

Placeholder because the CLIENT device on the netgen side doesn't
know what /64 the relay's IRB owns — that lives on the switch.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.upstream_hints import _dhcp_relay_stanza  # noqa: E402


def test_marker_present():
    src = (_REPO / "utils" / "upstream_hints.py").read_text()
    assert "v0.5.347 (audit dhcpv6-ra-hint)" in src


# --- Junos ---

def test_junos_v6_client_emits_ra_block():
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv4_enabled": False, "ipv6_enabled": True,
         "upstream_server_hint_v6": "2001:db8:20::2"},
    )
    # Three canonical RA settings must appear on irb.40.
    assert "set protocols router-advertisement interface irb.40 managed-configuration" in stanza
    assert "set protocols router-advertisement interface irb.40 other-stateful-configuration" in stanza
    assert "set protocols router-advertisement interface irb.40 prefix <CLIENT-VLAN-PREFIX> on-link" in stanza


def test_junos_v6_ra_block_has_placeholder():
    """The prefix must be a placeholder the operator substitutes —
    the client-side dialog doesn't know the switch's IRB /64."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv6_enabled": True, "ipv4_enabled": False},
    )
    assert "<CLIENT-VLAN-PREFIX>" in stanza


def test_junos_v4_only_client_does_NOT_emit_ra_block():
    """RA only makes sense when v6 is enabled. A v4-only DHCP
    client must NOT emit the RA config."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv4_enabled": True, "ipv6_enabled": False,
         "upstream_server_hint": "10.0.0.2"},
    )
    assert "router-advertisement" not in stanza


# --- Cisco ---

def test_cisco_v6_client_emits_nd_block_on_svi():
    stanza = _dhcp_relay_stanza(
        "cisco", "40",
        {"ipv4_enabled": False, "ipv6_enabled": True,
         "upstream_server_hint_v6": "2001:db8:20::2"},
    )
    assert " ipv6 nd managed-config-flag" in stanza
    assert " ipv6 nd other-config-flag" in stanza
    assert " ipv6 nd prefix <CLIENT-VLAN-PREFIX>" in stanza


def test_cisco_v4_only_client_does_NOT_emit_nd_block():
    stanza = _dhcp_relay_stanza(
        "cisco", "40",
        {"ipv4_enabled": True, "ipv6_enabled": False,
         "upstream_server_hint": "10.0.0.2"},
    )
    assert "ipv6 nd managed-config-flag" not in stanza
    assert "ipv6 nd prefix" not in stanza


# --- Arista ---

def test_arista_v6_client_emits_nd_block_on_svi():
    stanza = _dhcp_relay_stanza(
        "arista", "40",
        {"ipv4_enabled": False, "ipv6_enabled": True,
         "upstream_server_hint_v6": "2001:db8:20::2"},
    )
    assert "   ipv6 nd managed-config-flag" in stanza
    assert "   ipv6 nd other-config-flag" in stanza
    assert "   ipv6 nd prefix <CLIENT-VLAN-PREFIX>" in stanza


def test_arista_v4_only_client_does_NOT_emit_nd_block():
    stanza = _dhcp_relay_stanza(
        "arista", "40",
        {"ipv4_enabled": True, "ipv6_enabled": False,
         "upstream_server_hint": "10.0.0.2"},
    )
    assert "ipv6 nd managed-config-flag" not in stanza


# --- placeholder-substitution note in Junos ---

def test_junos_ra_comment_explains_why_ra_is_required():
    """The comment must explain that RA is REQUIRED (not optional)
    for on-link + default. Operator-facing text."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv6_enabled": True, "ipv4_enabled": False},
    )
    assert "REQUIRED" in stanza
    assert "on-link" in stanza.lower()
    assert "/128" in stanza  # explanation of what happens without RA


def test_ra_block_absent_when_no_dhcp_at_all():
    """Sanity check: passing dhcp_config that turns off both
    families returns empty (no relay, no RA)."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv4_enabled": False, "ipv6_enabled": False},
    )
    assert stanza == ""


def test_v0_5_341_and_v0_5_346_markers_still_intact():
    """v0.5.341 wrote the v6 relay stanza; v0.5.346 fixed the
    client's accept_ra. v0.5.347 is stacked on both — the earlier
    markers must survive."""
    hints_src = (_REPO / "utils" / "upstream_hints.py").read_text()
    assert "v0.5.341 (audit dhcpv6-relay-hint-parity)" in hints_src
    dhcp_src = (_REPO / "utils" / "dhcp.py").read_text()
    assert "v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)" in dhcp_src
