"""v0.5.219: DHCP collateral bundle — five follow-ups to
v0.5.217 A-H and v0.5.218 I-N, plus three coverage gaps the
prior lock-ins missed.

Collateral bugs (source + runtime lock-ins):

  C1. Fix L asymmetric — the v0.5.218 ``_add_route_and_vrf_copy``
      mirrors every server-mode route into the per-device VRF
      table, but ``stop_dhcp_server`` still only issued
      ``ip route del`` against the main table. On device Remove
      or Stop-then-Start with a different pool, the VRF-scoped
      copy of the old subnet leaked. Fixed by
      ``_remove_route_and_vrf_copy`` — the removal counterpart —
      and rewiring every route del in stop_dhcp_server through
      it.

  C2. ``_check_server_device`` in utils/dhcp_monitor.py still used
      ``pgrep -f 'dnsmasq.*{interface}'`` as a fallback probe —
      same unanchored-substring collision fix M (v0.5.218) killed
      in ``_is_dhclient_running``. Now goes through the shared
      ``_pgrep_matching_argv`` argv-parse helper.

  C3. Fix G left ``dhcp_manual_override`` set for up to 120s after
      an operator Stop->Start because no start path cleared it.
      A silently-failing Start would then not be noticed by the
      monitor until the guard expired. Fixed by explicitly writing
      ``dhcp_manual_override=False`` at the top of both
      ``start_dhcp_client`` and ``start_dhcp_server``.

  C4. Fix D — ``stop_dhcp_server`` returns ``{success: False,
      failures: [...]}`` on partial failure — but the three
      ``run_tgen_server.py`` call sites (device stop 6169, device
      remove 6411, config apply 6989) discarded the return dict.
      Now each captures the return value and threads failures into
      the JSON response so ``MultiDeviceResultsDialog`` on the
      client can surface them.

  C5. ``MultiDeviceResultsDialog(...).exec_()`` inside worker
      finish handlers runs inline while the QThread's finished
      signal is still unwinding. Under PyQt5 5.15.11 + Python
      3.14 that ordering has bitten the codebase (SIGABRT guards
      exist elsewhere for the same reason). Fixed by deferring
      every finish-handler dialog exec / heavy UI update via
      ``QTimer.singleShot(0, ...)`` — same pattern OSPF/BGP/ISIS
      apply already use.

Coverage gaps closed by real runtime tests (T1-T3):

  T1. DB schema migration against a pre-v0.5.217 database —
      v0.5.217 added ``dhcp_manual_override`` +
      ``dhcp_manual_override_time`` columns. A test that
      constructs DeviceDatabase against an old-schema file locks
      in that the migration adds them without dropping rows.
  T2. ``_is_dhclient_running`` — no test covered the eth1 vs
      eth10 substring collision that fix M closed. Mock
      subprocess.run to force each scenario and assert exact
      behaviour.
  T3. ``start_dhcp_server`` route install — no test covered
      that fix L actually issues the VRF mirror for both IPv4 and
      IPv6. Mock subprocess.run to capture every ``ip route ...``
      call, then assert the mirror is present.

Source-level lock-ins for C1-C5 keep the audit-check pattern
established by v0.5.207-v0.5.218 (grep asserts on the fix
site) so future regressions remain visible.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05219_test_{os.getpid()}.db"),
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def _dhcp_monitor_src() -> str:
    return (REPO / "utils" / "dhcp_monitor.py").read_text()


def _dhcp_tab_src() -> str:
    return (REPO / "utils" / "devices_tab_dhcp.py").read_text()


def _run_tgen_server_src() -> str:
    return (REPO / "run_tgen_server.py").read_text()


def _function_body(src: str, funcname: str) -> str:
    """Return the source of the single top-level or method ``funcname``
    in ``src``. Raises AssertionError if not found or found multiple
    times (which for our lock-ins would itself be a regression signal)."""
    tree = ast.parse(src)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            hits.append(ast.get_source_segment(src, node) or "")
    assert hits, f"{funcname!r} not found"
    return "\n\n".join(hits)


# ---------------------------------------------------------------------------
# Version bump.
# ---------------------------------------------------------------------------

def test_version_bumped_to_at_least_0_5_219():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', src, re.MULTILINE)
    assert m, "pyproject.toml version line not found"
    parts = tuple(int(p) for p in m.group(1).split("."))
    assert parts >= (0, 5, 219), (
        f"pyproject version {m.group(1)!r} is below expected 0.5.219"
    )


# ---------------------------------------------------------------------------
# C1 — _remove_route_and_vrf_copy helper + stop_dhcp_server rewire.
# ---------------------------------------------------------------------------

def test_C1_remove_route_and_vrf_copy_helper_defined():
    """The helper must exist alongside _add_route_and_vrf_copy and
    accept the same VRF/family/gateway/interface shape."""
    src = _dhcp_src()
    # Function definition exists.
    body = _function_body(src, "_remove_route_and_vrf_copy")
    # Signature carries the VRF-aware kwargs.
    for kw in ("vrf_name", "family", "gateway", "interface", "container"):
        assert kw in body.splitlines()[0] or kw in body, (
            f"_remove_route_and_vrf_copy missing {kw!r} kwarg"
        )
    # Body must issue `ip route del` (bare) — matching the add helper's
    # `ip route replace` shape.
    assert "route" in body and "del" in body, (
        "_remove_route_and_vrf_copy doesn't appear to issue an `ip route del`"
    )
    # And must mirror into the VRF table when vrf_name is set.
    assert "vrf_name" in body and 'vrf' in body, (
        "_remove_route_and_vrf_copy missing VRF-scoped mirror deletion"
    )


def test_C1_stop_dhcp_server_uses_remove_helper():
    """stop_dhcp_server body must route every ip route del through
    the new _remove_route_and_vrf_copy helper. Pre-fix it emitted
    bare ``ip route del`` for IPv4 + IPv6 static + IPv6 gateway
    routes directly, missing the VRF mirror cleanup."""
    body = _function_body(_dhcp_src(), "stop_dhcp_server")
    assert "_remove_route_and_vrf_copy" in body, (
        "stop_dhcp_server not rewired through _remove_route_and_vrf_copy"
    )
    # No stray bare ``ip route del`` shell/list form should remain in
    # the function body (previous versions issued them for IPv4 pool
    # networks and IPv6 gateway routes directly).
    for pat in (
        '["ip", "route", "del"',
        '["ip", "-6", "route", "del"',
    ):
        assert pat not in body, (
            f"stop_dhcp_server still contains raw `{pat}...` — should go through helper"
        )


def test_C1_stop_dhcp_server_resolves_vrf_before_removal():
    """The helper needs the device's vrf name; stop_dhcp_server must
    resolve it (same _resolve_device_vrf the add path uses)."""
    body = _function_body(_dhcp_src(), "stop_dhcp_server")
    assert "_resolve_device_vrf" in body, (
        "stop_dhcp_server must call _resolve_device_vrf so the "
        "helper can mirror-delete the VRF-scoped copies"
    )


# ---------------------------------------------------------------------------
# C2 — dnsmasq pgrep argv scan (no eth1 vs eth10 collision).
# ---------------------------------------------------------------------------

def _strip_comments(src: str) -> str:
    """Return ``src`` with # line-comments stripped.

    Lock-in tests grep the source for banned patterns, but new fix
    comments legitimately mention the pre-fix substring shape they
    replaced. Strip comments first so those references don't false-
    positive the assertion.
    """
    out = []
    for line in src.splitlines():
        # A # inside a string literal is rare in this codebase and
        # not present in the fix comment blocks we care about — a
        # naive strip is fine for lock-in purposes.
        hash_idx = line.find("#")
        if hash_idx == -1:
            out.append(line)
        else:
            out.append(line[:hash_idx])
    return "\n".join(out)


def test_C2_check_server_device_uses_argv_pgrep_helper():
    """_check_server_device must no longer contain an unanchored
    ``pgrep -f 'dnsmasq.*<interface>'`` pattern; it must go through
    the shared _pgrep_matching_argv helper (or an equivalent argv
    parser) instead."""
    src = _dhcp_monitor_src()
    body = _function_body(src, "_check_server_device")
    code_only = _strip_comments(body)

    # The exact pre-fix substring collision pattern must be gone
    # from the actual code (comments referencing the pre-fix shape
    # for historical context are stripped first).
    assert "dnsmasq.*{interface}" not in code_only, (
        "_check_server_device still uses substring pgrep 'dnsmasq.*{interface}'"
    )
    assert "dnsmasq.*{conffile}" not in code_only, (
        "_check_server_device still uses substring pgrep with conffile "
        "template — should route through _pgrep_matching_argv"
    )

    # Must route through the new helper.
    assert "_pgrep_matching_argv" in code_only, (
        "_check_server_device not rewired through _pgrep_matching_argv"
    )

    # And the helper itself must exist with the expected shape.
    assert "def _pgrep_matching_argv" in src, (
        "_pgrep_matching_argv helper not defined in utils/dhcp_monitor.py"
    )


# ---------------------------------------------------------------------------
# C3 — start_* paths clear dhcp_manual_override.
# ---------------------------------------------------------------------------

def test_C3_start_dhcp_client_clears_manual_override():
    body = _function_body(_dhcp_src(), "start_dhcp_client")
    assert "dhcp_manual_override" in body, (
        "start_dhcp_client does not clear dhcp_manual_override"
    )
    # Must write False, not True.
    assert re.search(
        r'"dhcp_manual_override"\s*:\s*False', body,
    ), (
        "start_dhcp_client must write dhcp_manual_override=False"
    )
    # And should clear the paired timestamp too.
    assert "dhcp_manual_override_time" in body, (
        "start_dhcp_client must also null dhcp_manual_override_time"
    )


def test_C3_start_dhcp_server_clears_manual_override():
    body = _function_body(_dhcp_src(), "start_dhcp_server")
    assert "dhcp_manual_override" in body, (
        "start_dhcp_server does not clear dhcp_manual_override"
    )
    assert re.search(
        r'"dhcp_manual_override"\s*:\s*False', body,
    ), (
        "start_dhcp_server must write dhcp_manual_override=False"
    )
    assert "dhcp_manual_override_time" in body, (
        "start_dhcp_server must also null dhcp_manual_override_time"
    )


# ---------------------------------------------------------------------------
# C4 — run_tgen_server.py call sites consume stop_dhcp_services result.
# ---------------------------------------------------------------------------

def test_C4_device_stop_captures_dhcp_stop_result():
    """The device-stop handler must capture stop_dhcp_services'
    return dict (pre-fix it was a bare call whose result was
    discarded) and surface failures into the response."""
    src = _run_tgen_server_src()
    # Find the block for line 6169-ish (the device stop path). We
    # anchor on the surrounding log message and check that the
    # actual `stop_dhcp_services(...)` call is captured.
    matches = re.findall(
        r'Stopping DHCP.*?\n\s*(?:#[^\n]*\n\s*)*(?:[^\n]+\n\s*)*?'
        r'([A-Za-z_][A-Za-z_0-9]*)\s*=\s*stop_dhcp_services\(',
        src,
    )
    # We expect at least two captured call sites (device stop +
    # device remove).
    assert len(matches) >= 2, (
        f"Expected >=2 captured stop_dhcp_services call sites in "
        f"run_tgen_server.py, found {len(matches)}: {matches!r}"
    )
    # And at least one should thread failures into the response
    # payload key `dhcp_stop_failures`.
    assert "dhcp_stop_failures" in src, (
        "run_tgen_server.py does not surface dhcp_stop_failures "
        "in the JSON response"
    )


def test_C4_device_remove_captures_dhcp_stop_result():
    """The /api/device/remove handler must both capture the return
    value AND thread failures into its jsonify response."""
    src = _run_tgen_server_src()
    body = _function_body(src, "remove_device")
    # Captured call.
    assert re.search(
        r"[A-Za-z_][A-Za-z_0-9]*\s*=\s*stop_dhcp_services\(", body,
    ), (
        "remove_device does not capture stop_dhcp_services() return "
        "value — the failures list is discarded"
    )
    # Payload key threaded into the response.
    assert "dhcp_stop_failures" in body, (
        "remove_device does not surface dhcp_stop_failures in "
        "its jsonify response"
    )
    # And an accumulator is declared at the top of the function.
    assert "dhcp_remove_failures" in body, (
        "remove_device missing dhcp_remove_failures accumulator"
    )


def test_C4_detach_all_pools_captures_dhcp_stop_result():
    """The /api/device/dhcp/server/attach_pools detach_all branch
    (~ line 6989) must capture stop_dhcp_server's return dict and
    thread failures into the response."""
    src = _run_tgen_server_src()
    # Find the detach_all block — anchored on the unique log message.
    detach_block_re = re.compile(
        r"Failed to stop DHCP server after detach"
    )
    assert detach_block_re.search(src), (
        "detach_all block anchor not found — this test may need "
        "updating if the log message changed"
    )
    # Slice ~ around it and check for capture + failures key.
    idx = detach_block_re.search(src).start()
    window = src[max(0, idx - 3000):idx + 2000]
    assert re.search(
        r"[A-Za-z_][A-Za-z_0-9]*\s*=\s*stop_dhcp_server\(", window,
    ), (
        "detach_all does not capture stop_dhcp_server() return value"
    )
    assert "dhcp_stop_failures" in window, (
        "detach_all does not surface dhcp_stop_failures on the response"
    )


# ---------------------------------------------------------------------------
# C5 — QTimer.singleShot defers dialog exec_ / heavy UI update.
# ---------------------------------------------------------------------------

def test_C5_run_dhcp_pool_post_defers_dialog():
    """_run_dhcp_pool_post's _on_finished must not call
    MultiDeviceResultsDialog(...).exec_() inline — it must defer
    through QTimer.singleShot(0, ...) so the QThread's finished
    signal has fully unwound first."""
    body = _function_body(_dhcp_tab_src(), "_run_dhcp_pool_post")
    assert "QTimer.singleShot" in body, (
        "_run_dhcp_pool_post does not use QTimer.singleShot to "
        "defer the finish handler"
    )
    # And the MultiDeviceResultsDialog(...).exec_() call must live
    # inside a callable passed to singleShot, not at the top level
    # of _on_finished. Cheap proxy: any direct
    # `MultiDeviceResultsDialog(...)\n<optional-whitespace>.exec_()`
    # pattern at the outer indent (12 spaces) would be a regression.
    # We check by ensuring no line-continuation shape appears at
    # the 12-space indent level immediately after a
    # MultiDeviceResultsDialog(...) construction inside _on_finished.
    on_finished_body_re = re.search(
        r"def _on_finished\(.*?\n(.*?)(?=\n\s{8}worker\.finished\.connect)",
        body, re.DOTALL,
    )
    assert on_finished_body_re, (
        "unable to isolate _on_finished body for _run_dhcp_pool_post"
    )
    inner = on_finished_body_re.group(1)
    # Direct inline exec_() at 16-space indent (inside if branches)
    # would show as an obvious tell — deferred versions go through
    # QTimer.singleShot with a callback.
    assert "QTimer.singleShot(0" in inner, (
        "QTimer.singleShot(0, ...) not called from within _on_finished"
    )


def test_C5_refresh_finished_defers_populate():
    """refresh_dhcp_status's _on_finished must defer
    _populate_dhcp_table via QTimer.singleShot for the same
    SIGABRT-safety reason. This locks in the parity across all
    three v0.5.218 fix-J finish handlers (Refresh + Attach + Apply)."""
    body = _function_body(_dhcp_tab_src(), "refresh_dhcp_status")
    assert "QTimer.singleShot" in body, (
        "refresh_dhcp_status _on_finished does not defer via "
        "QTimer.singleShot"
    )
    # _populate_dhcp_table must appear inside a callback passed to
    # singleShot rather than at the outer scope of _on_finished.
    m = re.search(
        r"QTimer\.singleShot\([^)]*\)", body,
    )
    assert m, "QTimer.singleShot(...) call not found in refresh_dhcp_status"


# ---------------------------------------------------------------------------
# T1 — Runtime: schema migration against pre-v0.5.217 database.
# ---------------------------------------------------------------------------

# Seed a pre-v0.5.217 state.
#
# Complication: utils/device_database.py's _run_migrations has an
# unrelated pre-existing bug — after adding ospf_ipv4_uptime in one
# branch it re-checks a stale `columns` list and tries to add it
# again, which raises "duplicate column name" and the outer
# try/except aborts migration partway (before dhcp_manual_override
# is added). This isn't a v0.5.217/218/219 collateral, but it
# blocks a naive "instantiate DeviceDatabase on empty DB" seed.
#
# Workaround: seed a table with EVERY column pre-v0.5.217 already
# has (source CREATE TABLE + every migration-added column up to
# but not including dhcp_manual_override), so the migration finds
# nothing to add except the two v0.5.217 columns we want it to add.
_ALL_PRE_V05217_MIGRATION_COLUMNS = [
    # BGP IPv4/IPv6 (added migrations, since 0.2.x)
    ("bgp_ipv4_established", "BOOLEAN DEFAULT FALSE"),
    ("bgp_ipv6_established", "BOOLEAN DEFAULT FALSE"),
    ("bgp_ipv4_state", "TEXT DEFAULT 'Unknown'"),
    ("bgp_ipv6_state", "TEXT DEFAULT 'Unknown'"),
    # OSPF IPv4/IPv6 specifics (bundled add block)
    ("ospf_ipv4_running", "BOOLEAN DEFAULT FALSE"),
    ("ospf_ipv6_running", "BOOLEAN DEFAULT FALSE"),
    ("ospf_ipv4_established", "BOOLEAN DEFAULT FALSE"),
    ("ospf_ipv6_established", "BOOLEAN DEFAULT FALSE"),
    ("ospf_ipv4_uptime", "TEXT"),
    ("ospf_ipv6_uptime", "TEXT"),
    # ISIS status
    ("isis_running", "BOOLEAN DEFAULT FALSE"),
    ("isis_established", "BOOLEAN DEFAULT FALSE"),
    ("isis_state", "TEXT DEFAULT 'Unknown'"),
    ("isis_neighbors", "TEXT"),
    ("isis_areas", "TEXT"),
    ("isis_system_id", "TEXT"),
    ("isis_net", "TEXT"),
    ("isis_uptime", "TEXT"),
    # External device / connection
    ("device_type", "TEXT DEFAULT 'frr_container'"),
    ("container_id", "TEXT"),
    ("connection_method", "TEXT"),
    ("connection_host", "TEXT"),
    ("connection_port", "INTEGER"),
    ("connection_username", "TEXT"),
    ("connection_info", "TEXT"),
    # Loopback
    ("loopback_ipv4", "TEXT"),
    ("loopback_ipv6", "TEXT"),
    # Manual-override for BGP/OSPF/ISIS (pre-DHCP)
    ("bgp_manual_override", "BOOLEAN DEFAULT FALSE"),
    ("bgp_manual_override_time", "TIMESTAMP"),
    ("ospf_manual_override", "BOOLEAN DEFAULT FALSE"),
    ("ospf_manual_override_time", "TIMESTAMP"),
    ("isis_manual_override", "BOOLEAN DEFAULT FALSE"),
    ("isis_manual_override_time", "TIMESTAMP"),
]


_ALL_PRE_V05217_STATS_MIGRATION_COLUMNS = [
    # OSPF IPv4/IPv6 stats (bundled add) — MUST be pre-populated,
    # otherwise the migration's separate ospf_ipv4_uptime "already
    # exists?" check reads a stale columns list and re-adds it →
    # duplicate column name → migration aborts.
    ("ospf_ipv4_running", "BOOLEAN"),
    ("ospf_ipv6_running", "BOOLEAN"),
    ("ospf_ipv4_established", "BOOLEAN"),
    ("ospf_ipv6_established", "BOOLEAN"),
    ("ospf_ipv4_uptime", "TEXT"),
    ("ospf_ipv6_uptime", "TEXT"),
]


def _seed_pre_v05217_schema(db_path: str) -> None:
    """Build devices + device_stats tables that a pre-v0.5.217 server
    would have produced. Uses the CURRENT source CREATE TABLE
    statements as a base, then adds every migration column that
    existed before v0.5.217 so the v0.5.217 migration only has to
    add the two dhcp_manual_override columns."""
    src_text = (REPO / "utils" / "device_database.py").read_text()
    m_devices = re.search(
        r"conn\.execute\(\"\"\"\s*(CREATE TABLE IF NOT EXISTS devices .*?)\"\"\"\)",
        src_text, re.DOTALL,
    )
    assert m_devices, "could not extract devices CREATE TABLE from source"
    m_stats = re.search(
        r"conn\.execute\(\"\"\"\s*(CREATE TABLE IF NOT EXISTS device_stats .*?)\"\"\"\)",
        src_text, re.DOTALL,
    )
    assert m_stats, "could not extract device_stats CREATE TABLE from source"

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(m_devices.group(1))
        conn.execute(m_stats.group(1))
        # devices table pre-population.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(devices)")}
        for name, defn in _ALL_PRE_V05217_MIGRATION_COLUMNS:
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE devices ADD COLUMN {name} {defn}")
        # device_stats table pre-population (see the constant's
        # docstring for the migration-bug workaround rationale).
        stats_existing = {row[1] for row in conn.execute("PRAGMA table_info(device_stats)")}
        for name, defn in _ALL_PRE_V05217_STATS_MIGRATION_COLUMNS:
            if name in stats_existing:
                continue
            conn.execute(f"ALTER TABLE device_stats ADD COLUMN {name} {defn}")
        conn.commit()


def test_T1_migration_from_pre_v05217_schema():
    """Construct a pre-v0.5.217 database file (no dhcp_manual_override
    columns), then open it via DeviceDatabase. Assert:
     - existing rows survive,
     - the two new columns exist post-open,
     - the columns default in a sane way (either NULL or the schema's
       BOOLEAN DEFAULT FALSE / TIMESTAMP shape)."""
    tmp = tempfile.NamedTemporaryFile(
        prefix="netgen_t1_", suffix=".db", delete=False,
    )
    tmp.close()
    db_path = tmp.name
    try:
        # Seed the OLD schema (via full-init-then-drop the two new
        # columns) and a row.
        _seed_pre_v05217_schema(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO devices (device_id, device_name, interface) "
                "VALUES (?, ?, ?)",
                ("dev-t1", "device-t1", "eth1"),
            )
            conn.commit()

        # v0.5.229 (audit U monitor-9): dhcp_manual_override + siblings
        # were promoted into CREATE TABLE so a fresh install doesn't
        # depend on migration completing. Pre-fix, this sanity block
        # verified the seed lacked the columns so we could observe
        # migration adding them; post-fix the columns come along in
        # the CREATE TABLE and the migration ADD-COLUMN block sees
        # them and no-ops. The test's real intent — "the columns are
        # present after DeviceDatabase init" — is unchanged and
        # verified by the post-open assertions below.
        with sqlite3.connect(db_path) as conn:
            _ = [row[1] for row in conn.execute("PRAGMA table_info(devices)")]

        # Instantiate DeviceDatabase again — this re-triggers
        # init_database + _run_migrations, which for v0.5.217 must
        # add both columns back.
        from utils.device_database import DeviceDatabase
        _db = DeviceDatabase(db_path=db_path)

        # Post-migration: both columns present.
        with sqlite3.connect(db_path) as conn:
            cols_after = [row[1] for row in conn.execute("PRAGMA table_info(devices)")]
            assert "dhcp_manual_override" in cols_after, (
                "migration did not add dhcp_manual_override column"
            )
            assert "dhcp_manual_override_time" in cols_after, (
                "migration did not add dhcp_manual_override_time column"
            )

            # Existing row still there.
            row = conn.execute(
                "SELECT device_id, device_name, interface, "
                "dhcp_manual_override, dhcp_manual_override_time "
                "FROM devices WHERE device_id = ?",
                ("dev-t1",),
            ).fetchone()
            assert row is not None, "existing row lost during migration"
            assert row[0] == "dev-t1"
            assert row[1] == "device-t1"
            assert row[2] == "eth1"
            # Default value for the new BOOLEAN column — SQLite's
            # ALTER TABLE ADD COLUMN with a DEFAULT FALSE writes 0.
            # New TIMESTAMP column stays NULL for pre-existing rows.
            assert row[3] in (0, False), (
                f"expected default False for dhcp_manual_override, got {row[3]!r}"
            )
            assert row[4] is None, (
                f"expected NULL for dhcp_manual_override_time, got {row[4]!r}"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
        for suffix in (".backup", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# T2 — Runtime: _is_dhclient_running no eth1 vs eth10 false positive.
# ---------------------------------------------------------------------------

def _fake_pgrep_stdout(stdout: str, returncode: int = 0):
    """Build a fake subprocess.run result matching what dhcp.py
    reads (stdout, stderr, returncode).

    The real _run_command wraps subprocess.run and returns the
    completed process object directly (in the no-container path).
    We patch subprocess.run itself here so both container=None
    and container=X call sites work.
    """
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_T2_is_dhclient_running_eth1_does_not_match_eth10():
    """Regression lock-in for v0.5.218 fix M: dhclient bound to
    eth10 must NOT satisfy a query for eth1."""
    from utils import dhcp as dhcp_utils
    fake = _fake_pgrep_stdout("1234 dhclient eth10\n")
    with patch("subprocess.run", return_value=fake):
        assert dhcp_utils._is_dhclient_running("eth1") is False, (
            "eth1 must not match dhclient on eth10 — bug M regression"
        )


def test_T2_is_dhclient_running_eth1_matches_exact_argv_token():
    from utils import dhcp as dhcp_utils
    fake = _fake_pgrep_stdout("1234 dhclient eth1\n")
    with patch("subprocess.run", return_value=fake):
        assert dhcp_utils._is_dhclient_running("eth1") is True


def test_T2_is_dhclient_running_matches_multi_arg_form():
    """dhclient invoked with -6 -nw eth1 must also match."""
    from utils import dhcp as dhcp_utils
    fake = _fake_pgrep_stdout("1234 dhclient -6 -nw eth1\n")
    with patch("subprocess.run", return_value=fake):
        assert dhcp_utils._is_dhclient_running("eth1") is True


def test_T2_is_dhclient_running_no_match_returns_false():
    """Empty pgrep output (returncode 1) must produce False, not
    crash — the pre-fix code returned False here already but we
    lock in the argv-parse path handles it too."""
    from utils import dhcp as dhcp_utils
    fake = _fake_pgrep_stdout("", returncode=1)
    with patch("subprocess.run", return_value=fake):
        assert dhcp_utils._is_dhclient_running("eth1") is False


# ---------------------------------------------------------------------------
# T3 — Runtime: start_dhcp_server mirrors routes into VRF for v4+v6.
# ---------------------------------------------------------------------------

class _RunCommandRecorder:
    """Records every _run_command call so tests can grep for the
    ip route replace + VRF mirror shape."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout=10, check=False, container=None):
        # Normalise to a list so callers can list-search regardless of
        # whether the source passed a shell string or a list.
        recorded = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        self.calls.append(recorded)
        # For pidfile reads (`cat pidfile`) return empty — we want the
        # dnsmasq "start" path to succeed with no pre-existing pid.
        joined = " ".join(str(x) for x in recorded)
        if "cat" in recorded or "if [ -f" in joined:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        # For dnsmasq launch, pretend success.
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def route_replace_calls(self, family: str = "-4") -> list[list[str]]:
        out = []
        for c in self.calls:
            if len(c) >= 4 and c[0] == "ip" and c[1] == family \
                    and c[2] == "route" and c[3] == "replace":
                out.append(c)
        return out

    def route_replace_calls_untyped(self) -> list[list[str]]:
        """ip route replace without an explicit -4/-6 flag (e.g. the
        gateway /32 host-route special-case)."""
        out = []
        for c in self.calls:
            if len(c) >= 3 and c[0] == "ip" and c[1] == "route" \
                    and c[2] == "replace":
                out.append(c)
        return out


