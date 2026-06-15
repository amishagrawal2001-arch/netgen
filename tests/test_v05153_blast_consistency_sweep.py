"""v0.5.153: Blast RDMA Flow consistency bug sweep.

Audit (post-v0.5.152) surfaced 8 inconsistencies between configured
state and reported state in the Blast dialog. v0.5.153 ships the
top 6 operator-blocking + resource-leak fixes:

1. **Pre-flight CIDR suggester** was hardcoding `10.42.0.1/24` +
   `10.42.0.2/24` for the 2-iface case — same subnet, the literal
   trap pre-flight exists to prevent. Validator caught it on
   Validate but the dialog SHOULDN'T have proposed it.
2. **Pre-flight Test CIDR section** pre-populated even when the
   verdict was OK. Operator saw "Pre-flight OK" + auto-fill that
   failed Validate — confusing.
3. **Auto-apply-on-Start** POSTed `/configure` directly without
   `/validate` first. If `10.43.0.0/24` is already a kernel route,
   the apply silently failed AFTER the operator committed to Start.
4. **`_detect_same_subnet_trap()` on Start** only caught subnet
   collisions. DOWN ports and missing-IP cases returned `False` →
   Start fired → perftest died with "QP→RTR" → operator saw same
   error string with wrong root cause.
5. **Probe timeout** replaced peer's real data with `{"error":
   "timeout"}` → detector silently ignored it → Start proceeded.
6. **Stop button** didn't fire preflight cleanup. Apply IPs → Stop
   → IPs stay. Only closeEvent cleaned up.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_PRE = (REPO / "widgets" / "rdma_preflight_dialog.py").read_text()


# ───── Pre-flight smart auto-suggest ────────────────────────────────────


def test_preflight_no_longer_hardcodes_same_subnet():
    """The exact same-subnet bug from the operator screenshot:
    `seen[0] = 10.42.0.1/24, seen[1] = 10.42.0.2/24`. Must be
    gone. The replacement uses a walker that proposes
    non-conflicting /24s."""
    assert 'suggestions[seen[0]] = "10.42.0.1/24"' not in SRC_PRE
    assert 'suggestions[seen[1]] = "10.42.0.2/24"' not in SRC_PRE


def test_preflight_walker_uses_distinct_subnets():
    """The new walker uses different /24 blocks per iface. Look
    for the `10.{next_octet}.0.0/24` candidate + `proposed_nets`
    set that ensures no two ifaces share."""
    assert "next_octet" in SRC_PRE
    assert "proposed_nets" in SRC_PRE


def test_preflight_skips_ifaces_that_already_have_v4():
    """If iface already has an IPv4 → suggestion is empty; the
    note column explains why. No more 'will be skipped' surprise
    on Validate."""
    body = _extract_method(SRC_PRE, "_populate_config_rows")
    assert "existing_v4.get(iface):" in body or "existing_v4[iface]" in body
    assert "already has IPv4" in body


def test_preflight_clarifies_when_nothing_to_apply():
    """When every iface is already configured, the status line
    explains 'nothing to apply' instead of leaving the operator
    wondering why the auto-suggest is empty."""
    body = _extract_method(SRC_PRE, "_populate_config_rows")
    assert "needs_fix" in body
    assert "non-conflicting subnets" in body


def test_preflight_cidr_field_has_placeholder():
    """Empty CIDR boxes need a placeholder so the operator
    understands 'empty = skip', not 'system error'."""
    body = _extract_method(SRC_PRE, "_populate_config_rows")
    assert "setPlaceholderText" in body
    assert "leave empty to skip" in body


# ───── Broader Start-probe detection ────────────────────────────────────


def test_detect_start_blockers_function_exists():
    """v0.5.153 adds `_detect_start_blockers` as the unified
    entry point. Old `_detect_same_subnet_trap` stays as a
    helper for the same-subnet branch."""
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    assert callable(_detect_start_blockers)


def test_detect_start_blockers_catches_down_port():
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    reason, detail = _detect_start_blockers(
        {"state": "DOWN", "hca": "rocep132s0",
         "kernel_iface": "ens6np0"},
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.1/24"]},
    )
    assert reason == "down_port"
    assert "DOWN" in detail


def test_detect_start_blockers_catches_missing_ip():
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    reason, _ = _detect_start_blockers(
        {"state": "ACTIVE", "ip_addresses": [],
         "kernel_iface": "eth0"},
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.1/24"]},
    )
    assert reason == "missing_ip"


def test_detect_start_blockers_catches_same_subnet():
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    reason, detail = _detect_start_blockers(
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.1/24"]},
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.2/24"]},
    )
    assert reason == "same_subnet"
    assert detail == "10.10.0.0/24"


def test_detect_start_blockers_catches_probe_failure():
    """v0.5.153 fix for the operator-impactful BUG #5: a probe
    timeout used to be silently ignored, letting Start fire blind."""
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    reason, detail = _detect_start_blockers(
        {"error": "timeout"},
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.1/24"]},
    )
    assert reason == "probe_failed"
    assert "server" in detail


def test_detect_start_blockers_priority_order():
    """Probe failure dominates DOWN port; DOWN port dominates
    missing IP; missing IP dominates same-subnet. Order matters
    because the operator sees the FIRST blocker — and the most
    serious one should win."""
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    # All four problems on the server side at once.
    reason, _ = _detect_start_blockers(
        {"error": "boom",
         "state": "DOWN",
         "ip_addresses": []},
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.1/24"]},
    )
    assert reason == "probe_failed"


def test_detect_start_blockers_returns_none_on_clean():
    """All ports ACTIVE, both sides have IPs, different subnets →
    return (None, None) → Start proceeds without confirm."""
    from widgets.rdma_blast_flow_dialog import _detect_start_blockers
    reason, detail = _detect_start_blockers(
        {"state": "ACTIVE", "ip_addresses": ["10.10.0.1/24"]},
        {"state": "ACTIVE", "ip_addresses": ["10.20.0.1/24"]},
    )
    assert reason is None
    assert detail is None


# ───── Confirm dialog class ─────────────────────────────────────────────


def test_confirm_dialog_renamed():
    """v0.5.153 renames `_SameSubnetTrapConfirmDialog` to the
    broader `_StartBlockerConfirmDialog`. Old name kept as alias
    for back-compat."""
    assert "class _StartBlockerConfirmDialog(" in SRC_BLAST
    # And the alias.
    assert (
        "_SameSubnetTrapConfirmDialog = _StartBlockerConfirmDialog"
        in SRC_BLAST
    )


def test_confirm_dialog_handles_all_four_reasons():
    cls = _extract_class(SRC_BLAST, "_StartBlockerConfirmDialog")
    assert '"probe_failed"' in cls
    assert '"down_port"' in cls
    assert '"missing_ip"' in cls
    assert '"same_subnet"' in cls


def test_confirm_dialog_only_offers_apply_for_autofixable():
    """`down_port` and `probe_failed` CAN'T be auto-fixed (netgen
    can't bring a link up or revive a dead server). For those,
    no Apply button — Open Pre-flight / Continue / Cancel."""
    cls = _extract_class(SRC_BLAST, "_StartBlockerConfirmDialog")
    assert '_AUTOFIXABLE = {"missing_ip", "same_subnet"}' in cls
    assert "Open Pre-flight" in cls


def test_on_probe_complete_handles_open_preflight_choice():
    """The new `open_preflight` choice routes operator into the
    Pre-flight dialog so they can investigate manually."""
    body = _extract_method(SRC_BLAST, "_on_auto_probe_complete")
    assert '"open_preflight"' in body
    assert "_on_preflight_clicked" in body


# ───── Auto-apply validates first ───────────────────────────────────────


def test_auto_apply_validates_before_configure():
    """v0.5.153 fix for BUG #2: previously POSTed configure
    directly; now POSTs validate first, refuses on hard errors.

    The order is enforced by the closure structure: validate's
    on-done callback contains the configure POST, so configure
    only runs after validate returns ok=True. We don't enforce
    source-order (that doesn't match call-order when callbacks
    are involved) — just that both endpoints are reachable and
    the validate callback gates the configure call."""
    body = _extract_method(SRC_BLAST, "_apply_test_ips_then_start")
    # Both endpoints present.
    assert "/api/rdma/test_ifaces/validate" in body
    assert "/api/rdma/test_ifaces/configure" in body
    # Validate's callback gates configure: the configure POST
    # happens inside `_on_validated` AFTER the ok-check.
    assert "_on_validated" in body
    assert "_on_applied" in body
    # And the validate POST is the OUTER call (at the bottom of
    # the function) — it kicks off the whole flow, with the
    # configure POST happening inside `_on_validated`'s ok-path.
    assert "_on_validated," in body  # validate's callback
    assert "_on_applied," in body    # configure's callback


def test_auto_apply_refuses_on_validation_error():
    """If validate returns issues with severity=error, the apply
    aborts with a meaningful message pointing at Pre-flight."""
    body = _extract_method(SRC_BLAST, "_apply_test_ips_then_start")
    assert 'i.get("severity") == "error"' in body
    assert "Open Pre-flight" in body


# ───── Stop fires cleanup ───────────────────────────────────────────────


def test_stop_button_fires_cleanup():
    """v0.5.153 fix for BUG #7: Stop now invokes
    `_cleanup_preflight_state_ids()` so applied test IPs don't
    outlive a stopped test."""
    body = _extract_method(SRC_BLAST, "_on_stop_clicked")
    assert "self._cleanup_preflight_state_ids()" in body


# ───── helpers ──────────────────────────────────────────────────────────


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)


def _extract_class(src: str, name: str) -> str:
    pat = re.compile(
        rf"class {re.escape(name)}\(.*?(?=\nclass \w|\Z)",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"class {name}(...) not found"
    return m.group(0)
