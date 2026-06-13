"""v0.5.112: Destination MAC Auto button — symmetric to the
v0.5.110 Source Auto, with RX iface resolved from the
rx_port_dropdown.

The srv06 saga's final asymmetry: source-MAC fix alone got
~22% delivery because the dst MAC was still synthetic, switch
treated the frame as unknown unicast and flooded/dropped.
Both MACs corrected → ~100% delivery. Operator workflow
needed a second one-click button for dst.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _make_dialog(qapp, **kw):
    from widgets.stream_dialog import AddStreamDialog
    return AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: ens2f0np0",
        stream_data=None,
        server_interfaces=[{
            "tg_id": "0",
            "address": "http://1.1.1.1:5050",
            "online": True,
        }],
        **kw,
    )


def test_dst_auto_button_exists(qapp):
    d = _make_dialog(qapp)
    try:
        assert hasattr(d, "mac_destination_auto_btn")
        assert d.mac_destination_auto_btn.text() == "Auto"
    finally:
        d.deleteLater()


def test_dst_auto_uses_rx_port_dropdown_selection(qapp, monkeypatch):
    """Auto pulls the iface name from rx_port_dropdown's text
    (canonical "TG N - Port: <iface>" format), then hits the
    server's /api/interfaces/<rx_iface>/mac endpoint."""
    d = _make_dialog(qapp)
    try:
        # Seed the RX dropdown with an explicit iface — distinct
        # from TX so we can prove the dst Auto follows RX, not TX.
        d.rx_port_dropdown.clear()
        d.rx_port_dropdown.addItem("TG 0 - Port: ens2f1np1")
        d.rx_port_dropdown.setCurrentIndex(0)

        captured = {}
        class _R:
            ok = True
            def json(self):
                return {"mac_address": "5c:25:73:3f:30:57"}
        def _capture_get(url, *a, **kw):
            captured["url"] = url
            return _R()
        monkeypatch.setattr("requests.get", _capture_get)

        d._on_autopopulate_dst_mac()

        assert "/api/interfaces/ens2f1np1/mac" in captured["url"]
        assert d.mac_destination_address.text() == "5c:25:73:3f:30:57"
    finally:
        d.deleteLater()


def test_dst_auto_falls_back_to_tx_iface_when_same_as_tx(qapp, monkeypatch):
    """rx_port = "Same as TX Port" → dst Auto should fetch the
    TX iface's MAC (single-iface loopback test scenario)."""
    d = _make_dialog(qapp)
    try:
        d.rx_port_dropdown.clear()
        d.rx_port_dropdown.addItem("Same as TX Port")
        d.rx_port_dropdown.setCurrentIndex(0)

        captured = {}
        class _R:
            ok = True
            def json(self):
                return {"mac_address": "aa:bb:cc:dd:ee:ff"}
        monkeypatch.setattr(
            "requests.get",
            lambda url, *a, **kw: (captured.setdefault("url", url), _R())[1],
        )

        d._on_autopopulate_dst_mac()
        # Falls back to TX iface name (ens2f0np0 from the dialog
        # constructor's interface= arg).
        assert "/api/interfaces/ens2f0np0/mac" in captured["url"]
    finally:
        d.deleteLater()


def test_dst_auto_does_not_set_when_server_returns_zero_mac(qapp, monkeypatch):
    """All-zeros MAC is treated as "iface has no real MAC"
    (some virtual ifaces report this). Don't write it into
    the field — that'd just produce the same broken state we
    started from."""
    d = _make_dialog(qapp)
    try:
        d.rx_port_dropdown.clear()
        d.rx_port_dropdown.addItem("TG 0 - Port: vlan100")
        d.rx_port_dropdown.setCurrentIndex(0)

        # Set field to a known non-default so we can detect
        # whether the Auto click overwrote it.
        d.mac_destination_address.setText("aa:bb:cc:dd:ee:ff")

        class _R:
            ok = True
            def json(self): return {"mac_address": "00:00:00:00:00:00"}
        monkeypatch.setattr("requests.get", lambda *a, **kw: _R())

        d._on_autopopulate_dst_mac()
        assert d.mac_destination_address.text() == "aa:bb:cc:dd:ee:ff"
    finally:
        d.deleteLater()


def test_resolve_rx_iface_handles_various_formats(qapp):
    """Sanity check the iface-name parser across the formats
    that show up in different code paths (canonical, legacy,
    bare iface)."""
    d = _make_dialog(qapp)
    try:
        cases = [
            ("TG 0 - Port: ens2f1np1", "ens2f1np1"),
            ("TG 12 - Port: enp24s0f0np0", "enp24s0f0np0"),
            ("ens5", "ens5"),
            ("Same as TX Port", "ens2f0np0"),  # = tx iface
        ]
        for ui_text, expected in cases:
            d.rx_port_dropdown.clear()
            d.rx_port_dropdown.addItem(ui_text)
            d.rx_port_dropdown.setCurrentIndex(0)
            assert d._resolve_rx_iface_name() == expected, (
                f"input {ui_text!r} → {d._resolve_rx_iface_name()!r}, "
                f"expected {expected!r}"
            )
    finally:
        d.deleteLater()
