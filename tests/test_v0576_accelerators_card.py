"""v0.5.76 — DPDK accelerators card + iface table filter.

Operator on srv06 (Jun 9 2026):

  "check admin console, there are intel PCI which are not
   associated with interface, what are these, it also shows
   state DPDK accelerator (kernel ioatdma)"

Those are Intel I/OAT Crystal Beach DMA engines built into
Xeon CPUs (8 per socket × 2 sockets = 16). They have kernel
driver `ioatdma`, PCI class 0x0880 (Other system peripheral).
DPDK can use them via its dmadev API — but they're not
network interfaces, and bind isn't required.

Pre-fix the dpdk-devbind.py parser was tracking section state
based on header-string detection ('Network devices using kernel
driver', 'Other Network devices', etc.). When dpdk-devbind
emits 'Other DMA devices using kernel driver', that header
isn't recognised so the parser kept current_section at the
previous network value → the 16 ioatdma lines came through
as network devices.

v0.5.76 ships:

  1. `_detect_pci_class(pci)` — pure sysfs read of
     `/sys/bus/pci/devices/<bdf>/class`. ~20 µs per call.

  2. `_DPDK_ACCELERATOR_CLASSES` — driver-to-label map for
     ioatdma / idxd / qat / ntb_hw_intel.

  3. `/api/dpdk/interfaces` filters out any device whose PCI
     base class isn't 0x02xx (Network controller). The
     section-state-leak symptom can't bite anymore.

  4. `/api/dpdk/accelerators` — new GET endpoint that lists
     non-NIC DPDK-capable PCI devices with friendly labels +
     hints + per-label counts.

  5. Admin console adds a "DPDK Accelerators" card above
     Network Interfaces with the count summary and a
     collapsible per-device table. Hidden when no
     accelerators are present (most lab boxes).
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_detect_pci_class_helper_defined():
    src = _src()
    assert "def _detect_pci_class(" in src, (
        "_detect_pci_class() helper not defined"
    )
    # Must read sysfs, no subprocess.
    m = re.search(
        r"def _detect_pci_class\([\s\S]+?(?=\n\n\n|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "/sys/bus/pci/devices/" in body
    assert "/class" in body
    assert "subprocess.run(" not in body, (
        "_detect_pci_class must not subprocess — called per device"
    )


def test_accelerator_class_db_includes_ioatdma_and_friends():
    src = _src()
    assert "_DPDK_ACCELERATOR_CLASSES" in src
    # Operator-named driver MUST be there.
    assert '"ioatdma"' in src
    # Plus the other common DPDK accelerator drivers.
    for drv in ("idxd", "qat", "ntb_hw_intel"):
        assert f'"{drv}"' in src, (
            f"Accelerator DB missing {drv}"
        )
    # And each has both label + hint (2-tuple).
    assert 'Intel I/OAT DMA' in src
    assert 'Intel DSA' in src


def test_iface_response_filters_non_network_pci_class():
    """The parser must skip devices whose PCI base class isn't
    0x02. Pre-fix 16 ioatdma rows leaked into the iface table
    when dpdk-devbind's 'Other DMA devices' header confused the
    section-state tracker."""
    src = _src()
    # Anchor on the unique v0.5.73 card_model field — the DPDK
    # iface block is the only append site that has it.
    idx = src.find('"card_model": _detect_nic_model(pci)')
    assert idx > 0, (
        "Can't find the DPDK iface append site (card_model anchor)"
    )
    pre = src[max(0, idx - 3500):idx]
    assert "_detect_pci_class(pci)" in pre, (
        "DPDK iface append doesn't call _detect_pci_class before "
        "the append"
    )
    assert re.search(
        r"_cls\s+and\s+not\s+_is_network|startswith\(['\"]02['\"]\)",
        pre,
    ), "No PCI base-class 0x02 filter before the DPDK iface append"


def test_iface_entry_includes_pci_class_field():
    src = _src()
    idx = src.find('"card_model": _detect_nic_model(pci)')
    assert idx > 0
    # The pci_class field is added a couple lines after card_model.
    post = src[idx:idx + 500]
    assert '"pci_class"' in post, (
        "DPDK iface entry doesn't surface pci_class field"
    )


def test_accelerators_endpoint_defined():
    src = _src()
    assert (
        '@app.route("/api/dpdk/accelerators"' in src
        and "def dpdk_accelerators(" in src
    ), "/api/dpdk/accelerators endpoint not registered"


def test_accelerators_endpoint_scans_sysfs_directly():
    """The accelerators endpoint should read /sys/bus/pci/devices
    directly — avoids the slow dpdk-devbind.py round-trip."""
    src = _src()
    m = re.search(
        r"def dpdk_accelerators\(\)[\s\S]+?return\s+jsonify\(",
        src,
    )
    assert m
    body = m.group(0)
    assert "/sys/bus/pci/devices" in body, (
        "accelerators endpoint doesn't scan sysfs"
    )
    assert "os.readlink" in body, (
        "accelerators endpoint doesn't resolve driver via sysfs symlink"
    )


def test_accelerators_endpoint_response_shape():
    """Response must include both accelerators list AND
    counts_by_label aggregate so the admin card can render
    `16 × Intel I/OAT DMA` without rolling its own counter."""
    src = _src()
    m = re.search(
        r"def dpdk_accelerators\(\)[\s\S]+?return\s+jsonify\([\s\S]+?\)",
        src,
    )
    body = m.group(0)
    assert '"accelerators"' in body
    assert '"counts_by_label"' in body
    # Each accelerator entry has label + hint + driver + class.
    assert '"label":' in body and '"hint":' in body
    assert '"driver":' in body and '"class":' in body


def test_admin_html_includes_accelerators_card():
    src = _src()
    assert '<h2 style="margin: 0 0 4px;">DPDK Accelerators</h2>' in src \
        or "<h2>DPDK Accelerators</h2>" in src \
        or 'DPDK Accelerators' in src, (
        "Admin HTML missing DPDK Accelerators card heading"
    )
    assert 'id="card-accelerators"' in src
    assert 'id="accel-list"' in src


def test_admin_js_renders_accelerators_card():
    src = _src()
    # The render function must exist and call the new endpoint.
    assert "async function refreshAccelerators(" in src
    assert "/api/dpdk/accelerators" in src
    # And it must show counts_by_label as an aggregate.
    assert "counts_by_label" in src or "counts" in src
    # Initial load wires it up.
    assert "refreshAccelerators();" in src


def test_admin_js_hides_card_when_no_accelerators():
    """v0.5.80 changed the behavior: on empty list, the card now
    SHOWS with an informational message (so AMD users know it's
    not just missing). The card is still hidden on a fetch error
    so we don't render anything when the endpoint is down."""
    src = _src()
    idx = src.find("async function refreshAccelerators(")
    body = src[idx:idx + 3000]
    # The fetch-error path still hides the card.
    assert "card.hidden = true" in body, (
        "Accelerators card not hidden on fetch error"
    )
    # And the empty-list path now shows an informational message
    # (v0.5.80 audit LOW #13).
    assert "No DPDK accelerators detected" in body, (
        "Empty-state doesn't surface a message"
    )


def test_pyproject_version_at_least_0576():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 76), (
        f"Version {m.group(1)} < 0.5.76"
    )
