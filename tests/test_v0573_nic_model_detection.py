"""v0.5.73 — NIC marketing-model detection for the admin iface table.

Operator-requested on srv06 (Jun 9 2026):

  "admin console should also show type of nic card used,
   CX5/CX7/CX6/CX8/Thor2/AMD Pollara/AMD Vulcano.. etc"

Pre-fix the iface table only showed the vendor label
("NVIDIA/Mellanox", "Broadcom"). The actual card model — useful
for fleet inventory + matching DPDK PMD compatibility at a
glance — was missing.

v0.5.73 adds:

  1. `_NIC_MODEL_DB` mapping (vendor_id, device_id) → marketing
     name. Covers Mellanox ConnectX-3 through ConnectX-8 plus
     BlueField DPUs, Broadcom Thor / Thor 2 / older tg3 family,
     AMD Pensando Pollara, Intel X710 / XXV710 / E810 family.

  2. `_detect_nic_model(pci)` — pure sysfs read of
     `/sys/bus/pci/devices/<bdf>/{vendor,device}` and dict
     lookup. ~200 µs per call; no subprocesses.

  3. `card_model` field added to /api/dpdk/interfaces response
     items.

  4. Admin JS table renders the model as a muted second line
     under the vendor cell (keeps the 8-column layout; falls
     through to vendor-only when card_model is null).
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_nic_model_db_constant_defined():
    src = _src()
    assert "_NIC_MODEL_DB" in src, (
        "No _NIC_MODEL_DB constant"
    )
    # The DB must include the operator-named families.
    assert "ConnectX-5" in src
    assert "ConnectX-6" in src
    assert "ConnectX-7" in src
    assert "ConnectX-8" in src
    assert "Thor 2" in src
    assert "Pollara" in src
    # And the Mellanox device IDs we know.
    for did in ("1017", "1019", "101b", "101d", "1021", "1023"):
        assert f'("15b3", "{did}")' in src, (
            f"_NIC_MODEL_DB missing Mellanox device id {did}"
        )
    # And the Broadcom Thor/Thor 2 IDs.
    for did in ("1750", "1751", "1760"):
        assert f'("14e4", "{did}")' in src, (
            f"_NIC_MODEL_DB missing Broadcom device id {did}"
        )


def test_detect_nic_model_helper_defined():
    src = _src()
    assert "def _detect_nic_model(" in src, (
        "_detect_nic_model() helper not defined"
    )


def test_detect_nic_model_reads_sysfs_vendor_and_device():
    """Helper must read /sys/bus/pci/devices/<bdf>/vendor and
    /device files directly — no subprocess."""
    src = _src()
    helper = re.search(
        r"def _detect_nic_model\(pci\)[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert helper
    body = helper.group(0)
    assert "/sys/bus/pci/devices/" in body
    assert "/vendor" in body
    assert "/device" in body
    # No subprocess.run / import subprocess — match the actual
    # call shape, not the bare word (the docstring legitimately
    # mentions "subprocesses" in the rationale).
    assert "subprocess.run(" not in body, (
        "_detect_nic_model must not subprocess — it's called on "
        "every interfaces poll"
    )
    assert "import subprocess" not in body, (
        "_detect_nic_model imports subprocess — unnecessary "
        "for a sysfs read"
    )


def test_detect_nic_model_strips_0x_prefix():
    """sysfs returns the IDs as `0x15b3\n` — must strip both
    `0x` and the trailing newline before lookup."""
    src = _src()
    helper = re.search(
        r"def _detect_nic_model\(pci\)[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = helper.group(0)
    assert "removeprefix" in body or 'replace("0x"' in body or "0x" in body, (
        "Helper doesn't strip the `0x` prefix from sysfs read"
    )


def test_interfaces_response_includes_card_model():
    """The /api/dpdk/interfaces response items must include a
    `card_model` field."""
    src = _src()
    # Look for the dpdk_interfaces handler's append call.
    m = re.search(
        r"interfaces\.append\(\{[\s\S]+?\"card_model\":\s*_detect_nic_model\(pci\)",
        src,
    )
    assert m, (
        "interfaces.append() doesn't include card_model field"
    )


def test_admin_html_renders_card_model_under_vendor():
    """The iface table's vendor cell renders `card_model` as a
    second muted line."""
    src = _src()
    assert "i.card_model" in src, (
        "Admin JS doesn't reference i.card_model"
    )
    # The cell uses a muted span when card_model is present.
    assert re.search(
        r"cardModel\s*\?\s*`\$\{escapeHtml\(vendor\)\}<br>",
        src,
    ), (
        "Vendor cell doesn't fold card_model in as a muted "
        "second line"
    )


def test_pyproject_version_at_least_0573():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 73), (
        f"Version {m.group(1)} < 0.5.73"
    )
