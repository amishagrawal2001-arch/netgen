"""v0.5.365 — SECURITY HOTFIX: nine attacker-controlled-input paths.

Post-v0.5.364 route audit surfaced multiple missing @require_role
decorators AND unsanitized filesystem paths that let any caller
(viewer or unauth) read arbitrary host files, write attacker-
controlled bytes anywhere the process could write, SIGKILL init,
or SSH-exec into managed switches.

S1 — `/api/capture/download` returned any host-readable file via
    `send_file(request.args["filepath"])`. Fix: operator role +
    `_safe_within(_capture_root(), filepath)` allowlist.

S2 — `/api/capture/start` joined the operator-supplied `filename`
    into the captures dir without `secure_filename`; the
    `interface` string went straight into `tcpdump -i`'s argv
    unvalidated. Fix: operator role + `secure_filename` +
    strict `[A-Za-z0-9._@:-]+` regex on iface.

S3 — `/api/pcap/upload` wrote `file.filename` straight into path.
    Fix: operator role + `secure_filename` + `_safe_within`.

S4 — `/api/capture/summary` read arbitrary filepaths (like S1) +
    `scapy.rdpcap` materialized whole file into RAM. Fix: same
    allowlist as S1.

S6 — `frr_docker` interpolated `loopback_ipv4` / `loopback_ipv6`
    into a `bash -c` string running INSIDE a privileged
    container with cap_add=ALL + host `/var/log/frr` mounted rw.
    Fix: `ipaddress.IPv{4,6}Address(...)` validates + strips
    the value; argv-list form (`bash -c` removed entirely).

S7 — `/api/streams/orphans/reap` SIGTERM/SIGKILLs any PID in body
    with no membership check. Fix: admin role + intersect
    against `find_dpdk_workers()` — refuses PIDs outside the
    worker set (init, netgen-server itself, operator's shell).

S8 — `/api/device/external/execute` runs arbitrary SSH commands
    on registered external switches, no role gate. Fix: operator
    role + `get_json(silent=True)` for malformed-body safety.

S9 — `/api/interfaces/<iface>/admin` POST issues `ip link set …
    up|down` on any iface, no role gate — sibling
    `/api/admin/iface/<iface>/up|down` has one (v0.5.4). Fix:
    admin role for parity.

All nine carry the shared marker `v0.5.365 (audit …)`.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _frr_src():
    return (_REPO / "utils" / "frr_docker.py").read_text()


def test_all_markers_present():
    src = _server_src()
    assert "v0.5.365 (audit capture-routes-hardening, S1-S4)" in src
    assert "v0.5.365 (audit orphans-reap-arbitrary-pid, S7)" in src
    assert "v0.5.365 (audit device-external-execute-no-role, S8)" in src
    assert "v0.5.365 (audit iface-admin-no-role, S9)" in src
    assert "v0.5.365 (audit frr-loopback-shell-injection, S6)" in _frr_src()


# --- Helper: extract a Flask route function's decorator list ---


def _decorators_for_route(src: str, url: str, methods=None):
    """Return the decorator source strings for the FIRST route
    matching `url` (with optional `methods` filter). Uses AST so
    string matching around comments/whitespace is robust."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            _fn = ast.unparse(deco.func)
            if not (_fn.endswith("app.route") or _fn.endswith("app.get") or _fn.endswith("app.post")):
                continue
            if not deco.args:
                continue
            _url = deco.args[0]
            if not (isinstance(_url, ast.Constant) and _url.value == url):
                continue
            if methods:
                _m = None
                for kw in deco.keywords:
                    if kw.arg == "methods":
                        _m = [c.value for c in kw.value.elts if isinstance(c, ast.Constant)]
                if _m != methods and _m is not None:
                    continue
            return [ast.unparse(d) for d in node.decorator_list]
    return None


# --- S1-S4: capture routes ---


def test_S1_download_capture_has_operator_role():
    decos = _decorators_for_route(_server_src(), "/api/capture/download")
    assert decos is not None
    assert any("require_role('operator')" in d for d in decos), (
        "download_capture must be @require_role('operator')"
    )


def test_S1_download_capture_uses_safe_within():
    src = _server_src()
    fn_idx = src.index("def download_capture(")
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    assert "_safe_within(_capture_root(), filepath)" in body
    # And the bare `send_file(filepath)` was replaced with
    # `send_file(_safe, ...)`.
    assert "send_file(_safe" in body


def test_S2_start_capture_hardened():
    src = _server_src()
    decos = _decorators_for_route(src, "/api/capture/start", methods=["POST"])
    assert decos and any("require_role('operator')" in d for d in decos)
    fn_idx = src.index("def start_capture(")
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    # secure_filename applied to raw filename.
    assert "secure_filename" in body
    # Strict iface regex.
    assert "_CAPTURE_IFACE_RE.match(interface)" in body
    # Safe body parse (no bare `request.json` raising on bad body).
    assert "get_json(silent=True)" in body
    # Final path re-check.
    assert "_safe_within(capture_dir, filepath)" in body


