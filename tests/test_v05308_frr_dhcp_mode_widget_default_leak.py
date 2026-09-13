"""v0.5.308 — v0.5.307's guard needed dhcp_config to be in device_config,
but the caller at run_tgen_server.py:5286 builds container_device_config
WITHOUT dhcp_config. Widen the FRR-side guard to skip the widget-default
fallback for ANY dhcp_mode device regardless of dhcp_config presence.

srv06 v0.5.307 log showed:
  * dhcp_config.ipv4_enabled=False in /api/device/apply (correct)
  * [DEVICE APPLY WARN v0.5.306] didn't fire (input `ipv4` was already empty)
  * BUT `[FRR] Using interface IPv4 192.168.0.2 as loopback fallback` fired
  * AND `[FRR] Final loopback values: loopback_ipv4=192.168.0.2` fired
  * AND `san-hp-srv06(config-if)#  ip address 192.168.0.2/24` fired
    via vtysh inside the FRR container
  * Interface got 192.168.0.2/24 attached

Rationale for the widened guard: DHCP-server devices with v4 pool
enabled get their interface anchor from utils/dhcp._ensure_ipv4_address
(v0.5.222 fix); DHCP-server v6-only devices don't need a v4 anchor at
all; DHCP-client devices pick up their v4 from the lease. In none of
these cases should the FRR container step add its own static v4
default. The widget-default fallback stays for the non-DHCP path (some
legacy tests depend on it).
"""
from __future__ import annotations

from pathlib import Path

_FRR_PY = Path(__file__).resolve().parents[1] / "utils" / "frr_docker.py"


def _src() -> str:
    return _FRR_PY.read_text()


def test_marker_present_in_both_configure_blocks():
    """The two near-duplicate _configure_interfaces blocks both got
    the v0.5.308 marker + widened guard."""
    src = _src()
    assert src.count("v0.5.308 (audit v6-only-dhcp-server-leak") == 2


def test_widened_guard_covers_both_dhcp_modes():
    """The empty-string branch now triggers for dhcp_mode in
    ('client', 'server') — not just 'client' or the fragile
    v0.5.307 dhcp_config.ipv4_enabled probe."""
    src = _src()
    assert 'if dhcp_mode in ("client", "server"):' in src


def test_v0_5_307_fragile_probe_removed():
    """The v0.5.307 `_v4_off_by_config` / dhcp_config.get() probe is
    gone from both blocks — it was fragile because run_tgen_server.py
    call sites don't reliably pass dhcp_config in the container config
    dict."""
    src = _src()
    assert "_v4_off_by_config" not in src
    assert 'dhcp_config.get("ipv4_enabled")' not in src


def test_widget_default_fallback_preserved_for_non_dhcp():
    """Legacy behavior: static-IP (non-DHCP) devices with empty ipv4
    still fall through to 192.168.0.2/24 — some tests + non-DHCP
    device shapes depend on this. Only DHCP-family paths are diverted."""
    src = _src()
    assert "ipv4_addr = '192.168.0.2'" in src
    assert "ipv4_mask = '24'" in src
    # The fallback is guarded by an `else:` — verify it's not the
    # unconditional path.
    idx = src.index("v0.5.308 (audit v6-only-dhcp-server-leak")
    body = src[idx:idx + 3000]
    assert "else:" in body


def test_frr_docker_ast_parses():
    """v0.5.300 lesson."""
    import ast
    ast.parse(_src())
