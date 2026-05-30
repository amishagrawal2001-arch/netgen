"""L2 frame generators + multicast protocols REST surface.

Fourth Blueprint extracted from the monolith (after stateful_tcp,
device_db, events). Same `configure(require_role=...)` injection
pattern so auth state stays decoupled.

Routes:
  POST /api/l2/<protocol>/start      role=operator
  POST /api/l2/stop                  role=operator   (by session_id or all)
  GET  /api/l2/sessions              role=viewer
  GET  /api/l2/stats/<session_id>    role=viewer

Supported `<protocol>` values: lacp, lldp, vrrp, igmp, pim
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from flask import Blueprint, jsonify, request


l2_bp = Blueprint("l2", __name__)


try:
    from utils import l2_protocols as _l2
except Exception as _l2_imp_err:
    _l2 = None
    logging.warning(f"[L2] utils.l2_protocols unavailable: {_l2_imp_err}")


def _noop_role(_role: str):
    def _decorator(fn):
        return fn
    return _decorator


_require_role: Callable[[str], Callable] = _noop_role


def configure(*, require_role: Optional[Callable] = None) -> None:
    global _require_role
    if require_role is not None:
        _require_role = require_role


# ──────────────────────────────────────────────────────────────────────
# Per-protocol start handlers — each one validates required fields and
# dispatches to the matching utils.l2_protocols.start_* factory.
# ──────────────────────────────────────────────────────────────────────


# Every factory also accepts vlan_id + vlan_pcp (inline 802.1Q tag) and,
# as of 0.2.60, outer_vlan_id + outer_vlan_pcp for 802.1ad QinQ (S-VLAN).
# Appended to each allow-list so the body fields reach the factory.
_VLAN_KEYS = ("vlan_id", "vlan_pcp", "outer_vlan_id", "outer_vlan_pcp")
_PROTOCOL_FACTORIES = {
    "lacp": ("start_lacp",      ("system_priority", "system_mac", "key",
                                 "port_priority", "port_number", "state",
                                 "fast", "duration_s") + _VLAN_KEYS),
    "lldp": ("start_lldp",      ("chassis_id", "port_id", "system_name",
                                 "system_description", "ttl_s",
                                 "interval_s", "duration_s", "src_mac") + _VLAN_KEYS),
    "vrrp": ("start_vrrp",      ("version", "vrid", "priority",
                                 "virtual_ips", "interval_s", "duration_s",
                                 "src_ip", "src_mac", "family") + _VLAN_KEYS),
    "igmp": ("start_igmp",      ("version", "group", "type_code",
                                 "interval_s", "duration_s",
                                 "src_ip", "src_mac") + _VLAN_KEYS),
    "pim":  ("start_pim_hello", ("hold_time", "dr_priority", "generation_id",
                                 "interval_s", "duration_s",
                                 "src_ip", "src_mac") + _VLAN_KEYS),
}


@l2_bp.route("/api/l2/<protocol>/start", methods=["POST"])
def l2_start(protocol):
    """Spawn an L2 frame generator session. Operator-only."""
    return _require_role("operator")(_l2_start_impl)(protocol)


def _l2_start_impl(protocol):
    if _l2 is None:
        return jsonify({"error": "l2_protocols module not available"}), 500
    proto = (protocol or "").strip().lower()
    if proto not in _PROTOCOL_FACTORIES:
        return jsonify({
            "error": f"unknown protocol {protocol!r}",
            "supported": sorted(_PROTOCOL_FACTORIES.keys()),
        }), 400

    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception as exc:
        return jsonify({"error": f"bad JSON: {exc}"}), 400

    iface = body.get("iface")
    if not iface:
        return jsonify({"error": "missing field: iface"}), 400

    factory_name, allowed_keys = _PROTOCOL_FACTORIES[proto]
    factory = getattr(_l2, factory_name, None)
    if factory is None:
        return jsonify({"error": f"factory {factory_name} not exported"}), 500

    # Filter the body to only the kwargs this factory understands —
    # this way the same body shape is forward-compatible if we later
    # add fields to a factory.
    kwargs = {k: body[k] for k in allowed_keys if k in body}

    try:
        sid = factory(iface=iface, **kwargs)
    except TypeError as exc:
        return jsonify({"error": f"bad arguments: {exc}"}), 400
    except Exception as exc:
        logging.error(f"[L2] {factory_name} failed: {exc}")
        return jsonify({"error": str(exc)}), 500
    return jsonify({"session_id": sid, "protocol": proto, "iface": iface}), 200


@l2_bp.route("/api/l2/stop", methods=["POST"])
def l2_stop():
    """Stop one L2 session by ID, or all if no session_id given."""
    return _require_role("operator")(_l2_stop_impl)()


def _l2_stop_impl():
    if _l2 is None:
        return jsonify({"error": "l2_protocols module not available"}), 500
    try:
        body = request.get_json(force=True, silent=True) or {}
        sid = (body.get("session_id") or "").strip()
        if sid:
            ok = _l2.stop_session(sid)
            return jsonify({"stopped": bool(ok), "session_id": sid}), 200
        n = _l2.stop_all_sessions()
        return jsonify({"stopped_all": True, "count": n}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@l2_bp.route("/api/l2/sessions", methods=["GET"])
def l2_sessions():
    """List every L2 session (running + finished). Viewer-only."""
    return _require_role("viewer")(_l2_sessions_impl)()


def _l2_sessions_impl():
    if _l2 is None:
        return jsonify({"sessions": [], "count": 0}), 200
    sessions = _l2.list_sessions()
    return jsonify({"sessions": sessions, "count": len(sessions)}), 200


@l2_bp.route("/api/l2/stats/<session_id>", methods=["GET"])
def l2_stats(session_id):
    """Per-session live counters (frames sent / failed / bytes). Viewer-only."""
    return _require_role("viewer")(_l2_stats_impl)(session_id)


def _l2_stats_impl(session_id):
    if _l2 is None:
        return jsonify({"error": "l2_protocols module not available"}), 500
    snap = _l2.get_session(session_id)
    if snap is None:
        return jsonify({"error": "not found", "session_id": session_id}), 404
    return jsonify(snap), 200
