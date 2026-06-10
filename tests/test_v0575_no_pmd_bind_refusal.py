"""v0.5.75 — refuse bind when kernel driver has no DPDK PMD.

Operator-bite on srv06 (Jun 9 2026):

  "trying to bind dpdk on ens10f3 from admin console, screen seems
   to be hanging and does not enable DPDK."

ens10f3 is a BCM5719 — `tg3` chip. The DPDK bnxt PMD only
supports the modern NetXtreme-E/Thor controllers, NOT the
tg3 family (BCM5717/5719/5720). Clicking Bind triggered a
30s dpdk_bind.sh subprocess that succeeded at the sysfs level
(card moves to vfio-pci) but DPDK applications can't use it
because no PMD recognises the device.

Combined with two UI gaps that made it look like a hang:

  1. The button stayed clickable for tg3 cards — v0.5.47 only
     added a tooltip warning, not a disabled state.

  2. `bindInterface` had NO try/catch around the fetch, so
     a transient network error during the 30s subprocess
     produced silent failure (button re-enabled by .finally
     with no toast). The screen appeared to "hang then
     reset" without any explanation.

v0.5.75 ships:

  * Server `/api/dpdk/bind`: refuse with HTTP 409 + code
    `NO_PMD` when the current kernel driver is in the
    no-PMD set (tg3, e1000, e1000e, e100, r8169, atlantic).
    Reads the driver via sysfs symlink. `force=true`
    overrides (operator may have an out-of-tree PMD).

  * JS: render the Bind button DISABLED with label
    "Bind (no PMD)" for tg3-class drivers — not just
    tooltipped. The hint still shows on hover.

  * JS: `bindInterface` fetch wrapped in try/catch. Network
    errors get a toast explaining the bind may have
    disconnected the management NIC. Immediate "Binding
    <iface>…" toast on click so the operator sees the
    action started.

  * JS: NO_PMD 409 surfaces a tailored confirm dialog with
    the rejection reason and a Force/Cancel choice — before
    the generic active-routes check.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ───────── Server-side NO_PMD guard ─────────


def test_dpdk_bind_refuses_no_pmd_drivers():
    src = _src()
    # v0.5.80 hoisted _NO_PMD_DRIVERS to module scope. The bind
    # function still references it for the 409 guard; the set
    # itself lives outside the function now.
    m = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    assert "_NO_PMD_DRIVERS" in body, (
        "dpdk_bind doesn't reference the no-PMD driver set"
    )
    # And the module-scope definition must exist and include the
    # canonical no-PMD drivers.
    for drv in ("tg3", "e1000", "e1000e", "r8169"):
        assert f'"{drv}"' in src, (
            f"_NO_PMD_DRIVERS missing {drv}"
        )


def test_dpdk_bind_reads_driver_from_sysfs():
    """The driver lookup must read the sysfs symlink (no
    subprocess), because dpdk_bind() runs in the request
    handler — a subprocess per request would add ~50ms."""
    src = _src()
    m = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "os.readlink(f\"/sys/bus/pci/devices/{pci}/driver\")" in body or \
           "os.readlink" in body and "/driver" in body, (
        "dpdk_bind doesn't read driver from sysfs symlink"
    )


def test_dpdk_bind_no_pmd_returns_409_with_code():
    """The refusal must use HTTP 409 + code='NO_PMD' so the
    front-end can offer a force=true override (same pattern as
    v0.2.76 BIND_UNSAFE)."""
    src = _src()
    m = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert '"code": "NO_PMD"' in body
    assert "409" in body
    # And the operator gets the force=true escape hatch.
    assert '"can_force": True' in body


def test_dpdk_bind_force_true_bypasses_no_pmd_guard():
    """`force=true` skips the NO_PMD check — the operator may
    know about an out-of-tree PMD we don't ship."""
    src = _src()
    m = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # The NO_PMD block must be gated on `not force`.
    assert re.search(
        r"if\s+not\s+force\s+and\s+interface:\s*\n[\s\S]{0,800}?_NO_PMD_DRIVERS",
        body,
    ), "NO_PMD guard isn't gated on `not force`"


# ───────── Client-side renderer ─────────


def test_client_no_pmd_set_matches_server():
    src = _src()
    # The JS NO_PMD set should mirror the server-side set.
    m = re.search(
        r"const NO_PMD = new Set\(\[([\s\S]+?)\]\);",
        src,
    )
    assert m
    js_set = m.group(1)
    for drv in ("tg3", "e1000", "e1000e", "r8169"):
        assert f"'{drv}'" in js_set, (
            f"JS NO_PMD set missing {drv}"
        )


def test_client_renders_disabled_button_for_no_pmd():
    src = _src()
    # The renderer must mark the button `disabled` and use a
    # different label when noPmd=true. Pre-fix v0.5.47 only
    # tooltipped — the button stayed clickable.
    assert "Bind (no PMD)" in src, (
        "Disabled-bind button label not found"
    )
    # The disabled attr must be present in the noPmd branch.
    m = re.search(
        r"if\s+\(s\.noPmd\)\s*\{[\s\S]{0,300}?disabled",
        src,
    )
    assert m, (
        "Bind button isn't disabled in the noPmd branch"
    )


def test_client_state_carries_no_pmd_flag():
    src = _src()
    # The ifaceState helper must surface noPmd on the returned
    # state object (not just hint).
    assert "noPmd: noPmd" in src, (
        "ifaceState() doesn't surface the noPmd flag"
    )


# ───────── Client-side fetch hardening ─────────


def test_bind_interface_has_try_catch():
    """The bindInterface fetch must be wrapped in try/catch so
    network errors surface as a toast instead of looking like a
    silent hang."""
    src = _src()
    idx = src.find("async function bindInterface(")
    assert idx >= 0
    body = src[idx:idx + 3500]
    # try/catch around the fetch.
    assert re.search(
        r"try\s*\{\s*\n\s*r\s*=\s*await\s+fetch\(\s*['\"]/api/dpdk/bind['\"]",
        body,
    ), "bindInterface fetch isn't wrapped in try"
    assert "catch (e)" in body, "bindInterface has no catch clause"


def test_bind_interface_immediate_progress_toast():
    """Operator should see "Binding <iface>…" toast on click,
    so the 5-30s subprocess wait isn't perceived as a hang."""
    src = _src()
    idx = src.find("async function bindInterface(")
    body = src[idx:idx + 3500]
    assert re.search(
        r"toast\(`Binding\s+\$\{iface\.name",
        body,
    ), "No immediate `Binding <iface>` toast"


def test_bind_interface_handles_no_pmd_409():
    """When server returns 409 + code=NO_PMD, the client must
    show a tailored confirm dialog with force=true override —
    not the generic 'unknown error' toast."""
    src = _src()
    idx = src.find("async function bindInterface(")
    body = src[idx:idx + 3500]
    assert re.search(
        r"r\.status\s*===\s*409\s*&&\s*data\.code\s*===\s*['\"]NO_PMD['\"]",
        body,
    ), "bindInterface doesn't special-case the NO_PMD 409"


# ───────── Version ─────────


def test_pyproject_version_at_least_0575():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 75), (
        f"Version {m.group(1)} < 0.5.75"
    )
