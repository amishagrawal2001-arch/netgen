"""v0.5.229 — DHCP audit bundle (5 blockers + 10 user-visible fixes).

Comprehensive audit ordered from three parallel Explore agents surveying
server / client-UI / monitor+persistence surfaces on 2026-08-30. This
release addresses 15 of the 35 findings; the remainder are queued for
v0.5.230.

Test strategy: pin the SHAPE of each fix in code so a future refactor
that regresses one gets caught. Full runtime tests would require a
srv06 container harness — the ship-verify step upgrades srv06 to this
version and exercises the fixes there instead.
"""

import inspect
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# --- B1: Monitor restart storm on legitimate "Requesting" -----------------

def test_b1_monitor_skips_restart_when_mid_handshake():
    """dhclient in Requesting / Renewing / Rebinding is legitimately
    running DORA — restarting it every 60s made slow-relay networks
    unable to lease. Pre-fix `needs_restart = True` was unconditional."""
    src = _read("utils/dhcp_monitor.py")
    assert '_mid_handshake = _state in ("Requesting", "Renewing", "Rebinding")' in src
    assert "if _running and _mid_handshake:" in src


# --- B2: Pidfile suffix mismatch on v4 dhclient release -------------------

def test_b2_v4_release_tries_both_pidfile_shapes():
    """start writes `dhclient-{iface}-ipv4.pid`; the release used
    `dhclient-{iface}.pid` (no suffix). Silent lease-release failure."""
    src = _read("utils/dhcp.py")
    assert "pidfile_v4_suffixed = os.path.join" in src
    assert "pidfile_v4_legacy" in src
    assert "for _pf in (pidfile_v4_suffixed, pidfile_v4_legacy):" in src


# --- B3: Client IPv4 pool validation (parity with IPv6) --------------------

def test_b3_client_v4_pool_validation():
    src = _read("widgets/add_device_dialog.py")
    # Pool inversion rejection
    assert "must be ≤" in src or "must be <=" in src
    # Malformed CIDR rejection
    assert "Gateway Route" in src and "is not a" in src
    # Lease time as int
    assert "positive integer between 60 and 4294967295" in src


# --- B4 + B5: Merge semantics + empty-scalar propagation ------------------

def test_b4_merge_replaces_not_unions():
    """gateway_route and pool_names.additional REPLACE, not UNION.
    Without this, operator can't remove attachments via Edit."""
    src = _read("widgets/devices_tab.py")
    assert 'v0.5.229 (audit B4)' in src
    assert 'dialog value REPLACES stored value' in src
    # And the union-based _merge_gateway_routes call for gateway_route
    # is gone from the pool-name merge block.
    assert 'self._merge_gateway_routes(\n                    merged.get("gateway_route"), value\n                )' not in src


def test_b5_dialog_emits_empty_scalars():
    """DHCP dialog now writes even empty scalar values so
    _merge_dhcp_configs sees them as explicit clears."""
    src = _read("widgets/add_device_dialog.py")
    # Old `if pool_start: dhcp_config["pool_start"] = pool_start` should be gone
    assert 'if pool_start:\n                        dhcp_config["pool_start"]' not in src
    # New unconditional form
    assert 'dhcp_config["pool_start"] = pool_start' in src
    assert 'dhcp_config["ipv6_pool_start"] = pool6_start' in src


# --- U server-2: NameError in stop_dhcp_server -----------------------------

def test_u_server_2_prev_init_before_try():
    src = _read("utils/dhcp.py")
    # `device` and `dhcp_cfg` must be initialized BEFORE the try block.
    idx_init = src.find("device = None\n    dhcp_cfg: Dict = {}")
    idx_try = src.find("try:\n        device = device_db.get_device")
    assert idx_init != -1 and idx_try != -1 and idx_init < idx_try


# --- U server-3: IPv6 RA router lifetime ----------------------------------

def test_u_server_3_ra_lifetime_not_zero_by_default():
    """Pre-fix, unconditional `ra-param={iface},0,0` told clients
    NOT to use this box as default router."""
    src = _read("utils/dhcp.py")
    assert 'if _v6_gw == "none":' in src
    assert 'RFC 4861' in src


# --- U server-4: Per-pool lease/gateway propagated -------------------------

def test_u_server_4_additional_pool_uses_own_lease_and_gateway():
    src = _read("utils/dhcp.py")
    assert 'extra_lease = pool.get("lease_time") or lease_seconds' in src
    assert 'extra_gw = pool.get("gateway")' in src
    assert 'dhcp-option=tag:' in src  # per-pool scoped gateway advertisement


