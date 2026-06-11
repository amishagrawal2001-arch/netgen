"""v0.5.104: NIC counter reader — works for kernel-bound AND
vfio-bound interfaces.

Built after srv06's RX=0 saga: when a NIC is bound to vfio-pci
the kernel netdev disappears, so `ethtool`/`tcpdump`/`ip link`
all return "No such device". The operator had no way to
confirm whether DPDK packets were actually leaving the wire.

This module reads from the right sysfs path based on the
binding state:
- kernel-bound      → /sys/class/net/<iface>/statistics/*
- vfio-bound (mlx5) → /sys/class/infiniband/mlx5_N/ports/1/counters/*
                      (works because IB sysfs is at PCI level)
- vfio-bound (other) → no source available, return shape with
                       source=None + warning

Tests use a real filesystem (tmp_path + monkeypatched module-
internal path constants) rather than mocking each open() call —
the resolver logic IS the value, mocking each I/O hides bugs.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-counters.db")

from utils import nic_counters


# ─── Fixtures: synthetic /sys tree ───


@pytest.fixture
def fake_sys(tmp_path, monkeypatch):
    """Build a synthetic /sys layout for a Mellanox dual-port
    NIC and patch nic_counters to read from it.

    Layout mimics srv06's setup:
        ens2f0np0 → 0000:2b:00.0 → mlx5_2 (vfio-bound; netdev gone)
        ens2f1np1 → 0000:2b:00.1 → mlx5_3 (kernel-bound; mlx5_core)
    """
    sysroot = tmp_path / "sys"
    bdf_a = "0000:2b:00.0"
    bdf_b = "0000:2b:00.1"

    # PCI devices
    pci_a = sysroot / "bus/pci/devices" / bdf_a
    pci_b = sysroot / "bus/pci/devices" / bdf_b
    pci_a.mkdir(parents=True)
    pci_b.mkdir(parents=True)

    # PCI driver symlinks: a → vfio-pci, b → mlx5_core
    (sysroot / "bus/pci/drivers/vfio-pci").mkdir(parents=True)
    (sysroot / "bus/pci/drivers/mlx5_core").mkdir(parents=True)
    (pci_a / "driver").symlink_to(
        sysroot / "bus/pci/drivers/vfio-pci",
    )
    (pci_b / "driver").symlink_to(
        sysroot / "bus/pci/drivers/mlx5_core",
    )

    # InfiniBand devices (survive vfio bind)
    ib_a = pci_a / "infiniband/mlx5_2"
    ib_b = pci_b / "infiniband/mlx5_3"
    ib_a.mkdir(parents=True)
    ib_b.mkdir(parents=True)

    # Also publish them at /sys/class/infiniband/
    (sysroot / "class/infiniband").mkdir(parents=True)
    (sysroot / "class/infiniband/mlx5_2").symlink_to(ib_a)
    (sysroot / "class/infiniband/mlx5_3").symlink_to(ib_b)

    # InfiniBand counters
    def _write_ib_counters(ib_dir: Path, rx_pkts: int, tx_pkts: int):
        ctr = ib_dir / "ports/1/counters"
        ctr.mkdir(parents=True)
        (ctr / "port_rcv_packets").write_text(str(rx_pkts))
        (ctr / "port_xmit_packets").write_text(str(tx_pkts))
        (ctr / "port_rcv_data").write_text(str(rx_pkts * 64))
        (ctr / "port_xmit_data").write_text(str(tx_pkts * 64))
        (ctr / "port_rcv_errors").write_text("0")
        (ctr / "port_xmit_discards").write_text("0")
        # Mellanox extras the operator might want
        (ctr / "symbol_error").write_text("0")
        (ctr / "link_downed").write_text("0")
    _write_ib_counters(ib_a, rx_pkts=0,       tx_pkts=5_000_000)
    _write_ib_counters(ib_b, rx_pkts=4_900_000, tx_pkts=0)

    # Kernel netdev exists ONLY for the kernel-bound iface (b)
    (sysroot / "class/net").mkdir(parents=True)
    netdev_b = sysroot / "class/net/ens2f1np1"
    netdev_b.mkdir()
    (netdev_b / "device").symlink_to(pci_b)
    stats_b = netdev_b / "statistics"
    stats_b.mkdir()
    (stats_b / "rx_packets").write_text("4900000")
    (stats_b / "tx_packets").write_text("0")
    (stats_b / "rx_bytes").write_text("313600000")
    (stats_b / "tx_bytes").write_text("0")
    (stats_b / "rx_errors").write_text("0")
    (stats_b / "tx_errors").write_text("0")
    (stats_b / "rx_dropped").write_text("0")
    (stats_b / "tx_dropped").write_text("0")

    # For ens2f0np0 (vfio-bound), the kernel netdev is GONE —
    # don't create /sys/class/net/ens2f0np0/

    # Patch module-internal path constants. The module reads
    # `/sys/...` literal strings; redirect via path-rewrite
    # wrappers.
    def _rewrite(p: str) -> str:
        if p.startswith("/sys/"):
            return str(sysroot / p[5:])
        return p
    orig_isdir = os.path.isdir
    orig_listdir = os.listdir
    orig_readlink = os.readlink
    orig_open = open

    monkeypatch.setattr(
        nic_counters.os.path, "isdir",
        lambda p: orig_isdir(_rewrite(p)),
    )
    monkeypatch.setattr(
        nic_counters.os, "listdir",
        lambda p: orig_listdir(_rewrite(p)),
    )
    monkeypatch.setattr(
        nic_counters.os, "readlink",
        lambda p: orig_readlink(_rewrite(p)),
    )
    # nic_counters._read_int uses open() directly
    def _open(path, *a, **kw):
        return orig_open(_rewrite(path), *a, **kw)
    monkeypatch.setattr(nic_counters, "open", _open, raising=False)
    # Some indirect imports
    import builtins
    monkeypatch.setattr(builtins, "open", _open)

    return sysroot


# ─── PCI / driver / IB resolution ───


def test_iface_to_pci_bdf_resolves_kernel_bound(fake_sys):
    bdf = nic_counters.iface_to_pci_bdf("ens2f1np1")
    assert bdf == "0000:2b:00.1"


def test_iface_to_pci_bdf_returns_none_for_vfio_bound(fake_sys):
    # ens2f0np0 netdev doesn't exist in /sys/class/net (vfio-bound)
    assert nic_counters.iface_to_pci_bdf("ens2f0np0") is None


def test_iface_to_pci_bdf_returns_none_for_missing_iface(fake_sys):
    assert nic_counters.iface_to_pci_bdf("nonexistent99") is None


def test_pci_bdf_to_infiniband_resolves_for_both_bindings(fake_sys):
    # vfio-bound (PCI tree intact)
    assert nic_counters.pci_bdf_to_infiniband("0000:2b:00.0") == "mlx5_2"
    # kernel-bound
    assert nic_counters.pci_bdf_to_infiniband("0000:2b:00.1") == "mlx5_3"


def test_pci_bdf_binding_distinguishes_vfio_from_kernel(fake_sys):
    assert nic_counters.pci_bdf_binding("0000:2b:00.0") == "vfio-pci"
    assert nic_counters.pci_bdf_binding("0000:2b:00.1") == "kernel"


def test_pci_bdf_binding_returns_unknown_for_missing(fake_sys):
    assert nic_counters.pci_bdf_binding("0000:99:99.0") == "unknown"


# ─── Kernel sysfs reader ───


def test_read_kernel_counters_works_for_kernel_bound(fake_sys):
    kc = nic_counters.read_kernel_counters("ens2f1np1")
    assert kc is not None
    assert kc["rx_packets"] == 4_900_000
    assert kc["rx_bytes"] == 313_600_000
    assert kc["tx_packets"] == 0


def test_read_kernel_counters_returns_none_for_vfio_bound(fake_sys):
    # ens2f0np0 has no /sys/class/net entry → None
    assert nic_counters.read_kernel_counters("ens2f0np0") is None


# ─── Mellanox IB sysfs reader ───


def test_read_mellanox_sysfs_works_for_vfio_bound(fake_sys):
    """The whole point — vfio-bound port's counters via IB."""
    mc = nic_counters.read_mellanox_sysfs_counters("mlx5_2")
    assert mc is not None
    # Mellanox names are mapped to our unified set
    assert mc["tx_packets"] == 5_000_000
    assert mc["rx_packets"] == 0
    # Raw IB dict is preserved for the operator
    assert mc["_raw"]["port_xmit_packets"] == 5_000_000
    assert "symbol_error" in mc["_raw"]
    assert "link_downed" in mc["_raw"]


