"""DPDK Quick Start Wizard — v0.3.11 Phase 4.

QWizard for first-timers. Same orchestrator engine as the one-click
'Make DPDK Ready' dialog, but laid out as separate pages so the
operator sees what each step does before it runs:

  1. Welcome — what DPDK is + what this wizard does (~10 lines).
  2. Survey — POST GET /api/dpdk/status, render the result table
     ("install: ✓", "iommu: ✗ (not enabled in kernel cmdline)", …).
  3. Plan — show the ordered list of actions about to run with
     Skip checkboxes per row. Hugepage count is editable here.
  4. Run — live progress (same row-list widget as the one-click
     dialog), pauses on IOMMU reboot, opens NIC picker for bind.
  5. Done — summary + offer to open Blast a Flow if bind succeeded.

QWizard handles the Next/Back/Finish chrome. We supply pages.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    QWizard, QWizardPage,
)

from utils.dpdk_orchestrator import (
    Action, ActionKind, augment_with_bind_history,
    classify_iface_driver, is_dpdk_ready, plan, recommended_hugepages,
)


logger = logging.getLogger(__name__)


def _api_worker():
    """Late import to avoid the menu_actions ↔ widget circular."""
    from traffic_client.dpdk_menu_actions import _DpdkApiWorker
    return _DpdkApiWorker


# ─────────────────────────────────── pages

class _WelcomePage(QWizardPage):
    """Page 1 — what DPDK is and what we'll do. No input needed."""

    def __init__(self, server_url: str):
        super().__init__()
        self.setTitle("DPDK Quick Start")
        self.setSubTitle(
            f"Wizard for getting DPDK traffic flowing on "
            f"{server_url}."
        )
        v = QVBoxLayout(self)
        body = QLabel(
            "<p>DPDK (Data Plane Development Kit) lets the traffic "
            "generator transmit packets at line rate by bypassing "
            "the kernel network stack. To use it, the server needs:</p>"
            "<ul>"
            "<li><b>DPDK runtime + tx_worker</b> — installed binaries.</li>"
            "<li><b>IOMMU enabled</b> in the kernel cmdline "
            "(requires reboot the first time).</li>"
            "<li><b>vfio + vfio-pci</b> kernel modules loaded.</li>"
            "<li><b>Hugepages</b> allocated (typically 1024 × 2 MB).</li>"
            "<li><b>At least one NIC</b> bound to vfio-pci — except "
            "Mellanox mlx4/mlx5 NICs which run bifurcated and need "
            "no bind.</li>"
            "</ul>"
            "<p>This wizard checks what's missing, runs the fixes in "
            "the right order, pauses for reboot if needed, and "
            "finishes by binding a NIC of your choice. Click "
            "<b>Next</b> to survey the server.</p>"
            "<p style='color:#475569; font-size:11px;'><b>When to use "
            "which entry point:</b><br/>"
            "&nbsp;&nbsp;• <b>This wizard</b> — first-time setup, "
            "step-by-step explanation.<br/>"
            "&nbsp;&nbsp;• <b>Make DPDK Ready…</b> — same engine, "
            "no narrative; for repeat use.<br/>"
            "&nbsp;&nbsp;• <b>Blast a Flow…</b> — make ready PLUS "
            "auto-start a sample 64 B UDP line-rate flow (smoke "
            "test / demo).</p>"
        )
        body.setWordWrap(True)
        v.addWidget(body)