# --- U server-5: Apply-without-DHCP-payload doesn't resurrect --------------

def test_u_server_5_stored_config_only_when_dhcp_still_wanted():
    src = _read("run_tgen_server.py")
    assert '_dhcp_still_wanted = "DHCP" in (protocols or [])' in src
    assert 'if dhcp_config_empty and _dhcp_still_wanted:' in src


# --- U server-6: mode transition stops other daemons ----------------------

def test_u_server_6_mode_transition_stops_previous():
    src = _read("utils/dhcp.py")
    assert 'v0.5.229 (audit U server-6)' in src
    assert '_prev_mode and _prev_mode != mode' in src
    assert 'stop_dhcp_server' in src and 'stop_dhcp_client' in src


# --- U server-7: /api/device/dhcp/server/pool validation -------------------

def test_u_server_7_pool_endpoint_validates_input():
    src = _read("run_tgen_server.py")
    assert 'Invalid IPv4 pool address' in src
    assert 'must be ≤ pool_end' in src or 'must be <= pool_end' in src
    assert 'Invalid gateway address' in src
    assert 'Invalid gateway_route CIDR' in src


# --- U server-8: elif-clear branch actually stops daemons -----------------

def test_u_server_8_elif_clear_stops_daemons():
    src = _read("run_tgen_server.py")
    assert 'v0.5.229 (audit U server-8): actually STOP' in src
    # AND clears dhcp_last_error alongside the other lease fields.
    assert 'update_data["dhcp_last_error"] = ""' in src


# --- U monitor-2: dhcp_last_error cleared on Server Running --------------

def test_u_monitor_2_clears_last_error_on_recovery():
    src = _read("utils/dhcp_monitor.py")
    assert 'elif new_state == "Server Running":' in src
    assert 'update_payload["dhcp_last_error"] = ""' in src


# --- U monitor-3: server-mode transitions recorded to history -------------

def test_u_monitor_3_server_transitions_recorded():
    src = _read("utils/dhcp_monitor.py")
    idx = src.find("Server-mode probe for %s")
    body = src[idx:idx + 800]
    assert 'add_state_transition' in body
    assert '"dhcp"' in body  # protocol key
    assert 'v0.5.229 (audit U monitor-3)' in src


# --- U monitor-4: "Server Running" bucketed as UP on topology ------------

def test_u_monitor_4_server_running_in_up_states():
    src = _read("widgets/topology_tab.py")
    assert '"server running"' in src


# --- U monitor-5: Skip status="Stopped" devices --------------------------

def test_u_monitor_5_stopped_devices_skipped():
    src = _read("utils/dhcp_monitor.py")
    assert '_status = str(device.get("status") or "").lower()' in src
    assert 'if _status == "stopped":' in src


# --- U monitor-9: fresh-DB CREATE TABLE has DHCP columns -----------------

def test_u_monitor_9_fresh_db_has_all_dhcp_columns():
    src = _read("utils/device_database.py")
    idx = src.find("CREATE TABLE IF NOT EXISTS devices")
    body = src[idx:src.find(")", idx + 500) + 1500]
    for col in (
        "dhcp_lease_subnet",
        "dhcp_manual_override",
        "dhcp_manual_override_time",
        "dhcp_last_error",
    ):
        assert col in body, f"CREATE TABLE missing {col}"


# --- U client-6: _dhcp_extra_cleanup scrubs stale lease state ------------

def test_u_client_6_dhcp_cleanup_scrubs_all_stale():
    src = _read("widgets/devices_tab.py")
    idx = src.find("def _dhcp_extra_cleanup():")
    body = src[idx:idx + 1500]
    for key in (
        "dhcp_state", "dhcp_lease_ip", "dhcp_lease_gateway",
        "dhcp_last_error", "pool_names", "default_pool",
    ):
        assert f'"{key}"' in body, f"cleanup misses {key}"


# --- U client-13: DHCP subtab columns render per-mode --------------------

def test_u_client_13_columns_render_per_mode():
    src = _read("utils/devices_tab_dhcp.py")
    assert 'if _mode == "server":' in src
    assert '_served_gw = _dhcp_cfg.get("gateway")' in src
    # And the server sends dhcp_config on rows.
    srv = _read("run_tgen_server.py")
    assert '"dhcp_config": dhcp_cfg,' in srv
    assert '"server_interface_ip": device.get("ipv4_address")' in srv


# --- Version bump --------------------------------------------------------

def test_pyproject_at_or_beyond_229():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 229)
