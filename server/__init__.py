"""Netgen server-side route modules.

Pattern-setter for the broader `run_tgen_server.py` modularization
(see #9 on the roadmap). Each protocol surface gets its own
Flask Blueprint here so they can be registered from the monolith
without making it any bigger.

Currently exporting:
    * `stateful_tcp_bp` — /api/stateful_tcp/* endpoints
    * `device_db_bp`    — /api/device/database/devices/<id>/{events,history,statistics}
    * `events_bp`       — /api/events/{stream,status} (Server-Sent Events live feed)
    * `l2_bp`           — /api/l2/{lacp,lldp,vrrp,igmp,pim}/* (L2 + multicast emulators)

Migration plan
--------------
Move more route groups out one Blueprint at a time. The contract
each Blueprint follows:

  * Define routes against `bp = Blueprint(name, __name__)`.
  * Take any role-decorator / app-level helpers as imports from the
    parent module (or, for newer endpoints, accept them as a small
    `setup(...)` call from `run_tgen_server.py`).
  * Register from `run_tgen_server.py` via `app.register_blueprint(bp)`.

The goal isn't a sweeping rewrite — it's to set the precedent so
future surfaces (state-history, monitor health, device export/import)
can land in their own modules without rewriting auth or logging.
"""

from server.stateful_tcp_routes import stateful_tcp_bp, configure as configure_stateful_tcp  # noqa: F401
from server.device_db_routes import device_db_bp, configure as configure_device_db  # noqa: F401
from server.events_routes import events_bp, configure as configure_events  # noqa: F401
from server.l2_routes import l2_bp, configure as configure_l2  # noqa: F401

__all__ = [
    "stateful_tcp_bp", "configure_stateful_tcp",
    "device_db_bp", "configure_device_db",
    "events_bp", "configure_events",
    "l2_bp", "configure_l2",
]
