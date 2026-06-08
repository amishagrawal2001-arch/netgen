"""v0.5.43 — link status uses sysfs operstate + admin response
drives the icon immediately.

Operator-reported on srv06 (Jun 8 2026, post v0.5.38):
  "tried online interface, link is online but GUI link status
   shows red."

Two-layer bug:

  1. /api/interfaces used psutil.net_if_stats().isup, which on
     Linux means `IFF_UP and IFF_RUNNING`. IFF_RUNNING tracks
     carrier — takes 2-10s to come up on big NICs (Mellanox 100G
     especially). So a freshly-upped link reports "down" for
     several seconds via psutil even though `ip link show` says
     `state UP` (which uses operstate).

  2. The right-click Set Online action only triggered ONE refresh
     500ms after success. If carrier hadn't negotiated by then,
     the polling caught the still-down psutil reading and the
     icon stayed red — sometimes indefinitely if subsequent polls
     also happened mid-negotiation.

v0.5.43 fixes both layers:

  Server (/api/interfaces):
    Read /sys/class/net/<iface>/operstate as the primary signal.
    operstate=up → up, operstate=down → down,
    operstate=unknown → up (admin-requested state respected for
    virtual / loopback / driver-doesn't-report ifaces). Fall
    back to psutil.isup only when sysfs read fails (containers,
    macOS, etc.).

  Client (Set Online flow):
    The admin endpoint's response already carries operstate
    (read from /sys/.../operstate server-side). Use it
    immediately to update the matching port_item's icon —
    don't wait for the next /api/interfaces poll. The staggered
    update_server_tree() calls (500ms / 3s / 8s) remain as
    backup.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
_CLIENT = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "server_section.py"
)


# ─────────────────── /api/interfaces uses operstate ─────────────────


def test_interfaces_endpoint_reads_sysfs_operstate():
    """The endpoint must read /sys/class/net/<iface>/operstate as
    the canonical link-state source, NOT (only) psutil.isup."""
    src = _SERVER.read_text()
    m = re.search(
        r"def get_interfaces\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = m.group(0)
    assert "/sys/class/net/" in body and "/operstate" in body, (
        "/api/interfaces doesn't read sysfs operstate. Reverting "
        "to psutil.isup will leave Mellanox NICs reporting 'down' "
        "for 2-10s after `ip link set up`."
    )


def test_interfaces_endpoint_treats_unknown_as_up():
    """Virtual / loopback / drivers-that-don't-report-link surface
    operstate='unknown'. The admin-requested state for these is
    up (operator just enabled them), so unknown should NOT
    render as down."""
    src = _SERVER.read_text()
    m = re.search(
        r"def get_interfaces\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = m.group(0)
    # Look for the "up" or "unknown" condition.
    assert re.search(
        r'operstate\s+in\s+\(\s*["\']up["\']\s*,\s*["\']unknown["\']',
        body,
    ), (
        "/api/interfaces doesn't treat operstate='unknown' as up. "
        "Virtual interfaces would render as red despite the operator "
        "having set them up."
    )


def test_interfaces_endpoint_falls_back_to_psutil_on_sysfs_failure():
    """Sysfs is unavailable on macOS dev hosts, in containers
    with proc-only mounts, etc. The endpoint must fall back to
    psutil.isup instead of returning a confusing status."""
    src = _SERVER.read_text()
    m = re.search(
        r"def get_interfaces\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = m.group(0)
    # The sysfs read must be in a try/except, and the else-clause
    # must fall back to is_up (psutil).
    assert "try:" in body and "is_up" in body, (
        "Endpoint has no fallback path — sysfs read failure leaves "
        "status as None / down on non-Linux dev hosts."
    )


def test_interfaces_response_exposes_operstate_field():
    """The response should also surface the raw operstate so the
    client can use it for debug + the admin flow can directly
    apply it to icons."""
    src = _SERVER.read_text()
    m = re.search(
        r"def get_interfaces\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    body = m.group(0)
    # Look for "operstate" as a response field.
    assert re.search(
        r'["\']operstate["\']\s*:',
        body,
    ), (
        "Response dict doesn't include operstate field — client "
        "loses visibility into the kernel's raw view."
    )


# ─────────── Client applies operstate from admin response ───────────


def test_admin_action_calls_iface_icon_helper():
    """Set Online / Set Offline handler must call the new
    `_update_iface_icon_for_operstate` helper with the operstate
    from the admin response. Without this the icon update waits
    for the polling cycle."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _set_interface_admin_state[\s\S]+?(?=\n    def [a-z])",
        src,
    )
    assert m, "_set_interface_admin_state body not found"
    body = m.group(0)
    assert "_update_iface_icon_for_operstate" in body, (
        "Set Online handler doesn't call "
        "_update_iface_icon_for_operstate — the admin response's "
        "operstate is thrown away and the icon stays out of sync "
        "until the next polling tick."
    )


def test_iface_icon_helper_is_defined():
    """The helper must be defined — not just called."""
    src = _CLIENT.read_text()
    assert "def _update_iface_icon_for_operstate(" in src, (
        "_update_iface_icon_for_operstate helper not defined. "
        "The admin response operstate has no path to the icon."
    )


def test_iface_icon_helper_handles_unknown_as_up():
    """Same logic as server-side: operstate='unknown' on the
    client must render as green (admin-requested state)."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _update_iface_icon_for_operstate[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    assert re.search(
        r'in\s+\(\s*["\']up["\']\s*,\s*["\']unknown["\']',
        body,
    ), (
        "_update_iface_icon_for_operstate doesn't treat 'unknown' "
        "as up. Virtual/loopback ifaces would render red after "
        "Set Online."
    )


# ─────────────── Staggered refresh schedule ─────────────────────────


def test_set_interface_admin_schedules_three_refreshes():
    """A single 500ms QTimer.singleShot misses slow-carrier
    negotiation. The new pattern schedules at 500ms + 3s + 8s
    so even Mellanox-100G-class slow links flip green within
    the operator's attention window."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _set_interface_admin_state[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    # Count the QTimer.singleShot calls inside this method.
    ms_values = re.findall(
        r"QTimer\.singleShot\(\s*(\d+)\s*,",
        body,
    )
    ms_ints = [int(v) for v in ms_values]
    assert len(ms_ints) >= 3, (
        f"Only {len(ms_ints)} QTimer.singleShot calls in admin "
        f"handler — single 500ms refresh misses slow carrier "
        f"negotiation. Need at least 3 (500ms + 3s + 8s)."
    )
    # And the spread should cover ~10s total.
    assert max(ms_ints) >= 5000, (
        f"Refresh schedule max = {max(ms_ints)}ms. Mellanox 100G "
        f"carrier negotiation can take 8-10s; need a follow-up "
        f"poll past 5s."
    )


def test_pyproject_version_at_least_0543():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 43), (
        f"Version {m.group(1)} < 0.5.43"
    )
