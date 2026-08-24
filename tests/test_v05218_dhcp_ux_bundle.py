"""v0.5.218: DHCP UX + server-state bundle — bugs I-N off the
DHCP walkthrough.

v0.5.217 shipped bugs A-H (Edit-Device pre-fill, DHCP-client
+ BGP interaction, Remove path, stop_dhcp_server failure
reporting, monitor coverage, start_dhcp_server failure state,
manual-override guard, restart backoff). This bundle finishes
the audit:

  I. Delete-key shortcut + right-click menu no longer silently
     disable — the wire-up now points at a real method
     (`delete_selected_dhcp_row`) and the outer bare
     `except Exception: pass` was upgraded to log at ERROR.
  J. Refresh / Attach / Apply DHCP no longer freeze the UI —
     each wraps its `requests` call in a QThread with a
     QProgressDialog (Cancel enabled for Refresh; disabled for
     the two mutating ops, matching OSPF/BGP/ISIS Apply
     policy) and holds a `_dhcp_workers` keepalive.
  K. `stop_dhcp_client` now also kills IPv6 daemons
     (dhcp6c + dhclient -6), flushes non-link-local IPv6
     addresses, and clears the IPv6-side DB fields
     (ipv6_address / ipv6_mask / ipv6_gateway).
  L. `start_dhcp_server` mirrors every `ip route replace` into
     the device's per-device VRF table when one exists
     (via `_resolve_device_vrf` + `_add_route_and_vrf_copy`,
     the server-mode parallel of `_migrate_dhcp_route_to_vrf`).
  M. `_is_dhclient_running` no longer substring-matches — it
     parses `pgrep -a -f dhclient` and compares argv tokens
     exactly, so `dhclient eth10` no longer satisfies a query
     for `eth1`.
  N. The `/api/device/apply` "disable DHCP" branch now clears
     every dhcp_lease_* column (plus last_dhcp_check) so the
     UI doesn't keep showing a leased IP for a disabled row.

These are source-level lock-ins — grep-style assertions —
matched to the v0.5.207-v0.5.217 pattern that has caught
every regression since.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05218_test_{os.getpid()}.db"),
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _dhcp_tab_src() -> str:
    return (REPO / "utils" / "devices_tab_dhcp.py").read_text()


def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def _run_tgen_server_src() -> str:
    return (REPO / "run_tgen_server.py").read_text()


# ---------------------------------------------------------------------------
# Bug I — Delete key + right-click menu.
# ---------------------------------------------------------------------------

def test_I_delete_key_wired_to_real_method():
    """Pre-fix: `.connect(self.delete_selected_pool)` — but that
    method only exists on ManageDHCPPoolsDialog, not DHCPHandler,
    so the connect raised AttributeError which the outer
    ``except Exception: pass`` swallowed. Post-fix: wire to a
    real method on DHCPHandler."""
    src = _dhcp_tab_src()
    # The bad wire is gone.
    assert (
        "_dhcp_del.activated.connect(self.delete_selected_pool)"
        not in src
    ), (
        "Delete-key shortcut is still connected to "
        "self.delete_selected_pool — that method does not exist "
        "on DHCPHandler and the whole shortcut+menu block will "
        "silently disable itself again (Bug I is back)."
    )
    # The new wire points at delete_selected_dhcp_row.
    assert (
        "_dhcp_del.activated.connect(self.delete_selected_dhcp_row)"
        in src
    ), (
        "Delete-key shortcut no longer connects to "
        "delete_selected_dhcp_row — the audit-fix-I wire is gone."
    )


def test_I_delete_method_exists_on_dhcp_handler():
    """The method targeted by the Delete-key + context-menu wire
    must actually live on DHCPHandler — otherwise the connect
    fails and the whole block silently dies."""
    src = _dhcp_tab_src()
    # Parse the module and pull the DHCPHandler class methods.
    tree = ast.parse(src)
    handler = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "DHCPHandler"),
        None,
    )
    assert handler is not None, "DHCPHandler class missing"
    method_names = {
        n.name for n in handler.body if isinstance(n, ast.FunctionDef)
    }
    assert "delete_selected_dhcp_row" in method_names, (
        "DHCPHandler.delete_selected_dhcp_row is missing — the "
        "Delete-key wire will resolve to AttributeError again."
    )


def test_I_context_menu_setup_runs_after_delete_wire():
    """The context-menu setup (setContextMenuPolicy +
    customContextMenuRequested.connect) MUST come after the fixed
    Delete-key wire, but no longer sit behind a try/except that
    would silently swallow a future AttributeError from the wire
    without at least logging it."""
    src = _dhcp_tab_src()
    idx_wire = src.find(
        "_dhcp_del.activated.connect(self.delete_selected_dhcp_row)"
    )
    idx_policy = src.find(
        "self.parent.dhcp_table.setContextMenuPolicy"
    )
    idx_menu = src.find(
        "self.parent.dhcp_table.customContextMenuRequested.connect"
    )
    assert idx_wire > 0 and idx_policy > 0 and idx_menu > 0, (
        "Delete-key wire / setContextMenuPolicy / "
        "customContextMenuRequested.connect anchors missing"
    )
    assert idx_policy > idx_wire, (
        "setContextMenuPolicy no longer follows the Delete-key "
        "wire — layout regressed"
    )
    assert idx_menu > idx_policy, (
        "customContextMenuRequested.connect no longer follows "
        "setContextMenuPolicy — layout regressed"
    )


def test_I_outer_wire_except_logs_at_error():
    """The outer try/except around the shortcut+menu setup used to
    be a bare `except Exception: pass`. Post-fix it MUST at
    least emit a logging.error line so a future missing-symbol
    regression surfaces in logs instead of silently killing the
    whole block."""
    src = _dhcp_tab_src()
    # Find the wire block by anchoring on the Delete-key wire.
    idx_wire = src.find(
        "_dhcp_del.activated.connect(self.delete_selected_dhcp_row)"
    )
    assert idx_wire > 0
    # Slice a comfortable window that includes the outer except.
    window = src[idx_wire:idx_wire + 3500]
    # The window must contain a logging.error call — either
    # inside the outer except (`_wire_exc`) or one of the
    # per-menu-item excepts. All three per-item excepts also
    # switched to logging.error.
    assert "logging.error" in window, (
        "The outer wiring/context-menu try/except no longer "
        "emits any logging.error — a future AttributeError "
        "will silently disable the block again."
    )
    # And crucially the outer except is no longer bare-pass.
    # Search for the specific `_wire_exc` anchor from the fix.
    assert "_wire_exc" in window, (
        "The outer except no longer captures the exception into "
        "_wire_exc for logging — Bug I safety net regressed."
    )
    # Ensure the pre-fix bare `except Exception: pass` right after
    # the customContextMenuRequested.connect call is gone.
    tail = src[
        src.find(
            "self.parent.dhcp_table.customContextMenuRequested.connect"
        ):
    ]
    # The outer except must reference _wire_exc, not be
    # `except Exception:\n            pass`.
    assert re.search(
        r"except\s+Exception\s+as\s+_wire_exc", tail,
    ), (
        "Outer wiring try/except is no longer typed to "
        "_wire_exc — the safety-net logging is gone."
    )


# ---------------------------------------------------------------------------
# Bug J — Refresh / Attach / Apply progress dialogs + QThread.
# ---------------------------------------------------------------------------

def _slice_method(src: str, method_name: str) -> str:
    """Return the body text of a top-level or class-level method by
    walking the AST — tolerates nested classes and helpers."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            # ast.get_source_segment covers >=3.8.
            seg = ast.get_source_segment(src, node)
            if seg:
                return seg
    return ""


