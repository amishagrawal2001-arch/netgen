"""v0.5.349 — two fixes bundled:

**A. Server-side MAC auto-generation fallback (`audit auto-mac-
server-fallback`)** — defense in depth for v0.5.348. The client-
side auto-gen only fires in a fresh Add Device dialog; a stale
client (not `git pull`ed since v0.5.348), an imported device row,
or an apply from a script may still arrive with empty
`mac_address`. Operator on srv06 2026-09-16 hit exactly this on
device7: apply landed with `mac_address=""` → v0.5.325's
`if mac_address:` guard skipped `ip link set` → vlan41 kept its
parent NIC MAC.

Fix: when `/api/device/apply` sees empty `mac_address`, derive
one deterministically from `device_id` (MD5-hash → 5 low bytes
prefixed with `02:` LAA) and persist to the DB before running
the apply so subsequent reads see it.

**B. DHCP-client-with-no-lease shows green (`audit dhcp-client-
no-lease-shows-green`)** — regression from v0.5.334. My
`overall_resolved = True; if <fam>_configured: ...` chain sets
green when NOTHING is configured (all _configured flags False).
For a DHCP-client device whose lease is still in flight, all
three flags are False → overall_resolved stays True → green
icon despite no ARP resolution.

Fix: at all three v0.5.334 call sites, add a `dhcp_mode ==
'client'` special-case that forces `overall_resolved = False`
when the device has neither a v4 nor a v6 lease yet. The
_check_individual_arp_resolution status message also gets a
new "DHCP client: waiting for lease" branch to disambiguate
from ARP-pending-with-a-configured-IP.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _widget_src():
    return (_REPO / "widgets" / "devices_tab.py").read_text()


# --- A. server-side MAC fallback ---

def test_A_server_marker_present():
    assert "v0.5.349 (audit auto-mac-server-fallback)" in _server_src()


def test_A_fallback_derives_from_device_id_md5():
    src = _server_src()
    idx = src.index("v0.5.349 (audit auto-mac-server-fallback)")
    body = src[idx:idx + 4000]
    assert "hashlib" in body
    assert "md5(str(device_id).encode())" in body


def test_A_fallback_uses_laa_prefix_02():
    src = _server_src()
    idx = src.index("v0.5.349 (audit auto-mac-server-fallback)")
    body = src[idx:idx + 4000]
    # The `02:` LAA prefix is prepended.
    assert '"02:"' in body


def test_A_fallback_only_fires_when_mac_empty():
    src = _server_src()
    idx = src.index("v0.5.349 (audit auto-mac-server-fallback)")
    body = src[idx:idx + 4000]
    assert "if not mac_address and device_id:" in body


def test_A_fallback_persists_generated_mac_to_db():
    """The generated MAC must land in the DB so subsequent
    /api/device/database/devices/<id> reads (and the widget's
    display) see it."""
    src = _server_src()
    idx = src.index("v0.5.349 (audit auto-mac-server-fallback)")
    body = src[idx:idx + 4000]
    assert 'device_db.update_device(device_id, {"mac_address": mac_address})' in body


# --- B. DHCP client status ---

def test_B_widget_marker_present():
    src = _widget_src()
    assert "v0.5.349 (audit dhcp-client-no-lease-shows-green)" in src


def test_B_guard_applied_at_all_three_v0_5_334_sites():
    """The v0.5.334 fix has THREE call sites in devices_tab.py.
    The new v0.5.349 guard must appear at all three to keep them
    aligned — otherwise the polling / sync / individual paths
    would report different status for the same device."""
    src = _widget_src()
    # Count occurrences of the guard's marker.
    n_hits = src.count("v0.5.349 (audit dhcp-client-no-lease-shows-green)")
    assert n_hits == 3, (
        f"expected v0.5.349 guard at all three v0.5.334 sites, got "
        f"{n_hits} instances"
    )


def test_B_guard_checks_dhcp_mode_and_leases():
    """The guard must read `dhcp_mode == 'client'` AND check for
    v4 lease (dhcp_lease_ip) OR v6 lease (dhcp_lease_ip6). Only
    when BOTH leases are empty do we force overall_resolved = False."""
    src = _widget_src()
    # Guard fragments — each site has them.
    assert src.count('device_data.get("dhcp_mode")') >= 3
    assert src.count('device_data.get("dhcp_lease_ip")') >= 3
    assert src.count('device_data.get("dhcp_lease_ip6")') >= 3


def test_B_status_message_disambiguates_no_lease_from_arp_pending():
    """The `_check_individual_arp_resolution` site adds a specific
    'DHCP client: waiting for lease' status message so operators
    don't confuse it with a device that has an IP but arp failed."""
    src = _widget_src()
    assert "DHCP client: waiting for lease" in src


def test_v0_5_334_markers_preserved():
    """v0.5.349 is stacked on v0.5.334 — the earlier markers must
    still be present since we're extending, not replacing, the
    v0.5.334 logic."""
    src = _widget_src()
    assert src.count("v0.5.334 (audit ipv4-required-on-ipv6-only)") >= 3


# --- cross-cutting ---

def test_v0_5_325_and_v0_5_348_markers_still_intact():
    """v0.5.325 (server-side MAC apply) and v0.5.348 (client-side
    auto-gen) must still exist. v0.5.349's server-side fallback
    is defense-in-depth for both, not a replacement."""
    server_src = _server_src()
    assert "v0.5.325" in server_src
    dialog_src = (_REPO / "widgets" / "add_device_dialog.py").read_text()
    assert "v0.5.348 (audit auto-mac-generation)" in dialog_src


def test_edited_files_ast_parse():
    import ast
    ast.parse(_server_src())
    ast.parse(_widget_src())
