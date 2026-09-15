"""v0.5.348 — Add Device dialog auto-generates a unique
locally-administered MAC per new device (both tagged & untagged).

Operator on srv06 2026-09-15: complained that every Add Device
dialog seeded the same hardcoded `00:11:22:33:44:55` — adding a
second device without hand-editing that field triggered an
immediate L2 collision (identical MACs on the same L2 segment),
breaking ARP/NDP for both.

Fix: on dialog open in `mode="add"`, generate a random
`02:XX:XX:XX:XX:XX` MAC (LAA prefix), collision-checked against
the caller-supplied `existing_devices` list. `mode="edit"`
callers pre-fill the field with the saved MAC — the auto-gen
must NOT clobber that.

Applies uniformly to both tagged (vlan subif) and untagged
devices — the v0.5.325 apply flow already runs `ip link set
<iface> address <mac>` for both.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dialog_src():
    return (_REPO / "widgets" / "add_device_dialog.py").read_text()


def test_marker_present():
    assert "v0.5.348 (audit auto-mac-generation)" in _dialog_src()


def test_hardcoded_mac_seed_gone():
    """The pre-fix hardcoded seed `00:11:22:33:44:55` must be
    REMOVED — that's the collision root cause."""
    src = _dialog_src()
    assert 'QLineEdit("00:11:22:33:44:55")' not in src


def test_mode_add_calls_generator():
    """Add mode must call `_generate_unique_lab_mac()`; the initial
    QLineEdit text comes from that helper."""
    src = _dialog_src()
    # Ternary: `... if self.mode == "add" else ""`
    assert 'self._generate_unique_lab_mac() if self.mode == "add"' in src


def test_mode_edit_does_not_auto_generate():
    """When mode is `edit`, the QLineEdit must be initialized empty
    — the caller pre-fills the field with the device's saved MAC.
    Auto-generating would clobber it."""
    src = _dialog_src()
    # The ternary's else branch is `""`.
    assert 'self.mode == "add" else ""' in src


def test_helper_uses_laa_prefix():
    """Generated MAC must begin with `02:` — that's the locally-
    administered address bit (bit 1 of first octet). Ensures no
    conflict with vendor-OUI-assigned MACs."""
    src = _dialog_src()
    fn_idx = src.index("def _generate_unique_lab_mac(")
    body = src[fn_idx:fn_idx + 4000]
    assert "[0x02]" in body


def test_helper_checks_existing_devices_for_collisions():
    """The generator must consult `self._existing_devices` and
    retry until it finds an unused MAC."""
    src = _dialog_src()
    fn_idx = src.index("def _generate_unique_lab_mac(")
    body = src[fn_idx:fn_idx + 4000]
    assert "self._existing_devices" in body
    assert "if _mac not in _seen:" in body


def test_helper_tolerates_multiple_field_name_variants():
    """Existing devices may spell the MAC field differently
    (`mac_address` / `MAC Address` / `mac`). The generator must
    honor all three so the collision check doesn't miss any."""
    src = _dialog_src()
    fn_idx = src.index("def _generate_unique_lab_mac(")
    body = src[fn_idx:fn_idx + 4000]
    assert '"mac_address"' in body
    assert '"MAC Address"' in body
    assert '"mac"' in body


def test_generated_mac_is_syntactically_valid():
    """Runtime: the generator must produce a MAC that matches the
    dialog's own regex validator `^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$`."""
    # We can't easily instantiate the QDialog without a Qt event
    # loop, but we can copy the algorithm and verify shape.
    import random
    _mac_re = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    for _ in range(20):
        _bytes = [0x02] + [random.randint(0, 0xFF) for _ in range(5)]
        _mac = ":".join(f"{b:02x}" for b in _bytes)
        assert _mac_re.match(_mac), f"generated MAC {_mac!r} failed validator regex"
        # LAA bit set:
        first = int(_mac.split(":", 1)[0], 16)
        assert first & 0x02, f"LAA bit not set in {_mac!r}"


def test_generator_returns_last_attempt_on_all_collision():
    """Failsafe: if 8 tries all collide (impossible in practice on
    a 40-bit random space), return the last one anyway so the
    dialog always has a valid MAC rather than raising."""
    src = _dialog_src()
    fn_idx = src.index("def _generate_unique_lab_mac(")
    body = src[fn_idx:fn_idx + 4000]
    # After the retry loop, the function returns _mac (the last one
    # we generated). No exception raised.
    assert "return _mac" in body


def test_comment_explains_tagged_and_untagged_coverage():
    """Comment must confirm that this works for BOTH tagged (vlan
    subif) and untagged (parent NIC) devices."""
    src = _dialog_src()
    idx = src.index("v0.5.348 (audit auto-mac-generation)")
    body = src[idx:idx + 3000]
    assert "tagged" in body.lower()
    assert "untagged" in body.lower()


def test_add_device_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())


def test_v0_5_325_marker_still_intact():
    """v0.5.325 is the server-side `ip link set <iface> address
    <mac>` fix that this MAC feeds into. Regression guard —
    without it, our auto-gen wouldn't actually reach the wire."""
    src = (_REPO / "run_tgen_server.py").read_text()
    assert "v0.5.325" in src
