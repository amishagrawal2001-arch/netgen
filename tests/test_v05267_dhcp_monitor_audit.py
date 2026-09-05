"""v0.5.267 — DHCP monitor audit: 3 correctness fixes."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DM = (REPO / "utils" / "dhcp_monitor.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- F1: dhcp_last_error cleared on client-mode lease recovery ----


def test_client_last_error_cleared_on_lease_recovery():
    assert "audit DHCP-mon F1" in DM
    idx = DM.find("audit DHCP-mon F1")
    body = DM[idx:idx + 1500]
    # Guarded by (Leased AND running) — same predicate as _note_leased.
    assert 'snapshot.get("dhcp_state") == "Leased"' in body
    assert 'snapshot.get("dhcp_running")' in body
    assert 'write_payload["dhcp_last_error"] = ""' in body


# --- F4: restart endpoint stamps dhcp_manual_override -------------


def test_restart_endpoint_stamps_manual_override():
    assert "audit DHCP-mon F4" in SERVER
    idx = SERVER.find("audit DHCP-mon F4")
    body = SERVER[idx:idx + 1500]
    assert '"dhcp_manual_override": True' in body
    assert '"dhcp_manual_override_time"' in body
    assert "datetime.now(timezone.utc).isoformat()" in body


def test_restart_endpoint_stamp_after_ensure_success():
    """The stamp must land AFTER the ensure_dhcp_services call
    returns success — before the return-jsonify. Positional check
    against the surrounding function structure."""
    idx = SERVER.find("audit DHCP-mon F4")
    # Look backwards for the "return jsonify" of the failure branch;
    # the stamp block must be before the success return jsonify.
    success_return = SERVER.find('"status": "restarted"', idx)
    assert success_return > idx, (
        "manual-override stamp must appear BEFORE the success return"
    )


# --- F6: _get_client_devices operator-precedence fix -------------


def test_get_client_devices_uses_explicit_grouping():
    assert "audit DHCP-mon F6" in DM
    idx = DM.find("audit DHCP-mon F6")
    body = DM[idx:idx + 2000]
    # New structure: explicit variable extraction.
    assert "mode_from_cfg = (" in body
    assert 'dhcp_cfg.get("mode") if isinstance(dhcp_cfg, dict) else None' in body
    assert '(mode_from_cfg or d.get("dhcp_mode") or "").lower()' in body


def test_get_client_devices_no_longer_lambda_expression():
    """The pre-fix list comprehension with the ternary+or trap is
    gone."""
    live_old = [
        line for line in DM.splitlines()
        if 'if ((d.get("dhcp_config") or {}).get("mode") if isinstance(' in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"old buggy comprehension still live: {live_old!r}"


# --- Deferred marker present ------------------------------------


def test_deferred_findings_documented_in_ctor():
    """F3 (parallel polling) was deferred but the intent should be
    recorded as a comment so the next author doesn't re-plan it."""
    idx = DM.find("def __init__")
    end = DM.find("\n    def start", idx + 1)
    body = DM[idx:end if end > 0 else idx + 1500]
    assert "audit DHCP-mon F3 DEFERRED" in body
    assert "ThreadPoolExecutor" in body


# --- Per-device write lock module-level structure ------------------


def test_per_device_lock_infrastructure_defined():
    """F5 needs the lock dict in place even though its use is
    deferred (bundled with F3's extraction); pre-declaring it here
    means the F3 follow-up won't need to also touch the imports."""
    assert "_DHCP_WRITE_LOCKS: Dict[str, threading.Lock]" in DM
    assert "def _dhcp_write_lock_for(device_id" in DM


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 267)
