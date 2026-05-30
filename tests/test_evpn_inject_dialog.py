"""Tests for the EVPN Type-2 inject dialog (v0.2.63).

Layers under test:
  * Form → payload mapping for the four input shapes (MAC-only,
    MAC+IP, with VTEP, with L3 iface) plus the validation cases.
  * `_populate_active` table layout — every column carries the right
    value; the Clear button is parameterised by the row's inject_id
    (not the loop variable).
  * `refresh_active` and `_on_inject` HTTP round-trips with a mocked
    requests module, verifying request shape + the status_label
    updates correctly across OK / partial-failure / error responses.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt5.QtWidgets import QPushButton, QWidget


# ───────────────────────────────────────────────────────── fixtures
@pytest.fixture
def open_dialog(qapp, monkeypatch):
    """Build the dialog with QMessageBox silenced so validation
    failures don't pop a modal in headless tests."""
    from PyQt5 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    # Also stub requests.get used by the dialog's __init__ → refresh_active
    # so the constructor doesn't try to talk to a real server.
    import widgets.evpn_inject_dialog as mod
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: SimpleNamespace(
                            status_code=200,
                            json=lambda: {"injections": []},
                        ))

    from widgets.evpn_inject_dialog import EvpnInjectDialog
    parent = QWidget()
    dlg = EvpnInjectDialog(parent, server_url="http://1.1.1.1",
                           default_iface="vxlan100")
    # Hand the test the requests-module hook for follow-up patches.
    return dlg, mod, parent


# ──────────────────────────────────────────── build_inject_payload
def test_payload_mac_only_minimal(open_dialog):
    dlg, *_ = open_dialog
    dlg.iface_field.setText("vxlan100")
    dlg.base_mac_field.setText("aa:bb:cc:00:00:01")
    dlg.count_spin.setValue(50)
    dlg.base_ip_field.setText("")
    dlg.remote_vtep_field.setText("")
    dlg.l3_iface_field.setText("")
    body = dlg.build_inject_payload()
    assert body == {"iface": "vxlan100",
                    "base_mac": "aa:bb:cc:00:00:01",
                    "count": 50}


def test_payload_includes_optional_fields_only_when_set(open_dialog):
    dlg, *_ = open_dialog
    dlg.base_ip_field.setText("10.100.0.1")
    dlg.remote_vtep_field.setText("192.0.2.5")
    dlg.l3_iface_field.setText("br100")
    body = dlg.build_inject_payload()
    assert body["base_ip"] == "10.100.0.1"
    assert body["remote_vtep_ip"] == "192.0.2.5"
    assert body["l3_iface"] == "br100"


def test_payload_missing_required_returns_none(open_dialog):
    dlg, *_ = open_dialog
    dlg.iface_field.setText("")     # invalid
    assert dlg.build_inject_payload() is None
    dlg.iface_field.setText("vxlan100")
    dlg.base_mac_field.setText("")  # also invalid
    assert dlg.build_inject_payload() is None


# ────────────────────────────────────────────── _populate_active
def test_populate_active_renders_one_row_per_item(open_dialog):
    dlg, *_ = open_dialog
    dlg._populate_active([
        {"inject_id": "abcdefgh-1111", "iface": "vxlan100",
         "l3_iface": "br100", "remote_vtep_ip": "192.0.2.5", "count": 250},
        {"inject_id": "12345678-2222", "iface": "vxlan200",
         "l3_iface": None,     "remote_vtep_ip": None,        "count":  10},
    ])
    assert dlg.active_table.rowCount() == 2
    # First row carries every supplied field.
    assert dlg.active_table.item(0, dlg.COL_IFACE).text() == "vxlan100"
    assert dlg.active_table.item(0, dlg.COL_L3).text()    == "br100"
    assert dlg.active_table.item(0, dlg.COL_VTEP).text()  == "192.0.2.5"
    assert dlg.active_table.item(0, dlg.COL_COUNT).text() == "250"
    # Truncated inject_id in the cell, full one in the tooltip.
    assert "…" in dlg.active_table.item(0, dlg.COL_ID).text()
    assert dlg.active_table.item(0, dlg.COL_ID).toolTip() == "abcdefgh-1111"
    # Missing optional fields → em-dash.
    assert dlg.active_table.item(1, dlg.COL_L3).text()   == "—"
    assert dlg.active_table.item(1, dlg.COL_VTEP).text() == "—"
    # Each row has a Clear cellWidget.
    assert isinstance(dlg.active_table.cellWidget(0, dlg.COL_CLEAR), QPushButton)
    assert isinstance(dlg.active_table.cellWidget(1, dlg.COL_CLEAR), QPushButton)


def test_clear_button_binds_correct_inject_id(open_dialog, monkeypatch):
    """Closure-capture regression: the Clear lambda must call
    _clear_one with the row's *own* inject_id, not whatever the loop
    variable happened to end at."""
    dlg, mod, _parent = open_dialog
    dlg._populate_active([
        {"inject_id": "id-A", "iface": "vxlan100", "count": 1},
        {"inject_id": "id-B", "iface": "vxlan200", "count": 1},
        {"inject_id": "id-C", "iface": "vxlan300", "count": 1},
    ])
    seen = []
    monkeypatch.setattr(dlg, "_clear_one", lambda iid: seen.append(iid))
    for r in range(3):
        dlg.active_table.cellWidget(r, dlg.COL_CLEAR).click()
    assert seen == ["id-A", "id-B", "id-C"]


