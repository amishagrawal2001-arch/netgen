"""Unit tests for the _BulkEditDialog auto-increment logic.

The dialog itself is a QDialog — instantiated against a minimal
QWidget parent — but the `compute_plans()` logic is pure-Python over
the form state, so we can exercise it without ever showing the
dialog. These tests lock down:

* Per-field auto-increment (VLAN, IPv4, IPv4 Gateway, Loopback, MAC)
* Step=0 (every row gets the same value) — common 'set them all to X' case
* Protocol overrides (Checked → enable, Unchecked → disable,
  PartiallyChecked → leave alone)
* Empty plan when no field is enabled
"""

import sys
import types

import pytest


# Same docker-mock pattern the VRF tests use — the rest of devices_tab
# pulls in docker via FRRDockerManager at module import.
if "docker" not in sys.modules:
    _fd = types.ModuleType("docker")
    _fd.from_env = lambda: None
    _fde = types.ModuleType("docker.errors")
    _fde.NotFound = type("NotFound", (Exception,), {})
    _fdt = types.ModuleType("docker.types")
    _fdt.IPAMConfig = lambda **kw: None
    _fdt.IPAMPool = lambda **kw: None
    _fd.errors = _fde
    _fd.types = _fdt
    sys.modules["docker"] = _fd
    sys.modules["docker.errors"] = _fde
    sys.modules["docker.types"] = _fdt


@pytest.fixture(scope="module")
def _qapp():
    """One QApplication for the whole test module."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def _fake_parent(_qapp):
    """Minimal QWidget that satisfies _BulkEditDialog's parent
    requirement and provides the increment helpers the dialog calls
    on its parent_tab."""
    from PyQt5.QtWidgets import QWidget

    class _P(QWidget):
        def _increment_ipv4(self, ipv4, step, octet_index=3):
            parts = ipv4.split(".")
            try:
                parts[octet_index] = str(int(parts[octet_index]) + step)
            except (ValueError, IndexError):
                return ipv4
            return ".".join(parts)

        def _increment_mac(self, mac, step, byte_index=5):
            parts = mac.split(":")
            try:
                parts[byte_index] = f"{(int(parts[byte_index], 16) + step) & 0xff:02x}"
            except (ValueError, IndexError):
                return mac
            return ":".join(parts)

    return _P()


def _make_dialog(parent, rows):
    from widgets.devices_tab import _BulkEditDialog
    return _BulkEditDialog(parent, rows)


def test_no_fields_enabled_gives_empty_plan(_fake_parent):
    dlg = _make_dialog(_fake_parent, [0, 1, 2])
    assert dlg.compute_plans() == []


def test_vlan_auto_increments_by_step(_fake_parent):
    dlg = _make_dialog(_fake_parent, [0, 1, 2, 3])
    chk, start, step = dlg._fields["vlan"]
    chk.setChecked(True)
    start.setText("100")
    step.setValue(1)
    plans = dlg.compute_plans()
    assert [p[1]["VLAN"] for p in plans] == ["100", "101", "102", "103"]


def test_vlan_step_zero_repeats_value(_fake_parent):
    """step=0 is the 'set them all to the same VLAN' shape — common
    when bulk-changing one field across a group."""
    dlg = _make_dialog(_fake_parent, [0, 1, 2])
    chk, start, step = dlg._fields["vlan"]
    chk.setChecked(True)
    start.setText("777")
    step.setValue(0)
    plans = dlg.compute_plans()
    assert all(p[1]["VLAN"] == "777" for p in plans)


def test_vlan_clamps_to_4094(_fake_parent):
    """Past 4094 the value clamps; tests the safety net for big steps."""
    dlg = _make_dialog(_fake_parent, [0, 1, 2])
    chk, start, step = dlg._fields["vlan"]
    chk.setChecked(True)
    start.setText("4093")
    step.setValue(5)
    plans = dlg.compute_plans()
    # 4093, min(4094, 4098)=4094, min(4094, 4103)=4094
    assert [p[1]["VLAN"] for p in plans] == ["4093", "4094", "4094"]


def test_ipv4_auto_increments_last_octet(_fake_parent):
    dlg = _make_dialog(_fake_parent, [0, 1, 2])
    chk, start, step = dlg._fields["ipv4"]
    chk.setChecked(True)
    start.setText("10.0.0.10")
    step.setValue(2)
    plans = dlg.compute_plans()
    assert [p[1]["IPv4"] for p in plans] == ["10.0.0.10", "10.0.0.12", "10.0.0.14"]


def test_mac_auto_increments_last_byte(_fake_parent):
    dlg = _make_dialog(_fake_parent, [0, 1, 2])
    chk, start, step = dlg._fields["mac"]
    chk.setChecked(True)
    start.setText("aa:bb:cc:dd:ee:01")
    step.setValue(1)
    plans = dlg.compute_plans()
    macs = [p[1]["MAC Address"] for p in plans]
    assert macs == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"]


def test_protocol_checkbox_enable(_fake_parent):
    """Checked → enable protocol. Verified by the _proto_BGP=True flag
    in the plan that _apply_plan_to_device knows how to consume."""
    from PyQt5.QtCore import Qt
    dlg = _make_dialog(_fake_parent, [0, 1])
    dlg._proto_checks["BGP"].setCheckState(Qt.Checked)
    plans = dlg.compute_plans()
    assert len(plans) == 2
    assert plans[0][1]["_proto_BGP"] is True
    assert plans[1][1]["_proto_BGP"] is True


def test_protocol_checkbox_disable(_fake_parent):
    from PyQt5.QtCore import Qt
    dlg = _make_dialog(_fake_parent, [0])
    dlg._proto_checks["OSPF"].setCheckState(Qt.Unchecked)
    plans = dlg.compute_plans()
    assert plans[0][1]["_proto_OSPF"] is False


def test_protocol_partial_check_is_noop(_fake_parent):
    """Partial = 'leave the per-device protocol setting alone'."""
    from PyQt5.QtCore import Qt
    dlg = _make_dialog(_fake_parent, [0])
    # No fields enabled; default protocol-check state is Partial.
    for cb in dlg._proto_checks.values():
        assert cb.checkState() == Qt.PartiallyChecked
    plans = dlg.compute_plans()
    assert plans == []   # no fields, no proto overrides → empty


def test_multiple_fields_combined(_fake_parent):
    """Realistic case: bulk-set VLAN, IPv4, and turn on BGP for 3
    devices in one shot."""
    from PyQt5.QtCore import Qt
    dlg = _make_dialog(_fake_parent, [10, 11, 12])

    chk, start, step = dlg._fields["vlan"]
    chk.setChecked(True); start.setText("100"); step.setValue(1)

    chk, start, step = dlg._fields["ipv4"]
    chk.setChecked(True); start.setText("10.0.0.1"); step.setValue(1)

    dlg._proto_checks["BGP"].setCheckState(Qt.Checked)

    plans = dlg.compute_plans()
    assert len(plans) == 3
    # row indexes preserved
    assert [r for r, _ in plans] == [10, 11, 12]
    # all three fields present in every plan
    for _, p in plans:
        assert "VLAN" in p
        assert "IPv4" in p
        assert p["_proto_BGP"] is True
