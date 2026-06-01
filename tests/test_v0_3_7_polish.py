"""v0.3.7 — three small fixes:

1. Loss% null contract (`traffic_client/statistics_section.py`):
   when `tx_count == 0` the GUI now stores ``loss_pct = None``
   instead of `0.0`, and the renderer treats None as the muted
   "—" placeholder. Pre-v0.3.7 a freshly-started stream in its
   warmup window rendered "0.00%" in green — a false-positive
   "perfect zero loss" reading when actually no packets had
   been TX'd at all.

2. `.whl` extension warning on the install/upgrade dialog's
   wheel picker. Pre-v0.3.7 the file-dialog "All files" option
   let operators pick tarballs / arbitrary binaries, then wait
   5 minutes for the upload to finish before pip rejected.

3. Ctrl+Return shortcut on the install/upgrade dialog. Matches
   the standard pattern from Stream dialog v0.2.96 / RFC 2544
   v0.3.0 / DPDK Status v0.2.97. Dispatcher picks the active
   tab's primary button.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
STATS_FILE = REPO / "traffic_client" / "statistics_section.py"
INSTALL_FILE = REPO / "widgets" / "install_server_dialog.py"


@pytest.fixture(scope="module")
def stats_src():
    return STATS_FILE.read_text()


@pytest.fixture(scope="module")
def install_src():
    return INSTALL_FILE.read_text()


# ─────────────────────────────────── loss% null contract
def test_v0_3_7_loss_pct_returns_none_when_tx_zero(stats_src):
    """When `tx_count == 0`, the compute block must store None
    (not 0.0) so the renderer can show '—' instead of a
    misleading '0.00% green' reading."""
    # Find the loss-compute block. There are two — the snapshot
    # collector that builds `all_streams` and the renderer that
    # reads `stream["loss_pct"]`. We want the collector.
    m = re.search(
        r"# Calculate loss percentage.*?loss_pct\s*=\s*None",
        stats_src, flags=re.DOTALL,
    )
    assert m is not None, (
        "loss% compute block doesn't set loss_pct = None on the "
        "tx_count==0 branch — v0.3.7 null contract regressed"
    )


def test_v0_3_7_renderer_handles_none_loss(stats_src):
    """The renderer must treat `loss_pct is None` as the muted
    '—' placeholder, not crash on `f"{loss_pct:.2f}%"`."""
    # Look for the `elif loss_pct is None:` branch in the render
    # path. A comment may live between the elif and the assignment;
    # use DOTALL + a forgiving pattern to span it.
    assert re.search(
        r"elif loss_pct is None:.{0,200}?loss_text = \"—\"",
        stats_src, flags=re.DOTALL,
    ), "renderer missing 'elif loss_pct is None: → \"—\"' branch"


def test_v0_3_7_loss_pct_unchanged_when_tx_positive(stats_src):
    """Backward-compat: when tx_count > 0 the math is unchanged.
    Pin the formula so a refactor can't quietly drop the
    (tx-rx)/tx*100 calculation."""
    assert "(tx_count - rx_count) / tx_count * 100" in stats_src


# ─────────────────────────────────── .whl extension warning
def test_v0_3_7_browse_wheel_warns_on_non_whl(install_src):
    """The `_browse_wheel` helper must check the chosen path's
    extension and surface a QMessageBox.warning before the
    upload kicks off."""
    body = re.search(
        r"def _browse_wheel\(self.*?(?=\n    def |\Z)",
        install_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert ".whl" in text and "QMessageBox" in text, (
        "_browse_wheel missing the v0.3.7 non-.whl warning — "
        "operators can still silently pick a non-wheel file"
    )
    # And the check should be lowercased so .WHL also matches.
    assert ".lower()" in text and 'endswith(".whl")' in text


# ─────────────────────────────────── Ctrl+Return shortcut
def test_v0_3_7_ctrl_return_shortcut_wired(install_src):
    """The dialog must wire a Ctrl+Return shortcut at the end of
    __init__ that dispatches to the active tab's primary button."""
    # Inside InstallServerDialog.__init__ — look for the shortcut.
    init_body = re.search(
        r"class InstallServerDialog.*?def _build_upgrade_tab",
        install_src, flags=re.DOTALL,
    )
    assert init_body is not None
    text = init_body.group(0)
    assert "Key_Return" in text, (
        "Ctrl+Return shortcut not wired in InstallServerDialog.__init__"
    )
    assert "QShortcut" in text
    assert "_on_ctrl_return" in text


def test_v0_3_7_ctrl_return_dispatcher_method_exists(install_src):
    """The shortcut handler must be a named method (visible in
    tracebacks + searchable for future audits)."""
    assert re.search(
        r"^    def _on_ctrl_return\(self", install_src,
        flags=re.MULTILINE,
    ), "_on_ctrl_return method missing"


def test_v0_3_7_ctrl_return_dispatcher_branches_by_tab(install_src):
    """The dispatcher must check `self.tabs.currentIndex()` to
    decide which primary button to click — otherwise it would
    always fire the same one regardless of which tab is
    active."""
    body = re.search(
        r"def _on_ctrl_return\(self.*?(?=\n    def |\Z)",
        install_src, flags=re.DOTALL,
    )
    text = body.group(0)
    assert "currentIndex" in text, (
        "dispatcher doesn't branch on the active tab — pressing "
        "Ctrl+Return on Fresh Install would still click the "
        "Upgrade button (or vice versa)"
    )
    assert "isEnabled" in text, (
        "dispatcher should respect the busy flag — clicking a "
        "disabled button during an in-flight install/upgrade "
        "should be a no-op"
    )
