"""v0.5.268 — L2 emulation dialog default-value fixes.

Source-level checks (Qt not imported); we verify the fix markers
and the shape of the surrounding code so a future refactor that
regressed the intent would fail here.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
L2 = (REPO / "widgets" / "l2_emulation_tab.py").read_text()


# --- L2-B1: Interface field prefers TG-tab selection --------------


def test_guess_default_iface_reads_server_tree_selection_first():
    assert "v0.5.268 (L2-B1)" in L2
    idx = L2.find("def _guess_default_iface")
    assert idx > 0
    end = L2.find("\n    def ", idx + 1)
    body = L2[idx:end if end > 0 else idx + 3000]
    # 1. TG-tab lookup precedes the cached-servers fallback.
    tree_idx = body.find("server_tree")
    cache_idx = body.find("server_interfaces")
    assert 0 < tree_idx < cache_idx, (
        "server_tree selection must be consulted BEFORE the cached "
        "server_interfaces fallback"
    )
    # 2. Group rows ("TG 0"), URL-looking rows and the skip-list are
    #    filtered out.
    assert 'startswith(("TG ", "tg "))' in body
    assert '"://" not in _txt' in body
    assert "_skip_as_default_iface" in body
    # 3. Both columns (0, 1) are scanned since the TG-tree renders
    #    the interface in col 0 for some layouts and col 1 for others.
    assert "for col in (0, 1)" in body


def test_guess_default_iface_last_resort_still_eth0():
    """The final `return "eth0"` sentinel is preserved so an empty
    tree + empty cache still yields a syntactically-valid value."""
    idx = L2.find("def _guess_default_iface")
    end = L2.find("\n    def ", idx + 1)
    body = L2[idx:end]
    # `return "eth0"` sits at the base indent of the function body,
    # unconditional — i.e., not nested in any of the try blocks.
    assert '\n        return "eth0"\n' in body


# --- L2-B2: LLDP Port ID auto-tracks Interface field --------------


def test_lldp_port_id_syncs_from_interface_via_textchanged():
    assert "v0.5.268 (L2-B2)" in L2
    idx = L2.find("v0.5.268 (L2-B2)")
    body = L2[idx:idx + 2500]
    # The initial value seeds from _iface_input.text(), not "eth0".
    assert "_iface_input.text()" in body
    # An edited-latch prevents auto-sync from clobbering user input.
    assert "_lldp_port_id_manually_edited" in body
    # textEdited (user-typed) flips the latch; textChanged is what
    # the Interface field pushes into Port ID.
    assert "textEdited.connect" in body
    assert "textChanged.connect(_sync_port_id_from_iface)" in body
    # Guard against re-entrancy: setText inside the sync callback
    # must block signals so it doesn't accidentally trip the
    # user-edit latch.
    assert "blockSignals(True)" in body


def test_lldp_port_id_no_longer_hardcoded_eth0_only():
    """The pre-fix `QLineEdit("eth0")` seed must be gone (only
    surviving as the empty-string fallback)."""
    # Match the OLD form specifically — Port ID being constructed
    # with a bare "eth0" literal. Post-fix uses _initial_port_id.
    forbidden = 'self._lldp_port_id = QLineEdit("eth0")'
    assert forbidden not in L2


# --- L2-B3: Chassis ID defaults to hostname -----------------------


def test_lldp_chassis_id_defaults_to_client_hostname():
    assert "v0.5.268 (L2-B3" in L2
    idx = L2.find("v0.5.268 (L2-B3")
    body = L2[idx:idx + 1500]
    # socket.gethostname() (aliased locally to _socket) drives the
    # default, with "netgen-host" ONLY as the exception fallback.
    assert "_socket.gethostname()" in body
    assert "self._lldp_chassis_id = QLineEdit(_client_host)" in body


def test_lldp_chassis_id_no_longer_hardcoded_string():
    """The old `QLineEdit("netgen-host")` seed is gone."""
    forbidden = 'self._lldp_chassis_id = QLineEdit("netgen-host")'
    assert forbidden not in L2


# --- L2-B4: System Name defaults to hostname ----------------------


def test_lldp_system_name_defaults_to_client_hostname():
    assert "v0.5.268 (L2-B3 + L2-B4)" in L2 or "L2-B4" in L2
    # System Name reuses the same _client_host that Chassis ID does.
    assert "self._lldp_system_name = QLineEdit(_client_host)" in L2


def test_lldp_system_name_no_longer_hardcoded_string():
    forbidden = 'self._lldp_system_name = QLineEdit("netgen")'
    assert forbidden not in L2


# --- L2-B5: Source MAC blank by default + skip-validate -----------


def test_lldp_src_mac_default_is_blank():
    assert "v0.5.268 (L2-B5)" in L2
    idx = L2.find("v0.5.268 (L2-B5)")
    body = L2[idx:idx + 1500]
    # Default is a blank string, with placeholder text pointing at
    # the server's auto-derive behavior.
    assert 'self._lldp_src_mac = QLineEdit("")' in body
    assert "leave blank to auto-derive from interface MAC" in body


def test_lldp_src_mac_no_longer_documentation_mac():
    """The pre-fix hardcoded `00:11:22:33:44:02` seed is gone."""
    # Match the actual construction, not any doc/comment mention.
    forbidden = 'self._lldp_src_mac = QLineEdit("00:11:22:33:44:02")'
    assert forbidden not in L2


def test_submit_path_skips_validation_when_src_mac_blank():
    """Submit path in `accepted_payload` must skip _validate_mac
    when src_mac is empty (blank = server auto-derive signal).
    Same pattern as the v0.5.252 VRRP dialog fix."""
    # Locate the LLDP branch and confirm the guard.
    idx = L2.find('elif proto == "lldp":')
    assert idx > 0
    # The v0.5.268 marker for the skip-validate guard sits inside
    # this branch.
    branch = L2[idx:idx + 1500]
    assert "v0.5.268 (L2-B5)" in branch
    # The guard: `if src_mac:` wraps the validate+reject call.
    guard_idx = branch.find("if src_mac:")
    validate_idx = branch.find("_validate_mac(src_mac)")
    reject_idx = branch.find('_reject(f"Source MAC:')
    assert 0 < guard_idx < validate_idx < reject_idx, (
        "the _validate_mac call must be nested INSIDE the "
        "`if src_mac:` guard so blank input reaches the server"
    )
    # And the payload still carries src_mac (possibly empty) so the
    # server sees it — the server's start_lldp handler treats
    # empty string as "auto-derive from interface MAC".
    assert '"src_mac": src_mac,' in branch


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 268)
