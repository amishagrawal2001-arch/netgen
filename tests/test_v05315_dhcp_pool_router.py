"""v0.5.315 — DHCP Pool Router: distinct client-subnet gateway for OFFER.

Operator report on srv06 device4 (DHCP client) after v0.5.314:
IPv4 lease came through (192.16.30.105/24) but the IPv4 Gateway
column stayed BLANK and the client had no L3 egress. Root cause:
the DHCP-server device (device5) was in RELAY mode with
`relay_return_hop=172.16.30.1` and `gateway=<server iface gw on
172.16.30.0/24>` — dnsmasq emitted `dhcp-option=3,172.16.30.1`
(the iface gateway, wrongly treated as the client's router).
Clients on 192.16.30.0/24 couldn't ARP 172.16.30.1 (different
L2) → dhclient never installed a default route → dhcp_lease_gateway
in the DB stayed empty → UI blank + no egress.

Fix: introduce a THIRD explicit field on the DHCP-server dialog —
"Pool Router" — that holds the client-subnet gateway (e.g.
192.16.30.1). It's distinct from:
  * `gateway` — the server device's OWN iface gateway
  * `relay_return_hop` — the server-side hop back to the relay

Backend prefers `pool_router` for `dhcp-option=3,X`, falls back
to `gateway` for backward compat with pre-v0.5.315 configs and
direct-attached servers where iface gw = client gw.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DIALOG = _REPO / "widgets" / "add_device_dialog.py"
_DEVICES_TAB = _REPO / "widgets" / "devices_tab.py"
_DHCP = _REPO / "utils" / "dhcp.py"


def _dialog_src() -> str:
    return _DIALOG.read_text()


def _devices_tab_src() -> str:
    return _DEVICES_TAB.read_text()


def _dhcp_src() -> str:
    return _DHCP.read_text()


# ---------- Dialog field creation ----------


def test_dialog_creates_pool_router_input():
    src = _dialog_src()
    assert "self.dhcp_pool_router_input = QLineEdit()" in src


def test_dialog_pool_router_label_is_specific():
    """Row label must say "Pool Router" so operators don't confuse
    it with the existing "Gateway Route" or "Relay Return Hop"
    fields. Widget order matters — placement adjacent to Relay
    Return Hop keeps them visually paired."""
    src = _dialog_src()
    assert 'dhcp_left_layout.addRow("Pool Router:", self.dhcp_pool_router_input)' in src


def test_dialog_pool_router_tooltip_calls_out_client_subnet():
    """Tooltip must warn that the value MUST be on the client
    subnet — that's the operational failure mode from the srv06
    incident."""
    src = _dialog_src()
    idx = src.index("self.dhcp_pool_router_input.setToolTip(")
    body = src[idx:idx + 1500]
    assert "on the CLIENT's subnet" in body or "client subnet" in body.lower()


def test_dialog_pool_router_starts_disabled():
    """Like every other DHCP sub-field, must default disabled
    (grey out unless DHCP+IPv4+Server mode active)."""
    src = _dialog_src()
    assert "self.dhcp_pool_router_input.setEnabled(False)" in src


def test_dialog_pool_router_gets_reset_by_reset_all_dhcp_fields():
    """The blanket 'disable all DHCP fields on protocol change'
    block must include the new field, else stale state leaks
    across protocol switches (v0.5.229 bug pattern)."""
    src = _dialog_src()
    # find the block that already lists dhcp_pool_start_input +
    # dhcp_relay_return_hop_input side by side
    for marker in (
        "self.dhcp_gateway_route_input.setEnabled(False)\n"
        "        self.dhcp_relay_return_hop_input.setEnabled(False)\n"
        "        self.dhcp_pool_router_input.setEnabled(False)",
    ):
        assert marker in src, "pool_router missing from disable-all block"


def test_dialog_pool_router_in_enable_gate():
    """`_update_dhcp_field_states` walks a tuple of ipv4 DHCP-
    server widgets and enables/disables them together. Pool
    router must be in that tuple, else it stays disabled even
    when the operator ticks DHCP-server + IPv4."""
    src = _dialog_src()
    idx = src.index("def _update_dhcp_field_states")
    body = src[idx:idx + 3000]
    assert "self.dhcp_pool_router_input," in body


# ---------- Dialog save path ----------


def test_dialog_save_path_emits_pool_router():
    """save-path must populate `dhcp_config['pool_router']` even
    when the field is blank (v0.5.229 audit B5: emit ALL scalar
    keys so blank = explicit clear, not fall-back-to-stored)."""
    src = _dialog_src()
    assert 'dhcp_config["pool_router"]' in src
    # must call .text().strip() so trailing whitespace doesn't
    # break the IPv4Address parse in the backend.
    idx = src.index('dhcp_config["pool_router"]')
    body = src[idx:idx + 500]
    assert "self.dhcp_pool_router_input.text().strip()" in body


def test_dialog_save_path_emits_pool_router_inside_ipv4_gate():
    """pool_router lives inside the `if ipv4_enabled:` block so
    IPv6-only DHCP servers don't get a spurious empty key that
    the backend might mis-interpret."""
    src = _dialog_src()
    # find the ipv4_enabled block, then confirm pool_router is
    # inside it before we hit the ipv6_enabled block.
    ipv4_idx = src.index("if ipv4_enabled:")
    ipv6_idx = src.index("if ipv6_enabled:", ipv4_idx)
    between = src[ipv4_idx:ipv6_idx]
    assert 'dhcp_config["pool_router"]' in between, (
        "pool_router emit must be under `if ipv4_enabled:`"
    )


# ---------- Dialog validation ----------


def test_dialog_validates_pool_router_is_ipv4():
    """A bad IP string in the Pool Router field would land in
    the dnsmasq config verbatim and dnsmasq would refuse to
    start (device sits in Failed). Reject at the dialog before
    Save is accepted."""
    src = _dialog_src()
    assert "ipaddress.IPv4Address(v4_pool_router)" in src


def test_dialog_pool_router_validation_allows_empty():
    """Empty is legal (backend falls back to `gateway`). The
    guard must be `if v4_pool_router:` so empty short-circuits
    the parse."""
    src = _dialog_src()
    idx = src.index("v4_pool_router = (")
    body = src[idx:idx + 1500]
    assert "if v4_pool_router:" in body


# ---------- devices_tab edit-load ----------


def test_devices_tab_preloads_pool_router_on_edit():
    """Edit-open of an existing DHCP-server device must
    preload `dhcp_pool_router_input`. Without this preload,
    the field shows empty even when dhcp_config has a value,
    and any subsequent Save silently clears it (the save-path
    writes what the field CURRENTLY holds)."""
    src = _devices_tab_src()
    assert 'dhcp_config.get("pool_router")' in src
    assert "dialog.dhcp_pool_router_input.setText" in src


def test_devices_tab_preload_is_none_safe():
    """dhcp_config from disk might not have `pool_router` at
    all (pre-v0.5.315 devices). Guard on `is not None` (matches
    the neighboring relay_return_hop preload)."""
    src = _devices_tab_src()
    idx = src.index("_pool_router = dhcp_config.get")
    body = src[idx:idx + 500]
    assert "if _pool_router is not None" in body


# ---------- Backend consumer ----------


def test_backend_reads_pool_router_from_dhcp_config():
    src = _dhcp_src()
    assert 'dhcp_config.get("pool_router"' in src


def test_backend_prefers_pool_router_over_gateway():
    """dhcp-option=3 must use `pool_router` when set, and fall
    back to `gateway` when empty. Pattern: `router_option = pool_router or gateway`."""
    src = _dhcp_src()
    assert "router_option = pool_router or gateway" in src


def test_backend_emits_dhcp_option_3_from_router_option():
    """The actual emit line must use `router_option` (the
    computed value), not `gateway` directly, or the fix
    doesn't fire."""
    src = _dhcp_src()
    assert 'config_lines.append("dhcp-option=3," + router_option)' in src


