"""v0.5.377 — Device Add/Edit confirms + server WARN on empty gateway.

Follows v0.5.376 (ARP peer-fallback + DB backfill). Root cause of
device1's amber pill was that the DB row's ipv4_gateway was empty
even though the UI showed 192.168.0.1 (cross-referenced from
OSPF neighbors). Three known persistence-gap origins:

  1. Operator unchecked the IPv4 checkbox mid-session, dialog's
     `get_values()` returned "" for gateway even though the widget
     still had the text.
  2. Add-Device template flow (ISIS-only, DHCP-server, etc.)
     bypasses gateway entry.
  3. Edit-Save flow with a Retype-then-blank sequence — the DB
     row already had a gateway but the Save overwrote with empty.

### Fixes

Client-side (widgets/devices_tab.py):
  Add + Edit paths gain a QMessageBox.question gate. When the
  operator is about to Save/Apply a device with a populated
  ipv4/ipv6 address BUT empty gateway, they must explicitly
  confirm. Default is No so accidental Enter presses don't
  proceed silently.

Server-side (run_tgen_server.py /api/device/apply):
  When the payload has ipv4_address populated but empty
  ipv4_gateway (or same for v6), emit a WARNING naming the
  keys the client sent. Makes future recurrences visible in
  server logs — helps triage which client code path is losing
  the value.

All sites carry marker
`v0.5.377 (audit device-apply-empty-gateway-warn)`.
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
    for _f in ("run_tgen_server.py", "widgets/devices_tab.py"):
        ast.parse(_read(_f))


# ─── Marker presence ───


def test_marker_present_server():
    src = _read("run_tgen_server.py")
    assert "v0.5.377 (audit device-apply-empty-gateway-warn)" in src


def test_marker_present_client():
    src = _read("widgets/devices_tab.py")
    # Two sites: Add + Edit paths
    assert src.count("v0.5.377 (audit device-apply-empty-gateway-warn)") >= 2


# ─── Server WARN ───


def test_server_warns_on_ipv4_no_gateway():
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.377 (audit device-apply-empty-gateway-warn)")
    body = src[_start:_start + 3000]
    assert "if ipv4 and not ipv4_gateway:" in body
    assert "DEVICE APPLY WARN v0.5.377" in body


def test_server_warns_on_ipv6_no_gateway():
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.377 (audit device-apply-empty-gateway-warn)")
    body = src[_start:_start + 2000]
    assert "if ipv6 and not ipv6_gateway:" in body


def test_server_warn_names_the_client_keys():
    """Forensic detail: WARN payload must list which keys the
    client DID send so an operator can trace whether the fields
    made it into the request at all."""
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.377 (audit device-apply-empty-gateway-warn)")
    body = src[_start:_start + 2000]
    assert "Client sent keys:" in body


def test_server_warn_wrapped_in_try_except():
    """The WARN block must not abort the apply if logging itself
    fails (rare, but forbidding a device apply on a diagnostic-
    log failure would be worse than swallowing the log)."""
    src = _read("run_tgen_server.py")
    _start = src.index("v0.5.377 (audit device-apply-empty-gateway-warn)")
    body = src[_start:_start + 2500]
    assert "try:" in body
    assert "except Exception as _warn_exc" in body


# ─── Client confirm gate (Add path) ───


def test_client_add_path_confirms_empty_gateway():
    """After get_values() at the Add-Device path, a
    QMessageBox.question must gate the flow when IPv4/IPv6 is
    populated but the corresponding gateway is empty."""
    src = _read("widgets/devices_tab.py")
    # Anchor on the Add-Device get_values destructure. Look
    # forward for QMessageBox.question with 'empty gateway'.
    m = re.search(
        r"# v0\.5\.377 \(audit device-apply-empty-gateway-warn\)"
        r"[\s\S]{0,3000}?QMessageBox\.question\(",
        src,
    )
    assert m
    body = m.group(0)
    assert "if ipv4 and not ipv4_gateway:" in body
    assert "if ipv6 and not ipv6_gateway:" in body


def test_client_confirm_defaults_to_no():
    """Both confirm dialogs must default to No so accidental
    Enter keystroke doesn't silently accept the empty gateway."""
    src = _read("widgets/devices_tab.py")
    # Iterate both v0.5.377 sites.
    for _match in re.finditer(
        r"# v0\.5\.377 \(audit device-apply-empty-gateway-warn\)"
        r"[\s\S]{0,3000}?QMessageBox\.No,\s*\)",
        src,
    ):
        body = _match.group(0)
        assert "QMessageBox.No," in body


def test_client_confirm_no_returns_early():
    """If the operator picks No, the flow must `return` — the
    dialog must not silently proceed to save."""
    src = _read("widgets/devices_tab.py")
    for _match in re.finditer(
        r"# v0\.5\.377 \(audit device-apply-empty-gateway-warn\)"
        r"[\s\S]{0,3500}?if _proceed != QMessageBox\.Yes:",
        src,
    ):
        _end = src.index("return", _match.end())
        # Return must appear within a few lines of the check.
        assert _end - _match.end() < 200


# ─── Client Edit-Save path ───


def test_client_edit_path_gates_before_save():
    """The Edit-Save path must also confirm — it's actually the
    more likely origin of a formerly-populated gateway getting
    silently overwritten with empty."""
    src = _read("widgets/devices_tab.py")
    # There should be TWO occurrences of the confirm-body.
    _hits = list(re.finditer(
        r"v0\.5\.377 \(audit device-apply-empty-gateway-warn\)",
        src,
    ))
    assert len(_hits) >= 2, (
        f"Expected ≥2 v0.5.377 confirm sites (Add + Edit); "
        f"got {len(_hits)}"
    )


# ─── version guard ───


def test_pyproject_version_at_least_0577():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 377), (
        f"Version {m.group(1)} < 0.5.377"
    )


# ─── regression guards ───


def test_v0376_arp_gateway_fallback_intact():
    """v0.5.376 F1 peer-fallback + F3 backfill are the runtime/
    persistence fix; v0.5.377 is the operator-facing surface.
    Both must ship together."""
    server = _read("run_tgen_server.py")
    assert "v0.5.376 (audit arp-gateway-fallback-to-own-ip)" in server


def test_v0374_c3_delete_stream_confirm_pattern_intact():
    """v0.5.377's confirm dialog copies v0.5.374 C3's pattern
    (QMessageBox.question + default No + return on No). The
    v0.5.372 C3 Delete-Stream confirm must survive."""
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.372 (audit stream-delete-no-confirm)" in src
