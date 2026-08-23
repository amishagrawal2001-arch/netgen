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


def cmd_l2(args) -> int:
    """L2 frame generator subcommands: start-<protocol>, stop, list, stats."""
    base = args.server
    action = args.action
    if action.startswith("start-"):
        proto = action.removeprefix("start-")
        body = {"iface": args.iface}
        # Map argparse attributes back into the REST body verbatim.
        # Protocol-specific knobs are sparse since most have sensible
        # defaults baked into utils/l2_protocols.py.
        for attr in (
            "system_priority", "system_mac", "key", "port_priority",
            "port_number", "state", "fast",
            "chassis_id", "port_id", "system_name", "system_description",
            "ttl_s", "interval_s", "duration_s", "src_mac",
            "version", "vrid", "priority", "virtual_ips", "src_ip",
            "family", "group", "type_code",
            "hold_time", "dr_priority", "generation_id",
        ):
            v = getattr(args, attr, None)
            if v is not None:
                body[attr] = v
        r = _api("POST", base, f"/api/l2/{proto}/start", json=body, timeout=15)
    elif action == "stop":
        body = {"session_id": args.session_id} if args.session_id else {}
        r = _api("POST", base, "/api/l2/stop", json=body, timeout=10)
    elif action == "list":
        r = _api("GET", base, "/api/l2/sessions", timeout=10)
    elif action == "stats":
        if not args.session_id:
            _exit_fail("stats requires --session-id")
        r = _api("GET", base, f"/api/l2/stats/{args.session_id}", timeout=10)
    else:
        _exit_fail(f"unknown l2 action: {action}")
        return 2
    if r.status_code != 200:
        _exit_fail(f"HTTP {r.status_code}: {r.text[:200]}", code=3)
    _print_json(r.json())
    return 0


def cmd_wait(args) -> int:
    """Block until a device's ARP is fully resolved, or timeout."""
    if not args.device_id:
        _exit_fail("wait requires -i/--device-id")
    ok = _wait_for_device(args.server, args.device_id, args.timeout)
    return 0 if ok else 5


# --------------------------------------------------------------------- stateful TCP
#
# Thin wrapper over /api/stateful_tcp/*. The point is to let an operator
# kick off a real TCP test without having to hand-build curl commands;
# the heavy lifting lives in utils/stateful_tcp.py.


def cmd_tcp(args) -> int:
    """Stateful-TCP subcommands: start-client, start-server, stop, list, stats."""
    base = args.server
    action = args.action
    if action == "start-client":
        body = {
            "role": "client",
            "dst_ip": args.dst_ip,
            "dst_port": args.dst_port,
            "duration_s": args.duration,
            "payload_bytes": args.payload_bytes,
            "concurrency": args.concurrency,
            "interval_s": args.interval,
            "expect_echo": not args.no_echo,
            "protocol": args.protocol,
            "tls": args.tls,
            "tls_verify": args.tls_verify,
        }
        if args.src_ip:
            body["src_ip"] = args.src_ip
        if args.vrf:
            body["vrf"] = args.vrf
        if args.tls_server_hostname:
            body["tls_server_hostname"] = args.tls_server_hostname
        r = _api("POST", base, "/api/stateful_tcp/start", json=body, timeout=10)
    elif action == "start-server":
        body = {
            "role": "server",
            "listen_port": args.port,
            "listen_ip": args.bind,
            "mode": args.mode,
            "protocol": args.protocol,
            "response_bytes": args.response_bytes,
            "tls": args.tls,
        }
        if args.vrf:
            body["vrf"] = args.vrf
        if args.tls_cert:
            body["tls_cert"] = args.tls_cert
        if args.tls_key:
            body["tls_key"] = args.tls_key
        r = _api("POST", base, "/api/stateful_tcp/start", json=body, timeout=10)
    elif action == "stop":
        body = {"session_id": args.session_id} if args.session_id else {}
        r = _api("POST", base, "/api/stateful_tcp/stop", json=body, timeout=10)
    elif action == "list":
        r = _api("GET", base, "/api/stateful_tcp/sessions", timeout=10)
    elif action == "stats":
        if not args.session_id:
            _exit_fail("stats requires --session-id")
        r = _api("GET", base, f"/api/stateful_tcp/stats/{args.session_id}", timeout=10)
    else:
        _exit_fail(f"unknown tcp action: {action}")
        return 2

    if r.status_code != 200:
        _exit_fail(f"HTTP {r.status_code}: {r.text[:200]}", code=3)
    try:
        _print_json(r.json())
    except Exception:
        sys.stdout.write(r.text + "\n")
    return 0


