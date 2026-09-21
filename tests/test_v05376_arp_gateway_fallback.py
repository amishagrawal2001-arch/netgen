"""v0.5.376 — ARP gateway fallback to protocol-monitor peer.

Operator on srv06 2026-09-20 reported device1 stuck amber ⚠ in the
Devices tab even though:
    switch → ping 192.168.0.2       # works, 0.4ms
    vrf-fdde6b42126 → ping 192.168.0.1   # works, 0.34ms
    vrf-fdde6b42126 → ping6 2001:db8::1  # works, 0.37ms

But `/api/device/arp/<id>` returned:
    "ipv4_target": "192.168.0.2",         # device's OWN IP
    "gateway_target": "",                  # DB gw was empty
    "ipv4_ping": "failed",                 # own-IP ping via VRF fails
    "arp_ipv4_resolved": false,
    "arp_status": "Failed"

Root cause chain:
  1. Add/Edit Device dialog captured 192.168.0.1 as ipv4_gateway
     in the UI, but the DB row's ipv4_gateway is empty (client-
     side persistence gap — F2, deferred to v0.5.377).
  2. Devices-tab UI hides the DB miss by cross-referencing
     ospf_neighbors[0].address to DISPLAY a value in the IPv4
     Gateway column — operator sees 192.168.0.1 and thinks it's
     persisted. It isn't.
  3. Server-side ARP check reads ipv4_gateway=='' → falls back to
     `ipv4_target = ipv4_gateway or ipv4_address` → pings the
     device's own IP 192.168.0.2 from within the device's VRF.
  4. That ping fails: per-VRF local table (1000-3999) doesn't
     have a /32 route for the own IP; the 192.168.0.2/32 lo
     alias lives in the default netns local table 255. So the
     ping is instantly "Destination unreachable".
  5. arp_ipv4_resolved=False → amber pill.

### Fixes

F1  When ipv4_gateway is empty in the DB, fall back to the peer
    address a protocol monitor has already resolved:
    ospf_neighbors[0].address, isis_neighbors[0].ipv4_address,
    bgp_neighbors[0].remote_ip — first non-empty wins. Same
    for IPv6 (prefer global v6 over link-local — link-local
    can't be pinged without %iface). Emits INFO log when the
    fallback fires.

F3  Startup migration in DeviceDatabase._run_migrations backfills
    every empty ipv4_gateway/ipv6_gateway row from the same
    neighbor tables. Idempotent (only touches rows where the
    column IS empty). Heals the DB permanently so F1's runtime
    fallback becomes redundant after one netgen-server restart.

F2 (client dialog persistence gap) deferred to v0.5.377 — F1+F3
   restore correctness without an operator fix, so F2 becomes
   a cleanup for future device additions, not a blocker.

Both fixes carry marker `v0.5.376 (audit arp-gateway-fallback-to-own-ip)`.
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


# ─── AST sanity ───


def test_all_files_ast_parse():
    for _f in ("run_tgen_server.py", "utils/device_database.py"):
        ast.parse(_read(_f))


# ─── Marker presence ───


def test_marker_present_server():
    """Server-side ARP handler + details block both carry the
    v0.5.376 marker."""
    src = _read("run_tgen_server.py")
    assert src.count("v0.5.376 (audit arp-gateway-fallback-to-own-ip)") >= 2


def test_marker_present_migration():
    src = _read("utils/device_database.py")
    assert "v0.5.376 (audit arp-gateway-fallback-to-own-ip)" in src


# ─── F1: ARP endpoint neighbor fallback ───


def test_f1_peer_ip_helper_defined():
    """The ARP endpoint must contain a `_first_peer_ip` inline
    helper that parses the JSON-serialized neighbors field."""
    src = _read("run_tgen_server.py")
    assert "def _first_peer_ip(field_json_str, keys, family=" in src


def test_f1_fallback_covers_ospf_isis_bgp():
    """Structural: the fallback loop must consult ALL three
    protocol-neighbor DB fields in preference order."""
    src = _read("run_tgen_server.py")
    # Find the fallback block.
    m = re.search(
        r"if not ipv4_gateway:[\s\S]{0,1500}?"
        r"if _fallback4:",
        src,
    )
    assert m
    body = m.group(0)
    assert 'device.get("ospf_neighbors")' in body
    assert 'device.get("isis_neighbors")' in body
    assert 'device.get("bgp_neighbors")' in body


def test_f1_ipv4_and_ipv6_paths_both_fall_back():
    src = _read("run_tgen_server.py")
    assert "if not ipv4_gateway:" in src
    assert "if not ipv6_gateway:" in src


def test_f1_ipv6_fallback_excludes_link_local():
    """v6 fallback must skip fe80:: addresses — those can't be
    pinged as a gateway target without a %iface suffix, and the
    ARP endpoint's ping doesn't add one.

    v0.5.381 U4 refactored the fe80 check out of `_first_peer_ip`
    into a sibling `_peer_ip_for_family` helper, so widen the
    slice to cover both the helper and the caller.
    """
    src = _read("run_tgen_server.py")
    # Slice from the U4 healthy-states block (introduced in U4,
    # sits immediately BEFORE _peer_ip_for_family) through the
    # end of _first_peer_ip. Falls back to the raw def anchor
    # for older tree versions.
    try:
        _start = src.index("_HEALTHY_STATES = {")
    except ValueError:
        _start = src.index("def _first_peer_ip(")
    body = src[_start:_start + 4500]
    assert 'startswith("fe80")' in body
    # negation of fe80 (rejecting link-local). U4 wrapped the
    # value in str(...) for defensive typing so accept either
    # shape.
    assert "not _v.lower().startswith" in body \
        or "not str(_v).lower().startswith" in body


def test_f1_fallback_logs_info_when_fired():
    """Operator observability: log an INFO line when the DB
    gateway is empty and we're substituting from peers."""
    src = _read("run_tgen_server.py")
    # Find the log_message.
    assert 'DB ipv4_gateway empty; using peer-derived' in src \
        or 'ipv4_gateway empty' in src
    assert 'ipv6_gateway empty' in src


