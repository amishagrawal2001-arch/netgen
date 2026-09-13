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
    """The `else` branch that used to hardcode 192.168.0.2 now
    routes DHCP-server + ipv4_enabled=False into the empty-string
    path (same as the DHCP-client branch)."""
    src = _src()
    # Marker: the widened guard checks both dhcp_mode == "client"
    # AND (server AND ipv4_enabled=False).
    assert 'dhcp_mode == "client" or (' in src
    assert 'dhcp_mode == "server" and _v4_off_by_config' in src


def test_dhcp_config_parsed_defensively_from_device_config():
    """dhcp_config in device_config may arrive as dict or JSON
    string (depending on which layer built device_config).
    Both cases must yield a dict for the ipv4_enabled check."""
    src = _src()
    assert 'device_config.get("dhcp_config")' in src
    assert 'json.loads(_dc)' in src
    assert '_dc = _dc if isinstance(_dc, dict) else {}' in src


def test_json_module_imported():
    """The v0.5.307 fix uses json.loads for the string form of
    dhcp_config — make sure the module is available at the top of
    the file (was already imported for other paths, but pin it)."""
    src = _src()
    # Grab the top-of-file imports.
    head = src[:600]
    assert "\nimport json" in head or "^import json" in head or "import json\n" in head


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


def test_v6_only_check_uses_is_False_not_truthiness():
    """`_dc.get("ipv4_enabled") is False` — strict identity check.
    A missing key returns None (NOT False), so a device with no
    dhcp_config or dhcp_config missing the ipv4_enabled key
    would NOT trip the divert path — safe default. Truthiness
    (`not _dc.get("ipv4_enabled")`) would wrongly divert those
    devices too and break legacy behavior."""
    src = _src()
    assert '_dc.get("ipv4_enabled") is False' in src
    # And NOT the loose truthiness variant.
    assert 'not _dc.get("ipv4_enabled")' not in src


def test_frr_docker_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
