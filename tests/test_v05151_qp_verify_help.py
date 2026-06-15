"""v0.5.151: QP-verify help dialog on the Blast a RDMA Flow window.

Operator (after successfully running 171 Gbps with qp_count=10):

    "increased QP=10, how do i verify what QPs being used to
     send roce traffic ?"

perftest doesn't print its QP inventory by default. The right
answer is `rdma resource show qp link <hca>/<port>`, but the
operator has to remember the syntax, the HCA name, and the IB
port number — three things that are already on the dialog.

v0.5.151 adds an "❓ Verify" button next to the QP-count
spinbox that pops a `_QpVerifyHelpDialog` with five
copy-paste-ready command sections, each filled in with the
operator's current dialog state (host, HCA, IB port,
qp_count).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()


# ───── button placement


def test_verify_button_exists_next_to_qp_spinbox():
    """The button must be wired up under the existing
    `_qp_count_spin`. v0.5.151 wraps both in a small HBox so the
    button sits flush with the spinbox in the same grid cell."""
    assert "self._qp_verify_btn = QPushButton(" in SRC
    assert "❓ Verify" in SRC


def test_verify_button_clicked_signal_wired():
    assert (
        "self._qp_verify_btn.clicked.connect(self._show_qp_verify_help)"
        in SRC
    )


def test_show_qp_verify_help_method_exists():
    assert "def _show_qp_verify_help" in SRC


def test_show_qp_verify_help_passes_live_values():
    """The handler must read the dialog's CURRENT state — what
    HCAs are picked, what the QP spinbox shows — and hand those
    to the help dialog. Otherwise the commands shown are
    irrelevant stubs."""
    body = _extract_method(SRC, "_show_qp_verify_help")
    assert "self._server_device_combo.currentData()" in body
    assert "self._client_device_combo.currentData()" in body
    assert "self._server_port_spin.value()" in body
    assert "self._client_port_spin.value()" in body
    assert "self._qp_count_spin.value()" in body
    # And hostname extraction from the TG URL.
    assert "urlparse" in body


def test_show_qp_verify_help_falls_back_to_placeholder():
    """If the operator hasn't picked a device yet, the dialog
    should still open with a placeholder rather than crashing or
    embedding `None` into the command."""
    body = _extract_method(SRC, "_show_qp_verify_help")
    assert (
        '"<server-hca>"' in body
        or "'<server-hca>'" in body
    )


# ───── _QpVerifyHelpDialog class


def test_help_dialog_class_exists():
    assert "class _QpVerifyHelpDialog(QDialog):" in SRC


def test_help_dialog_constructor_takes_named_args():
    """The constructor must take the five identity values as
    keyword-only args (srv_host, srv_dev, srv_port, cli_*, qp_n,
    same_host) so the caller can't accidentally swap server and
    client tokens."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    sig = re.search(r"def __init__\(.*?\):", cls, flags=re.DOTALL)
    assert sig is not None
    text = sig.group(0)
    for name in [
        "srv_host", "cli_host",
        "srv_dev", "cli_dev",
        "srv_port", "cli_port",
        "qp_n", "same_host",
    ]:
        assert name in text, f"{name} missing from constructor"
    # Must be keyword-only.
    assert "*," in text


def test_help_dialog_renders_rdma_resource_show_qp():
    """The whole point: the operator must see the
    `rdma resource show qp link <hca>/<port>` command."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert "rdma resource show qp link" in cls


def test_help_dialog_includes_state_filter_hint():
    """A QP inventory is useless without explaining what state
    each row SHOULD be in. RTS = working; INIT / RTR = stuck."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert "RTS" in cls
    assert "INIT" in cls or "RTR" in cls


def test_help_dialog_includes_perftest_verbose_hint():
    """Section 4: perftest's own `-v` flag via the perf_extra
    escape hatch. Operators who want QP-by-QP introspection
    from perftest itself need to know this exists."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert "perf_extra" in cls
    # The `-v` lives inside an embedded JSON literal in a curl
    # command, so it shows up backslash-escaped in source.
    assert '\\"-v\\"' in cls or '"-v"' in cls


def test_help_dialog_includes_phy_counter_cross_check():
    """Section 5: ethtool -S PHY counters as an independent
    cross-check that the QPs are doing real work on the wire."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert "tx_packets_phy" in cls
    assert "ethtool" in cls


def test_help_dialog_has_copy_buttons():
    """Each command should have an individual Copy button + an
    overall Copy-all button. The whole point of the dialog is
    to short-circuit memorizing syntax."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert '"Copy"' in cls
    assert '"Copy all commands"' in cls


def test_help_dialog_substitutes_qp_count_in_count_blurb():
    """The 'count' section explains the expected number — and
    must use the operator's actual qp_n so they know what to
    compare against."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    # The blurb shows `≈ {qp_n}` plus the +1 control QP note.
    assert "{qp_n}" in cls
    assert "control QP" in cls


def test_help_dialog_uses_ssh_wrapping():
    """Operator is on macOS; the target is srv06. Wrap every
    command in `ssh <host> '...'` so it's actually runnable on
    the operator's terminal without further editing."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert "def _ssh(" in cls
    assert "f\"ssh {host}" in cls


def test_help_dialog_skips_duplicate_client_command_for_loopback():
    """When server and client share the same (host, HCA), one
    `rdma resource show qp` command covers both — printing the
    same command twice would be noisy. Section logic must check
    `same_host or srv_dev != cli_dev` before adding the client
    half."""
    cls = _extract_class(SRC, "_QpVerifyHelpDialog")
    assert "same_host or srv_dev != cli_dev" in cls


# ───── helpers


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)


def _extract_class(src: str, name: str) -> str:
    pat = re.compile(
        rf"class {re.escape(name)}\(.*?(?=\nclass \w|\Z)",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"class {name}(...) not found"
    return m.group(0)