def test_S3_upload_pcap_hardened():
    src = _server_src()
    decos = _decorators_for_route(src, "/api/pcap/upload")
    assert decos and any("require_role('operator')" in d for d in decos)
    fn_idx = src.index("def upload_pcap(")
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    assert "secure_filename" in body
    assert "_safe_within(pcap_dir, filepath)" in body


def test_S4_capture_summary_hardened():
    src = _server_src()
    decos = _decorators_for_route(src, "/api/capture/summary")
    assert decos and any("require_role('operator')" in d for d in decos)
    fn_idx = src.index("def capture_summary(")
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    assert "_safe_within(_capture_root(), filepath)" in body
    # rdpcap call must use the sanitized path, not the raw one.
    assert "rdpcap(_safe)" in body


def test_capture_helpers_defined_at_module_level():
    """`_safe_within`, `_capture_root`, `_pcap_upload_root`,
    `_CAPTURE_IFACE_RE` all need to live at module level so all
    four routes share the same allowlist definition."""
    src = _server_src()
    assert "def _capture_root()" in src
    assert "def _pcap_upload_root()" in src
    assert "def _safe_within(" in src
    assert "_CAPTURE_IFACE_RE = re.compile" in src


# --- S6: frr_docker loopback shell injection ---


def test_S6_loopback_uses_ipaddress_validation():
    """The `bash -c ip addr add {loopback}/32 …` shell-string
    interpolation must be gone. Post-fix, the value is validated
    via `ipaddress.IPv{4,6}Address(...)`; malformed input skips
    the anchor with a warning log."""
    src = _frr_src()
    marker_idx = src.index("v0.5.365 (audit frr-loopback-shell-injection, S6)")
    body = src[marker_idx:marker_idx + 5000]
    # Validation calls present.
    assert "_ipa.IPv4Address(" in body
    assert "_ipa.IPv6Address(" in body
    # Argv-form exec_run replaces `bash -c` for the anchor add.
    assert 'container.exec_run(\n                            ["ip", "addr", "add"' in body \
        or '"ip", "addr", "add", f"{_v4}/32"' in body
    assert '"ip", "-6", "addr", "add"' in body


def test_S6_no_bash_c_shell_interpolation_of_loopback_in_add():
    """Regression guard: the pre-fix `f"ip addr add {loopback_ipv4}
    /32 dev lo …"` string must be gone from live code."""
    src = _frr_src()
    marker_idx = src.index("v0.5.365 (audit frr-loopback-shell-injection, S6)")
    body = src[marker_idx:marker_idx + 5000]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    # Live code no longer contains the exploding f-string.
    assert 'f"ip addr add {loopback_ipv4}/32' not in _code_only
    assert 'f"ip -6 addr add {loopback_ipv6}/128' not in _code_only


# --- S7: orphans/reap ---


def test_S7_orphans_reap_admin_role():
    decos = _decorators_for_route(_server_src(), "/api/streams/orphans/reap")
    assert decos and any("require_role('admin')" in d for d in decos), (
        "orphans/reap must be admin role — pre-fix any caller "
        "could SIGKILL init"
    )


def test_S7_orphans_reap_membership_check():
    """Post-fix must intersect requested PIDs against
    `find_dpdk_workers()` and 400 on out-of-scope pids."""
    src = _server_src()
    fn_idx = src.index("def api_streams_orphans_reap(")
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    assert "find_dpdk_workers" in body
    assert "_worker_pids" in body
    assert "out_of_scope" in body


# --- S8: device/external/execute ---


def test_S8_external_execute_operator_role():
    decos = _decorators_for_route(_server_src(), "/api/device/external/execute")
    assert decos and any("require_role('operator')" in d for d in decos)


def test_S8_external_execute_uses_silent_get_json():
    """Regression guard: post-fix uses `get_json(silent=True) or
    {}` so a malformed body 400s cleanly instead of raising."""
    src = _server_src()
    fn_idx = src.index("def execute_external_device_command(")
    _next = src.index("\n    @app.route", fn_idx + 1)
    body = src[fn_idx:_next]
    assert "get_json(silent=True)" in body


# --- S9: interfaces/<iface>/admin ---


def test_S9_iface_admin_admin_role():
    decos = _decorators_for_route(_server_src(), "/api/interfaces/<iface>/admin")
    assert decos and any("require_role('admin')" in d for d in decos), (
        "interface_admin must be admin role for parity with "
        "sibling /api/admin/iface/<iface>/up|down"
    )


# --- Regression guards ---


def test_require_role_decorator_still_defined():
    """S1-S9 fixes all depend on @require_role. Guard against
    someone removing the decorator while these routes still
    reference it."""
    assert "def require_role(required: str):" in _server_src()


def test_ast_parses():
    ast.parse(_server_src())
    ast.parse(_frr_src())
