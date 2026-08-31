"""v0.5.230 — DHCP audit bundle #2 (12 more findings from the 35-audit).

Continues from v0.5.229 (15 findings). This ship closes 12 more:
6 monitor + server paper-cuts, 4 client-UI user-visible, 2 server
paper-cuts. Remaining 4 findings (named-pool IPv6 parity, rescue-
path buttons, state-string normalization, apply re-entrancy guard)
are heavier lifts queued for v0.5.231.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# --- P server-9: /31 /32 pool guard ----------------------------------------

def test_p_server_9_tiny_pool_guarded():
    src = _read("utils/dhcp.py")
    assert "prefixlen == 32" in src
    assert "prefixlen == 31" in src
    assert "Pool network %s has no usable host" in src


# --- P server-10: IPv6 auto-derive ----------------------------------------

def test_p_server_10_ipv6_auto_derive():
    src = _read("utils/dhcp.py")
    assert "Derived IPv6 server IP" in src
    assert "IPv6Network(" in src and "ipv6_pool_start" in src


# --- P server-11: fe80 mask uses is_link_local ----------------------------

def test_p_server_11_link_local_uses_full_range():
    src = _read("utils/dhcp.py")
    assert "is_link_local" in src
    # And the old startswith check is gone (near _flush_ipv6).
    idx = src.find("def _flush_ipv6")
    body = src[idx:idx + 2000]
    assert 'ip.startswith("fe80:")' not in body


# --- P monitor-7: backoff decay preserves count ---------------------------

def test_p_monitor_7_backoff_decays_not_zeros():
    src = _read("utils/dhcp_monitor.py")
    assert "_decayed = max(0, int(state.get(\"count\") or 0) // 2)" in src


# --- P monitor-8: prune restart-attempts dict ----------------------------

def test_p_monitor_8_prunes_orphan_devices():
    src = _read("utils/dhcp_monitor.py")
    assert "_live_ids = {d.get(\"device_id\") for d in devices}" in src
    assert "Pruned %d orphan restart-attempt entries" in src


# --- P monitor-10: post-restart lease detection matches pre --------------

def test_p_monitor_10_post_restart_check_matches_pre():
    src = _read("utils/dhcp_monitor.py")
    # Now requires both state=Leased AND dhcp_running.
    assert 'refreshed.get("dhcp_state") == "Leased"\n                                        and refreshed.get("dhcp_running")' in src


# --- P monitor-11: recovery transition recorded --------------------------

def test_p_monitor_11_recovery_transition_recorded():
    src = _read("utils/dhcp_monitor.py")
    assert "restart_recovery" in src
    assert 'add_state_transition(\n                                                device_id, "dhcp", "Leased"' in src


# --- U client-5: mode change honors IPv4 sub-checkbox --------------------

def test_u_client_5_mode_change_honors_af_checkbox():
    src = _read("widgets/add_device_dialog.py")
    assert "_ipv4_af_on = (" in src
    assert "dhcp_ipv4_enabled_checkbox.isChecked()" in src
    # AND it feeds into enable_server_fields
    assert "and _ipv4_af_on" in src


# --- U client-8: DHCPPoolDialog gateway validation -----------------------

def test_u_client_8_pool_dialog_validates_gateway():
    src = _read("utils/devices_tab_dhcp.py")
    assert "Invalid gateway address" in src


# --- U client-9: subtab shows IPv6 default_pool --------------------------

def test_u_client_9_subtab_renders_ipv6_default_pool():
    srv = _read("run_tgen_server.py")
    assert 'pool6_start = dhcp_cfg.get("ipv6_pool_start")' in srv
    assert '"pool6_range"' in srv
    ui = _read("utils/devices_tab_dhcp.py")
    assert 'pool6_range = default_pool.get("pool6_range")' in ui
    assert "(v6 default)" in ui


# --- U client-10: Attach gateway override pre-fill + clear ---------------

def test_u_client_10_attach_gateway_override_prefill_and_clear():
    src = _read("utils/devices_tab_dhcp.py")
    assert "self.gateway_override_edit.setText(_current_gw)" in src
    assert "_clear_btn = QPushButton(\"Clear\")" in src
    # And the caller passes the device dict.
    assert "device=metadata.get(\"entry\") or device_data" in src


# --- P client-12: refresh errors surface via status bar ------------------

def test_p_client_12_refresh_error_surfaced():
    src = _read("utils/devices_tab_dhcp.py")
    assert "DHCP status refresh failed:" in src
    assert "_bar.showMessage(_msg, 5000)" in src


# --- Version bump --------------------------------------------------------

def test_pyproject_at_or_beyond_230():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 230)
