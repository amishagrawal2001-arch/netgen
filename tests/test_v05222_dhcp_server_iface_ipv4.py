"""v0.5.222: DHCP server assigns an IPv4 to its interface
before launching dnsmasq, and surfaces launch failures into
the UI via a new ``dhcp_last_error`` DB column.

Operator report on JNPR-MAC-HWXVX1 2026-08-24 (after
v0.5.221's interface-name fix): DHCP-server device on
``vlan200`` moved from ``Server Down`` to ``Failed``. Pre-fix
``start_dhcp_server`` had ``_ensure_ipv6_address`` for the v6
side but nothing for v4 — if the operator didn't set an
``IPv4`` on the device in Add Device (only the pool + gateway
in the DHCP wizard), the VLAN sub-interface came up bare and
dnsmasq's launch failed with "no interface with matching
address ..." because ``bind-interfaces`` + ``dhcp-range``
require a matching address on the interface. The dnsmasq
stderr landed only in netgen-server logs; the UI showed
``dhcp_state="Failed"`` with no hint why.

Fix:
- New ``_ensure_ipv4_address`` helper mirrors the v6 side.
  Called from ``start_dhcp_server`` before dnsmasq launches.
  Prefers the operator's gateway when it fits the pool subnet;
  otherwise assigns ``.1`` of the pool network. Idempotent —
  skips if the interface already has an IPv4 in the subnet.
- New ``_iface_has_ipv4_in_subnet`` helper.
- New ``dhcp_last_error`` DB column, populated from the
  actual dnsmasq stderr / config-write error / interface-
  missing error on every Failed return, cleared on
  ``Server Running``. Surfaced in the ``/api/device/dhcp/
  status`` response as ``last_error`` and attached as the
  State cell's tooltip in the DHCP subtab.
"""
from __future__ import annotations

import ipaddress
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05222_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# Helper: pick an IPv4 for the interface (runtime)
# ─────────────────────────────────────────────────────────────────────

def test_ensure_ipv4_prefers_gateway_when_in_pool():
    """If operator provided gateway and it fits in the pool
    subnet, use it — that's the dnsmasq default GW anyway."""
    from utils import dhcp as m
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with patch.object(m, "_run_command", mock_run):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan200", "192.168.30.10", "192.168.30.200",
                gateway="192.168.30.1", container=None,
            )
    assert picked == "192.168.30.1", (
        "did not pick the operator-provided gateway even though it "
        "falls in the pool subnet"
    )


def test_ensure_ipv4_derives_dot1_when_no_gateway():
    """No gateway → use .1 of the pool subnet."""
    from utils import dhcp as m
    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with patch.object(m, "_run_command", mock_run):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan200", "192.168.30.10", "192.168.30.200",
                gateway="", container=None,
            )
    assert picked is not None
    assert ipaddress.IPv4Address(picked) in ipaddress.IPv4Network("192.168.30.0/24")


def test_ensure_ipv4_idempotent_when_already_assigned():
    """No-op when the interface already has an IPv4 in the subnet."""
    from utils import dhcp as m
    with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=True):
        picked = m._ensure_ipv4_address(
            "vlan200", "192.168.30.10", "192.168.30.200",
            gateway="192.168.30.1", container=None,
        )
    assert picked is None, (
        "helper returned a value even though the interface already had "
        "a usable IPv4 — indicates it would issue a redundant ip addr add"
    )