# ---------------------------------------------------------------- license


def cmd_license_status(args) -> int:
    """Print the currently-loaded License in a shell-friendly form."""
    from utils import license as _lic
    result = _lic.load()
    days = result.days_until_expiry()
    print(f"valid:            {result.is_valid}")
    print(f"reason:           {result.reason}")
    print(f"tier / billing:   {result.license_type or '?'} / {result.billing_type or '?'}")
    print(f"email:            {result.email or '?'}")
    print(f"end_date:         "
          f"{result.end_date.isoformat() if result.end_date else '?'}")
    print(f"session_expires:  "
          f"{result.expiry.isoformat(timespec='seconds') if result.expiry else '?'}")
    print(f"days_remaining:   {days if days is not None else '?'}")
    print(f"in_grace_period:  {result.in_grace_period()}")
    print(f"fingerprint:      {_lic.machine_fingerprint()}")
    return 0 if result.is_valid else 1


def cmd_license_activate(args) -> int:
    """Save a JWT from --token or --file."""
    from utils import license as _lic
    token = ""
    if args.token and args.file:
        print("error: --token and --file are mutually exclusive",
              file=sys.stderr)
        return 2
    if args.token:
        token = args.token.strip()
    elif args.file:
        try:
            token = open(args.file, "r", encoding="utf-8").read().strip()
        except OSError as exc:
            print(f"error: cannot read {args.file}: {exc}",
                  file=sys.stderr)
            return 2
    else:
        print("error: pass --token OR --file", file=sys.stderr)
        return 2
    # Sanity check BEFORE clobbering ~/.netgen/license.jwt.
    check = _lic.verify_jwt(token)
    if not check.is_valid:
        print(f"error: license rejected — {check.reason}",
              file=sys.stderr)
        return 1
    saved = _lic.save(token)
    print(f"activated: tier={saved.license_type or '?'} "
          f"billing={saved.billing_type or '?'} "
          f"expires={saved.end_date.isoformat() if saved.end_date else '?'}")
    return 0


def cmd_license_deactivate(args) -> int:
    from utils import license as _lic
    if not _lic.LICENSE_FILE.exists():
        print("no license loaded; nothing to do")
        return 0
    _lic.remove()
    print("deactivated")
    return 0


def cmd_license_trial(args) -> int:
    from utils import license as _lic
    result = _lic.start_trial()
    if not result.is_valid:
        print(f"error: {result.reason}", file=sys.stderr)
        return 1
    days = result.days_until_expiry()
    print(f"trial started: expires "
          f"{result.end_date.isoformat() if result.end_date else '?'} "
          f"({days} day(s) from now)")
    return 0


