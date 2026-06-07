"""'Make DPDK Ready' dialog — v0.3.11.

Replaces the manual 9-click chain (Tools→DPDK→Status, then IOMMU,
then VFIO, then hugepages, then bind…) with a single dialog that:

  1. Polls ``GET /api/dpdk/status`` to see what's missing.
  2. Computes the action plan via ``utils.dpdk_orchestrator.plan``.
  3. Walks each action sequentially, showing live progress.
  4. Pauses on actions that require a reboot (IOMMU) and prompts.
  5. For the bind step, pops a NIC picker and runs the bind.
  6. Re-polls status after each action to verify it succeeded.

Built on top of the existing ``_DpdkApiWorker`` HTTP plumbing in
``traffic_client.dpdk_menu_actions`` — no new endpoints, no new
threading model. The wizard (Phase 3) and Blast-a-Flow (Phase 4)
both reuse this dialog as a sub-step.
"""

from __future__ import annotations

import json as _json
import logging
import re
from typing import List, Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from utils.dpdk_orchestrator import (
    Action, ActionKind, augment_with_bind_history,
    classify_iface_driver, is_dpdk_ready, pick_default_bind_target,
    plan, recommended_hugepages,
)


logger = logging.getLogger(__name__)


# Reuse the existing HTTP worker from dpdk_menu_actions — same auth
# header handling + timeout semantics + signal shape. Importing here
# keeps the dialog self-contained from the GUI mixin's POV.
def _api_worker():
    """Late-import so this module loads without pulling the whole
    menu_actions chain (avoids circular import in tests)."""
    from traffic_client.dpdk_menu_actions import _DpdkApiWorker
    return _DpdkApiWorker


# ─────────────────────────────────── status row widget

_OK = "✓"
_PENDING = "○"
_RUNNING = "◐"
_FAIL = "✗"
_SKIP = "—"


class _StepRow(QListWidgetItem):
    """One row in the action list. Tracks status icon + label.

    Pure data — the parent QListWidget paints it. We pre-format the
    text so future tweaks don't need a custom item delegate.
    """

    def __init__(self, action: Action):
        super().__init__()
        self._action = action
        self._state = "pending"
        self._render()

    def set_state(self, state: str, note: str = "") -> None:
        """state ∈ {pending, running, ok, fail, skip}."""
        self._state = state
        self._note = note
        self._render()

    def _render(self) -> None:
        icon = {
            "pending": _PENDING, "running": _RUNNING, "ok": _OK,
            "fail":    _FAIL,    "skip":    _SKIP,
        }.get(self._state, _PENDING)
        label = self._action.label
        note = getattr(self, "_note", "")
        # v0.5.18: append ETA to pending/running rows so operators
        # see "5-10 min" before clicking Run, not 8 minutes in.
        # Once the step completes (ok/fail/skip) the ETA becomes
        # irrelevant — drop it to keep the row clean.
        eta = getattr(self._action, "eta", "") or ""
        if eta and self._state in ("pending", "running"):
            label = f"{label}  [{eta}]"
        suffix = f"  — {note}" if note else ""
        self.setText(f"  {icon}  {label}{suffix}")
        # Colour the state — green ok, red fail, grey skip.
        from PyQt5.QtGui import QColor
        self.setForeground(QColor({
            "pending": "#475569",
            "running": "#1d4ed8",
            "ok":      "#166534",
            "fail":    "#b91c1c",
            "skip":    "#94a3b8",
        }.get(self._state, "#475569")))


# ─────────────────────────────────── the dialog


