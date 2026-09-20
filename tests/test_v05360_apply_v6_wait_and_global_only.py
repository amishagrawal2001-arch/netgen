"""v0.5.360 — D10: Apply-path v6 wait-loop was too short + broke on
link-local-only iface.

Runtime evidence caught on srv06 v0.5.359 verification: after
Stop→Apply on device7, the wire had `2001:db8:30::13a/128` within
~15s but the DB stamped `state=Failed, dhcp_last_error="IPv6: no
global IPv6 observed (only link-local)"`. Root cause was two-part:

1. **Wait deadline too short** — `lease_deadline = time.time() +
   lease_timeout`, and `lease_timeout` defaults to 20s. v6
   SOLICIT/ADVERTISE/REQUEST/REPLY through a relay agent is
   slower than v4 DISCOVER/OFFER; on srv06's Juniper-relay setup
   the four-way handshake regularly took 20-30s.

2. **Wait-loop broke on link-local** — the `dhclient -6` fallback
   branch's polling loop broke as soon as `_parse_ipv6` returned
   anything, but `_parse_ipv6` returns link-local (`fe80::…`)
   which is always present after iface-up. First poll broke with
   only link-local, downstream `_pick_global_ipv6` returned None,
   and we stamped "no global IPv6 observed" seconds after Apply
   — long before dhclient completed SOLICIT.

3. **`dhcp6c` branch had NO polling loop at all** — it parsed
   once immediately after spawn, so `addr6` was always empty for
   that branch (dhcp6c returns from spawn before SOLICIT
   completes).

### Fix — three parts, one marker

- `lease_deadline` floored at 30s for v6:
  `time.time() + max(lease_timeout, 30)`.
- Both polling loops (dhcp6c and dhclient-6) require a GLOBAL
  address (`_pick_global_ipv6(parsed)` truthy) before breaking.
- The dhcp6c branch gains the same polling loop that the
  dhclient-6 branch has.

All three under one marker
`v0.5.360 (audit D10 apply-v6-wait-too-short)`.
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
    src = _dhcp_src()
    assert "v0.5.360 (audit D10 apply-v6-wait-too-short)" in src


# --- Deadline floored at 30s ---


def test_v6_lease_deadline_floored_at_30():
    """Pre-fix `lease_deadline = time.time() + lease_timeout`
    (default lease_timeout=20). Post-fix must floor at 30s so a
    v6 SOLICIT-through-relay has room to complete."""
    src = _dhcp_src()
    marker_idx = src.index("v0.5.360 (audit D10 apply-v6-wait-too-short)")
    body = src[marker_idx:marker_idx + 2000]
    assert "max(lease_timeout, 30)" in body


# --- Both branches require a GLOBAL v6 before breaking ---


def test_dhclient6_branch_requires_global_before_break():
    """The dhclient-6 fallback branch's polling loop must call
    `_pick_global_ipv6(parsed)` in the break condition. Pre-fix
    `if parsed:` broke on link-local (always present after iface-
    up) and downstream `_pick_global_ipv6` returned None →
    stamp Failed while dhclient was still mid-SOLICIT."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_client(")
    body = src[fn_idx:]
    # Two poll loops in start_dhcp_client's v6 section. Both must
    # gate on _pick_global_ipv6.
    assert body.count("if parsed and _pick_global_ipv6(parsed):") >= 2, (
        "at least two v6 poll loops (dhcp6c + dhclient-6) must "
        "require a global address before breaking"
    )


def test_dhcp6c_branch_gained_polling_loop():
    """Pre-fix the dhcp6c branch parsed once immediately after
    spawn (dhcp6c returns from spawn BEFORE SOLICIT completes,
    so addr6 was always empty). Post-fix must poll until the
    lease deadline the same way the dhclient-6 branch does."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_client(")
    body = src[fn_idx:]
    # Find the dhcp6c branch by its unique guard line and bound
    # via the unique log line that starts the else fallback
    # (`dhcp6c not found; falling back to dhclient -6`). Using
    # a bare `else:` search picks up nested if/else blocks inside
    # the config-writing step and misfires.
    dhcp6c_branch_start = body.index(
        'if _command_exists("dhcp6c", container=container):'
    )
    _fallback_log = body.index(
        "dhcp6c not found; falling back to dhclient -6",
        dhcp6c_branch_start,
    )
    dhcp6c_body = body[dhcp6c_branch_start:_fallback_log]
    assert "while time.time() < lease_deadline:" in dhcp6c_body, (
        "dhcp6c branch still lacks a polling loop — it will parse "
        "once immediately after dhcp6c spawn and always find only "
        "link-local"
    )
    # And that loop must also gate on global (same shape as the
    # dhclient-6 branch).
    assert "if parsed and _pick_global_ipv6(parsed):" in dhcp6c_body


# --- Regression guards ---


def test_pick_global_ipv6_helper_still_defined():
    """The fix's break condition depends on `_pick_global_ipv6`.
    Guard against a future refactor removing it while both v6
    branches still reference it."""
    src = _dhcp_src()
    assert "def _pick_global_ipv6(" in src


def test_v0_5_346_ra_lockdown_still_intact():
    """The v6 wait loop lives right after v0.5.346's sysctl
    lockdown block. Regression guard so the D10 fix didn't
    accidentally unwire the lockdown."""
    src = _dhcp_src()
    assert "v0.5.346" in src


def test_v0_5_350_dual_stack_partial_state_still_intact():
    """v0.5.350's dual-stack partial-lease handling reads the
    `_v6_ok` flag that D10's fix produces. Regression guard so
    the fix didn't accidentally revert that state machine."""
    src = _dhcp_src()
    assert "v0.5.350 (audit dual-stack-partial-success-hidden)" in src


def test_v0_5_359_stop_path_clears_still_intact():
    """Guard against D10's edits accidentally reverting v0.5.359's
    D7/D8 clears on stop_dhcp_server (which lives further down
    the same file)."""
    src = _dhcp_src()
    assert "v0.5.359 (audit stop-server-lease-field-clear, D7 + D8)" in src


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
