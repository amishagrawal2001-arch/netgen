"""Tests for the Type-5 panel + kind-aware Clear (v0.2.67).

Companion to ``tests/test_evpn_inject_dialog.py`` (Type-2 panel). The
refactor that added the tab selector + Type-5 form kept the v0.2.63
test contract intact (every attr/method preserved); this file pins the
*new* surface so the Type-5 path can't silently break in a later edit.

Tested:
  * Type-5 form → payload mapping for the four shape combinations
    (minimal / with gateway / with VRF / both, validation rejections).
  * Type-5 Inject HTTP path posts to ``/api/evpn/type5/inject`` with
    the right body.
  * Active table renders a Kind column; rows store kind in
    ``_row_kinds`` so the per-row Clear can dispatch.
  * Per-row Clear button routes to ``/api/evpn/type5/clear`` for
    type-5 rows and ``/api/evpn/type2/clear`` for type-2 rows.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt5.QtWidgets import QPushButton, QWidget


# ───────────────────────────────────────────────────────── fixtures
@pytest.fixture
def open_dialog(qapp, monkeypatch):
    """Build the dialog with QMessageBox silenced + the auto-refresh
    list call stubbed so the constructor doesn't touch the network."""
    from PyQt5 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

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
    return dlg, mod, parent


# ─────────────────────────────────────────── tab widget exists
def test_two_tabs_with_expected_labels(open_dialog):
    dlg, *_ = open_dialog
    assert dlg.tabs.count() == 2
    assert "Type-2" in dlg.tabs.tabText(0)
    assert "Type-5" in dlg.tabs.tabText(1)
    # Both inject buttons exist on `self` so dispatch can find them.
    assert isinstance(dlg.inject_btn, QPushButton)        # type-2 (legacy name)
    assert isinstance(dlg.inject_btn_t5, QPushButton)     # type-5


# ───────────────────────────────────────── build_inject_payload_t5
def test_t5_payload_minimal(open_dialog):
    dlg, *_ = open_dialog
    dlg.dev_field.setText("eth0")
    dlg.base_prefix_field.setText("10.100.0.0")
    dlg.prefix_len_spin.setValue(24)
    dlg.count_t5_spin.setValue(100)
    dlg.gateway_field.setText("")
    dlg.vrf_table_spin.setValue(0)   # "main" — should be omitted
    body = dlg.build_inject_payload_t5()
    assert body == {"dev": "eth0", "base_prefix": "10.100.0.0",
                    "prefix_len": 24, "count": 100}


def test_t5_payload_includes_gateway_only_when_set(open_dialog):
    dlg, *_ = open_dialog
    dlg.dev_field.setText("eth0")
    dlg.base_prefix_field.setText("10.100.0.0")
    dlg.gateway_field.setText("192.168.1.1")
    body = dlg.build_inject_payload_t5()
    assert body["gateway"] == "192.168.1.1"


def test_t5_payload_includes_vrf_table_only_when_nonzero(open_dialog):
    """vrf_table=0 means the kernel "main" table — encoded as
    omitting the field. Non-zero values pass through."""
    dlg, *_ = open_dialog
    dlg.dev_field.setText("eth0")
    dlg.base_prefix_field.setText("10.100.0.0")
    dlg.vrf_table_spin.setValue(0)
    body = dlg.build_inject_payload_t5()
    assert "vrf_table" not in body

    dlg.vrf_table_spin.setValue(1001)
    body = dlg.build_inject_payload_t5()
    assert body["vrf_table"] == 1001


def test_t5_payload_missing_required_returns_none(open_dialog):
    dlg, *_ = open_dialog
    dlg.dev_field.setText("")
    assert dlg.build_inject_payload_t5() is None
    dlg.dev_field.setText("eth0")
    dlg.base_prefix_field.setText("")
    assert dlg.build_inject_payload_t5() is None


# ──────────────────────────────────────────── _on_inject_t5 HTTP
def _fake_post_response(status, payload):
    return SimpleNamespace(
        status_code=status,
        json=lambda: payload,
        text=str(payload)[:200],
    )


def test_t5_inject_posts_to_type5_url(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    captured = {}
    def _post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json)
        return _fake_post_response(200, {"ok_count": 100,
                                          "failed_count": 0,
                                          "count": 100})
    monkeypatch.setattr(mod.requests, "post", _post)

    dlg.dev_field.setText("eth0")
    dlg.base_prefix_field.setText("10.100.0.0")
    dlg.prefix_len_spin.setValue(24)
    dlg.count_t5_spin.setValue(100)
    dlg._on_inject_t5()

    assert captured["url"] == "http://1.1.1.1/api/evpn/type5/inject"
    assert captured["body"]["dev"] == "eth0"
    assert captured["body"]["base_prefix"] == "10.100.0.0"
    assert captured["body"]["count"] == 100
    # Status message identifies the kind so the user can tell the
    # tabs apart at a glance.
    assert "Type-5" in dlg.status_label.text()