def test_ensure_ipv4_gateway_outside_pool_falls_back():
    """If the provided gateway is OUTSIDE the pool's subnet
    (misconfiguration), fall back to derived .1 rather than
    assigning a bogus address."""
    from utils import dhcp as m
    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with patch.object(m, "_run_command", mock_run):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan200", "192.168.30.10", "192.168.30.200",
                gateway="10.0.0.1", container=None,  # outside 192.168.30/24
            )
    assert picked in {"192.168.30.1"}, (
        f"expected .1 of pool subnet, got {picked!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-ins
# ─────────────────────────────────────────────────────────────────────

def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def _run_tgen() -> str:
    return (REPO / "run_tgen_server.py").read_text()


def _db_src() -> str:
    return (REPO / "utils" / "device_database.py").read_text()


def _dhcp_widget_src() -> str:
    return (REPO / "utils" / "devices_tab_dhcp.py").read_text()


def test_start_dhcp_server_calls_ensure_ipv4():
    src = _dhcp_src()
    idx = src.find("def start_dhcp_server(")
    assert idx >= 0
    body = src[idx:idx + 12000]
    assert "_ensure_ipv4_address(" in body, (
        "start_dhcp_server no longer calls _ensure_ipv4_address — "
        "operator's original 'Failed' bug is back for devices with "
        "no IPv4 on the interface"
    )


def test_dhcp_last_error_column_migration_exists():
    src = _db_src()
    assert "dhcp_last_error" in src, (
        "dhcp_last_error column migration missing from device_database.py"
    )
    assert "ADD COLUMN dhcp_last_error TEXT" in src


def test_dhcp_last_error_in_field_mapping():
    src = _db_src()
    assert "'dhcp_last_error': 'dhcp_last_error'" in src, (
        "dhcp_last_error missing from update_device's field_mapping — "
        "writes will silently drop the column"
    )


def test_failed_writes_populate_dhcp_last_error():
    """Every dhcp_state='Failed' write in start_dhcp_server
    must also set dhcp_last_error, otherwise the operator sees
    Failed with an empty error tooltip."""
    src = _dhcp_src()
    idx = src.find("def start_dhcp_server(")
    body = src[idx:idx + 20000]
    # count "dhcp_state": "Failed" occurrences in body
    fail_count = body.count('"dhcp_state": "Failed"')
    err_count = body.count('"dhcp_last_error"')
    assert fail_count > 0, "start_dhcp_server has no Failed writes (bug F reverted?)"
    assert err_count >= fail_count, (
        f"start_dhcp_server has {fail_count} Failed writes but only "
        f"{err_count} dhcp_last_error assignments — at least one "
        "Failed path silently drops the error message"
    )


def test_server_running_write_clears_dhcp_last_error():
    """On successful start, dhcp_last_error must be cleared —
    otherwise a stale error tooltip lingers after the operator
    fixes the config and re-applies."""
    src = _dhcp_src()
    idx = src.find('"dhcp_state": "Server Running"')
    assert idx >= 0
    window = src[idx:idx + 800]
    assert '"dhcp_last_error"' in window, (
        "Server Running write no longer clears dhcp_last_error — "
        "stale error tooltip persists after successful re-apply"
    )


def test_dhcp_status_endpoint_returns_last_error():
    src = _run_tgen()
    assert '"last_error": device.get("dhcp_last_error")' in src, (
        "/api/device/dhcp/status no longer includes last_error — "
        "the client widget can't surface the error to the operator"
    )


def test_client_widget_attaches_error_tooltip_to_state_cell():
    src = _dhcp_widget_src()
    # The tooltip attach must reference last_error and set it on
    # the State cell's item.
    assert 'entry.get("last_error"' in src, (
        "client widget doesn't read last_error from status entry"
    )
    # v0.5.231 (audit U monitor-6): the last_error tooltip attach
    # was refactored — last_error is now combined with the state-
    # disambiguation hint (from _state_hint_tooltip) into
    # _tooltip_parts and set together via `setToolTip("\n\n".join(...))`.
    # The invariant "last_error surfaces on the State cell" still
    # holds; verify via the intermediate variable.
    assert '_tooltip_parts.append(_last_err)' in src, (
        "client widget no longer feeds last_error into the State cell tooltip"
    )
    assert 'setToolTip("\\n\\n".join(_tooltip_parts))' in src, (
        "client widget dropped the combined-tooltip setToolTip call"
    )
