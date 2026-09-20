"""v0.5.372 — 6-bug bundle from client + protocol audit.

Triaged from 22 findings (client + protocol handlers audit). One
SEC + five correctness/UX. Bigger deferred findings (VRF-table
collision requiring allocation strategy design, VXLAN interface
name truncation dedup, DHCP restart async cancel, RFC 2544 poll
timeout + swallowed exceptions, per-server auth-token routing
pipeline) queued for v0.5.373+.

Fixes:
    C1 — SEC. run_tgen_client.py auth-token monkey-patch is
         URL-guarded so the Netgen bearer token is only forwarded
         to REGISTERED Netgen server hosts. Pre-fix the wrapper
         injected the header on every `requests.*` call — Ollama
         (localhost:11434), LM Studio, GitHub, external LLM APIs
         all received the token when the client called out.
    C2 — utils/device_database.py remove_device: verify BEFORE
         commit so a `conn.rollback()` on stale-row detection is
         actually meaningful. Pre-fix rolled back AFTER commit
         (no-op) but returned False — data was gone, callers
         thought abort had happened.
    C3 — traffic_client/stream_control.py remove_selected_stream:
         QMessageBox.question gate with names + count. Pre-fix
         iterated selection and deleted in place with only a
         post-hoc "Stream Removed" INFORMATION popup.
    C4 — widgets/devices_tab.py device delete: server DELETE
         first, only mutate UI on success. Pre-fix removed the
         row from the table BEFORE the server call — network
         failure = UI desynced, next Refresh restored the device.
    C5 — widgets/add_bgp_dialog.py: RFC 4271 §10 cross-field
         check enforcing hold-time ≥ 3 × keepalive at Accept.
         Pre-fix operator could save keepalive=60 / hold=3 and
         watch BGP churn every 3 s.
    C6 — widgets/add_bgp_dialog.py: ASN validators upgraded to
         QRegExpValidator accepting 1..4294967295 (RFC 6793 full
         4-byte range). Pre-fix QIntValidator(1, 2147483647)
         silently blocked keystrokes past 2^31-1.

All sites carry marker `v0.5.372 (audit …)`.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── ast sanity ───


def test_all_edited_files_ast_parse():
    for _f in ("run_tgen_client.py",
               "traffic_client/menu_actions.py",
               "traffic_client/stream_control.py",
               "widgets/devices_tab.py",
               "widgets/add_bgp_dialog.py",
               "utils/device_database.py"):
        ast.parse(_read(_f))


# ─── C1 SEC: auth token URL-guard ───


def test_c1_marker_present():
    src = _read("run_tgen_client.py")
    assert "v0.5.372 (audit client-auth-token-leak-monkey-patch)" in src


def test_c1_url_guard_function_defined():
    """The wrapper must consult a URL-guard function before
    injecting the header."""
    src = _read("run_tgen_client.py")
    assert "def _netgen_url_allowed(url):" in src
    assert "_NETGEN_ALLOWED_HOSTS" in src


def test_c1_default_allowed_hosts_include_loopback():
    """The client trusts loopback out of the box (server on same
    host is the desktop-in-a-lab pattern)."""
    src = _read("run_tgen_client.py")
    m = re.search(
        r"_NETGEN_ALLOWED_HOSTS\s*=\s*\{([^}]+)\}",
        src,
    )
    assert m
    inits = m.group(1)
    for _h in ("localhost", "127.0.0.1", "::1"):
        assert _h in inits, (
            f"Loopback host {_h!r} missing from default allowed set"
        )


def test_c1_wrapper_gates_on_url_allowed():
    """The `_patched` wrapper must call `_netgen_url_allowed(url)`
    BEFORE setting the Authorization header."""
    src = _read("run_tgen_client.py")
    # Extract the wrapper body.
    m = re.search(
        r"def _wrap_request_fn\(fn\):[\s\S]+?return _patched",
        src,
    )
    assert m
    body = m.group(0)
    assert "_netgen_url_allowed(url)" in body, (
        "Wrapper doesn't URL-guard the header injection — every "
        "requests.* call still leaks the token"
    )


def test_c1_register_hook_exposed():
    """The register function must be exposed on the requests
    module namespace so Add Server / load_server_interfaces can
    grow the allowed set."""
    src = _read("run_tgen_client.py")
    assert "def _netgen_register_server_host(host):" in src
    assert "_rq._netgen_register_server_host = _netgen_register_server_host" in src


def test_c1_add_server_calls_register():
    src = _read("traffic_client/menu_actions.py")
    m = re.search(
        r"def add_server_interface\([\s\S]+?self\.server_interfaces\.append",
        src,
    )
    assert m
    # The register call must be reachable inside add_server_interface.
    fn_end = src.index("def save_server_interfaces", m.start())
    body = src[m.start():fn_end]
    assert "_netgen_register_server_host" in body, (
        "add_server_interface doesn't grow the allowed-hosts set"
    )


def test_c1_load_server_interfaces_calls_register():
    src = _read("traffic_client/menu_actions.py")
    m = re.search(
        r"def load_server_interfaces\([\s\S]+?def save_server_interfaces",
        src,
    )
    assert m
    body = m.group(0)
    assert "_netgen_register_server_host" in body, (
        "load_server_interfaces doesn't grow the allowed-hosts set "
        "— servers restored from disk get their token stripped by "
        "the wrapper"
    )


# ─── C2: device_database rollback-after-commit ───


def test_c2_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.372 (audit device-db-rollback-after-commit)" in src


def test_c2_verify_before_commit():
    """Structural: the SELECT verify must appear BEFORE the
    commit in remove_device. Pre-fix order was commit then
    verify then no-op-rollback."""
    src = _read("utils/device_database.py")
    fn_start = src.index("def remove_device")
    _end = src.index("\n    def ", fn_start + 1)
    body = src[fn_start:_end]
    _verify_pos = body.index(
        'SELECT device_id FROM devices WHERE device_id = ?',
    )
    _commit_pos = body.rindex('conn.commit()')
    assert _verify_pos < _commit_pos, (
        "verify SELECT still appears AFTER commit — rollback on "
        "stale row is a no-op, return False lies"
    )


# ─── C3: Delete Stream confirm ───


def test_c3_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.372 (audit stream-delete-no-confirm)" in src


def test_c3_confirm_before_delete_loop():
    """Structural: QMessageBox.question must appear inside
    remove_selected_stream BEFORE the `for row in selected_rows`
    delete loop."""
    src = _read("traffic_client/stream_control.py")
    fn_start = src.index("def remove_selected_stream")
    # Fetch a bounded window (methods can be huge). 5000 chars is
    # enough to see the confirm gate + the first delete iteration.
    body = src[fn_start:fn_start + 5000]
    _q_pos = body.index("QMessageBox.question")
    _for_pos = body.index("for row in selected_rows:", _q_pos)
    assert _q_pos < _for_pos, (
        "QMessageBox.question doesn't gate the delete loop"
    )
    # And the safer No-default option is present.
    assert "QMessageBox.No," in body


# ─── C4: device delete server-first-then-UI ───


def test_c4_marker_present():
    src = _read("widgets/devices_tab.py")
    assert "v0.5.372 (audit device-delete-ui-first-server-later)" in src


def test_c4_server_delete_before_row_removal():
    """Structural: `_remove_device_from_server` must be called
    BEFORE `self.devices_table.removeRow(row)` inside the
    remove-selected-devices loop."""
    src = _read("widgets/devices_tab.py")
    # Anchor on our marker so we look at the fixed region.
    marker_pos = src.index(
        "v0.5.372 (audit device-delete-ui-first-server-later)",
    )
    tail = src[marker_pos:marker_pos + 2000]
    _server_call_pos = tail.index("_remove_device_from_server(")
    _removeRow_pos = tail.index("self.devices_table.removeRow(row)")
    assert _server_call_pos < _removeRow_pos, (
        "Server DELETE still happens AFTER removeRow — UI desync "
        "on server failure"
    )


def test_c4_skips_ui_removal_on_server_false():
    """When _remove_device_from_server returns False, the code
    must NOT proceed to removeRow — the row must stay so the
    next Refresh doesn't 'restore' a stale device."""
    src = _read("widgets/devices_tab.py")
    marker_pos = src.index(
        "v0.5.372 (audit device-delete-ui-first-server-later)",
    )
    tail = src[marker_pos:marker_pos + 2000]
    # `if _server_ok is False:` branch that then `continue`s.
    assert "_server_ok is False" in tail
    assert "continue" in tail


