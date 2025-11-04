# ostg/server.py
import argparse, os, sys

def main(argv=None):
    parser = argparse.ArgumentParser(prog="ostg-server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5050")))
    args = parser.parse_args(argv)

    try:
        import run_tgen_server as m
    except Exception as e:
        print(f"[ostg-server] Failed to import run_tgen_server: {e}", file=sys.stderr)
        raise SystemExit(2)

    # Prefer module's own main()
    if hasattr(m, "main"):
        return m.main([f"--host={args.host}", f"--port={args.port}"])

    # Fallback: run Flask app if present
    if hasattr(m, "app"):
        app = m.app
        # add /healthz if missing
        try:
            rules = [r.rule for r in app.url_map.iter_rules()]
            if "/healthz" not in rules:
                from flask import jsonify
                @app.get("/healthz")
                def _healthz():
                    return jsonify(status="ok")
        except Exception:
            pass
        return app.run(host=args.host, port=args.port)

    raise SystemExit("[ostg-server] Neither main() nor app found in run_tgen_server.py")
