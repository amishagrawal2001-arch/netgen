"""Regression tests for v0.5.18 — DPDK menu Tier 1 optimizations.

Five distinct improvements:

  1. Menu restructured: 10 flat items → Primary (★ Setup DPDK,
     Blast a Flow) + Diagnostics + Advanced ▸ submenu (atomic).
  2. Status + Verify merged into Diagnostics dialog (two tabs).
  3. Time estimates (Action.eta) shown in wizard rows.
  4. Shared TTL cache for /api/dpdk/status (30s).
  5. Status-bar chip tooltip leads with "Missing: <list>" summary.

These are UX/perf optimizations, not bug fixes. The previous
implementation worked; this release cuts operator clicks +
round-trips and reduces menu cognitive load.
"""
from __future__ import annotations

import re
from pathlib import Path


_MAIN = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "main.py"
)
_MENU_ACTIONS = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "dpdk_menu_actions.py"
)
_DIAGNOSTICS = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_diagnostics_dialog.py"
)
_ORCHESTRATOR = (
    Path(__file__).resolve().parents[1]
    / "utils" / "dpdk_orchestrator.py"
)
_CHIP = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_readiness_chip.py"
)
_WIZARD = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


# ─────────────────────────────────────────────── menu restructure


def test_menu_has_setup_dpdk_as_primary_entry():
    """The new ★ Setup DPDK item should be at the top of the DPDK
    submenu — single canonical entry point for new operators."""
    src = _MAIN.read_text()
    assert "Setup DPDK" in src, (
        "DPDK menu doesn't have the new '★ Setup DPDK' primary item."
    )
    # And it points to show_dpdk_make_ready_dialog (the existing
    # one-click orchestrator). Window {0,2000} because the tooltip
    # text between the label and the connect() call is large.
    assert re.search(
        r"Setup DPDK[\s\S]{0,2000}?show_dpdk_make_ready_dialog",
        src,
    ), (
        "★ Setup DPDK isn't wired to show_dpdk_make_ready_dialog. "
        "Wrong entry point."
    )


def test_menu_has_diagnostics_item():
    """Status + Verify are merged into one Diagnostics dialog."""
    src = _MAIN.read_text()
    assert "Diagnostics" in src, (
        "DPDK menu missing the Diagnostics entry that replaces "
        "Status + Verify."
    )
    assert "show_dpdk_diagnostics" in src, (
        "Diagnostics action not wired to show_dpdk_diagnostics."
    )


def test_menu_has_advanced_submenu():
    """Atomic actions (Quick Start, Bind/Unbind, Configure
    Hugepages/IOMMU, Load VFIO) live under an Advanced submenu so
    they don't clutter the top level."""
    src = _MAIN.read_text()
    assert re.search(
        r'QMenu\(\s*["\']Advanced["\']',
        src,
    ), (
        "No 'Advanced' submenu created — atomic actions still "
        "clutter top-level DPDK menu."
    )
    # Quick Start should be under Advanced.
    m = re.search(
        r"dpdk_advanced_menu\s*=\s*QMenu[\s\S]+?(?=dpdk_menu\.addSeparator|"
        r"# v0\.|rdma_menu|class\s+)",
        src,
    )
    assert m, "advanced submenu code block not found"
    advanced_body = m.group(0)
    for item in (
        "Quick Start Wizard",
        "Bind Interface",
        "Unbind Interface",
        "Configure Hugepages",
        "Configure IOMMU",
        "Load VFIO Modules",
    ):
        assert item in advanced_body, (
            f"Advanced submenu doesn't contain '{item}'. Atomic "
            f"action still at top level."
        )


# ─────────────────────────────────────────────── diagnostics dialog


def test_diagnostics_dialog_exists_with_two_tabs():
    """The merged dialog needs both Status and Verify tabs."""
    src = _DIAGNOSTICS.read_text()
    assert "class DpdkDiagnosticsDialog" in src, (
        "DpdkDiagnosticsDialog class not found."
    )
    assert "QTabWidget" in src, (
        "Diagnostics dialog doesn't use a QTabWidget — Status and "
        "Verify should be in separate tabs."
    )
    # Both endpoints must be queried.
    assert "/api/dpdk/status" in src, (
        "Diagnostics dialog doesn't query /api/dpdk/status."
    )
    assert "/api/dpdk/verify" in src, (
        "Diagnostics dialog doesn't query /api/dpdk/verify."
    )


def test_diagnostics_dialog_uses_ttl_cache():
    """The dialog should hit the TTL cache before doing fresh HTTP
    — that's the whole point of the cache."""
    src = _DIAGNOSTICS.read_text()
    assert "get_cached_dpdk_status" in src, (
        "Diagnostics dialog doesn't check the TTL cache."
    )
    assert "cache_dpdk_status" in src, (
        "Diagnostics dialog doesn't populate the TTL cache on "
        "successful fetch — other surfaces won't see fresh data."
    )


def test_show_dpdk_diagnostics_method_exists():
    """The menu hooks to a `show_dpdk_diagnostics` method that
    opens the dialog."""
    src = _MENU_ACTIONS.read_text()
    assert "def show_dpdk_diagnostics" in src, (
        "show_dpdk_diagnostics method missing from "
        "TrafficGenClientDPDKMenuActions."
    )


# ─────────────────────────────────────────────── action.eta


def test_action_dataclass_has_eta_field():
    """The Action dataclass should carry an eta field for the
    wizard row label."""
    src = _ORCHESTRATOR.read_text()
    assert re.search(
        r"eta\s*:\s*str\s*=\s*['\"]\s*['\"]",
        src,
    ), (
        "Action dataclass missing `eta: str = \"\"` field — wizard "
        "rows can't show duration estimates."
    )


