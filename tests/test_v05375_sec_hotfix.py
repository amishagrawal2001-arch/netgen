"""v0.5.375 SECURITY HOTFIX — AI subsystem + DB layer HIGH SEC.

Triaged from the parallel DB+AI audit. Fixes 8 HIGH-severity
findings across two subsystems:

  AI subsystem SEC:
    F11 — /api/ai/model/activate no auth + version path traversal
          → pickle load → RCE. Fix: @require_role("admin") +
          _ai_safe_model_version.
    F13 — /api/ai/pytest/run no auth + executes arbitrary content.
          Fix: @require_role("admin") + script_path/name defense.
    F14 — /api/ai/pytest/generate write-anywhere primitive. Fix:
          @require_role("admin") + _ai_safe_pytest_write_path.
    F15 — /api/ai/pytest/script/<name> path traversal on read /
          delete. Fix: @require_role("admin") + _ai_safe_script_name.
    F16 — /api/ai/settings openai_api_base SSRF + api-key exfil.
          Fix: @require_role("admin") + _ai_safe_api_base_url
          allowlist.
    F17 — /api/ai/chat no auth (budget burn). Fix:
          @require_role("operator").
    F18 — 65 of 67 /api/ai/* routes unauthenticated. Fix: sweep
          @require_role across every /api/ai/* route.

  DB layer SEC/correctness:
    F1  — restore_database uses shutil.copy2 while WAL sidecars
          from previous state remain → next open replays stale
          WAL onto restored file. Fix: sqlite3.Connection.backup
          + remove -wal / -shm sidecars.
    F2  — _run_migrations marks path applied BEFORE running body
          → mid-migration exception leaves permanent stale mark.
          Fix: except-clause removes path so retry re-runs.
    F3  — PRAGMA foreign_keys = ON only in init_database +
          remove_device → every other cascade path ran with FKs
          OFF, orphan rows accumulated. Fix: _connect_fk_on()
          helper wired into remove_route_pool / remove_dhcp_pool /
          remove_device_dhcp_pools / remove_device_route_pools /
          stream_database.delete_stream.

All sites carry marker `v0.5.375 (audit …)`.
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


# ─── AST sanity ───


def test_all_files_ast_parse():
    for _f in ("run_tgen_server.py",
               "utils/device_database.py",
               "utils/stream_database.py"):
        ast.parse(_read(_f))


# ─── AI @require_role coverage (F18) ───


def test_all_ai_routes_gated():
    """Every /api/ai/* route must be immediately followed by a
    @require_role decorator. This is the sweep test — zero
    ungated routes tolerated."""
    src = _read("run_tgen_server.py")
    lines = src.splitlines()
    route_re = re.compile(r'@app\.route\("(/api/ai[^"]+)"')
    ungated = []
    for i, line in enumerate(lines):
        m = route_re.search(line)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if not (j < len(lines) and
                lines[j].strip().startswith("@require_role")):
            ungated.append((i + 1, m.group(1)))
    assert not ungated, (
        f"Ungated /api/ai/* routes: "
        f"{', '.join(f'L{ln}:{path}' for ln, path in ungated)}"
    )


def test_ai_route_count_regressions():
    """At least 60 /api/ai/* routes should exist (67 as of audit).
    Bounded lower to catch accidental deletion, no upper bound so
    new endpoints can be added freely."""
    src = _read("run_tgen_server.py")
    routes = re.findall(r'@app\.route\("/api/ai[^"]+"', src)
    assert len(routes) >= 60, (
        f"Suspicious /api/ai/* count: {len(routes)} (expected ≥60)"
    )


# ─── AI SEC HIGH endpoints — specific role checks ───


def test_f11_model_activate_admin_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/model/activate"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "/api/ai/model/activate not gated to admin"


def test_f11_model_rollback_admin_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/model/rollback"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "/api/ai/model/rollback not gated to admin"


def test_f13_pytest_run_admin_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/pytest/run"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "/api/ai/pytest/run not gated to admin (RCE risk)"


def test_f14_pytest_generate_admin_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/pytest/generate"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "/api/ai/pytest/generate not gated to admin"


def test_f15_pytest_script_admin_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/pytest/script/<script_name>"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "/api/ai/pytest/script/<script_name> not gated to admin"


def test_f16_settings_post_admin_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/settings",\s*methods=\["POST"\][\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "POST /api/ai/settings not gated to admin"


def test_f17_chat_operator_gated():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/ai/chat"[\s\S]{0,600}?'
        r'@require_role\(\s*[\'"]operator[\'"]\s*\)',
        src,
    )
    assert m, "/api/ai/chat not gated (budget burn + prompt injection)"


# ─── Path traversal defenses ───


def test_helpers_defined():
    """The four SEC helpers must be present with the exact names
    the rest of the code expects."""
    src = _read("run_tgen_server.py")
    for _fn in ("_ai_safe_model_version",
                "_ai_safe_script_name",
                "_ai_safe_pytest_write_path",
                "_ai_safe_api_base_url"):
        assert f"def {_fn}(" in src, f"Missing SEC helper: {_fn}"


def test_helpers_wired_into_endpoints():
    """Verify each helper is CALLED from its target endpoint —
    not just defined. Match the WHOLE function body (bounded 6000
    chars) so we catch calls inside inner branches."""
    src = _read("run_tgen_server.py")

    def _body(fn_name, size=6000):
        _start = src.index(f"def {fn_name}(")
        return src[_start:_start + size]

    # F11 activate uses _ai_safe_model_version.
    assert "_ai_safe_model_version(version)" in _body("ai_model_activate")
    # F14 generate uses _ai_safe_pytest_write_path.
    assert "_ai_safe_pytest_write_path(file_path)" in _body("ai_generate_pytest")
    # F13 run uses BOTH pytest_write_path (for script_path) and
    # script_name defense.
    _run = _body("ai_run_pytest")
    assert "_ai_safe_pytest_write_path(script_path)" in _run
    assert "_ai_safe_script_name(script_name)" in _run
    # F15 script GET/DELETE uses _ai_safe_script_name.
    assert "_ai_safe_script_name(script_name)" in _body("ai_manage_pytest_script")


# ─── F16 URL allowlist ───


def test_openai_api_base_allowlist_enforced():
    """The allowlist must be applied INSIDE set_ai_settings before
    os.environ or ai_settings is touched."""
    src = _read("run_tgen_server.py")
    _start = src.index("def set_ai_settings():")
    body = src[_start:_start + 6000]
    assert "_ai_safe_api_base_url(" in body
    # Rejection path returns HTTP 400 with an error payload.
    assert "}), 400" in body


def test_allowlist_covers_default_providers():
    src = _read("run_tgen_server.py")
    for _h in ("api.openai.com", "openrouter.ai", "azure.com",
               "localhost", "127.0.0.1"):
        assert f'"{_h}"' in src, (
            f"Allowlist missing default provider host: {_h}"
        )


# ─── DB F1 restore ───


def test_db_restore_uses_connection_backup_not_shutil():
    """restore_database must call sqlite3 Connection.backup() and
    remove the WAL/SHM sidecars — not shutil.copy2 (pre-fix). The
    string 'shutil.copy2' still appears in the docstring as
    historical context; check the actual call form
    `shutil.copy2(` doesn't occur in executable code."""
    src = _read("utils/device_database.py")
    _start = src.index("def restore_database(self)")
    # Grab a bounded slice large enough to cover the whole method
    # (both the early-return and the actual restore path).
    body = src[_start:_start + 4000]
    # Strip the docstring (opens with triple-quote, closes with
    # triple-quote) so its historical mention of shutil.copy2
    # doesn't trip us.
    _stripped = re.sub(r'"""[\s\S]*?"""', "", body, count=1)
    assert "shutil.copy2(" not in _stripped, (
        "restore_database still calls shutil.copy2 — WAL replay bug"
    )
    # The right call.
    assert "src_conn.backup(dst_conn)" in body
    # And sidecar cleanup.
    assert "-wal" in body and "-shm" in body


# ─── DB F2 migration mark ───


def test_db_migration_removes_mark_on_failure():
    """The except body in _run_migrations must discard this
    db_path from _MIGRATIONS_APPLIED_FOR_PATHS so a retry on
    next construction actually re-runs the migration."""
    src = _read("utils/device_database.py")
    fn = re.search(
        r"def _run_migrations\(self, conn\)[\s\S]+?def add_device",
        src,
    )
    assert fn
    body = fn.group(0)
    # The exception path must remove-on-failure.
    assert "_MIGRATIONS_APPLIED_FOR_PATHS.discard(self.db_path)" in body


# ─── DB F3 foreign_keys ON ───


def test_db_connect_fk_on_helper_defined():
    src = _read("utils/device_database.py")
    assert "def _connect_fk_on(self):" in src
    m = re.search(
        r"def _connect_fk_on\(self\)[\s\S]+?return conn",
        src,
    )
    assert m
    body = m.group(0)
    assert 'PRAGMA foreign_keys = ON' in body


def test_db_cascade_sites_use_fk_helper():
    """The 4 cascade sites in device_database.py must use
    self._connect_fk_on() not raw sqlite3.connect."""
    src = _read("utils/device_database.py")
    for _fn in ("remove_route_pool", "remove_dhcp_pool",
                "remove_device_dhcp_pools",
                "remove_device_route_pools"):
        fn = re.search(
            rf"def {_fn}\(self[^)]*\)[\s\S]+?return False",
            src,
        )
        assert fn, f"Couldn't locate {_fn}"
        body = fn.group(0)
        assert "self._connect_fk_on()" in body, (
            f"{_fn} still opens a vanilla sqlite3.connect (FKs OFF)"
        )


def test_stream_delete_pragma_fk_on():
    """stream_database.delete_stream must set PRAGMA foreign_keys
    = ON on its connection so stream_stats cascade fires."""
    src = _read("utils/stream_database.py")
    fn = re.search(
        r"def delete_stream\(self, stream_id: str\)[\s\S]+?return False",
        src,
    )
    assert fn
    body = fn.group(0)
    assert "PRAGMA foreign_keys = ON" in body


# ─── version guard ───


def test_pyproject_version_at_least_0575():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 375), (
        f"Version {m.group(1)} < 0.5.375"
    )


# ─── regression guards ───


def test_v0374_admin_upgrade_wheel_card_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.374 (audit admin-upgrade-wheel-card)" in src


def test_v0370_health_running_version_intact():
    src = _read("run_tgen_server.py")
    m = re.search(
        r"def api_health\(\)[\s\S]+?return jsonify\(\{[\s\S]+?\}\)",
        src,
    )
    assert m and '"running_version"' in m.group(0)


def test_v0365_route_auth_sweep_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.365" in src  # rough regression marker


def test_v0372_c2_remove_device_verify_before_commit_intact():
    """v0.5.372 C2 fix (verify BEFORE commit) mustn't regress —
    v0.5.375 didn't touch remove_device itself but did touch
    surrounding code."""
    src = _read("utils/device_database.py")
    assert "v0.5.372 (audit device-db-rollback-after-commit)" in src
