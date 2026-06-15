"""v0.5.147: one-click Loopback buttons in Blast + Topology dialogs.

Operator (after the v0.5.146 filter exposed the real perftest error
on a same-host two-HCA configuration):

    "add an explicit 'Loopback test' button"

Same-host loopback IS supported by perftest — it's the canonical
RDMA smoke test (`ib_send_bw -d mlx5_0` on both sides). v0.5.147
makes it one click instead of "type the same device into two
places".

* Blast dialog: "↔ Use server device for loopback" button copies
  the server-side device combo + IB-port spin onto the client side.
  Only enabled when server_tg_url == client_tg_url (otherwise the
  test wouldn't be on a single host).
* Topology dialog: "↔ Loopback test (same HCA on both sides)"
  button opens `_LoopbackPickerDialog` — a single-pair picker
  (combo of registered TGs, combo of HCAs on the selected TG). On
  accept, BOTH endpoint editors are set to the same single line.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOP = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


# ═════════════════════════════ Blast dialog ════════════════════════════


def test_blast_loopback_button_exists():
    """The Blast dialog grew a "Use server device for loopback"
    button in the device-grid."""
    assert "Use server device for loopback" in SRC_BLAST


def test_blast_loopback_button_only_enabled_for_same_tg():
    """Disabled when server and client TG are different hosts —
    'loopback' there would be a misnomer."""
    assert "self._loopback_btn.setEnabled(same_tg)" in SRC_BLAST


def test_blast_mirror_method_exists():
    """The button wires up to a `_mirror_server_to_client` slot
    that copies the server-side selection onto the client."""
    assert "def _mirror_server_to_client" in SRC_BLAST


def test_blast_mirror_copies_device_and_port():
    """The mirror must copy BOTH device combo AND the IB-port
    spin — port mismatch on dual-port HCAs would still produce
    'Failed to modify QP to RTR' even with the same device."""
    body = _extract_method(SRC_BLAST, "_mirror_server_to_client")
    assert "_client_device_combo.setCurrentIndex" in body, (
        "must update the client device combo"
    )
    assert "_client_port_spin.setValue" in body, (
        "must mirror the IB-port spinbox too"
    )
    assert "_server_port_spin.value()" in body, (
        "...sourcing from the server-side spin"
    )


def test_blast_mirror_short_circuits_when_server_not_probed():
    """If the server combo still shows "(probing…)" with
    userData=None, the button must no-op. Otherwise we'd write
    `None` into the client combo, which is worse than doing
    nothing."""
    body = _extract_method(SRC_BLAST, "_mirror_server_to_client")
    assert "if not srv_dev:" in body or "srv_dev is None" in body
    assert "return" in body


def test_blast_mirror_adds_missing_device_if_client_still_probing():
    """A common race: server combo populated, client combo still
    probing (or running on a slightly slower TG). The mirror should
    still set the right user-data on the client side rather than
    silently dropping the selection."""
    body = _extract_method(SRC_BLAST, "_mirror_server_to_client")
    assert "findData" in body
    # Must add the missing item with the right userData rather
    # than just bailing.
    assert "addItem(srv_dev" in body


# ═════════════════════════════ Topology dialog ════════════════════════


def test_topology_loopback_button_exists():
    """The Topology dialog grew a same-host test button under
    the endpoints group. v0.5.148 renamed it to cover both
    loopback and two-HCA modes."""
    assert (
        "Same-host test (loopback or two HCAs)" in SRC_TOP
        or "Loopback test (same HCA on both sides)" in SRC_TOP
    )


def test_topology_loopback_button_disabled_without_servers():
    """When no TGs are registered, the button must gray out.
    Same UX as the v0.5.143 'Pick from servers…' picker."""
    assert "self._loopback_btn.setEnabled(bool(self._known_servers))" in SRC_TOP


def test_topology_loopback_opens_picker():
    """Click → opens `_LoopbackPickerDialog` via
    `_open_loopback_picker`."""
    assert "def _open_loopback_picker" in SRC_TOP
    assert "self._loopback_btn.clicked.connect(self._open_loopback_picker)" in SRC_TOP


def test_topology_loopback_writes_lines_to_both_editors():
    """v0.5.148: picker now returns a (server_line, client_line)
    tuple. Same-HCA mode → both strings identical; two-HCA mode →
    same URL, different device tokens."""
    body = _extract_method(SRC_TOP, "_open_loopback_picker")
    assert "self._server_edit.setPlainText(srv_line)" in body
    assert "self._client_edit.setPlainText(cli_line)" in body
    assert "picker.selected_lines()" in body


def test_topology_loopback_replaces_rather_than_appends():
    """Unlike the multi-server picker (v0.5.143), loopback is a
    focused smoke test — appending to a multi-endpoint config
    would be confusing. Replace, don't append."""
    body = _extract_method(SRC_TOP, "_open_loopback_picker")
    # Loose check: setPlainText replaces. If we ever switched to
    # appendPlainText / + existing this test would catch it.
    assert "appendPlainText" not in body
    assert "+ existing" not in body


