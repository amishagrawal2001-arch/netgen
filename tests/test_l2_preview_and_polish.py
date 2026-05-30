"""L2 emulation POLISH bundle tests (v0.2.84).

Covers:
  * ``build_preview_frame`` produces non-empty bytes for each of the
    6 supported protocols (the new pure preview helper).
  * Sessions table COL_ACTION column exists + per-row Stop button is
    placed only on running rows.
  * Filter QLineEdit hides non-matching rows.
"""

import pytest
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QPushButton, QWidget


# ─────────────────────────────────────────── build_preview_frame
from utils.l2_protocols import build_preview_frame


def test_preview_unknown_protocol_returns_none():
    assert build_preview_frame("nonesuch", {}) is None
    assert build_preview_frame("", {}) is None


@pytest.mark.parametrize("proto", ["lacp", "lldp", "vrrp", "igmp", "pim", "bfd"])
def test_preview_returns_non_empty_bytes_for_each_protocol(proto):
    """Every supported protocol must yield a real packet — operators
    rely on this in the GUI before clicking Start. Empty / falsy body
    triggers factory defaults, which still produce a valid frame."""
    frame = build_preview_frame(proto, {})
    assert frame is not None
    raw = bytes(frame)
    assert len(raw) > 14, f"{proto} preview frame too short: {len(raw)}"


def test_preview_vrrpv2_with_simple_password_packs_auth_fields():
    """The v0.2.83 auth wiring round-trips through the preview path
    too (otherwise operators would see the wrong wire bytes for a
    config they're about to push)."""
    from scapy.layers.vrrp import VRRP
    frame = build_preview_frame("vrrp", {
        "version": 2,
        "vrid": 1, "priority": 100,
        "virtual_ips": ["192.168.1.254"],
        "src_ip": "10.0.0.1", "src_mac": "00:00:5e:00:01:01",
        "interval_s": 1.0,
        "auth_type": 1, "auth_data": "secret",
    })
    assert frame is not None
    vrrp = frame[VRRP]
    assert vrrp.authtype == 1
    pw = vrrp.auth1.to_bytes(4, "big") + vrrp.auth2.to_bytes(4, "big")
    assert pw == b"secret\x00\x00"


def test_preview_igmpv1_query_dest_is_all_systems():
    """v0.2.82 IGMPv1 path: setting version=1 + type_code=0x11 must
    produce a frame targeted at 224.0.0.1, not the group."""
    from scapy.layers.inet import IP
    frame = build_preview_frame("igmp", {
        "version": 1, "group": "239.1.1.1",
        "src_ip": "10.0.0.10", "src_mac": "00:11:22:33:44:04",
        "type_code": 0x11,
    })
    assert frame is not None
    ip = frame[IP]
    assert ip.dst == "224.0.0.1"


def test_preview_with_qinq_wraps_outer_vlan():
    """A QinQ-tagged config must produce a frame whose ethertype is
    0x88a8 (802.1ad outer S-VLAN); easiest to assert via the byte
    position rather than wading through scapy layer accessors."""
    frame = build_preview_frame("lacp", {
        "system_mac": "00:11:22:33:44:01",
        "vlan_id": 100, "vlan_pcp": 0,
        "outer_vlan_id": 200, "outer_vlan_pcp": 0,
    })
    raw = bytes(frame)
    # Ethertype at bytes 12-13 of the Ethernet header.
    assert raw[12:14] == b"\x88\xa8"


# ───────────────────────────────────────── L2 tab polish wiring
@pytest.fixture
def make_tab(qapp, monkeypatch):
    """Build an L2EmulationTab inside a parent so the QTimer + worker
    don't get GC'd. Stubs the parent's get_server_url to return a
    fixed URL and silences QMessageBox."""
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: 0))
    parents = []
    tabs = []

    def _make():
        from widgets.l2_emulation_tab import L2EmulationTab
        parent = QWidget()
        # Stub parent.get_server_url for the tab's _get_server_url helper.
        parent.get_server_url = lambda silent=False: "http://1.1.1.1"  # noqa: E501
        parents.append(parent)
        tab = L2EmulationTab(parent)
        tabs.append(tab)
        return tab

    yield _make
    for t in tabs:
        try:
            t._timer.stop()
        except Exception:
            pass


def test_sessions_table_has_action_column(make_tab):
    """v0.2.84 #3: a new COL_ACTION column hosts the per-row Stop
    button. It's the 11th column (index 10) and its header is
    blank — the button itself is the affordance."""
    tab = make_tab()
    assert tab.COL_ACTION == 10
    assert tab._table.columnCount() == 11
    # Header for action column is blank.
    hdr = tab._table.horizontalHeaderItem(tab.COL_ACTION)
    if hdr is not None:
        assert hdr.text() == ""