def test_read_mellanox_sysfs_returns_none_for_missing(fake_sys):
    assert nic_counters.read_mellanox_sysfs_counters("mlx5_99") is None


# ─── Top-level unified read ───


def test_read_counters_kernel_bound_uses_kernel_source(fake_sys):
    snap = nic_counters.read_counters("ens2f1np1")
    assert snap["source"] == "kernel"
    assert snap["binding"] == "kernel"
    assert snap["rx_packets"] == 4_900_000
    assert snap["pci_bdf"] == "0000:2b:00.1"
    assert snap["warnings"] == []


def test_read_counters_vfio_bound_uses_mellanox_sysfs(fake_sys):
    """For vfio-bound iface the kernel path is gone; we must
    fall back to Mellanox sysfs via PCI BDF override."""
    snap = nic_counters.read_counters(
        "ens2f0np0", pci_bdf="0000:2b:00.0",
    )
    assert snap["source"] == "mellanox-sysfs"
    assert snap["binding"] == "vfio-pci"
    assert snap["tx_packets"] == 5_000_000
    assert snap["rx_packets"] == 0
    assert snap["extra"]["infiniband_device"] == "mlx5_2"
    # Warning explains the source switch
    assert any("vfio-bound" in w for w in snap["warnings"])
    assert any("mlx5_2" in w for w in snap["warnings"])


