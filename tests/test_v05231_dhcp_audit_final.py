"""v0.5.231 — DHCP audit bundle #3 (last 4 of 35).

Closes the final 4 findings from the 35-audit surveyed on 2026-08-30:
- U client-7 (named pools IPv6 parity)
- U client-11 (rescue path — Restart DHCP button + backend endpoint)
- U monitor-6 (client-mode "off" state string disambiguation)
- P server-12 (apply_device re-entrancy guard)

After this ship: 35/35 findings closed.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# --- P server-12: apply_device re-entrancy guard --------------------------

def test_p_server_12_per_device_apply_lock():
    src = _read("run_tgen_server.py")
    assert "_APPLY_LOCKS: Dict[str, _threading.Lock]" in src
    assert "def _get_apply_lock(device_id: str)" in src
    # Non-blocking acquire returns 409 on contention.
    assert "_apply_lock.acquire(blocking=False)" in src
    assert "still in flight" in src
    # finally: releases regardless of outcome.
    assert "_apply_lock.release()" in src


# --- U monitor-6: state hint helper + tooltip -----------------------------

def test_u_monitor_6_state_hints_defined():
    src = _read("utils/devices_tab_dhcp.py")
    assert "_DHCP_STATE_HINTS_CLIENT = {" in src
    assert "_DHCP_STATE_HINTS_SERVER = {" in src
    assert 'def _state_hint_tooltip(' in src
    # Both server + client "off" states covered.
    for key in ("stopped", "no lease", "requesting", "renewing", "leased"):
        assert f'"{key}":' in src
    for key in ("server running", "server down", "no pool", "disabled"):
        assert f'"{key}":' in src


def test_u_monitor_6_state_tooltip_wired_into_render():
    src = _read("utils/devices_tab_dhcp.py")
    assert "_state_hint_tooltip(_state_str, _mode)" in src


# --- U client-11: Restart DHCP endpoint + button --------------------------

def test_u_client_11_restart_endpoint_exists():
    src = _read("run_tgen_server.py")
    assert '@app.route("/api/device/dhcp/restart", methods=["POST"])' in src
    assert "def restart_dhcp_service():" in src
    assert 'ensure_dhcp_services(' in src


def test_u_client_11_restart_button_wired():
    src = _read("utils/devices_tab_dhcp.py")
    assert 'self.parent.dhcp_restart_button = _dhcp_btn(' in src
    assert '"Restart DHCP"' in src
    assert 'def restart_dhcp_service(self):' in src
    assert '/api/device/dhcp/restart' in src


def test_u_client_11_restart_endpoint_validates_input():
    src = _read("run_tgen_server.py")
    # Empty device_id → 400
    assert '"device_id is required"' in src
    # Missing device → 404
    assert '"Device not found"' in src
    # No DHCP mode → 400 with actionable text
    assert 'has no DHCP mode configured' in src


# --- U client-7: named pools IPv6 fields ---------------------------------

def test_u_client_7_pool_dialog_has_ipv6_fields():
    src = _read("utils/devices_tab_dhcp.py")
    assert "self.pool6_start_edit = QLineEdit(" in src
    assert "self.pool6_end_edit = QLineEdit(" in src
    assert "self.prefix6_edit = QLineEdit(" in src
    # And they land in the payload.
    assert '"pool6_start": self.pool6_start_edit.text().strip()' in src
    assert '"pool6_end": self.pool6_end_edit.text().strip()' in src
    assert '"prefix6": self.prefix6_edit.text().strip()' in src


def test_u_client_7_ipv6_validation_all_or_none():
    src = _read("utils/devices_tab_dhcp.py")
    # If any of the three is set, all three are required.
    assert "IPv6 pool needs Pool Start, Pool End, AND Prefix" in src
    assert "IPv6 Pool Start" in src and "Pool End" in src


def test_u_client_7_ipv6_pool_validates_addresses():
    src = _read("utils/devices_tab_dhcp.py")
    # Order enforced
    assert "IPv6 Pool Start (" in src and "must be ≤" in src
    # Prefix in 0..128
    assert "IPv6 Prefix must be between 0 and 128." in src


# --- Version bump --------------------------------------------------------

def test_pyproject_at_or_beyond_231():
    src = _read("pyproject.toml")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 231)
