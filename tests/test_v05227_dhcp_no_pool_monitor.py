"""v0.5.227 — DHCP monitor preserves "No Pool" state + client rejects
Save when server-mode is missing a pool range.

Bug driver (operator on srv06 2026-08-30): device3 was Applied as a
DHCP-server device but with pool_start / pool_end missing from
dhcp_config. Two things went wrong from there:

1. `start_dhcp_server` correctly refused to launch dnsmasq and wrote
   `dhcp_state="No Pool"` + an actionable `dhcp_last_error` message
   (v0.5.223 fix). Good.
2. The DHCP monitor polls every ~5 s and unconditionally wrote
   `dhcp_state="Server Running" if running else "Server Down"`
   (utils/dhcp_monitor.py:373 pre-fix). That clobbered "No Pool"
   with "Server Down" on the very next poll — the UI showed
   "Server Down" but the last_error tooltip still said "No pool
   attached", so operators saw a contradictory state and thought
   dnsmasq had crashed for real. Bad.

Plus the monitor then tried to auto-restart dnsmasq via
`ensure_dhcp_services`, which just re-hit the same no-pool refusal
on every poll — restart spam in the logs.

Root cause of the missing pool: the client's `validate_and_accept`
let a Server-mode + IPv4-enabled config through with blank pool
fields. Server would have caught it downstream, but the "No Pool"
state that resulted looked identical to real dnsmasq crashes on
first glance.

Three layers fixed:
- utils/dhcp_monitor.py `_check_server_device` — before writing
  "Server Down", check `_has_dhcp_pool(dhcp_config)`. If no pool,
  write "No Pool" + the same actionable last_error and RETURN
  (skip the futile ensure_dhcp_services restart).
- utils/dhcp_monitor.py new module-level `_has_dhcp_pool()` —
  the pool-presence check, shared with future callers.
- widgets/add_device_dialog.py `validate_and_accept` — refuse
  Save when DHCP mode is Server and no address family is enabled,
  or when an enabled AF has blank pool fields.
"""

import inspect

from utils import dhcp_monitor as monitor


# --- _has_dhcp_pool helper --------------------------------------------------

def test_has_pool_ipv4_only():
    cfg = {"pool_start": "172.16.30.10", "pool_end": "172.16.30.200"}
    assert monitor._has_dhcp_pool(cfg) is True


def test_has_pool_ipv6_only():
    cfg = {"pool6_start": "2001:db8::100", "pool6_end": "2001:db8::1ff"}
    assert monitor._has_dhcp_pool(cfg) is True


def test_has_pool_dual_stack():
    cfg = {
        "pool_start": "172.16.30.10", "pool_end": "172.16.30.200",
        "pool6_start": "2001:db8::100", "pool6_end": "2001:db8::1ff",
    }
    assert monitor._has_dhcp_pool(cfg) is True


def test_no_pool_empty_config():
    assert monitor._has_dhcp_pool({}) is False


def test_no_pool_missing_pool_start():
    """srv06 device3 shape: gateway + gateway_route + lease_time
    + mode, but NO pool_start / pool_end. dnsmasq refuses to launch."""
    cfg = {
        "gateway": "172.16.30.1",
        "gateway_route": ["172.16.30.0/24"],
        "interface": "vlan10",
        "ipv4_enabled": True,
        "ipv6_enabled": False,
        "lease_time": 3600,
        "mode": "server",
    }
    assert monitor._has_dhcp_pool(cfg) is False


def test_no_pool_only_pool_start_no_end():
    """Half-configured pool doesn't count — dnsmasq needs both."""
    cfg = {"pool_start": "172.16.30.10"}
    assert monitor._has_dhcp_pool(cfg) is False


def test_no_pool_whitespace_only_values():
    cfg = {"pool_start": "   ", "pool_end": "   "}
    assert monitor._has_dhcp_pool(cfg) is False


def test_no_pool_non_dict_input():
    """Server sometimes stores dhcp_config as an already-parsed dict
    but the raw DB row is JSON. Whichever the monitor sees, a
    non-dict returns False rather than crashing the loop."""
    assert monitor._has_dhcp_pool(None) is False
    assert monitor._has_dhcp_pool("not a dict") is False
    assert monitor._has_dhcp_pool([]) is False


# --- Monitor state-write branches -------------------------------------------

def _monitor_source() -> str:
    return inspect.getsource(monitor._DhcpMonitorImpl if hasattr(monitor, "_DhcpMonitorImpl") else monitor)


def test_monitor_writes_no_pool_when_config_empty():
    """The state-decision block at _check_server_device must have
    a "No Pool" branch driven by `has_pool`."""
    src = _monitor_source()
    assert 'has_pool = _has_dhcp_pool(dhcp_config)' in src
    assert 'new_state = "No Pool"' in src
    # And the tri-state ordering: running wins, then not-has-pool,
    # then Server Down.
    idx_running = src.index('new_state = "Server Running"')
    idx_nopool = src.index('new_state = "No Pool"')
    idx_down = src.index('new_state = "Server Down"')
    assert idx_running < idx_nopool < idx_down


def test_monitor_writes_actionable_last_error_on_no_pool():
    """Keep the last-error message in sync with what
    start_dhcp_server writes at Apply time, so the operator sees
    the same guidance whether the monitor or the Apply landed
    the state first."""
    src = _monitor_source()
    assert 'No DHCP pool attached' in src
    assert 'Attach Route Pools' in src


def test_monitor_skips_restart_when_no_pool():
    """ensure_dhcp_services on a pool-less config just re-hits the
    same refusal every 5 s — skip it entirely."""
    src = _monitor_source()
    # The early-return guard must live between the state-write
    # block and the ensure_dhcp_services call.
    idx_return = src.index('if not has_pool:')
    idx_ensure = src.index('ensure_dhcp_services(')
    assert idx_return < idx_ensure


# --- Client-side reject Save -----------------------------------------------

def test_client_rejects_server_mode_without_af():
    src = _dialog_source()
    assert 'DHCP Server: no address family' in src
    assert 'dnsmasq needs at least one pool to serve' in src


def test_client_rejects_server_mode_with_blank_ipv4_pool():
    src = _dialog_source()
    assert 'DHCP Server: pool range required' in src
    assert "pool_start / pool_end" not in src  # human-friendly copy
    assert '172.16.30.10 and 172.16.30.200' in src  # example matches
    # Preserve the invariant that we check both fields before allowing
    # Save: an empty start OR empty end is a refusal.
    assert 'pool_start and pool_end' in src


def test_client_rejects_server_mode_with_blank_ipv6_pool():
    src = _dialog_source()
    assert 'DHCP Server: IPv6 pool range required' in src


def _dialog_source() -> str:
    from pathlib import Path
    return (
        Path(__file__).resolve().parents[1]
        / "widgets" / "add_device_dialog.py"
    ).read_text()


# --- Version bump -----------------------------------------------------------

def test_pyproject_version_bumped():
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    assert 'version = "0.5.227"' in src