def test_J_refresh_dhcp_status_uses_qthread_and_progress_dialog():
    src = _dhcp_tab_src()
    body = _slice_method(src, "refresh_dhcp_status")
    assert body, "refresh_dhcp_status method missing"
    assert "from PyQt5.QtCore import QThread" in body, (
        "refresh_dhcp_status no longer imports QThread — Bug J fix "
        "for Refresh regressed"
    )
    assert "from PyQt5.QtWidgets import QProgressDialog" in body, (
        "refresh_dhcp_status no longer imports QProgressDialog"
    )
    assert "class RefreshDHCPWorker(QThread)" in body, (
        "refresh_dhcp_status no longer defines a QThread worker"
    )
    assert "finished = pyqtSignal" in body, (
        "refresh_dhcp_status worker no longer exposes a finished signal"
    )
    # Indeterminate progress bar (0, 0) — Refresh doesn't know the
    # step count, and Cancel is enabled + safe (no server-side
    # side effects).
    assert "QProgressDialog(" in body and "0, 0" in body, (
        "refresh_dhcp_status progress dialog is no longer "
        "indeterminate (0, 0)"
    )
    # Cancel is enabled (setCancelButton(None) MUST NOT appear).
    assert "setCancelButton(None)" not in body, (
        "refresh_dhcp_status disabled Cancel — but Refresh has "
        "no server-side side-effects; Cancel MUST stay enabled"
    )


