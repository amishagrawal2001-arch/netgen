#!/usr/bin/env python3
"""netgen-cli — headless companion to the netgen-client GUI.

Wraps the most common multi-device workflows in a small curl-driven
script so they can run from CI / scripts / a tmux pane without needing
an X display. Everything it does is a thin layer over the REST API
documented in the in-app Help → API Guide.

Subcommands
-----------
    netgen-cli health      [-s URL]
    netgen-cli list        [-s URL]
    netgen-cli export      [-s URL] [-o devices.json]
    netgen-cli import      [-s URL] [-f devices.json] [--wait]
    netgen-cli apply       [-s URL] -f single_device.json [--wait]
    netgen-cli status      [-s URL] [-i DEVICE_ID]
    netgen-cli wait        [-s URL] [-i DEVICE_ID] [--timeout 60]

Server URL: defaults to $NETGEN_SERVER_URL, then http://localhost:5050.
Auth: if $NETGEN_AUTH_TOKEN is set, an `Authorization: Bearer …` header
is added to every request automatically (same scheme as the GUI uses
since the auth middleware shipped).
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    print("netgen-cli: the `requests` library is required (pip install requests).",
          file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------- helpers


def _default_server_url() -> str:
    return os.environ.get("NETGEN_SERVER_URL", "http://localhost:5050").rstrip("/")


def _auth_headers() -> Dict[str, str]:
    """Mirror of run_tgen_client.py's auth bootstrap — auto-injects the
    bearer token when NETGEN_AUTH_TOKEN is set."""
    tok = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _api(method: str, base: str, path: str, **kwargs) -> requests.Response:
    """Single point for every HTTP call — merges auth headers and a
    short connect-timeout so unreachable servers fail fast."""
    headers = dict(kwargs.pop("headers", None) or {})
    headers.update(_auth_headers())
    kwargs.setdefault("timeout", 30)
    return requests.request(method, f"{base}{path}", headers=headers, **kwargs)


def _exit_fail(msg: str, code: int = 1) -> None:
    print(f"netgen-cli: {msg}", file=sys.stderr)
    sys.exit(code)


def _print_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


# --------------------------------------------------------------------- commands


def cmd_health(args) -> int:
    """Probe /api/health and /api/monitors/health on one call."""
    base = args.server
    try:
        r = _api("GET", base, "/api/health", timeout=5)
    except Exception as exc:
        _exit_fail(f"unreachable: {exc}")
    if r.status_code != 200:
        _exit_fail(f"/api/health returned HTTP {r.status_code}: {r.text[:200]}", code=3)
    health = r.json()
    try:
        m = _api("GET", base, "/api/monitors/health", timeout=5).json()
    except Exception:
        m = {"ok": "unknown", "monitors": {}}
    _print_json({"server": health, "monitors": m})
    return 0


def cmd_list(args) -> int:
    """Dump device DB rows."""
    r = _api("GET", args.server, "/api/device/database/devices")
    if r.status_code != 200:
        _exit_fail(f"HTTP {r.status_code}: {r.text[:200]}", code=3)
    _print_json(r.json())
    return 0


def cmd_export(args) -> int:
    """Export every device's configuration (no runtime state) to JSON."""
    r = _api("GET", args.server, "/api/devices/export")
    if r.status_code != 200:
        _exit_fail(f"HTTP {r.status_code}: {r.text[:200]}", code=3)
    payload = r.json()
    if args.output and args.output != "-":
        with open(args.output, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"exported {payload.get('count', 0)} device(s) → {args.output}")
    else:
        _print_json(payload)
    return 0


def cmd_import(args) -> int:
    """Apply a previously-exported (or hand-rolled) device topology."""
    if not args.file or args.file == "-":
        try:
            payload = json.load(sys.stdin)
        except Exception as exc:
            _exit_fail(f"could not read stdin JSON: {exc}")
    else:
        try:
            with open(args.file, "r") as f:
                payload = json.load(f)
        except Exception as exc:
            _exit_fail(f"could not read {args.file}: {exc}")

    r = _api("POST", args.server, "/api/devices/import",
             json=payload, timeout=300)
    if r.status_code != 200:
        _exit_fail(f"HTTP {r.status_code}: {r.text[:200]}", code=3)
    result = r.json()
    print(f"imported: {result.get('imported', 0)} / {result.get('total', 0)} "
          f"(failed: {result.get('failed', 0)})")
    for err in (result.get("errors") or [])[:10]:
        print(f"  ✗ {err}")
    if args.wait:
        devices = payload.get("devices", []) if isinstance(payload, dict) else []
        for d in devices:
            did = d.get("device_id")
            if did:
                _wait_for_device(args.server, did, args.timeout)
    return 0 if result.get("failed", 0) == 0 else 4


