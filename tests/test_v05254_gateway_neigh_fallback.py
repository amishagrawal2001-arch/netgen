"""v0.5.254 — Gateway (and self-IP) ARP status: fall back to
neighbor table when ping is filtered.

Pre-fix, ``/api/device/arp/<device_id>`` set
``arp_gateway_resolved`` / ``arp_ipv4_resolved`` /
``arp_ipv6_resolved`` purely from ``ping`` / ``ping6`` exit code.
The field name says "arp" but the probe was ICMP echo. Juniper
QFX IRB filters ICMPv4 by default → gateway went orange in the
GUI even while BGP was UP through it.

Fix: new ``_neigh_state_ok(target, family)`` helper (closure
inside ``get_device_arp_status``) that runs
``ip [-6] neigh show to <ip>`` in the same VRF as the ping and
treats REACHABLE / STALE / DELAY / PROBE / PERMANENT / NOARP as
resolved. All three ping branches fall back to it on ping-fail.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SERVER = (REPO / "run_tgen_server.py").read_text()


def _endpoint_body():
    """Return the body of get_device_arp_status (the endpoint we
    fixed). Anchored inside route registration for /api/device/arp/
    with a device_id parameter."""
    idx = SERVER.find('@app.route("/api/device/arp/<device_id>", methods=["GET"])')
    assert idx > 0, "endpoint registration not found"
    # Ends at the next @app.route.
    end = SERVER.find("\n@app.route", idx + 1)
    return SERVER[idx:end if end > 0 else idx + 8000]


# --- helper presence ------------------------------------------------


def test_neigh_state_ok_helper_defined_inside_endpoint():
    body = _endpoint_body()
    assert "def _neigh_state_ok(target, family=" in body
    assert "v0.5.254" in body


def test_helper_uses_ip_neigh_show_to():
    body = _endpoint_body()
    # `ip [-6] neigh show to <target>` — the `to` selector is what
    # narrows the listing to a specific IP.
    assert '"neigh", "show", "to", str(target)' in body
    # IPv6 branch must add `-6` to the argv.
    assert '"ip"' in body and 'family == "ipv6"' in body


def test_helper_treats_reachable_family_as_resolved():
    body = _endpoint_body()
    idx = body.find("_RESOLVED_NEIGH_STATES = {")
    assert idx > 0
    block = body[idx:idx + 400]
    for state in ("REACHABLE", "STALE", "DELAY", "PROBE",
                  "PERMANENT", "NOARP"):
        assert f'"{state}"' in block, f"state {state} missing"


def test_helper_treats_failed_family_as_not_resolved():
    body = _endpoint_body()
    # INCOMPLETE / FAILED / NONE must short-circuit to False,
    # not fall through to a maybe-True default.
    assert 'in ("INCOMPLETE", "FAILED", "NONE")' in body


# --- each of the 3 ping branches must fall back ---------------------


def test_ipv4_self_branch_uses_fallback():
    body = _endpoint_body()
    # The IPv4 self branch is anchored on the target `ipv4_address`.
    idx = body.find("if ipv4_address:")
    assert idx > 0
    branch = body[idx:idx + 1500]
    assert '_neigh_state_ok(ipv4_address, family="ipv4")' in branch
    assert '"ipv4_neigh_fallback"' in branch


def test_ipv6_self_branch_uses_fallback():
    body = _endpoint_body()
    idx = body.find("if ipv6_address or ipv6_gateway:")
    assert idx > 0
    branch = body[idx:idx + 2000]
    assert '_neigh_state_ok(ipv6_target, family="ipv6")' in branch
    assert '"ipv6_neigh_fallback"' in branch


def test_ipv4_gateway_branch_uses_fallback():
    body = _endpoint_body()
    # The gateway branch is anchored on `if ipv4_gateway:`.
    # The IPv4 self branch is `if ipv4_address:` — find the SECOND
    # `if ipv4_` conditional in the body.
    matches = [m.start() for m in re.finditer(r"if ipv4_gateway:", body)]
    assert matches
    branch = body[matches[0]:matches[0] + 1500]
    assert '_neigh_state_ok(ipv4_gateway, family="ipv4")' in branch
    assert '"gateway_neigh_fallback"' in branch
    # Marker so the "important one" comment is present.
    assert "Juniper QFX" in branch or "srv06 lab" in branch


# --- ping-success short-circuit -------------------------------------


def test_ping_success_bypasses_neigh_fallback():
    """If ping succeeds, the neigh fallback should never run —
    keeps the fast path fast. Look for the `if ping_ok:` guard
    that gates the fallback."""
    body = _endpoint_body()
    # All three branches must gate the fallback behind `if ping_ok:`
    # / `else:` — count occurrences to prove all 3 branches have it.
    ping_ok_guards = body.count("if ping_ok:")
    assert ping_ok_guards >= 3, f"expected 3+ ping_ok guards, saw {ping_ok_guards}"


# --- classifier unit tests via re-import ----------------------------


def test_classifier_states_are_upper_case_only():
    """State comparison must be case-insensitive on input but the
    state set is spelled UPPER-CASE (matching iproute2 output)."""
    body = _endpoint_body()
    # `up = tok.upper()` — every comparison happens on the uppered
    # form so the ordering in `_RESOLVED_NEIGH_STATES` (upper) is
    # correct.
    assert "up = tok.upper()" in body


# --- version bumped -------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 254)


# --- classifier logic (extracted-shape unit test) ------------------


def test_classifier_shape_matches_source():
    """The state set + classification rules are pinned in source
    via other tests; here we prove the CLASSIFICATION LOGIC ORDER
    is correct: check for RESOLVED states first (so a REACHABLE
    token wins even if a FAILED token appears elsewhere on the
    same line — never happens in real iproute2 output but the
    reversed-token walk should still terminate correctly).
    """
    body = _endpoint_body()
    # Loop walks tokens in reverse order.
    assert "for tok in reversed(last):" in body
    # RESOLVED check happens BEFORE FAILED check on each token.
    resolved_check = body.find("if up in _RESOLVED_NEIGH_STATES:")
    failed_check = body.find('if up in ("INCOMPLETE", "FAILED", "NONE")')
    assert resolved_check > 0 and failed_check > 0
    assert resolved_check < failed_check


def test_helper_returns_false_on_missing_target():
    """Empty target must not run subprocess — guard at the top."""
    body = _endpoint_body()
    idx = body.find("def _neigh_state_ok(")
    fn = body[idx:idx + 2000]
    # First actionable line inside the function body.
    assert "if not target:" in fn
    assert "return False" in fn
