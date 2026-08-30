"""v0.5.223: DHCP server writes ``dhcp_state="No Pool"``
instead of ``"Failed"`` when the device has no pool attached.

Operator report on JNPR-MAC-HWXVX1 2026-08-25: DHCP server
device showed ``dhcp_state="Failed"`` with an empty Pools
column. Turned out the Delete-key shortcut on the DHCP subtab
(v0.5.218 fix I) had detached all pools while leaving
``dhcp_mode="server"`` intact. Next Apply hit
``start_dhcp_server``'s "no ipv4/ipv6 pool" branch which
wrote ``dhcp_state="Failed"`` — indistinguishable from an
actual dnsmasq launch failure.

Fix: write ``dhcp_state="No Pool"`` in that branch with a
helpful last_error message pointing operators to Attach Route
Pools. Real dnsmasq failures still write ``"Failed"``.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05223_test_{os.getpid()}.db"),
)


def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def test_no_pool_branch_writes_no_pool_not_failed():
    src = _dhcp_src()
    # Find the branch that checks `not ipv4_enabled and not ipv6_enabled`
    marker = "if not ipv4_enabled and not ipv6_enabled:"
    idx = src.find(marker)
    assert idx >= 0, "no-pool guard branch moved"
    body = src[idx:idx + 2000]
    assert '"dhcp_state": "No Pool"' in body, (
        "no-pool branch no longer writes state='No Pool' — operators "
        "can't tell config-incomplete apart from real dnsmasq crashes"
    )
    assert '"dhcp_state": "Failed"' not in body, (
        "no-pool branch is still writing 'Failed' — the point of "
        "v0.5.223 was to stop conflating the two"
    )


def test_no_pool_last_error_message_actionable():
    """The last_error message should point operators to the actual
    toolbar button ('Attach Pool' since v0.5.228, previously worded
    as 'Attach Route Pools' in v0.5.223) — not just say 'no pool'."""
    src = _dhcp_src()
    idx = src.find("if not ipv4_enabled and not ipv6_enabled:")
    body = src[idx:idx + 2000]
    lowered = body.lower()
    assert (
        "'attach pool'" in lowered
        or "attach route pools" in lowered
        or "attach a pool" in lowered
    ), "no-pool last_error doesn't tell operators HOW to fix it"


def test_real_dnsmasq_failure_still_writes_failed():
    """Regression guard: the actual dnsmasq launch-failure
    branch must still write 'Failed', not silently upgrade to
    'No Pool' too."""
    src = _dhcp_src()
    # Find the dnsmasq launch returncode!=0 block
    marker = 'logger.error("[DHCP] dnsmasq failed:'
    idx = src.find(marker)
    assert idx >= 0, "dnsmasq launch-failure marker moved"
    body = src[idx:idx + 1500]
    assert '"dhcp_state": "Failed"' in body, (
        "real dnsmasq crash no longer writes 'Failed' — regression"
    )


def test_config_write_failure_still_writes_failed():
    src = _dhcp_src()
    marker = 'logger.error("[DHCP] Failed to write dnsmasq config'
    idx = src.find(marker)
    assert idx >= 0
    body = src[idx:idx + 1200]
    assert '"dhcp_state": "Failed"' in body, (
        "config write failure no longer writes 'Failed' — regression"
    )


def test_interface_missing_still_writes_failed():
    src = _dhcp_src()
    marker = 'Interface {interface} not found in container/host. Cannot start DHCP server'
    idx = src.find(marker)
    assert idx >= 0
    body = src[idx:idx + 1500]
    assert '"dhcp_state": "Failed"' in body, (
        "interface-missing error no longer writes 'Failed' — regression"
    )