# ─── C5: BGP keepalive vs hold-time ───


def test_c5_marker_present():
    src = _read("widgets/add_bgp_dialog.py")
    assert "v0.5.372 (audit bgp-timer-cross-field-missing)" in src


def test_c5_hold_time_gte_3x_keepalive_check():
    """Structural: _validate must reject hold < 3 × keepalive."""
    src = _read("widgets/add_bgp_dialog.py")
    fn_start = src.index("def _validate")
    _next = src.index("\n    def ", fn_start + 1)
    body = src[fn_start:_next]
    assert "3 * _keepalive" in body or "3*_keepalive" in body
    assert "RFC 4271" in body
    # And the failure branch returns False (blocks accept).
    assert "return False" in body


# ─── C6: 4-byte ASN validator ───


def test_c6_marker_present():
    src = _read("widgets/add_bgp_dialog.py")
    assert "v0.5.372 (audit bgp-asn-4byte-truncated)" in src


def test_c6_asn_widget_uses_regexp_validator():
    """The widget-level validator must accept up to 10 digits
    (4294967295 is 10 chars)."""
    src = _read("widgets/add_bgp_dialog.py")
    # Both ASN inputs use the same _asn_regex validator now.
    assert "_asn_regex = QRegExpValidator(" in src
    # No remaining QIntValidator(1, 2147483647) that anchors on
    # the ASN inputs.
    assert re.search(
        r"self\.bgp_asn_input\.setValidator\(QIntValidator",
        src,
    ) is None
    assert re.search(
        r"self\.bgp_remote_asn_input\.setValidator\(QIntValidator",
        src,
    ) is None