class _SurveyPage(QWizardPage):
    """Page 2 — fetch + display current status. Stashes the parsed
    status dict on the wizard so the Plan page can read it."""

    def __init__(self, server_url: str):
        super().__init__()
        self.setTitle("Server Survey")
        self.setSubTitle(
            "Checking what's installed and configured on the server."
        )
        self._server_url = server_url.rstrip("/")
        self._status_data = None

        v = QVBoxLayout(self)
        self._summary = QLabel("Fetching DPDK status…")
        self._summary.setWordWrap(True)
        v.addWidget(self._summary)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 6px; font-family: "
            "Menlo, Monaco, monospace; font-size: 12px; }"
        )
        v.addWidget(self._list, 1)

    def initializePage(self):
        """Kick the fetch when the page is shown. QWizard reuses
        pages so we re-fetch on every back→next round-trip."""
        self._list.clear()
        self._summary.setText("Fetching DPDK status…")
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/dpdk/status",
            timeout=10,
        )
        w.setParent(self)
        w.done.connect(self._on_status)
        w.start()

    def _on_status(self, data, err):
        if err:
            self._summary.setText(
                f"<span style='color:#b91c1c;'>Failed to fetch "
                f"status: {err}. Click Back to retry.</span>"
            )
            return
        self._status_data = data or {}
        self.wizard().setProperty("status_data", data or {})

        if is_dpdk_ready(data):
            self._summary.setText(
                "<span style='color:#166534;'><b>✓ DPDK is already "
                "ready on this server.</b></span> The wizard will "
                "still let you bind another NIC or revisit any "
                "step — click Next to continue."
            )
        else:
            self._summary.setText(
                "Survey complete. Status of each prerequisite is "
                "shown below. Click <b>Next</b> to review the action "
                "plan."
            )

        # One row per prereq with ✓/✗ icon and short note.
        rows = [
            ("DPDK runtime installed",
             bool(data.get("dpdk_installed")),
             f"v{data.get('dpdk_version', '?')}"),
            ("tx_worker binary present",
             bool(data.get("tx_worker_exists")),
             f"built {data.get('tx_worker_built', '?')}"),
            ("IOMMU enabled",
             bool(data.get("iommu_enabled")),
             str(data.get("iommu_details") or "")),
            ("vfio module loaded",
             bool(data.get("vfio_loaded")),
             ""),
            ("vfio-pci module loaded",
             bool(data.get("vfio_pci_loaded")),
             ""),
            ("Hugepages allocated",
             bool(data.get("hugepages_configured")),
             f"{data.get('hugepages_available', 0)} × "
             f"{data.get('hugepage_size', '?')}"),
        ]
        for label, ok, note in rows:
            icon = "✓" if ok else "✗"
            text = f"  {icon}  {label}"
            if note:
                text += f"  — {note}"
            item = QListWidgetItem(text)
            item.setForeground(QColor("#166534" if ok else "#b91c1c"))
            self._list.addItem(item)

        # Interface section.
        interfaces = data.get("interfaces") or []
        n_bound = sum(
            1 for i in interfaces
            if isinstance(i, dict)
            and "vfio" in str(i.get("driver", "")).lower()
        )
        item = QListWidgetItem(
            f"  {'✓' if n_bound else '✗'}  "
            f"{n_bound} NIC(s) bound to vfio-pci"
        )
        item.setForeground(
            QColor("#166534" if n_bound else "#b91c1c"),
        )
        self._list.addItem(item)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._status_data is not None


class _PlanPage(QWizardPage):
    """Page 3 — show the ordered action plan with Skip checkboxes
    + tunable hugepage count. Operator confirms here before any
    state-changing call fires."""

    def __init__(self, server_url: str):
        super().__init__()
        self.setTitle("Action Plan")
        self.setSubTitle(
            "Review the steps the wizard will run. Uncheck any "
            "step to skip it."
        )
        self._server_url = server_url
        self._step_checks: List[QCheckBox] = []
        self._actions: List[Action] = []

        v = QVBoxLayout(self)
        intro = QLabel(
            "Each step runs only if the previous one succeeded. "
            "The IOMMU step requires a reboot — the wizard will "
            "pause and prompt you, and you'll need to re-run it "
            "after the reboot to continue."
        )
        intro.setWordWrap(True)
        v.addWidget(intro)

        # Hugepage tuner.
        hp_row = QHBoxLayout()
        hp_row.addWidget(QLabel("Hugepages (2 MB each):"))
        self._hugepages = QSpinBox()
        self._hugepages.setMinimum(64)
        self._hugepages.setMaximum(16384)
        self._hugepages.setSingleStep(128)
        self._hugepages.setValue(recommended_hugepages())
        hp_row.addWidget(self._hugepages)
        hp_row.addStretch(1)
        v.addLayout(hp_row)

        # Plan group (filled on initializePage).
        self._plan_group = QGroupBox("Steps")
        self._plan_layout = QVBoxLayout(self._plan_group)
        v.addWidget(self._plan_group, 1)

    def initializePage(self):
        """Recompute the plan from the survey's status_data each
        time the page is shown — handles back→tweak→next."""
        status = self.wizard().property("status_data") or {}
        self._actions = plan(
            status, hugepages=self._hugepages.value(),
        )

        # Clear previous checkboxes (back→next case).
        while self._plan_layout.count():
            item = self._plan_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._step_checks = []

        if not self._actions:
            lbl = QLabel(
                "<span style='color:#166534;'>Nothing to do — DPDK "
                "is already fully set up.</span>"
            )
            lbl.setWordWrap(True)
            self._plan_layout.addWidget(lbl)
            return

        for action in self._actions:
            cb = QCheckBox(action.label)
            cb.setChecked(True)
            if action.needs_reboot:
                cb.setToolTip(
                    "This step modifies the kernel cmdline. "
                    "Reboot required after the wizard finishes."
                )
            self._step_checks.append(cb)
            self._plan_layout.addWidget(cb)

        # Stash the (possibly trimmed) plan for the Run page.
        self.wizard().setProperty("planned_actions", self._actions)

    def validatePage(self) -> bool:
        """Strip out unchecked steps before handing to Run page."""
        kept = [
            a for a, cb in zip(self._actions, self._step_checks)
            if cb.isChecked()
        ]
        self.wizard().setProperty("planned_actions", kept)
        self.wizard().setProperty(
            "hugepages_override", self._hugepages.value(),
        )
        return True