# ═════════════════════════════ _LoopbackPickerDialog ═════════════════════


def test_loopback_picker_class_exists():
    assert "class _LoopbackPickerDialog(" in SRC_TOP


def test_loopback_picker_uses_combos_not_tree():
    """Two combos (TG, HCA), not a tree. The multi-server picker
    uses a tree because it's multi-select; loopback is single-
    select. v0.5.148 renamed `_device_combo` → `_server_device_combo`
    when the two-HCA mode added a sibling `_client_device_combo`."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "_server_combo = QComboBox" in body
    assert "_server_device_combo = QComboBox" in body
    # Definitely NOT a tree.
    assert "QTreeWidget" not in body


def test_loopback_picker_has_two_hca_toggle():
    """v0.5.148: a checkbox lets the operator switch to two-HCA
    same-host mode."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "_two_hca_check" in body
    assert "_client_device_combo" in body


def test_loopback_picker_client_combo_disabled_by_default():
    """Same-HCA loopback is the safer default — the client combo
    must start disabled and only enable when the operator opts in."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    # The setup block disables both the client label + combo.
    assert "self._client_hca_label.setEnabled(False)" in body
    assert "self._client_device_combo.setEnabled(False)" in body


def test_loopback_picker_two_hca_mode_auto_picks_different_device():
    """When the operator flips the toggle, the dialog should pre-
    populate the client side with a DIFFERENT device — typically
    the next HCA index. This is the dual-port case (rocep…f0 →
    rocep…f1) becoming one click."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "_auto_pick_client_device" in body
    # And the toggle calls it.
    assert (
        "self._auto_pick_client_device()" in body
    )


def test_loopback_picker_blocks_same_device_in_two_hca_mode():
    """If the operator manually picks the same HCA on both sides
    while two-HCA mode is on, OK must disable + a hint must
    appear. Otherwise we'd silently emit a same-HCA loopback when
    the operator wanted two HCAs."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "srv_dev == cli_dev" in body
    assert "different Client HCA" in body or "different devices" in body


def test_loopback_picker_reprobes_on_server_change():
    """Switching the server combo must repopulate the HCA combo.
    Otherwise the operator could end up sending a probe to TG A
    but picking a device that exists only on TG B."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert (
        "currentIndexChanged.connect(self._probe_devices)" in body
    ), "server combo must reprobe HCAs when changed"


def test_loopback_picker_disables_ok_until_devices_load():
    """No accidental accept on (TG, '(probing…)') — the OK button
    must be disabled until the device combo holds real entries."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "self._ok_btn.setEnabled(False)" in body
    # And re-enabled in the on-done callback.
    assert "self._ok_btn.setEnabled(True)" in body


def test_loopback_picker_selected_lines_returns_tuple():
    """v0.5.148: selected_lines() returns (server_line,
    client_line). In same-HCA mode both strings are identical; in
    two-HCA mode they share the URL but differ in device token."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "def selected_lines" in body
    # Line format unchanged: <url> <device>.
    assert 'f"{url} {srv_dev}"' in body or 'f"{url} {cli_dev}"' in body


def test_loopback_picker_surfaces_no_hcas_clearly():
    """If /api/rdma/devices comes back empty (RDMA not installed),
    say so + point at the Setup RDMA wizard. A blank combo with
    no explanation is the kind of UX dead-end that wastes an
    operator's afternoon."""
    body = _extract_class(SRC_TOP, "_LoopbackPickerDialog")
    assert "(no HCAs)" in body
    assert "Setup RDMA" in body


# ═════════════════════════════ helpers ═══════════════════════════════════


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"could not locate def {name}(...) in source"
    return m.group(0)


def _extract_class(src: str, name: str) -> str:
    pat = re.compile(
        rf"class {re.escape(name)}\(.*?(?=\nclass \w|\Z)",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"could not locate class {name}(...) in source"
    return m.group(0)
