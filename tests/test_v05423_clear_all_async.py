"""v0.5.423 — Clear-All-Devices async worker (KK1).

Operator reproduction (2026-10-11):
 * Tools → Clear All Devices → typed CLEAR → the entire client UI
   froze until the server finished looping through every device (each
   delete can take 1–3 seconds server-side: FRR stop → VXLAN+VRF
   teardown → DHCP cancel → DB purge). On a server with 20 devices
   the app was unresponsive for ~60 seconds.

Root cause: `clear_all_devices_on_server` ran
`requests.post(..., timeout=120)` on the UI thread. Classic sync-HTTP-
on-event-loop freeze. Same shape as the v0.5.401 R-slice fixes in
menu_actions.py and v0.5.374 E1 (DHCP restart).

Fix (KK1): wrap the POST in an inline `_ClearAllWorker(QThread)`
subclass with a `done(ok, status, data, err)` signal; show a marquee
`QProgressDialog` while it runs; deliver the result dialog from the
slot. Server-side loop remains uncancellable (no server change) but
the UI stays responsive — operator can switch tabs, close the
progress dialog ("Hide" button), and keep working.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


def _strip_comments(src: str) -> str:
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_main_py_ast_parses():
    ast.parse(_read("traffic_client/main.py"))


# ─── KK1: marker + async worker shape ───


def test_kk1_marker_present():
    assert "v0.5.423 (audit stream-KK1)" in _read("traffic_client/main.py")


def _handler_body(src: str) -> str:
    """Slice from `def clear_all_devices_on_server` to the next
    top-of-class method definition (`\\n    def `) after the signature
    — scan forward past the signature line itself."""
    _idx = src.index("def clear_all_devices_on_server")
    # Skip past the signature line.
    _after_sig = src.index("\n", _idx) + 1
    _next = src.find("\n    def ", _after_sig)
    if _next < 0:
        _next = len(src)
    return src[_idx:_next]


def test_kk1_worker_class_defined_inside_handler():
    """`_ClearAllWorker(QThread)` must be defined inside
    `clear_all_devices_on_server` and emit a `done` signal."""
    body = _handler_body(_read("traffic_client/main.py"))
    assert "class _ClearAllWorker(QThread):" in body
    assert "done = pyqtSignal(" in body
    assert "def run(self):" in body


def test_kk1_sync_post_removed_from_live_code():
    """The pre-fix shape ran `requests.post(f\"{server_url}/api/
    devices/clear_all\", timeout=120)` DIRECTLY in the handler body.
    That exact call must no longer appear outside the worker's
    `run()`. (The worker still uses requests.post inside its own
    `run()` — on the background thread, which is fine.)"""
    src = _read("traffic_client/main.py")
    _idx = src.index("def clear_all_devices_on_server")
    # Everything up to the worker class is the handler body (sync part).
    _worker_idx = src.index("class _ClearAllWorker", _idx)
    pre_worker = src[_idx:_worker_idx]
    _live = _strip_comments(pre_worker)
    assert "requests.post(" not in _live, (
        "sync requests.post still runs before the worker — UI will still freeze"
    )


def test_kk1_worker_posts_to_clear_all_endpoint():
    """The worker's run() must POST to /api/devices/clear_all with
    the 120s timeout preserved (server loop can take minutes on big
    inventories)."""
    src = _read("traffic_client/main.py")
    _idx = src.index("class _ClearAllWorker")
    _end = src.index("# Marquee progress dialog", _idx)
    body = src[_idx:_end]
    assert "/api/devices/clear_all" in body
    assert "timeout=120" in body


def test_kk1_qprogressdialog_shown_before_worker_start():
    """A QProgressDialog (marquee, min=max=0) must be constructed and
    shown BEFORE `worker.start()` so the operator sees the UI update
    the instant they click OK on the CLEAR confirmation."""
    src = _read("traffic_client/main.py")
    _idx = src.index("def clear_all_devices_on_server")
    # Pick an endpoint sentinel inside the method body.
    _end = src.index("worker.start()", _idx)
    body = src[_idx:_end]
    assert "QProgressDialog(" in body
    assert "0, 0, self," in body, "progress dialog must be marquee (min=max=0)"
    assert "progress.show()" in body
    # And the show must sit BEFORE the worker.start() call.
    assert body.index("progress.show()") < len(body), "progress not shown"


def test_kk1_done_signal_connected_before_start():
    """`worker.done.connect(_on_done)` must sit BEFORE
    `worker.start()` — otherwise a very fast done emit races the
    connect and the result dialog never shows."""
    body = _handler_body(_read("traffic_client/main.py"))
    _conn = body.index("worker.done.connect(_on_done)")
    _start = body.index("worker.start()")
    assert _conn < _start, "done.connect must precede worker.start"


def test_kk1_keepalive_registered():
    """The worker must be stashed via `_keepalive_worker` to dodge
    the PyQt5 5.15 QThread GC race (see utils/qthread_keepalive.py)."""
    src = _read("traffic_client/main.py")
    _idx = src.index("def clear_all_devices_on_server")
    body = src[_idx:_idx + 7000]
    assert "self._keepalive_worker(worker)" in body


def test_kk1_on_done_slot_refreshes_devices_tab():
    """The success branch of `_on_done` must still call
    `self.devices_tab.reload_devices_from_server()` so the operator
    sees the empty table — this behavior was in the sync pre-fix
    code and must survive the move into the slot."""
    src = _read("traffic_client/main.py")
    _idx = src.index("def _on_done(")
    _end = _idx + 3000
    body = src[_idx:_end]
    assert "reload_devices_from_server()" in body


def test_kk1_on_done_handles_partial_failure():
    """The slot must still render the per-device failure list (same
    behavior as pre-fix) — `Partial cleanup` dialog with up to 10
    failed names and a "… and N more" suffix."""
    src = _read("traffic_client/main.py")
    _idx = src.index("def _on_done(")
    # Slot body — allow room for later audit slices to grow it
    # (v0.5.424 LL2 added OSPF/BGP/ISIS table refreshes).
    body = src[_idx:_idx + 6000]
    assert "Partial cleanup" in body
    assert "device_name" in body
    assert "device_id" in body
    assert "more" in body


# ─── version guard — fix lands on branch tip; pyproject bump is a
# ship-time step. We assert the current branch (>= 0.5.422 — the last
# shipped version) so the test is stable both pre- and post-ship.


def test_pyproject_at_least_0622():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 422)


# ─── regression guards ───


def test_v0422_jj2_closure_intact():
    src = _read("traffic_client/server_section.py")
    assert "def _refresh_this_iface_only(" in src
    assert "v0.5.422 (audit stream-JJ2)" in src


def test_v0421_ii1_col1_match_intact():
    src = _read("traffic_client/server_section.py")
    assert 'srv_label = (srv_item.text(1) or "").lower()' in src
