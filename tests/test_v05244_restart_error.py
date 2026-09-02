"""v0.5.244 — Restart DHCP endpoint reports actual reason instead of
"Restart failed" + treats client-mode Lease timeout as a soft
success (HTTP 200 pending_lease) not a hard failure (HTTP 500).

Operator on srv06 2026-09-02: clicked Restart DHCP → dialog said
"HTTP 500: Restart failed". No detail on WHY.

Trace: `ensure_dhcp_services` returned
    {'success': False,
     'ipv4': {'success': False, 'error': 'Lease timeout'},
     'ipv6': {'success': False, 'error': 'IPv6 skipped'}}
The endpoint saw `success=False`, no top-level `error` key, and
fell through to the generic hardcoded string "Restart failed".
Meanwhile "Lease timeout" isn't a restart failure at all — dhclient
DID restart, it just hasn't gotten a DHCPOFFER yet (server slow /
unreachable / wrong VLAN). The DHCP monitor will keep polling.

Fixes:

- Server-side `restart_dhcp_service` — helper
  `_pluck_family_errors` extracts per-family errors from
  `result["ipv4"]["error"]` / `result["ipv6"]["error"]` (skipping
  cosmetic "IPv4 skipped" / "IPv6 skipped" markers) and also
  server-mode `result["failures"]` aggregator (v0.5.217). When
  only errors are `Lease timeout` on a client-mode restart,
  return HTTP 200 with `status: "restarted_pending_lease"` +
  `warning` field so the client can render a friendly
  "kicking… waiting for lease" info dialog. Hard failures
  return HTTP 500 with the ACTUAL reason + `family_errors: [...]`
  array.

- Client-side `restart_dhcp_service` in devices_tab_dhcp.py —
  handles the new `restarted_pending_lease` status with an
  Information dialog (not a Warning), and unpacks the
  `family_errors` list from both the hard-fail and pending-lease
  responses so the operator sees "ipv4: Lease timeout" instead
  of an opaque "Restart failed".
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SERVER = (REPO / "run_tgen_server.py").read_text()
UI = (REPO / "utils" / "devices_tab_dhcp.py").read_text()


# --- Server: extract nested errors, distinguish soft vs hard --------


def test_pluck_family_errors_helper_defined_in_restart_endpoint():
    """The helper lives INSIDE the restart handler (per-request scope)
    so it can't accidentally be reused elsewhere with different
    semantics."""
    idx = SERVER.find("def restart_dhcp_service(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 12000]
    assert "v0.5.244 (audit U restart-error)" in body
    assert "def _pluck_family_errors(" in body


def test_pluck_family_errors_reads_ipv4_and_ipv6_subkeys():
    idx = SERVER.find("def _pluck_family_errors(")
    body = SERVER[idx:idx + 2500]
    assert 'for _fam in ("ipv4", "ipv6"):' in body
    # Skips cosmetic "skipped" markers — not real failures.
    assert '"IPv4 skipped"' in body and '"IPv6 skipped"' in body


def test_pluck_family_errors_also_reads_stop_dhcp_server_failures():
    """v0.5.217's server-mode aggregator returns a `failures` list
    on stop_dhcp_server; we surface those too."""
    idx = SERVER.find("def _pluck_family_errors(")
    body = SERVER[idx:idx + 2500]
    assert '_r.get("failures")' in body


def test_client_lease_timeout_returns_http_200_pending_lease():
    """Lease timeout on a client-mode restart is NOT a hard
    failure — dhclient restarted OK, it just hasn't got a lease.
    Response must be HTTP 200 with status='restarted_pending_lease'."""
    idx = SERVER.find("def restart_dhcp_service(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 12000]
    assert 'if _lease_timeout_only and mode == "client":' in body
    assert '"status": "restarted_pending_lease"' in body
    assert '"warning":' in body
    # 200 return path for the pending-lease branch.
    assert "}), 200" in body


def test_hard_failure_surfaces_actual_reason():
    """Non-lease-timeout hard failures must NOT return the generic
    "Restart failed" — must include the extracted family errors."""
    idx = SERVER.find("def restart_dhcp_service(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 12000]
    # The fallback ties the error string to the family_errors list.
    assert 'or "; ".join(_fam_errs)' in body
    # Structured error body includes family_errors for the client.
    assert '"family_errors": _fam_errs,' in body


# --- Client: friendly pending-lease dialog + surface family errors ---


def test_client_handles_restarted_pending_lease_status():
    """Client renders pending-lease as an Information (not Warning)
    dialog with the server's warning text."""
    idx = UI.find("def restart_dhcp_service(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert "v0.5.244" in body
    assert 'if _status == "restarted_pending_lease":' in body
    assert 'QMessageBox.information(' in body
    assert '"DHCP Restarted — Waiting for Lease"' in body


def test_client_surfaces_family_errors_in_error_dialog():
    """Even on hard failure, unpack `family_errors` into a
    per-line detail block so the operator sees the actual reason."""
    idx = UI.find("def restart_dhcp_service(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert '_family_errs = _js.get("family_errors") or []' in body
    assert 'Per-family details:' in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 244)
