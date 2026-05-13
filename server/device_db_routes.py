"""Device-DB query endpoints, packaged as a Flask Blueprint.

Second extraction out of the run_tgen_server.py monolith. Same
contract as `stateful_tcp_routes.py`:

* Define routes against a Blueprint.
* Take any external dependencies (the DeviceDatabase instance, the
  role-decorator) via a `configure(...)` call before Blueprint
  registration so this module stays decoupled.

Routes bundled here:
  * GET  /api/device/database/devices/<device_id>/events
  * GET  /api/device/database/devices/<device_id>/history[/<protocol>]
  * GET  /api/device/database/devices/<device_id>/statistics
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from flask import Blueprint, jsonify, request


device_db_bp = Blueprint("device_db", __name__)


# Injected at configure() time. Defaults are inert so the module
# imports cleanly without an app context (useful for unit tests).
_device_db = None


def _noop_role(_role: str):
    def _decorator(fn):
        return fn
    return _decorator


_require_role: Callable[[str], Callable] = _noop_role

# Valid protocols for the /history/<protocol> route. Mirrors the set
# the monitor modules can produce. Typos return 400 instead of an
# empty list — "the history is broken" bug reports were almost always
# /history/bgpp instead of /bgp.
STATE_HISTORY_PROTOCOLS = {"bgp", "ospf", "isis", "arp", "dhcp"}


def configure(*, device_db=None, require_role: Optional[Callable] = None) -> None:
    """Wire in the parent app's DeviceDatabase + role decorator."""
    global _device_db, _require_role
    if device_db is not None:
        _device_db = device_db
    if require_role is not None:
        _require_role = require_role


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────


@device_db_bp.route(
    "/api/device/database/devices/<device_id>/events", methods=["GET"]
)
def get_device_events(device_id):
    """Free-form ad-hoc event log for a device (anything the monitors
    or apply path wanted to log). Viewer-only."""
    return _require_role("viewer")(_get_device_events_impl)(device_id)


def _get_device_events_impl(device_id):
    if _device_db is None:
        return jsonify({"error": "device_db not configured"}), 500
    try:
        limit = request.args.get("limit", 100, type=int)
        events = _device_db.get_device_events(device_id, limit)
        return jsonify({"events": events, "count": len(events)}), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] events fetch failed for {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@device_db_bp.route(
    "/api/device/database/devices/<device_id>/history", methods=["GET"]
)
@device_db_bp.route(
    "/api/device/database/devices/<device_id>/history/<protocol>", methods=["GET"]
)
def get_device_state_history(device_id, protocol=None):
    """Per-protocol state-transition history for a device.

    Each row is a *change* — the monitors de-dup repeated polls of the
    same state, so this surface is the minimal change-only timeline you
    want to power a "what happened to BGP last hour" view. Viewer-only.

    Path params:
      device_id – any device row
      protocol  – one of bgp / ospf / isis / arp / dhcp (or omitted
                  for an interleaved cross-protocol view). Typos
                  return 400 rather than silently empty rows.

    Query params:
      limit  – max rows (default 50)
    """
    return _require_role("viewer")(_get_device_state_history_impl)(
        device_id, protocol
    )


def _get_device_state_history_impl(device_id, protocol):
    if _device_db is None:
        return jsonify({"error": "device_db not configured"}), 500
    try:
        if protocol is not None:
            proto_norm = protocol.strip().lower()
            if proto_norm not in STATE_HISTORY_PROTOCOLS:
                return jsonify({
                    "error": f"unknown protocol {protocol!r}; "
                             f"valid: {sorted(STATE_HISTORY_PROTOCOLS)}",
                }), 400
            protocol = proto_norm
        limit = request.args.get("limit", 50, type=int)
        rows = _device_db.get_state_history(device_id, protocol=protocol, limit=limit)
        return jsonify({
            "device_id": device_id,
            "protocol": protocol,
            "history": rows,
            "count": len(rows),
        }), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] history fetch failed for {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@device_db_bp.route(
    "/api/device/database/devices/<device_id>/statistics", methods=["GET"]
)
def get_device_statistics(device_id):
    """Per-device statistics rollup over the last N hours. Viewer-only."""
    return _require_role("viewer")(_get_device_statistics_impl)(device_id)


def _get_device_statistics_impl(device_id):
    if _device_db is None:
        return jsonify({"error": "device_db not configured"}), 500
    try:
        hours = request.args.get("hours", 24, type=int)
        stats = _device_db.get_device_statistics(device_id, hours)
        return jsonify({"statistics": stats, "count": len(stats)}), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] statistics fetch failed for {device_id}: {e}")
        return jsonify({"error": str(e)}), 500
