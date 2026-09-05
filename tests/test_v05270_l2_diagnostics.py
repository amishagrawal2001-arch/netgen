"""v0.5.270 — L2 emulation diagnostic banners + ARP-fail
surfacing. Source-level checks (no PyQt runtime)."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
L2 = (REPO / "utils" / "l2_protocols.py").read_text()
DLG = (REPO / "widgets" / "l2_emulation_tab.py").read_text()


# --- L2-D1: LACP bridge-consumption banner -----------------------


def test_lacp_panel_has_bridge_consumed_banner():
    assert "v0.5.270 (L2-D1)" in DLG
    idx = DLG.find("def _build_lacp_panel")
    end = DLG.find("\n    def ", idx + 1)
    body = DLG[idx:end]
    # Warning-yellow amber styling for the banner
    assert "background-color: #fef3c7" in body
    # Text mentions the reserved dst MAC + gives the valid topologies
    assert "01:80:c2:00:00:02" in body
    assert ("back-to-back" in body.lower()
            or "back to back" in body.lower())
    assert "IEEE 802.1D" in body


def test_lacp_panel_uses_vbox_wrapper_for_banner():
    """Since QGroupBox now holds [banner + form_holder] via
    QVBoxLayout, verify the wrapping pattern lands (banner ABOVE
    the form, not to the side)."""
    idx = DLG.find("def _build_lacp_panel")
    end = DLG.find("\n    def ", idx + 1)
    body = DLG[idx:end]
    assert "_outer = QVBoxLayout(w)" in body
    assert "_form_holder = QWidget()" in body
    assert "f = QFormLayout(_form_holder)" in body


# --- L2-D2: IGMP snooping-behavior banner -----------------------


def test_igmp_panel_has_snooping_note():
    assert "v0.5.270 (L2-D2)" in DLG
    idx = DLG.find("def _build_igmp_panel")
    end = DLG.find("\n    def ", idx + 1)
    body = DLG[idx:end]
    # Info-blue styling (distinct from LACP's amber warning)
    assert "background-color: #dbeafe" in body
    # Body mentions the snooping-forward rule + verification hint
    assert "snooping" in body.lower()
    assert "show ip igmp snooping groups" in body


# --- L2-D3: PIM peer-requirement banner -------------------------


def test_pim_panel_has_peer_requirement_note():
    assert "v0.5.270 (L2-D3)" in DLG
    idx = DLG.find("def _build_pim_panel")
    end = DLG.find("\n    def ", idx + 1)
    body = DLG[idx:end]
    assert "background-color: #dbeafe" in body
    # Cites frr pimd + Cisco ip-pim-sparse-mode + verification cmd
    assert "pimd" in body
    assert "sparse-mode" in body
    assert "show ip pim neighbor" in body


# --- L2-D4: BFD ARP-fail seeds counters.last_error --------------


def test_bfd_arp_fail_diagnostic_string_present():
    """Wording changed in v0.5.270 (L2-D4); check the new phrasing
    that goes into counters.last_error."""
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert "v0.5.270 (L2-D4)" in body
    # The diagnostic itself.
    assert "ARP resolve for" in body
    assert "failed at" in body and "session start" in body
    assert "documentation MAC" in body
    # And the actionable next step.
    assert "Ping the peer" in body


def test_bfd_last_error_seeded_before_return():
    """`_arp_fail_diag` is captured when ARP resolve fails and
    assigned into `sess.counters.last_error` under the session's
    lock BEFORE the function returns the session id."""
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert "_arp_fail_diag: Optional[str] = None" in body
    # The seed happens inside `if _arp_fail_diag:` after
    # _register_and_start (so the counters object exists).
    seed_idx = body.find("if _arp_fail_diag:")
    register_idx = body.find("_register_and_start(sess, _factory")
    return_idx = body.rfind("return sid")
    assert 0 < register_idx < seed_idx < return_idx, (
        "seed must land AFTER _register_and_start (counters exist) "
        "and BEFORE return sid"
    )
    # The seed uses the session's own lock (same lock the read
    # side uses in snapshot()) so there's no race.
    assert "with sess.lock:" in body[seed_idx:seed_idx + 200]
    assert "sess.counters.last_error = _arp_fail_diag" in body


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 270)
