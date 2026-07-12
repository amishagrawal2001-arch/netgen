# run_tgen_client.py
# ostg/client.py
import os, sys, argparse, traceback

# Fix Qt platform plugin issue on macOS
if sys.platform == 'darwin':
    import site
    site_packages = site.getsitepackages()[0] if site.getsitepackages() else None
    if site_packages:
        qt_plugin_path = os.path.join(site_packages, 'PyQt5', 'Qt5', 'plugins')
        if os.path.exists(qt_plugin_path):
            os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt
from traffic_client.main import TrafficGeneratorClient

DEFAULT_URL = os.environ.get("OSTG_SERVER_URL", "http://127.0.0.1:5051")

def launch(server_url: str, fullscreen: bool, server_explicitly_provided: bool = False):
    # Propagate for code paths that read from env
    if server_url:
        os.environ["OSTG_SERVER_URL"] = server_url

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontUseNativeMenuBar)

    # Install the process-global QThread keepalive BEFORE building the
    # main window (which spawns interface-fetch / stats-poll workers on
    # startup). On PyQt5 5.15.11 + Python 3.14 a QThread whose last
    # Python ref drops during the gap between run() returning and Qt's
    # internal teardown completing gets its C++ object deleted by PyQt
    # mid-teardown → "QThread: Destroyed while thread is still running"
    # → SIGABRT on launch. This hook makes every QThread.start() pin a
    # strong ref in a global registry (trimmed once workers are provably
    # done), eliminating the race app-wide. See utils/qthread_keepalive.py.
    try:
        from utils.qthread_keepalive import install as _install_qthread_keepalive
        _install_qthread_keepalive()
    except Exception as _e:
        print(f"[WARN] QThread keepalive install failed: {_e}", file=sys.stderr)
    # Identify the app so QSettings has a deterministic on-disk
    # location across macOS / Linux. Used by the Settings dialog
    # (File → Settings…) and any other key/value preferences.
    app.setOrganizationName("Netgen")
    app.setOrganizationDomain("netgen.local")
    app.setApplicationName("netgen-client")

    # v0.5.116: set the in-app window icon. Picks
    # resources/icons/netgen.png as the canonical 256×256; the
    # macOS Dock icon, Windows taskbar icon, and Linux WM icon
    # come from the platform bundle (.icns / .ico / .desktop+png)
    # built in the PyInstaller spec or AppImage scripts, not from
    # this call — but Qt's window-titlebar icon + alt-tab icon
    # come from QApplication.setWindowIcon. Without this, the
    # PyQt default (a generic computer glyph) shows in the
    # title bar even when the Dock has the right .icns.
    try:
        from PyQt5.QtGui import QIcon as _QIcon
        _icon_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "resources", "icons",
        )
        _icon_path = os.path.join(_icon_dir, "netgen.png")
        if os.path.exists(_icon_path):
            app.setWindowIcon(_QIcon(_icon_path))
    except Exception as _e:
        # Best-effort — a missing icon is cosmetic, not fatal.
        print(f"[WARN] window icon load failed: {_e}", file=sys.stderr)

    # Optional bearer-token auth — mirror of the server-side
    # NETGEN_AUTH_TOKEN gate in run_tgen_server.py. When set in the
    # client's environment, every `requests.{get,post,put,…}` call
    # auto-injects an `Authorization: Bearer <token>` header so the
    # 100+ HTTP sites scattered through traffic_client / widgets
    # don't each need bespoke wiring. Override the per-call header
    # by passing `headers={"Authorization": "Bearer …"}` explicitly.
    _auth_token = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
    if _auth_token:
        import requests as _rq
        def _wrap_request_fn(fn):
            def _patched(url, **kwargs):
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("Authorization", f"Bearer {_auth_token}")
                kwargs["headers"] = headers
                return fn(url, **kwargs)
            return _patched
        for _m in ("get", "post", "put", "delete", "patch", "head", "options"):
            try:
                setattr(_rq, _m, _wrap_request_fn(getattr(_rq, _m)))
            except Exception:
                pass

    # v0.5.183: license gate. If the user has no cached license, or
    # the cached one has expired / been tampered with, show the
    # blocking activation dialog. Cancel/X → exit 0, no window.
    try:
        from widgets.license_activation_dialog import (
            run_activation_gate,
        )
        if not run_activation_gate(parent=None):
            return 0
    except Exception as _e:
        # A blown-up license dialog should NOT lock the operator
        # out of their tool — fall back to legacy behaviour with a
        # loud warning. Server-side gates would still enforce this
        # if we add them later.
        print(f"[WARN] license gate failed: {_e}", file=sys.stderr)

    try:
        # Preferred: widget takes server_url and explicit flag
        try:
            window = TrafficGeneratorClient(server_url=server_url, server_explicitly_provided=server_explicitly_provided)
        except TypeError:
            # Fallback: older client that reads OSTG_SERVER_URL itself
            # If server was explicitly provided, set it in the instance
            window = TrafficGeneratorClient()
            if server_explicitly_provided and server_url:
                window.server_url = server_url
                window.server_url_from_cli = True

        if fullscreen:
            window.showFullScreen()
        else:
            window.show()

        return app.exec_()

    except Exception as e:
        # Surface the real cause to the user
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Netgen Client - Startup Error")
        msg.setText(str(e))
        msg.setDetailedText(tb)
        msg.exec_()
        return 1

def main(argv=None):
    # v0.5.175: defensive global socket timeout — any blocking
    # socket op (incl. getaddrinfo via _socket.getaddrinfo) caps
    # at this many seconds. Without it, macOS DNS lookups for an
    # unresolvable lab hostname (VPN dropped, host off network)
    # block 30+ seconds at the OS level, freezing the Qt event
    # loop. Operator hit this on `san-hp-srv06` and had to Ctrl+C
    # the GUI. The per-request `timeout=5` arg in `requests.get()`
    # only covers connect/read AFTER DNS resolves — it doesn't
    # bound the getaddrinfo call itself. socket.setdefaulttimeout
    # does, at the cost of also bounding intentionally-long-lived
    # connections (we have none in the client).
    import socket
    socket.setdefaulttimeout(8.0)

    # Check if -s or --server was explicitly provided before parsing
    if argv is None:
        check_argv = sys.argv[1:]  # Skip script name
    else:
        check_argv = argv
    server_explicitly_provided = any(arg in check_argv for arg in ['-s', '--server'])
    
    parser = argparse.ArgumentParser(
        prog="ostg-client",
        description="Netgen Traffic Generator GUI client"
    )
    parser.add_argument(
        "-s", "--server",
        nargs='?',  # Optional value
        const=DEFAULT_URL,  # If -s is provided without value, use DEFAULT_URL
        default=None,  # If -s is not provided at all, default to None
        help="Server base URL (e.g., http://10.0.0.5:5051). "
             "If not provided, loads servers from session.json. "
             "If -s is provided without value, uses default."
    )
    parser.add_argument("--fullscreen", action="store_true", help="Launch fullscreen")
    args = parser.parse_args(argv)
    
    # Determine the server URL based on whether it was explicitly provided
    if server_explicitly_provided:
        # Server was explicitly provided via CLI
        final_server_url = args.server if args.server is not None else DEFAULT_URL
    else:
        # Server was not explicitly provided, pass None to let client load from session.json
        final_server_url = None
    
    sys.exit(launch(final_server_url, args.fullscreen, server_explicitly_provided))

if __name__ == "__main__":
    main()
