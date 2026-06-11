"""v0.5.88 — Interface control: up / down / reset.

Operator (Jun 10 2026):

  "also allow user to on/off/reset network interfaces from
   admin console..."

Three new admin-gated endpoints:
  POST /api/admin/iface/<name>/up    — `ip link set <n> up`
  POST /api/admin/iface/<name>/down  — `ip link set <n> down`
  POST /api/admin/iface/<name>/reset — down → 1s sleep → up

Safety: down and reset refuse with 409 + code IFACE_*_UNSAFE
when the iface carries the default route, the SSH session,
or has an active stream. Body `{"force": true}` overrides
(matches the v0.2.76 bind-safety pattern).

JS: three compact ↑/↓/↻ buttons next to the existing Bind/Unbind
button on each row. Up is disabled when the link is already up;
down is disabled when already down. Click triggers a confirm
(except for up) + handles the 409 by re-prompting with force.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── Endpoints ───


def test_iface_up_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/iface/<iface>/up"' in src
    assert "def api_admin_iface_up(" in src


def test_iface_down_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/iface/<iface>/down"' in src
    assert "def api_admin_iface_down(" in src


def test_iface_reset_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/iface/<iface>/reset"' in src
    assert "def api_admin_iface_reset(" in src


def test_iface_endpoints_require_admin():
    src = _src()
    for verb in ("up", "down", "reset"):
        m = re.search(
            rf'@app\.route\("/api/admin/iface/<iface>/{verb}"[\s\S]'
            rf'{{0,400}}?def api_admin_iface_{verb}',
            src,
        )
        assert m and "@require_role" in m.group(0), (
            f"/api/admin/iface/<iface>/{verb} not admin-gated"
        )


def test_iface_name_validation_regex_defined():
    src = _src()
    assert "_RE_IFACE_NAME" in src, (
        "Missing iface-name validation regex"
    )
    # Matches kernel IFNAMSIZ rules: 1–15 chars, alphanumerics
    # plus dots/underscores/hyphens.
    assert re.search(
        r"_RE_IFACE_NAME\s*=\s*re\.compile\(r\"\^\[A-Za-z0-9_\.\\\-\]\{1,15\}\$",
        src,
    ), "Iface-name regex doesn't restrict to IFNAMSIZ"


def test_iface_action_safety_check_defined():
    src = _src()
    assert "def _iface_action_safety_check(" in src
    m = re.search(
        r"def _iface_action_safety_check[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Reads default-route + ssh-client iface.
    assert "collect_default_route_iface" in body
    assert "collect_ssh_client_iface" in body
    # And checks active-stream conflicts.
    assert "stream_tracker.get_stream_stats" in body
    # Up is harmless — skipped.
    assert 'action == "up"' in body


def test_down_endpoint_supports_force_override():
    src = _src()
    m = re.search(
        r"def api_admin_iface_down\([\s\S]+?(?=\ndef |\n@app\.route)",
        src,
    )
    body = m.group(0)
    assert "_strict_true" in body, (
        "down endpoint not parsing force boolean strictly"
    )
    assert '"IFACE_DOWN_UNSAFE"' in body
    assert '"can_force": True' in body


def test_reset_endpoint_uses_down_sleep_up_sequence():
    src = _src()
    m = re.search(
        r"def api_admin_iface_reset\([\s\S]+?(?=\ndef |\n@app\.route)",
        src,
    )
    body = m.group(0)
    # down then up sequence.
    assert '"down"' in body and '"up"' in body
    # Brief sleep between.
    assert "sleep(" in body, "reset doesn't pause between down + up"
    # Friendly recovery message if up fails.
    assert "brought down but" in body or "Manual:" in body


def test_run_ip_link_helper_caps_timeout():
    src = _src()
    m = re.search(
        r"def _run_ip_link\([\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "timeout=5" in body
    assert "TimeoutExpired" in body
    assert "FileNotFoundError" in body


# ─── JS ───


def test_iface_row_renders_lifecycle_buttons():
    src = _src()
    assert 'data-iface-action="up"' in src
    assert 'data-iface-action="down"' in src
    assert 'data-iface-action="reset"' in src


def test_iface_lifecycle_buttons_have_titles():
    src = _src()
    assert 'title="Bring' in src
    assert 'title="Reset' in src


def test_iface_lifecycle_click_handler_defined():
    src = _src()
    assert "ifaceLifecycle" in src
    # Handles 409 force flow.
    assert "IFACE_DOWN_UNSAFE" in src or "IFACE_RESET_UNSAFE" in src
    assert "Force ${action} anyway" in src or "force ${action}" in src.lower()


def test_iface_lifecycle_refreshes_after_action():
    src = _src()
    idx = src.find("async function ifaceLifecycle(")
    assert idx > 0
    body = src[idx:idx + 2500]
    assert "refreshInterfaces" in body, (
        "Lifecycle handler doesn't refresh the iface table"
    )


# ─── Version ───


def test_pyproject_version_at_least_0588():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 88)