class _RunPage(QWizardPage):
    """Page 4 — execute the plan with live row updates. Reuses the
    same status icons + bind-picker the one-click dialog uses."""

    # Icon glyphs (shared with MakeDpdkReadyDialog — keep in sync).
    _OK = "✓"
    _PENDING = "○"
    _RUNNING = "◐"
    _FAIL = "✗"
    _SKIP = "—"

    def __init__(self, server_url: str, management_iface: Optional[str]):
        super().__init__()
        self.setTitle("Running")
        self.setSubTitle("Executing each step in order.")
        self._server_url = server_url.rstrip("/")
        self._mgmt_iface = management_iface
        self._actions: List[Action] = []
        self._rows: List[QListWidgetItem] = []
        self._step_idx = 0
        self._bound_iface: Optional[str] = None
        self._workers: List = []
        self._done = False

        v = QVBoxLayout(self)
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 6px; font-family: "
            "Menlo, Monaco, monospace; font-size: 12px; }"
        )
        v.addWidget(self._list, 1)

        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(
            "color: #475569; font-size: 11px; padding: 4px 0;"
        )
        v.addWidget(self._detail)

    def initializePage(self):
        self._actions = self.wizard().property("planned_actions") or []
        self._rows = []
        self._list.clear()
        for action in self._actions:
            item = QListWidgetItem(
                f"  {self._PENDING}  {action.label}"
            )
            item.setForeground(QColor("#475569"))
            self._list.addItem(item)
            self._rows.append(item)
        self._step_idx = 0
        self._done = False
        if not self._actions:
            self._detail.setText(
                "Nothing to do. Click Next to finish."
            )
            self._done = True
            self.completeChanged.emit()
            return
        self._advance()

    def isComplete(self) -> bool:
        return self._done

    def _set_row(self, idx: int, state: str, note: str = ""):
        icon = {
            "pending": self._PENDING, "running": self._RUNNING,
            "ok":      self._OK,      "fail":    self._FAIL,
            "skip":    self._SKIP,
        }.get(state, self._PENDING)
        action = self._actions[idx]
        suffix = f"  — {note}" if note else ""
        self._rows[idx].setText(f"  {icon}  {action.label}{suffix}")
        self._rows[idx].setForeground(QColor({
            "pending": "#475569", "running": "#1d4ed8",
            "ok":      "#166534", "fail":    "#b91c1c",
            "skip":    "#94a3b8",
        }.get(state, "#475569")))

    def _advance(self):
        if self._step_idx >= len(self._actions):
            self._on_all_done()
            return
        action = self._actions[self._step_idx]
        self._set_row(self._step_idx, "running")
        self._list.scrollToItem(self._rows[self._step_idx])
        self._detail.setText(f"Running: {action.label}…")

        if action.kind == ActionKind.BIND_INTERFACE:
            self._run_bind()
            return

        Worker = _api_worker()
        timeout = 600 if action.kind == ActionKind.INSTALL_DPDK else 60
        w = Worker(
            method=action.method,
            url=f"{self._server_url}{action.endpoint}",
            json=action.body,
            timeout=timeout,
        )
        w.setParent(self)
        w.done.connect(self._on_step_done)
        self._workers.append(w)
        w.start()

    def _on_step_done(self, data, err):
        action = self._actions[self._step_idx]
        if err:
            self._set_row(self._step_idx, "fail", str(err)[:120])
            self._detail.setText(
                f"<span style='color:#b91c1c;'>{action.label} "
                f"failed: {err}</span><br/>Click Back to revise the "
                f"plan, or Cancel to exit."
            )
            return
        self._set_row(self._step_idx, "ok")
        if action.needs_reboot:
            from PyQt5.QtWidgets import QMessageBox
            self._detail.setText(
                "<b>Reboot required.</b> IOMMU has been enabled in "
                "the kernel cmdline. Reboot the server and re-run "
                "the wizard to continue from where we left off."
            )
            QMessageBox.information(
                self, "Reboot Required",
                f"IOMMU has been enabled on {self._server_url}.\n\n"
                f"Reboot the server, then re-run the Quick Start "
                f"Wizard — already-completed steps will be skipped.",
            )
            self._done = True
            self.completeChanged.emit()
            return
        self._step_idx += 1
        self._advance()

    def _run_bind(self):
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/dpdk/interfaces",
            timeout=15,
        )
        w.setParent(self)
        w.done.connect(self._on_interfaces_for_bind)
        self._workers.append(w)
        w.start()

    def _on_interfaces_for_bind(self, data, err):
        from utils.dpdk_orchestrator import pick_default_bind_target
        if err:
            self._set_row(
                self._step_idx, "fail",
                f"interfaces fetch failed: {err}",
            )
            return
        interfaces = (data or {}).get("interfaces") or []
        if not interfaces:
            self._set_row(
                self._step_idx, "fail",
                "no interfaces reported",
            )
            return

        # v0.3.11: chain a bind-history fetch so bound NICs in the
        # picker show their pre-bind name. Same pattern as
        # MakeDpdkReadyDialog. Non-fatal on failure.
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/admin/bind_history",
            timeout=5,
        )
        w.setParent(self)
        w.done.connect(
            lambda hist_data, hist_err, ifaces=interfaces:
                self._on_history_for_bind(ifaces, hist_data, hist_err)
        )
        self._workers.append(w)
        w.start()

    def _on_history_for_bind(self, interfaces, hist_data, hist_err):
        """History fetch returned — merge and continue to picker."""
        from utils.dpdk_orchestrator import pick_default_bind_target
        if hist_err:
            logger.debug(
                f"[WIZARD] bind_history fetch failed (non-fatal): "
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
        # Reuse the same NIC-picker shape as the one-click dialog —
        # just inline since the wizard already provides Next/Back chrome.
        # v0.3.11 sizing fix: cap width + word-wrap help + ellide
        # long combo items (mirror of dpdk_make_ready_dialog).
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import (
            QComboBox, QDialog, QDialogButtonBox, QVBoxLayout, QLabel,
        )
        picker = QDialog(self)
        picker.setWindowTitle("Pick NIC to Bind")
        picker.setMinimumWidth(440)
        picker.setMaximumWidth(640)
        pv = QVBoxLayout(picker)
        help_lbl = QLabel(
            "Select the NIC to bind to vfio-pci. The management "
            "interface is excluded — binding it would disconnect "
            "this client from the server."
        )
        help_lbl.setWordWrap(True)
        pv.addWidget(help_lbl)
        combo = QComboBox()
        combo.setMaximumWidth(620)
        combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon,
        )
        combo.setMinimumContentsLength(40)
        combo.view().setTextElideMode(Qt.ElideRight)
        for i in interfaces:
            if not isinstance(i, dict):
                continue
            name = (i.get("name") or i.get("interface") or "").strip().lstrip("• ").strip()
            if not name:
                continue
            # v0.3.11 driver-aware filtering — same rules as
            # MakeDpdkReadyDialog. Hide virtual ifaces (lo / br /
            # bond / vnet / tun / tap / veth …) since DPDK has no
            # PMD for them. Badge bifurcated Mellanox so the
            # operator knows no rebind will occur.
            cls = classify_iface_driver(i)
            if cls == "virtual":
                continue
            # Compact format — drop "driver=" / "link=" / "ip="
            # prefixes; column position carries meaning. Cuts ~30
            # chars off the row. For bound NICs with bind-history,
            # the driver column reads "was tg3" to identify which
            # NIC this used to be.
            previous_driver = i.get("previous_driver") or ""
            if cls == "bound" and previous_driver:
                drv_str = f"was {previous_driver}"
            else:
                drv_str = str(i.get("driver", "—"))
            label_bits = [name, drv_str, str(i.get("link", "?"))]
            ip = i.get("ipv4") or i.get("ipv6")
            if ip:
                label_bits.append(str(ip))
            if cls == "bound":
                label_bits.append("BOUND")
            elif cls == "bifurcated":
                label_bits.append("bifurcated (no bind)")
            if self._mgmt_iface and name == self._mgmt_iface:
                label_bits.append("MGMT")
            # Use pre-bind name as the combo's data so downstream
            # bind calls reference the bare iface, not the
            # "eno8303 (DPDK)" annotated string.
            combo_data = i.get("_pre_bind_name") or name
            combo.addItem(" · ".join(label_bits), combo_data)
            if combo_data == default:
                combo.setCurrentIndex(combo.count() - 1)
        pv.addWidget(combo)
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        btns.accepted.connect(picker.accept)
        btns.rejected.connect(picker.reject)
        pv.addWidget(btns)
        if picker.exec_() != QDialog.Accepted:
            self._set_row(self._step_idx, "skip", "cancelled")
            self._done = True
            self.completeChanged.emit()
            return
        chosen = combo.currentData()
        if not chosen:
            self._set_row(self._step_idx, "fail", "no NIC chosen")
            return
        self._bound_iface = chosen
        action = self._actions[self._step_idx]
        action.body["interface"] = chosen

        # v0.3.11: bifurcated NICs (Mellanox mlx4/mlx5) skip the
        # vfio-pci bind — DPDK uses them via RDMA verbs alongside
        # the kernel driver. Mark the step ok and continue.
        chosen_meta = next(
            (i for i in interfaces if isinstance(i, dict)
             and (i.get("name") or i.get("interface") or "").strip().lstrip("• ").strip() == chosen),
            None,
        )
        if chosen_meta and classify_iface_driver(chosen_meta) == "bifurcated":
            self._set_row(
                self._step_idx, "ok",
                f"{chosen} — bifurcated (no rebind needed)",
            )
            self._step_idx += 1
            self._advance()
            return

        Worker = _api_worker()
        w = Worker(
            method="POST",
            url=f"{self._server_url}/api/dpdk/bind",
            json=action.body,
            timeout=60,
        )
        w.setParent(self)
        w.done.connect(self._on_bind_done)
        self._workers.append(w)
        w.start()

    def _on_bind_done(self, data, err):
        if err:
            self._set_row(
                self._step_idx, "fail", f"bind failed: {err}",
            )
            return
        self._set_row(
            self._step_idx, "ok", f"bound {self._bound_iface}",
        )
        self._step_idx += 1
        self._advance()

    def _on_all_done(self):
        self._detail.setText(
            f"<span style='color:#166534;'><b>✓ DPDK is ready.</b></span> "
            f"Bound NIC: <code>{self._bound_iface or '—'}</code>. "
            f"Click <b>Next</b> for the summary."
        )
        self.wizard().setProperty("bound_iface", self._bound_iface)
        self._done = True
        self.completeChanged.emit()


