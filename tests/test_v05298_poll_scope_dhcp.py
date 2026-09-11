"""v0.5.298 — poll_device_status must include DHCP-mode rows.

srv06 2026-09-11 (v0.5.297 running): operator screenshot showed
device4 (DHCP-client) IPv4 column STILL empty. Server-side data
correct (dhcp_lease_ip=192.16.30.105, status=Running). v0.5.297
code (which lives in _apply_device_status_row) was in the wheel.

Root cause: poll_device_status's filter — `if status == "Running":
rows_to_refresh.append(row); elif status in ("Starting","Stopping")`
— ONLY refreshes rows whose local-cached Status is one of those
three. Devices whose client-cached Status is empty / Stopped /
Unknown are SKIPPED → _apply_device_status_row never runs →
v0.5.297's IPv4 refresh code never fires → IPv4 stays empty.

The catch-22: for a fresh session load where the client cache says
Status="Stopped" but the server says "Running", the poll skips
until something (Start, Stop, manual F5) breaks the cache stale-
ness. For a DHCP-client that always needs lease-state polling,
this is fatal.

Fix: extend the poll filter to include any row whose device_info
has dhcp_mode set (client or server) regardless of Status. Cheap
— the per-device HTTP fetch is already async and gated at 30s.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def _src() -> str:
    return (REPO / "widgets" / "devices_tab.py").read_text()


def test_v05298_marker_present():
    assert "v0.5.298 (audit poll-scope-dhcp)" in _src()


def test_poll_reads_dhcp_mode_from_device_info():
    src = _src()
    idx = src.find("def poll_device_status")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert 'device_info.get("dhcp_mode")' in body
    assert 'device_info.get("DHCP Mode")' in body


def test_poll_appends_dhcp_client_rows_even_when_status_not_running():
    """The critical wiring — the new elif branch that catches
    DHCP-mode devices whose Status is Stopped/empty/Unknown."""
    src = _src()
    idx = src.find("def poll_device_status")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "elif _needs_dhcp_refresh:" in body
    assert "rows_to_refresh.append(row)" in body


def test_poll_dhcp_refresh_gates_on_client_or_server():
    """Regression: only DHCP-mode devices should get the extra
    refresh — not every device."""
    src = _src()
    idx = src.find("def poll_device_status")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert '_dhcp_mode in ("client", "server")' in body


def test_poll_still_counts_running_devices():
    """Regression: v0.5.215's running_count logic (for polling
    cadence) must survive the v0.5.298 addition."""
    src = _src()
    idx = src.find("def poll_device_status")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "running_count += 1" in body
    assert 'if status == "Running":' in body


def test_v05215_transient_status_still_included():
    """Regression: v0.5.215 fix (include Stopping) must survive."""
    src = _src()
    idx = src.find("def poll_device_status")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert '"Starting", "Stopping"' in body


def test_v05297_refresh_code_intact():
    """Regression: my v0.5.297 code in _apply_device_status_row
    must survive — that's what actually updates the cell."""
    assert "v0.5.297 (audit dhcp-lease-display-guard)" in _src()


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 298)
