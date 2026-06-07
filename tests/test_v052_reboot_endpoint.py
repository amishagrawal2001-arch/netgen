"""Regression test for v0.5.2: Server → Reboot Physical Server
actually reboots the server now.

Operator-reported: clicking Server → Reboot Physical Server
showed "✅ Reboot initiated successfully" in the result dialog
but the target server didn't actually reboot.

Root cause (traffic_client/menu_actions.py:_reboot_servers_list):

  cmd = ["ssh", f"root@{hostname}", "reboot"]
  result = subprocess.run(cmd, capture_output=True, ...)
  if result.returncode == 0 or result.returncode == 255:
      results.append(f"✅ TG {tg_id}: Reboot initiated successfully")

Two compounding bugs:

  1. Passwordless `ssh root@host` rarely works — operators don't
     have root SSH keys distributed. SSH falls back to a password
     prompt, hits subprocess.run's captured-stdin → exits with
     rc=255 "Permission denied".
  2. The code treats rc=255 as SUCCESS (the rationale was "SSH
     disconnected during reboot is expected"). But rc=255 ALSO
     means "Permission denied" / "Connection refused" / "Host key
     verification failed" — the EXACT cases where no reboot
     happened. Operator sees ✅; reality is ✗.

Fix (two halves):

  Server side: new /api/system/reboot endpoint. The server already
  runs as root (per its systemd unit's ExecStart), so it can
  schedule its own reboot via subprocess.Popen — the same pattern
  the existing /api/dpdk/iommu reboot already uses. No SSH
  credentials needed on the client.

  Client side: POST to /api/system/reboot first. HTTP 200 is
  PROOF the reboot was scheduled. Fall back to SSH only on HTTP
  404 (pre-v0.5.1 server). When SSH is the fallback, parse stderr
  for hard-failure markers (Permission denied, Connection refused,
  etc.) and DON'T treat those as success.

This file pins both halves so a future refactor that breaks
either re-introduces the silent-failure operator bug.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
_CLIENT = Path(__file__).resolve().parents[1] / "traffic_client" / "menu_actions.py"


# ─────────────────────────── server endpoint ──────────────────────────


def test_server_reboot_endpoint_exists():
    """POST /api/system/reboot must exist. Without it the client
    has no HTTP path and is forced back to the broken SSH flow."""
    src = _SERVER.read_text()
    assert '@app.route("/api/system/reboot", methods=["POST"])' in src, (
        "POST /api/system/reboot endpoint missing. Client falls "
        "back to the broken `ssh root@host reboot` flow → silent "
        "no-op for operators without root SSH keys."
    )


def test_server_reboot_uses_detached_subprocess():
    """The endpoint must schedule the reboot in a detached
    subprocess — not call /sbin/reboot synchronously. Synchronous
    reboot would kill the Flask thread before HTTP 200 reaches
    the client, leaving the operator with a connection-reset and
    no idea whether the reboot was actually scheduled."""
    src = _SERVER.read_text()
    # Find the function body.
    m = re.search(
        r"def system_reboot\(\)[\s\S]+?(?=\n@app\.route|\n# ---- )",
        src,
    )
    assert m, "system_reboot function body not found"
    body = m.group(0)
    # Must use subprocess.Popen (detached), NOT subprocess.run / call.
    assert "subprocess.Popen" in body, (
        "system_reboot doesn't use Popen — would block Flask thread "
        "and the HTTP 200 wouldn't reach the client."
    )
    # Must NOT use synchronous run/call (those would block until
    # reboot torches the network).
    forbidden = ("subprocess.run(", "subprocess.call(", "subprocess.check_")
    for bad in forbidden:
        assert bad not in body, (
            f"system_reboot uses {bad} which blocks until the reboot "
            f"itself happens — client never receives HTTP 200."
        )


def test_server_reboot_returns_proof_of_scheduling():
    """The HTTP 200 body must include `ok: true` AND the delay so
    the operator can see WHEN the reboot will fire. Empty 200s
    look like silent successes."""
    src = _SERVER.read_text()
    m = re.search(
        r"def system_reboot\(\)[\s\S]+?(?=\n@app\.route|\n# ---- )",
        src,
    )
    body = m.group(0)
    assert '"ok": True' in body, (
        "system_reboot doesn't return ok:true — client can't "
        "distinguish 200-with-body from 200-empty."
    )
    assert '"delay_s"' in body, (
        "system_reboot doesn't echo delay_s — operator can't see "
        "WHEN the reboot fires (immediate vs 30s)."
    )


def test_server_reboot_clamps_delay():
    """A maliciously-large delay_s would silently DoS the operator
    (they'd think the reboot failed and keep retrying). Clamp to
    a reasonable upper bound."""
    src = _SERVER.read_text()
    m = re.search(
        r"def system_reboot\(\)[\s\S]+?(?=\n@app\.route|\n# ---- )",
        src,
    )
    body = m.group(0)
    # Must have a numeric ceiling on delay_s.
    assert re.search(r"min\(\s*\d+\s*,\s*int\(.*delay_s", body), (
        "system_reboot doesn't clamp delay_s with min(N, ...) — "
        "a delay_s=99999 request would silently DoS the operator."
    )


def test_server_reboot_falls_back_if_systemctl_unavailable():
    """`systemctl reboot` is the modern path; older / container
    hosts may lack systemd-init. Fall back to /sbin/reboot or
    bare `reboot` so the endpoint works on a wider matrix."""
    src = _SERVER.read_text()
    m = re.search(
        r"def system_reboot\(\)[\s\S]+?(?=\n@app\.route|\n# ---- )",
        src,
    )
    body = m.group(0)
    assert "systemctl reboot" in body and "/sbin/reboot" in body, (
        "system_reboot's reboot command lacks the systemctl OR "
        "/sbin/reboot fallback. Will fail on hosts that have only "
        "one of the two."
    )


# ─────────────────────────── client dispatcher ────────────────────────


def test_client_tries_http_endpoint_first():
    """Client must call POST /api/system/reboot before falling back
    to SSH. Pre-fix the SSH path was the ONLY path and silently
    failed on hosts without passwordless root SSH."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _reboot_servers_list\(self,[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    assert m, "_reboot_servers_list body not found"
    body = m.group(0)
    assert "/api/system/reboot" in body, (
        "_reboot_servers_list doesn't call /api/system/reboot. "
        "Operators without root SSH keys still hit the silent-"
        "failure path."
    )
    # The HTTP call must come BEFORE the SSH fallback.
    http_idx = body.find("/api/system/reboot")
    ssh_idx = body.find('"ssh"')  # actual SSH subprocess invocation
    assert http_idx >= 0 and ssh_idx >= 0
    assert http_idx < ssh_idx, (
        "SSH fallback runs BEFORE the HTTP endpoint try — that's "
        "the wrong order. HTTP is the new primary path."
    )


def test_client_falls_back_only_on_404():
    """The HTTP fallback to SSH must trigger ONLY on 404 (pre-v0.5.1
    server doesn't have the endpoint). Other HTTP errors (500,
    network) should surface to the operator instead of silently
    falling through to a path that would probably also fail."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _reboot_servers_list\(self,[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    body = m.group(0)
    assert "status_code == 404" in body, (
        "Client doesn't check for HTTP 404 specifically — would "
        "fall back to SSH on every non-200 error, including "
        "transient 500s the operator should see directly."
    )


def test_client_treats_ssh_permission_denied_as_failure():
    """The pre-fix bug: SSH rc=255 was unconditionally treated as
    success. Now rc=255 WITH `Permission denied` (or similar) in
    stderr must be reported as FAILURE."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _reboot_servers_list\(self,[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    body = m.group(0)
    # The hard-fail-markers list must include at least "Permission
    # denied" — that's the operator-reported failure mode.
    assert "permission denied" in body.lower(), (
        "Client doesn't check stderr for 'Permission denied' — "
        "rc=255 with permission-denied stderr would still be "
        "treated as success."
    )
    # And at least one more hard-fail marker — the multi-marker
    # check should catch the cluster of "SSH refused to connect"
    # cases (host-key, refused, no-route).
    hard_markers = ("host key", "connection refused", "no route", "resolve")
    assert any(m in body.lower() for m in hard_markers), (
        "Client's SSH-failure heuristic only checks 'Permission "
        "denied' — host-key / refused / no-route SSH failures "
        "would still false-success."
    )


def test_client_uses_batchmode_to_prevent_password_prompt():
    """`ssh -o BatchMode=yes` forces SSH to fail-fast instead of
    blocking on a password prompt. Without it, SSH may hang on
    the prompt (since subprocess.run captures stdin) until
    timeout, then return rc=255 with a misleading "exit" message."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _reboot_servers_list\(self,[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    body = m.group(0)
    assert "BatchMode=yes" in body, (
        "SSH fallback doesn't pass BatchMode=yes — SSH would block "
        "on a password prompt instead of failing fast."
    )
