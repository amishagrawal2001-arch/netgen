"""v0.5.47 — admin-console ifaceState() routes kernel-driven NICs to
the Bind-to-DPDK action, not the dangerous Unbind action.

Operator-reported on srv06 (Jun 8 2026) via admin console screenshot:
the Broadcom NICs ens10f3 / ens10f1 (kernel driver tg3) showed up
labelled "Unbound" with an "Unbind" button. Two bugs:

  1. **Wrong label.** A NIC bound to tg3 is NOT unbound — it has a
     kernel driver attached. The label should reflect that.

  2. **Dangerous action.** Clicking "Unbind" would hit
     /api/dpdk/unbind, which detaches the NIC from its current
     driver. For a tg3-managed NIC, that breaks the interface
     (no replacement driver to take over). The button should be
     "Bind to DPDK", which does the safe unbind-from-current +
     bind-to-vfio-pci dance.

Cause: the pre-fix `ifaceState()` checked `status === 'unbound'`
BEFORE the kernel-bound default case. `/api/dpdk/interfaces`
returns `status: 'unbound'` for ANY NIC not bound to
vfio-pci/uio_pci_generic — that includes kernel-bound NICs.
So tg3/bnxt_en/ixgbe/i40e etc all tripped the "Unbound" branch.

v0.5.47 reorders the checks:

  1. KERNEL_DRIVER_OK (mlx5_core, mlx4_core, idxd, ioatdma) →
     bifurcated DPDK-ready, no action
  2. vfio-pci / uio_pci_generic / status='dpdk-bound' →
     DPDK-bound, Unbind action
  3. ANY other real kernel driver → Kernel ({driver}), Bind action
  4. Truly unbound (no driver at all) → Unbound (no driver),
     Bind action (NOT unbind — can't unbind nothing)

Plus a no-PMD warning hint for tg3/e1000/e100 chips that don't
have a stock DPDK PMD. The Bind-to-DPDK button still renders
(operator may have a custom PMD or know something we don't), but
the tooltip warns that vfio-pci bind will succeed without DPDK
apps actually being able to use the NIC.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _iface_state_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"function ifaceState\(iface\)[\s\S]+?\n    \}\n",
        src,
    )
    assert m, "ifaceState() body not located in admin HTML JS"
    return m.group(0)


def _action_btn_block() -> str:
    """The JS that renders the action button cell."""
    src = _SERVER.read_text()
    m = re.search(
        r"let actionBtn = '';\s*\n\s+if \(s\.action === 'bind'\)"
        r"[\s\S]+?else if \(s\.hint\)[\s\S]+?\}\n",
        src,
    )
    assert m, "action-btn rendering block not located"
    return m.group(0)


def test_kernel_driven_nic_gets_bind_action_not_unbind():
    """A kernel-driven NIC (tg3, bnxt_en, e1000e, ixgbe, i40e, ...)
    must route to the `action: 'bind'` branch, NOT `action:
    'unbind'`. Pre-fix the operator's Broadcom tg3 NICs showed
    Unbind buttons; clicking them would have detached the NIC
    from its kernel driver and broken the interface."""
    body = _iface_state_body()
    # Locate the kernel-driver-effective branch.
    m = re.search(
        r"effectiveKDriver\s*=[\s\S]+?if\s*\(effectiveKDriver[\s\S]+?action:\s*['\"]bind['\"]",
        body,
    )
    assert m, (
        "No `effectiveKDriver` branch returning `action: 'bind'`. "
        "Kernel-driven NICs still tripping the dangerous "
        "`action: 'unbind'` branch."
    )


def test_unbound_status_no_longer_routes_to_unbind():
    """The legacy `if (status === 'unbound' ...)` branch returning
    `action: 'unbind'` must be GONE. Either dropped entirely or
    changed to `action: 'bind'`. `status === 'unbound'` from
    /api/dpdk/interfaces just means "not DPDK-bound" — it does
    not mean "no driver at all"."""
    body = _iface_state_body()
    # Pattern: status === 'unbound' followed by action: 'unbind'
    # within close proximity (same return object).
    bad = re.search(
        r"status\s*===\s*['\"]unbound['\"][\s\S]{0,200}?"
        r"action:\s*['\"]unbind['\"]",
        body,
    )
    assert not bad, (
        "Legacy `status === 'unbound' → action: 'unbind'` branch "
        "still present. Kernel-driven NICs would still get the "
        "dangerous Unbind button."
    )


def test_kernel_driver_branch_runs_before_unbound_status_check():
    """The kernel-driver check must come BEFORE any `status ===
    'unbound'` check, because /api/dpdk/interfaces marks
    kernel-bound NICs as status='unbound'. Look for the actual
    statements (`const effectiveKDriver` and `if (status ===`)
    rather than the bare strings — those appear in the comments
    too and would make the ordering test ambiguous."""
    body = _iface_state_body()
    kdrv_idx = body.find("const effectiveKDriver")
    # The actual if-statement, not the comment mention.
    unbound_idx = body.find("if (status === 'unbound')")
    assert kdrv_idx >= 0, "`const effectiveKDriver` assignment missing"
    if unbound_idx >= 0:
        assert kdrv_idx < unbound_idx, (
            "`const effectiveKDriver` runs AFTER the `if (status "
            "=== 'unbound')` check. Kernel-driven NICs would "
            "still hit the unbound branch first."
        )


def test_truly_unbound_gets_bind_not_unbind():
    """The remaining `status === 'unbound'` branch (for NICs with
    no driver at all) must return `action: 'bind'`, not `'unbind'`.
    You can't unbind a NIC that isn't bound to anything."""
    body = _iface_state_body()
    # Find the truly-unbound branch.
    m = re.search(
        r"status\s*===\s*['\"]unbound['\"][\s\S]{0,250}?"
        r"action:\s*['\"](\w+)['\"]",
        body,
    )
    if m:  # branch may have been dropped entirely
        action = m.group(1)
        assert action == "bind", (
            f"Truly-unbound branch action is `{action}` — should "
            f"be `bind`. Can't unbind a NIC with no driver attached."
        )


def test_no_pmd_warning_for_tg3_and_friends():
    """tg3 / e1000 / e100 NICs have no stock-DPDK PMD. The Bind
    button should still render (operator may have a custom PMD),
    but the hint must warn so the operator sees it BEFORE
    clicking."""
    body = _iface_state_body()
    # NO_PMD set must include tg3 at minimum. NB: use `*?` not
    # `+?` for the wildcard — the first char after `[` is the
    # opening quote of 'tg3', so `+?` would eat it and leave no
    # way for the quote class to match.
    assert re.search(
        r"NO_PMD\s*=\s*new\s+Set\(\[\s*['\"]tg3['\"]",
        body,
    ), (
        "No NO_PMD set including tg3. tg3 NICs would Bind to "
        "vfio-pci with no warning, and the DPDK app wouldn't be "
        "able to use them — operator wastes a debugging cycle."
    )
    # The hint text must mention `DPDK PMD`.
    assert "DPDK PMD" in body, (
        "NO_PMD hint doesn't mention 'DPDK PMD' — operator gets "
        "an opaque warning instead of a clear technical reason."
    )


def test_bind_button_renders_hint_as_tooltip():
    """When `s.action === 'bind'` AND `s.hint` is set (no-PMD
    case), the button must include a `title=` tooltip so the
    hint surfaces on hover."""
    block = _action_btn_block()
    # Pattern: in the 'bind' branch, hint is interpolated as a
    # title attribute.
    m = re.search(
        r"s\.action\s*===\s*['\"]bind['\"][\s\S]+?"
        r"(s\.hint|title)[\s\S]+?title=",
        block,
    )
    assert m, (
        "Bind-to-DPDK button doesn't render `s.hint` as a "
        "tooltip — tg3 PMD warning would never surface."
    )


def test_unbind_button_still_renders_for_vfio_pci_nics():
    """Regression guard: an actual DPDK-bound NIC (vfio-pci) must
    still get the Unbind button. The fix is about kernel-driven
    NICs; legitimately DPDK-bound NICs still need to be
    unbindable."""
    body = _iface_state_body()
    m = re.search(
        r"driver\s*===\s*['\"]vfio-pci['\"][\s\S]+?"
        r"action:\s*['\"]unbind['\"]",
        body,
    )
    assert m, (
        "vfio-pci branch no longer returns `action: 'unbind'` — "
        "v0.5.47 fix over-corrected and broke legitimate unbind."
    )


def test_pyproject_version_at_least_0547():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 47), (
        f"Version {m.group(1)} < 0.5.47"
    )