def test_f1_details_block_exposes_gateway_source():
    """The response's details block gets `gateway_target_source`
    so clients can render "peer-derived" vs "db" — makes the
    fallback visible in /admin and desktop UI."""
    src = _read("run_tgen_server.py")
    assert '"gateway_target_source"' in src
    assert '"gateway_target_v6"' in src


# ─── F3: startup migration backfill ───


def test_f3_backfill_scoped_to_empty_rows_only():
    """SELECT must filter WHERE ipv4_gateway IS NULL OR = '' —
    non-empty rows must be untouched."""
    src = _read("utils/device_database.py")
    m = re.search(
        r"v0\.5\.376 \(audit arp-gateway-fallback-to-own-ip\)"
        r"[\s\S]{0,3000}?"
        r"SELECT device_id, device_name",
        src,
    )
    assert m, "F3 backfill SELECT not found under the v0.5.376 marker"
    tail = src[m.end():m.end() + 500]
    assert "ipv4_gateway IS NULL OR ipv4_gateway = ''" in tail
    assert "ipv6_gateway IS NULL OR ipv6_gateway = ''" in tail


def test_f3_backfill_uses_UPDATE_not_INSERT():
    """The backfill must never insert new rows — only UPDATE
    existing devices."""
    src = _read("utils/device_database.py")
    _start = src.index("v0.5.376 (audit arp-gateway-fallback-to-own-ip)")
    body = src[_start:_start + 8000]
    assert "UPDATE devices SET ipv4_gateway = ?" in body
    assert "UPDATE devices SET ipv6_gateway = ?" in body
    # And no INSERTs in the backfill body.
    assert "INSERT" not in body


def test_f3_backfill_logs_per_device_and_summary():
    """Structural: per-device INFO on healing + one summary at
    the end so operators can grep for it."""
    src = _read("utils/device_database.py")
    # per-device log message
    assert "v0.5.376 backfill: " in src
    # summary log line at the end
    assert "v0.5.376 gateway backfill: " in src
    assert "rows healed" in src


def test_f3_backfill_wrapped_in_try_so_migration_survives():
    """A backfill parse error must not abort the outer migration.
    Structural: the backfill body is inside its own try/except
    with a logger.warning fallback."""
    src = _read("utils/device_database.py")
    _start = src.index("v0.5.376 (audit arp-gateway-fallback-to-own-ip)")
    tail = src[_start:_start + 8000]
    assert "except Exception as _backfill_exc" in tail
    assert "v0.5.376 gateway backfill skipped" in tail


# ─── version guard ───


def test_pyproject_version_at_least_0576():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 376), (
        f"Version {m.group(1)} < 0.5.376"
    )


# ─── regression guards ───


def test_v0311_arp_ipv4_semantic_intact():
    """v0.5.311's comment about ARP-is-for-remote-peers must
    still be there — v0.5.376 builds on that principle."""
    src = _read("run_tgen_server.py")
    assert "v0.5.311 (audit arp-ipv4-semantic-fix)" in src


def test_v0254_neigh_fallback_still_present():
    """v0.5.254's neigh-cache fallback (ping-fail is not proof of
    ARP failure) must remain — v0.5.376's peer-fallback is a
    LAYER on top, not a replacement."""
    src = _read("run_tgen_server.py")
    assert "v0.5.254: ping-fail is not proof of ARP failure" in src
    assert "_neigh_state_ok" in src


def test_v0375_sec_hotfix_intact():
    """v0.5.375 SEC decorators must survive since v0.5.376 also
    edits run_tgen_server.py."""
    src = _read("run_tgen_server.py")
    assert "v0.5.375 (audit ai-subsystem-sec-hotfix)" in src


def test_v0288_migration_lock_still_present():
    """The v0.5.288 log-spam fix + migrations lock must still be
    intact — v0.5.376 adds a backfill INSIDE _run_migrations
    that lives inside the same lock scope."""
    src = _read("utils/device_database.py")
    assert "_MIGRATIONS_LOCK" in src
    assert "v0.5.288" in src


def test_v0375_db_f3_connect_fk_on_intact():
    """v0.5.375 F3 (_connect_fk_on helper) must still be there."""
    src = _read("utils/device_database.py")
    assert "def _connect_fk_on(self):" in src
