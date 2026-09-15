"""v0.5.334 — overall device-status calculation must not require
`arp_ipv4_resolved` when IPv4 isn't configured.

Operator on srv06 2026-09-15: after upgrading to v0.5.333 (VRF
reconcile on re-apply) and re-applying device5 (IPv6-only,
2001:db8:20::2/64 on vlan20, no IPv4), the ARP monitor correctly
reported `arp_ipv6_resolved=True` in the database. But the client's
overall-status calculation started with `overall_resolved =
ipv4_resolved` UNCONDITIONALLY, then AND-ed the ipv6/gateway flags
in only when configured. On IPv6-only devices `arp_ipv4_resolved`
is 0 (nothing to resolve on the server side), so `overall_resolved`
was False even when everything that was configured resolved fine —
device5 stayed yellow.

Fix: mirror the ipv6_configured / gateway_configured pattern for
IPv4 too. Start `overall_resolved` at True and AND in each family
that is actually configured.

Touches three sites in `widgets/devices_tab.py`:
- `_apply_device_status_row` (device-poll overall status icon)
- `_check_arp_resolution_sync` (sync ARP status check)
- `_check_individual_arp_resolution` (detailed per-IP status)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _devices_tab_src():
    return (_REPO / "widgets" / "devices_tab.py").read_text()


def test_marker_present():
    src = _devices_tab_src()
    assert "v0.5.334 (audit ipv4-required-on-ipv6-only)" in src


def test_no_unconditional_ipv4_seed_in_overall_resolved():
    """Regression guard: the buggy line `overall_resolved =
    ipv4_resolved` (or its `arp_results["ipv4_resolved"]` variant)
    must be GONE from all three call sites. The new shape is
    `overall_resolved = True` followed by a chain of `if
    <family>_configured:` AND-s."""
    src = _devices_tab_src()
    # Neither of the two buggy shapes should survive.
    assert "overall_resolved = ipv4_resolved" not in src, (
        "buggy unconditional IPv4 seed (bare variable) still present"
    )
    assert 'overall_resolved = arp_results["ipv4_resolved"]' not in src, (
        "buggy unconditional IPv4 seed (dict-lookup variant) still "
        "present"
    )


def test_three_call_sites_use_new_shape():
    """The three overall-resolved computation sites in this file
    must all start `overall_resolved = True` and gate IPv4 behind
    `if ipv4_configured:`."""
    src = _devices_tab_src()
    # Count `overall_resolved = True` — must be exactly 3 (one per
    # site).
    count_true_seed = src.count("overall_resolved = True")
    assert count_true_seed == 3, (
        f"expected exactly 3 `overall_resolved = True` seeds, got "
        f"{count_true_seed} — the three arp-overall sites are: "
        f"_apply_device_status_row, _check_arp_resolution_sync, "
        f"_check_individual_arp_resolution"
    )
    # And `if ipv4_configured:` must appear 3 times.
    count_ipv4_gate = src.count("if ipv4_configured:")
    assert count_ipv4_gate == 3, (
        f"expected exactly 3 `if ipv4_configured:` guards, got "
        f"{count_ipv4_gate}"
    )


def test_ipv4_configured_derived_from_ipv4_address_field():
    """`ipv4_configured` must derive from the same field pair as
    `ipv6_configured` — the server-side `ipv4_address` field OR the
    UI-side `IPv4` field, .strip()-ed for whitespace."""
    src = _devices_tab_src()
    # The derivation pattern must appear at all three sites.
    patt = re.compile(
        r'ipv4_value\s*=\s*\(device_data\.get\("ipv4_address"\)\s*'
        r'or\s*device_data\.get\("IPv4"\)\s*or\s*""\)\.strip\(\)'
    )
    # Two of the three sites use ipv4_value → ipv4_configured; the
    # third (in _apply_device_status_row) inlines it directly.
    # Verify BOTH patterns exist.
    two_step_hits = patt.findall(src)
    assert len(two_step_hits) >= 2, (
        f"expected at least 2 `ipv4_value = ...` two-step "
        f"derivations, got {len(two_step_hits)}"
    )
    inline_patt = re.compile(
        r'ipv4_configured\s*=\s*bool\(\(device_data\.get\("ipv4_address"\)\s*'
        r'or\s*device_data\.get\("IPv4"\)\s*or\s*""\)\.strip\(\)\)'
    )
    inline_hits = inline_patt.findall(src)
    assert len(inline_hits) >= 1, (
        "expected at least 1 inlined `ipv4_configured = bool(...)` "
        "site — _apply_device_status_row uses this form"
    )


def test_failed_parts_message_also_gates_ipv4():
    """The status-message builder for the unresolved case at
    `_check_individual_arp_resolution` must skip IPv4 when it's not
    configured — otherwise the client reports `ARP pending: IPv4`
    on IPv6-only devices even though there's no IPv4 to resolve."""
    src = _devices_tab_src()
    # The buggy shape:
    assert "if not ipv4_resolved:\n                        failed_parts.append(\"IPv4\")" not in src, (
        "buggy unconditional `if not ipv4_resolved` failed_parts "
        "guard still present"
    )
    # And the fixed shape must exist:
    assert "if ipv4_configured and not ipv4_resolved:" in src


def test_devices_tab_ast_parses():
    import ast
    ast.parse(_devices_tab_src())


def test_comment_names_operator_incident():
    """The comment at the fix site must name srv06 device5 and the
    IPv6-only shape so a future refactor can see WHY the guard
    exists."""
    src = _devices_tab_src()
    idx = src.index("v0.5.334 (audit ipv4-required-on-ipv6-only)")
    body = src[idx:idx + 3000]
    assert "IPv6-only" in body or "ipv6-only" in body.lower()
    assert "device5" in body or "srv06" in body
    assert "arp_ipv4_resolved" in body


def test_v0_5_333_marker_still_intact():
    """v0.5.333 (VRF reconcile on re-apply) must still be present in
    the sister file — v0.5.334 is stacked on top, not a replacement."""
    src = (_REPO / "utils" / "frr_docker.py").read_text()
    assert "v0.5.333 (audit vrf-reconcile-on-reapply)" in src
