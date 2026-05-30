"""BFD GUI tests for the L2 emulation tab (v0.2.74).

v0.2.61 wired BFD as the 6th L2 protocol but exposed only a subset of
the RFC 5880 control-packet fields. v0.2.74 closes the gap:

  * ``diag`` (RFC 5880 §4.1, 5-bit diagnostic code) — picked from a
    QComboBox of named codes. Default 0 (No Diagnostic).
  * ``required_min_echo_rx_us`` (RFC 5880 §6.8.1) — µs spinner, default
    0 (echo function disabled).

Both fields are already accepted by ``server/l2_routes.py`` and
``utils/l2_protocols.py:start_bfd``; only the GUI was missing them.
"""

from PyQt5.QtWidgets import QWidget


def _open_dialog(qapp, monkeypatch):
    """Build the L2 config dialog with QMessageBox silenced (so the
    My-Discriminator-zero guard etc. don't block under offscreen Qt)."""
    from PyQt5 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    from widgets.l2_emulation_tab import _L2ConfigDialog
    parent = QWidget()      # hold a ref so the dialog's parent isn't GC'd
    dlg = _L2ConfigDialog(parent, default_iface="ens1f0")
    return parent, dlg


def _select_bfd(dlg):
    """Switch the protocol combo to BFD so _on_accept dispatches into
    the BFD branch."""
    combo = dlg._proto_combo
    for i in range(combo.count()):
        if combo.itemData(i) == "bfd":
            combo.setCurrentIndex(i)
            return
    raise AssertionError("bfd not in protocol combo")


# ──────────────────────────────────────────────── new field presence
def test_bfd_panel_exposes_diag_combo(qapp, monkeypatch):
    """The diagnostic code is a 5-bit RFC 5880 field with 9 defined
    codes — the GUI should let operators pick from named options
    instead of forcing them to remember the integer."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    # Field exists as a QComboBox attribute on the dialog.
    assert hasattr(dlg, "_bfd_diag"), "BFD diagnostic combo is missing"
    # All 9 RFC 5880 §4.1 codes available.
    codes = [dlg._bfd_diag.itemData(i) for i in range(dlg._bfd_diag.count())]
    assert codes == list(range(9))
    # Default to "No Diagnostic" — the safe pick while State = Up.
    assert dlg._bfd_diag.currentData() == 0


def test_bfd_panel_exposes_required_min_echo_rx(qapp, monkeypatch):
    """The echo-RX-interval µs field; 0 disables echo function (the
    common case) so default must be 0."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    assert hasattr(dlg, "_bfd_echo_rx_us"), \
        "Required Min Echo RX spinner is missing"
    assert dlg._bfd_echo_rx_us.value() == 0
    # 0 must be a permitted value (disabled-echo case).
    assert dlg._bfd_echo_rx_us.minimum() == 0


# ──────────────────────────────────────────────── payload round-trip
def test_bfd_payload_carries_diag_and_echo_rx(qapp, monkeypatch):
    """Both new fields must reach the body the dialog hands to the
    REST layer — set non-defaults and confirm round-trip."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    _select_bfd(dlg)
    # Pick a non-default diag (5 = Path Down) and a non-zero echo RX.
    diag_combo = dlg._bfd_diag
    for i in range(diag_combo.count()):
        if diag_combo.itemData(i) == 5:
            diag_combo.setCurrentIndex(i)
            break
    dlg._bfd_echo_rx_us.setValue(250_000)
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None, "dialog did not accept — check guard messages"
    body = p["body"]
    assert body["diag"] == 5
    assert body["required_min_echo_rx_us"] == 250_000
    # Sanity: existing fields still present and unchanged in shape.
    assert body["detect_mult"] == 3
    assert body["desired_min_tx_us"] == 1_000_000
    assert body["required_min_rx_us"] == 1_000_000


def test_bfd_payload_defaults_diag_to_zero(qapp, monkeypatch):
    """Untouched dialog → diag=0 and echo_rx=0 reach the body, not
    missing keys (the server-side allow-list would drop missing keys
    silently, but explicit 0 is the documented default)."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    _select_bfd(dlg)
    dlg._on_accept()
    body = dlg.accepted_payload()["body"]
    assert body["diag"] == 0
    assert body["required_min_echo_rx_us"] == 0
