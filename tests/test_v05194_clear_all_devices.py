"""v0.5.194: bulk-clear-all-devices endpoint + Tools-menu action.

The client can be restarted at any time; the server-side FRR
containers / VRFs / device rows are persistent and can drift
into a stale state where the operator sees devices in the tab
whose containers no longer exist. Removing them one row at a
time is tedious past a handful. This ships a Tools → Clear All
Devices path that hits a new `/api/devices/clear_all` endpoint,
which loops the device list through the *existing*
`/api/device/remove` handler (via `app.test_client()`) so no
behaviour drift is possible.

Tests here cover the endpoint's contract:
    * empty DB → `total=0`
    * multi-device → returns per-device `status_code` + `body`
    * per-device call went through /api/device/remove (via test_client)
    * admin-role gate exists
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05194_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# Empty DB → total 0
# ─────────────────────────────────────────────────────────────────────

def test_clear_all_empty_db_returns_zero():
    import run_tgen_server as srv

    with patch.object(srv, "device_db") as db:
        db.get_all_devices.return_value = []
        with srv.app.test_client() as client:
            resp = client.post("/api/devices/clear_all")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["total"] == 0
    assert body["removed"] == 0
    assert body["failed"] == 0
    assert body["results"] == []


# ─────────────────────────────────────────────────────────────────────
# Multi-device → per-device results returned
# ─────────────────────────────────────────────────────────────────────

def test_clear_all_loops_all_devices():
    import run_tgen_server as srv

    devices = [
        {"device_id": "d1", "device_name": "alpha"},
        {"device_id": "d2", "device_name": "beta"},
        {"device_id": "d3", "device_name": "gamma"},
    ]

    # Mock the underlying remove_device view so we don't actually
    # try to talk to FRR / Docker / iproute. The endpoint under
    # test enters remove_device via app.test_client(), which will
    # re-dispatch through the flask route table — we replace the
    # unwrapped view function.
    remove_calls = []

    def fake_remove_device():
        # Called inside a request context by test_client.
        from flask import request, jsonify
        payload = request.get_json() or {}
        did = payload.get("device_id")
        remove_calls.append(did)
        return jsonify({
            "status": "removed",
            "database_removed": True,
            "container_removed": True,
        }), 200

    with patch.object(srv, "device_db") as db, \
         patch.object(srv, "remove_device", side_effect=fake_remove_device):
        db.get_all_devices.return_value = devices
        # The remove_device patch above replaces the function object,
        # but Flask's URL map still holds a bound reference to the
        # original. Re-bind by replacing the view in the URL map.
        srv.app.view_functions["remove_device"] = fake_remove_device
        try:
            with srv.app.test_client() as client:
                resp = client.post("/api/devices/clear_all")
        finally:
            # Restore original view (import fresh) so other tests aren't
            # polluted by the substitution.
            import importlib
            importlib.reload(srv)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["total"] == 3
    assert body["removed"] == 3
    assert body["failed"] == 0
    assert len(body["results"]) == 3
    assert {r["device_id"] for r in body["results"]} == {"d1", "d2", "d3"}
    for r in body["results"]:
        assert r["status_code"] == 200
        assert r["body"].get("status") == "removed"
    # Every device got dispatched to /api/device/remove.
    assert set(remove_calls) == {"d1", "d2", "d3"}


# ─────────────────────────────────────────────────────────────────────
# Endpoint is registered on the admin-role path
# ─────────────────────────────────────────────────────────────────────

def test_clear_all_endpoint_registered_with_admin_role():
    """Locks in the URL rule + role decorator so a rename doesn't
    silently downgrade the gate."""
    # Fresh import — the multi-device test above reloads the module.
    import importlib
    import run_tgen_server as srv
    importlib.reload(srv)

    # URL rule exists.
    rules = {r.rule for r in srv.app.url_map.iter_rules()}
    assert "/api/devices/clear_all" in rules, rules

    # Source of the view function includes the admin-role decorator.
    import inspect
    src = inspect.getsource(srv.clear_all_devices)
    # Look at the source *around* the function — the decorator lives
    # in the surrounding module, so grep the file too.
    file_src = Path(srv.__file__).read_text()
    assert '@require_role("admin")\ndef clear_all_devices' in file_src, (
        "clear_all_devices is not admin-role-gated"
    )
    # Body loops over device_db.get_all_devices() and hits /api/device/remove.
    assert "device_db.get_all_devices" in src
    assert "/api/device/remove" in src
    assert "app.test_client" in src
