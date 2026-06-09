"""v0.5.57 — admin JS in-flight guards (H9) + bind-history XSS
escape (H10).

H9: pre-fix `refreshHealth` and `refreshInterfaces` had no
in-flight guards. On a multi-NIC box the operator clicking
Bind/Unbind on several rows in quick succession (each action's
.finally fires both refreshes) plus the 30 s setInterval got
8+ parallel fetches racing. The slower response's
innerHTML-overwrite won — table flickered to stale state for
a few seconds.

Fix: `_healthInFlight` / `_ifacesInFlight` boolean + a rerun
flag. Concurrent re-entry returns immediately and sets the
flag; when the in-flight call settles, it checks the flag and
re-calls itself.

H10: `name = \`${history[pci].name} <span ...>\`` interpolated
operator-POSTed `name` into innerHTML without escaping. A bind
history record with `name=<img onerror=...>` would inject raw.
Admin-token-gated so risk is bounded, but defense-in-depth.

Fix: wrap `history[pci].name` in `escapeHtml()`.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_health_in_flight_guard_declared():
    """The in-flight flag must exist at the JS scope."""
    src = _src()
    assert "_healthInFlight" in src, (
        "No _healthInFlight flag declared — race guard missing"
    )
    assert "_healthRerun" in src, (
        "No _healthRerun flag for queued re-entry"
    )


def test_refresh_health_skips_when_in_flight():
    """refreshHealth must early-return when already in flight,
    setting the rerun flag."""
    src = _src()
    # Locate the refreshHealth function body.
    m = re.search(
        r"async function refreshHealth\(\)\s*\{[\s\S]+?\n    \}",
        src,
    )
    assert m, "refreshHealth() body not located"
    body = m.group(0)
    assert re.search(
        r"if\s*\(_healthInFlight\)[\s\S]{0,100}?return",
        body,
    ), (
        "refreshHealth doesn't early-return on _healthInFlight — "
        "race still possible"
    )
    # And it must set the rerun flag on early return.
    assert re.search(
        r"_healthRerun\s*=\s*true",
        body,
    ), (
        "Early return doesn't set _healthRerun — queued caller "
        "would never run"
    )


def test_refresh_health_clears_flag_in_finally():
    """The flag must be reset in `finally` so an exception doesn't
    permanently lock out the function."""
    src = _src()
    m = re.search(
        r"async function refreshHealth\(\)\s*\{[\s\S]+?\n    \}",
        src,
    )
    body = m.group(0)
    assert re.search(
        r"finally\s*\{[\s\S]{0,200}?_healthInFlight\s*=\s*false",
        body,
    ), (
        "refreshHealth doesn't clear _healthInFlight in finally — "
        "an exception would lock the function out forever"
    )


def test_refresh_health_reruns_if_queued():
    """If _healthRerun was set during the in-flight window, the
    finally branch must re-invoke refreshHealth."""
    src = _src()
    m = re.search(
        r"async function refreshHealth\(\)\s*\{[\s\S]+?\n    \}",
        src,
    )
    body = m.group(0)
    assert re.search(
        r"_healthRerun\s*=\s*false;\s*refreshHealth\(\)",
        body,
    ) or re.search(
        r"if\s*\(_healthRerun\)[\s\S]{0,50}?refreshHealth\(\)",
        body,
    ), (
        "Finally branch doesn't re-invoke refreshHealth when "
        "_healthRerun was set — queued caller lost"
    )


def test_ifaces_in_flight_guard_declared():
    src = _src()
    assert "_ifacesInFlight" in src, (
        "No _ifacesInFlight flag — same race as refreshHealth"
    )
    assert "_ifacesRerun" in src, "No _ifacesRerun flag"


def test_refresh_interfaces_uses_guard_pattern():
    """refreshInterfaces gets the same guard + rerun pattern."""
    src = _src()
    m = re.search(
        r"async function refreshInterfaces\(\)\s*\{[\s\S]+?\n    \}",
        src,
    )
    assert m, "refreshInterfaces() body not located"
    body = m.group(0)
    assert re.search(
        r"if\s*\(_ifacesInFlight\)[\s\S]{0,100}?return",
        body,
    ), "refreshInterfaces missing early-return guard"
    assert re.search(
        r"finally\s*\{[\s\S]{0,300}?_ifacesInFlight\s*=\s*false",
        body,
    ), "refreshInterfaces missing finally reset"


def test_bind_history_name_is_escaped():
    """`history[pci].name` going into innerHTML must be wrapped
    in escapeHtml. Pre-fix the operator-controlled name was raw."""
    src = _src()
    # The pre-fix pattern was `${history[pci].name} <span...`.
    forbidden = re.search(
        r"\$\{history\[pci\]\.name\}\s+<span",
        src,
    )
    assert not forbidden, (
        "Raw `${history[pci].name}` interpolation into innerHTML "
        "still present — XSS surface unfixed."
    )
    # The fix uses escapeHtml(history[pci].name).
    assert re.search(
        r"escapeHtml\(history\[pci\]\.name\)",
        src,
    ), (
        "history[pci].name not wrapped in escapeHtml — XSS via "
        "POST /api/admin/bind_history with malicious name"
    )


def test_pyproject_version_at_least_0557():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 57), (
        f"Version {m.group(1)} < 0.5.57"
    )
