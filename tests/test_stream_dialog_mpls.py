"""Round-trip tests for the SR-MPLS label-stack field in Stream Edit
(v0.2.65 — GUI follow-up to 0.2.64's `utils/mpls.build_mpls_stack`).

Verify:
  * The field exists, has sensible defaults / placeholder.
  * Empty stack → save payload omits `mpls_labels` (legacy single-label
    streams stay bit-identical).
  * Non-empty stack → save payload carries a *parsed list* (not the raw
    string) so the server-side stacker doesn't have to re-parse.
  * Load with `mpls_labels` as a list normalises back to comma-separated
    text in the field (what the user typed).
  * Load with `mpls_labels` as a string passes through unchanged.

The dialog imports many client modules and a few non-existent test
helpers; build it via the same offscreen-QApplication fixture pattern
the other GUI tests use.
"""

import pytest


def _open_stream_dialog(qapp, monkeypatch):
    """Build the AddStreamDialog with QMessageBox silenced. Returns
    (parent, dlg). The dialog's __init__ is complex; if it ever needs
    a server_interfaces / stream_data arg, just pass minimal stubs."""
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QWidget
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        staticmethod(lambda *a, **k: None))
    from widgets.stream_dialog import AddStreamDialog
    parent = QWidget()
    # AddStreamDialog accepts parent + interface + stream_data +
    # server_interfaces (per the edit_selected_stream call site we
    # already studied). Empty stream_data → fresh-dialog flow.
    dlg = AddStreamDialog(
        parent=parent, interface="ens1f0",
        stream_data={}, server_interfaces=[],
    )
    return parent, dlg


# ────────────────────────────────────────────────── widget exists
def test_mpls_labels_field_exists_with_helpful_placeholder(qapp, monkeypatch):
    _, dlg = _open_stream_dialog(qapp, monkeypatch)
    assert hasattr(dlg, "mpls_labels_field")
    # Defaults to empty so legacy single-label flow stays the default.
    assert dlg.mpls_labels_field.text() == ""
    ph = dlg.mpls_labels_field.placeholderText()
    assert "comma-separated" in ph and "label stack" in ph
    # Tooltip mentions SR-MPLS so a curious user can discover the
    # feature without reading docs.
    assert "SR-MPLS" in dlg.mpls_labels_field.toolTip()


# ────────────────────────────────────────────────── save path
def test_save_omits_mpls_labels_when_field_blank(qapp, monkeypatch):
    """Legacy single-label flow: blank stack field MUST NOT add
    `mpls_labels` to the payload — that's how we keep bit-identical
    backward compatibility with pre-0.2.64 streams."""
    _, dlg = _open_stream_dialog(qapp, monkeypatch)
    dlg.mpls_label_field.setText("100")
    dlg.mpls_ttl_field.setText("64")
    dlg.mpls_experimental_field.setText("0")
    dlg.mpls_labels_field.setText("")   # explicit blank

    payload = dlg.get_stream_details()
    mpls = payload.get("protocol_data", {}).get("mpls", {})
    assert mpls.get("mpls_label") == "100"
    assert "mpls_labels" not in mpls


def test_save_parses_comma_separated_into_list(qapp, monkeypatch):
    _, dlg = _open_stream_dialog(qapp, monkeypatch)
    dlg.mpls_labels_field.setText("16000, 16001, 16002")
    payload = dlg.get_stream_details()
    mpls = payload.get("protocol_data", {}).get("mpls", {})
    # Stored as a parsed list (not the raw string) so the server
    # builder doesn't have to re-parse and the JSON round-trips
    # cleanly via REST.
    assert mpls["mpls_labels"] == [16000, 16001, 16002]


def test_save_accepts_hex_labels(qapp, monkeypatch):
    """The pure helper accepts hex; the dialog must pass that through
    so an operator typing `0x10, 0x20, 0x30` gets the right labels."""
    _, dlg = _open_stream_dialog(qapp, monkeypatch)
    dlg.mpls_labels_field.setText("0x10, 16, 0x20")
    payload = dlg.get_stream_details()
    assert payload["protocol_data"]["mpls"]["mpls_labels"] == [16, 16, 32]


def test_save_drops_garbage_stack_without_breaking_payload(qapp, monkeypatch):
    """If the user typed nonsense into the stack field, the save path
    must NOT raise — just omit `mpls_labels`. The legacy single-label
    field still goes through unchanged so the stream isn't bricked."""
    _, dlg = _open_stream_dialog(qapp, monkeypatch)
    dlg.mpls_label_field.setText("100")
    dlg.mpls_labels_field.setText("not-a-number, 200")
    payload = dlg.get_stream_details()
    mpls = payload.get("protocol_data", {}).get("mpls", {})
    assert mpls.get("mpls_label") == "100"
    assert "mpls_labels" not in mpls   # garbage dropped


# ────────────────────────────────────────────────── load path
def test_load_normalises_list_back_to_comma_separated_text(qapp, monkeypatch):
    """Editing an existing stream that already has a list-form stack
    must show the user the comma-separated text they originally typed —
    not a Python `[16000, 16001]` repr."""
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QWidget
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    from widgets.stream_dialog import AddStreamDialog
    parent = QWidget()
    dlg = AddStreamDialog(
        parent=parent, interface="ens1f0",
        stream_data={"protocol_data": {"mpls": {
            "mpls_label": "16",
            "mpls_ttl": "64",
            "mpls_experimental": "0",
            "mpls_labels": [16000, 16001, 16002],
        }}},
        server_interfaces=[],
    )
    assert dlg.mpls_labels_field.text() == "16000, 16001, 16002"


def test_load_passes_string_through_unchanged(qapp, monkeypatch):
    """When the stream stored a comma-separated string (e.g. typed by
    a previous open-and-save in this same dialog) load preserves it."""
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QWidget
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    from widgets.stream_dialog import AddStreamDialog
    parent = QWidget()
    dlg = AddStreamDialog(
        parent=parent, interface="ens1f0",
        stream_data={"protocol_data": {"mpls": {
            "mpls_labels": "100, 200, 300",
        }}},
        server_interfaces=[],
    )
    assert dlg.mpls_labels_field.text() == "100, 200, 300"


def test_load_empty_stack_yields_empty_field(qapp, monkeypatch):
    """Default (no mpls_labels key) → field is empty so the user sees
    'no stack configured'. Legacy single-label streams open this way."""
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QWidget
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    from widgets.stream_dialog import AddStreamDialog
    parent = QWidget()
    dlg = AddStreamDialog(
        parent=parent, interface="ens1f0",
        stream_data={"protocol_data": {"mpls": {
            "mpls_label": "100", "mpls_ttl": "64",
        }}},
        server_interfaces=[],
    )
    assert dlg.mpls_labels_field.text() == ""


# ─────────────────────────────────────────── load → save round-trip
def test_round_trip_preserves_stack(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QWidget
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    from widgets.stream_dialog import AddStreamDialog
    parent = QWidget()
    original = {"protocol_data": {"mpls": {
        "mpls_label": "16",
        "mpls_ttl": "64",
        "mpls_experimental": "0",
        "mpls_labels": [16000, 16001, 16002],
    }}}
    dlg = AddStreamDialog(parent=parent, interface="ens1f0",
                          stream_data=original, server_interfaces=[])
    out = dlg.get_stream_details()
    assert out["protocol_data"]["mpls"]["mpls_labels"] == [16000, 16001, 16002]
