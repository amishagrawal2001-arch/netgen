"""v0.5.307 — actual root cause of the v0.5.306 "same problem" report.

Operator on srv06 upgraded to v0.5.306 (server-side defensive drop
in /api/device/apply that zeros `ipv4` when the payload declares
`dhcp_config.ipv6_enabled=True, ipv4_enabled=False, mode="server"`)
and re-applied the DHCPv6 server template — but 192.168.0.2/24
STILL landed on the vlan sub-interface.

Server logs confirmed:
  * dhcp_config.ipv4_enabled=False arrived correctly.
  * No `[DEVICE APPLY] Configured IPv4 address` line fired
    (the top-level /api/device/apply IPv4-add block did skip).
  * BUT `[FRR] Final loopback values: loopback_ipv4=192.168.0.2`
    fired from utils/frr_docker.py, and the interface got the
    IPv4 address anyway.

Root cause in `utils/frr_docker._configure_interfaces`
(two places, ~line 607 and ~line 799):

    else:
        if dhcp_mode == "client":
            ipv4_addr = ''
            ipv4_mask = ''
        else:
            ipv4_addr = '192.168.0.2'  # <-- widget-default fallback
            ipv4_mask = '24'

When `ipv4` is empty in device_config (which is exactly what
v0.5.306 forces for v6-only DHCP-server devices), the else
branch hardcoded the widget default `192.168.0.2/24`. FRR
vtysh then wrote `ip address 192.168.0.2/24` on the kernel
interface via the container. The v0.5.306 top-level drop was
correct but incomplete — a second injection site existed one
layer down.

v0.5.307 extends the "no IPv4 needed" branch to also cover
DHCP-server devices whose dhcp_config declares
ipv4_enabled=False, matching the client branch's shape. The
widget-default-as-fallback anti-pattern is preserved for all
non-DHCP paths (some legacy tests depend on it), but both
DHCP-family paths now correctly emit empty.
"""
from __future__ import annotations

from pathlib import Path

_FRR_PY = Path(__file__).resolve().parents[1] / "utils" / "frr_docker.py"


def _src() -> str:
    return _FRR_PY.read_text()


def test_marker_present_in_both_configure_blocks():
    src = _src()
    # v0.5.307 comment marker fires in both occurrences (two
    # near-duplicate _configure_interfaces blocks).
    assert src.count("v0.5.307 (audit v6-only-dhcp-server-leak") == 2


def test_v6_only_dhcp_server_gets_empty_ipv4_addr():
    """v0.5.308 widened the guard: any DHCP mode (client OR
    server) skips the 192.168.0.2 widget-default fallback and
    lands in the empty-string branch. The v0.5.307 shape
    (`dhcp_mode == "client" or (dhcp_mode == "server" and
    _v4_off_by_config)`) depended on dhcp_config being present
    in device_config — but 6 callsites in run_tgen_server inline-
    build container_device_config WITHOUT dhcp_config, so the
    guard silently missed and re-injected the widget default.
    The v0.5.308 fix broadened to `dhcp_mode in ("client",
    "server")` which needs only dhcp_mode (always present)."""
    src = _src()
    # v0.5.308 shape — appears at BOTH _configure_interfaces
    # blocks (main + startup).
    assert src.count('if dhcp_mode in ("client", "server"):') >= 2, (
        "v0.5.308 widened guard must apply in both "
        "_configure_interfaces blocks"
    )


def test_v6_only_dhcp_server_no_hardcoded_widget_default_reachable():
    """After v0.5.308, no code path in _configure_interfaces
    should assign ipv4_addr = '192.168.0.2' when dhcp_mode is
    'client' or 'server'. The hardcoded 192.168.0.2 must live
    ONLY in the else-branch that fires when dhcp is disabled."""
    src = _src()
    # find each 'if dhcp_mode in ("client", "server"):' block and
    # verify the immediate body does NOT contain 192.168.0.2 —
    # the widget default must be relegated to the else branch.
    idx = 0
    seen = 0
    while True:
        marker = 'if dhcp_mode in ("client", "server"):'
        i = src.find(marker, idx)
        if i < 0:
            break
        # Grab the next 200 chars — the if-body up to else:
        body = src[i:i + 400]
        # Split at else: — everything before is the true branch
        else_i = body.find("else:")
        true_branch = body[:else_i] if else_i > 0 else body
        assert "192.168.0.2" not in true_branch, (
            "192.168.0.2 leaked into the dhcp-mode true branch "
            "at offset {} — v0.5.308 fix regressed".format(i)
        )
        idx = i + 1
        seen += 1
    assert seen >= 2, "expected at least 2 dhcp_mode guards"


def test_widget_default_still_used_for_non_dhcp_devices():
    """We DON'T want to remove the 192.168.0.2 widget-default
    fallback entirely — legacy tests and non-DHCP paths still
    depend on it. Only the DHCP-server + v4-off case is
    diverted to empty."""
    src = _src()
    # The hardcoded fallback line still exists (for the non-
    # DHCP paths that fall through when dhcp_mode is neither
    # "client" nor "server-with-v4-off").
    assert "ipv4_addr = '192.168.0.2'" in src
    assert "ipv4_mask = '24'" in src


def test_v0308_widened_guard_does_not_need_ipv4_enabled_check():
    """v0.5.307 tried to check `_dc.get("ipv4_enabled") is False`
    to distinguish IPv6-only DHCP-server from IPv4 DHCP-server.
    v0.5.308 discovered that `dhcp_config` isn't propagated to
    every callsite (6 inline-built container_device_config
    callsites in run_tgen_server), so the check silently failed.
    The v0.5.308 fix widened the guard to depend only on
    `dhcp_mode` (always present) — a DHCP-server device with
    ipv4_enabled=True now also skips the widget default and gets
    the anchor written by utils/dhcp._ensure_ipv4_address (v0.5.222)
    or via the DHCP lease. This test PINS that we no longer
    depend on the fragile ipv4_enabled propagation."""
    src = _src()
    # v0.5.307's fragile check must be GONE — the fix is at the
    # authoritative side now (feedback_close_gap_at_authoritative_side).
    assert '_dc.get("ipv4_enabled") is False' not in src, (
        "v0.5.308 removed the fragile ipv4_enabled propagation "
        "check — reintroducing it would re-open the v0.5.306/307 "
        "leak on any callsite that doesn't propagate dhcp_config"
    )


def test_frr_docker_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
