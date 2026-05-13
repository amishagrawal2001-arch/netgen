"""Stateful-TCP REST endpoints, packaged as a Flask Blueprint.

First extraction out of the `run_tgen_server.py` monolith. Sets the
pattern future PRs can follow for state-history, monitor-health, etc.

Why a Blueprint:
    * Same `@bp.route(...)` decorator surface — moving code in is
      mechanical, just s/@app./@bp./
    * `bp.before_app_request` / `bp.after_app_request` can layer
      per-Blueprint hooks later (audit logging, blueprint-scoped
      rate limits) without touching `run_tgen_server.py`.
    * Role enforcement is injected via `configure()` so this module
      stays decoupled from the auth scheme — if/when auth moves out
      of the monolith too, only the injection target changes.

Registration (from run_tgen_server.py)::

    from server import stateful_tcp_bp, configure_stateful_tcp
    configure_stateful_tcp(require_role=require_role)
    app.register_blueprint(stateful_tcp_bp)
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from flask import Blueprint, jsonify, request

try:
    from utils import stateful_tcp as _stateful_tcp
except Exception as _stateful_tcp_imp_err:
    _stateful_tcp = None
    logging.warning(
        f"[STATEFUL TCP] worker module unavailable: {_stateful_tcp_imp_err}"
    )


stateful_tcp_bp = Blueprint("stateful_tcp", __name__)


# Role decorators are configured at app-startup via `configure()` so this
# Blueprint doesn't have to import auth state directly. The default is a
# pass-through — useful for unit tests that import the Blueprint without
# spinning up the full app.
def _noop_role(_role: str):
    def _decorator(fn):
        return fn
    return _decorator


_require_role: Callable[[str], Callable] = _noop_role


def configure(*, require_role: Optional[Callable[[str], Callable]] = None) -> None:
    """Wire in the parent app's role-enforcement decorator.

    Call this BEFORE `app.register_blueprint(stateful_tcp_bp)` so the
    route decorators bake in the real check. Calling it after is a
    no-op — Flask captures decorator state at registration time.
    """
    global _require_role
    if require_role is not None:
        _require_role = require_role


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────


@stateful_tcp_bp.route("/api/stateful_tcp/start", methods=["POST"])
def stateful_tcp_start():
    """Spawn a stateful-TCP client OR server session.

    Body keys: see `utils.stateful_tcp.start_client` / `start_server`
    docstrings for the full surface. Operator-only.
    """
    # Apply the role decorator lazily — see comment on _require_role.
    return _require_role("operator")(_stateful_tcp_start_impl)()


def _stateful_tcp_start_impl():
    if _stateful_tcp is None:
        return jsonify({"error": "stateful_tcp worker not available"}), 500
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception as exc:
        return jsonify({"error": f"bad JSON: {exc}"}), 400

    role = (body.get("role") or "").strip().lower()
    try:
        if role == "client":
            sid = _stateful_tcp.start_client(
                dst_ip=body["dst_ip"],
                dst_port=int(body["dst_port"]),
                src_ip=body.get("src_ip"),
                vrf=body.get("vrf"),
                duration_s=float(body.get("duration_s", 30.0)),
                payload_bytes=int(body.get("payload_bytes", 1024)),
                concurrency=int(body.get("concurrency", 1)),
                connect_timeout_s=float(body.get("connect_timeout_s", 5.0)),
                interval_s=float(body.get("interval_s", 0.0)),
                expect_echo=bool(body.get("expect_echo", True)),
                protocol=body.get("protocol", "raw"),
                tls=bool(body.get("tls", False)),
                tls_verify=bool(body.get("tls_verify", False)),
                tls_server_hostname=body.get("tls_server_hostname"),
                dns_qname=body.get("dns_qname"),
                sip_host=body.get("sip_host"),
                sip_user=body.get("sip_user"),
            )
        elif role == "server":
            sid = _stateful_tcp.start_server(
                listen_port=int(body["listen_port"]),
                listen_ip=body.get("listen_ip", "0.0.0.0"),
                vrf=body.get("vrf"),
                mode=body.get("mode", "echo"),
                protocol=body.get("protocol", "raw"),
                response_bytes=int(body.get("response_bytes", 1024)),
                backlog=int(body.get("backlog", 128)),
                tls=bool(body.get("tls", False)),
                tls_cert=body.get("tls_cert"),
                tls_key=body.get("tls_key"),
                dns_response_rcode=int(body.get("dns_response_rcode", 3)),
                sip_response_status=int(body.get("sip_response_status", 200)),
                sip_response_reason=str(body.get("sip_response_reason", "OK")),
            )
        else:
            return jsonify({"error": "role must be 'client' or 'server'"}), 400
    except KeyError as exc:
        return jsonify({"error": f"missing field: {exc}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"session_id": sid, "role": role}), 200


@stateful_tcp_bp.route("/api/stateful_tcp/stop", methods=["POST"])
def stateful_tcp_stop():
    """Stop one session by ID, or all sessions when no ID is given.
    Operator-only."""
    return _require_role("operator")(_stateful_tcp_stop_impl)()


def _stateful_tcp_stop_impl():
    if _stateful_tcp is None:
        return jsonify({"error": "stateful_tcp worker not available"}), 500
    try:
        body = request.get_json(force=True, silent=True) or {}
        sid = (body.get("session_id") or "").strip()
        if sid:
            ok = _stateful_tcp.stop_session(sid)
            return jsonify({"stopped": bool(ok), "session_id": sid}), 200
        n = _stateful_tcp.stop_all_sessions()
        return jsonify({"stopped_all": True, "count": n}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@stateful_tcp_bp.route("/api/stateful_tcp/sessions", methods=["GET"])
def stateful_tcp_sessions():
    """List every known session (running + finished). Viewer-only."""
    return _require_role("viewer")(_stateful_tcp_sessions_impl)()


def _stateful_tcp_sessions_impl():
    if _stateful_tcp is None:
        return jsonify({"sessions": [], "count": 0}), 200
    sessions = _stateful_tcp.list_sessions()
    return jsonify({"sessions": sessions, "count": len(sessions)}), 200


@stateful_tcp_bp.route("/api/stateful_tcp/stats/<session_id>", methods=["GET"])
def stateful_tcp_stats(session_id):
    """Per-session live counters. Viewer-only."""
    return _require_role("viewer")(_stateful_tcp_stats_impl)(session_id)


def _stateful_tcp_stats_impl(session_id):
    if _stateful_tcp is None:
        return jsonify({"error": "stateful_tcp worker not available"}), 500
    snap = _stateful_tcp.get_session(session_id)
    if snap is None:
        return jsonify({"error": "not found", "session_id": session_id}), 404
    return jsonify(snap), 200
