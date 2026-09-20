"""v0.5.359 — D7 + D8: stop-path lease-field clear + dhcp_last_error clear.

Runtime evidence caught on srv06 v0.5.358 verification 2026-09-19:
Stopped device7, re-applied it, saw the wire lease `2001:db8:30::197/128`
come back within seconds — but the DB kept reading `state=Failed`,
`dhcp_lease_ip6=""`. Root cause: `stop_dhcp_server`'s Stopped DB
write only touched three fields (state / running / lease_subnet)
and never cleared `dhcp_last_error` from a prior cycle's failure.
The stale error made every subsequent Apply's status readout look
Failed even when the wire was healthy.

Twin issue on `stop_dhcp_client`: cleared every lease field but
left `dhcp_last_error` intact.

Two fixes, one marker `v0.5.359 (audit stop-server-lease-field-clear,
D7 + D8)`:

D7 — `stop_dhcp_server`'s Stopped DB write extends to blank all
    v4 and v6 lease fields (parity with `stop_dhcp_client` at
    :3893). Mode-flip server → client also no longer inherits a
    stale v6 gateway/ip from the previous v6 server role.

D8 — Both stop paths clear `dhcp_last_error` on success. On
    partial failure `stop_dhcp_server` sets it to the aggregated
    failure summary instead, so operators still see WHY the stop
    wasn't clean.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def test_marker_present():
    assert _dhcp_src().count(
        "v0.5.359 (audit stop-server-lease-field-clear, D7 + D8)"
    ) >= 1
    # D8 marker in stop_dhcp_client (single-line reference — pinned
    # to the shared audit slug so a grep finds both fixes).
    assert "v0.5.359 D8" in _dhcp_src()


# --- D7: stop_dhcp_server extends lease-field clear ---


def test_D7_stop_server_clears_all_v4_and_v6_lease_fields():
    """The pre-fix Stopped DB write listed three fields. Post-fix
    it must list every lease field that `stop_dhcp_client` clears,
    so mode-flip server → client doesn't inherit a stale lease."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    # stop_dhcp_server is a long function; bounded window covers
    # the Stopped DB write near the tail.
    body = src[fn_idx:]
    # v4 lease fields.
    for _key in (
        '"dhcp_lease_ip": ""',
        '"dhcp_lease_mask": ""',
        '"dhcp_lease_gateway": ""',
        '"dhcp_lease_server": ""',
        '"dhcp_lease_expires": None',
        '"dhcp_lease_subnet": ""',
    ):
        assert _key in body, (
            f"stop_dhcp_server Stopped DB write missing v4 field "
            f"{_key} — mode-flip server → client would inherit a "
            f"stale v4 lease value"
        )
    # v6 lease fields (v0.5.302 parity).
    for _key in (
        '"dhcp_lease_ip6": ""',
        '"dhcp_lease_prefix6": ""',
        '"dhcp_lease_gateway6": ""',
    ):
        assert _key in body, (
            f"stop_dhcp_server Stopped DB write missing v6 field "
            f"{_key}"
        )


def test_D7_uses_stop_payload_dict_pattern():
    """Structural check: the fix builds the payload as a local dict
    first (so D8 can conditionally add `dhcp_last_error` based on
    the failures list), then calls `_update_device_db` once. This
    prevents a future refactor from splitting the write into two
    calls (which would race against the monitor's next tick)."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    body = src[fn_idx:]
    marker_idx = body.index(
        "v0.5.359 (audit stop-server-lease-field-clear, D7 + D8)"
    )
    fix_body = body[marker_idx:marker_idx + 3000]
    assert "_stop_payload = {" in fix_body
    assert "_update_device_db(device_db, device_id, _stop_payload)" in fix_body


# --- D8: dhcp_last_error clear on success + set on partial failure ---


def test_D8_stop_server_clears_last_error_on_success():
    """On the success path (empty failures list), the fix must
    write `dhcp_last_error = ""` so a stale message from a prior
    cycle doesn't stick and confuse the next Apply."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    body = src[fn_idx:]
    marker_idx = body.index(
        "v0.5.359 (audit stop-server-lease-field-clear, D7 + D8)"
    )
    fix_body = body[marker_idx:marker_idx + 3000]
    # Success branch clears the field.
    assert "if not failures:" in fix_body
    assert '_stop_payload["dhcp_last_error"] = ""' in fix_body


def test_D8_stop_server_sets_last_error_on_partial_failure():
    """On partial failure the fix must set `dhcp_last_error` to
    the aggregated failure summary. Operators lose visibility into
    WHY the stop wasn't clean if the field is blanked
    unconditionally."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    body = src[fn_idx:]
    marker_idx = body.index(
        "v0.5.359 (audit stop-server-lease-field-clear, D7 + D8)"
    )
    fix_body = body[marker_idx:marker_idx + 3000]
    assert "else:" in fix_body
    assert '_stop_payload["dhcp_last_error"] = "; ".join(failures)' in fix_body


def test_D8_stop_client_clears_last_error():
    """`stop_dhcp_client` always reaches the DB write on success
    (any hard failure earlier raises), so unconditional clear is
    safe here — no need for the failures-list branch that
    `stop_dhcp_server` needs."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_client(")
    body = src[fn_idx:]
    # Bounded window on the Stopped DB write inside stop_dhcp_client.
    _sr_idx = body.index("v0.5.359 D8")
    fix_body = body[_sr_idx:_sr_idx + 1200]
    assert '"dhcp_last_error": ""' in fix_body


# --- Regression guards ---


def test_stop_client_still_clears_all_lease_fields():
    """Baseline that stop_dhcp_client's field list hasn't been
    accidentally trimmed. If someone shrinks it, the mode-flip
    client → server scenario surfaces the same class of bug D7
    caught on the server side."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_client(")
    # stop_dhcp_client is long (v0.5.218 + v0.5.240 + v0.5.351
    # + v0.5.359 all landed here); take the whole function body up
    # to the next top-level def.
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    for _key in (
        '"dhcp_lease_ip": ""',
        '"dhcp_lease_ip6": ""',
        '"ipv4_address": ""',
        '"ipv6_address": ""',
        '"dhcp_lease_gateway6": ""',
    ):
        assert _key in body


def test_stop_server_still_returns_failures_when_present():
    """v0.5.217 fix D contract: `stop_dhcp_server` returns
    `{"success": False, "error": ..., "failures": [...]}` when
    the stop wasn't clean. Regression guard so the D7/D8 fix
    didn't accidentally short-circuit that return."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    body = src[fn_idx:]
    marker_idx = body.index(
        "v0.5.359 (audit stop-server-lease-field-clear, D7 + D8)"
    )
    fix_body = body[marker_idx:marker_idx + 3000]
    assert 'if failures:' in fix_body
    assert '"success": False' in fix_body
    assert '"failures": failures' in fix_body


def test_v0_5_357_hotfix_still_intact():
    """Regression guard so the D7/D8 edits didn't accidentally
    revert the v0.5.357 v6 hosts() explosion fix — the two live
    in overlapping code (both touch stop_dhcp_server)."""
    src = _dhcp_src()
    assert src.count("v0.5.357 (audit v6-hosts-generator-explosion)") >= 3


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
