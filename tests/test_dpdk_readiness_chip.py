"""DpdkReadinessChip widget + classify_dpdk_status (v0.2.76).

The pure-function ``classify_dpdk_status`` covers every state-transition
combination an operator might land in; the Qt smoke-tests confirm the
chip widget actually constructs, paints, and refreshes on demand.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt5.QtWidgets import QMainWindow, QWidget


# ───────────────────────────────────────── classify_dpdk_status (pure)
from widgets.dpdk_readiness_chip import classify_dpdk_status


def _ready_payload():
    """All five subsystems reporting OK — the green case."""
    return {
        "dpdk_installed": True,
        "tx_worker_exists": True,
        "hugepages_configured": True,
        "hugepages_available": 4096,
        "hugepage_size": "2MB",
        "iommu_enabled": True,
        "iommu_details": "Intel IOMMU enabled (passthrough mode)",
        "vfio_pci_loaded": True,
    }


def test_classify_green_when_all_subsystems_up():
    state, headline, tip = classify_dpdk_status(_ready_payload())
    assert state == "green"
    assert "ready" in headline.lower()
    # Every subsystem listed in the tip — operator can sanity-check
    # what we counted.
    for k in ("DPDK libraries", "tx_worker", "Hugepages", "IOMMU", "vfio-pci"):
        assert k in tip


def test_classify_red_when_tx_worker_missing():
    p = _ready_payload()
    p["tx_worker_exists"] = False
    state, headline, tip = classify_dpdk_status(p)
    assert state == "red"
    assert "tx_worker" in headline
    assert "fall back to Scapy" in tip


def test_classify_red_when_libdpdk_missing():
    p = _ready_payload()
    p["dpdk_installed"] = False
    state, headline, _ = classify_dpdk_status(p)
    assert state == "red"
    assert "libdpdk" in headline


def test_classify_red_when_both_hard_subsystems_missing():
    p = _ready_payload()
    p["dpdk_installed"] = False
    p["tx_worker_exists"] = False
    state, headline, _ = classify_dpdk_status(p)
    assert state == "red"
    # Both reasons in headline so the operator doesn't fix one and
    # come back to discover the other.
    assert "libdpdk" in headline and "tx_worker" in headline


def test_classify_amber_when_hugepages_missing():
    """Hard subsystems present but hugepages aren't allocated — call
    it degraded (mlx5 NICs work without hugepages)."""
    p = _ready_payload()
    p["hugepages_configured"] = False
    state, headline, tip = classify_dpdk_status(p)
    assert state == "amber"
    assert "degraded" in headline.lower()
    # Tooltip explains the mlx5 escape hatch so the operator isn't
    # tricked into thinking it's broken on Mellanox hardware.
    assert "mlx5" in tip or "Mellanox" in tip


def test_classify_amber_when_iommu_off():
    p = _ready_payload()
    p["iommu_enabled"] = False
    state, _, _ = classify_dpdk_status(p)
    assert state == "amber"


def test_classify_amber_when_vfio_unloaded():
    p = _ready_payload()
    p["vfio_pci_loaded"] = False
    state, _, _ = classify_dpdk_status(p)
    assert state == "amber"


def test_classify_handles_empty_payload():
    """No keys → everything reads False → red (correct: nothing
    works), no exception."""
    state, headline, _ = classify_dpdk_status({})
    assert state == "red"


def test_classify_hugepage_count_appears_in_tooltip():
    """The hugepage-count detail (so the operator can see whether 4
    pages or 4096 are allocated) lives in the tooltip."""
    p = _ready_payload()
    p["hugepages_available"] = 1024
    p["hugepage_size"] = "2MB"
    _, _, tip = classify_dpdk_status(p)
    assert "1024" in tip
    assert "2MB" in tip


def test_classify_surfaces_dpdk_version_in_tooltip():
    """v0.2.77: ABI version surfaced so operators can catch the
    "rebuild tx_worker after upgrading libdpdk" class of crashes."""
    p = _ready_payload()
    p["dpdk_version"] = "23.11.0"
    _, _, tip = classify_dpdk_status(p)
    assert "23.11.0" in tip


def test_classify_surfaces_tx_worker_build_date_in_tooltip():
    p = _ready_payload()
    p["tx_worker_built"] = "2026-05-15 14:32"
    _, _, tip = classify_dpdk_status(p)
    assert "2026-05-15" in tip


def test_classify_omits_versions_when_payload_lacks_them():
    """Pre-0.2.77 servers don't return the new fields — tooltip
    should still render cleanly (no 'None' leakage)."""
    p = _ready_payload()
    # Intentionally NOT setting dpdk_version / tx_worker_built.
    _, _, tip = classify_dpdk_status(p)
    assert "None" not in tip
    assert "DPDK libraries: ok" in tip
    assert "tx_worker binary: ok" in tip


# ───────────────────────────────────────────────── chip widget (Qt)
@pytest.fixture
def make_chip(qapp, monkeypatch):
    """Build a chip with poll disabled so tests drive refresh()
    explicitly, with a settable mock for requests.get."""
    parents: list[QWidget] = []
    chips: list = []

    def _make(server_url="http://1.1.1.1", payload=None):
        from widgets import dpdk_readiness_chip as mod
        if payload is not None:
            monkeypatch.setattr(
                mod.requests, "get",
                lambda *a, **k: SimpleNamespace(
                    status_code=200, json=lambda: payload, text=""
                ),
            )
        else:
            # The 300 ms first-refresh singleShot would hit the network
            # otherwise; raise loudly so a missing payload arg surfaces.
            monkeypatch.setattr(
                mod.requests, "get",
                lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("no payload set"))
            )
        provider = (lambda: server_url) if server_url else (lambda: None)
        parent = QWidget()
        parents.append(parent)
        chip = mod.DpdkReadinessChip(provider, parent=parent,
                                     poll_interval_ms=0)
        chips.append(chip)
        return chip, mod

    yield _make
    for c in chips:
        try:
            c.stop()
        except Exception:
            pass
    chips.clear()
    parents.clear()


def test_chip_starts_in_gray_state_when_no_server(qapp, monkeypatch):
    from widgets import dpdk_readiness_chip as mod
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("should not poll")))
    parent = QWidget()  # noqa: F841
    chip = mod.DpdkReadinessChip(lambda: None, parent=parent,
                                 poll_interval_ms=0)
    # Constructor immediately paints gray "—" before any refresh.
    assert chip.state() == "gray"
    chip.refresh()  # no-op: no URL
    assert chip.state() == "gray"


def test_chip_renders_green_on_ready_payload(make_chip):
    chip, _ = make_chip(payload={
        "dpdk_installed": True, "tx_worker_exists": True,
        "hugepages_configured": True, "hugepages_available": 4096,
        "hugepage_size": "2MB", "iommu_enabled": True,
        "iommu_details": "Intel IOMMU enabled", "vfio_pci_loaded": True,
    })
    chip.refresh()
    assert chip.state() == "green"
    assert "ready" in chip.text().lower()


def test_chip_renders_red_when_tx_worker_missing(make_chip):
    chip, _ = make_chip(payload={
        "dpdk_installed": True, "tx_worker_exists": False,
        "hugepages_configured": True, "iommu_enabled": True,
        "vfio_pci_loaded": True,
    })
    chip.refresh()
    assert chip.state() == "red"


def test_chip_silent_on_http_failure(make_chip, monkeypatch):
    """Pills hold their previous state on a flaky link, like the
    preflight bar — no modal pop-up."""
    chip, mod = make_chip(payload={
        "dpdk_installed": True, "tx_worker_exists": True,
        "hugepages_configured": True, "iommu_enabled": True,
        "vfio_pci_loaded": True,
    })
    chip.refresh()
    assert chip.state() == "green"
    # Flip to a raising mock and refresh again — state stays green.
    def boom(*a, **k):
        raise ConnectionError("server unreachable")
    monkeypatch.setattr(mod.requests, "get", boom)
    chip.refresh()  # should not raise
    assert chip.state() == "green"


def test_chip_silent_on_non_200(make_chip, monkeypatch):
    chip, mod = make_chip(payload={
        "dpdk_installed": True, "tx_worker_exists": True,
        "hugepages_configured": False, "iommu_enabled": False,
        "vfio_pci_loaded": False,
    })
    chip.refresh()
    assert chip.state() == "amber"
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: SimpleNamespace(
                            status_code=503, json=lambda: {}, text=""))
    chip.refresh()
    assert chip.state() == "amber"  # unchanged
