"""v0.5.110: AddStreamDialog Auto-MAC button + MAC-mismatch
hint chip + modifier-preserving behavior.

These are GUI-side tests under the offscreen Qt platform. We
stub the iface MAC fetch so the dialog never hits a real server.

What's tested:
  • Auto button populates mac_source_address from server fetch
  • Modifier (Source mode = Increment, Step = 1) stays as the
    operator set it after Auto — the scaling is preserved
  • Mismatch chip stays hidden when engine = Scapy
  • Mismatch chip shows when engine = DPDK + src MAC is default
  • Mismatch chip hides as soon as the operator types the
    iface's real MAC
  • rx_engine combo's value round-trips to get_stream_details
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _make_dialog(qapp, **kw):
    """Build an AddStreamDialog with sane test defaults. TG-0
    server stub at http://1.1.1.1 so _resolve_server_base_for_tx
    has a target to match against (the test then patches the
    requests.get call so no real HTTP fires)."""
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: ens2f1np1",
        stream_data=None,
        server_interfaces=[{
            "tg_id": "0",
            "address": "http://1.1.1.1:5050",
            "online": True,
        }],
        **kw,
    )
    return d


def test_autopopulate_button_sets_src_mac_from_server(qapp, monkeypatch):
    """Click Auto → dialog fetches /api/interfaces/<iface>/mac
    and stuffs the result into mac_source_address."""
    d = _make_dialog(qapp)
    try:
        # Stub the HTTP fetch — return a known iface MAC.
        class _R:
            ok = True
            def json(self): return {"mac_address": "ec:0d:9a:11:22:33"}
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **kw: _R(),
        )
        d._on_autopopulate_src_mac()
        assert d.mac_source_address.text() == "ec:0d:9a:11:22:33"
    finally:
        d.deleteLater()


def test_autopopulate_preserves_modifier_settings(qapp, monkeypatch):
    """Operator picked Increment + Count=64 + Step=2. Auto must
    only touch the BASE address, not the modifier knobs."""
    d = _make_dialog(qapp)
    try:
        d.mac_source_mode.setCurrentText("Increment")
        d.mac_source_count.setText("64")
        d.mac_source_step.setText("2")
        class _R:
            ok = True
            def json(self): return {"mac_address": "ec:0d:9a:11:22:33"}
        monkeypatch.setattr("requests.get", lambda *a, **kw: _R())
        d._on_autopopulate_src_mac()
        assert d.mac_source_address.text() == "ec:0d:9a:11:22:33"
        assert d.mac_source_mode.currentText() == "Increment"
        assert d.mac_source_count.text() == "64"
        assert d.mac_source_step.text() == "2"
    finally:
        d.deleteLater()


def test_mismatch_chip_hidden_for_scapy_streams(qapp, monkeypatch):
    """Scapy goes through AF_PACKET which rewrites the src MAC
    to the iface MAC anyway, so a synthetic src MAC there is
    harmless. The chip must stay hidden."""
    d = _make_dialog(qapp)
    try:
        # Force engine = scapy
        idx = d.engine_combo.findData("scapy")
        d.engine_combo.setCurrentIndex(idx)
        # Even a clearly synthetic default MAC shouldn't trigger
        # the chip under scapy.
        d.mac_source_address.setText("02:00:00:00:00:01")
        d._refresh_mac_mismatch_warning()
        assert d._mac_mismatch_label.isHidden()
    finally:
        d.deleteLater()


def test_mismatch_chip_shows_for_dpdk_default_mac(qapp, monkeypatch):
    """DPDK + default 02:00:00:00:00:01 (template default) → chip
    warns the operator about likely switch port-security drops."""
    d = _make_dialog(qapp)
    d.show()
    try:
        # No server reachable — _fetch_iface_mac_from_server
        # returns None. With is_default=True we still show the
        # chip (the "DPDK + default src MAC" generic warning).
        class _R:
            ok = False
        monkeypatch.setattr("requests.get", lambda *a, **kw: _R())
        idx = d.engine_combo.findData("dpdk")
        d.engine_combo.setCurrentIndex(idx)
        d.mac_source_address.setText("02:00:00:00:00:01")
        d._refresh_mac_mismatch_warning()
        assert not d._mac_mismatch_label.isHidden()
        text = d._mac_mismatch_label.text()
        # Generic-default branch wording.
        assert "DPDK" in text and "src MAC" in text
    finally:
        d.deleteLater()


def test_mismatch_chip_hides_when_src_mac_matches_iface(qapp, monkeypatch):
    """DPDK + src MAC == iface MAC → chip hides. This is the
    state after the Auto button succeeds."""
    d = _make_dialog(qapp)
    d.show()
    try:
        class _R:
            ok = True
            def json(self):
                return {"mac_address": "ec:0d:9a:aa:bb:cc"}
        monkeypatch.setattr("requests.get", lambda *a, **kw: _R())
        idx = d.engine_combo.findData("dpdk")
        d.engine_combo.setCurrentIndex(idx)
        d.mac_source_address.setText("ec:0d:9a:aa:bb:cc")
        d._refresh_mac_mismatch_warning()
        assert d._mac_mismatch_label.isHidden()
    finally:
        d.deleteLater()


def test_mismatch_chip_explicit_mismatch_branch(qapp, monkeypatch):
    """DPDK + src MAC differs from iface MAC → strongest signal
    branch fires, names the iface MAC in the warning text."""
    d = _make_dialog(qapp)
    d.show()
    try:
        class _R:
            ok = True
            def json(self):
                return {"mac_address": "ec:0d:9a:aa:bb:cc"}
        monkeypatch.setattr("requests.get", lambda *a, **kw: _R())
        idx = d.engine_combo.findData("dpdk")
        d.engine_combo.setCurrentIndex(idx)
        d.mac_source_address.setText("de:ad:be:ef:00:01")
        d._refresh_mac_mismatch_warning()
        assert not d._mac_mismatch_label.isHidden()
        text = d._mac_mismatch_label.text()
        # Iface MAC must appear in the warning so the operator
        # knows what to type if they don't click Auto.
        assert "ec:0d:9a:aa:bb:cc" in text
        assert "port-security" in text
    finally:
        d.deleteLater()


def test_rx_engine_combo_value_in_get_stream_details(qapp):
    """Regression: rx_engine selection in the combo MUST round-
    trip to the dict get_stream_details returns. Pre-fix the
    operator's 'DPDK (rx_worker)' choice silently degraded to
    Scapy because the serialization path didn't carry it; the
    v0.5.105 wiring still works, this test pins it down so a
    refactor doesn't regress."""
    d = _make_dialog(qapp)
    try:
        rx_idx = d.rx_engine_combo.findData("dpdk")
        assert rx_idx >= 0
        d.rx_engine_combo.setCurrentIndex(rx_idx)
        # Set the TX engine so we don't get caught by an RDMA
        # short-circuit. Need a name + enabled for get_stream_details
        # to return a real dict.
        d.stream_name.setText("test")
        details = d.get_stream_details()
        assert details["rx_engine"] == "dpdk"
    finally:
        d.deleteLater()


def test_autopopulate_does_not_crash_when_server_unreachable(qapp, monkeypatch):
    """Edge case: TG server is unreachable (requests.get raises).
    Auto button must surface a hint, not propagate the exception."""
    d = _make_dialog(qapp)
    d.show()
    try:
        def _boom(*a, **kw):
            raise ConnectionError("server down")
        monkeypatch.setattr("requests.get", _boom)
        # Should not raise.
        d._on_autopopulate_src_mac()
        # And the mismatch chip should now carry a "could not
        # fetch" error message.
        assert not d._mac_mismatch_label.isHidden()
        text = d._mac_mismatch_label.text()
        assert "Could not fetch" in text
    finally:
        d.deleteLater()
