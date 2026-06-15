"""v0.5.152: Blast dialog gets compact params + taller stats +
auto-detect-on-Start + Keep-IPs option.

Operator (after hitting the same-subnet trap a second time because
v0.5.150 cleanup ran on dialog close):

    "option C, and also make test paramter section compact and
     increase Live stats log vertical area"

Closes the "I forgot to run Pre-flight" rake. Now Start probes
both endpoints first; if the same-subnet trap is detected without
applied test IPs already in play, a 3-button confirm pops with
Apply & Start / Continue / Cancel. A complementary 📌 Keep
checkbox in the Pre-flight dialog stops the parent's auto-cleanup
so operators can iterate without re-applying every run.
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
SRC_TOP = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


# ─────────────────── Compact params + taller Live stats ─────────────────


def test_test_params_grid_compacted():
    """Tighten the QGridLayout spacing/margins so the test-params
    section claims less vertical space, freeing room for Live
    stats. v0.5.152: setVerticalSpacing(2) (was 4),
    setContentsMargins(6, 2, 6, 2) (was (8, 4, 8, 4))."""
    assert "tg.setVerticalSpacing(2)" in SRC_BLAST
    assert "tg.setContentsMargins(6, 2, 6, 2)" in SRC_BLAST


def test_spinbox_widths_shrunk():
    """Spinboxes were 120 px; v0.5.152 trims to 100 px for the
    same compaction goal. Five spinboxes affected."""
    # Confirm 100-px appears at LEAST 5 times (the five test-param
    # spinboxes). Bumps to other spinboxes elsewhere would only
    # add more matches.
    n100 = len(re.findall(
        r"\.setFixedWidth\(100\)", SRC_BLAST))
    assert n100 >= 5, f"expected ≥5 spinboxes at 100 px; got {n100}"
    # And the old 120-px shouldn't appear anywhere in the
    # test-params section. (Other places may still use 120 — we
    # only check the live source doesn't have ALL the historical
    # five.)
    n120 = len(re.findall(
        r"\.setFixedWidth\(120\)", SRC_BLAST))
    # Allow at most a couple of stray 120s elsewhere.
    assert n120 < 5, (
        f"too many old 120-px spinboxes remain ({n120}); compaction "
        f"didn't sweep them all"
    )


def test_live_stats_minheight_bumped():
    """Live stats panel was minHeight=160 → 280 (v0.5.152). And
    the panel is added with stretch=1 so it claims any freed
    vertical room."""
    assert "self._stats_view.setMinimumHeight(280)" in SRC_BLAST
    assert "root.addWidget(stats_box, 1)" in SRC_BLAST


# ─────────────────── Auto-detect same-subnet trap on Start ──────────────


def test_detect_same_subnet_trap_helper_exists():
    """Pure helper at module level so tests can exercise it
    without spinning up a QDialog."""
    from widgets.rdma_blast_flow_dialog import _detect_same_subnet_trap
    # Same subnet → trap.
    t, net = _detect_same_subnet_trap(
        {"ip_addresses": ["10.10.0.1/24"]},
        {"ip_addresses": ["10.10.0.2/24"]},
    )
    assert t is True
    assert net == "10.10.0.0/24"


def test_detect_helper_returns_false_for_different_subnets():
    from widgets.rdma_blast_flow_dialog import _detect_same_subnet_trap
    t, net = _detect_same_subnet_trap(
        {"ip_addresses": ["10.10.0.1/24"]},
        {"ip_addresses": ["10.20.0.1/24"]},
    )
    assert t is False
    assert net is None


def test_detect_helper_skips_ipv6():
    """IPv6 GIDs don't suffer the IPv4 same-host loopback trap in
    the same way. The helper must ignore IPv6 entries."""
    from widgets.rdma_blast_flow_dialog import _detect_same_subnet_trap
    t, _ = _detect_same_subnet_trap(
        {"ip_addresses": ["fe80::1/64"]},
        {"ip_addresses": ["fe80::2/64"]},
    )
    assert t is False


def test_detect_helper_no_ips_no_trap():
    """No IPs anywhere → can't be trapped; preflight has bigger
    fish to fry (likely missing-IP banner)."""
    from widgets.rdma_blast_flow_dialog import _detect_same_subnet_trap
    t, _ = _detect_same_subnet_trap({}, {"ip_addresses": []})
    assert t is False


def test_on_start_clicked_runs_probe_before_perftest_for_same_host():
    """When server_tg_url == client_tg_url AND no preflight state
    is already applied, _on_start_clicked must defer to
    _auto_probe_then_start instead of going straight to perftest."""
    body = _extract_method(SRC_BLAST, "_on_start_clicked")
    # Has the same-host guard.
    assert "same_host = (self._server_tg_url == self._client_tg_url)" in body
    # Has the already-applied guard.
    assert "self._preflight_state_ids" in body
    # And the deferral target.
    assert "self._auto_probe_then_start(" in body


def test_on_start_skips_probe_when_state_already_applied():
    """If the operator already went through Pre-flight, don't
    probe a second time. Just call _proceed_with_start."""
    body = _extract_method(SRC_BLAST, "_on_start_clicked")
    # The check is `not already_applied`; when already_applied
    # is True, we don't probe.
    assert "already_applied" in body


def test_proceed_with_start_method_exists():
    """The original start-perftest logic must live in its own
    method so the confirm dialog can defer / proceed."""
    assert "def _proceed_with_start" in SRC_BLAST


def test_apply_test_ips_then_start_method_exists():
    """The 'Apply & Start' button must trigger configure POST →
    on success → proceed."""
    assert "def _apply_test_ips_then_start" in SRC_BLAST


def test_auto_apply_posts_configure_endpoint():
    """The auto-apply path must POST to the v0.5.150 configure
    endpoint, not jump straight to applying via raw `ip` calls
    on the client."""
    body = _extract_method(SRC_BLAST, "_apply_test_ips_then_start")
    assert "/api/rdma/test_ifaces/configure" in body


def test_auto_apply_picks_different_subnets():
    """The auto-pick must NOT use the same subnet on both ifaces
    (that's literally the trap we're trying to escape). Pin two
    distinct /24s."""
    body = _extract_method(SRC_BLAST, "_apply_test_ips_then_start")
    # Look for two different subnets in the cidr_pair tuple.
    assert "10.42.0.1/24" in body
    assert "10.43.0.1/24" in body


def test_auto_apply_tracks_state_id_on_success():
    """Successful apply must store the state_id in
    self._preflight_state_ids so closeEvent's auto-cleanup
    finds it later."""
    body = _extract_method(SRC_BLAST, "_apply_test_ips_then_start")
    assert "self._preflight_state_ids.add(" in body


def test_confirm_dialog_class_exists():
    # v0.5.153 renamed to _StartBlockerConfirmDialog; v0.5.158
    # dropped the back-compat alias.
    assert "class _StartBlockerConfirmDialog(" in SRC_BLAST


def test_confirm_dialog_has_three_choices():
    """Apply & Start / Continue anyway / Cancel — three buttons,
    three string options on the choice() return."""
    # v0.5.153 renamed; both class names extract the same body
    # since the old name is now just an alias.
    cls = _extract_class(SRC_BLAST, "_StartBlockerConfirmDialog")
    assert '"Apply && Start"' in cls
    assert '"Continue anyway"' in cls
    assert '"Cancel"' in cls
    # And the choice strings.
    assert '"apply"' in cls
    assert '"continue"' in cls
    assert '"cancel"' in cls


def test_confirm_dialog_explains_the_trap():
    """The body text must explain WHY this is happening so the
    operator can make an informed decision."""
    # v0.5.153 renamed; both class names extract the same body
    # since the old name is now just an alias.
    cls = _extract_class(SRC_BLAST, "_StartBlockerConfirmDialog")
    # v0.5.153 reworded slightly ("Linux routes" vs "Linux will
    # route") when generalizing the class to cover four blocker
    # types.
    assert ("Linux routes" in cls or "Linux will route" in cls)
    assert "<code>lo</code>" in cls
    assert "QP" in cls and "RTR" in cls


def test_confirm_dialog_choice_method_returns_string():
    # v0.5.153 renamed; both class names extract the same body
    # since the old name is now just an alias.
    cls = _extract_class(SRC_BLAST, "_StartBlockerConfirmDialog")
    assert "def choice(self) -> str" in cls


# ─────────────────── 📌 Keep-IPs checkbox on Pre-flight ──────────────────


def test_preflight_has_keep_checkbox():
    """Pre-flight dialog grew a 📌 Keep checkbox that tells the
    parent to skip auto-cleanup."""
    assert "_keep_check" in SRC_PRE
    assert "Keep these test IPs" in SRC_PRE


def test_preflight_keep_applied_method_exists():
    """Caller pulls this on close. Returns a bool."""
    assert "def keep_applied(self) -> bool" in SRC_PRE


def test_preflight_keep_applied_defensive_on_missing_attr():
    """Init-race safety: keep_applied must not crash if the
    checkbox doesn't exist yet."""
    body = _extract_method(SRC_PRE, "keep_applied")
    assert "AttributeError" in body or "getattr" in body
    assert "RuntimeError" in body or "return False" in body


# ─────────────────── Blast + Topology honor keep_applied ────────────────


def test_blast_skips_tracking_when_keep_is_set():
    """In `_on_preflight_clicked`, the parent must check
    dlg.keep_applied() and skip adding the state_id to the
    cleanup set when True."""
    body = _extract_method(SRC_BLAST, "_on_preflight_clicked")
    assert "dlg.keep_applied()" in body
    # And the status text changes to reflect the choice.
    assert "📌 Keep" in body


def test_topology_skips_tracking_when_keep_is_set():
    """Same wiring on the Topology dialog."""
    body = _extract_method(SRC_TOP, "_on_preflight_clicked")
    assert "dlg.keep_applied()" in body
    assert "📌 Keep" in body


# ─────────────────── helpers ────────────────────────────────────────────


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