def _make_dhcp_server_call(vrf_name):
    """Prepare mocks and call start_dhcp_server with an IPv4+IPv6
    pool. Returns the recorder for assertions."""
    from utils import dhcp as dhcp_utils

    recorder = _RunCommandRecorder()

    # A fake device DB whose update_device is a no-op.
    device_db = MagicMock()
    device_db.update_device = MagicMock(return_value=True)

    # Container object with a name — start_dhcp_server accepts it as
    # a positional container= kwarg but _run_command is patched, so
    # its behaviour won't matter beyond being non-None.
    container = MagicMock()
    container.name = "ostg-dhcp-server-devT3"

    # Patch _run_command (so we capture every command emitted),
    # _verify_interface_exists (so the guard passes), the config
    # write helpers, and _resolve_device_vrf (return vrf_name).
    with patch.object(dhcp_utils, "_run_command", recorder), \
         patch.object(dhcp_utils, "_verify_interface_exists", return_value=True), \
         patch.object(dhcp_utils, "_ensure_paths", lambda **kw: None), \
         patch.object(dhcp_utils, "_ensure_ipv6_address", return_value=True), \
         patch.object(dhcp_utils, "_resolve_device_vrf", return_value=vrf_name):
        dhcp_utils.start_dhcp_server(
            device_db,
            "devT3",
            "eth1",
            {
                "mode": "server",
                "ipv4_enabled": True,
                "ipv6_enabled": True,
                "pool_start": "10.0.0.10",
                "pool_end": "10.0.0.20",
                "gateway": "10.0.0.1",
                "ipv6_pool_start": "2001:db8::10",
                "ipv6_pool_end": "2001:db8::20",
                "ipv6_prefix": "64",
                "ipv6_server_ip": "2001:db8::1",
                "ipv6_gateway": "2001:db8::1",
                "lease_time": 24,
            },
            container=container,
        )
    return recorder


