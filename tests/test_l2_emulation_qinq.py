"""Dialog + sessions-table tests for QinQ in the L2 emulation tab (v0.2.60).

  * `_L2ConfigDialog` round-trips ``outer_vlan_id`` / ``outer_vlan_pcp``
    into the payload only when Outer VLAN ID > 0, AND rejects the
    "outer set, inner unset" case (invalid 802.1ad).
  * `L2EmulationTab._render_sessions` shows ``"<outer> » <inner>"`` in
    the VLAN cell for QinQ sessions, with a tooltip naming the S-/C-VLAN
    semantics, falling back cleanly to the single-tag and untagged
    renders.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget


def _open_dialog(qapp, monkeypatch):
    """Build the L2 config dialog with QMessageBox silenced so tests don't
    block on the QinQ-validation modal."""
    from PyQt5 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    from widgets.l2_emulation_tab import _L2ConfigDialog
    parent = QWidget()      # hold a ref so the dialog's parent isn't GC'd
    dlg = _L2ConfigDialog(parent, default_iface="ens1f0")
    return parent, dlg


# ──────────────────────────────────────────────────── dialog payload
def test_payload_carries_outer_vlan_when_set(qapp, monkeypatch):
    parent, dlg = _open_dialog(qapp, monkeypatch)
    dlg._vlan_id_spin.setValue(100)
    dlg._vlan_pcp_spin.setValue(3)
    dlg._outer_vlan_id_spin.setValue(200)
    dlg._outer_vlan_pcp_spin.setValue(5)
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    body = p["body"]
    assert body["vlan_id"] == 100 and body["vlan_pcp"] == 3
    assert body["outer_vlan_id"] == 200 and body["outer_vlan_pcp"] == 5


def test_payload_omits_outer_when_unset(qapp, monkeypatch):
    """Single-tag case: only the inner vlan_id/pcp should reach the
    factory — no spurious outer_vlan_id in the body."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    dlg._vlan_id_spin.setValue(50)
    dlg._outer_vlan_id_spin.setValue(0)
    dlg._on_accept()
    body = dlg.accepted_payload()["body"]
    assert body["vlan_id"] == 50
    assert "outer_vlan_id" not in body
    assert "outer_vlan_pcp" not in body


def test_payload_omits_both_when_untagged(qapp, monkeypatch):
    parent, dlg = _open_dialog(qapp, monkeypatch)
    # Both spins at 0 → no VLAN keys in body at all.
    dlg._vlan_id_spin.setValue(0)
    dlg._outer_vlan_id_spin.setValue(0)
    dlg._on_accept()
    body = dlg.accepted_payload()["body"]
    for k in ("vlan_id", "vlan_pcp", "outer_vlan_id", "outer_vlan_pcp"):
        assert k not in body


def test_outer_without_inner_is_refused(qapp, monkeypatch):
    """Outer VLAN set + inner VLAN 0 must NOT accept the dialog — that's
    invalid 802.1ad and would be a confusing payload to ship."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    dlg._vlan_id_spin.setValue(0)
    dlg._outer_vlan_id_spin.setValue(200)
    dlg._on_accept()
    # accepted_payload stays None — the dialog returned early on the warning.
    assert dlg.accepted_payload() is None


# ────────────────────────────────────────────── sessions-table render
def test_sessions_table_shows_qinq_outer_inner(qapp):
    from widgets.l2_emulation_tab import L2EmulationTab
    tab = L2EmulationTab(QWidget()); tab._timer.stop()
    tab._render_sessions([{
        "session_id": "sid-q",
        "protocol": "lldp", "iface": "ens1f0",
        "config": {"vlan_id": 100, "vlan_pcp": 3,
                   "outer_vlan_id": 200, "outer_vlan_pcp": 5},
        "running": True,
        "counters": {"frames_sent": 1, "frames_failed": 0,
                     "bytes_sent": 64, "uptime_s": 1.0,
                     "last_error": None},
    }])
    cell = tab._table.item(0, tab.COL_VLAN)
    assert cell.text() == "200 » 100"
    tip = cell.toolTip()
    assert "QinQ" in tip
    assert "S-VLAN 200" in tip and "C-VLAN 100" in tip


def test_sessions_table_single_tag_unchanged(qapp):
    """Regression: when only the inner tag is set, the 0.2.41 render
    (``"<id> · p<pcp>"``) must still apply."""
    from widgets.l2_emulation_tab import L2EmulationTab
    tab = L2EmulationTab(QWidget()); tab._timer.stop()
    tab._render_sessions([{
        "session_id": "sid-1", "protocol": "lldp", "iface": "ens1f0",
        "config": {"vlan_id": 100, "vlan_pcp": 3},
        "running": True,
        "counters": {"frames_sent": 1, "frames_failed": 0,
                     "bytes_sent": 64, "uptime_s": 1.0, "last_error": None},
    }])
    assert tab._table.item(0, tab.COL_VLAN).text() == "100 · p3"


def test_sessions_table_untagged_unchanged(qapp):
    from widgets.l2_emulation_tab import L2EmulationTab
    tab = L2EmulationTab(QWidget()); tab._timer.stop()
    tab._render_sessions([{
        "session_id": "sid-0", "protocol": "lldp", "iface": "ens1f0",
        "config": {"vlan_id": 0},
        "running": True,
        "counters": {"frames_sent": 1, "frames_failed": 0,
                     "bytes_sent": 64, "uptime_s": 1.0, "last_error": None},
    }])
    assert tab._table.item(0, tab.COL_VLAN).text() == "untagged"