def test_c6_accept_check_covers_4byte_range():
    """The _validate ASN block must reject > 4294967295 (RFC 6793
    4-byte range)."""
    src = _read("widgets/add_bgp_dialog.py")
    # Inside _validate, look for both asn_local and asn_remote
    # bounded against 4294967295.
    fn_start = src.index("def _validate")
    body = src[fn_start:fn_start + 4000]
    assert "4294967295" in body, (
        "_validate doesn't reference the 4-byte upper bound"
    )
    assert "RFC 6793" in body, (
        "_validate doesn't cite RFC 6793 in the ASN error path"
    )


# ─── version guard ───


def test_pyproject_version_at_least_0572():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 372), (
        f"Version {m.group(1)} < 0.5.372"
    )


# ─── regression guards ───


def test_v0371_install_dpdk_marker_intact():
    """Sanity: v0.5.371 marker on install_dpdk.sh still there."""
    dpdk = (_REPO / "resources" / "dpdk" / "install_dpdk.sh").read_text()
    assert "v0.5.371 (audit install-dpdk-must-not-fail)" in dpdk


def test_v0370_health_running_version_intact():
    """/api/health still returns running_version + restart_pending
    (v0.5.370 B8)."""
    server = (_REPO / "run_tgen_server.py").read_text()
    assert "_STARTUP_NETGEN_VERSION" in server
    assert '"running_version"' in server


def test_v0202_bgp_hold_time_field_intact():
    """v0.5.202 fix for BGP hold-time inline-edit revert is
    unrelated to v0.5.372 but touches the same dialog — regression
    guard."""
    src = _read("widgets/add_bgp_dialog.py")
    assert "self.bgp_hold_time_input" in src