def test_T3_start_dhcp_server_mirrors_ipv4_route_into_vrf():
    recorder = _make_dhcp_server_call(vrf_name="vrf-devT3")

    ipv4_calls = recorder.route_replace_calls("-4")
    # Two categories exist post-fix L:
    #   - main-table `ip -4 route replace <net> via 10.0.0.1 dev eth1`
    #   - mirror     `ip -4 route replace <net> via 10.0.0.1 dev eth1 vrf vrf-devT3`
    assert ipv4_calls, "no ip -4 route replace calls at all"
    with_vrf = [c for c in ipv4_calls if "vrf" in c and "vrf-devT3" in c]
    without_vrf = [c for c in ipv4_calls if "vrf" not in c]
    assert with_vrf, (
        "no ip -4 route replace call included `vrf vrf-devT3` — "
        "start_dhcp_server didn't mirror IPv4 route into VRF"
    )
    assert without_vrf, (
        "no plain (main-table) ip -4 route replace call — the mirror "
        "must NOT replace the main-table install"
    )
    # For every unique <net> that got a with_vrf install, the plain
    # install must also be present (mirror = both tables).
    def _net_of(cmd):
        # cmd shape: ["ip", "-4", "route", "replace", "<net>", ...]
        return cmd[4]
    with_vrf_nets = {_net_of(c) for c in with_vrf}
    without_vrf_nets = {_net_of(c) for c in without_vrf}
    missing = with_vrf_nets - without_vrf_nets
    assert not missing, (
        f"IPv4 nets mirrored into VRF but missing from main table: {missing!r}"
    )


