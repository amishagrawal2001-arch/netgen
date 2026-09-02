"""v0.5.247 — Startup reaper for orphan DHCP containers.

Operator on srv06 2026-09-02: `docker ps` showed two `dhcp-client-*`
containers when only one device existed in the DB. The extra one
(`dhcp-client-7f539da4-…`) was left behind by a pre-v0.5.241
`/api/device/remove` that hung indefinitely in `stop_dhcp_client`
(before v0.5.241's container.exec_run timeout fix). The DB row
was eventually cleared, but nothing ever went back to kill the
orphan container.

Fix: on every server startup, enumerate every container whose
name starts with `dhcp-client-`, `dhcp-server-`, or `dhcp-frr-`,
extract the device_id suffix, and force-remove any container
whose device_id is not in the current DB. Best-effort, logged
loudly, non-fatal.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- Reaper implementation ------------------------------------------


def test_reap_orphan_dhcp_containers_helper_exists():
    assert "def reap_orphan_dhcp_containers(device_db)" in DHCP
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert "v0.5.247 (audit U orphan-container-reap)" in body


def test_reaper_scans_all_three_dhcp_prefixes():
    """`dhcp-client-`, `dhcp-server-`, and `dhcp-frr-` — the three
    container-name prefixes the DHCP layer manages. Missing any
    leaves that class of orphan uncleaned."""
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert 'f"{DHCP_CLIENT_PREFIX}-"' in body
    assert 'f"{DHCP_SERVER_PREFIX}-"' in body
    assert '"dhcp-frr-"' in body


def test_reaper_returns_structured_result_dict():
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert '"scanned": 0' in body
    assert '"orphans_reaped": 0' in body
    assert '"orphan_names":' in body
    assert '"errors":' in body


def test_reaper_snapshots_known_ids_once():
    """Avoid racing with concurrent device add/remove — take one
    snapshot of the known-device set, then iterate containers."""
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert "_known = {d.get(\"device_id\") for d in _devs if d.get(\"device_id\")}" in body


def test_reaper_force_removes_orphans_and_logs_loudly():
    """Force-remove + warning-level log so the operator sees what
    got reaped in the journal."""
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert "c.remove(force=True)" in body
    assert "logger.warning(" in body
    assert "[DHCP REAP] Orphan container" in body


def test_reaper_handles_docker_daemon_absent():
    """If docker.from_env or containers.list fails, the reaper
    must return an error-marked dict instead of raising — caller
    is on the boot path and must not be blocked."""
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert "docker.from_env failed" in body
    assert "docker containers.list failed" in body


def test_reaper_handles_none_device_db():
    idx = DHCP.find("def reap_orphan_dhcp_containers(device_db)")
    body = DHCP[idx:idx + 4500]
    assert "if device_db is None:" in body


# --- Startup wiring --------------------------------------------------


def test_server_startup_calls_reaper():
    """The wire-in lives in run_tgen_server.py main() alongside
    the tx_worker orphan sweep. Best-effort, non-fatal."""
    idx = SERVER.find("v0.5.247 (audit U orphan-container-reap)")
    assert idx > 0, "startup reaper call missing from server main()"
    body = SERVER[idx:idx + 2500]
    assert "_dhcp.reap_orphan_dhcp_containers(device_db)" in body
    # Failure path is logged but doesn't propagate.
    assert "non-fatal, server continues" in body


def test_server_startup_logs_reap_summary():
    """Report at least the orphan count + names so the operator
    knows what happened in the journal."""
    idx = SERVER.find("v0.5.247 (audit U orphan-container-reap)")
    body = SERVER[idx:idx + 2500]
    assert 'orphans_reaped' in body
    assert 'orphan_names' in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 247)
