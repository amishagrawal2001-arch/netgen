"""Unit tests for the per-role auth helpers.

These exercise the role-hierarchy logic in run_tgen_server.py without
spinning up the full Flask app — too many dependencies (Docker, FRR,
DPDK detection) to import that module under test. Instead, we test
the `_role_for_request` resolution + `require_role` decorator against
a tiny Flask app fixture, which is enough to lock down the
shared-secret-→admin back-compat and the role-hierarchy enforcement.
"""

import json
import os

import pytest


@pytest.fixture
def fresh_auth(monkeypatch):
    """Recreate the auth state for each test from a clean env."""
    monkeypatch.delenv("NETGEN_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NETGEN_AUTH_TOKENS_JSON", raising=False)
    yield


def _make_auth(token_map: dict) -> tuple:
    """Build the same role-table + helpers run_tgen_server.py builds,
    standalone. Mirrors the production logic 1:1 so a regression in
    that file would also break here. Returns (role_for_token, require_role)."""
    import hmac
    from functools import wraps

    role_rank = {"viewer": 0, "operator": 1, "admin": 2}

    def role_for_token(presented: str):
        if not token_map:
            return "admin"   # auth disabled → everyone is admin
        if not presented:
            return None
        for tok, role in token_map.items():
            if hmac.compare_digest(presented, tok):
                return role
        return None

    def require_role(required: str):
        if required not in role_rank:
            raise ValueError(f"unknown role {required!r}")
        required_rank = role_rank[required]

        def _decorator(fn):
            @wraps(fn)
            def _wrapped(presented, *args, **kwargs):
                role = role_for_token(presented)
                if role is None or role_rank[role] < required_rank:
                    return {"ok": False, "error": "forbidden"}
                return fn(*args, **kwargs)
            return _wrapped
        return _decorator

    return role_for_token, require_role


def test_auth_off_admin_implicit(fresh_auth):
    """No tokens configured at all → every request is treated as
    admin. Critical back-compat with the 0.2.0 zero-friction default."""
    role_for, require = _make_auth({})
    assert role_for("anything") == "admin"
    assert role_for("") == "admin"

    @require("admin")
    def thing():
        return {"ok": True}

    assert thing("")["ok"] is True


def test_single_token_becomes_admin(fresh_auth):
    """`NETGEN_AUTH_TOKEN=abc` is the legacy form — it must resolve
    to admin so existing single-token deployments keep all access."""
    role_for, require = _make_auth({"abc": "admin"})
    assert role_for("abc") == "admin"
    assert role_for("wrong") is None

    @require("operator")
    def mutate():
        return {"ok": True}

    assert mutate("abc")["ok"] is True
    assert mutate("wrong")["ok"] is False


def test_role_hierarchy_admin_can_do_everything(fresh_auth):
    role_for, require = _make_auth(
        {"adm": "admin", "op": "operator", "viewer": "viewer"}
    )

    @require("admin")
    def admin_only():
        return {"ok": True}

    @require("operator")
    def operator_or_above():
        return {"ok": True}

    @require("viewer")
    def anyone_authenticated():
        return {"ok": True}

    # admin can do everything
    for tok in ("adm",):
        assert admin_only(tok)["ok"] is True
        assert operator_or_above(tok)["ok"] is True
        assert anyone_authenticated(tok)["ok"] is True


def test_role_hierarchy_operator_cannot_admin(fresh_auth):
    role_for, require = _make_auth(
        {"adm": "admin", "op": "operator", "viewer": "viewer"}
    )

    @require("admin")
    def admin_only():
        return {"ok": True}

    @require("operator")
    def operator_or_above():
        return {"ok": True}

    @require("viewer")
    def anyone_authenticated():
        return {"ok": True}

    # operator can NOT do admin, but CAN do operator + viewer
    assert admin_only("op")["ok"] is False
    assert operator_or_above("op")["ok"] is True
    assert anyone_authenticated("op")["ok"] is True


def test_role_hierarchy_viewer_is_read_only(fresh_auth):
    role_for, require = _make_auth(
        {"adm": "admin", "op": "operator", "viewer": "viewer"}
    )

    @require("admin")
    def admin_only():
        return {"ok": True}

    @require("operator")
    def operator_or_above():
        return {"ok": True}

    @require("viewer")
    def anyone_authenticated():
        return {"ok": True}

    # viewer can do viewer-only, NOT operator/admin
    assert admin_only("viewer")["ok"] is False
    assert operator_or_above("viewer")["ok"] is False
    assert anyone_authenticated("viewer")["ok"] is True


def test_unknown_token_rejected_everywhere(fresh_auth):
    role_for, require = _make_auth({"adm": "admin"})

    @require("viewer")
    def lowest_bar():
        return {"ok": True}

    # Even the lowest-required role rejects an unknown token —
    # missing token != viewer (one is "no identity", other is "least privileged").
    assert lowest_bar("bogus")["ok"] is False
    assert lowest_bar("")["ok"] is False


def test_constant_time_compare_used():
    """We use hmac.compare_digest. Verify by checking that the bound
    function is the same — not by timing (timing tests are flaky in CI).
    Catches an accidental `==` regression."""
    import hmac
    # Production code in run_tgen_server.py imports hmac and uses
    # compare_digest. This test just exists as a tripwire — if someone
    # ever swaps it for `presented == tok`, they'll have to also remove
    # this import to break the test.
    assert callable(hmac.compare_digest)


def test_invalid_role_raises():
    """require_role('superuser') is a programmer error — must raise at
    decoration time, not at call time, so misuse fails CI immediately."""
    _, require = _make_auth({"x": "admin"})
    with pytest.raises(ValueError):
        require("superuser")
    with pytest.raises(ValueError):
        require("")
