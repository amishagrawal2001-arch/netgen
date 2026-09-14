"""v0.5.318 — DHCP-relay stanza in the Upstream Config Hint dialog.

Operator report: the v0.5.317 in-dialog QLabel hint was single-
vendor (Juniper only) and lived in a duplicate place. The existing
"Upstream Config Hint…" button opens a proper multi-vendor dialog
(utils.upstream_hints) with tabs for Juniper / Cisco IOS / Arista
EOS and a copy-to-clipboard button, but pre-fix it emitted no
DHCP-relay stanza (the module's docstring even asserted this was
intentional). Add the stanza across all three vendors and revert
the v0.5.317 QLabel so operators have one place to look.
"""
from __future__ import annotations

from pathlib import Path

_UPSTREAM = Path(__file__).resolve().parents[1] / "utils" / "upstream_hints.py"
_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_device_dialog.py"


def _upstream_src() -> str:
    return _UPSTREAM.read_text()


def _dialog_src() -> str:
    return _DIALOG.read_text()


# ---------- v0.5.317 revert ----------


def test_v05317_qlabel_hint_removed():
    """The v0.5.317 in-dialog QLabel is superseded by the upstream-
    hint dialog stanza. Its widget must no longer exist — else
    operators see BOTH the panel AND the dialog stanza, which is
    duplicated advice."""
    src = _dialog_src()
    assert "self.dhcp_client_relay_hint = QLabel()" not in src
    assert "self.dhcp_client_relay_hint.setVisible" not in src


# ---------- module-level integration ----------


def test_upstream_hints_reads_dhcp_config():
    """The `_render` orchestrator must pull dhcp_config off the
    device data so `_dhcp_relay_stanza` can key off client mode."""
    src = _upstream_src()
    assert 'device_data.get("dhcp_config")' in src


def test_dhcp_relay_stanza_only_fires_for_client_mode():
    """Server + non-DHCP devices must NOT get a relay stanza.
    Otherwise a DHCP-server device's upstream-hint would carry
    a self-referential relay config, which is nonsense."""
    src = _upstream_src()
    idx = src.index("_dhcp_mode = (")
    body = src[idx:idx + 800]
    assert 'if _dhcp_mode == "client":' in body


def test_dhcp_relay_stanza_defined():
    """Renderer function must exist so `_render` can call it."""
    src = _upstream_src()
    assert "def _dhcp_relay_stanza(vendor: str, vlan: str, dhcp_config: dict)" in src


def test_dhcp_relay_stanza_wired_into_render():
    src = _upstream_src()
    assert "_dhcp_relay_stanza(vendor, vlan, dhcp_config)" in src


# ---------- runtime rendering (execute the code path) ----------


def _import_upstream_hints():
    import sys, importlib
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("utils.upstream_hints")


