"""v0.5.95 — audit batch 4: recovery + integration tests.

From the v0.5.91 admin-console audit:

  H4 — `_iface_action_safety_check` had
        `except Exception: pass` around the stream-tracker
        query. If the tracker was broken, the safety check
        silently reported "no active streams" and allowed a
        down on a NIC with running streams.

        Fix: fail SAFE — return 503 + "use force=true to
        override" so the operator gets an explicit signal.

  H5 — `_ethtool_full_dump` returned text-with-error-marker
        (`"(ethtool returned rc=2: ...)"`) as the stdout value.
        Client couldn't tell "section unavailable" from
        "section legitimately empty".

        Fix: return `{"stdout": "...", "error": "..."}` per
        section. JS renders error sections with a muted
        summary line and no <pre>.

  H7 — Reset's "down ok / up failed" 500 message read
        `Manual: ip link set <n> up.` That's an SSH instruction.
        The operator has the ↑ button right there in the row.

        Fix: new message reads "Click the ↑ button in the row
        to retry" + `recoverable_via: "iface_up"` for the JS.

  H8 — Tests were regex-presence only. Real integration tests
        with Flask's test_client + subprocess mocked exercise
        the actual handler logic: safety-check 409, force
        override, reset's down-then-up sequence, the recovery
        message.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── Source-level checks (H4, H5, H7) ───


def test_safety_check_fails_safe_on_tracker_error():
    src = _src()
    m = re.search(
        r"def _iface_action_safety_check[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Pre-fix had `except Exception: pass`; new code returns
    # 503 + "use force=true".
    assert re.search(
        r"return\s*\(\s*$\s*f?\"stream tracker unavailable",
        body,
        re.MULTILINE,
    ) or "stream tracker unavailable" in body, (
        "safety check still silently passes on tracker errors"
    )
    assert "503" in body
    # Logs the warning so operator can see in journal.
    assert "logging.warning" in body or "logging.info" in body


def test_full_dump_returns_structured_section():
    src = _src()
    m = re.search(
        r"def _ethtool_full_dump[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Each section is a dict {stdout, error}.
    assert '"stdout"' in body and '"error"' in body
    # The mid-string error marker pattern is gone.
    assert "(ethtool returned rc=" not in body, (
        "Old text-with-marker shape still emitted"
    )


def test_drawer_handles_structured_sections():
    src = _src()
    idx = src.find("async function ifaceDetailsToggle(")
    assert idx > 0
    # v0.5.105: the drawer body grew (Live counters tile + RX-drop
    # surfacing). Widen the window so the test sees the entire
    # function, not a truncated slice.
    body = src[idx:idx + 12000]
    # JS reads either {stdout, error} or backward-compat raw string.
    assert "_sec.stdout" in body
    assert "_sec.error" in body
    # Error sections render with a different color hint.
    assert "var(--bad)" in body or "color: #b91c1c" in body or "muted" in body


def test_reset_failure_points_at_up_button():
    src = _src()
    m = re.search(
        r"def api_admin_iface_reset[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Pre-fix said `Manual: ip link set <n> up`; new says
    # "Click the ↑ button in the row".
    assert "↑ button" in body or "Click the" in body
    # Recoverable-via signal for the JS.
    assert "IFACE_RESET_HALF_DONE" in body
    assert "recoverable_via" in body


# ─── Integration tests (H8) — Flask test client + mocked subprocess ───


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Flask test client with auth disabled. Stash original
    state + restore after the test.

    Scoped to the module so we only pay the server-import cost
    once. Module-load creates /opt/netgen/ by default — point
    DB paths at a tmpdir via NETGEN_DB_PATH so import works on
    dev machines without root."""
    flask = pytest.importorskip("flask")
    import os
    tmp = tmp_path_factory.mktemp("netgen_db")
    os.environ["NETGEN_DB_PATH"] = str(tmp / "device.db")
    # The server is a big single file — import via importlib so
    # we get a fresh module each test session.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_tgen_server", str(_SERVER),
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server.app.config["TESTING"] = True
    return server.app.test_client(), server


def test_iface_up_endpoint_routes(client):
    c, server = client
    # Patch _run_ip_link so we don't actually invoke `ip`.
    with mock.patch.object(
        server, "_run_ip_link",
        return_value=(True, "", ""),
    ):
        r = c.post("/api/admin/iface/eth0/up")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["action"] == "up"
    assert body["interface"] == "eth0"


