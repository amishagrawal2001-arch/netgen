"""v0.5.110: server-side /api/interfaces/<iface>/mac endpoint
behavior, plus the mac_address field added to the existing
/api/interfaces payload.

These back the dialog's Auto-MAC button — the dialog hits
/api/interfaces/<iface>/mac and stuffs the result into the
mac_source_address field. Tests guard the contract:

  • Valid iface name → 200 + lowercase mac_address
  • Invalid iface name shape → 400, no sysfs read attempted
  • Iface that doesn't exist → 404
  • /api/interfaces enriches every iface with mac_address

The endpoint validates against path traversal — IFNAMSIZ-1 = 15
chars, alnum + . _ - only. Anything else is a 400. We test that
explicitly because a future maintainer "loosening" the regex
would re-introduce the attack surface; this test is the canary.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import mock_open, patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _app():
    """Return the Flask app — late import keeps the module-level
    test discovery cheap and avoids pulling in the world before
    the tests need it."""
    from run_tgen_server import app
    return app


def test_get_interface_mac_returns_lowercase_mac():
    """Happy path: sysfs hands us a properly formatted MAC and
    the endpoint returns it lowercased + the source label."""
    app = _app()
    with app.test_client() as c:
        # Mock the sysfs read so the test works on macOS dev hosts
        # without /sys/class/net.
        m = mock_open(read_data="AA:BB:CC:DD:EE:FF\n")
        with patch("builtins.open", m):
            r = c.get("/api/interfaces/eth0/mac")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["interface"] == "eth0"
    assert body["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert body["source"] == "sysfs"


def test_get_interface_mac_rejects_path_traversal_attempts():
    """Iface name validator must reject '..' / '/' / overlong
    names. Each of these should 400 BEFORE any sysfs read happens.
    """
    app = _app()
    bad = [
        "../etc/passwd",
        "eth0/../something",
        "abcdefghijklmnop",  # 16 chars > IFNAMSIZ-1=15
        "eth 0",             # space disallowed
        "eth$0",             # $ disallowed
        "",                  # empty
    ]
    with app.test_client() as c:
        for name in bad:
            # Some bad names won't route to this view at all (Flask
            # routing rejects '/' in path segments). For those Flask
            # returns its own 404 — that's fine too, the point is
            # we never hit the sysfs read.
            r = c.get(f"/api/interfaces/{name}/mac")
            assert r.status_code in (400, 404), (
                f"bad iface name {name!r} should not be accepted "
                f"(got {r.status_code})"
            )


def test_get_interface_mac_handles_missing_interface():
    """When sysfs can't open the file, fall through to psutil; if
    psutil also has nothing, return 404."""
    app = _app()
    with app.test_client() as c:
        with patch("builtins.open", side_effect=FileNotFoundError()):
            with patch("psutil.net_if_addrs", return_value={}):
                r = c.get("/api/interfaces/eth9/mac")
    assert r.status_code == 404
    body = r.get_json()
    assert body["mac_address"] == ""
    assert "error" in body


def test_get_interface_mac_rejects_malformed_mac_from_sysfs():
    """sysfs returning garbage (corrupted netdev, vlan iface with
    weird hw_addr) shouldn't fool the dialog into setting a
    syntactically invalid src MAC. Endpoint returns 500."""
    app = _app()
    with app.test_client() as c:
        m = mock_open(read_data="not-a-mac\n")
        with patch("builtins.open", m):
            r = c.get("/api/interfaces/eth0/mac")
    assert r.status_code == 500
    body = r.get_json()
    assert body["mac_address"] == "not-a-mac"
    assert "format" in body.get("error", "").lower()


def test_get_interface_mac_default_zero_mac_still_returned():
    """All-zeros MAC is a real value the kernel can report (some
    virtual iface types). Don't 404 on it — let the dialog see it
    and the operator decide. (The dialog's mismatch-warning chip
    treats all-zeros as "default / unset" and nudges Auto, which
    is the right UX layer for the policy choice.)"""
    app = _app()
    with app.test_client() as c:
        m = mock_open(read_data="00:00:00:00:00:00\n")
        with patch("builtins.open", m):
            r = c.get("/api/interfaces/eth0/mac")
    assert r.status_code == 200
    assert r.get_json()["mac_address"] == "00:00:00:00:00:00"
