"""v0.5.251 — API_GUIDE documents v0.5.240–v0.5.250 additions.

Docs-only ship. The 11 previous versions added many new fields
and response shapes but only v0.5.237's initial DHCP-section
rewrite touched the API_GUIDE. Anyone reading the guide since
would see a snapshot frozen at v0.5.231's semantics — no Force
Apply, no relay mode, no soft `restarted_pending_lease`, no
`family_errors`, no ensure lock, no orphan reaper, no 60 s
Remove-timeout guidance.

This ship folds all of that into the DHCP section (§5) so the
next operator reading the guide gets the right picture.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
GUIDE = (REPO / "API_GUIDE.md").read_text()


# --- v0.5.244: Restart endpoint's three response shapes -------------


def test_restart_endpoint_documents_pending_lease_soft_success():
    """v0.5.244's `restarted_pending_lease` HTTP-200 case must
    be documented — it's what most client-mode restarts hit when
    the DHCP server is slow, and the client dialog now renders
    it as a friendly info dialog instead of a scary error."""
    assert 'status: "restarted_pending_lease"' in GUIDE
    assert '"warning"' in GUIDE
    # Multi-line JSON string in the example so match on shorter tokens
    # that survive the line-break.
    assert 'DHCP monitor will keep' in GUIDE
    assert 'polling' in GUIDE


def test_restart_endpoint_documents_family_errors_field():
    """Both the soft-success and hard-failure branches include
    `family_errors: [...]` for per-address-family error strings."""
    assert '"family_errors"' in GUIDE
    assert 'ipv4: Lease timeout' in GUIDE


def test_restart_endpoint_documents_hard_failure_actual_reason():
    """v0.5.244 stops the opaque `"Restart failed"` fallback —
    hard failures now include the real error extracted from
    ensure_dhcp_services's nested ipv4/ipv6 subresults."""
    assert 'dnsmasq launch failed' in GUIDE
    assert 'no interface with matching address' in GUIDE


# --- v0.5.243: ensure_dhcp_services per-device lock -----------------


def test_ensure_lock_documented_alongside_apply_lock():
    """Two locks stack: _APPLY_LOCKS (v0.5.231) and _ENSURE_LOCKS
    (v0.5.243). The guide must call out both so ops teams don't
    get surprised by 45s waits or 409s."""
    assert '_APPLY_LOCKS' in GUIDE
    assert '_ENSURE_LOCKS' in GUIDE
    assert 'blocks up to 45s' in GUIDE


def test_ensure_lock_documented_with_root_cause():
    """The docstring explains WHY: two dhclients bound to the
    same raw AF_PACKET socket, neither transmitting."""
    assert 'two dhclients' in GUIDE
    assert 'raw AF_PACKET socket' in GUIDE


# --- v0.5.242: Force Apply -----------------------------------------


def test_force_apply_documented():
    assert 'force: true' in GUIDE or '`force`' in GUIDE
    assert 'force_supported' in GUIDE
    assert 'conflicting_device_id' in GUIDE
    assert 'conflicting_device_name' in GUIDE


def test_force_apply_code_field_values_listed():
    """The 5 possible `code` values that trigger a Force Apply
    prompt on the client."""
    for _code in (
        'duplicate_iface_vlan',
        'duplicate_loopback_ipv4',
        'duplicate_loopback_ipv6',
        'duplicate_ipv4_address',
        'duplicate_ipv6_address',
    ):
        assert _code in GUIDE, f"code={_code} not documented"


def test_apply_body_lists_force_optional_field():
    """The /api/device/apply Body section must call out `force`
    so someone reading only the compact endpoint reference sees it."""
    idx = GUIDE.find("**Apply device (POST `/api/device/apply`):**")
    body = GUIDE[idx:idx + 2000]
    assert '`force: true`' in body
    assert 'v0.5.242' in body


# --- v0.5.241: Remove hang fix + client timeout guidance ------------


def test_remove_hang_root_cause_documented():
    """v0.5.241 fixed container.exec_run timeout; operators
    running older client code need to know to bump to 60 s."""
    assert 'container.exec_run()' in GUIDE
    assert '60 s HTTP timeout' in GUIDE
    assert 'threading.Thread' in GUIDE


# --- v0.5.245: DHCP relay mode -------------------------------------


def test_relay_return_hop_field_in_pool_payload():
    """The pool schema must show `relay_return_hop` as the new
    optional field so anyone building a pool via API knows how
    to enable relay mode."""
    assert '"relay_return_hop"' in GUIDE
    assert 'DHCP relay mode' in GUIDE


def test_relay_mode_explains_anchor_skip_and_route_via_relay():
    """The three-point explanation (skip anchor, install
    via-relay route, keep client-gateway from `gateway`) must
    all appear, so relay-mode config isn't a black box."""
    assert 'Skips' in GUIDE and 'anchor' in GUIDE
    assert 'via <relay_return_hop>' in GUIDE
    assert 'dhcp-option=3' in GUIDE


def test_relay_mode_notes_v0_5_246_netmask():
    """v0.5.246's mandatory netmask on the dhcp-range line —
    critical for relay-mode pools where the subnet isn't on
    any local interface."""
    assert 'v0.5.246' in GUIDE
    assert 'netmask' in GUIDE


def test_attach_pools_accepts_relay_return_hop_override():
    """v0.5.245's `relay_return_hop` override at attach time
    (supersedes pool's default). Also per-additional-pool
    override for mixed setups."""
    assert 'relay_return_hop' in GUIDE
    # The attach-pools note about override lives in the pool
    # catalog section.
    assert 'override' in GUIDE.lower()


# --- v0.5.247: Orphan-container reaper -----------------------------


def test_orphan_reaper_documented():
    assert 'reap_orphan_dhcp_containers' in GUIDE
    assert 'orphans_reaped' in GUIDE
    assert 'orphan_names' in GUIDE


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 251)
