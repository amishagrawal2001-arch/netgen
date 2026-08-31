"""v0.5.241 — /api/device/remove hangs forever on DHCP-mode devices;
UI shows "removed" then row REAPPEARS on refresh.

Operator on srv06 2026-08-31 reported: after clicking Remove on a
DHCP client row, the row disappeared briefly then came back on the
next refresh. Both DHCP client Remove AND DHCP server Remove
exhibited the same behavior.

Trace on srv06:
  23:19:37 POST /api/device/remove from client
  23:19:37 [DEVICE REMOVE] Successfully cleaned up VXLAN
  23:19:37 [DHCP] Stopping DHCP client before removing device …
  (…never returns…)

`stop_dhcp_client` hung indefinitely. The client's 10s HTTP
timeout gave up, but the server thread kept blocking. The
`device_db.remove_device` call at the END of remove_device()
was NEVER reached, so the DB row survived. Next `refresh_dhcp_status`
re-fetched the row and put it back in the UI.

Root cause (utils/dhcp.py `_run_command`):
  container.exec_run() has NO timeout enforcement. The `timeout`
  parameter was ONLY honored on the subprocess.run() branch. When
  dhclient's `-r` release blocks (e.g., waiting for DHCPRELEASE
  ack from an unreachable server on a stuck-in-Requesting client),
  the entire request thread freezes.

Fix (this ship):
- `_run_command` container path: run exec_run() in a
  threading.Thread and enforce `timeout` on `thread.join()`.
  Return exit_code=124 (conventional shell "timeout") on
  expiry so callers can move on. The lingering exec inside the
  container continues to run on its own — cheap price vs. a
  hung request.
- Client-side `_remove_device_from_server`: timeout bumped
  10s → 60s so even a slow-but-not-hanging server-side remove
  fits. Failure paths (timeout, non-200, "status:partial")
  now surface a QMessageBox instead of silent logger.debug —
  the operator sees WHY the row reappeared, not just that
  it did.

Same bug affected DHCP server Remove: same `_run_command`
container path, same missing timeout.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()
TAB = (REPO / "widgets" / "devices_tab.py").read_text()


# --- Server-side timeout enforcement ---------------------------------


def test_run_command_container_path_uses_threading():
    """The container path must wrap exec_run in a thread so we can
    enforce a timeout via thread.join()."""
    assert "import threading" in DHCP
    idx = DHCP.find("def _run_command(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 6000]
    # The v0.5.241 marker.
    assert "v0.5.241" in body
    # Thread-based enforcement.
    assert "threading.Thread(" in body
    assert "_t.join(timeout=" in body
    assert "if _t.is_alive():" in body


def test_run_command_container_timeout_returns_124():
    """On timeout, return exit_code=124 (shell's `timeout` exit
    convention) so callers can detect this without raising."""
    idx = DHCP.find("def _run_command(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 6000]
    assert "returncode=124" in body
    # Timeout is surfaced in stderr for downstream logging.
    assert 'stderr=f"docker exec timed out after {timeout}s"' in body


def test_run_command_container_check_true_raises_on_timeout():
    """When called with check=True, timeout should raise
    TimeoutExpired so caller can catch normally."""
    idx = DHCP.find("def _run_command(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 6000]
    assert "subprocess.TimeoutExpired(exec_cmd, timeout)" in body


# --- Client-side timeout + error surfacing ---------------------------


def test_client_cleanup_timeout_bumped_to_60():
    """/api/device/cleanup call: 10 → 60."""
    idx = TAB.find("def _remove_device_from_server(")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    # The pre-fix 10s timeout on cleanup is gone.
    assert "cleanup_payload, timeout=10)" not in body
    # 60s timeout is present.
    assert "cleanup_payload, timeout=60)" in body


def test_client_remove_timeout_bumped_to_60():
    """/api/device/remove call: 10 → 60."""
    idx = TAB.find("def _remove_device_from_server(")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    assert "remove_payload, timeout=10)" not in body
    assert "remove_payload, timeout=60)" in body


def test_client_remove_surfaces_partial_status():
    """When server returns HTTP 200 but status:'partial' or
    database_removed:False, the client must show a QMessageBox
    explaining the DB row wasn't deleted."""
    idx = TAB.find("def _remove_device_from_server(")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    assert 'if _status == "removed" and _db_removed:' in body
    assert '"Partial Removal"' in body
    assert "database_removed=False" not in body  # Not a hardcoded check.
    assert "database_removed={_db_removed}" in body


def test_client_remove_surfaces_timeout_explicitly():
    """A requests.exceptions.Timeout must be caught separately and
    shown to the operator with a clear "Remove Timed Out" dialog —
    not swallowed into the generic Exception handler where it
    lands in logger.error and vanishes."""
    idx = TAB.find("def _remove_device_from_server(")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    assert "except requests.exceptions.Timeout as exc:" in body
    assert '"Remove Timed Out"' in body


def test_client_remove_surfaces_non_200_error():
    """A non-200 response must show QMessageBox instead of just
    logger.debug — pre-fix, an operator had no way to know their
    Remove failed at the server."""
    idx = TAB.find("def _remove_device_from_server(")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 6000]
    # The old silent debug is gone for the HTTP-error path.
    assert 'logger.debug(f"Remove API failed:' not in body
    # The warning + QMessageBox path is present.
    assert '"Remove Failed"' in body
    assert "HTTP {remove_resp.status_code}" in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 241)