class _DonePage(QWizardPage):
    """Page 5 — summary + next-step nudges."""

    def __init__(self):
        super().__init__()
        self.setTitle("Done")
        self.setFinalPage(True)
        v = QVBoxLayout(self)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        v.addWidget(self._summary)

    def initializePage(self):
        bound = self.wizard().property("bound_iface")
        if bound:
            self._summary.setText(
                f"<p><b>✓ DPDK is ready on this server.</b></p>"
                f"<p>NIC <code>{bound}</code> is bound to vfio-pci. "
                f"To start sending traffic, you can either:</p>"
                f"<ul>"
                f"<li>Open the Streams tab, add a stream on this NIC, "
                f"tick <i>Use DPDK</i>, and click ▶ Start.</li>"
                f"<li>Or use <b>Tools → DPDK → Blast a Flow…</b> for "
                f"a one-click 64 B UDP line-rate test flow.</li>"
                f"</ul>"
                f"<p>Click <b>Finish</b> to close the wizard.</p>"
            )
        else:
            self._summary.setText(
                "<p>Wizard ended without binding a NIC. You can "
                "re-run it any time from <b>Tools → DPDK → "
                "Quick Start Wizard…</b>. Already-completed steps "
                "will be skipped automatically.</p>"
            )


# ─────────────────────────────────── wizard


class DpdkQuickStartWizard(QWizard):
    """Top-level QWizard. Pages registered in order; the wizard
    handles Next/Back/Finish chrome on its own."""

    def __init__(
        self,
        server_url: str,
        *,
        management_iface: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("DPDK Quick Start Wizard")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.HaveHelpButton, False)
        self.setMinimumWidth(720)
        self.resize(760, 580)
        # v0.3.11: application-modal so the main window is blocked
        # while the wizard runs. Default QWidget modality on
        # macOS doesn't reliably block the parent for QWizard;
        # explicit ApplicationModal is portable.
        self.setWindowModality(Qt.ApplicationModal)

        self.addPage(_WelcomePage(server_url))
        self.addPage(_SurveyPage(server_url))
        self.addPage(_PlanPage(server_url))
        self.addPage(_RunPage(server_url, management_iface))
        self.addPage(_DonePage())
