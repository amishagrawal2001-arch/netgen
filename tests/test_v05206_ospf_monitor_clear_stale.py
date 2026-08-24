"""v0.5.206: OSPF monitor clears the DB when the FRR container
is gone, instead of silently skipping the update and letting a
stale Full/Backup snapshot linger.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: `sudo docker ps`
on srv06 showed no `device_*` container for the OSPF-configured
device, but the netgen client still displayed a green OSPF row
for 10.254.0.102 with `Full/Backup`, priority 128, dead-timer
~33s counting down, uptime ~49s. The client reads
`/api/ospf/status/database/<id>` (the DB, populated by the
OSPF monitor) — and the monitor's 404 branch (utils/
ospf_monitor.py `_check_single_device_ospf_status`) returned
`None`, which the caller (`_check_ospf_status_batch`) silently
skipped. Result: the last-known snapshot stayed frozen in the
DB indefinitely.

ISIS monitor (utils/isis_monitor.py:180-206) already handles
this case by writing an "all down" snapshot when the container
is missing. OSPF was the parity gap. Fix: on 404, return a
synthesized all-down status dict so the caller writes it to
the DB and the UI shows Down within one monitor cycle (~10s).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05206_test_{os.getpid()}.db"),
)


def _make_monitor():
    from utils.ospf_monitor import OSPFStatusMonitor
    device_db = MagicMock()
    return OSPFStatusMonitor(device_db=device_db,
                             server_url="http://fake",
                             check_interval=1,
                             max_workers=1)


def test_404_returns_synthesized_down_status_not_none():
    """The KEY fix. Pre-v0.5.206 the 404 branch returned None
    and the caller silently skipped the DB update; post-fix
    it returns an all-down dict the caller writes through."""
    from utils import ospf_monitor as m
    monitor = _make_monitor()

    fake_response = MagicMock()
    fake_response.status_code = 404
    with patch.object(m.requests, "get", return_value=fake_response):
        result = monitor._check_single_device_ospf_status({"device_id": "d1"})

    assert result is not None, (
        "404 branch still returns None — the caller will skip the "
        "DB update and stale snapshots will keep lingering."
    )
    assert result["ospf_established"] is False
    assert result["ospf_state"] == "Down"
    assert result["neighbors"] == []
    assert result["ospf_ipv4_established"] is False
    assert result["ospf_ipv6_established"] is False
    assert result["ospf_ipv4_running"] is False
    assert result["ospf_ipv6_running"] is False


def test_synthesized_down_status_flows_to_db_update():
    """End-to-end: monitor loop sees a 404 → synthesized down →
    _update_device_ospf_status writes empty neighbors + all-down
    flags to the DB. This is what actually clears the stale
    Full/Backup entry the operator was seeing."""
    from utils import ospf_monitor as m
    monitor = _make_monitor()

    # `device_data` returned by the DB — no manual override so
    # the update path proceeds.
    monitor.device_db.get_device.return_value = {
        "device_id": "d1", "ospf_manual_override": False}
    monitor.device_db.update_device_statistics = MagicMock()
    monitor.device_db.update_device = MagicMock()

    fake_response = MagicMock()
    fake_response.status_code = 404
    with patch.object(m.requests, "get", return_value=fake_response):
        monitor._check_ospf_status_batch([{"device_id": "d1"}])

    # Both tables get the all-down write.
    stats_call = monitor.device_db.update_device_statistics.call_args
    dev_call = monitor.device_db.update_device.call_args
    assert stats_call is not None, \
        "update_device_statistics never called — monitor silently skipped"
    stats_payload = stats_call.args[1]
    dev_payload = dev_call.args[1]

    assert stats_payload["ospf_established"] is False
    assert stats_payload["ospf_neighbors"] is None  # cleared
    assert stats_payload["ospf_ipv4_established"] is False
    assert stats_payload["ospf_ipv6_established"] is False

    assert dev_payload["ospf_established"] is False
    assert dev_payload["ospf_neighbors"] is None
    assert dev_payload["ospf_state"] == "Down"


def test_non_200_non_404_still_returns_none():
    """Transient 5xx / network errors should NOT synthesize a
    Down write — that would flap the UI whenever the server
    briefly stumbles. Only 404 (definitively missing) gets the
    clear."""
    from utils import ospf_monitor as m
    monitor = _make_monitor()

    fake_response = MagicMock()
    fake_response.status_code = 500
    with patch.object(m.requests, "get", return_value=fake_response):
        result = monitor._check_single_device_ospf_status({"device_id": "d1"})

    assert result is None


def test_200_with_real_status_returns_it_unchanged():
    """Live path regression guard — a real 200 with an OSPF
    status dict must pass straight through."""
    from utils import ospf_monitor as m
    monitor = _make_monitor()
    real_status = {"ospf_established": True, "ospf_state": "Established",
                   "neighbors": [{"neighbor_id": "10.0.0.1",
                                  "state": "Full/BDR", "type": "IPv4"}]}
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"ospf_status": real_status}

    with patch.object(m.requests, "get", return_value=fake_response):
        result = monitor._check_single_device_ospf_status({"device_id": "d1"})

    assert result == real_status


def test_source_404_branch_synthesizes_down():
    """Source-level lock-in — if a refactor deletes the
    synthesis or reverts to `return None`, the operator's
    original bug is back."""
    src = (REPO / "utils" / "ospf_monitor.py").read_text()
    tail = src.split("status_code == 404", 1)[1][:1200]
    # Must not immediately return None from the 404 branch —
    # the return must include a synthesized status dict.
    assert "'ospf_established': False" in tail, \
        "404 branch no longer synthesizes an all-down status dict"
    assert "'ospf_state': 'Down'" in tail, \
        "404 branch no longer sets ospf_state='Down'"
    assert "'neighbors': []" in tail, \
        "404 branch no longer clears neighbors"