def test_J_attach_dhcp_pools_dispatches_to_worker():
    """`attach_dhcp_pools` should now route the final POST through
    `_run_dhcp_pool_post` (the shared QThread + QProgressDialog
    worker), not call requests.post inline on the UI thread."""
    src = _dhcp_tab_src()
    body = _slice_method(src, "attach_dhcp_pools")
    assert body, "attach_dhcp_pools missing"
    # The pre-fix inline attach POST is gone.
    assert not re.search(
        r"requests\.post\(\s*\n?\s*f?\"?"
        r"\{server_url\}/api/device/dhcp/server/attach_pools\"?",
        body,
    ), (
        "attach_dhcp_pools still calls requests.post inline — "
        "Bug J fix for Attach regressed"
    )
    assert "self._run_dhcp_pool_post(" in body, (
        "attach_dhcp_pools no longer dispatches to "
        "_run_dhcp_pool_post"
    )


def test_J_apply_dhcp_pools_dispatches_to_worker():
    src = _dhcp_tab_src()
    body = _slice_method(src, "apply_dhcp_pools")
    assert body, "apply_dhcp_pools missing"
    assert not re.search(
        r"requests\.post\(\s*\n?\s*f?\"?"
        r"\{server_url\}/api/device/dhcp/server/attach_pools\"?",
        body,
    ), (
        "apply_dhcp_pools still calls requests.post inline — "
        "Bug J fix for Apply regressed"
    )
    # Both the "no pools → detach_all" branch AND the main apply
    # branch must dispatch to the worker.
    assert body.count("self._run_dhcp_pool_post(") >= 2, (
        "apply_dhcp_pools no longer dispatches BOTH branches "
        "(no-pool detach_all + primary_pool apply) to "
        "_run_dhcp_pool_post"
    )


def test_J_pool_post_worker_disables_cancel():
    """Attach/Apply mutate server-side dnsmasq state; interrupting
    a partial apply matches the OSPF/BGP/ISIS Apply footgun. So
    `_run_dhcp_pool_post` must set setCancelButton(None)."""
    src = _dhcp_tab_src()
    body = _slice_method(src, "_run_dhcp_pool_post")
    assert body, "_run_dhcp_pool_post helper missing"
    assert "class DHCPPoolPostWorker(QThread)" in body, (
        "_run_dhcp_pool_post no longer defines a QThread worker"
    )
    assert "QProgressDialog(" in body, (
        "_run_dhcp_pool_post no longer opens a QProgressDialog"
    )
    assert "setCancelButton(None)" in body, (
        "_run_dhcp_pool_post no longer disables Cancel — but "
        "Attach/Apply MUST NOT be cancellable (matches OSPF/"
        "BGP/ISIS Apply policy against interrupting partial "
        "applies)"
    )


def test_J_dhcp_workers_keepalive_list():
    """All three Bug J paths (Refresh + shared pool-post helper)
    must hold onto their worker on `self._dhcp_workers` — the
    PyQt5 5.15.11 + Python 3.14 SIGABRT guard OSPF/BGP/ISIS/
    Ping use."""
    src = _dhcp_tab_src()
    refresh_body = _slice_method(src, "refresh_dhcp_status")
    pool_body = _slice_method(src, "_run_dhcp_pool_post")
    for label, body in (
        ("refresh_dhcp_status", refresh_body),
        ("_run_dhcp_pool_post", pool_body),
    ):
        assert "self._dhcp_workers" in body, (
            f"{label} no longer holds a keepalive on "
            f"self._dhcp_workers — Bug J SIGABRT guard regressed"
        )
        assert ".append(worker)" in body, (
            f"{label} no longer appends the worker to the "
            f"keepalive list"
        )