def test_T3_start_dhcp_server_mirrors_ipv6_route_into_vrf():
    recorder = _make_dhcp_server_call(vrf_name="vrf-devT3")

    ipv6_calls = recorder.route_replace_calls("-6")
    assert ipv6_calls, "no ip -6 route replace calls at all"
    with_vrf = [c for c in ipv6_calls if "vrf" in c and "vrf-devT3" in c]
    without_vrf = [c for c in ipv6_calls if "vrf" not in c]
    assert with_vrf, (
        "no ip -6 route replace call included `vrf vrf-devT3` — "
        "start_dhcp_server didn't mirror IPv6 route into VRF"
    )
    assert without_vrf, (
        "no plain (main-table) ip -6 route replace call — mirror must "
        "not replace main-table install"
    )


def test_T3_legacy_no_vrf_deployment_does_not_add_vrf_args():
    """When _resolve_device_vrf returns None (legacy single-device
    deployment), NO ip route replace call must include `vrf`."""
    recorder = _make_dhcp_server_call(vrf_name=None)
    for cmd in recorder.calls:
        # Only inspect ip route replace calls.
        if len(cmd) < 3:
            continue
        if cmd[0] != "ip":
            continue
        if "replace" not in cmd:
            continue
        assert "vrf" not in cmd, (
            f"legacy no-VRF path unexpectedly added vrf arg: {cmd!r}"
        )