def test_juniper_render_client_emits_forwarding_options():
    """The Juniper stanza matches the operator's srv06 QFX5130
    reference config exactly (set-form lines that a Junos CLI
    accepts verbatim)."""
    mod = _import_upstream_hints()
    dev = {
        "device_name": "dhcp-client-1",
        "vlan": "30",
        "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    out = mod.render_juniper(dev)
    assert "set forwarding-options dhcp-relay overrides allow-snooped-clients" in out
    assert "set forwarding-options dhcp-relay forward-only" in out
    assert "set forwarding-options dhcp-relay server-group DHCP-SERVERS" in out
    assert "set forwarding-options dhcp-relay active-server-group DHCP-SERVERS" in out
    assert "set forwarding-options dhcp-relay group CLIENTS interface irb.30" in out


def test_cisco_render_client_emits_ip_helper_address():
    """Cisco uses `ip helper-address` on the client-facing SVI —
    NOT `forwarding-options dhcp-relay` (that's Junos-only)."""
    mod = _import_upstream_hints()
    dev = {
        "device_name": "dhcp-client-1",
        "vlan": "30",
        "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    out = mod.render_cisco(dev)
    assert "interface Vlan30" in out
    assert "ip helper-address" in out


def test_arista_render_client_emits_ip_helper_address():
    mod = _import_upstream_hints()
    dev = {
        "device_name": "dhcp-client-1",
        "vlan": "30",
        "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    out = mod.render_arista(dev)
    assert "interface Vlan30" in out
    assert "ip helper-address" in out


def test_server_mode_gets_no_relay_stanza():
    """dhcp_mode == server must not emit a relay stanza (a server
    doesn't need its own upstream to relay to itself)."""
    mod = _import_upstream_hints()
    dev = {
        "device_name": "dhcp-server-1",
        "vlan": "10",
        "dhcp_mode": "server",
        "dhcp_config": {"mode": "server"},
    }
    for out in (mod.render_juniper(dev), mod.render_cisco(dev), mod.render_arista(dev)):
        assert "dhcp-relay" not in out
        assert "ip helper-address" not in out


def test_no_dhcp_config_gets_no_relay_stanza():
    """A non-DHCP device (no dhcp_mode + no dhcp_config) must not
    trip the relay-stanza code path."""
    mod = _import_upstream_hints()
    dev = {"device_name": "static-1", "vlan": "10", "ipv4_address": "10.0.0.2"}
    for out in (mod.render_juniper(dev), mod.render_cisco(dev), mod.render_arista(dev)):
        assert "dhcp-relay" not in out
        assert "ip helper-address" not in out


def test_placeholder_server_ip_when_no_hint():
    """Client-side dialog doesn't know its server IP — render a
    `<DHCP-SERVER-IP>` placeholder so the operator visibly sees
    the substitution needed."""
    mod = _import_upstream_hints()
    dev = {
        "device_name": "c1", "vlan": "30", "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    out = mod.render_juniper(dev)
    assert "<DHCP-SERVER-IP>" in out


def test_upstream_server_hint_replaces_placeholder():
    """If dhcp_config carries an explicit `upstream_server_hint`
    (some deployments pin it), use that instead of the
    placeholder."""
    mod = _import_upstream_hints()
    dev = {
        "device_name": "c1", "vlan": "30", "dhcp_mode": "client",
        "dhcp_config": {
            "mode": "client",
            "upstream_server_hint": "172.16.30.2",
        },
    }
    out = mod.render_juniper(dev)
    assert "172.16.30.2" in out
    assert "<DHCP-SERVER-IP>" not in out


def test_vlan_zero_falls_back_to_placeholder_svi():
    """No VLAN → can't guess an SVI name; render `<vlan>`
    placeholder + a note so the operator picks."""
    mod = _import_upstream_hints()
    dev = {
        "device_name": "c1", "vlan": "0", "dhcp_mode": "client",
        "dhcp_config": {"mode": "client"},
    }
    out = mod.render_juniper(dev)
    assert "irb.<vlan>" in out


# ---------- dialog snapshot integration ----------


def test_snapshot_emits_dhcp_config_when_client():
    """`_snapshot_for_upstream_hint` must emit dhcp_config so the
    renderer can key off it. Otherwise DHCP-client devices get
    no relay stanza even after the module fix."""
    src = _dialog_src()
    idx = src.index("def _snapshot_for_upstream_hint")
    body = src[idx:idx + 5000]
    assert 'data["dhcp_mode"]' in body
    assert 'data["dhcp_config"]' in body


def test_snapshot_gated_on_dhcp_enable_checkbox():
    """No DHCP → no dhcp_config emitted. Otherwise a static device
    with dhcp_mode_combo default "Client" would still emit a
    relay stanza."""
    src = _dialog_src()
    idx = src.index("def _snapshot_for_upstream_hint")
    body = src[idx:idx + 5000]
    assert '_dhcp_on = checked("dhcp_enable_checkbox")' in body
    assert "if _dhcp_on and hasattr(self, \"dhcp_mode_combo\"):" in body


# ---------- AST parse (v0.5.300 lesson) ----------


def test_upstream_hints_ast_parses():
    import ast
    ast.parse(_upstream_src())


def test_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())
