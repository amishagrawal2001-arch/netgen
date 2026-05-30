"""Shared pytest fixtures.

Split into two groups:

* Path / directory helpers — used by the existing server-side tests
  (preserved verbatim from the original conftest).

* GUI test scaffolding — `qapp` (session-scoped, offscreen QApplication)
  and `client_stub` (factory that builds a minimal stub of the client's
  StreamControl + ServerSection mixins on a QWidget base). The stub
  pattern replaces the ~30-line headless harness that was being copy-
  pasted into every regression repro — see test_client_stream_ops.py
  for what it makes possible.
"""

import os
import sys
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────────────────── path fixtures
@pytest.fixture
def project_root():
    """Return the project root directory path."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def utils_dir(project_root):
    """Return the utils directory path."""
    return os.path.join(project_root, "utils")


@pytest.fixture
def config_dir(project_root):
    """Return the config directory path."""
    return os.path.join(project_root, "config")


# ────────────────────────────────────────────────────────── GUI scaffolding


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped offscreen QApplication for headless GUI tests.

    Force the offscreen Qt platform BEFORE the first QApplication is
    instantiated — otherwise PyQt would try to talk to the host's display
    (no display on CI / headless runs → crash).
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def client_stub(qapp):
    """Factory returning a fresh `_Stub` for client-side regression tests.

    The Stub composes the client's two big mixins — `StreamControl`
    (Streams-tab widgets / handlers) and `ServerSection` (server-tree +
    the `_do_update_stream_table` rebuild) — over a `QWidget` base, so
    Qt-parent-aware operations (selection models, focus, itemWidget)
    behave as they do in the real client. Every external handler the
    mixins reference is stubbed to a no-op so tests can call
    `copy_selected_stream`, `paste_stream_to_interface`, etc. without
    needing a live server, dialogs, or stats workers.

    Pass `streams=` to seed the model and `servers=` to override the
    default single-server `server_interfaces`. The tree is left at None
    (some tests build their own tree with the real itemWidget layout to
    exercise tg_id resolution).

    Modal `QMessageBox` calls are monkey-patched to no-ops so a guard
    failing open doesn't block the test runner.
    """
    from PyQt5.QtWidgets import QWidget
    from PyQt5 import QtWidgets
    from traffic_client.stream_control import TrafficGenClientStreamControl
    from traffic_client.server_section import TrafficGenClientServerSection

    # Every handler `setup_stream_section` wires up (button slots etc.)
    # and every mixin attribute touched during a rebuild. Stubbed at the
    # class level so all instances share them.
    NO_OP_HANDLERS = (
        "open_add_stream_dialog", "edit_selected_stream", "remove_selected_stream",
        "start_stream", "stop_stream", "_toggle_all_streams", "apply_stream",
        "update_all_streams_toggle_ui", "handle_enabled_combo_change",
        "handle_flow_tracking_change", "update_stream_empty_state",
        "ensure_unique_stream_ids", "_find_table_row",
        "_prepare_tx_rate", "_prepare_duration", "_streams_in_flight",
    )

    class _Stub(TrafficGenClientStreamControl,
                TrafficGenClientServerSection, QWidget):
        def __init__(self, streams=None, servers=None):
            QWidget.__init__(self)
            self.streams = streams or {}
            self.server_interfaces = servers or [
                {"tg_id": "0", "address": "http://1.1.1.1", "online": True}
            ]
            self.server_tree = None

    for name in NO_OP_HANDLERS:
        setattr(_Stub, name, (lambda self, *a, **k: None))

    # _alloc_stream_id is called from paste — give it a real (uuid) impl
    # so pasted streams get unique IDs.
    import uuid
    _Stub._alloc_stream_id = lambda self, extra_used=None: str(uuid.uuid4())

    # Silence modal dialogs in headless runs.
    QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
    QtWidgets.QMessageBox.critical = staticmethod(lambda *a, **k: None)

    def _make(streams=None, servers=None):
        s = _Stub(streams=streams, servers=servers)
        s.setup_stream_section(s)
        return s

    return _make