class MakeDpdkReadyDialog(QDialog):
    """Top-of-funnel orchestrator UI. Opens, surveys, executes,
    closes.

    Lifecycle:
      * __init__ → builds the dialog shell, kicks an initial
        ``GET /api/dpdk/status`` to discover state.
      * On status response: runs ``plan()``, populates the row list,
        flips the Run button enabled.
      * Run click → walks rows in order, one HTTP call per row.
      * Bind row → opens NIC picker; uses ``pick_default_bind_target``
        to preselect.
      * Reboot row (IOMMU) → after success, shows a "reboot now then
        click Make Ready again" message and closes.
      * Success → posts a status-bar toast + emits ``finished_ready``
        signal so callers (Quick Start wizard, Blast-a-Flow) can chain.
    """

    # Emitted on successful completion of the full plan. Payload:
    # name of the freshly-bound NIC (so Blast-a-Flow can target it).
    finished_ready = pyqtSignal(str)

    def __init__(
        self,
        server_url: str,
        *,
        management_iface: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Make DPDK Ready")
        self.setMinimumWidth(680)
        self.resize(720, 520)
        # v0.3.11: application-modal so the main window is blocked
        # while the orchestrator runs. User reported they could
        # click around the main GUI with Blast a Flow open — on
        # macOS, QDialog defaults to window-modal which doesn't
        # always block the parent cleanly. Explicit
        # ApplicationModal is the cross-platform safe choice.
        # Also inherited by DpdkBlastFlowDialog (subclass) so the
        # blast-flow dialog gets the same blocking behaviour.
        self.setWindowModality(Qt.ApplicationModal)
        self._server_url = server_url.rstrip("/")
        self._mgmt_iface = management_iface
        self._actions: List[Action] = []
        self._rows: List[_StepRow] = []
        self._bound_iface: Optional[str] = None
        self._workers: List[QThread] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # Header explains intent so the operator doesn't have to
        # guess what "Make Ready" entails.
        hdr = QLabel(
            "<b>Make DPDK Ready</b><br/>"
            "<span style='color:#475569;'>This dialog detects what's "
            "missing for DPDK traffic on the selected server, then "
            "runs each fix in sequence: install → IOMMU → VFIO "
            "modules → hugepages → bind a NIC. Reboot is required "
            "after IOMMU is enabled the first time.</span>"
        )
        hdr.setWordWrap(True)
        outer.addWidget(hdr)

        # Server label (so multi-server users know which one they're
        # remediating).
        srv = QLabel(
            f"<b>Server:</b> <code>{self._server_url}</code>"
        )
        srv.setStyleSheet("color: #334155;")
        outer.addWidget(srv)

        # v0.3.11: advanced-options group — hugepage size + count,
        # plus tx-cores override. Collapsed visually by sitting in a
        # single QGroupBox so newcomers can ignore it. Defaults are
        # always correct for 99% of cases.
        adv = QGroupBox("Advanced options (defaults are usually fine)")
        adv_layout = QVBoxLayout(adv)
        adv_layout.setSpacing(4)

        hp_row = QHBoxLayout()
        hp_lbl = QLabel("Hugepages:")
        hp_lbl.setToolTip(
            "Number of hugepages to reserve. Each DPDK port wants "
            "~256–512 MB of hugepage memory; the default count gives "
            "~2 GB which is enough for several 10 G NICs. Use 1 GB "
            "pages for very-high-throughput (>100 G aggregate)."
        )
        self._hugepages = QSpinBox()
        self._hugepages.setMinimum(2)
        self._hugepages.setMaximum(16384)
        self._hugepages.setValue(recommended_hugepages())
        self._hugepage_2mb = QRadioButton("2 MB pages")
        self._hugepage_2mb.setChecked(True)
        self._hugepage_1gb = QRadioButton("1 GB pages")
        # v0.3.11: the server's /api/dpdk/hugepages handler only
        # accepts "2MB" today — sending "1GB" returns a 400.
        # Disable the radio with an explanatory tooltip rather than
        # let the operator pick a broken option. Server-side 1 GB
        # support is a follow-up; the orchestrator/dialog plumbing
        # is already in place for when it lands.
        self._hugepage_1gb.setEnabled(False)
        self._hugepage_1gb.setToolTip(
            "Not yet supported by the server — /api/dpdk/hugepages "
            "only accepts 2 MB pages today. 1 GB support is on the "
            "roadmap. For now, allocate more 2 MB pages instead."
        )
        hp_row.addWidget(hp_lbl)
        hp_row.addWidget(self._hugepages)
        hp_row.addSpacing(8)
        hp_row.addWidget(self._hugepage_2mb)
        hp_row.addWidget(self._hugepage_1gb)
        hp_row.addStretch(1)
        adv_layout.addLayout(hp_row)
        # When operator flips to 1 GB, default count to something
        # sensible (2 GB total = 2 × 1 GB). Reverting goes back to
        # the 2 MB recommendation.
        self._hugepage_1gb.toggled.connect(self._on_hp_size_changed)

        cores_row = QHBoxLayout()
        cores_lbl = QLabel("TX cores (0 = auto):")
        cores_lbl.setToolTip(
            "Number of CPU cores tx_worker pins to. Auto-pick is "
            "usually right — DPDK uses NUMA + isolated-CPU hints to "
            "pick non-noisy cores. Override only if you've already "
            "isolated specific cores in /etc/default/grub."
        )
        self._tx_cores = QSpinBox()
        self._tx_cores.setMinimum(0)
        self._tx_cores.setMaximum(64)
        self._tx_cores.setValue(0)
        self._tx_cores.setSpecialValueText("auto")
        cores_row.addWidget(cores_lbl)
        cores_row.addWidget(self._tx_cores)
        cores_row.addStretch(1)
        adv_layout.addLayout(cores_row)

        outer.addWidget(adv)

        # Action list — populated after status fetch.
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 6px; font-family: "
            "Menlo, Monaco, monospace; font-size: 12px; }"
        )
        # v0.3.11 layout follow-up: after a successful bind the
        # action row text grows long (e.g. "✓ Bind a NIC to vfio-
        # pci — enp181s0f0np0 — bifurcated (no rebind needed)").
        # Without elide-mode the list forces the dialog wider,
        # which triggers a Sample Stream grid reflow downstream
        # in DpdkBlastFlowDialog where field overlaps reappear.
        # Elide + word-wrap keeps the dialog at a stable width
        # and overlong rows just truncate / wrap in place.
        self._list.setTextElideMode(Qt.ElideRight)
        self._list.setWordWrap(True)
        # Prevent horizontal scroll-bar from sneaking in when a
        # row's natural width exceeds the list — we WANT elide,
        # not scroll.
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff,
        )
        outer.addWidget(self._list, 1)

        # Detail / log area for the most recent action.
        self._detail = QLabel("Surveying server…")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(
            "color: #475569; font-size: 11px; padding: 4px 0;"
        )
        outer.addWidget(self._detail)

        # Buttons. Run starts disabled — enabled after status fetch
        # populates the action list.
        btns = QDialogButtonBox()
        self._run_btn = QPushButton("Run All Steps")
        self._run_btn.setEnabled(False)
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._on_run)
        btns.addButton(self._run_btn, QDialogButtonBox.ActionRole)
        # v0.3.11 fix #6: "Unbind NIC..." button so the operator can
        # revert a bound NIC to kernel use without hunting through
        # Tools→DPDK→Unbind. Reuses the existing menu handler.
        self._unbind_btn = QPushButton("Unbind NIC…")
        self._unbind_btn.setToolTip(
            "Return a DPDK-bound NIC to the kernel network stack. "
            "Useful when re-purposing a NIC or troubleshooting a "
            "failed bind."
        )
        self._unbind_btn.clicked.connect(self._on_unbind_request)
        btns.addButton(self._unbind_btn, QDialogButtonBox.ActionRole)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        btns.addButton(self._close_btn, QDialogButtonBox.RejectRole)
        outer.addWidget(btns)

        # Defer the initial status fetch to showEvent — that way
        # constructing the dialog (e.g. in tests) doesn't spawn a
        # QThread that outlives the test object and segfaults on
        # interpreter teardown. The fetch happens immediately on
        # first show.
        self._initial_fetch_pending = True

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._initial_fetch_pending:
            self._initial_fetch_pending = False
            self._fetch_status()

    # ─────────────────────────────── status fetch + plan

    def _fetch_status(self) -> None:
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/dpdk/status",
            timeout=10,
        )
        # Qt-parent the worker so closing the dialog mid-fetch
        # gracefully cleans it up (don't leak a QThread on cancel).
        w.done.connect(self._on_status_ready)
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _on_status_ready(self, data, err) -> None:
        if err:
            self._detail.setText(
                f"<span style='color:#b91c1c;'>Failed to fetch DPDK "
                f"status: {err}</span>"
            )
            return
        if is_dpdk_ready(data):
            self._detail.setText(
                "<span style='color:#166534;'>✓ DPDK is already "
                "ready on this server. No action needed.</span>"
            )
            self._run_btn.setText("Nothing to do")
            return
        # Thread the operator's hugepage-size choice (1 GB vs 2 MB)
        # through the planner so the allocate-hugepages action body
        # uses the right size_kb.
        hp_size_kb = (
            1048576 if self._hugepage_1gb.isChecked() else 2048
        )
        self._actions = plan(
            data,
            hugepages=self._hugepages.value(),
            hugepage_size_kb=hp_size_kb,
        )
        self._list.clear()
        self._rows = []
        for action in self._actions:
            row = _StepRow(action)
            self._list.addItem(row)
            self._rows.append(row)
        self._detail.setText(
            f"Survey complete — {len(self._actions)} action(s) needed. "
            f"Click <b>Run All Steps</b> to proceed."
        )
        self._run_btn.setEnabled(bool(self._actions))

    # ─────────────────────────────── action execution

    def _on_run(self) -> None:
        self._run_btn.setEnabled(False)
        self._close_btn.setText("Cancel")
        self._step_idx = 0
        self._advance()

    def _advance(self) -> None:
        if self._step_idx >= len(self._actions):
            self._on_all_done()
            return
        action = self._actions[self._step_idx]
        row = self._rows[self._step_idx]
        row.set_state("running")
        self._list.scrollToItem(row)
        self._detail.setText(f"Running: {action.label}…")

        if action.kind == ActionKind.BIND_INTERFACE:
            self._run_bind_step(action, row)
            return

        # All other actions: just POST to the endpoint with the body.
        Worker = _api_worker()
        # IOMMU + install can take a long time; bump the timeout.
        timeout = 600 if action.kind == ActionKind.INSTALL_DPDK else 60
        w = Worker(
            method=action.method,
            url=f"{self._server_url}{action.endpoint}",
            json=action.body,
            timeout=timeout,
        )
        w.done.connect(
            lambda data, err, a=action, r=row: self._on_step_done(a, r, data, err)
        )
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _on_step_done(self, action: Action, row: _StepRow,
                      data, err) -> None:
        if err:
            row.set_state("fail", str(err)[:120])
            self._detail.setText(
                f"<span style='color:#b91c1c;'>{action.label} failed: "
                f"{err}</span>"
            )
            self._run_btn.setText("Retry")
            self._run_btn.setEnabled(True)
            self._close_btn.setText("Close")
            return

        # v0.5.17: INSTALL_DPDK is special. The HTTP 200 from
        # /api/admin/install_dpdk only confirms the script SPAWNED
        # — install_dpdk.sh runs for 5-10 minutes in the background.
        # Previously the wizard treated the immediate 200 as
        # completion and marched forward; operator hit "all steps ✓"
        # but the install was still running (or already failed
        # silently). Verify Installation then showed everything
        # missing.
        #
        # Now: keep the row in "running" state, start polling
        # /api/admin/install_dpdk/log until `running=False`. Then
        # check return_code to decide ✓ vs ✗.
        if action.kind == ActionKind.INSTALL_DPDK:
            # Don't set "ok" yet — script's still running. Show the
            # spawn-confirmation in the detail pane and start the
            # poll timer.
            log_path = (data or {}).get("log_path") or "(no log path)"
            self._detail.setText(
                f"<b>Installing DPDK runtime + building tx_worker</b>"
                f" — typically 5-10 minutes on a clean Ubuntu host.<br>"
                f"<span class='muted'>Log: <code>{log_path}</code></span>"
                f"<br>Polling for completion…"
            )
            self._start_install_dpdk_poll(action, row)
            return

        row.set_state("ok")
        if action.needs_reboot:
            # IOMMU success — reboot then come back.
            #
            # v0.5.15: prompt with an inline "Reboot Now" button. The
            # GRUB cmdline change doesn't take effect until reboot, so
            # making the operator alt-tab to a terminal and run
            # `sudo reboot` is friction. POST /api/system/reboot
            # (v0.5.2+) lets us trigger the reboot from the dialog.
            #
            # Three-button choice:
            #   Reboot Now        → POST /api/system/reboot, close dialog
            #   I'll Reboot Later → just close (user reboots manually)
            #   Cancel            → leave dialog open (user reviews log)
            self._detail.setText(
                "<b>Reboot required.</b> IOMMU has been written to the "
                "kernel cmdline. Reboot the server, then click "
                "<i>Make DPDK Ready</i> again to continue from where "
                "we left off."
            )
            self._prompt_reboot(action)
            self._close_btn.setText("Close")
            return

        self._step_idx += 1
        self._advance()

    def _prompt_reboot(self, action: Action) -> None:
        """v0.5.15: prompt operator about the IOMMU-required reboot
        and offer an inline 'Reboot Now' button that hits
        /api/system/reboot. Falls back to a friendly message if the
        server's too old to have the endpoint (pre-v0.5.2)."""
        # parsing the server's host out of the URL for the body
        host = self._server_url
        for prefix in ("http://", "https://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
                break
        host = host.rstrip("/").split(":")[0]

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Reboot Required")
        msg.setText(
            f"<b>IOMMU has been enabled on {host}.</b>"
        )
        msg.setInformativeText(
            "The kernel cmdline change doesn't take effect until the "
            "host reboots. After it comes back up, run <b>Make DPDK "
            "Ready</b> again — the wizard will skip the already-"
            "completed steps automatically.\n\n"
            "Reboot the server now?"
        )
        reboot_btn = msg.addButton(
            "Reboot Now", QMessageBox.AcceptRole,
        )
        reboot_btn.setToolTip(
            f"POST /api/system/reboot on {host}. Server schedules a "
            f"systemd reboot ~3 s after replying so the HTTP response "
            f"reaches the client cleanly."
        )
        later_btn = msg.addButton(
            "I'll Reboot Later", QMessageBox.RejectRole,
        )
        later_btn.setToolTip(
            "Close this dialog. Reboot the server manually (sudo reboot) "
            "when you're ready, then re-run Make DPDK Ready."
        )
        msg.setDefaultButton(reboot_btn)
        msg.exec_()

        if msg.clickedButton() is reboot_btn:
            self._trigger_reboot()

    def _trigger_reboot(self) -> None:
        """POST /api/system/reboot and surface the result. The server
        replies first, then schedules the reboot — so we should see a
        2xx, then the server goes away shortly after. Run async via
        the same _api_worker to keep the UI responsive."""
        self._detail.setText(
            "<b>Reboot requested.</b> POST /api/system/reboot — server "
            "will reply, then disappear ~3 s later. Wait for it to "
            "come back, then re-run Make DPDK Ready."
        )
        Worker = _api_worker()
        w = Worker(
            method="POST",
            url=f"{self._server_url}/api/system/reboot",
            json={},
            timeout=10,
        )
        w.done.connect(self._on_reboot_response)
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _on_reboot_response(self, data, err) -> None:
        """Handle the reply from /api/system/reboot. 404 means the
        server's too old (pre-v0.5.2) and the operator must reboot
        manually."""
        if err:
            err_text = str(err)
            if "404" in err_text:
                # Pre-v0.5.2 server — no /api/system/reboot endpoint.
                # Tell the operator how to reboot manually.
                QMessageBox.warning(
                    self, "Reboot Endpoint Missing",
                    "This server is too old to support remote reboot "
                    "(no /api/system/reboot — added in v0.5.2).\n\n"
                    "Reboot manually:\n"
                    f"  ssh root@{self._server_url} 'sudo reboot'\n\n"
                    "Then re-run Make DPDK Ready.",
                )
            else:
                QMessageBox.warning(
                    self, "Reboot Request Failed",
                    f"POST /api/system/reboot returned an error:\n\n"
                    f"{err}\n\n"
                    f"Reboot the server manually and re-run Make DPDK "
                    f"Ready when it's back up.",
                )
            return
        # Success — server is rebooting.
        self._detail.setText(
            "<b>✓ Reboot scheduled.</b> Server replied OK and will "
            "restart in ~3 s. Wait for it to come back online "
            "(typically 30–60 s), then re-run <i>Make DPDK Ready</i> "
            "— the wizard will skip the IOMMU step (now active) and "
            "continue from there."
        )

    # ─────────────── INSTALL_DPDK completion polling (v0.5.17)

    def _start_install_dpdk_poll(self, action: Action, row: _StepRow) -> None:
        """v0.5.17: kick off a QTimer that polls
        /api/admin/install_dpdk/log every 5 s until `running=False`,
        then resumes the wizard via _on_install_dpdk_complete().

        Why polling not a streamed log: the GET returns a JSON envelope
        with `running` + `return_code` already parsed; we don't need
        the log body for the wizard step (the detail pane only needs
        progress phase if available). The Make DPDK Ready dialog
        intentionally stays minimal — operators wanting the live log
        can click Tools → DPDK → Show Install Log separately.

        On each poll, update the detail pane with the parsed phase
        from the server response (server-side already parses
        Step N/total + ninja % from the log)."""
        # Store enough context for the timer callback to resume the
        # wizard state machine correctly when the install completes.
        self._install_poll_action = action
        self._install_poll_row = row
        self._install_poll_consecutive_errors = 0
        self._install_poll_log_offset = 0
        # 5 s cadence: install_dpdk.sh is many minutes long; tighter
        # would just generate noise.
        self._install_poll_timer = QTimer(self)
        self._install_poll_timer.setInterval(5000)
        self._install_poll_timer.timeout.connect(self._poll_install_dpdk_log)
        self._install_poll_timer.start()
        # Fire once immediately too — no need to wait 5 s for the
        # first phase update.
        self._poll_install_dpdk_log()

    def _poll_install_dpdk_log(self) -> None:
        """Fire one GET against /api/admin/install_dpdk/log via the
        existing async _api_worker, then dispatch to
        _on_install_dpdk_log_response()."""
        Worker = _api_worker()
        # Pass `offset` so the server only ships bytes since last poll
        # — the server already supports this (see api_admin_install_dpdk_log).
        offset = getattr(self, "_install_poll_log_offset", 0)
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/admin/install_dpdk/log?offset={offset}",
            timeout=10,
        )
        w.done.connect(self._on_install_dpdk_log_response)
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _on_install_dpdk_log_response(self, data, err) -> None:
        """Handle one /api/admin/install_dpdk/log response.

        On running=False, stop the timer and resume the wizard:
          - return_code == 0 → step ✓, advance
          - return_code != 0 → step ✗, surface error in detail pane
          - return_code == None (race) → wait one more tick

        Tolerate transient request errors: if 3 polls in a row fail
        with HTTP errors (server flapping, network blip), give up and
        mark the step as ✗ with the error. Single transient errors
        are absorbed silently."""
        action = getattr(self, "_install_poll_action", None)
        row = getattr(self, "_install_poll_row", None)
        if action is None or row is None:
            # Shouldn't happen — _start_install_dpdk_poll sets both.
            return

        if err:
            self._install_poll_consecutive_errors += 1
            if self._install_poll_consecutive_errors >= 3:
                self._stop_install_dpdk_poll()
                row.set_state("fail", f"log poll failed: {err}"[:120])
                self._detail.setText(
                    f"<span style='color:#b91c1c;'>"
                    f"Lost contact with /api/admin/install_dpdk/log "
                    f"after 3 consecutive errors: {err}<br>"
                    f"The install MAY still be running on the server "
                    f"— check <code>journalctl</code> or the log file "
                    f"path shown earlier.</span>"
                )
                self._run_btn.setText("Retry")
                self._run_btn.setEnabled(True)
                self._close_btn.setText("Close")
            return
        # Successful response — reset the error counter.
        self._install_poll_consecutive_errors = 0
        data = data or {}

        # Update phase in detail pane (server parses this from the
        # log on its side).
        phase = data.get("phase") or {}
        if phase:
            cur = phase.get("current_step")
            total = phase.get("total_steps")
            step_name = phase.get("step_name") or ""
            ninja_pct = phase.get("ninja_progress")
            overall_pct = phase.get("overall_pct")
            bits = []
            if cur and total:
                bits.append(f"Step {cur}/{total}")
            if step_name:
                bits.append(step_name)
            if ninja_pct is not None:
                bits.append(f"ninja {ninja_pct}%")
            if overall_pct is not None:
                bits.append(f"~{overall_pct}% overall")
            if bits:
                self._detail.setText(
                    f"<b>Installing DPDK runtime + building tx_worker</b><br>"
                    f"{' · '.join(bits)}"
                )

        # Advance the log offset so the next poll only ships new bytes.
        if "log_size" in data:
            self._install_poll_log_offset = int(data.get("log_size") or 0)

        # Still running? Keep polling.
        if data.get("running"):
            return

        # Done. Stop the timer and decide ✓ vs ✗ based on return_code.
        self._stop_install_dpdk_poll()
        rc = data.get("return_code")
        if rc == 0:
            row.set_state("ok")
            self._detail.setText(
                f"<b>✓ DPDK installed.</b> Continuing with remaining steps."
            )
            self._step_idx += 1
            self._advance()
        elif rc is None:
            # Race: server says not running but no return_code yet.
            # Restart the timer and try once more. (Should be rare.)
            self._install_poll_timer.start()
        else:
            row.set_state("fail", f"install_dpdk.sh exit {rc}")
            self._detail.setText(
                f"<span style='color:#b91c1c;'>"
                f"<b>install_dpdk.sh exited with code {rc}.</b><br>"
                f"Check the log on the server for the failure reason. "
                f"Common causes: apt timeout, network unreachable "
                f"during DPDK source clone, missing dev packages.<br>"
                f"Log file path was shown when the step started."
                f"</span>"
            )
            self._run_btn.setText("Retry")
            self._run_btn.setEnabled(True)
            self._close_btn.setText("Close")

    def _stop_install_dpdk_poll(self) -> None:
        """Stop the timer + clear the per-poll state. Safe to call
        multiple times."""
        try:
            timer = getattr(self, "_install_poll_timer", None)
            if timer is not None:
                timer.stop()
                self._install_poll_timer = None
        except Exception:
            pass
        self._install_poll_action = None
        self._install_poll_row = None

    # ─────────────────────────────── bind step (special: NIC picker)

    def _run_bind_step(self, action: Action, row: _StepRow) -> None:
        """Fetch interfaces, show picker, then run bind."""
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/dpdk/interfaces",
            timeout=15,
        )
        w.done.connect(
            lambda data, err, a=action, r=row: self._on_interfaces_for_bind(a, r, data, err)
        )
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _on_interfaces_for_bind(self, action: Action, row: _StepRow,
                                data, err) -> None:
        if err:
            row.set_state("fail", f"interfaces fetch failed: {err}")
            self._run_btn.setText("Retry")
            self._run_btn.setEnabled(True)
            return
        interfaces = (data or {}).get("interfaces") or []
        if not interfaces:
            row.set_state(
                "fail", "no interfaces reported by server",
            )
            self._run_btn.setText("Retry")
            self._run_btn.setEnabled(True)
            return

        # v0.3.11: chain a bind-history fetch so we can show the
        # pre-bind kernel name for vfio-pci-bound NICs. The picker
        # otherwise renders bound rows as "0000:22:00.0 · (no
        # interface)" which is correct but useless. The server's
        # /admin portal does the same join client-side; we mirror
        # it here. History fetch failure is non-fatal — we fall back
        # to showing the raw BDF.
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/admin/bind_history",
            timeout=5,
        )
        w.setParent(self)
        w.done.connect(
            lambda hist_data, hist_err, a=action, r=row, ifaces=interfaces:
                self._on_bind_history_ready(a, r, ifaces, hist_data, hist_err)
        )
        self._workers.append(w)
        w.start()

    def _on_bind_history_ready(self, action, row, interfaces,
                                hist_data, hist_err):
        """History fetch came back — merge and open picker."""
        if hist_err:
            logger.debug(
                f"[MAKE READY] bind_history fetch failed (non-fatal): "
                f"{hist_err}"
            )
            history = {}
        else:
            history = (hist_data or {}).get("history") or {}
        if history:
            interfaces = augment_with_bind_history(interfaces, history)

        default = pick_default_bind_target(
            interfaces, management_iface=self._mgmt_iface,
        )
        # Tiny NIC-picker sub-dialog. Inline rather than its own file
        # because it's tightly coupled to this flow.
        #
        # v0.3.11 sizing fix: user reported the picker was too wide
        # under Blast a DPDK Flow. Cap the dialog + combo width and
        # word-wrap the help label so a long iface name like
        # `enp13s0f0np0  ·  driver=mlx5_core  ·  link=up  ·  ip=… ·
        # BIFURCATED — no bind needed` can't push the dialog past
        # 640 px. Combo's view ellide-mode trims long items in the
        # closed state; the dropdown still shows the full text.
        picker = QDialog(self)
        picker.setWindowTitle("Pick NIC to Bind")
        picker.setMinimumWidth(440)
        picker.setMaximumWidth(640)
        pv = QVBoxLayout(picker)
        help_lbl = QLabel(
            "Select the NIC for DPDK traffic. The picker hides "
            "software interfaces (lo, br, bond, vnet, tun, tap, "
            "veth…) because DPDK has no PMD for them. The "
            "management interface is also hidden — binding it "
            "would disconnect this client. <b>Mellanox</b> NICs "
            "(mlx4/mlx5) run DPDK in <i>bifurcated</i> mode — no "
            "vfio-pci bind is needed; selecting one just records "
            "the choice and the wizard skips the rebind step."
        )
        help_lbl.setWordWrap(True)
        pv.addWidget(help_lbl)
        combo = QComboBox()
        combo.setMaximumWidth(620)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(40)
        combo.view().setTextElideMode(Qt.ElideRight)
        for i in interfaces:
            if not isinstance(i, dict):
                continue
            name = (i.get("name") or i.get("interface") or "").strip().lstrip("• ").strip()
            if not name:
                continue
            # v0.3.11 driver-aware filtering: hide unbindable
            # virtual ifaces entirely so they can't be picked by
            # mistake. The orchestrator's classifier is the single
            # source of truth.
            cls = classify_iface_driver(i)
            if cls == "virtual":
                continue
            driver = i.get("driver", "—")
            link = i.get("link", "?")
            ip = i.get("ipv4") or i.get("ipv6") or ""
            is_mgmt = self._mgmt_iface and name == self._mgmt_iface
            # Compact format — drop the "driver=" / "link=" prefixes
            # (column position carries the meaning) and shorten the
            # badge text. Cuts ~30 chars off the typical row.
            # For bound NICs with bind-history, the driver column
            # reads "was tg3" to identify which NIC this used to be.
            previous_driver = i.get("previous_driver") or ""
            if cls == "bound" and previous_driver:
                drv_str = f"was {previous_driver}"
            else:
                drv_str = str(driver)
            label_bits = [name, drv_str, str(link)]
            if ip:
                label_bits.append(str(ip))
            if cls == "bound":
                label_bits.append("BOUND")
            elif cls == "bifurcated":
                label_bits.append("bifurcated (no bind)")
            if is_mgmt:
                label_bits.append("MGMT")
            # `name` here may have "(DPDK)" suffix if augment_with_
            # bind_history rewrote it; use the original pre-bind name
            # as the combo's data so callers downstream still see the
            # bare iface name (not "eno8303 (DPDK)").
            combo_data = i.get("_pre_bind_name") or name
            combo.addItem(" · ".join(label_bits), combo_data)
            if combo_data == default and cls not in ("bound",) and not is_mgmt:
                combo.setCurrentIndex(combo.count() - 1)
        pv.addWidget(combo)

        pbtns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        pbtns.accepted.connect(picker.accept)
        pbtns.rejected.connect(picker.reject)
        pv.addWidget(pbtns)

        if picker.exec_() != QDialog.Accepted:
            row.set_state("skip", "cancelled by operator")
            self._run_btn.setText("Run All Steps")
            self._run_btn.setEnabled(True)
            return

        chosen = combo.currentData()
        if not chosen:
            row.set_state("fail", "no NIC selected")
            self._run_btn.setText("Retry")
            self._run_btn.setEnabled(True)
            return

        self._bound_iface = chosen
        action.body["interface"] = chosen

        # v0.3.11: short-circuit for bifurcated Mellanox NICs.
        # mlx4/mlx5 keep their kernel driver loaded and DPDK runs
        # alongside via RDMA verbs — there's no vfio-pci bind to
        # perform. Mark the step done and proceed without HTTP.
        chosen_meta = next(
            (i for i in interfaces if isinstance(i, dict)
             and (i.get("name") or i.get("interface") or "").strip().lstrip("• ").strip() == chosen),
            None,
        )
        if chosen_meta and classify_iface_driver(chosen_meta) == "bifurcated":
            row.set_state(
                "ok",
                f"{chosen} — bifurcated (no rebind needed)",
            )
            self._step_idx += 1
            self._advance()
            return

        # Standard bindable NIC — POST to /api/dpdk/bind.
        Worker = _api_worker()
        w = Worker(
            method="POST",
            url=f"{self._server_url}/api/dpdk/bind",
            json=action.body,
            timeout=60,
        )
        w.done.connect(
            lambda data, err, a=action, r=row: self._on_bind_done(a, r, chosen, data, err)
        )
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _on_bind_done(self, action: Action, row: _StepRow,
                      iface: str, data, err) -> None:
        if err:
            row.set_state("fail", f"bind failed: {err}")
            self._run_btn.setText("Retry")
            self._run_btn.setEnabled(True)
            self._close_btn.setText("Close")
            return
        row.set_state("ok", f"bound {iface}")
        self._step_idx += 1
        self._advance()

    # ─────────────────────────────── completion

    def _on_all_done(self) -> None:
        self._detail.setText(
            f"<span style='color:#166534;'><b>✓ DPDK is ready.</b></span> "
            f"All steps completed. Bound NIC: <code>"
            f"{self._bound_iface or '—'}</code>."
        )
        self._run_btn.setText("Done")
        self._run_btn.setEnabled(False)
        self._close_btn.setText("Close")
        if self._bound_iface:
            self.finished_ready.emit(self._bound_iface)

    # ─────────────────────────────── v0.3.11 fixes

    def _on_hp_size_changed(self, using_1gb: bool) -> None:
        """When operator flips between 2 MB and 1 GB hugepages, reset
        the count to a sensible default for that size — 2 GB total
        either way. Avoids leaving the count at 1024 when switching
        to 1 GB (= 1 TB, nobody wants that)."""
        if using_1gb:
            self._hugepages.setValue(2)
            self._hugepages.setMaximum(64)
            self._hugepages.setSingleStep(1)
        else:
            self._hugepages.setMaximum(16384)
            self._hugepages.setSingleStep(128)
            self._hugepages.setValue(recommended_hugepages())

    def _on_unbind_request(self) -> None:
        """Bridge to the existing Tools→DPDK→Unbind Interface
        handler so the operator can revert a bound NIC without
        leaving the orchestrator dialog. We walk up the parent
        chain to find the main window; if not found, fall back to
        a hint dialog directing them to Tools→DPDK."""
        parent = self.parent()
        # The DPDK mixin's unbind handler is named
        # `unbind_interface_from_dpdk` (see dpdk_menu_actions.py).
        while parent is not None and not hasattr(
            parent, "unbind_interface_from_dpdk",
        ):
            parent = parent.parent() if hasattr(parent, "parent") else None
        if parent is None:
            QMessageBox.information(
                self, "Unbind NIC",
                "Use Tools → DPDK → Unbind Interface… to return a "
                "DPDK-bound NIC to the kernel driver.",
            )
            return
        try:
            parent.unbind_interface_from_dpdk()
        except Exception as exc:
            logger.warning(
                f"[MAKE READY] unbind bridge failed: {exc}"
            )
