"""v0.5.341 — Upstream Config Hint emits a DHCPv6 relay-agent
stanza for every vendor alongside the existing v0.5.318 v4 stanza.

Operator on srv06 2026-09-15 verified end-to-end DHCPv6 with the
Junos QFX5130 config below, and asked netgen to hint the same
shape so any dual-stack DHCP-client device the operator adds gets
a copy-paste-ready block:

```
forwarding-options {
    dhcp-relay {
        overrides { allow-snooped-clients; }
        forward-only;
        server-group { DHCP-SERVERS { <v4-server>; } }
        active-server-group DHCP-SERVERS;
        group CLIENTS { interface irb.<vlan>; }

        dhcpv6 {                                    ← v0.5.341 adds this
            overrides { allow-snooped-clients; }
            server-group { DHCPV6-SERVERS { <v6-server>; } }
            group CLIENTS-V6 {
                active-server-group DHCPV6-SERVERS;
                interface irb.<vlan>;
            }
        }
    }
}
```

Cisco IOS and Arista EOS use `ipv6 dhcp relay destination
<v6-server>` on the same SVI alongside `ip helper-address`; both
vendor branches gain that block.

The stanza emits each family only when its `_enabled` flag on the
DHCP-client's `dhcp_config` is truthy. Pre-v0.5.341 (v4-only)
configs still emit v4 to preserve the v0.5.318 behavior; adding
`ipv6_enabled=True` adds the v6 block.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.upstream_hints import _dhcp_relay_stanza  # noqa: E402


# --- Junos ---

def test_junos_dual_stack_emits_v4_and_v6_blocks():
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {
            "ipv4_enabled": True,
            "ipv6_enabled": True,
            "upstream_server_hint": "2001:db8:20::2",   # intentionally v6 shape ignored for v4
            "upstream_server_hint_v6": "2001:db8:20::2",
        },
    )
    # v4 block:
    assert "set forwarding-options dhcp-relay overrides allow-snooped-clients" in stanza
    assert "set forwarding-options dhcp-relay group CLIENTS interface irb.40" in stanza
    # v6 block:
    assert "set forwarding-options dhcp-relay dhcpv6 overrides allow-snooped-clients" in stanza
    assert "set forwarding-options dhcp-relay dhcpv6 server-group DHCPV6-SERVERS 2001:db8:20::2" in stanza
    assert "set forwarding-options dhcp-relay dhcpv6 group CLIENTS-V6 active-server-group DHCPV6-SERVERS" in stanza
    assert "set forwarding-options dhcp-relay dhcpv6 group CLIENTS-V6 interface irb.40" in stanza


def test_junos_v6_only_client_skips_v4_block():
    """A v6-only DHCP-client (like operator's device6) must NOT emit
    the v4 relay block — the operator hasn't set an IPv4 server."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv4_enabled": False, "ipv6_enabled": True},
    )
    assert "dhcp-relay dhcpv6" in stanza
    # No v4 group/server-group lines.
    assert "set forwarding-options dhcp-relay overrides" not in stanza
    assert "set forwarding-options dhcp-relay group CLIENTS interface" not in stanza
    assert "set forwarding-options dhcp-relay server-group DHCP-SERVERS" not in stanza


def test_junos_v4_only_client_matches_pre_v0_5_341_behavior():
    """Regression guard: pre-v0.5.341 `dhcp_config` had no
    ipv4_enabled/ipv6_enabled fields and always emitted v4. Same
    behavior must be preserved for callers that omit the flags."""
    stanza = _dhcp_relay_stanza(
        "juniper", "30", {"upstream_server_hint": "192.168.1.1"},
    )
    assert "set forwarding-options dhcp-relay server-group DHCP-SERVERS 192.168.1.1" in stanza
    # No v6 block when ipv6_enabled defaults to False.
    assert "dhcpv6" not in stanza


def test_junos_marker_note_names_the_gotcha():
    """The Junos v6 block must include a note explaining that the
    top-level dhcp-relay handles v4 only — this is the exact
    gotcha the operator hit on srv06."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv6_enabled": True, "ipv4_enabled": False},
    )
    assert "handles v4 only" in stanza or "v4 only" in stanza


# --- Cisco ---

def test_cisco_dual_stack_svi_has_both_helper_and_v6_destination():
    stanza = _dhcp_relay_stanza(
        "cisco", "40",
        {
            "ipv4_enabled": True, "ipv6_enabled": True,
            "upstream_server_hint": "10.0.0.2",
            "upstream_server_hint_v6": "2001:db8:20::2",
        },
    )
    # The SVI block appears once and carries both address families.
    assert "interface Vlan40" in stanza
    assert " ip helper-address 10.0.0.2" in stanza
    assert " ipv6 dhcp relay destination 2001:db8:20::2" in stanza


def test_cisco_v6_only_client_skips_v4_lines():
    stanza = _dhcp_relay_stanza(
        "cisco", "40",
        {"ipv4_enabled": False, "ipv6_enabled": True,
         "upstream_server_hint_v6": "2001:db8:20::2"},
    )
    assert "ipv6 dhcp relay destination 2001:db8:20::2" in stanza
    assert "ip helper-address" not in stanza
    assert "service dhcp" not in stanza


# --- Arista ---

def test_arista_dual_stack_svi_has_both_helper_and_v6_destination():
    stanza = _dhcp_relay_stanza(
        "arista", "40",
        {
            "ipv4_enabled": True, "ipv6_enabled": True,
            "upstream_server_hint": "10.0.0.2",
            "upstream_server_hint_v6": "2001:db8:20::2",
        },
    )
    assert "interface Vlan40" in stanza
    assert "   ip helper-address 10.0.0.2" in stanza
    assert "   ipv6 dhcp relay destination 2001:db8:20::2" in stanza


def test_arista_v6_only_client_skips_v4_lines():
    stanza = _dhcp_relay_stanza(
        "arista", "40",
        {"ipv4_enabled": False, "ipv6_enabled": True,
         "upstream_server_hint_v6": "2001:db8:20::2"},
    )
    assert "ipv6 dhcp relay destination 2001:db8:20::2" in stanza
    assert "ip helper-address" not in stanza


# --- placeholders + hint substitution ---

def test_v6_server_hint_shows_placeholder_when_absent():
    """No `upstream_server_hint_v6` → placeholder `<DHCPV6-SERVER-IP>`
    lands in the output so the operator sees what to substitute."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv6_enabled": True, "ipv4_enabled": False},
    )
    assert "<DHCPV6-SERVER-IP>" in stanza


def test_v6_server_hint_honored_when_supplied():
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv6_enabled": True, "ipv4_enabled": False,
         "upstream_server_hint_v6": "2001:db8:20::2"},
    )
    assert "2001:db8:20::2" in stanza
    assert "<DHCPV6-SERVER-IP>" not in stanza


# --- no-op case ---

def test_no_families_enabled_returns_empty():
    """Defensive: if both families are off (weird config), don't
    emit a stanza."""
    stanza = _dhcp_relay_stanza(
        "juniper", "40",
        {"ipv4_enabled": False, "ipv6_enabled": False},
    )
    assert stanza == ""


# --- source markers ---

def test_v0_5_341_marker_present():
    src = (_REPO / "utils" / "upstream_hints.py").read_text()
    assert "v0.5.341 (audit dhcpv6-relay-hint-parity)" in src


def test_v0_5_318_marker_still_intact():
    src = (_REPO / "utils" / "upstream_hints.py").read_text()
    assert "v0.5.318" in src
