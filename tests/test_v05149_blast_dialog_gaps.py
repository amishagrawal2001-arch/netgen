"""v0.5.149: Blast a RDMA Flow dialog gap fixes.

Audit (post-v0.5.148, via Explore agent) surfaced three concrete
gaps in `widgets/rdma_blast_flow_dialog.py` that the Topology
dialog had already closed:

A. **Error display clipped to 120 chars** on the client even
   though v0.5.146 already filtered the perftest banner
   server-side. Anything past char 120 of the cleaned
   diagnostic was lost — the operator saw a truncated tail with
   no indication more existed.
B. **No "device = RDMA HCA" inline hint** under the device
   combos. Operators conflated the HCA name with the Ethernet
   iface picker elsewhere in the GUI. Topology fixed this in
   v0.5.143.
C. **No "OTHER HCA" shortcut** for same-host two-HCA testing.
   v0.5.147 added single-HCA loopback. v0.5.148 added the
   two-HCA TOGGLE to the Topology picker. Blast was left with
   only the same-HCA mirror, even though the explicit
   server/client device combos made dual-HCA possible — just
   manual.

v0.5.149 closes all three.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()


# ───── Gap A+F: error display ────────────────────────────────────────────


def test_error_display_no_120_char_clip():
    """The 120-char clip on `job.get('error')[:120]` must be gone.
    v0.5.146's server-side _format_rc_error already filters and
    clips at ~400 chars; the client must NOT clip again."""
    assert "job.get('error')[:120]" not in SRC, (
        "v0.5.149 dropped the redundant 120-char clip — if this "
        "test fires, the clip was reintroduced and the cleaned "
        "diagnostic is being truncated again"
    )


def test_error_display_uses_newline_separator():
    """A multi-line error renders cleanly because the QTextEdit
    wraps. Switching from inline `  err=…` to `\\n  err=…` lets
    long banners scroll without overflowing the right edge."""
    assert "f\"\\n  err={job.get('error')}\"" in SRC


# ───── Gap C: HCA-vs-Ethernet hint ───────────────────────────────────────


def test_device_combo_has_hca_clarification_hint():
    """A hint label under the device combos must spell out that
    the second token is an RDMA HCA name, not an Ethernet
    interface — matches the v0.5.143 hint in the Topology
    dialog."""
    assert "RDMA HCA name" in SRC
    assert "libibverbs" in SRC
    # Contrast against the Ethernet iface picker — the operator
    # confusion this hint resolves.
    assert (
        "Ethernet interface" in SRC
        or "ens2f0np0" in SRC
    )


def test_hint_added_to_device_grid_row_2():
    """The hint sits between the existing device combos (rows 0–1)
    and the new loopback row. Validates the geometry stays sane
    and the buttons below don't overlap."""
    assert "dev_grid.addWidget(_hca_hint, 2, 0, 1, 4)" in SRC


# ───── Gap B: same-host two-HCA shortcut ─────────────────────────────────


def test_other_hca_button_exists():
    """A button labelled for the same-host two-HCA case must
    appear next to the existing loopback mirror button."""
    assert "Use OTHER HCA (same host two-port test)" in SRC


def test_other_hca_button_only_enabled_for_same_tg():
    """Two-HCA test only makes sense between processes on the
    same host. Disabled when server_tg_url != client_tg_url."""
    assert "self._other_hca_btn.setEnabled(same_tg)" in SRC


def test_other_hca_button_wires_to_handler():
    """Click → _pick_other_hca_for_client. Distinct from the
    same-HCA mirror handler so the two intents stay separate in
    the codebase."""
    assert (
        "self._other_hca_btn.clicked.connect(self._pick_other_hca_for_client)"
        in SRC
    )


def test_pick_other_hca_method_exists():
    assert "def _pick_other_hca_for_client" in SRC


def test_pick_other_hca_picks_next_real_device():
    """The slot must skip placeholder items (userData=None) and
    pick the device AFTER the server's index — wrapping to the
    first real entry when the server picked the last."""
    body = _extract_method(SRC, "_pick_other_hca_for_client")
    # Build the real-indices list.
    assert "real_indices" in body
    assert "itemData(i) is not None" in body
    # Wrap-around modulo.
    assert "% len(real_indices)" in body
    # And the right combo gets the new index.
    assert "self._client_device_combo.setCurrentIndex(" in body


def test_pick_other_hca_no_op_single_hca():
    """When the client combo only has one real device, two-HCA
    is impossible. The slot must no-op rather than silently
    setting client = server (which would just be loopback again)."""
    body = _extract_method(SRC, "_pick_other_hca_for_client")
    assert "len(real_indices) < 2" in body


def test_pick_other_hca_falls_back_to_mirror_on_asymmetric_probe():
    """Edge case: server combo has an HCA the client combo
    hasn't probed yet (asymmetric response). Don't crash, don't
    leave the client unset — fall back to the same-HCA mirror
    so the operator at least gets a valid loopback config."""
    body = _extract_method(SRC, "_pick_other_hca_for_client")
    assert "self._mirror_server_to_client()" in body


def test_pick_other_hca_short_circuits_when_server_not_probed():
    """If the server combo is still on `(probing…)` /
    userData=None, the slot must bail rather than writing None
    into the client combo."""
    body = _extract_method(SRC, "_pick_other_hca_for_client")
    assert "if not srv_dev:" in body or "srv_dev is None" in body


# ───── helpers ────────────────────────────────────────────────────────────


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"could not locate def {name}(...) in source"
    return m.group(0)
