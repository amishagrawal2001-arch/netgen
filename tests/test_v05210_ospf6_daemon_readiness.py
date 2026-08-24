"""v0.5.210: OSPF6 no longer silently fails on freshly created
FRR containers because ospf6d wasn't ready when vtysh ran.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: added a new
device with BGP v4+v6 and OSPF v4+v6 all enabled up-front.
OSPFv3 didn't come up on FRR. Selecting the v6 row from the
OSPF table and clicking Apply worked — by the time the second
apply ran, ospf6d had finished initializing.

Root cause: `configure_ospf_neighbor` in `utils/ospf.py` had a
readiness loop that only tested `vtysh -c 'show ip ospf'`
(the v4 daemon, ospfd). On a fresh container ospf6d takes
longer to initialize than ospfd; if the vtysh heredoc batch
ran while ospf6d was still starting, the v6 `router ospf6 …`
and `interface X\n ipv6 ospf6 area …` commands were silently
rejected by vtysh (ospf6d not attached), but the batch's
overall exit_code stayed 0 because vtysh reports parser
success, not per-command success. The function returned True,
the client showed "success", and OSPFv3 was quietly not
configured.

Fix: gate the readiness loop on BOTH daemons when v6 is
actually going to be configured. `want_ipv6` peeks at
`ospf_config["ipv6_enabled"]` (or the payload `ipv6` arg) and
respects `_apply_address_families` — no point waiting for
ospf6d on a v4-only apply. If v6 is wanted, the loop keeps
retrying until both ospfd and ospf6d respond. If they never
become ready in max_retries the loop still exits (with a
loud WARNING) rather than blocking forever.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05210_test_{os.getpid()}.db"),
)


def _readiness_block() -> str:
    """Extract the readiness-loop region from configure_ospf_neighbor.
    We anchor on the v0.5.210 marker + the loop declaration so a
    refactor moving the code around won't quietly slip past."""
    src = (REPO / "utils" / "ospf.py").read_text()
    idx = src.find("def configure_ospf_neighbor")
    assert idx >= 0, "configure_ospf_neighbor moved"
    body = src[idx:]
    # Loop starts at `for attempt in range(max_retries):`.
    loop_idx = body.find("for attempt in range(max_retries):")
    assert loop_idx >= 0, "readiness retry loop moved"
    # Include the preceding ~2000 chars for the peek/setup + loop body.
    return body[max(0, loop_idx - 2500):loop_idx + 3500]


def test_readiness_gate_checks_ospf6d_when_ipv6_wanted():
    """The core fix — the readiness loop must issue `show ipv6
    ospf6` (not just `show ip ospf`) when the payload asks for
    IPv6 configuration."""
    block = _readiness_block()
    assert "show ipv6 ospf6" in block, (
        "readiness loop no longer probes ospf6d — v0.5.210 fix reverted"
    )
    assert "ospf6d is not running" in block, (
        "readiness loop no longer knows the ospf6d missing marker"
    )


def test_readiness_gate_still_checks_ospfd():
    """No regression on the v4 check."""
    block = _readiness_block()
    assert "show ip ospf" in block
    assert "ospfd is not running" in block


def test_readiness_gate_conditions_on_want_ipv6():
    """v6 check must be conditional on `want_ipv6` — otherwise
    v4-only applies get slower for no reason."""
    block = _readiness_block()
    assert "want_ipv6" in block, "want_ipv6 gate variable missing"
    # v6 daemon-check should be guarded by want_ipv6.
    assert re.search(r"if\s+want_ipv6\s+and\s+not\s+ospf6d_ready", block), \
        "ospf6d readiness check not gated on want_ipv6"


def test_want_ipv6_honors_partial_apply():
    """On a manual `_apply_address_families=['IPv4']` apply, we
    must NOT stall waiting for ospf6d."""
    block = _readiness_block()
    assert "_apply_address_families" in block, (
        "readiness peek doesn't respect partial-apply — v4-only "
        "applies could stall waiting for ospf6d"
    )
    # want_ipv6 must be `and`ed with IPv6-in-partial-list.
    assert re.search(r'want_ipv6\s*=\s*want_ipv6\s+and\s+["\']IPv6["\']\s+in\s+_peek_partial', block), \
        "want_ipv6 not correctly narrowed by _apply_address_families"


def test_break_requires_both_daemons_when_v6_wanted():
    """The exit condition must require BOTH ospfd_ready AND
    ospf6d_ready when v6 is wanted — pre-fix the loop broke as
    soon as ospfd was ready and vtysh ran against a not-ready
    ospf6d."""
    block = _readiness_block()
    assert re.search(r"if\s+ospfd_ready\s+and\s+ospf6d_ready\s*:", block), \
        "break condition doesn't gate on both daemons — the original bug is back"


def test_warning_message_names_both_daemons():
    """The 'proceeding anyway' warning must reference both
    daemons so operators see what actually didn't come up."""
    block = _readiness_block()
    assert "ospfd=" in block and "ospf6d=" in block, (
        "readiness-fail warning no longer names both daemons — makes "
        "the ospf6d flavor of this bug harder to spot in logs"
    )
