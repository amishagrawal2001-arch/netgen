"""v0.5.250 — DHCP refresh stampede: coalesce + silent auto-refresh.

Operator on 2026-09-02: "some polling is freezing the window".

Trace: `refresh_dhcp_status()` is fired from many places:
  - line 1117 startup (200 ms after tab opens)
  - line 1557 on Attach completion
  - line 1695 on Restart completion
  - line 1928 on Apply completion
  - widgets/devices_tab.py QTimer.singleShot after every per-device
    Apply result — one call PER DEVICE in a multi-device batch

Each call opened its own modal QProgressDialog + spawned a fresh
15-20 s worker. After a 5-device Apply the operator saw a STACK of
5 modal dialogs draining back-to-back, each blocking input —
looked like a hard freeze.

Fixes:

1. **Coalesce** — new `self._dhcp_refresh_in_flight` guard. Second
   call while a refresh is running skips cleanly instead of
   stacking a duplicate worker + modal.
2. **Silent auto-refresh** — new `user_initiated: bool = False`
   kwarg. The modal `QProgressDialog` only pops when the user
   actually clicked the Refresh button; auto-triggered refreshes
   (post-Apply/Attach/Restart) run in the background without a
   modal blocking input. The Refresh button's `clicked` handler
   is rewired to a `lambda: self.refresh_dhcp_status(user_initiated=True)`.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "devices_tab_dhcp.py").read_text()


def test_refresh_takes_user_initiated_kwarg():
    """New kwarg defaults to False so every internal auto-caller
    stays silent without editing every call site."""
    assert "def refresh_dhcp_status(self, user_initiated: bool = False):" in DHCP


def test_v05250_marker_in_docstring():
    idx = DHCP.find("def refresh_dhcp_status(self")
    end = DHCP.find("\n    def ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 8000]
    assert "v0.5.250 (audit U refresh-stampede)" in body


def test_coalesce_guard_defined_and_checked():
    idx = DHCP.find("def refresh_dhcp_status(self")
    end = DHCP.find("\n    def ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 8000]
    # Guard set at the top of the function AFTER server-URL check.
    assert 'if getattr(self, "_dhcp_refresh_in_flight", False):' in body
    assert "self._dhcp_refresh_in_flight = True" in body


def test_guard_reset_on_worker_finish():
    """The finished handler drops the guard so the NEXT trigger
    can fire. Must run whether the worker succeeded or errored."""
    idx = DHCP.find("def refresh_dhcp_status(self")
    end = DHCP.find("\n    def ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 8000]
    assert "self._dhcp_refresh_in_flight = False" in body


def test_modal_only_when_user_initiated():
    """QProgressDialog is only constructed inside `if user_initiated:`."""
    idx = DHCP.find("def refresh_dhcp_status(self")
    end = DHCP.find("\n    def ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 8000]
    assert "progress = None" in body
    assert "if user_initiated:" in body
    # progress.show() only after the guard.
    _guard_pos = body.find("if user_initiated:")
    _show_pos = body.find("progress.show()")
    assert 0 < _guard_pos < _show_pos, "progress.show() must be inside the user_initiated branch"


def test_finish_handler_handles_no_modal_case():
    """When there's no modal (auto-refresh), the close/setLabelText
    guards must not raise."""
    idx = DHCP.find("def refresh_dhcp_status(self")
    end = DHCP.find("\n    def ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 8000]
    assert "if progress is not None:" in body
    # Both the finish-handler close and the cancel-handler setLabelText
    # are behind the None check.
    _close_pos = body.find("progress.close()")
    _guard_pos_1 = body.rfind("if progress is not None:", 0, _close_pos)
    assert 0 < _guard_pos_1, "progress.close() must be inside a None guard"


def test_refresh_button_passes_user_initiated_true():
    """The Refresh toolbar button's clicked signal must invoke
    refresh_dhcp_status(user_initiated=True) so the modal shows."""
    idx = DHCP.find("v0.5.250: user-clicked Refresh")
    assert idx > 0
    body = DHCP[idx:idx + 500]
    assert "self.refresh_dhcp_status(user_initiated=True)" in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 250)
