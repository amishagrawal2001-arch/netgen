"""RDMA menu actions for the traffic-gen client — v0.3.12.

Companion to traffic_client/dpdk_menu_actions.py. Hosts the entry
points for the new RDMA submenu under Tools:

  * Blast a RDMA Flow…   — RdmaBlastFlowDialog (two-TG perftest)
  * RDMA Devices…        — per-server /api/rdma/devices viewer
  * RDMA Jobs…           — per-server /api/rdma/perftest/jobs viewer

All HTTP calls reuse _DpdkApiWorker from dpdk_menu_actions to stay
on the established async pattern (off the GUI thread, parent-owned
QThread, no SIGABRT-after-GC class of bug).

Held as a mixin (class with no __init__) the same way TrafficGen-
ClientDPDKMenuActions is mixed into the main window — keeps menu
plumbing close to the rest of the menu code while letting the main
window own state like `self._get_selected_servers`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)


def _api_worker():
    """Late import — dpdk_menu_actions also imports widgets in some
    code paths; defer to break any latent cycle."""
    from traffic_client.dpdk_menu_actions import _DpdkApiWorker
    return _DpdkApiWorker


class TrafficGenClientRDMAMenuActions:
    """RDMA menu actions for the traffic-gen client.

    Mixed into the main window class alongside TrafficGenClient-
    DPDKMenuActions. Expects the host to provide:
        self._get_selected_servers() -> list[dict-or-str]
        self._track_dpdk_worker(worker) -> None   (reused for RDMA)
    """

    # ─────────────────────────────────────────── tracking helper

    def _track_rdma_worker(self, worker):
        """Hold a strong ref until done. Mirror of DPDK helper —
        keeps the worker bookkeeping isolated per subsystem so a
        leak in one doesn't pin the other's threads."""
        if not hasattr(self, "_rdma_workers"):
            self._rdma_workers = set()
        self._rdma_workers.add(worker)
        worker.done.connect(
            lambda *_a, w=worker: self._rdma_workers.discard(w)
        )

    # ─────────────────────────────────────────── single + dual TG helpers

    def _resolve_two_servers_for_rdma(
        self, action_name: str,
    ) -> Tuple[Optional[dict], Optional[dict]]:
        """Pick (server-TG, client-TG) for a two-sided RDMA test.

        Rules:
          * 0 selected → ask the operator to pick.
          * 1 selected → loopback (returns (only, only)).
          * 2 selected → first = server, second = client (selection
            order). Operator can pre-swap by reselecting if they
            care about which is which.
          * >2 selected → ask the operator to narrow to 1 or 2.
        """
        servers = self._get_selected_servers()
        if not servers:
            QMessageBox.information(
                self, action_name,
                f"Select 1 server (for a loopback test) or 2 servers "
                f"(server then client) in the server tree, then open "
                f"{action_name}.",
            )
            return None, None
        if len(servers) == 1:
            return servers[0], servers[0]
        if len(servers) == 2:
            return servers[0], servers[1]
        QMessageBox.information(
            self, action_name,
            f"{len(servers)} servers selected. {action_name} runs "
            f"between 1 (loopback) or 2 (server + client) TGs only — "
            f"narrow the selection and try again.",
        )
        return None, None

    @staticmethod
    def _server_url_label(server) -> Tuple[Optional[str], str]:
        """Return (url, display-label). Display label includes the
        TG id so the operator can tell which side is which in the
        Endpoints box of the Blast RDMA dialog."""
        if isinstance(server, dict):
            url = server.get("address")
            tg_id = server.get("tg_id", "?")
            return url, f"TG {tg_id}"
        return server, f"TG {server}"

    # ─────────────────────────────────────────── Setup RDMA (v0.5.27)

    def show_setup_rdma_dialog(self):
        """Open the Setup RDMA installer dialog for the selected server.

        v0.5.27: operator-requested separation from DPDK install. The
        RDMA stack (libibverbs-dev, rdma-core, perftest, ibverbs-utils,
        infiniband-diags + optional libmlx5-dev) is now an independent
        wizard rather than a side-effect of Setup DPDK. Drives
        /api/admin/install_rdma on the selected server and tails the
        log via /api/admin/install_rdma/log.

        Single-server only (same shape as Make DPDK Ready) — pops a
        warning if multiple TGs are selected.
        """
        servers = self._get_selected_servers()
        if not servers:
            QMessageBox.information(
                self, "Setup RDMA",
                "Select a server (TG) in the server tree first, then "
                "open the dialog.",
            )
            return
        if len(servers) > 1:
            QMessageBox.information(
                self, "Setup RDMA",
                "Multiple servers selected. Setup RDMA operates on one "
                "server at a time — please select a single TG in the "
                "server tree and try again.",
            )
            return
        server = servers[0]
        server_url, _label = self._server_url_label(server)
        if not server_url:
            QMessageBox.warning(
                self, "Setup RDMA",
                "Could not resolve the selected server's URL.",
            )
            return

        from widgets.setup_rdma_dialog import SetupRdmaDialog
        dlg = SetupRdmaDialog(server_url, parent=self)
        dlg.exec_()

    # ─────────────────────────────────────────── Blast a RDMA Flow

    def show_rdma_blast_flow_dialog(self):
        """Open the Blast a RDMA Flow orchestrator dialog.

        Non-blocking via show() so the operator can fan out multiple
        RDMA blasts across NIC pairs in parallel — same shape as
        Blast a DPDK Flow.
        """
        server_tg, client_tg = self._resolve_two_servers_for_rdma(
            "Blast a RDMA Flow",
        )
        if server_tg is None:
            return
        server_url, server_label = self._server_url_label(server_tg)
        client_url, client_label = self._server_url_label(client_tg)
        if not server_url or not client_url:
            QMessageBox.warning(
                self, "Blast a RDMA Flow",
                "Could not resolve the selected servers' URLs.",
            )
            return

        from widgets.rdma_blast_flow_dialog import RdmaBlastFlowDialog
        dlg = RdmaBlastFlowDialog(
            server_url, client_url,
            server_tg_label=server_label,
            client_tg_label=client_label,
            parent=self,
        )

        # Same multi-dialog bookkeeping pattern as Blast DPDK Flow.
        if not hasattr(self, "_rdma_blast_dialogs"):
            self._rdma_blast_dialogs = []
        cascade_step = 36
        idx = len(self._rdma_blast_dialogs)
        if idx > 0:
            try:
                anchor = self.geometry().topLeft()
                dlg.move(
                    anchor.x() + 80 + cascade_step * idx,
                    anchor.y() + 80 + cascade_step * idx,
                )
            except Exception:
                pass

        # Sibling-device conflict guard — surface (tg_url, device)
        # pairs already claimed by other RDMA blast dialogs.
        def _siblings(excluding=dlg):
            claimed = set()
            for d in self._rdma_blast_dialogs:
                if d is excluding:
                    continue
                # Server side
                sd = d._server_device_combo.currentData() if hasattr(d, "_server_device_combo") else None
                if sd:
                    claimed.add((d._server_tg_url, sd))
                # Client side
                cd = d._client_device_combo.currentData() if hasattr(d, "_client_device_combo") else None
                if cd:
                    claimed.add((d._client_tg_url, cd))
            return claimed
        dlg.set_sibling_iface_provider(_siblings)

        self._rdma_blast_dialogs.append(dlg)

        def _on_closed(_result, _dlg=dlg):
            try:
                self._rdma_blast_dialogs.remove(_dlg)
            except ValueError:
                pass

        dlg.finished.connect(_on_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ─────────────────────────────────────────── RDMA Topology Test

    def show_rdma_topology_dialog(self):
        """Open the v0.4.0 RDMA Topology Test dialog — N×M perftest
        orchestrator (fan-in, fan-out, mesh, pairwise).

        Unlike Blast a RDMA Flow (which is 1:1), this dialog manages
        endpoint GROUPS + a topology shape, expanding to the right
        cross-product of perftest pairs. Aggregates stats across all
        pairs. See Help → Install Guide §10d for the Ixia comparison
        that motivates this feature.

        Non-blocking via show() — operator can run a topology stress
        test in parallel with other RDMA / DPDK dialogs."""
        from widgets.rdma_topology_dialog import RdmaTopologyDialog
        dlg = RdmaTopologyDialog(parent=self)

        # Pre-populate the endpoint editors with sensible starter
        # text using the currently-selected servers' URLs (if any) +
        # an mlx5_0 placeholder. Operator usually adjusts; this just
        # saves them from typing the very first line by hand.
        try:
            selected = self._selected_servers() or []
            if selected:
                lines = []
                for srv in selected:
                    url, _label = self._server_url_label(srv)
                    if url:
                        lines.append(f"{url} mlx5_0")
                if lines:
                    dlg._server_edit.setPlainText("\n".join(lines))
                    # And mirror for the client side as a starting
                    # point (loopback) — operator can edit.
                    dlg._client_edit.setPlainText("\n".join(lines))
        except Exception:
            # Pre-population is convenience, not contract — silently
            # skip if the selection API doesn't exist on this build.
            pass

        if not hasattr(self, "_rdma_topology_dialogs"):
            self._rdma_topology_dialogs = []
        self._rdma_topology_dialogs.append(dlg)

        def _on_closed(_result, _dlg=dlg):
            try:
                self._rdma_topology_dialogs.remove(_dlg)
            except ValueError:
                pass
        dlg.finished.connect(_on_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ─────────────────────────────────────────── RDMA Devices

    def show_rdma_devices_dialog(self):
        """List /api/rdma/devices on each selected server.

        Doesn't require a 1-or-2 selection — happy to enumerate
        across as many TGs as the operator selected. Useful pre-
        flight check before opening Blast a RDMA Flow.
        """
        servers = self._get_selected_servers()
        if not servers:
            QMessageBox.warning(
                self, "RDMA Devices",
                "Select one or more servers (TGs) in the server tree "
                "first, then open RDMA Devices.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("RDMA Devices")
        dialog.setGeometry(300, 300, 800, 540)
        layout = QVBoxLayout(dialog)

        header = QHBoxLayout()
        last = QLabel(f"Loaded {datetime.now().strftime('%H:%M:%S')}")
        last.setStyleSheet("color: #6b7280; font-size: 10px;")
        header.addWidget(last)
        header.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setMaximumWidth(90)
        header.addWidget(refresh)
        layout.addLayout(header)

        server_views = {}  # address → QTextEdit

        for server in servers:
            address = server.get("address", "") if isinstance(server, dict) else server
            tg_id = server.get("tg_id", "?") if isinstance(server, dict) else "?"
            row = QLabel(f"TG {tg_id} ({address}):")
            row.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(row)

            view = QTextEdit()
            view.setReadOnly(True)
            mono = QFont("Menlo")
            mono.setStyleHint(QFont.Monospace)
            view.setFont(mono)
            view.setMaximumHeight(200)
            view.setPlainText("Loading…")
            layout.addWidget(view)
            server_views[address] = view

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        def _kick_fetch():
            last.setText(f"Loading {datetime.now().strftime('%H:%M:%S')}…")
            for srv in servers:
                addr = srv.get("address", "") if isinstance(srv, dict) else srv
                if not addr:
                    continue
                view = server_views.get(addr)
                if view is None:
                    continue
                view.setPlainText("Loading…")

                W = _api_worker()
                worker = W("GET", f"{addr}/api/rdma/devices", timeout=5)
                # also probe perftest so we can show its install state.
                installed_worker = W(
                    "GET", f"{addr}/api/rdma/perftest/installed", timeout=5,
                )

                def _on_devs(data, err, v=view, a=addr, iw=installed_worker):
                    if err or not data:
                        v.setPlainText(f"Error: {err or 'no data'}")
                        return
                    devs = data.get("devices") or []
                    if not devs:
                        v.setPlainText(
                            "(no RDMA devices found on this TG — kernel "
                            "lacks RDMA support, or the container has no "
                            "/sys/class/infiniband mount)"
                        )
                        return
                    def _fmt_qp(n):
                        """v0.3.15: pretty-print HCA capability ceilings.
                        max_qp is typically 6 digits on modern Mellanox;
                        comma-separated reads better than raw."""
                        if n is None:
                            return "?"
                        if n >= 1_000_000:
                            return f"{n/1_000_000:.1f}M"
                        if n >= 1000:
                            return f"{n:,}"
                        return str(n)

                    lines = []
                    for d in devs:
                        # v0.3.16+: surface the kernel netdev name(s)
                        # alongside the abstract HCA ID. Without it
                        # operators can't correlate `mlx5_N` with
                        # their `ip link` output / IP config.
                        net_ifaces = d.get("net_ifaces") or []
                        iface_str = ("+".join(net_ifaces)
                                     if net_ifaces else "(no netdev)")
                        lines.append(
                            f"{d.get('name')}  "
                            f"iface={iface_str}  "
                            f"vendor={d.get('vendor') or '-'}  "
                            f"fw={d.get('fw_version') or '-'}"
                        )
                        # v0.3.15: HCA capability ceilings from ibv_devinfo
                        # (max_qp + friends). Reads "(?)" when ibv_devinfo
                        # isn't installed or perms blocked the probe.
                        max_qp = d.get("max_qp")
                        if max_qp is not None or d.get("max_cq") is not None:
                            lines.append(
                                f"  HCA caps:  max_qp={_fmt_qp(max_qp)}  "
                                f"max_qp_wr={_fmt_qp(d.get('max_qp_wr'))}  "
                                f"max_cq={_fmt_qp(d.get('max_cq'))}  "
                                f"max_mr={_fmt_qp(d.get('max_mr'))}  "
                                f"max_pd={_fmt_qp(d.get('max_pd'))}  "
                                f"max_sge={_fmt_qp(d.get('max_sge'))}"
                            )
                        else:
                            lines.append(
                                "  HCA caps:  (ibv_devinfo not available "
                                "— install rdma-core)"
                            )
                        for p in d.get("ports") or []:
                            gids = p.get("gids") or []
                            lines.append(
                                f"  port {p.get('port')}  "
                                f"state={p.get('state')}  "
                                f"link={p.get('link_layer')}  "
                                f"rate={p.get('rate')}  "
                                f"mtu={p.get('mtu')}B  "
                                f"gids={len(gids)}"
                            )
                            for gi, gid in enumerate(gids[:8]):
                                lines.append(f"      gid[{gi}]={gid}")
                            if len(gids) > 8:
                                lines.append(f"      … +{len(gids) - 8} more")
                    v.setPlainText("\n".join(lines))

                def _on_installed(data, err, v=view, a=addr):
                    if err or not data:
                        return
                    if not data.get("installed"):
                        v.append(
                            "\n[!] perftest tools NOT installed on this TG "
                            "(apt install perftest)."
                        )
                    else:
                        version = data.get("version") or "?"
                        v.append(f"\n[ok] perftest {version} present.")

                worker.done.connect(_on_devs)
                installed_worker.done.connect(_on_installed)
                self._track_rdma_worker(worker)
                self._track_rdma_worker(installed_worker)
                worker.start()
                installed_worker.start()
            last.setText(f"Loaded {datetime.now().strftime('%H:%M:%S')}")

        refresh.clicked.connect(_kick_fetch)
        _kick_fetch()
        dialog.show()
        # Don't exec() — non-modal so refresh/close are independent.

    # ─────────────────────────────────────────── RDMA Jobs

    def show_rdma_jobs_dialog(self):
        """List /api/rdma/perftest/jobs on each selected server."""
        servers = self._get_selected_servers()
        if not servers:
            QMessageBox.warning(
                self, "RDMA Jobs",
                "Select one or more servers (TGs) in the server tree "
                "first, then open RDMA Jobs.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("RDMA perftest Jobs")
        dialog.setGeometry(320, 320, 880, 540)
        layout = QVBoxLayout(dialog)
        header = QHBoxLayout()
        last = QLabel("Loading…")
        last.setStyleSheet("color: #6b7280; font-size: 10px;")
        header.addWidget(last)
        header.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setMaximumWidth(90)
        header.addWidget(refresh)
        layout.addLayout(header)

        views = {}
        for srv in servers:
            addr = srv.get("address", "") if isinstance(srv, dict) else srv
            tg_id = srv.get("tg_id", "?") if isinstance(srv, dict) else "?"
            row = QLabel(f"TG {tg_id} ({addr}):")
            row.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(row)
            view = QTextEdit()
            view.setReadOnly(True)
            mono = QFont("Menlo")
            mono.setStyleHint(QFont.Monospace)
            view.setFont(mono)
            view.setMaximumHeight(200)
            view.setPlainText("Loading…")
            layout.addWidget(view)
            views[addr] = view

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        def _kick():
            last.setText(f"Loading {datetime.now().strftime('%H:%M:%S')}…")
            for srv in servers:
                addr = srv.get("address", "") if isinstance(srv, dict) else srv
                if not addr:
                    continue
                view = views.get(addr)
                if view is None:
                    continue
                view.setPlainText("Loading…")
                W = _api_worker()
                worker = W("GET", f"{addr}/api/rdma/perftest/jobs", timeout=5)

                def _on_jobs(data, err, v=view):
                    if err or not data:
                        v.setPlainText(f"Error: {err or 'no data'}")
                        return
                    jobs = data.get("jobs") or []
                    if not jobs:
                        v.setPlainText("(no perftest jobs)")
                        return
                    lines = []
                    for j in jobs:
                        run = "RUN" if j.get("running") else f"done({j.get('returncode')})"
                        hsid = (j.get("handshake_id") or "")[:8] or "-"
                        is_lat = (j.get("test") or "").endswith("_lat")
                        if is_lat:
                            metric = (
                                f"lat avg={j.get('final_lat_avg_us')}µs "
                                f"p99={j.get('final_lat_p99_us')}"
                            )
                        else:
                            metric = (
                                f"BW avg={j.get('final_bw_avg_gbps')} Gbps "
                                f"Mpps={j.get('final_msg_rate_mpps')}"
                            )
                        lines.append(
                            f"{run}  {j.get('role')}  {j.get('test')}  "
                            f"dev={j.get('device')}  hsid={hsid}  "
                            f"job={j.get('job_id', '')[:8]}  {metric}"
                        )
                        if j.get("error"):
                            lines.append(f"   err: {j.get('error')[:160]}")
                    v.setPlainText("\n".join(lines))

                worker.done.connect(_on_jobs)
                self._track_rdma_worker(worker)
                worker.start()
            last.setText(f"Loaded {datetime.now().strftime('%H:%M:%S')}")

        refresh.clicked.connect(_kick)
        _kick()
        dialog.show()