def cmd_apply(args) -> int:
    """Apply one device config from a JSON file (single-device shape)."""
    if not args.file:
        _exit_fail("apply requires -f/--file")
    try:
        with open(args.file, "r") as f:
            cfg = json.load(f)
    except Exception as exc:
        _exit_fail(f"could not read {args.file}: {exc}")

    r = _api("POST", args.server, "/api/device/apply", json=cfg, timeout=60)
    if r.status_code != 200:
        _exit_fail(f"HTTP {r.status_code}: {r.text[:200]}", code=3)
    _print_json(r.json())
    if args.wait and cfg.get("device_id"):
        _wait_for_device(args.server, cfg["device_id"], args.timeout)
    return 0


def cmd_status(args) -> int:
    """Dump device + protocol status."""
    base = args.server
    if args.device_id:
        out = {"device_id": args.device_id}
        for proto, path in (
            ("device", f"/api/device/database/devices/{args.device_id}"),
            ("arp",    f"/api/device/arp/{args.device_id}"),
            ("bgp",    f"/api/bgp/status/{args.device_id}"),
            ("ospf",   f"/api/ospf/status/{args.device_id}"),
            ("isis",   f"/api/isis/status/{args.device_id}"),
        ):
            try:
                r = _api("GET", base, path, timeout=10)
                out[proto] = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            except Exception as exc:
                out[proto] = {"error": str(exc)}
        _print_json(out)
    else:
        r = _api("GET", base, "/api/device/database/devices")
        if r.status_code != 200:
            _exit_fail(f"HTTP {r.status_code}", code=3)
        devs = r.json().get("devices", [])
        for d in devs:
            print(f"{d.get('device_id', '?'):<40}  {d.get('device_name', '?'):<20}  "
                  f"{d.get('status', '?'):<12}  ARP={d.get('arp_status', '?')}")
    return 0


# --------------------------------------------------------------------- internals


def _wait_for_device(base: str, device_id: str, timeout: int) -> bool:
    """Poll /api/device/arp/<id> until arp_resolved=true or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = _api("GET", base, f"/api/device/arp/{device_id}", timeout=5)
            if r.status_code == 200:
                if r.json().get("arp_resolved"):
                    print(f"  ✓ {device_id} ready ({int(time.time() - start)}s)")
                    return True
        except Exception:
            pass
        time.sleep(2)
    print(f"  ! {device_id} not ready after {timeout}s")
    return False


def cmd_wait(args) -> int:
    """Block until a device's ARP is fully resolved, or timeout."""
    if not args.device_id:
        _exit_fail("wait requires -i/--device-id")
    ok = _wait_for_device(args.server, args.device_id, args.timeout)
    return 0 if ok else 5


# --------------------------------------------------------------------- main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="netgen-cli",
        description="Headless CLI for the Netgen REST API.",
    )
    parser.add_argument(
        "-s", "--server", default=_default_server_url(),
        help="Server URL (default: $NETGEN_SERVER_URL or http://localhost:5050)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_health = sub.add_parser("health", help="Probe /api/health + /api/monitors/health")
    p_health.set_defaults(func=cmd_health)

    p_list = sub.add_parser("list", help="List devices from the DB")
    p_list.set_defaults(func=cmd_list)

    p_export = sub.add_parser("export", help="Export devices to JSON")
    p_export.add_argument("-o", "--output", help="Output file (default: stdout)")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", help="Import devices from JSON")
    p_import.add_argument("-f", "--file", help="JSON file (default: stdin)")
    p_import.add_argument("--wait", action="store_true", help="Wait for each device's ARP to resolve")
    p_import.add_argument("--timeout", type=int, default=60, help="Per-device wait timeout (s)")
    p_import.set_defaults(func=cmd_import)

    p_apply = sub.add_parser("apply", help="Apply one device from JSON")
    p_apply.add_argument("-f", "--file", required=True)
    p_apply.add_argument("--wait", action="store_true")
    p_apply.add_argument("--timeout", type=int, default=60)
    p_apply.set_defaults(func=cmd_apply)

    p_status = sub.add_parser("status", help="Print device + protocol status")
    p_status.add_argument("-i", "--device-id", help="Single device (else lists all)")
    p_status.set_defaults(func=cmd_status)

    p_wait = sub.add_parser("wait", help="Block until a device's ARP resolves")
    p_wait.add_argument("-i", "--device-id", required=True)
    p_wait.add_argument("--timeout", type=int, default=60)
    p_wait.set_defaults(func=cmd_wait)

    args = parser.parse_args(argv)
    args.server = args.server.rstrip("/")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
