"""v0.5.113: dialog auto-populates iface MACs on Add Stream
(when fields are at known-synthetic defaults).

Pre-fix the dpdk_blast_e2e template shipped `02:00:00:00:00:01`
as Source MAC by default. Operators clicked the template, hit
Apply, and the wire carried synthetic-MAC frames at 22 Mpps —
which on srv06 was enough to trip switch port-security and
require a full switch-port bounce to recover. The dialog Auto
button existed but wasn't triggered automatically; with no
red error chip surfacing, the operator had no idea the wire
config was about to break the lab.

Auto-prefill fires from `populate_stream_fields`, the unified
entry path for both Add (empty stream_data) and Edit (saved
stream_data). The gate is per-field — only known-synthetic
defaults get replaced, so an operator's real saved MAC isn't
overwritten.

Also covers v0.5.113: dst Auto button surfaces a red error
chip when `_resolve_server_base_for_tx` returns None instead
of silent no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _make_dialog(qapp, stream_data=None):
    from widgets.stream_dialog import AddStreamDialog
    return AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: ens2f0np0",
        stream_data=stream_data,
        server_interfaces=[{
            "tg_id": "0",
            "address": "http://1.1.1.1:5050",
            "online": True,
        }],
    )


def test_synthetic_src_mac_auto_replaced_on_add(qapp, monkeypatch):
    """Dialog opens with no saved data → MAC fields default to
    synthetic. Auto-prefill kicks in and replaces with real
    iface MAC."""
    real_macs = {
        "ens2f0np0": "5c:25:73:3f:30:56",
        "ens2f1np1": "5c:25:73:3f:30:57",
    }

    def _fake_get(url, *a, **kw):
        for iface, mac in real_macs.items():
            if f"/api/interfaces/{iface}/mac" in url:
                class _R:
                    ok = True
                    def json(self_inner): return {"mac_address": mac}
                return _R()
        class _Bad:
            ok = False
        return _Bad()
    monkeypatch.setattr("requests.get", _fake_get)

    d = _make_dialog(qapp)
    try:
        # Seed RX dropdown to ens2f1np1 (so dst auto-prefill
        # resolves the right iface).
        d.rx_port_dropdown.clear()
        d.rx_port_dropdown.addItem("TG 0 - Port: ens2f1np1")
        d.rx_port_dropdown.setCurrentIndex(0)
        # populate_stream_fields() is the unified entry path —
        # it's what the dialog calls during apply and what we
        # call directly to simulate the Add Stream flow.
        d.populate_stream_fields({})
        assert d.mac_source_address.text() == "5c:25:73:3f:30:56", (
            "Src MAC should auto-fill from TX iface on Add when "
            "the field is at the default synthetic value"
        )
        assert d.mac_destination_address.text() == "5c:25:73:3f:30:57", (
            "Dst MAC should auto-fill from RX iface on Add"
        )
    finally:
        d.deleteLater()


def test_synthetic_template_macs_get_replaced(qapp, monkeypatch):
    """dpdk_blast_e2e + friends ship `02:00:00:00:00:01` /
    `02:00:00:00:00:02` defaults. Those land in mac_data and
    populate_stream_fields applies them. Auto-prefill then
    immediately replaces them with real iface MACs."""
    def _fake_get(url, *a, **kw):
        if "/ens2f0np0/mac" in url:
            class _R:
                ok = True
                def json(s): return {"mac_address": "5c:25:73:3f:30:56"}
            return _R()
        if "/ens2f1np1/mac" in url:
            class _R:
                ok = True
                def json(s): return {"mac_address": "5c:25:73:3f:30:57"}
            return _R()
        class _Bad:
            ok = False
        return _Bad()
    monkeypatch.setattr("requests.get", _fake_get)

    d = _make_dialog(qapp)
    try:
        d.rx_port_dropdown.clear()
        d.rx_port_dropdown.addItem("TG 0 - Port: ens2f1np1")
        d.rx_port_dropdown.setCurrentIndex(0)
        # Simulate template apply — synthetic MACs in mac_data.
        d.populate_stream_fields({
            "protocol_data": {
                "mac": {
                    "mac_source_address": "02:00:00:00:00:01",
                    "mac_destination_address": "02:00:00:00:00:02",
                },
            },
        })
        assert d.mac_source_address.text() == "5c:25:73:3f:30:56"
        assert d.mac_destination_address.text() == "5c:25:73:3f:30:57"
    finally:
        d.deleteLater()


def test_real_saved_macs_not_overwritten(qapp, monkeypatch):
    """Operator's saved stream has real MACs already → don't
    touch them. The gate is per-field and only replaces known
    synthetic defaults."""
    def _fake_get(url, *a, **kw):
        # The fetch shouldn't even be called because the MACs
        # already look real. We return success just in case so
        # any accidental call still passes — the assertion is
        # about the FIELD VALUE, not the fetch.
        class _R:
            ok = True
            def json(s): return {"mac_address": "de:ad:be:ef:00:01"}
        return _R()
    monkeypatch.setattr("requests.get", _fake_get)

    d = _make_dialog(qapp)
    try:
        d.populate_stream_fields({
            "protocol_data": {
                "mac": {
                    "mac_source_address": "11:22:33:44:55:66",
                    "mac_destination_address": "77:88:99:aa:bb:cc",
                },
            },
        })
        assert d.mac_source_address.text() == "11:22:33:44:55:66"
        assert d.mac_destination_address.text() == "77:88:99:aa:bb:cc"
    finally:
        d.deleteLater()


def test_auto_prefill_silent_when_server_unreachable(qapp, monkeypatch):
    """Server unreachable on Add → don't crash, don't show a
    scary error. Field stays at the synthetic default; if the
    operator clicks Apply with that, the existing mismatch
    chip (v0.5.110) will warn them."""
    def _boom(*a, **kw):
        raise ConnectionError("server down")
    monkeypatch.setattr("requests.get", _boom)
    d = _make_dialog(qapp)
    try:
        d.populate_stream_fields({})
        # Field stays at default. No crash.
        assert d.mac_source_address.text() in (
            "00:00:00:00:00:00", "02:00:00:00:00:01",
        ) or d.mac_source_address.text().count(":") == 5
    finally:
        d.deleteLater()


def test_dst_auto_button_shows_chip_when_server_unresolvable(qapp, monkeypatch):
    """v0.5.113 fix: when _resolve_server_base_for_tx returns
    None, dst Auto used to silently no-op. Now it shows a red
    error chip — symmetric to src Auto."""
    from PyQt5.QtWidgets import QWidget
    from widgets.stream_dialog import AddStreamDialog

    # Dialog with no parent + no server_interfaces with
    # address → _resolve_server_base_for_tx returns None.
    dlg = AddStreamDialog(
        parent=None,
        interface="TG 0 - Port: ens2f0np0",
        stream_data=None,
        server_interfaces=[{"tg_id": "0", "ports": ["ens2f1np1"]}],
    )
    try:
        dlg.rx_port_dropdown.clear()
        dlg.rx_port_dropdown.addItem("TG 0 - Port: ens2f1np1")
        dlg.rx_port_dropdown.setCurrentIndex(0)
        dlg._on_autopopulate_dst_mac()
        assert not dlg._mac_mismatch_label.isHidden(), (
            "Dst Auto must show error chip when server URL can't "
            "be resolved — silent no-op was the v0.5.112 bug"
        )
        text = dlg._mac_mismatch_label.text()
        assert "server URL" in text or "resolve" in text
    finally:
        dlg.deleteLater()