def test_t5_inject_partial_failure_status(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    monkeypatch.setattr(
        mod.requests, "post",
        lambda *a, **k: _fake_post_response(
            200, {"ok_count": 90, "failed_count": 10, "count": 100}),
    )
    dlg._on_inject_t5()
    assert "10 kernel error(s)" in dlg.status_label.text()
    assert "Type-5" in dlg.status_label.text()
    assert "#b45309" in dlg.status_label.styleSheet()   # amber


# ─────────────────────────────────────────── active table — kind col
def test_populate_active_renders_kind_column(open_dialog):
    dlg, *_ = open_dialog
    dlg._populate_active([
        {"inject_id": "aa-1", "kind": "type2", "iface": "vxlan100",
         "l3_iface": "br100", "remote_vtep_ip": "192.0.2.5", "count": 10},
        {"inject_id": "bb-2", "kind": "type5", "iface": "eth0",
         "l3_iface": None, "remote_vtep_ip": None, "count": 100},
    ])
    assert dlg.active_table.rowCount() == 2
    assert dlg.active_table.item(0, dlg.COL_KIND).text() == "Type-2"
    assert dlg.active_table.item(1, dlg.COL_KIND).text() == "Type-5"
    # Cache populated so per-row Clear can dispatch.
    assert dlg._row_kinds == {"aa-1": "type2", "bb-2": "type5"}


def test_populate_active_defaults_kind_to_type2_when_missing(open_dialog):
    """Legacy server (0.2.62, pre-kind-tagging) omits the `kind` field
    entirely. Must default to type-2 so the Clear button still hits the
    Type-2 endpoint and we don't break that flow."""
    dlg, *_ = open_dialog
    dlg._populate_active([
        {"inject_id": "legacy-1", "iface": "vxlan100", "count": 5},
    ])
    assert dlg.active_table.item(0, dlg.COL_KIND).text() == "Type-2"
    assert dlg._row_kinds == {"legacy-1": "type2"}


# ─────────────────────────────────────────── per-row Clear routing
def test_clear_routes_to_type5_endpoint_for_type5_row(open_dialog, monkeypatch):
    """Critical contract — a Clear click on a type-5 row must POST to
    /api/evpn/type5/clear, NOT /type2/clear. Bad routing would leak
    kernel state (the wrong cleaner builds wrong commands)."""
    dlg, mod, _ = open_dialog
    dlg._populate_active([
        {"inject_id": "tt5", "kind": "type5", "iface": "eth0",
         "l3_iface": None, "remote_vtep_ip": None, "count": 50},
    ])
    seen = {}
    def _post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json)
        return _fake_post_response(200, {"ok_count": 50, "failed_count": 0})
    monkeypatch.setattr(mod.requests, "post", _post)
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_post_response(
                            200, {"injections": []}))
    dlg.active_table.cellWidget(0, dlg.COL_CLEAR).click()
    assert seen["url"].endswith("/api/evpn/type5/clear")
    assert seen["body"] == {"inject_id": "tt5"}


def test_clear_routes_to_type2_endpoint_for_type2_row(open_dialog, monkeypatch):
    dlg, mod, _ = open_dialog
    dlg._populate_active([
        {"inject_id": "tt2", "kind": "type2", "iface": "vxlan100",
         "l3_iface": "br100", "remote_vtep_ip": "192.0.2.5", "count": 10},
    ])
    seen = {}
    def _post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json)
        return _fake_post_response(200, {"ok_count": 10, "failed_count": 0})
    monkeypatch.setattr(mod.requests, "post", _post)
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_post_response(
                            200, {"injections": []}))
    dlg.active_table.cellWidget(0, dlg.COL_CLEAR).click()
    assert seen["url"].endswith("/api/evpn/type2/clear")


def test_clear_one_default_kind_is_type2_for_backcompat(open_dialog, monkeypatch):
    """v0.2.63's test calls `dlg._clear_one("abc-123")` directly with
    no row populated. The default must remain type-2 so that test still
    holds (regression-locked here in addition)."""
    dlg, mod, _ = open_dialog
    seen = {}
    def _post(url, json=None, headers=None, timeout=None):
        seen.update(url=url)
        return _fake_post_response(200, {"ok_count": 1, "failed_count": 0})
    monkeypatch.setattr(mod.requests, "post", _post)
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _fake_post_response(
                            200, {"injections": []}))
    # No prior _populate_active → _row_kinds is empty → defaults to type2.
    dlg._clear_one("never-seen-before")
    assert seen["url"].endswith("/api/evpn/type2/clear")
