"""Operator-bite caught on srv06 (Jun 11 2026): TWO bugs hit at
once on a Mellanox ConnectX-6 bifurcated DPDK stream launch:

1. mlx5_net: ingress mlx5dv_dr_create_domain failed
   mlx5_net: probe of PCI device 0000:2b:00.0 aborted —
     Cannot allocate memory

   Root cause: systemd unit had no LimitMEMLOCK setting. Default
   was the ~64 KiB system cap; the mlx5 PMD needs to mlock NIC
   queue + flow-table memory and bailed.

2. /usr/local/bin/tx_worker: unrecognized option '--tx-cores'

   Root cause: the binary at /usr/local/bin/ was built by the
   previous wheel's install_dpdk.sh (Jun 8). The new wheel's
   launcher (utils/dpdk_tx_worker.py) passes --tx-cores; the
   stale binary doesn't know it.

Both fixes ship in netgen-install / netgen-upgrade for the
next release:

- netgen-install systemd unit gains LimitMEMLOCK=infinity,
  LimitNOFILE=1048576, LimitNPROC=infinity.
- netgen-upgrade gains a _rebuild_tx_worker step that fires
  after pip-install + import-verify. Best-effort: if build
  deps are missing (libdpdk-dev / meson / ninja), warns and
  continues.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_INSTALL = _REPO / "scripts" / "tarball" / "netgen-install"
_UPGRADE = _REPO / "resources" / "tarball" / "netgen-upgrade"


# ─── Systemd unit gets the Mellanox rlimits ───


def test_install_unit_has_limit_memlock_infinity():
    """Without LimitMEMLOCK=infinity, Mellanox mlx5 PMD probe
    fails with 'Cannot allocate memory'."""
    src = _INSTALL.read_text()
    assert "LimitMEMLOCK=infinity" in src, (
        "netgen-install systemd unit doesn't set LimitMEMLOCK=infinity"
        " — Mellanox mlx5 PMD will fail with 'Cannot allocate memory'"
    )


def test_install_unit_has_limit_nofile_bump():
    """Mellanox 200G NICs can want 1k+ fds for per-queue
    structures. Default 1024 hits EMFILE."""
    src = _INSTALL.read_text()
    m = re.search(r"LimitNOFILE=(\d+)", src)
    assert m, "netgen-install unit doesn't bump LimitNOFILE"
    assert int(m.group(1)) >= 65536, (
        f"LimitNOFILE={m.group(1)} too low for high-queue NICs"
    )


def test_install_unit_has_limit_nproc_infinity():
    """tx_worker spawns N worker threads per stream. Without
    NPROC=infinity, multi-instance launches hit fork() EAGAIN."""
    src = _INSTALL.read_text()
    assert "LimitNPROC=infinity" in src


def test_install_unit_documents_mellanox_bite():
    """Tomorrow's maintainer should know WHY these rlimits are
    there. Anchor on the operator-visible error string so the
    grep-from-error path works."""
    src = _INSTALL.read_text()
    assert "mlx5dv_dr_create_domain" in src or "Cannot allocate memory" in src, (
        "Missing operator-visible error string in the rlimit "
        "comment — future maintainers won't connect the dots"
    )


# ─── netgen-upgrade rebuilds tx_worker ───


def test_upgrade_calls_rebuild_tx_worker():
    """The launcher's flag set evolves with each wheel; the
    binary must follow. Otherwise the stale binary errors with
    'unrecognized option'."""
    src = _UPGRADE.read_text()
    assert "_rebuild_tx_worker" in src, (
        "netgen-upgrade doesn't rebuild tx_worker after wheel "
        "install — launcher/binary will drift"
    )


def test_rebuild_tx_worker_targets_canonical_install_path():
    """install_dpdk.sh and v0.5.99's _resolve_tx_worker_bin
    both treat /usr/local/bin/tx_worker as the canonical home.
    The rebuild step must install there."""
    src = _UPGRADE.read_text()
    m = re.search(
        r"def _rebuild_tx_worker[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    assert "/usr/local/bin/tx_worker" in body, (
        "Rebuild doesn't install to the canonical resolver path"
    )


def test_rebuild_tx_worker_skips_on_missing_deps():
    """If libdpdk-dev / meson / ninja aren't there, the upgrade
    must continue (Scapy streams still work). The function
    should warn, not error."""
    src = _UPGRADE.read_text()
    m = re.search(
        r"def _rebuild_tx_worker[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Checks for build deps before invoking meson.
    assert "meson" in body and "ninja" in body
    assert "libdpdk" in body
    # Warns + returns instead of raising.
    assert "logger.warning" in body
    # The early-return pattern: return without raising.
    assert "return" in body


def test_rebuild_tx_worker_called_before_service_restart():
    """If the rebuild ran AFTER the restart, the freshly-
    started server would already have used the stale binary
    for any in-flight launches. Rebuild first, restart second."""
    src = _UPGRADE.read_text()
    rebuild_pos = src.find("_rebuild_tx_worker(")
    # The restart call comes AFTER the rebuild.
    restart_pos = src.find('systemctl", "restart"', rebuild_pos)
    assert rebuild_pos > 0 and restart_pos > rebuild_pos, (
        "Rebuild must come before systemctl restart"
    )
