"""v0.5.197: BGP apply no longer silently drops attachments when
the pool doesn't exist on the server.

Operator flow reproducing the bug (verified on san-hp-srv06):
  1. Client GUI attaches pool names 'p2', 'p5' to a BGP neighbor
  2. Client Apply BGP hits /api/device/bgp/configure
  3. Server saves the attachment in bgp_config.route_pools
  4. Server checks `if attached_pools and all_pools:` — all_pools
     was empty because the pools were never POSTed to /api/bgp/pools
  5. Falls into the else branch → SILENTLY cleans up any existing
     advertisement. Neither log nor toast on the operator's side.
  6. Switch sees zero prefixes received. No indication of why.

Fix:
  - Warn loudly (WARNING log) listing the unknown pool names
  - Advertise the KNOWN subset (partial success beats silent no-op)
  - Return `warnings: [{code, neighbor, unknown_pools, message}]`
    in the configure response so the client can toast
  - BGP-start restore path (line ~10308) gets the same treatment
    (log-only; not called via the client-visible apply)
  - Client stashes warnings on device_info["_apply_warnings"] and
    the Devices tab folds them into the Apply Results dialog
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05197_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# The bug was a silent branch in configure_bgp. Lock-in the fix at
# the source level — the wrong `if attached_pools and all_pools:`
# branch must not reappear, and the new `warnings` list must be
# returned by the configure endpoint.
# ─────────────────────────────────────────────────────────────────────

def test_configure_bgp_no_silent_pool_gate():
    """The single-line `if attached_pools and all_pools:` gate was
    the exact source of the silent no-op. If it reappears, the
    server is back to swallowing the misconfiguration. Locking it
    out at the source level (ignoring comment lines that quote the
    old pattern for context)."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # Strip pure-comment lines before grepping — the fix's own
    # docstring quotes the old pattern for context, which would
    # false-positive a naïve substring search.
    live_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    live_src = "\n".join(live_lines)
    assert "if attached_pools and all_pools:" not in live_src, (
        "The old silent gate has reappeared in configure_bgp. See "
        "v0.5.197 CHANGELOG — pools must split into known/unknown, "
        "with the unknown set surfaced via `warnings`."
    )


def test_configure_bgp_splits_pools_into_known_and_unknown():
    """Contract: the fixed configure_bgp must compute both a known
    subset (to advertise) and an unknown subset (to warn about),
    keyed off `known_pool_names`."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    assert "known_pool_names" in src
    assert "unknown_pools" in src
    assert "apply_warnings" in src
    # And the warning entry carries the fields the client parses.
    assert '"code": "unknown_pools"' in src
    assert '"unknown_pools":' in src
    assert '"neighbor":' in src


def test_configure_bgp_response_includes_warnings():
    """The success-path jsonify at the end of configure_bgp must
    return a `warnings` field (empty list when clean, populated
    when a pool attachment references an unknown name)."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # The response dict is jsonify({...}) — the warnings key is
    # what the client reads via response.json().get('warnings').
    assert '"warnings": apply_warnings' in src


# ─────────────────────────────────────────────────────────────────────
# The BGP-start restore path (line ~10308) had the same shape. It
# doesn't return warnings (not called from the client apply flow)
# but it must at least log a WARNING and still advertise the known
# subset, so a stale attachment doesn't blackhole an entire restart.
# ─────────────────────────────────────────────────────────────────────

def test_bgp_start_restore_path_no_silent_gate():
    """The BGP-start restore had `if attached_pools and all_pools:`
    too — same silent-drop bug in a different function. It must
    also split, log, and advertise the known subset."""
    src = (REPO / "run_tgen_server.py").read_text()
    # The `[BGP START] Restoring route pools` block should now use
    # the split-and-warn pattern.
    marker = "[BGP START] Restoring route pools"
    assert marker in src
    # Locate the surrounding block and confirm it computes
    # known/unknown against `known_pool_names`.
    block = src.split(marker, 1)[0][-2000:] + src.split(marker, 1)[1][:2000]
    assert "known_pool_names" in block
    assert "unknown_pools" in block
    # And the warning is loud (WARNING not INFO).
    assert 'logging.warning' in block


# ─────────────────────────────────────────────────────────────────────
# Client-side: devices_tab_bgp captures warnings + devices_tab
# surfaces them as an Apply Results line.
# ─────────────────────────────────────────────────────────────────────

def test_client_bgp_apply_captures_warnings():
    """When configure returns 200 with warnings, the sync BGP apply
    must stash them on device_info['_apply_warnings'] so the outer
    apply path picks them up."""
    src = (REPO / "utils/devices_tab_bgp.py").read_text()
    # Must READ warnings from response.json() and STASH on device_info.
    assert 'body.get("warnings")' in src or "body.get('warnings')" in src
    assert '_apply_warnings' in src


def test_client_devices_tab_surfaces_warnings():
    """The Devices tab's apply-results emitter must fold
    _apply_warnings into a user-facing message so the operator
    sees WHY partial success happened without opening logs."""
    src = (REPO / "widgets/devices_tab.py").read_text()
    assert '_apply_warnings' in src
    # Uses ⚠ marker (distinct from ✅ success and ❌ fail) so the
    # results dialog is scannable.
    assert 'Applied with warnings' in src
