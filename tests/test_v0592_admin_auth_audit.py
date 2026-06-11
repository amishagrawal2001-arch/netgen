"""v0.5.92 — audit batch: auth fortification + audit trail.

From the v0.5.91 admin-console audit (4-agent fan-out):

  H1 — 4 admin endpoints had NO @require_role decorator.
       /health, /install_dpdk/log, /install_rdma/log,
       /upgrade_wheel/log were anonymously readable in any
       auth-enabled deployment because the bearer middleware
       only gates "is token known".

  M1 — bind_history had two stacked @require_role decorators.
       Python applies them bottom-up so only the inner admin
       gate ever ran; the outer viewer gate was dead code.

  H6 — v0.5.88-v0.5.91 lifecycle/flash/details endpoints
       emitted zero log lines. Operators couldn't reconstruct
       who issued what action from journalctl.

  M11 — /api/admin/journal returned journalctl verbatim. If
       OSTG_LOG_LEVEL=DEBUG ever got toggled, two existing
       `logging.debug(dict(request.headers))` sites would
       dump live Authorization: Bearer tokens that any viewer
       with the /journal endpoint could exfiltrate.

  M12 — three `subprocess.run(["cp", ...], check=True)` calls
       in the IOMMU GRUB-config path had no timeout. A stuck
       FS would hang the Flask worker forever.

  SEC L1 — flash error response leaked `str(e)` from Popen
       (resolved ethtool path, etc).
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── H1: every admin route is decorated ───


def _route_then_require_role(src, route_url):
    """Find the @app.route("X") decorator + scan forward up to
    400 chars for a @require_role. Returns the require_role
    string or None."""
    needle = f'@app.route("{route_url}"'
    idx = src.find(needle)
    if idx < 0:
        return "MISSING_ROUTE"
    window = src[idx:idx + 400]
    m = re.search(r"@require_role\(['\"](\w+)['\"]\)", window)
    return m.group(1) if m else None


def test_admin_health_has_require_role():
    assert _route_then_require_role(_src(), "/api/admin/health") == "viewer"


def test_install_dpdk_log_has_require_role():
    assert _route_then_require_role(_src(), "/api/admin/install_dpdk/log") == "viewer"


def test_install_rdma_log_has_require_role():
    assert _route_then_require_role(_src(), "/api/admin/install_rdma/log") == "viewer"


def test_upgrade_wheel_log_has_require_role():
    assert _route_then_require_role(_src(), "/api/admin/upgrade_wheel/log") == "viewer"


# ─── M1: bind_history decorator de-stacked ───


def test_bind_history_has_single_require_role_decorator():
    """Pre-v0.5.92 the route had two stacked @require_role lines.
    De-stack to one and rely on internal branching for POST."""
    src = _src()
    idx = src.find('@app.route("/api/admin/bind_history"')
    assert idx > 0
    window = src[idx:idx + 400]
    matches = re.findall(r"@require_role\(['\"](\w+)['\"]\)", window)
    assert len(matches) == 1, (
        f"Expected exactly one @require_role on bind_history, "
        f"found {len(matches)}: {matches}"
    )
    assert matches[0] == "viewer"


def test_bind_history_post_branches_on_role():
    src = _src()
    idx = src.find("def api_admin_bind_history(")
    assert idx > 0
    body = src[idx:idx + 1500]
    # POST branch checks role explicitly.
    assert "_role_for_request()" in body
    assert "POST requires 'admin'" in body or "admin role required" in body
    assert "403" in body


# ─── H6: [ADMIN] structured audit logging ───


def test_admin_audit_helper_defined():
    src = _src()
    assert "def _admin_audit(" in src
    m = re.search(
        r"def _admin_audit[\s\S]+?(?=\ndef [a-z_]|\n@app\.route)",
        src,
    )
    body = m.group(0)
    # Captures remote addr + role.
    assert "remote_addr" in body
    assert "_role_for_request" in body
    # Best-effort — must not crash the handler.
    assert "except Exception" in body
    # Emits at INFO level.
    assert "logging.info" in body
    # With [ADMIN] prefix.
    assert "[ADMIN]" in body


def test_lifecycle_endpoints_call_admin_audit():
    src = _src()
    for verb in ("up", "down", "reset", "flash", "details"):
        # Each handler must call _admin_audit at least once.
        m = re.search(
            rf"def api_admin_iface_{verb}\(iface\)"
            rf"[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
            src,
        )
        assert m, f"Handler for {verb} not found"
        body = m.group(0)
        assert "_admin_audit(" in body, (
            f"api_admin_iface_{verb} doesn't log via _admin_audit"
        )


def test_iface_up_audits_ok_and_fail_paths():
    """All three exit branches (invalid_name, fail, ok) must log."""
    src = _src()
    m = re.search(
        r"def api_admin_iface_up\(iface\)[\s\S]+?(?=\ndef |\n@app)",
        src,
    )
    body = m.group(0)
    assert 'rc="invalid_name"' in body
    assert 'rc="fail"' in body
    assert 'rc="ok"' in body


def test_iface_down_audits_force_value():
    """Audit log records whether force=true was used — key
    operator question after a customer-facing port drops."""
    src = _src()
    m = re.search(
        r"def api_admin_iface_down\(iface\)[\s\S]+?(?=\ndef |\n@app)",
        src,
    )
    body = m.group(0)
    assert "force=force" in body


# ─── M11: journal token redaction ───


def test_journal_redaction_helper_defined():
    src = _src()
    assert "def _redact_journal_secrets(" in src
    assert "_RE_BEARER_TOKEN" in src
    assert "_RE_AUTH_HEADER" in src
    assert "_RE_AUTH_TOKEN_ENV" in src


def test_redaction_catches_bearer_token():
    """Smoke-test the actual scrubber against a representative
    journal line."""
    # Import the helpers from the file by executing it
    # carefully — only run if the file can be exec'd without
    # side effects. Simpler: regex-test the patterns directly.
    src = _src()
    m = re.search(r"_RE_BEARER_TOKEN\s*=\s*re\.compile\(\s*r?\"([^\"]+)\"", src)
    assert m
    pat = re.compile(m.group(1), re.IGNORECASE)
    sample = "Jun 11 02:30 host journal: Authorization: Bearer abc123XYZ"
    out = pat.sub(r"\1[REDACTED]", sample)
    assert "abc123XYZ" not in out, f"Token not redacted: {out}"
    assert "Bearer [REDACTED]" in out


def test_journal_endpoint_applies_redaction():
    src = _src()
    m = re.search(
        r"def admin_journal\([\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    assert "_redact_journal_secrets" in body, (
        "/api/admin/journal doesn't scrub before returning"
    )


# ─── M12: GRUB cp timeouts ───


def test_all_grub_cp_calls_have_timeout():
    src = _src()
    # Find every `subprocess.run(["cp", ...])` line and verify
    # there's a `timeout=` on the same statement (within a
    # reasonable window).
    for m in re.finditer(
        r'subprocess\.run\(\["cp"[\s\S]{0,200}?\)',
        src,
    ):
        block = m.group(0)
        assert "timeout=" in block, (
            f"cp call without timeout: {block!r}"
        )


# ─── SEC L1: generic flash error response ───


def test_flash_error_does_not_leak_str_e():
    """Audit SEC L1: the v0.5.91 flash handler did
    `jsonify({"error": str(_e)})` which leaks the resolved
    ethtool binary path under PermissionError. Fix returns a
    generic message and logs the full detail."""
    src = _src()
    m = re.search(
        r"def api_admin_iface_flash[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    assert '"failed to start ethtool"' in body, (
        "flash handler still returns raw str(e) — leaks Popen "
        "internals"
    )
    assert "logging.warning" in body, (
        "flash handler doesn't log the full exception detail"
    )


# ─── Version ───


def test_pyproject_version_at_least_0592():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 92)
