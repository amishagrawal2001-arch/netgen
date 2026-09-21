"""v0.5.396 — server-side streams endpoints audit (5 items).

  M1  Dangling @app.route('/api/streams/register') deleted.
  M2  launch_single_stream TOCTOU guard via _launch_reservations.
  M3  J1 mirror — DB Stopped while running gets counter-trust override.
  M4  /api/traffic/stop last-resort mass-stop removed.
  M5  Role gates added to 5 unprotected endpoints.
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


def _strip_comments(src: str) -> str:
    """Drop full-line python comments so decorator-string mentions
    inside a comment don't create false positives."""
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


# ─── M1: dangling decorator deleted ───


def test_m1_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.396 (audit streams M1)" in src


def test_m1_dangling_decorator_deleted():
    """Ensure only ONE @app.route('/api/streams/register') decorator
    exists in the file (excluding comment mentions), and it lives
    on register_streams (not dangling above advertise_bgp_routes)."""
    src = _strip_comments(_read("run_tgen_server.py"))
    _routes = re.findall(
        r"@app\.route\(['\"]/api/streams/register['\"]",
        src,
    )
    assert len(_routes) == 1, (
        f"expected exactly 1 @app.route('/api/streams/register') "
        f"in non-comment code; got {len(_routes)}"
    )
    _idx = src.index("@app.route('/api/streams/register'")
    _tail = src[_idx:_idx + 500]
    assert "def register_streams" in _tail, (
        "surviving /api/streams/register decorator must decorate "
        "register_streams, not something else"
    )


def test_m1_bgp_route_intact():
    """advertise_bgp_routes must still be reachable via
    /api/bgp/routes/advertise, undecorated by the deleted dangling
    line above it."""
    src = _read("run_tgen_server.py")
    _idx = src.index('def advertise_bgp_routes')
    # The immediate preceding decorators should NOT include /api/streams/register
    _preceding = src[max(0, _idx - 500):_idx]
    assert "@app.route(\"/api/bgp/routes/advertise\"" in _preceding
    # No @app.route('/api/streams/register' immediately before advertise_bgp_routes
    assert "/api/streams/register" not in _preceding


# ─── M2: TOCTOU reservation ───


def test_m2_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.396 (audit streams M2)" in src


def test_m2_reservation_state_defined():
    src = _read("run_tgen_server.py")
    assert "_launch_reservations = set()" in src
    assert "_launch_reservations_lock = Lock()" in src


def test_m2_reservation_used_in_launch():
    src = _read("run_tgen_server.py")
    _idx = src.index("def launch_single_stream(stream_data, interface):")
    _end = src.index("def _launch_single_stream_body", _idx)
    body = src[_idx:_end]
    # Reserve the slot with lock
    assert "with _launch_reservations_lock:" in body
    assert "_launch_reservations.add(_launch_key)" in body
    # Re-check tracker inside the reservation
    assert "stream_tracker.find_stream_by_id(interface, stream_id)" in body
    # Refuse to double-launch
    assert "concurrent-start-in-progress" in body
    assert "already-running" in body
    # Release in finally
    assert "finally:" in body
    assert "_launch_reservations.discard(_launch_key)" in body


def test_m2_body_split_into_helper():
    src = _read("run_tgen_server.py")
    assert "def _launch_single_stream_body(stream_data, interface, stream_id," in src


# ─── M3: J1 mirror ───


def test_m3_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.396 (audit streams M3)" in src


def test_m3_counter_trust_in_else_branch():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.396 (audit streams M3)")
    body = src[_idx:_idx + 3500]
    # The condition: db_status != Running AND (tracker OR counts advancing)
    assert 'db_status != "Running"' in body
    assert "is_actually_running" in body
    assert "tx_count_now > 0" in body
    assert "rx_count_now > 0" in body
    # Overrides to Running when drift detected
    assert 'actual_status = "Running"' in body
    # Logs drift
    assert "DB drift" in body


# ─── M4: /api/traffic/stop mass-stop removed ───


def test_m4_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.396 (audit streams M4)" in src


def test_m4_mass_stop_removed():
    """The pre-fix 'Stopping orphaned stream ... on {iface}' loop
    in the last-resort branch must be gone. Log line survives
    (with different wording) but no loop over
    matching_interface_streams that stops each one."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.396 (audit streams M4)")
    body = src[_idx:_idx + 3000]
    assert "refusing to" in body.lower()
    # The stop_event.set() and stream_tracker.remove_stream_by_id
    # inside the old for-loop are gone in this branch
    _bad_lines = [
        "for s in matching_interface_streams:",
        "stream_obj[\"stop_event\"].set()",
    ]
    for _bl in _bad_lines:
        # These strings can exist elsewhere in the function — bound
        # our search tightly to just the M4 block.
        _block_end = min(len(body), body.find("_emit_event", 0)
                         if "_emit_event" in body else len(body))
        assert _bl not in body[:_block_end], (
            f"mass-stop remnant still present in M4 block: {_bl}"
        )


def test_m4_points_to_orphans_reap():
    """The new log message points operator to /api/streams/orphans/reap
    as the correct tool for stopping orphans they know about."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.396 (audit streams M4)")
    body = src[_idx:_idx + 3000]
    assert "orphans/reap" in body


# ─── M5: role gates ───


def test_m5_five_endpoints_gated():
    """Each of the 5 flagged endpoints now has @require_role above
    its @app.route (in non-comment code)."""
    src = _strip_comments(_read("run_tgen_server.py"))
    _cases = [
        ('@app.route("/api/streams/load"', '@require_role("operator")'),
        ('@app.route("/api/streams/stats"', '@require_role("viewer")'),
        ('@app.route("/api/streams/<stream_id>/rx_debug"', '@require_role("viewer")'),
        ('@app.route("/api/streams/orphans"', '@require_role("viewer")'),
        ("@app.route('/api/streams/register'", '@require_role("operator")'),
        ("@app.route('/api/streams/update'", '@require_role("operator")'),
    ]
    for _route, _gate in _cases:
        _idx = src.index(_route)
        _window = src[_idx:_idx + 400]
        assert _gate in _window, (
            f"expected {_gate} within 400 chars of {_route}; "
            f"got: {_window[:400]!r}"
        )


def test_m5_orphans_reap_still_admin():
    """/api/streams/orphans/reap must remain admin-only — the audit
    called it out as the correct target for the M4 refactor."""
    src = _read("run_tgen_server.py")
    _idx = src.index('@app.route("/api/streams/orphans/reap"')
    _window = src[_idx:_idx + 400]
    assert '@require_role("admin")' in _window


# ─── version guard ───


def test_pyproject_at_least_0596():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 396)


# ─── regression guards ───


def test_v0393_j1_first_half_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.393 (audit streams J1)" in src


def test_v0395_stream_dialog_async_helper_intact():
    src = _read("widgets/stream_dialog.py")
    assert "def _stream_async_get(self, url, on_ok, on_err=None, timeout=3.0):" in src


def test_v0394_k1_rfc2544_worker_intact():
    src = _read("widgets/rfc2544_dialog.py")
    assert "class _RfcHttpWorker(QThread):" in src