# ────────────────────────────────────────────── _on_inject (HTTP)
def _fake_post_response(status, payload):
    return SimpleNamespace(
        status_code=status,
        json=lambda: payload,
        text=str(payload)[:200],
    )


def test_on_inject_posts_to_correct_url_with_body(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    captured = {}
    def _post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, headers=headers)
        return _fake_post_response(200, {"ok_count": 100, "failed_count": 0,
                                          "count": 50})
    monkeypatch.setattr(mod.requests, "post", _post)

    dlg.iface_field.setText("vxlan100")
    dlg.base_mac_field.setText("aa:bb:cc:00:00:01")
    dlg.count_spin.setValue(50)
    dlg._on_inject()

    assert captured["url"] == "http://1.1.1.1/api/evpn/type2/inject"
    assert captured["body"]["iface"] == "vxlan100"
    assert captured["body"]["count"] == 50


def test_on_inject_success_sets_green_status(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    monkeypatch.setattr(
        mod.requests, "post",
        lambda *a, **k: _fake_post_response(
            200, {"ok_count": 100, "failed_count": 0, "count": 50}),
    )
    dlg._on_inject()
    assert "Injected 50" in dlg.status_label.text()
    assert "100 kernel command(s) OK" in dlg.status_label.text()
    assert "#15803d" in dlg.status_label.styleSheet()   # green


def test_on_inject_partial_failure_sets_amber_status(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    monkeypatch.setattr(
        mod.requests, "post",
        lambda *a, **k: _fake_post_response(
            200, {"ok_count": 90, "failed_count": 10, "count": 50}),
    )
    dlg._on_inject()
    assert "10 kernel error(s)" in dlg.status_label.text()
    assert "#b45309" in dlg.status_label.styleSheet()   # amber


def test_on_inject_server_400_surfaces_error_message(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    monkeypatch.setattr(
        mod.requests, "post",
        lambda *a, **k: _fake_post_response(400, {"error": "bad MAC"}),
    )
    dlg._on_inject()
    assert "HTTP 400" in dlg.status_label.text()
    assert "bad MAC" in dlg.status_label.text()
    assert "#b91c1c" in dlg.status_label.styleSheet()   # red


def test_on_inject_network_exception_caught(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    def _raise(*a, **k):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(mod.requests, "post", _raise)
    dlg._on_inject()
    assert "Request failed" in dlg.status_label.text()
    assert "connection refused" in dlg.status_label.text()
    assert "#b91c1c" in dlg.status_label.styleSheet()


# ───────────────────────────────────────────── refresh_active (HTTP)
def test_refresh_active_populates_table_from_server(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    monkeypatch.setattr(
        mod.requests, "get",
        lambda *a, **k: SimpleNamespace(
            status_code=200,
            json=lambda: {"injections": [
                {"inject_id": "x" * 12, "iface": "vxlan100",
                 "l3_iface": "br100", "remote_vtep_ip": "192.0.2.5",
                 "count": 42},
            ]},
        ),
    )
    dlg.refresh_active()
    assert dlg.active_table.rowCount() == 1
    assert dlg.active_table.item(0, dlg.COL_IFACE).text() == "vxlan100"
    assert dlg.active_table.item(0, dlg.COL_COUNT).text() == "42"


def test_refresh_active_swallows_network_errors(open_dialog, monkeypatch):
    """list endpoint failing shouldn't crash the dialog or leave it
    in an inconsistent state — the user can hit Refresh again."""
    dlg, mod, _ = open_dialog
    def _raise(*a, **k):
        raise ConnectionError("nope")
    monkeypatch.setattr(mod.requests, "get", _raise)
    # Should not raise.
    dlg.refresh_active()


# ────────────────────────────────────────────────── _clear_one
def test_clear_one_posts_inject_id(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    seen = {}
    def _post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json)
        return _fake_post_response(200, {"ok_count": 4, "failed_count": 0})
    monkeypatch.setattr(mod.requests, "post", _post)
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_post_response(
                            200, {"injections": []}))
    dlg._clear_one("abc-123")
    assert seen["url"].endswith("/api/evpn/type2/clear")
    assert seen["body"] == {"inject_id": "abc-123"}
    assert "Cleared inject abc-123" in dlg.status_label.text()


def test_clear_one_partial_failure_amber(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    monkeypatch.setattr(
        mod.requests, "post",
        lambda *a, **k: _fake_post_response(
            200, {"ok_count": 2, "failed_count": 2}),
    )
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_post_response(
                            200, {"injections": []}))
    dlg._clear_one("abc-123")
    assert "with 2 kernel error(s)" in dlg.status_label.text()
    assert "#b45309" in dlg.status_label.styleSheet()