def test_iface_down_refuses_without_force_on_default_route(client):
    c, server = client
    # Mock the safety check to claim eth0 carries the default
    # route.
    with mock.patch.object(
        server, "_iface_action_safety_check",
        return_value=("eth0 carries the default route. ...", 409),
    ), mock.patch.object(
        server, "_run_ip_link",
        return_value=(True, "", ""),
    ):
        r = c.post(
            "/api/admin/iface/eth0/down",
            json={"force": False},
        )
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "IFACE_DOWN_UNSAFE"
    assert body["can_force"] is True


def test_iface_down_force_true_skips_safety_check(client):
    c, server = client
    # Even if the safety check would refuse, force=true skips
    # the check entirely.
    safety_called = []

    def _fake_safety(iface, action):
        safety_called.append((iface, action))
        return ("would refuse", 409)

    with mock.patch.object(
        server, "_iface_action_safety_check",
        side_effect=_fake_safety,
    ), mock.patch.object(
        server, "_run_ip_link",
        return_value=(True, "", ""),
    ):
        r = c.post(
            "/api/admin/iface/eth0/down",
            json={"force": True},
        )
    assert r.status_code == 200
    # Safety check must NOT have been called with force=true.
    assert safety_called == []


def test_iface_reset_runs_down_then_up(client):
    c, server = client
    call_order = []

    def _fake_link(iface, action):
        call_order.append(action)
        return (True, "", "")

    with mock.patch.object(
        server, "_iface_action_safety_check",
        return_value=(None, 0),
    ), mock.patch.object(
        server, "_run_ip_link",
        side_effect=_fake_link,
    ), mock.patch(
        "time.sleep",  # don't actually wait 1 second
    ):
        r = c.post("/api/admin/iface/eth0/reset")
    assert r.status_code == 200
    assert call_order == ["down", "up"], (
        f"reset didn't down-then-up: {call_order}"
    )


def test_iface_reset_down_ok_up_failed_returns_recovery_hint(client):
    c, server = client
    # down succeeds, up fails — operator should get the new
    # "click ↑ button" recovery message and IFACE_RESET_HALF_DONE.
    seen = []

    def _fake_link(iface, action):
        seen.append(action)
        if action == "down":
            return (True, "", "")
        return (False, "", "RTNETLINK answers: Device or resource busy")

    with mock.patch.object(
        server, "_iface_action_safety_check",
        return_value=(None, 0),
    ), mock.patch.object(
        server, "_run_ip_link",
        side_effect=_fake_link,
    ), mock.patch("time.sleep"):
        r = c.post("/api/admin/iface/eth0/reset")
    assert r.status_code == 500
    body = r.get_json()
    assert body["code"] == "IFACE_RESET_HALF_DONE"
    assert body["recoverable_via"] == "iface_up"
    assert "↑" in body["error"] or "button" in body["error"]


def test_iface_invalid_name_rejected(client):
    c, server = client
    # Iface name with `/` should fail the regex check before
    # any subprocess call.
    with mock.patch.object(
        server, "_run_ip_link",
        side_effect=AssertionError("should NOT be called"),
    ):
        r = c.post("/api/admin/iface/has\\u002fslash/up")
    # Flask returns 404 for unmatched routes; the iface-name
    # regex would catch this server-side once routed.
    assert r.status_code in (400, 404)


def test_iface_flash_endpoint_clamps_seconds(client):
    c, server = client
    # operator passes seconds=999 → backend clamps to 60.
    spawned = []

    def _fake_popen(cmd, **kwargs):
        spawned.append(cmd)

        class _P:
            pid = 12345

            def wait(self, *_a, **_kw):
                # v0.5.97 (audit L4): the flash handler now
                # spawns a daemon thread to wait+reap the
                # Popen. Mock must satisfy `.wait()`.
                return 0

            def poll(self, *_a, **_kw):
                return 0

        return _P()

    with mock.patch("subprocess.Popen", side_effect=_fake_popen):
        r = c.post(
            "/api/admin/iface/eth0/flash",
            json={"seconds": 999},
        )
    assert r.status_code == 200
    body = r.get_json()
    assert body["seconds"] == 60, "Flash didn't clamp 999 → 60"
    # Cmd should include "60" not "999".
    assert spawned and "60" in spawned[0], spawned


# ─── Version ───


def test_pyproject_version_at_least_0595():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 95)