def test_plan_populates_eta_for_install_dpdk():
    """The longest-running action (INSTALL_DPDK ~5-10 min) MUST
    have an ETA — that's the one operators most need to know about
    upfront."""
    src = _ORCHESTRATOR.read_text()
    # Match the full Action(...) block: from kind=ActionKind.INSTALL_DPDK
    # to the closing '))' (out.append(Action(...))).
    m = re.search(
        r"kind=ActionKind\.INSTALL_DPDK[\s\S]+?satisfies_keys=[^)]+\)[\s\S]+?\)",
        src,
    )
    assert m, "INSTALL_DPDK action block not found"
    body = m.group(0)
    assert re.search(r'eta\s*=\s*["\'][^"\']*(?:min|sec)', body), (
        "INSTALL_DPDK action doesn't set eta. Operators see no "
        "warning about the 5-10 min wait."
    )


def test_plan_populates_eta_for_iommu_with_reboot_warning():
    """ENABLE_IOMMU has a special property — needs reboot. The
    ETA string should call that out."""
    src = _ORCHESTRATOR.read_text()
    m = re.search(
        r"kind=ActionKind\.ENABLE_IOMMU[\s\S]+?satisfies_keys=[^)]+\)[\s\S]+?\)",
        src,
    )
    assert m, "ENABLE_IOMMU action block not found"
    body = m.group(0)
    assert re.search(r'eta\s*=\s*["\'][^"\']*REBOOT', body, re.IGNORECASE), (
        "ENABLE_IOMMU action eta doesn't mention REBOOT. Operator "
        "won't be warned about the reboot upfront."
    )


def test_step_row_renders_eta_in_pending_state():
    """The wizard's _StepRow must include the eta in the row text
    when pending/running."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _render\(self\)[\s\S]+?setText",
        src,
    )
    body = m.group(0)
    assert "eta" in body, (
        "_StepRow._render doesn't reference action.eta — wizard "
        "rows won't show duration estimates."
    )
    # And should only render eta while state is pending/running.
    assert re.search(
        r'pending["\']\s*,\s*["\']running',
        body,
    ) or "pending" in body and "running" in body, (
        "_StepRow._render doesn't gate eta display by state. "
        "Should only show on pending/running, drop on ok/fail/skip."
    )


# ─────────────────────────────────────────────── TTL cache


def test_cache_helpers_exist():
    """get_cached_dpdk_status + cache_dpdk_status +
    invalidate_dpdk_status_cache must all exist in the menu-actions
    module so any surface can use the shared cache."""
    src = _MENU_ACTIONS.read_text()
    for name in (
        "get_cached_dpdk_status",
        "cache_dpdk_status",
        "invalidate_dpdk_status_cache",
    ):
        assert f"def {name}" in src, (
            f"TTL cache helper {name}() missing from "
            f"traffic_client.dpdk_menu_actions."
        )


def test_cache_ttl_is_around_30_seconds():
    """Default TTL should be ~30s — short enough that mutations
    not properly invalidated only show stale for half a minute,
    long enough that opening 2-3 dialogs in a row hits cache."""
    src = _MENU_ACTIONS.read_text()
    assert re.search(
        r"_DPDK_STATUS_CACHE_TTL_S\s*=\s*(?:15|30|60)",
        src,
    ), (
        "TTL cache default isn't in the 15-60s sane range. Either "
        "too short (perpetual miss) or too long (stale state)."
    )


def test_post_mutation_invalidates_cache():
    """Any successful POST to a DPDK-mutating endpoint should
    invalidate the cache so the next read shows fresh state."""
    src = _MENU_ACTIONS.read_text()
    # The _DpdkApiWorker.run() body should reference
    # invalidate_dpdk_status_cache after a successful POST.
    m = re.search(
        r"def run\(self\)[\s\S]+?(?=\n    def |\nclass )",
        src,
    )
    assert m, "_DpdkApiWorker.run not found"
    body = m.group(0)
    assert "invalidate_dpdk_status_cache" in body, (
        "_DpdkApiWorker.run doesn't invalidate the cache on a "
        "successful POST mutation. Stale state would persist for "
        "up to the full TTL after bind / unbind / etc."
    )


# ─────────────────────────────────────────────── chip tooltip


def test_chip_tooltip_lists_missing_items_summary():
    """Tooltip should lead with a one-line 'Missing: X, Y' summary
    when state is amber/red so operators don't have to read 5 rows
    to figure out the problem."""
    src = _CHIP.read_text()
    # Must build a `missing_items` list.
    assert "missing_items" in src, (
        "Chip classify function doesn't build a missing_items list "
        "— tooltip can't lead with a summary."
    )
    # And must add it to the front of `tip`.
    assert re.search(
        r"Missing:\s*\{",
        src,
    ) or re.search(
        r"f['\"]Missing:",
        src,
    ), (
        "Chip tooltip doesn't have a 'Missing: ...' lead line. "
        "Operators still have to read individual rows."
    )


def test_chip_apply_populates_ttl_cache():
    """When the chip polls /api/dpdk/status, the result should also
    land in the shared cache so the Diagnostics dialog / wizard
    can reuse it without their own round-trip."""
    src = _CHIP.read_text()
    assert "cache_dpdk_status" in src, (
        "Chip's _apply() doesn't populate the TTL cache — other "
        "surfaces won't benefit from the chip's regular polling."
    )


def test_pyproject_version_at_least_0518():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 18), (
        f"Version {m.group(1)} < 0.5.18"
    )