# ---------------------------------------------------------------------------
# Bug K — stop_dhcp_client releases IPv6 daemons + wipes IPv6 DB fields.
# ---------------------------------------------------------------------------

def test_K_stop_dhcp_client_releases_dhclient_v6():
    src = _dhcp_src()
    body = _slice_method(src, "stop_dhcp_client")
    assert body, "stop_dhcp_client missing"
    # Must issue dhclient -6 -r on the interface.
    assert '"dhclient", "-6", "-r"' in body, (
        "stop_dhcp_client no longer releases the DHCPv6 dhclient "
        "lease — Bug K regressed (v6 daemon leaks after Stop)."
    )


def test_K_stop_dhcp_client_kills_dhcp6c():
    src = _dhcp_src()
    body = _slice_method(src, "stop_dhcp_client")
    assert body, "stop_dhcp_client missing"
    # Must pkill any dhcp6c bound to this interface. The pattern
    # is anchored via re.escape (see bug M for why anchoring
    # matters).
    assert 'pkill' in body and 'dhcp6c' in body, (
        "stop_dhcp_client no longer kills lingering dhcp6c "
        "processes — the DHCPv6 daemon leaks after Stop"
    )
    assert "re.escape(interface)" in body, (
        "stop_dhcp_client's dhcp6c pkill is no longer anchored "
        "via re.escape — eth1 vs eth10 collision returns"
    )


def test_K_stop_dhcp_client_flushes_ipv6_addresses():
    src = _dhcp_src()
    body = _slice_method(src, "stop_dhcp_client")
    assert body, "stop_dhcp_client missing"
    assert "_flush_ipv6(interface" in body, (
        "stop_dhcp_client no longer flushes IPv6 addresses — "
        "stale DHCPv6 lease sticks around on the interface"
    )


def test_K_stop_dhcp_client_clears_ipv6_db_fields():
    """The DB write must clear ipv6_address / ipv6_mask /
    ipv6_gateway along with the IPv4 fields — otherwise the
    row keeps rendering the stale IPv6 lease."""
    src = _dhcp_src()
    body = _slice_method(src, "stop_dhcp_client")
    assert body, "stop_dhcp_client missing"
    for field in ("ipv6_address", "ipv6_mask", "ipv6_gateway"):
        assert f'"{field}": ""' in body, (
            f'stop_dhcp_client no longer clears {field} in the '
            f"DB write — Bug K regressed"
        )


# ---------------------------------------------------------------------------
# Bug L — start_dhcp_server mirrors routes into the device's VRF.
# ---------------------------------------------------------------------------

def test_L_vrf_resolver_helper_exists():
    src = _dhcp_src()
    assert "def _resolve_device_vrf(" in src, (
        "_resolve_device_vrf helper is missing — Bug L fix removed"
    )
    # Body must consult FRRDockerManager and check the VRF link.
    body = _slice_method(src, "_resolve_device_vrf")
    assert body, "_resolve_device_vrf body missing"
    assert "FRRDockerManager" in body, (
        "_resolve_device_vrf no longer imports FRRDockerManager"
    )
    assert "vrf_name_for_device(device_id)" in body, (
        "_resolve_device_vrf no longer calls vrf_name_for_device"
    )
    assert '"ip", "-o", "link", "show"' in body, (
        "_resolve_device_vrf no longer verifies the VRF link exists"
    )


def test_L_route_and_vrf_helper_exists():
    src = _dhcp_src()
    assert "def _add_route_and_vrf_copy(" in src, (
        "_add_route_and_vrf_copy helper missing — Bug L server-"
        "path VRF-mirror is gone"
    )
    body = _slice_method(src, "_add_route_and_vrf_copy")
    assert body, "_add_route_and_vrf_copy body missing"
    # Must run BOTH the main-table replace AND (conditionally) the
    # vrf-scoped replace.
    assert '"vrf", vrf_name' in body, (
        "_add_route_and_vrf_copy no longer adds a VRF-scoped "
        "route — Bug L regressed"
    )


