"""v0.5.242 — Force Apply lets an operator recover from stale-peer
collisions in the /api/device/apply gates.

Operator on srv06 2026-09-01: after a botched Remove left a stale
DB row (see v0.5.241 — pre-fix `_run_command` hung → Remove
returned before deleting the row), re-Applying the device hit:

    ❌ device4: Failed to apply to server -
    Interface 'ens2f1np1' with VLAN '30' is already in use by
    device 'device4'. To run multiple devices on the same
    physical interface, give each device a different VLAN tag.

The peer device 'device4' was already gone from the UI, but its
DB row survived and blocked every re-Apply. The operator needed a
way to say "the peer is stale, purge it and let me continue."

Fixes:

- **run_tgen_server.py** — `/api/device/apply` honors `force=true`
  in the payload. On the (iface, vlan) collision gate: enumerates
  every peer row on the same (iface, vlan) tuple, deletes them
  from the DB via `device_db.remove_device(...)`, then continues
  with the apply. On the loopback/IP collision gate: skips the
  check entirely (logged as warning). Both gates now return the
  409 error body with `code`, `conflicting_device_id`,
  `conflicting_device_name`, `force_supported: true` so the client
  can render a "Force Apply" prompt without string-matching.

- **widgets/devices_tab.py `_apply_device_to_server_sync`** —
  preserves the full JSON error body as `device_info["_apply_error_details"]`
  and propagates `device_info["_force_apply"]` into the request
  payload as `force: true`.

- **widgets/devices_tab.py `_on_multi_device_apply_finished`** —
  scans failed devices for `_apply_error_details.get("force_supported")`,
  and if any, offers a modal QMessageBox listing the stale peers
  and asking to Force Apply. On Yes, sets `_force_apply=True` on
  those device_infos and re-fires `apply_selected_device_silent`.
  Guarded against retry loops: a device that already has
  `_force_apply=True` is skipped from the offer.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SERVER = (REPO / "run_tgen_server.py").read_text()
UI = (REPO / "widgets" / "devices_tab.py").read_text()


# --- Server: force flag support --------------------------------------


def test_apply_reads_force_flag_from_payload():
    """Both `force` and `force_apply` keys should be accepted for
    forward-compat with any older client patch."""
    idx = SERVER.find("Multi-device-on-same-interface validation gate")
    body = SERVER[idx:idx + 6000]
    assert 'v0.5.242 (audit U force-apply)' in body
    assert '_force_apply = bool(data.get("force") or data.get("force_apply"))' in body


def test_iface_vlan_gate_returns_structured_error_body():
    """On rejection, the response body must include the
    machine-readable fields the client uses to render the prompt."""
    idx = SERVER.find("Multi-device-on-same-interface validation gate")
    body = SERVER[idx:idx + 6000]
    for key in (
        '"code": "duplicate_iface_vlan"',
        '"conflicting_device_id"',
        '"conflicting_device_name"',
        '"force_supported": True',
    ):
        assert key in body, f"missing {key} in structured error body"


def test_iface_vlan_gate_purges_stale_peer_on_force():
    """When force=true, iterate every stale peer on the same
    (iface, vlan) and call device_db.remove_device on each."""
    idx = SERVER.find("Multi-device-on-same-interface validation gate")
    body = SERVER[idx:idx + 6000]
    assert "if _peer_conflict and _force_apply and _stale_peer_ids:" in body
    assert "DeviceDatabase().remove_device(_stale_id)" in body
    # Loud audit log — force is destructive, has to leave a trail.
    assert "force=true: purging" in body


def test_loopback_gate_also_honors_force():
    """The second gate (duplicate loopback/IP) must also skip on
    force so a single operator click covers both."""
    idx = SERVER.find("Duplicate loopback / interface-IP / MAC gate")
    body = SERVER[idx:idx + 4000]
    assert "v0.5.242 (audit U force-apply)" in body
    assert "if _force_apply:" in body
    assert '"force_supported": True' in body


# --- Client: apply payload + error details --------------------------


def test_apply_client_propagates_force_flag_to_payload():
    """`device_info["_force_apply"]` must become `payload["force"]`
    for the /api/device/apply POST."""
    idx = UI.find("def _apply_device_to_server_sync(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert 'v0.5.242' in body
    assert 'device_info.get("_force_apply")' in body
    assert 'basic_payload["force"] = True' in body


def test_apply_client_preserves_full_error_body():
    """Store the full JSON error body so the finish handler can
    render Force Apply without string-matching."""
    idx = UI.find("def _apply_device_to_server_sync(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert 'device_info["_apply_error_details"] = error_details' in body


# --- Client: Force Apply prompt in finish handler -------------------


def test_finish_handler_scans_for_force_supported():
    idx = UI.find("def _on_multi_device_apply_finished(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert "v0.5.242 (audit U force-apply)" in body
    assert '_dev_info.get("_apply_error_details")' in body
    assert 'if not _details.get("force_supported"):' in body


def test_finish_handler_avoids_retry_loop():
    """A device that already has _force_apply=True must NOT be
    offered a second Force Apply prompt — that would loop forever
    if the collision is real (peer is legitimate, not stale)."""
    idx = UI.find("def _on_multi_device_apply_finished(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert 'if _dev_info.get("_force_apply"):' in body
    assert "Already forced" in body


def test_finish_handler_shows_force_apply_dialog():
    idx = UI.find("def _on_multi_device_apply_finished(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert '"Force Apply — purge stale peers?"' in body
    # Retry re-uses the silent apply entry-point.
    assert "QTimer.singleShot(200, self.apply_selected_device_silent)" in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 242)
