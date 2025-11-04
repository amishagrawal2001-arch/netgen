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
        msg.setWindowTitle("OSTG Client - Startup Error")
        msg.setText(str(e))
        msg.setDetailedText(tb)
        msg.exec_()
        return 1

def main(argv=None):
    # Check if -s or --server was explicitly provided before parsing
    if argv is None:
        check_argv = sys.argv[1:]  # Skip script name
    else:
        check_argv = argv
    server_explicitly_provided = any(arg in check_argv for arg in ['-s', '--server'])
    
    parser = argparse.ArgumentParser(
        prog="ostg-client",
        description="OSTG Traffic Generator GUI client"
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