def test_sessions_table_filter_hides_non_matching_rows(make_tab):
    """v0.2.84 #2: the filter QLineEdit substring-matches on
    protocol / iface / session_id and hides rows that don't match."""
    tab = make_tab()
    # Render two synthetic sessions and confirm filter behaviour.
    tab._render_sessions([
        {"session_id": "aaa1", "protocol": "lacp", "iface": "ens1",
         "running": True, "counters": {}, "config": {}},
        {"session_id": "bbb2", "protocol": "bfd", "iface": "ens2",
         "running": True, "counters": {}, "config": {}},
    ])
    assert tab._table.rowCount() == 2
    assert not tab._table.isRowHidden(0)
    assert not tab._table.isRowHidden(1)
    # Filter on "bfd" → only the second row remains visible.
    tab._filter_edit.setText("bfd")
    assert tab._table.isRowHidden(0)
    assert not tab._table.isRowHidden(1)
    # Filter on the session_id prefix of the first row.
    tab._filter_edit.setText("aaa")
    assert not tab._table.isRowHidden(0)
    assert tab._table.isRowHidden(1)
    # Empty → all visible again.
    tab._filter_edit.setText("")
    assert not tab._table.isRowHidden(0)
    assert not tab._table.isRowHidden(1)


def test_per_row_stop_button_only_on_running_rows(make_tab):
    """v0.2.84 #3: running row gets a QPushButton in COL_ACTION;
    stopped row gets a placeholder QTableWidgetItem so sort/filter
    still see a stable cell."""
    tab = make_tab()
    tab._render_sessions([
        {"session_id": "a", "protocol": "lacp", "iface": "ens1",
         "running": True, "counters": {}, "config": {}},
        {"session_id": "b", "protocol": "lldp", "iface": "ens2",
         "running": False, "counters": {}, "config": {}},
    ])
    running_widget = tab._table.cellWidget(0, tab.COL_ACTION)
    stopped_widget = tab._table.cellWidget(1, tab.COL_ACTION)
    assert isinstance(running_widget, QPushButton)
    assert running_widget.text() == "Stop"
    assert stopped_widget is None
    # Stopped row still has a placeholder item.
    assert tab._table.item(1, tab.COL_ACTION) is not None


def test_per_row_stop_button_click_targets_correct_session_id(make_tab, monkeypatch):
    """Closure-capture regression: each row's Stop button must fire
    with its OWN session_id (default-arg lambda pattern; same trap
    as the EVPN Inject dialog's per-row Clear, v0.2.63)."""
    tab = make_tab()
    tab._render_sessions([
        {"session_id": "id-A", "protocol": "lacp", "iface": "ens1",
         "running": True, "counters": {}, "config": {}},
        {"session_id": "id-B", "protocol": "bfd", "iface": "ens2",
         "running": True, "counters": {}, "config": {}},
        {"session_id": "id-C", "protocol": "vrrp", "iface": "ens3",
         "running": True, "counters": {}, "config": {}},
    ])
    seen = []
    monkeypatch.setattr(tab, "_stop_session_by_id",
                        lambda sid: seen.append(sid))
    for r in range(3):
        tab._table.cellWidget(r, tab.COL_ACTION).click()
    assert seen == ["id-A", "id-B", "id-C"]


def test_sessions_table_sorting_intentionally_off(make_tab):
    """v0.2.84 #2: header-click sort was considered but skipped — Qt
    reorders items on sort but NOT the cellWidget-based per-row Stop
    buttons, so a click after sort would fire the wrong session's
    Stop. The filter QLineEdit (textChanged + setRowHidden) covers
    the operator's "find a specific session" need without that
    foot-gun. Pinned so a future refactor that re-enables sort
    without also fixing the cellWidget association fails this test
    loudly."""
    tab = make_tab()
    tab._render_sessions([
        {"session_id": "a", "protocol": "lacp", "iface": "ens1",
         "running": True, "counters": {}, "config": {}},
    ])
    assert tab._table.isSortingEnabled() is False


# ──────────────────────────────────────── last-error cap (v0.2.84 #1)
def test_last_error_cell_caps_at_200(make_tab):
    """120 was the old cap; 200 is the new one. Long error strings
    are truncated to 200 chars in the cell but the FULL text lives
    in the tooltip."""
    tab = make_tab()
    long_err = "X" * 350
    tab._render_sessions([
        {"session_id": "a", "protocol": "lacp", "iface": "ens1",
         "running": True, "config": {},
         "counters": {"last_error": long_err}},
    ])
    item = tab._table.item(0, tab.COL_ERR)
    assert item is not None
    assert len(item.text()) == 200
    assert item.text() == "X" * 200
    # Full text in the tooltip.
    assert item.toolTip() == long_err