def test_backend_still_emits_when_only_gateway_set():
    """Backward compat: a device with `gateway="10.0.0.1"` and
    no `pool_router` must still emit dhcp-option=3. That means
    the guard is `if router_option:`, not `if pool_router:`."""
    src = _dhcp_src()
    # search near the emit for the guard
    idx = src.index('config_lines.append("dhcp-option=3," + router_option)')
    before = src[max(0, idx - 500):idx]
    assert "if router_option:" in before


def test_backend_logs_when_pool_router_diverges_from_gateway():
    """Operator debuggability — when pool_router != gateway,
    log INFO so `journalctl -u netgen-server` traces the
    relay-mode router-option decision (matches v0.5.245
    relay-mode INFO log style)."""
    src = _dhcp_src()
    assert (
        "dhcp-option=3 uses explicit " in src
        and "pool_router=" in src
    )


def test_backend_pool_router_gated_on_ipv4_enabled():
    """A v6-only DHCP server must not read `pool_router` (the
    field is only in the IPv4 half of the dialog). Backend
    read must be gated: `if ipv4_enabled else ""`."""
    src = _dhcp_src()
    idx = src.index('pool_router = (dhcp_config.get("pool_router"')
    body = src[idx:idx + 300]
    assert "if ipv4_enabled else" in body


# ---------- AST parse discipline (v0.5.300 lesson) ----------


def test_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())


def test_devices_tab_ast_parses():
    import ast
    ast.parse(_devices_tab_src())


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
