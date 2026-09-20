"""v0.5.366 — route authorization sweep.

Post-v0.5.365 hotfix, the routes agent flagged 10 more MED-tier
state-changing endpoints that lacked `@require_role`. Adding the
decorator is the whole fix — no logic change, no API break for
callers that already send an operator-role bearer token.

MP1 — `/api/streams/save` — GET writes disk (CSRF-able if a
    viewer's browser loads a page that references the URL);
    role gate closes it because the bearer token isn't
    included on cross-origin requests.

MP2 — 9 BGP/OSPF/pool mutators — `/api/ospf/pools` POST,
    `/api/ospf/pools/<name>` PUT+DELETE, `/api/bgp/pools`
    POST, `/api/bgp/pools/<name>` PUT+DELETE,
    `/api/bgp/pools/batch` POST, `/api/device/<id>/route-pools`
    POST+DELETE. All mutate `device_db`.

MP3 — `/api/rfc2544/start` — POST spawns a long, expensive
    tx_worker/tcpdump run; other tests get 409 while it runs.
    Same as S5 in the audit report; operator role.

MP4 — `/api/ai/device/discover` — LOW `get_json()` without
    `silent=True` raised 500 on empty body; also given
    operator-role for free since it scans the network.

All 11 fixes carry the shared marker
`v0.5.366 (audit route-auth-sweep)`.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _decos_for(url: str, methods=None):
    """Return decorator source strings for the first matching route."""
    src = _server_src()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            _fn = ast.unparse(deco.func)
            if not _fn.endswith("app.route"):
                continue
            if not deco.args:
                continue
            _url = deco.args[0]
            if not (isinstance(_url, ast.Constant) and _url.value == url):
                continue
            if methods:
                _m = None
                for kw in deco.keywords:
                    if kw.arg == "methods":
                        _m = [c.value for c in kw.value.elts if isinstance(c, ast.Constant)]
                if _m != methods and _m is not None:
                    continue
            return [ast.unparse(d) for d in node.decorator_list]
    return None


def _has_operator_role(decos):
    if not decos:
        return False
    return any("require_role('operator')" in d for d in decos)


def test_marker_present():
    src = _server_src()
    assert "v0.5.366 (audit route-auth-sweep" in src


# --- MP1: streams/save ---


def test_MP1_streams_save_has_operator_role():
    assert _has_operator_role(_decos_for("/api/streams/save"))


# --- MP2: BGP/OSPF/pool mutators ---


def test_MP2_ospf_pools_create_gated():
    assert _has_operator_role(_decos_for("/api/ospf/pools", methods=["POST"]))


def test_MP2_ospf_pools_update_gated():
    assert _has_operator_role(_decos_for("/api/ospf/pools/<pool_name>", methods=["PUT"]))


def test_MP2_ospf_pools_delete_gated():
    assert _has_operator_role(_decos_for("/api/ospf/pools/<pool_name>", methods=["DELETE"]))


def test_MP2_bgp_pools_create_gated():
    assert _has_operator_role(_decos_for("/api/bgp/pools", methods=["POST"]))


def test_MP2_bgp_pools_update_gated():
    assert _has_operator_role(_decos_for("/api/bgp/pools/<pool_name>", methods=["PUT"]))


def test_MP2_bgp_pools_delete_gated():
    assert _has_operator_role(_decos_for("/api/bgp/pools/<pool_name>", methods=["DELETE"]))


def test_MP2_bgp_pools_batch_gated():
    assert _has_operator_role(_decos_for("/api/bgp/pools/batch"))


def test_MP2_device_route_pools_attach_gated():
    assert _has_operator_role(
        _decos_for("/api/device/<device_id>/route-pools", methods=["POST"])
    )


def test_MP2_device_route_pools_remove_gated():
    assert _has_operator_role(
        _decos_for("/api/device/<device_id>/route-pools", methods=["DELETE"])
    )


# --- MP3: rfc2544/start ---


def test_MP3_rfc2544_start_gated():
    assert _has_operator_role(_decos_for("/api/rfc2544/start"))


# --- MP4: ai/device/discover (silent + role) ---


def test_MP4_ai_discover_gated_and_silent():
    """`/api/ai/device/discover` gets both fixes: operator role
    AND `get_json(silent=True)` so an empty body 400s cleanly
    instead of raising."""
    assert _has_operator_role(_decos_for("/api/ai/device/discover"))
    src = _server_src()
    fn_idx = src.index("def ai_discover_devices(")
    # Fenced by the next `def` at same indent (~8 spaces here since
    # nested inside a `try:` block). Use next @app.route as boundary.
    _end = src.index("\n    @app.route", fn_idx + 1)
    body = src[fn_idx:_end]
    assert "get_json(silent=True)" in body


# --- GET routes that should NOT have gained a role gate ---


def test_read_only_routes_still_ungated():
    """Sanity check the sweep didn't accidentally gate read-only
    endpoints — pool LIST/GET are the sibling routes and should
    stay accessible to any authenticated caller (they don't
    mutate state)."""
    src = _server_src()
    for url in ("/api/bgp/pools", "/api/ospf/pools"):
        # For GET, require_role should NOT be operator (list is
        # informational).
        decos = _decos_for(url, methods=["GET"])
        if decos:  # only assert when the GET route exists
            _has_op = any("require_role('operator')" in d for d in decos)
            assert not _has_op, (
                f"{url} GET should not require operator (list is "
                f"read-only informational)"
            )


# --- Regression guard ---


def test_v0_5_365_security_hotfix_still_intact():
    """v0.5.366 auth sweep must not have accidentally reverted
    the v0.5.365 security hotfix."""
    src = _server_src()
    assert "v0.5.365 (audit capture-routes-hardening, S1-S4)" in src
    assert "v0.5.365 (audit orphans-reap-arbitrary-pid, S7)" in src
    assert "v0.5.365 (audit device-external-execute-no-role, S8)" in src
    assert "v0.5.365 (audit iface-admin-no-role, S9)" in src


def test_ast_parses():
    ast.parse(_server_src())
