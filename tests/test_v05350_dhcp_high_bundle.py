"""v0.5.350 — six HIGH-priority DHCP fixes bundled from the
post-cadence audit.

## Fixes

**A. Monitor mis-labels v6-only server as "No Pool"** — v6.
`utils/dhcp_monitor.py::_has_dhcp_pool` only checked
`pool6_start`/`pool6_end`. Devices persist v6 pool fields under
`ipv6_pool_start`/`ipv6_pool_end`. v6-only server bounced to
"No Pool" every tick + auto-restart suppressed.

**B. Client→Server transition swallows TypeError** — both.
`utils/dhcp.py:5279-5282` passed `dhcp_config=` kwarg not in
`stop_dhcp_client`'s signature. TypeError raised, swallowed by
outer except, client never stopped, server started on top of live
client → collision (the exact v0.5.229 audit-U-server-6 bug).

**C. Attach v6 pool → no v6 range in dnsmasq** — v6.
`run_tgen_server.py::attach_dhcp_pools` copied only v4 fields from
the named pool into the device config. v6-only attached pools
silently produced dnsmasq without a v6 range.

**D. AttachDHCPPoolsDialog is v4-only** — v6.
`utils/devices_tab_dhcp.py::AttachDHCPPoolsDialog` had 9 v4-focused
columns. v6-only pools showed Name + blank cells; operator
couldn't tell what they were attaching. Now 12 columns with
explicit "IPv6 Pool Start/End/Prefix".

**E. Dual-stack client hides one-family failure behind green** —
both. `utils/dhcp.py::start_dhcp_client` did `success = v4 OR v6`
and wrote `dhcp_state=Leased` with no `dhcp_last_error` for the
failed family. Now emits `Leased (partial)` and surfaces the
failed family's error message.

**F. Delete-pool has no in-use check** — both.
`run_tgen_server.py::delete_dhcp_pool` DELETE-d unconditionally.
Now returns 409 with the list of using devices unless `?force=true`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def _monitor_src():
    return (_REPO / "utils" / "dhcp_monitor.py").read_text()


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _tab_src():
    return (_REPO / "utils" / "devices_tab_dhcp.py").read_text()


# --- A. Monitor v6-only pool detection ---

def test_A_marker_present():
    assert "v0.5.350 (audit v6-only-server-mislabeled-no-pool)" in _monitor_src()


def test_A_has_dhcp_pool_reads_both_key_shapes():
    src = _monitor_src()
    fn_idx = src.index("def _has_dhcp_pool(")
    body = src[fn_idx:fn_idx + 2500]
    # BOTH v6 key spellings.
    assert 'dhcp_config.get("pool6_start")' in body
    assert 'dhcp_config.get("pool6_end")' in body
    assert 'dhcp_config.get("ipv6_pool_start")' in body
    assert 'dhcp_config.get("ipv6_pool_end")' in body


# --- B. Client→Server transition kwarg fix ---

def test_B_marker_present():
    assert "v0.5.350 (audit stop-client-typeerror-swallowed)" in _dhcp_src()


def test_B_phantom_kwarg_removed():
    """The broken `dhcp_config=` kwarg on `stop_dhcp_client` inside
    the mode-transition block must be gone. Regression guard: the
    call site must NOT pass a kwarg the function signature can't
    accept."""
    src = _dhcp_src()
    idx = src.index("v0.5.350 (audit stop-client-typeerror-swallowed)")
    body = src[idx:idx + 2000]
    # New shape: only `container=` kwarg (matches signature).
    assert "stop_dhcp_client(device_db, device_id, interface" in body
    assert "container=_ensure_dhcp_container(device_id, mode=\"client\"))" in body
    # No more `dhcp_config=_prev.get(...)` on the call:
    assert "dhcp_config=_prev.get" not in body


# --- C. Attach-pool v6 field copy ---

def test_C_marker_present():
    assert "v0.5.350 (audit v6-pool-attach-not-propagated)" in _server_src()


def test_C_attach_copies_v6_fields_to_ipv6_keys():
    src = _server_src()
    idx = src.index("v0.5.350 (audit v6-pool-attach-not-propagated)")
    body = src[idx:idx + 3000]
    # Assigns to the keys start_dhcp_server READS from.
    assert 'dhcp_cfg["ipv6_pool_start"] = primary_pool.get("pool6_start")' in body
    assert 'dhcp_cfg["ipv6_pool_end"] = primary_pool.get("pool6_end")' in body
    assert 'dhcp_cfg["ipv6_prefix"] = primary_pool.get("prefix6")' in body


def test_C_attach_flips_ipv6_enabled_to_match():
    src = _server_src()
    idx = src.index("v0.5.350 (audit v6-pool-attach-not-propagated)")
    body = src[idx:idx + 3000]
    # Flag is set based on whether v6 pool was populated.
    assert 'dhcp_cfg["ipv6_enabled"] = bool(' in body
    assert 'dhcp_cfg["ipv4_enabled"] = bool(' in body


# --- D. AttachDHCPPoolsDialog v6 columns ---

def test_D_marker_present():
    assert "v0.5.350 (audit attach-pool-dialog-v4-only)" in _tab_src()


def test_D_table_has_12_columns_with_v6_labels():
    src = _tab_src()
    idx = src.index("v0.5.350 (audit attach-pool-dialog-v4-only)")
    body = src[idx:idx + 3000]
    # Column count grew from 9 → 12.
    assert "QTableWidget(0, 12)" in body
    # v6 header labels present.
    assert '"IPv6 Pool Start"' in body
    assert '"IPv6 Pool End"' in body
    assert '"IPv6 Prefix"' in body


def test_D_populate_reads_v6_pool_fields():
    src = _tab_src()
    # Populate builds the display row from the pool dict.
    # After the fix the row now includes pool6_start / pool6_end / prefix6.
    assert 'pool.get("pool6_start", "")' in src
    assert 'pool.get("pool6_end", "")' in src
    assert 'pool.get("prefix6", "")' in src


# --- E. Dual-stack partial success ---

def test_E_marker_present():
    assert "v0.5.350 (audit dual-stack-partial-success-hidden)" in _dhcp_src()


def test_E_state_distinguishes_partial_from_full_leased():
    src = _dhcp_src()
    idx = src.index("v0.5.350 (audit dual-stack-partial-success-hidden)")
    body = src[idx:idx + 4000]
    assert '"Leased (partial)"' in body
    assert '"Leased"' in body


def test_E_surfaces_failed_family_to_last_error():
    src = _dhcp_src()
    idx = src.index("v0.5.350 (audit dual-stack-partial-success-hidden)")
    body = src[idx:idx + 4000]
    # Failed-message list feeds dhcp_last_error.
    assert "_failed_msgs" in body
    assert '"dhcp_last_error"' in body
    # And the both-families-succeed path CLEARS dhcp_last_error
    # (so a stale tooltip doesn't linger post-recovery).
    assert '_db_update["dhcp_last_error"] = ""' in body


# --- F. Delete-pool in-use guard ---

def test_F_marker_present():
    assert "v0.5.350 (audit delete-pool-no-in-use-check)" in _server_src()


def test_F_delete_endpoint_returns_409_with_users():
    src = _server_src()
    idx = src.index("v0.5.350 (audit delete-pool-no-in-use-check)")
    body = src[idx:idx + 3000]
    assert 'SELECT device_id, is_primary FROM device_dhcp_pools' in body
    # Returns 409 when in use.
    assert "), 409" in body
    # And exposes the users list to the caller.
    assert '"in_use_by": _in_use' in body


def test_F_force_query_param_bypasses_guard():
    src = _server_src()
    idx = src.index("v0.5.350 (audit delete-pool-no-in-use-check)")
    body = src[idx:idx + 3000]
    assert 'request.args.get("force"' in body
    assert 'if not _force:' in body


# --- cross-cutting ---

def test_all_files_ast_parse():
    import ast
    ast.parse(_dhcp_src())
    ast.parse(_monitor_src())
    ast.parse(_server_src())
    ast.parse(_tab_src())


def test_prior_markers_all_still_intact():
    """v0.5.349 (server MAC fallback + dhcp-client-no-lease
    green), v0.5.348 (client-side MAC auto-gen), v0.5.346 (accept_ra=2)
    must all survive."""
    assert "v0.5.349 (audit auto-mac-server-fallback)" in _server_src()
    assert "v0.5.349 (audit dhcp-client-no-lease-shows-green)" in (
        _REPO / "widgets" / "devices_tab.py"
    ).read_text()
    assert "v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)" in _dhcp_src()
