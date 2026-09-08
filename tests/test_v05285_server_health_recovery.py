"""v0.5.285 — Client-side split-brain fix: server LED stuck RED
forever after health-probe failed N times, while device pollers
correctly showed devices under that server GREEN.

Two-part fix (SERVER-A1a + A1b): poll offline servers too, and
flip online=True on successful health response so the LED can
recover red→green."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SEC = (REPO / "traffic_client" / "server_section.py").read_text()


def test_server_a1_marker_present():
    assert "v0.5.285 (SERVER-A1)" in SEC


# --- SERVER-A1a: poll ALL servers, not just online ones ---------


def test_poll_server_health_no_longer_filters_by_online():
    """The `if s.get('online')` filter that made server offline a
    one-way trip must be gone. Poll list now includes offline
    servers so they can auto-recover."""
    idx = SEC.find("def poll_server_health")
    end = SEC.find("\n    def ", idx + 1)
    body = SEC[idx:end]
    # No live-code filter on s.get("online") — pre-fix line was
    # `[s for s in ... if s.get("online")]`. Post-fix takes all.
    live_lines = [
        ln for ln in body.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    # Assert the pre-fix pattern is gone (only surviving as
    # comment history).
    for ln in live_lines:
        assert 'if s.get("online")' not in ln, (
            f"pre-fix filter still in live code: {ln!r}"
        )
    # And the new list-comprehension takes all.
    assert 'list(getattr(self, "server_interfaces"' in body


def test_poll_server_health_documents_why_offline_are_polled():
    """A future author who thinks 'polling offline servers is
    wasteful' must see the rationale before stripping the fix."""
    idx = SEC.find("def poll_server_health")
    end = SEC.find("\n    def ", idx + 1)
    body = SEC[idx:end]
    assert "auto-recover" in body
    assert "one-way trip" in body


# --- SERVER-A1b: flip online=True on successful health ----------


def test_apply_server_health_flips_online_on_success():
    """The success branch of _apply_server_health must flip
    server['online'] to True. Pre-fix it only wrote health, so
    an offline server that responded healthy stayed painted red."""
    idx = SEC.find("def _apply_server_health")
    end = SEC.find("\n    def ", idx + 1)
    body = SEC[idx:end]
    assert "v0.5.285 (SERVER-A1)" in body
    # Detects the was-offline case + flips both flags.
    assert '_was_offline = not server.get("online")' in body
    assert 'server["online"] = True' in body
    assert 'server["is_online"] = True' in body


def test_apply_server_health_logs_recovery_transition():
    """When flipping red→green, log at info so ops-log grep
    confirms the recovery happened. Otherwise a future operator
    seeing 'still red' can't tell whether the flip fired."""
    idx = SEC.find("def _apply_server_health")
    end = SEC.find("\n    def ", idx + 1)
    body = SEC[idx:end]
    assert 'if _was_offline:' in body
    assert 'flipping back online' in body


def test_apply_server_health_success_still_updates_health_and_led():
    """v0.5.285 additions must not disable the pre-fix behavior:
    write health, health_issues, netgen_version, refresh LED."""
    idx = SEC.find("def _apply_server_health")
    end = SEC.find("\n    def ", idx + 1)
    body = SEC[idx:end]
    for line in (
        'server["health_fail_count"] = 0',
        'server["health"] = "degraded" if degraded else "healthy"',
        'server["health_issues"] = health.get("issues") or []',
        'self._update_server_led(server)',
    ):
        assert line in body, f"pre-fix line missing: {line!r}"


# --- Metadata ---------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 285)