def test_L_start_dhcp_server_resolves_and_uses_vrf():
    src = _dhcp_src()
    body = _slice_method(src, "start_dhcp_server")
    assert body, "start_dhcp_server missing"
    assert "_resolve_device_vrf(device_id)" in body, (
        "start_dhcp_server no longer resolves the device's VRF — "
        "server-mode routes stay main-table-only (Bug L)"
    )
    # It must feed vrf_name into the route helper on the IPv4
    # side.
    assert "_add_route_and_vrf_copy(" in body, (
        "start_dhcp_server no longer routes through "
        "_add_route_and_vrf_copy — Bug L regressed"
    )
    # And IPv6 gateway/static routes use the same helper.
    assert "IPv6 gateway route" in body or "IPv6 static route" in body, (
        "start_dhcp_server no longer labels its IPv6 route "
        "installations — sanity anchor gone"
    )


# ---------------------------------------------------------------------------
# Bug M — _is_dhclient_running argv-token match, not substring.
# ---------------------------------------------------------------------------

def test_M_is_dhclient_running_no_longer_uses_unanchored_substring():
    src = _dhcp_src()
    body = _slice_method(src, "_is_dhclient_running")
    assert body, "_is_dhclient_running missing"
    # The pre-fix unanchored `pgrep -f 'dhclient.*{interface}'`
    # shell-string form is gone from live code. Strip comments
    # and the module docstring so historical footgun references
    # in the audit-fix comment don't count.
    code_lines = []
    in_docstring = False
    for ln in body.splitlines():
        stripped = ln.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        code_lines.append(ln)
    live = "\n".join(code_lines)
    assert "dhclient.*{interface}" not in live, (
        "_is_dhclient_running still uses the unanchored "
        "'dhclient.*{interface}' substring in live code — "
        "Bug M is back (eth1 matches eth10)."
    )
    # The fix explicitly says "compare argv tokens exactly" —
    # look for the argv token-list check.
    assert "if interface in argv" in body, (
        "_is_dhclient_running no longer checks `interface in "
        "argv` as an exact whole-token match — Bug M anchor lost"
    )
    # And it uses pgrep -a so we get argv rows to parse.
    assert '"pgrep", "-a", "-f", "dhclient"' in body, (
        "_is_dhclient_running no longer invokes "
        "`pgrep -a -f dhclient` for token parsing"
    )


# ---------------------------------------------------------------------------
# Bug N — disabling DHCP clears all dhcp_lease_* columns.
# ---------------------------------------------------------------------------

def test_N_disable_dhcp_clears_all_lease_fields():
    src = _run_tgen_server_src()
    # Find the "elif existing_device.get("dhcp_mode")" disable
    # branch and inspect the surrounding block.
    m = re.search(
        r'elif existing_device\.get\("dhcp_mode"\) and \('
        r'"DHCP" not in protocols\):(.+?)(?:\n\s{16,20}[a-z]|'
        r'\n\s{20}#\s+Always update VXLAN)',
        src, re.DOTALL,
    )
    assert m, (
        "The /api/device/apply DHCP-disable branch could not be "
        "located — layout regressed"
    )
    branch = m.group(1)
    for field in (
        "dhcp_lease_ip",
        "dhcp_lease_mask",
        "dhcp_lease_gateway",
        "dhcp_lease_server",
        "dhcp_lease_expires",
        "dhcp_lease_subnet",
    ):
        assert f'"{field}"' in branch, (
            f"Disable-DHCP branch no longer clears {field} — the "
            f"UI will keep showing a stale lease on a disabled "
            f"row (Bug N)."
        )


# ---------------------------------------------------------------------------
# Smoke: all changed files still parse.
# ---------------------------------------------------------------------------

def test_all_changed_files_still_parse():
    import ast as _ast
    for rel in (
        "utils/devices_tab_dhcp.py",
        "utils/dhcp.py",
        "run_tgen_server.py",
    ):
        p = REPO / rel
        try:
            _ast.parse(p.read_text(), filename=str(p))
        except SyntaxError as exc:
            raise AssertionError(f"{rel} no longer parses: {exc}") from exc


def test_version_bumped():
    py = (REPO / "pyproject.toml").read_text()
    assert 'version = "0.5.218"' in py, (
        "pyproject.toml version not bumped to 0.5.218"
    )