def cmd_license_fingerprint(args) -> int:
    from utils import license as _lic
    print(_lic.machine_fingerprint())
    return 0


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

    # v0.5.183: license subcommands — activate / status / deactivate.
    # Same underlying `utils.license` module as the GUI dialog. For
    # headless / CI: `netgen-cli license activate --token <jwt>`.
    p_lic = sub.add_parser(
        "license", help="Manage the local netgen license")
    lic_sub = p_lic.add_subparsers(dest="action", required=True)

    p_lic_status = lic_sub.add_parser(
        "status", help="Print the currently loaded license")
    p_lic_status.set_defaults(func=cmd_license_status)

    p_lic_activate = lic_sub.add_parser(
        "activate",
        help="Save + verify a license JWT (from --token or --file)")
    p_lic_activate.add_argument(
        "--token",
        help="The raw JWT string (mutually exclusive with --file)")
    p_lic_activate.add_argument(
        "--file",
        help="Path to a file containing the JWT")
    p_lic_activate.set_defaults(func=cmd_license_activate)

    p_lic_deactivate = lic_sub.add_parser(
        "deactivate", help="Remove the local license file")
    p_lic_deactivate.set_defaults(func=cmd_license_deactivate)

    p_lic_trial = lic_sub.add_parser(
        "trial", help="Start the local self-service trial")
    p_lic_trial.set_defaults(func=cmd_license_trial)

    p_lic_fp = lic_sub.add_parser(
        "fingerprint",
        help="Print this device's fingerprint (to send to your license issuer)")
    p_lic_fp.set_defaults(func=cmd_license_fingerprint)

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

    # tcp <action> — stateful TCP foundation. One subparser per action
    # keeps the args clean instead of a giant flag soup.
    p_tcp = sub.add_parser(
        "tcp",
        help="Stateful TCP traffic — real handshakes via OS sockets",
    )
    tcp_sub = p_tcp.add_subparsers(dest="action", required=True)

    p_tcp_sc = tcp_sub.add_parser("start-client", help="Start a stateful-TCP client session")
    p_tcp_sc.add_argument("--dst-ip", required=True)
    p_tcp_sc.add_argument("--dst-port", type=int, required=True)
    p_tcp_sc.add_argument("--src-ip", default=None)
    p_tcp_sc.add_argument("--vrf", default=None,
                          help="Linux VRF/iface name (SO_BINDTODEVICE)")
    p_tcp_sc.add_argument("--duration", type=float, default=30.0, help="Run time in seconds")
    p_tcp_sc.add_argument("--payload-bytes", type=int, default=1024)
    p_tcp_sc.add_argument("--concurrency", type=int, default=1)
    p_tcp_sc.add_argument("--interval", type=float, default=0.0,
                          help="Sleep between connections per sender (s)")
    p_tcp_sc.add_argument("--no-echo", action="store_true",
                          help="Don't expect response — send + close immediately")
    p_tcp_sc.add_argument("--protocol", choices=("raw", "http"), default="raw",
                          help="L7 framing on top of TCP")
    p_tcp_sc.add_argument("--tls", action="store_true", help="Wrap connection in TLS")
    p_tcp_sc.add_argument("--tls-verify", action="store_true",
                          help="Enforce cert+hostname verification (default off)")
    p_tcp_sc.add_argument("--tls-server-hostname", default=None,
                          help="SNI / hostname check target (defaults to --dst-ip)")
    p_tcp_sc.set_defaults(func=cmd_tcp)

    p_tcp_ss = tcp_sub.add_parser("start-server", help="Start a stateful-TCP listener")
    p_tcp_ss.add_argument("--port", type=int, required=True)
    p_tcp_ss.add_argument("--bind", default="0.0.0.0")
    p_tcp_ss.add_argument("--vrf", default=None,
                          help="Linux VRF/iface name (SO_BINDTODEVICE)")
    p_tcp_ss.add_argument("--mode", choices=("echo", "discard"), default="echo")
    p_tcp_ss.add_argument("--protocol", choices=("raw", "http"), default="raw",
                          help="L7 framing on top of TCP")
    p_tcp_ss.add_argument("--response-bytes", type=int, default=1024,
                          help="HTTP body size (only when --protocol=http)")
    p_tcp_ss.add_argument("--tls", action="store_true")
    p_tcp_ss.add_argument("--tls-cert", default=None, help="Server cert PEM path")
    p_tcp_ss.add_argument("--tls-key", default=None, help="Server key PEM path")
    p_tcp_ss.set_defaults(func=cmd_tcp)

    p_tcp_stop = tcp_sub.add_parser("stop", help="Stop a session (or all sessions)")
    p_tcp_stop.add_argument("--session-id", default=None,
                            help="Specific session ID (omit to stop all)")
    p_tcp_stop.set_defaults(func=cmd_tcp)

    p_tcp_list = tcp_sub.add_parser("list", help="List known TCP sessions")
    p_tcp_list.set_defaults(func=cmd_tcp)

    p_tcp_stats = tcp_sub.add_parser("stats", help="Live counters for one session")
    p_tcp_stats.add_argument("--session-id", required=True)
    p_tcp_stats.set_defaults(func=cmd_tcp)

    # L2 frame generators + multicast protocols
    p_l2 = sub.add_parser(
        "l2",
        help="L2 frame generators — LACP / LLDP / VRRP / IGMP / PIM-Hello",
    )
    l2_sub = p_l2.add_subparsers(dest="action", required=True)

    # `--iface` is required by every start-*; defining it once on the
    # parent action would shadow the action-specific args, so we add
    # it per-action via _add_iface().
    def _add_iface(p):
        p.add_argument("--iface", required=True,
                       help="Network interface to send frames on (eth0, ens1, …)")
        p.add_argument("--duration-s", dest="duration_s", type=float, default=None,
                       help="Stop after N seconds (default: run forever)")
        p.add_argument("--interval-s", dest="interval_s", type=float, default=None)
        return p

    # LACP
    p_lacp = _add_iface(l2_sub.add_parser("start-lacp",
        help="Start an LACP (802.1AX) frame emitter"))
    p_lacp.add_argument("--system-mac", dest="system_mac", default=None)
    p_lacp.add_argument("--system-priority", dest="system_priority", type=int, default=None)
    p_lacp.add_argument("--key", type=int, default=None)
    p_lacp.add_argument("--port-priority", dest="port_priority", type=int, default=None)
    p_lacp.add_argument("--port-number", dest="port_number", type=int, default=None)
    p_lacp.add_argument("--state", type=int, default=None,
                        help="LACP state bits (0x01=Activity, 0x04=Aggregation, …)")
    p_lacp.add_argument("--fast", action="store_true",
                        help="1s cadence (LACP_Short_Timeout) — default is 30s")
    p_lacp.set_defaults(func=cmd_l2)

    # LLDP
    p_lldp = _add_iface(l2_sub.add_parser("start-lldp",
        help="Start an LLDP (802.1AB) advertiser"))
    p_lldp.add_argument("--chassis-id", dest="chassis_id", default=None)
    p_lldp.add_argument("--port-id", dest="port_id", default=None)
    p_lldp.add_argument("--system-name", dest="system_name", default=None)
    p_lldp.add_argument("--system-description", dest="system_description", default=None)
    p_lldp.add_argument("--ttl-s", dest="ttl_s", type=int, default=None)
    p_lldp.add_argument("--src-mac", dest="src_mac", default=None)
    p_lldp.set_defaults(func=cmd_l2)

    # VRRP
    p_vrrp = _add_iface(l2_sub.add_parser("start-vrrp",
        help="Start a VRRP master advertiser (v2 or v3, IPv4 or IPv6)"))
    p_vrrp.add_argument("--version", type=int, choices=(2, 3), default=None)
    p_vrrp.add_argument("--vrid", type=int, default=None)
    p_vrrp.add_argument("--priority", type=int, default=None)
    p_vrrp.add_argument("--virtual-ips", dest="virtual_ips", nargs="+", default=None,
                        help="One or more virtual IP addresses")
    p_vrrp.add_argument("--src-ip", dest="src_ip", default=None)
    p_vrrp.add_argument("--src-mac", dest="src_mac", default=None)
    p_vrrp.add_argument("--family", choices=("ipv4", "ipv6"), default=None)
    p_vrrp.set_defaults(func=cmd_l2)

    # IGMP
    p_igmp = _add_iface(l2_sub.add_parser("start-igmp",
        help="Start an IGMP membership-report emitter"))
    p_igmp.add_argument("--version", type=int, choices=(2, 3), default=None)
    p_igmp.add_argument("--group", default=None, help="Multicast group address")
    p_igmp.add_argument("--type-code", dest="type_code", type=lambda x: int(x, 0), default=None,
                        help="Override IGMP type byte (e.g. 0x17 for v2 Leave)")
    p_igmp.add_argument("--src-ip", dest="src_ip", default=None)
    p_igmp.add_argument("--src-mac", dest="src_mac", default=None)
    p_igmp.set_defaults(func=cmd_l2)

    # PIM Hello
    p_pim = _add_iface(l2_sub.add_parser("start-pim",
        help="Start a PIM Hello (RFC 7761) emitter"))
    p_pim.add_argument("--hold-time", dest="hold_time", type=int, default=None)
    p_pim.add_argument("--dr-priority", dest="dr_priority", type=int, default=None)
    p_pim.add_argument("--generation-id", dest="generation_id",
                       type=lambda x: int(x, 0), default=None)
    p_pim.add_argument("--src-ip", dest="src_ip", default=None)
    p_pim.add_argument("--src-mac", dest="src_mac", default=None)
    p_pim.set_defaults(func=cmd_l2)

    # Generic stop/list/stats
    p_l2_stop = l2_sub.add_parser("stop", help="Stop an L2 session (all if no ID)")
    p_l2_stop.add_argument("--session-id", default=None)
    p_l2_stop.set_defaults(func=cmd_l2)

    p_l2_list = l2_sub.add_parser("list", help="List L2 sessions")
    p_l2_list.set_defaults(func=cmd_l2)

    p_l2_stats = l2_sub.add_parser("stats", help="Live counters for one L2 session")
    p_l2_stats.add_argument("--session-id", required=True)
    p_l2_stats.set_defaults(func=cmd_l2)

    args = parser.parse_args(argv)
    args.server = args.server.rstrip("/")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