def test_read_counters_vfio_bound_without_explicit_bdf_warns(fake_sys):
    """If the operator doesn't pass pci_bdf and iface is gone
    (vfio-bound), we can't resolve. Return shape with source=
    None and an actionable warning."""
    snap = nic_counters.read_counters("ens2f0np0")
    assert snap["source"] is None
    assert snap["pci_bdf"] is None
    assert any("pci_bdf" in w for w in snap["warnings"])


def test_read_counters_includes_monotonic_timestamp(fake_sys):
    t0 = time.monotonic_ns()
    snap = nic_counters.read_counters("ens2f1np1")
    assert snap["ts_ns"] >= t0


# ─── pps computation ───


def test_compute_pps_basic():
    prev = {"ts_ns": 1_000_000_000, "rx_packets": 100, "tx_packets": 200,
            "rx_bytes": 6400, "tx_bytes": 12800}
    curr = {"ts_ns": 2_000_000_000, "rx_packets": 1100, "tx_packets": 1200,
            "rx_bytes": 70_400, "tx_bytes": 76_800}
    out = nic_counters.compute_pps(prev, curr)
    assert out["elapsed_s"] == 1.0
    assert out["rx_pps"] == 1000.0
    assert out["tx_pps"] == 1000.0
    # bps = bytes_delta * 8 / elapsed
    assert out["rx_bps"] == (64_000 * 8)
    assert out["tx_bps"] == (64_000 * 8)


def test_compute_pps_handles_zero_elapsed():
    """Two snapshots at the same instant → no math, no
    divide-by-zero."""
    s = {"ts_ns": 1_000, "rx_packets": 100}
    out = nic_counters.compute_pps(s, s)
    assert out["rx_pps"] is None
    assert out["elapsed_s"] == 0.0


def test_compute_pps_skips_none_fields():
    """If a source doesn't expose a field (e.g. mellanox-sysfs
    has no rx_dropped equivalent), don't crash; just leave it
    out."""
    prev = {"ts_ns": 0, "rx_packets": 100, "tx_packets": None,
            "rx_bytes": 6400, "tx_bytes": None}
    curr = {"ts_ns": 1_000_000_000, "rx_packets": 200,
            "tx_packets": None, "rx_bytes": 12_800, "tx_bytes": None}
    out = nic_counters.compute_pps(prev, curr)
    assert out["rx_pps"] == 100.0
    assert out["tx_pps"] is None
    assert out["rx_bps"] == 6400 * 8
    assert out["tx_bps"] is None


def test_compute_pps_handles_counter_reset():
    """If iface is reset between samples, counter goes
    backwards. Don't report a negative pps."""
    prev = {"ts_ns": 0, "rx_packets": 1_000_000}
    curr = {"ts_ns": 1_000_000_000, "rx_packets": 50}
    out = nic_counters.compute_pps(prev, curr)
    assert out["rx_pps"] is None


# ─── Module hygiene ───


def test_no_pii_in_returned_shape(fake_sys):
    """The counter dict must be all integers + source metadata.
    No MAC addresses, IPs, or anything else operator-
    identifiable. Safe to log + display in the admin UI."""
    snap = nic_counters.read_counters(
        "ens2f0np0", pci_bdf="0000:2b:00.0",
    )
    # Recursively walk and check we don't see anything that
    # looks like a MAC or IP.
    import json, re
    text = json.dumps(snap, default=str)
    assert not re.search(r"\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", text), (
        "MAC address leaked into counters response"
    )
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text), (
        "IP address leaked into counters response"
    )
