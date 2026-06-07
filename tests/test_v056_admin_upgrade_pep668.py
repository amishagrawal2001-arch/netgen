"""Regression test for v0.5.6: /api/admin/upgrade_wheel must work
on PEP 668 hosts AND on v0.5.0+ tarball installs.

Operator-reported on san-hp-srv06 (Ubuntu 24.04 Noble):

  [client] POST http://san-hp-srv06:5050/api/admin/upgrade_wheel
  [upgrade] cmd: /usr/bin/python3 -m pip install --upgrade ...
  error: externally-managed-environment
  × This environment is externally managed
  ╰─> ... try --break-system-packages
  [client] pip exited rc=1; aborting

Same operator-trust failure class we whacked through v0.4.7 /
v0.4.8 / v0.4.9 — except this time it landed in the
HTTP-upgrade endpoint we hadn't touched yet. The v0.5.1
SshUpgradeWorker dispatch fix covered the OTHER upgrade path
(client → SSH → systemctl restart), not this HTTP-API one.

v0.5.6 fixes both flavors of the bug at once:

  1. v0.5.0+ tarball install: if /opt/netgen-server/bin/netgen-
     upgrade exists, dispatch through it. The script runs pip
     in the bundled venv (no PEP 668 surface at all), AND it
     handles the systemctl restart inside itself.

  2. v0.4.x system install: detect /usr/lib/python3*/
     EXTERNALLY-MANAGED. If present, add --break-system-packages
     to the pip command. If absent (pre-PEP 668 host with older
     pip that wouldn't recognize the flag), omit it.

Detection cached at module level — repeated upgrades skip the
filesystem stat.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def test_upgrade_endpoint_dispatches_via_tarball_script_when_present():
    """If /opt/netgen-server/bin/netgen-upgrade exists, the endpoint
    must dispatch through it instead of running `sys.executable -m
    pip install`. The tarball script runs pip in the bundled venv
    AND handles systemctl restart itself."""
    src = _SERVER.read_text()
    assert "/opt/netgen-server/bin/netgen-upgrade" in src, (
        "/api/admin/upgrade_wheel doesn't check for the tarball "
        "install's bin/netgen-upgrade script. v0.5.0+ tarball "
        "installs would run system pip3 → wheel lands in the "
        "wrong site-packages → silent upgrade failure."
    )
    # And the existence test must use os.path.isfile + os.access X_OK
    # so a tarball without a chmod'd script doesn't false-positive.
    assert "os.path.isfile" in src and "os.access" in src and "X_OK" in src, (
        "Tarball script existence check doesn't verify it's a real "
        "executable file. A stale empty file or non-executable "
        "leftover would dispatch and then fail confusingly."
    )


def test_legacy_path_detects_externally_managed_marker():
    """The legacy (non-tarball) pip path must detect the
    /usr/lib/python3*/EXTERNALLY-MANAGED marker — the canonical
    PEP 668 signal. Without this the v0.4.x → v0.5.x upgrade
    flow stays broken on Noble + Debian 12."""
    src = _SERVER.read_text()
    # Find the upgrade endpoint body.
    m = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    assert m, "api_admin_upgrade_wheel body not found"
    body = m.group(0)
    assert "EXTERNALLY-MANAGED" in body, (
        "Upgrade endpoint doesn't detect the EXTERNALLY-MANAGED "
        "marker. Operators on Noble + Debian 12+ get the silent-"
        "upgrade-failure trap from san-hp-srv06."
    )
    # Must check /usr/lib/python3*/ AND /usr/local/lib/python3*/ —
    # the marker can land in either.
    assert "/usr/lib/python3" in body, (
        "Marker check missing /usr/lib/python3*/ — the standard "
        "Debian/Ubuntu path."
    )


def test_legacy_path_conditionally_adds_break_system_packages():
    """--break-system-packages must be added ONLY when the marker
    exists. Pre-PEP 668 systems (Ubuntu 22.04, etc.) may have an
    older pip that rejects the unknown flag — always-on would
    break THEM. Conditional is the only correct shape."""
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    # The flag must be appended conditionally on the detection
    # variable.
    assert re.search(
        r"if\s+_PIP_BREAK_FLAG_DETECTED:\s*\n\s+cmd\.append\(['\"]--break-system-packages['\"]",
        body,
    ), (
        "--break-system-packages isn't gated on "
        "_PIP_BREAK_FLAG_DETECTED. Either always-on (breaks "
        "Jammy) or always-off (breaks Noble) — pick by detection."
    )


def test_detection_is_cached_at_module_level():
    """Repeated upgrades shouldn't re-stat the filesystem to
    check for the marker. Cache it as a module-global so the
    first upgrade pays the stat cost, subsequent ones don't."""
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    # Match `if "_PIP_BREAK_FLAG_DETECTED" not in globals():`
    assert "_PIP_BREAK_FLAG_DETECTED" in body, (
        "Detection result isn't kept around for reuse — each "
        "upgrade re-stats /usr/lib/python3*/EXTERNALLY-MANAGED."
    )
    assert re.search(
        r'["\']_PIP_BREAK_FLAG_DETECTED["\']\s+not in\s+globals\(\)',
        body,
    ), (
        "Cache check isn't `not in globals()` — the variable could "
        "be re-set on every upgrade."
    )


def test_log_records_upgrade_mode_for_diagnostics():
    """The upgrade log must record which path was taken (tarball
    vs legacy, with or without --break-system-packages). When a
    fresh operator sees this in /var/log/netgen-upgrade.log they
    can immediately tell whether the host went through the
    expected code path."""
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_upgrade_wheel\(\)[\s\S]+?(?=\n@app\.route|\ndef )",
        src,
    )
    body = m.group(0)
    assert "upgrade_mode" in body, (
        "Endpoint doesn't compute or log an upgrade_mode. "
        "Operator log won't reveal whether tarball-script vs "
        "system-pip+break ran."
    )
    # Both code paths must set it.
    assert "tarball:netgen-upgrade" in body, (
        "Tarball-script path doesn't tag its mode in the log."
    )
    assert "legacy:system-pip" in body, (
        "Legacy path doesn't tag its mode in the log."
    )
