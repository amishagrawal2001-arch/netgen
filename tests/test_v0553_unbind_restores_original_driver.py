"""v0.5.53 — /api/dpdk/unbind restores the original kernel driver
from the persistent registry, even after reboot. Audit finding H3.

Pre-fix the original-driver hint lived only in
`/tmp/netgen_admin_bind_history.json`, which dies on reboot.
After a reboot, the unbind handler passed empty `kernel_driver`
to `dpdk_bind.sh`, which then fell back to a vendor-ID heuristic
that picks `ice` for any Intel NIC (vendor 0x8086). That's wrong
for:

  - X710 / X722 family → driver should be `i40e`
  - X550 / X540 → driver should be `ixgbe`
  - 82576 / 82575 / 82574 → driver should be `igb` or `e1000e`

The unbind would either fail with "driver not loaded" or restore
the wrong driver, leaving the NIC half-bricked until the operator
SSH'd in and ran `modprobe i40e` manually.

v0.5.53:

  1. /api/dpdk/bind now snapshots the current kernel driver from
     bind_history (which the admin UI POSTs right before binding)
     and passes it to `_dpdk_persist_bind(..., original_driver=...)`.

  2. `_dpdk_persist_bind` records `original_driver` in the
     `/etc/netgen/dpdk-interfaces.json` registry. On repeat-bind
     calls (replacing an existing entry), the old `original_driver`
     is preserved if the new call doesn't provide one.

  3. /api/dpdk/unbind reads the persistent registry FIRST for
     `original_driver` when the request body didn't specify
     `kernel_driver`. Falls back to /tmp/...history (in-session),
     then to the dpdk_bind.sh heuristic.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_persist_bind_signature_accepts_original_driver():
    """`_dpdk_persist_bind` must accept an `original_driver`
    keyword argument."""
    src = _src()
    m = re.search(
        r"def _dpdk_persist_bind\([^)]+\)",
        src,
    )
    assert m, "_dpdk_persist_bind() signature not located"
    sig = m.group(0)
    assert "original_driver" in sig, (
        "_dpdk_persist_bind() doesn't accept original_driver — "
        "can't record the kernel driver to restore on unbind."
    )


def test_persist_bind_writes_original_driver_to_registry():
    """The persisted entry must include the `original_driver`
    field when supplied."""
    src = _src()
    body = re.search(
        r"def _dpdk_persist_bind\([\s\S]+?(?=\ndef [a-z_])",
        src,
    ).group(0)
    # The entry dict gets `original_driver` added conditionally.
    assert re.search(
        r'entry\[["\']original_driver["\']\]\s*=\s*original_driver',
        body,
    ), (
        "Persist function doesn't write original_driver to the "
        "entry — registry would be missing the field even after "
        "the fix."
    )


def test_persist_bind_preserves_original_driver_on_repeat():
    """A second bind call to the SAME PCI must preserve the old
    `original_driver` if the new call didn't supply one — without
    this, repeat binds (which are common: operator clicks Bind,
    then clicks again to test, etc.) would lose the kernel-driver
    memory."""
    src = _src()
    body = re.search(
        r"def _dpdk_persist_bind\([\s\S]+?(?=\ndef [a-z_])",
        src,
    ).group(0)
    # Look for the prev-lookup + conditional restoration.
    assert re.search(
        r"prev\s*=\s*next\(",
        body,
    ) or re.search(
        r"original_driver\s+is\s+None\s+and\s+prev",
        body,
    ), (
        "Repeat-bind path doesn't preserve old original_driver — "
        "second bind would wipe the memory."
    )


def test_bind_endpoint_captures_original_driver_from_history():
    """`/api/dpdk/bind` must look up the kernel_driver in
    bind_history (set just before bind by the admin UI) and pass
    it through to `_dpdk_persist_bind`."""
    src = _src()
    bind_body = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    # Look for _load_bind_history call before _dpdk_persist_bind.
    hist_idx = bind_body.find("_load_bind_history")
    persist_idx = bind_body.find("_dpdk_persist_bind(")
    assert hist_idx >= 0 and persist_idx >= 0, (
        "bind handler doesn't call _load_bind_history before "
        "_dpdk_persist_bind"
    )
    assert hist_idx < persist_idx, (
        "_load_bind_history call must precede _dpdk_persist_bind — "
        "we need the original driver name to pass through."
    )
    # And the persist call must include original_driver kwarg.
    assert re.search(
        r"_dpdk_persist_bind\([\s\S]{0,250}?original_driver\s*=",
        bind_body,
    ), (
        "_dpdk_persist_bind call doesn't pass original_driver — "
        "registry won't get the field even though it was looked up."
    )


def test_unbind_endpoint_reads_original_driver_from_registry():
    """`/api/dpdk/unbind` must consult the persistent registry
    for `original_driver` when the request body doesn't include
    `kernel_driver`. Pre-fix, after reboot the in-memory bind
    history was gone and we fell through to the broken heuristic."""
    src = _src()
    unbind_body = re.search(
        r"def dpdk_unbind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    # The registry path is _DPDK_BIND_REGISTRY (used in unbind body).
    assert "_DPDK_BIND_REGISTRY" in unbind_body, (
        "Unbind handler doesn't reference _DPDK_BIND_REGISTRY — "
        "can't read the persisted original_driver."
    )
    # And the field name we're reading.
    assert "original_driver" in unbind_body, (
        "Unbind handler doesn't extract original_driver from "
        "the registry entry."
    )


def test_unbind_endpoint_falls_back_to_tmp_history():
    """When the registry doesn't have an entry (e.g., legacy bind
    done before v0.5.53), fall back to the /tmp history. Pre-fix
    that was the ONLY source; v0.5.53 makes it a fallback rather
    than removing it."""
    src = _src()
    unbind_body = re.search(
        r"def dpdk_unbind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    # The fallback is _load_bind_history.
    assert "_load_bind_history" in unbind_body, (
        "Unbind handler doesn't fall back to the /tmp history — "
        "legacy binds (before v0.5.53) would have no driver-restore"
    )


def test_unbind_logs_when_restoring_from_registry():
    """When we successfully restore a driver from the registry,
    log it so the operator can trace why the unbind picked a
    specific driver."""
    src = _src()
    unbind_body = re.search(
        r"def dpdk_unbind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    # Match across f-string concatenation (`"Restoring " f"original_driver=..."`)
    # — the source has `Restoring "` close-quote whitespace then a new
    # f-string starting with `original_driver`. Anything-but-newline-doesn't-
    # contain-Restoring acceptable here.
    assert re.search(
        r"Restoring[\s\S]{0,80}?original_driver",
        unbind_body,
    ), (
        "Unbind doesn't log when it restores the driver from "
        "the registry — silent recovery is hard to debug."
    )


def test_pyproject_version_at_least_0553():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 53), (
        f"Version {m.group(1)} < 0.5.53"
    )
