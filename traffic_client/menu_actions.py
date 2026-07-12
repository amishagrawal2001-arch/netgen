#menu_actions.py#
import json, os, requests
import subprocess
from urllib.parse import urlparse
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem, QAbstractItemView, QLabel
from PyQt5.QtWidgets import QTableWidgetItem
import uuid
from PyQt5.QtCore import Qt, QTimer
import logging

logger = logging.getLogger(__name__)


def _persist_current_session_path(path: str) -> None:
    """Save the active session file path to QSettings.

    v0.4.7: pre-fix, `_current_session_path` was set in memory but
    never persisted. Save As / Load From… became one-shot: after a
    restart the app silently snapped back to ``~/Documents/OSTG/
    session.json`` (the default), which is empty for first-time
    macOS-app users — operators saw their TGs / streams "disappear"
    on restart, when in fact the work was sitting at the path they'd
    Save As'd to and the app had forgotten.

    Called from Save As + Load From… so any deliberate user action
    that changes the active file gets persisted. Best-effort —
    a QSettings write failure logs and moves on; the operator's
    in-memory state is unaffected.
    """
    try:
        from PyQt5.QtCore import QSettings
        QSettings().setValue("current_session_path", path)
    except Exception as exc:
        logger.debug(
            f"[SESSION PATH] persist to QSettings failed: {exc}"
        )


def _next_tg_id(server_interfaces) -> int:
    """Return the next-available unique tg_id for a freshly-added server.

    v0.3.16 fix: previously every add-server callsite computed
    ``tg_id = _next_tg_id(self.server_interfaces)``. That breaks the moment the
    existing list isn't a contiguous 0-indexed range — which happens
    routinely:

      * Session JSON saved with only one server and tg_id=1 (or any
        non-zero), reloaded on next launch. ``len([{tg_id:1}]) = 1``;
        new server gets tg_id=1 too → both render as "TG 1".
      * After a remove: started with [{tg_id:0}, {tg_id:1}],
        removed the first → [{tg_id:1}]. ``len()`` = 1; new add
        collides with tg_id=1.

    Reported user-visibly as "two servers added but both labelled
    TG 1" (one operator screenshot). Fix is the obvious next-unique:
    max of existing + 1, with 0 fallback for an empty list. This also
    keeps the FIRST add starting at 0 unchanged for fresh installs.
    """
    if not server_interfaces:
        return 0
    used = []
    for s in server_interfaces:
        try:
            used.append(int(s.get("tg_id", 0)))
        except (TypeError, ValueError):
            continue
    if not used:
        return 0
    return max(used) + 1


def sanitize_for_json(obj):
    """Recursively convert non-serializable objects to JSON-safe formats."""
    # Check for PyQt objects first (they can't be serialized)
    if hasattr(obj, '__class__'):
        obj_type = str(type(obj))
        if 'PyQt5' in obj_type or 'QWidget' in obj_type or 'QTreeWidgetItem' in obj_type or 'QLabel' in obj_type:
            # Skip PyQt objects - return a placeholder
            return f"<PyQt-object: {type(obj).__name__}>"
    
    if isinstance(obj, dict):
        # Filter out PyQt objects from dictionaries
        result = {}
        for k, v in obj.items():
            if hasattr(v, '__class__'):
                v_type = str(type(v))
                if 'PyQt5' in v_type or 'QWidget' in v_type or 'QTreeWidgetItem' in v_type:
                    continue  # Skip PyQt objects
            result[k] = sanitize_for_json(v)
        return result
    elif isinstance(obj, list):
        # Filter out PyQt objects from lists
        result = []
        for v in obj:
            if hasattr(v, '__class__'):
                v_type = str(type(v))
                if 'PyQt5' in v_type or 'QWidget' in v_type or 'QTreeWidgetItem' in v_type:
                    continue  # Skip PyQt objects
            result.append(sanitize_for_json(v))
        return result
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    elif hasattr(obj, "text") and callable(getattr(obj, "text", None)):
        # Handles QLabel, QLineEdit, etc. - but check if it's PyQt first
        try:
            # Try calling text() without arguments
            return obj.text()
        except TypeError:
            # If it requires arguments (like QTreeWidgetItem.text(column)), skip it
            return f"<non-serializable: {type(obj).__name__}>"
    elif hasattr(obj, "__str__"):
        return str(obj)
    else:
        return f"<non-serializable: {type(obj).__name__}>"

class TrafficGenClientMenuAction():
    def _keepalive_worker(self, worker):
        """Thin instance-method shim over the process-global QThread
        keepalive registry (utils.qthread_keepalive.keep).

        Why a global registry rather than a per-window list / setParent:
        on PyQt5 5.15.11 + Python 3.14, a QThread whose last Python ref
        drops during the window between run() returning and Qt's internal
        teardown completing gets its C++ object deleted by PyQt's wrapper
        destructor while Qt still thinks the thread runs → "QThread:
        Destroyed while thread is still running" → SIGABRT. setParent()
        only fixes this when the worker shares the parent's thread
        affinity — workers spawned from a background threading.Thread
        (monitor callbacks) can't be parented to the main-thread window,
        so setParent no-ops there. A process-global strong ref is
        thread-agnostic and always wins. See utils/qthread_keepalive.py.
        """
        from utils.qthread_keepalive import keep
        keep(worker)

    def add_server_interface(self):
        """Add a TGEN chassis via the Spirent-style dialog.

        Replaces the legacy two-step QInputDialog flow (address, port).
        The dialog keeps a history of past connections at
        ~/.netgen/chassis_history.json so operators can pick a chassis
        with one click instead of re-typing the address. Auto-probes
        /api/health for each history row on open so the LED column
        shows live reachability.
        """
        try:
            from widgets.add_tgen_dialog import AddTGenDialog
        except Exception as e:
            # Fallback to the legacy prompt if the dialog can't load
            # (shouldn't happen — module is part of the wheel/AppImage).
            logger.error(f"AddTGenDialog import failed, falling back: {e}")
            return self._add_server_interface_legacy()

        dlg = AddTGenDialog(self)
        # v0.5.16: when operator clicks Reboot Physical Server, mark
        # the matching server offline IMMEDIATELY so periodic pollers
        # stop spamming the dead host while it reboots. Without this
        # the LED stays green for ~60s (until health-fail-count
        # threshold) and stats workers pile up issuing 7s-retrying
        # requests = intermittent UI freezing.
        try:
            dlg.server_rebooted.connect(self._on_server_rebooted)
        except Exception as e:
            logger.debug(f"server_rebooted connect failed: {e}")
        dlg.exec_()
        # chosen_connections is the multi-target result list. Empty when
        # the user added entries to history only (clicked "Add to History"
        # then Close) or cancelled outright.
        targets = getattr(dlg, "chosen_connections", None) or []
        if not targets:
            return

        existing = {server["address"] for server in self.server_interfaces}
        added, skipped, added_offline = [], [], []
        for entry in targets:
            full_url = entry.get("url")
            if not full_url:
                continue
            if full_url in existing:
                skipped.append(full_url)
                continue

            # connect_now=False entries come from the dialog's "Add to
            # TGen List" button — operator wants the chassis in the
            # tree but hasn't asked for a connection attempt yet.
            # Mark offline so the icon shows red until the periodic
            # health worker probes /api/health successfully. Anything
            # without the field (legacy callers, "Connect & Add",
            # "Connect Selected") defaults to online=True.
            connect_now = bool(entry.get("connect_now", True))
            online = connect_now

            tg_id = _next_tg_id(self.server_interfaces)
            server_entry = {"tg_id": tg_id, "address": full_url, "online": online}
            label = entry.get("label", "")
            if label:
                server_entry["label"] = label
            self.server_interfaces.append(server_entry)
            existing.add(full_url)
            if connect_now:
                added.append(full_url)
            else:
                added_offline.append(full_url)

            # Auth token (session-only — not persisted to disk) for /api/*
            auth = entry.get("auth_token", "")
            if auth:
                if not hasattr(self, "_server_auth_tokens"):
                    self._server_auth_tokens = {}
                self._server_auth_tokens[full_url] = auth

            # Forgive a prior remove
            if full_url in self.removed_servers:
                self.removed_servers.discard(full_url)
                logger.info(f"[ADD SERVER] Removed {full_url} from removed_servers (re-added)")

            if hasattr(self, "server_manager"):
                from utils.server_manager import ServerManager
                server_id = ServerManager._extract_server_id_from_url(full_url)
                self.server_manager.register_server(
                    server_id=server_id,
                    address=full_url,
                    tg_id=tg_id,
                    online=online,
                )
                logger.info(
                    f"[ADD SERVER] Registered server {server_id} in ServerManager "
                    f"(online={online})"
                )

        if added or added_offline:
            self.update_server_tree()
            self.save_server_interfaces()
            if added:
                logger.info(f"[ADD SERVER] Added {len(added)} chassis (online): {', '.join(added)}")
            if added_offline:
                logger.info(
                    f"[ADD SERVER] Added {len(added_offline)} chassis (offline, no connect): "
                    f"{', '.join(added_offline)}"
                )
            # v0.5.19: probe DPDK readiness for each new online server.
            # If not ready (libdpdk missing, no tx_worker, hugepages
            # unset, etc.), show a non-blocking suggestion to run
            # ★ Setup DPDK. Operator-driven feature: the original
            # bug was "operator finished install, didn't know DPDK
            # was a separate setup step, then was confused about why
            # Verify Installation showed all ✗". The banner closes
            # that gap.
            try:
                for url in added:
                    self._check_dpdk_and_suggest_setup(url)
            except Exception as e:
                logger.debug(f"[ADD SERVER] DPDK auto-detect failed: {e}")
        if skipped:
            QMessageBox.information(
                self, "Already connected",
                "These chassis were already in your active list and were "
                "skipped:\n\n• " + "\n• ".join(skipped),
            )

    def _check_dpdk_and_suggest_setup(self, server_url: str) -> None:
        """v0.5.19: probe /api/dpdk/status on a freshly-added server.
        If not ready, fire a non-blocking notification suggesting the
        operator run ★ Setup DPDK.

        Async — uses _DpdkApiWorker so the UI thread doesn't block on
        the probe. The notification appears via QMessageBox.information
        with "Setup Now" + "Dismiss" buttons.

        We're deliberately gentle: this fires ONCE per added server
        (no nagging), and dismissed-state isn't persisted (operator
        will get prompted again on next session start — by design,
        since DPDK might have been intentionally skipped).
        """
        from traffic_client.dpdk_menu_actions import _DpdkApiWorker
        try:
            w = _DpdkApiWorker(
                method="GET",
                url=f"{server_url.rstrip('/')}/api/dpdk/status",
                timeout=4,
            )
            w.done.connect(
                lambda data, err, url=server_url: self._on_dpdk_probe_for_suggest(
                    url, data, err
                )
            )
            w.setParent(self)
            # Keepalive — pattern from elsewhere in this file.
            if hasattr(self, "_keepalive_worker"):
                self._keepalive_worker(w)
            w.start()
        except Exception as e:
            logger.debug(f"[DPDK AUTODETECT] worker spawn failed for {server_url}: {e}")

    def _on_dpdk_probe_for_suggest(self, server_url: str, data, err) -> None:
        """v0.5.19 callback: decide whether to surface the Setup
        suggestion based on /api/dpdk/status. Skip silently on any
        error (the operator didn't ask for this probe; failing
        loudly would be hostile)."""
        if err:
            logger.debug(
                f"[DPDK AUTODETECT] probe failed for {server_url}: {err}"
            )
            return
        data = data or {}
        # Use the same is_dpdk_ready() helper the wizard uses.
        try:
            from utils.dpdk_orchestrator import is_dpdk_ready
            if is_dpdk_ready(data):
                logger.debug(
                    f"[DPDK AUTODETECT] {server_url} is DPDK-ready — no "
                    f"banner needed."
                )
                return
        except Exception as e:
            logger.debug(f"[DPDK AUTODETECT] is_dpdk_ready check failed: {e}")
            return
        # Not ready — surface the suggestion. List what's missing for
        # context (operator can decide if they care).
        missing = []
        if not data.get("dpdk_installed"):
            missing.append("DPDK libraries")
        if not data.get("tx_worker_exists"):
            missing.append("tx_worker binary")
        if not data.get("hugepages_configured"):
            missing.append("hugepages")
        if not data.get("iommu_enabled"):
            missing.append("IOMMU")
        if not data.get("vfio_pci_loaded"):
            missing.append("vfio-pci")
        missing_str = ", ".join(missing) if missing else "(unknown)"

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("DPDK Setup Suggested")
        msg.setText(
            f"<b>{server_url}</b> isn't configured for DPDK yet."
        )
        msg.setInformativeText(
            f"Missing: {missing_str}\n\n"
            f"DPDK is required for line-rate stream generation. "
            f"Without it, streams fall back to Scapy / kernel path "
            f"(slower).\n\n"
            f"Run ★ Setup DPDK now? Takes ~5-15 min on a fresh "
            f"host, mostly building the DPDK source. You can also "
            f"skip and run it later from Tools → DPDK → ★ Setup DPDK."
        )
        setup_btn = msg.addButton("Setup Now", QMessageBox.AcceptRole)
        msg.addButton("Skip — set up later", QMessageBox.RejectRole)
        msg.setDefaultButton(setup_btn)
        msg.exec_()
        if msg.clickedButton() is setup_btn:
            # Select this server in the tree first so the dialog
            # targets it. The wizard reads from _get_selected_servers.
            try:
                self._select_server_in_tree(server_url)
            except Exception:
                pass
            try:
                self.show_dpdk_make_ready_dialog()
            except Exception as e:
                logger.error(f"[DPDK AUTODETECT] failed to open Setup: {e}")

    def _select_server_in_tree(self, server_url: str) -> None:
        """v0.5.19 helper: find the row in self.server_tree matching
        server_url (col 1 has the URL) and select it so the next
        menu action targets this server."""
        if not hasattr(self, "server_tree"):
            return
        for i in range(self.server_tree.topLevelItemCount()):
            item = self.server_tree.topLevelItem(i)
            if item.text(1) == server_url:
                self.server_tree.setCurrentItem(item)
                return

    def _on_server_rebooted(self, host_port: str) -> None:
        """v0.5.16: AddTGenDialog signaled that /api/system/reboot
        returned 200 for `host_port`. Mark the matching server in
        server_interfaces as offline IMMEDIATELY so:

          * status LED flips red right away (operator sees the
            server is going down — not still-green-and-lying)
          * periodic stats / health pollers skip the dead host
            (their filter is `online=True`), so workers stop
            piling up issuing 7s-retrying requests against a
            host that's about to disappear for 3-5 minutes
        """
        if not host_port:
            return
        try:
            servers = getattr(self, "server_interfaces", []) or []
        except Exception:
            return
        # The host_port format from the dialog is "<addr>:<port>".
        # server_interfaces[i]["address"] is the full URL
        # ("http://<addr>:<port>"). Match via substring.
        target = host_port.strip()
        flipped = 0
        for srv in servers:
            addr = srv.get("address") or ""
            if target in addr:
                srv["online"] = False
                srv["health"] = None
                srv["health_fail_count"] = 0  # reset so post-reboot
                                              # health probes start fresh
                try:
                    if hasattr(self, "update_server_status_icon"):
                        self.update_server_status_icon(srv, False)
                except Exception as e:
                    logger.debug(
                        f"[REBOOT] status icon update failed for "
                        f"{addr}: {e}"
                    )
                flipped += 1
        if flipped:
            logger.info(
                f"[REBOOT] Marked {flipped} server(s) offline for "
                f"{host_port} — pollers will skip until reachability returns"
            )

    def _add_server_interface_legacy(self):
        """Fallback path: bare two-step input dialog.

        Kept for environments where widgets/add_tgen_dialog.py fails to
        import (corrupted install, custom strip-down). Same logic as the
        pre-0.2.7 implementation.
        """
        server_url, ok = QInputDialog.getText(self, "Add Server", "Enter Server Address (e.g., 127.0.0.1):")
        if not ok or not server_url.strip():
            return

        port, ok = QInputDialog.getText(self, "Add Port", "Enter Port (default: 5050):")
        port = port.strip() if port else "5050"

        try:
            full_url = f"http://{server_url.strip()}:{int(port)}"
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a valid number.")
            return

        if full_url not in [server["address"] for server in self.server_interfaces]:
            tg_id = _next_tg_id(self.server_interfaces)
            server_entry = {"tg_id": tg_id, "address": full_url, "online": True}
            self.server_interfaces.append(server_entry)
            if full_url in self.removed_servers:
                self.removed_servers.discard(full_url)
            if hasattr(self, "server_manager"):
                from utils.server_manager import ServerManager
                server_id = ServerManager._extract_server_id_from_url(full_url)
                self.server_manager.register_server(
                    server_id=server_id, address=full_url, tg_id=tg_id, online=True,
                )
            self.update_server_tree()
            self.save_server_interfaces()
        else:
            QMessageBox.warning(self, "Duplicate Server", "This server is already added.")
    def remove_selected_server(self):
        """Remove the currently selected server(s) from the tree."""
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a server to remove.")
            return

        for item in selected_items:
            if item.parent() is None:  # Ensure it's a top-level item (server)
                server_address = item.text(1)  # Server address column
                tg_id_text = item.text(0)  # TG ID column (string, may be "TG 1" or "1")

                # Normalize tg_id to the bare integer the rest of the
                # codebase uses for cache keys ("TG 1 - eth0").
                tg_id_numeric = None
                # Try tree text first
                try:
                    tg_id_numeric = int(str(tg_id_text).replace("TG ", "").strip())
                except (ValueError, TypeError):
                    pass
                # Fallback: look up by server_address in server_interfaces
                if tg_id_numeric is None:
                    for s in self.server_interfaces:
                        if s.get("address") == server_address:
                            try:
                                tg_id_numeric = int(s.get("tg_id"))
                            except (ValueError, TypeError):
                                pass
                            break

                # Add server to removed_servers set
                self.removed_servers.add(server_address)

                # Remove the server and its ports from the server interfaces
                # Update ServerManager if available
                if hasattr(self, "server_manager"):
                    from utils.server_manager import ServerManager
                    server_id = ServerManager._extract_server_id_from_url(server_address)
                    self.server_manager.unregister_server(server_id)
                    logger.info(f"[REMOVE SERVER] Unregistered server {server_id} from ServerManager")

                self.server_interfaces = [
                    server for server in self.server_interfaces if server["address"] != server_address
                ]

                # Remove related entries from removed_interfaces
                self.removed_interfaces = {
                    port for port in self.removed_interfaces if not port.startswith(f"{tg_id_text} - ")
                }

                # Remove the selected server from selected_servers if applicable
                self.selected_servers = [
                    server for server in self.selected_servers if server["address"] != server_address
                ]

                # Remove the server item from the tree
                index = self.server_tree.indexOfTopLevelItem(item)
                self.server_tree.takeTopLevelItem(index)

                # Purge associated rows from the Traffic Statistics +
                # Stream Statistics tables. Without this they linger
                # until the next refresh tick (or forever in the cached
                # _last_statistics fallback path), so the operator sees
                # ghost rows for a chassis they just removed.
                if hasattr(self, "prune_server_stats"):
                    try:
                        self.prune_server_stats(server_address, tg_id_numeric)
                    except Exception as e:
                        logger.warning(
                            f"[REMOVE SERVER] prune_server_stats failed for {server_address}: {e}"
                        )

                logger.info(f"Removed server: {server_address} and all associated ports.")

        # Session save removed - only save on explicit user action (Save Session menu or Apply button)
        self.save_server_interfaces()

        # QMessageBox.information(self, "Server Removed", "Selected server(s) and associated ports removed successfully.")
    
    def readd_servers_dialog(self):
        """Display a dialog to re-add removed servers."""
        if not self.removed_servers:
            QMessageBox.information(self, "No Removed Servers", "No servers have been removed.")
            return
            
        dialog = QDialog(self)
        dialog.setWindowTitle("Re-add Servers")
        dialog.setGeometry(300, 300, 500, 300)

        layout = QVBoxLayout(dialog)

        # List widget to display removed servers with checkboxes
        list_widget = QListWidget()
        list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
        layout.addWidget(list_widget)

        # Populate the list widget with removed servers
        for server_address in sorted(self.removed_servers):
            item = QListWidgetItem(server_address)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            list_widget.addItem(item)

        # Confirm and Cancel buttons
        button_layout = QHBoxLayout()
        confirm_button = QPushButton("Re-add Selected Servers")
        confirm_button.clicked.connect(lambda: self.readd_servers(list_widget, dialog))
        button_layout.addWidget(confirm_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        dialog.exec()
    
    def readd_servers(self, list_widget, dialog):
        """Re-add the selected servers from the dialog."""
        readded_servers = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                server_address = item.text().strip()
                
                # Remove from removed_servers set
                self.removed_servers.discard(server_address)
                
                # Add back to server_interfaces with a new TG ID
                tg_id = _next_tg_id(self.server_interfaces)  # Assign the next available TG ID
                server_entry = {"tg_id": tg_id, "address": server_address, "online": True}
                self.server_interfaces.append(server_entry)
                
                # Update ServerManager if available
                if hasattr(self, "server_manager"):
                    from utils.server_manager import ServerManager
                    server_id = ServerManager._extract_server_id_from_url(server_address)
                    self.server_manager.register_server(
                        server_id=server_id,
                        address=server_address,
                        tg_id=tg_id,
                        online=True
                    )
                    logger.info(f"[READD SERVER] Registered server {server_id} in ServerManager")
                
                readded_servers.append(server_address)

        if readded_servers:
            logger.info(f"Re-added servers: {readded_servers}")
            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            self.update_server_tree()  # Update the server tree
            QMessageBox.information(self, "Servers Re-added", f"Re-added servers: {', '.join(readded_servers)}")
        else:
            QMessageBox.information(self, "No Servers Selected", "No servers were selected to re-add.")

        dialog.accept()
    def load_server_interfaces(self):
        """Load server interfaces from a file and assign TG IDs."""
        try:
            from utils.path_utils import get_ostg_data_directory
            data_dir = get_ostg_data_directory()
            server_file = os.path.join(data_dir, "server_interfaces.txt")
            
            with open(server_file, "r") as f:
                servers = [line.strip() for line in f.readlines()]
            self.server_interfaces = [{"tg_id": i, "address": server} for i, server in enumerate(servers)]
            logger.info(f"Loaded servers: {self.server_interfaces}")
        except FileNotFoundError:
            logger.info("server_interfaces.txt not found. Starting with an empty server list.")
            self.server_interfaces = []
    def save_server_interfaces(self):
        """Save the server interfaces to a file."""
        try:
            from utils.path_utils import get_ostg_data_directory
            data_dir = get_ostg_data_directory()
            server_file = os.path.join(data_dir, "server_interfaces.txt")
            
            with open(server_file, "w") as f:
                for server in self.server_interfaces:
                    f.write(f"{server['address']}\n")
            logger.info("Server interfaces saved successfully.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not save server interfaces: {e}")

    # ---------- Settings dialog ----------
    # Lightweight QSettings-backed preferences. Persists to:
    #   macOS:  ~/Library/Preferences/com.netgen.netgen-client.plist
    #   Linux:  ~/.config/netgen/netgen-client.conf
    # via the (organization, application) tuple set in QApplication
    # bootstrap. Read with:
    #   from PyQt5.QtCore import QSettings
    #   QSettings().value("monitor_poll_interval", 30, type=int)
    SETTINGS_DEFAULTS = {
        "monitor_poll_interval": 30,       # seconds; min 5, max 300
        "default_bgp_asn":       65000,    # initial value for Add Device → BGP
        "default_ipv4_base":     "192.168.0.2",
        "default_loopback_ipv4": "192.255.0.1",
    }

    def open_settings_dialog(self):
        """File → Settings… — QSettings-backed preferences dialog."""
        from PyQt5.QtWidgets import (
            QDialog, QFormLayout, QSpinBox, QLineEdit, QDialogButtonBox,
            QLabel, QVBoxLayout,
        )
        from PyQt5.QtCore import QSettings

        s = QSettings()
        dlg = QDialog(self)
        dlg.setWindowTitle("Netgen — Settings")
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        intro = QLabel(
            "Application preferences. Changes apply on OK; some take effect "
            "on next refresh."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #6b7280; font-size: 11px; padding-bottom: 6px;")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)

        # Monitor poll interval
        poll_input = QSpinBox()
        poll_input.setRange(5, 300)
        poll_input.setSuffix(" s")
        poll_input.setValue(int(s.value(
            "monitor_poll_interval",
            self.SETTINGS_DEFAULTS["monitor_poll_interval"],
            type=int,
        )))
        poll_input.setToolTip("How often the Devices tab polls /api/monitors/health")
        form.addRow("Monitor poll interval:", poll_input)

        # Default BGP ASN.
        # v0.3.11 crash fix: was QSpinBox.setRange(1, 4_294_967_295)
        # which throws OverflowError on Qt5 (QSpinBox stores int32 —
        # max 2_147_483_647). Settings dialog wouldn't even open.
        # Switched to QLineEdit + regex so we can support the full
        # BGP 4-byte ASN range (1..2^32-1) RFC 6793 allows.
        from PyQt5.QtCore import QRegExp
        from PyQt5.QtGui import QRegExpValidator
        _ASN_MAX = 4_294_967_295   # 2^32 - 1, BGP 4-byte AS upper bound
        # Read as STRING (not type=int) — QSettings.value(..., type=int)
        # routes through Qt's signed int32 conversion and silently
        # truncates values > 2_147_483_647 to -1. Python int() handles
        # the full range without overflow.
        _saved_asn = s.value(
            "default_bgp_asn",
            str(self.SETTINGS_DEFAULTS["default_bgp_asn"]),
        )
        try:
            _saved_asn_int = int(_saved_asn)
        except (TypeError, ValueError):
            _saved_asn_int = self.SETTINGS_DEFAULTS["default_bgp_asn"]
        asn_input = QLineEdit(str(_saved_asn_int))
        # 1–10 digits — keystroke filter; range is enforced in _apply
        # because regex can't express "<=4294967295" cleanly.
        asn_input.setValidator(QRegExpValidator(QRegExp(r"\d{1,10}")))
        asn_input.setToolTip(
            "Pre-filled in the Add Device → BGP form. "
            "Accepts 1..4,294,967,295 (BGP 4-byte ASN per RFC 6793)."
        )
        form.addRow("Default BGP ASN:", asn_input)

        # Default IPv4 base address
        ipv4_input = QLineEdit(str(s.value(
            "default_ipv4_base",
            self.SETTINGS_DEFAULTS["default_ipv4_base"],
        )))
        ipv4_input.setPlaceholderText("e.g. 192.168.0.2")
        form.addRow("Default IPv4 base:", ipv4_input)

        # Default loopback IPv4
        lo_input = QLineEdit(str(s.value(
            "default_loopback_ipv4",
            self.SETTINGS_DEFAULTS["default_loopback_ipv4"],
        )))
        lo_input.setPlaceholderText("e.g. 192.255.0.1")
        form.addRow("Default loopback IPv4:", lo_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            | QDialogButtonBox.RestoreDefaults
        )
        layout.addWidget(buttons)

        def _apply():
            s.setValue("monitor_poll_interval", poll_input.value())
            # Parse + clamp the ASN to the legal 4-byte range. Regex
            # validator already filtered to digits, but extreme
            # values (>2^32-1) still possible if operator types 10
            # nines. Fall back to default on parse failure (which
            # shouldn't happen given the validator, but be safe).
            try:
                _asn_val = int(asn_input.text() or 0)
            except ValueError:
                _asn_val = self.SETTINGS_DEFAULTS["default_bgp_asn"]
            _asn_val = max(1, min(_asn_val, _ASN_MAX))
            # Store as STRING for the same reason we READ as string:
            # writing a Python int > 2^31-1 lands as QVariant.Int
            # which truncates to -1 on the next read.
            s.setValue("default_bgp_asn", str(_asn_val))
            s.setValue("default_ipv4_base", ipv4_input.text().strip() or self.SETTINGS_DEFAULTS["default_ipv4_base"])
            s.setValue("default_loopback_ipv4", lo_input.text().strip() or self.SETTINGS_DEFAULTS["default_loopback_ipv4"])
            s.sync()
            # Push the poll-interval change to the monitor-health timer
            # if it's wired up on the Devices tab.
            try:
                if hasattr(self, "devices_tab") and hasattr(
                    self.devices_tab, "_monitor_health_timer"
                ):
                    self.devices_tab._monitor_health_timer.setInterval(
                        max(5_000, poll_input.value() * 1000)
                    )
            except Exception as exc:
                logger.debug(f"[SETTINGS] could not retune monitor timer: {exc}")
            dlg.accept()

        def _restore():
            poll_input.setValue(self.SETTINGS_DEFAULTS["monitor_poll_interval"])
            asn_input.setText(str(self.SETTINGS_DEFAULTS["default_bgp_asn"]))
            ipv4_input.setText(self.SETTINGS_DEFAULTS["default_ipv4_base"])
            lo_input.setText(self.SETTINGS_DEFAULTS["default_loopback_ipv4"])

        buttons.accepted.connect(_apply)
        buttons.rejected.connect(dlg.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(_restore)

        dlg.exec_()

    def export_devices_to_file(self):
        """File → Export Devices: dump the selected server's device
        configurations to a user-chosen JSON file. Strips runtime
        state (ARP / BGP / OSPF status, lease info, container_id) on
        the server side so the file represents intent."""
        from PyQt5.QtWidgets import QFileDialog
        import requests, json as _json
        server_url = None
        # Prefer the active server URL; fall back to the first
        # registered one.
        if hasattr(self, "server_manager") and self.server_manager:
            try:
                server_url = self.server_manager.get_server_url()
            except Exception:
                server_url = None
        if not server_url:
            QMessageBox.warning(self, "No Server", "Select a server first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Devices", "devices.json", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            r = requests.get(f"{server_url}/api/devices/export", timeout=15)
            if r.status_code != 200:
                QMessageBox.critical(
                    self, "Export Failed",
                    f"Server returned HTTP {r.status_code}:\n{r.text[:300]}",
                )
                return
            payload = r.json()
            with open(path, "w") as f:
                _json.dump(payload, f, indent=2)
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {payload.get('count', 0)} device(s) to:\n{path}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def import_devices_from_file(self):
        """File → Import Devices: read a JSON file produced by Export
        and apply each entry via /api/devices/import on the selected
        server. Shows a summary of imported/failed."""
        from PyQt5.QtWidgets import QFileDialog
        import requests, json as _json
        server_url = None
        if hasattr(self, "server_manager") and self.server_manager:
            try:
                server_url = self.server_manager.get_server_url()
            except Exception:
                server_url = None
        if not server_url:
            QMessageBox.warning(self, "No Server", "Select a server first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Devices", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                payload = _json.load(f)
            if not isinstance(payload, dict) or "devices" not in payload:
                QMessageBox.critical(
                    self, "Bad File",
                    "Expected a JSON object with a 'devices' array — "
                    "see /api/devices/export for the format.",
                )
                return
            r = requests.post(
                f"{server_url}/api/devices/import", json=payload,
                timeout=300,  # batches can take a while
            )
            if r.status_code != 200:
                QMessageBox.critical(
                    self, "Import Failed",
                    f"Server returned HTTP {r.status_code}:\n{r.text[:300]}",
                )
                return
            result = r.json()
            imported = result.get("imported", 0)
            failed = result.get("failed", 0)
            errors = result.get("errors", []) or []
            msg = f"Imported {imported} device(s). Failed: {failed}."
            if errors:
                msg += "\n\nFirst errors:\n• " + "\n• ".join(errors[:5])
            box = QMessageBox.information if failed == 0 else QMessageBox.warning
            box(self, "Import Complete", msg)
            # Refresh the device tree so the new rows show up.
            if hasattr(self, "update_server_tree"):
                self.update_server_tree()
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", str(exc))

    def save_session(self, blocking: bool = False, manual: bool = False):
        """Save the current session to a JSON file.

        Args:
            blocking: When True, perform the save synchronously on the caller thread.
                      When False (default), run the save in a background QThread.
            manual: When True, the call originated from a deliberate user
                action (Ctrl+S, Save As, Load From… side-effects). Manual
                saves ALWAYS run, regardless of the auto-save preference.
                When False (default), this is an auto-save triggered by
                an edit handler — gated behind `self._auto_save_enabled`
                so the user can opt out of every-edit-writes-to-disk
                behaviour without each edit site needing to know.

                Old behaviour: every BGP/OSPF/ISIS/DHCP/VXLAN apply,
                every stream remove, every inline-edit fired
                save_session() and the throttled trailing-edge
                eventually wrote to disk. That meant
                "I removed a row to experiment" silently committed
                the deletion to the active session file. v0.3.11
                makes that opt-in: the operator gates auto-saves via
                File → Auto-save Session.
        """
        import traceback
        import time
        # Starting save_session()

        # v0.3.11: auto-save opt-in gate. Default False — the operator
        # explicitly toggles File → Auto-save Session to bring back the
        # historical edit-triggered-save behaviour. Manual saves
        # (Ctrl+S, Save As…) bypass the gate. Logged once at DEBUG so
        # the trailing-save loop doesn't spam the log every 2 s.
        if not manual and not getattr(self, "_auto_save_enabled", False):
            # Mark the session dirty so the closeEvent prompt fires
            # and the title-bar asterisk appears — without this the
            # operator has no signal that there are pending edits
            # the gate suppressed. Set BEFORE the early return.
            self._has_unsaved_edits = True
            try:
                self._update_window_title()
            except Exception:
                pass
            logger.debug(
                "[SAVE SESSION] auto-save disabled; skipping "
                "edit-triggered save. Use Ctrl+S to save manually."
            )
            return

        current_time = time.time()
        # Prevent new async saves while shutting down; blocking saves still allowed
        if getattr(self, "_is_closing", False) and not blocking:
            return
        
        # Check if another save is already in progress
        # Use a lock to prevent race conditions
        if not hasattr(self, '_save_lock'):
            from threading import Lock
            self._save_lock = Lock()
        
        with self._save_lock:
            in_progress = getattr(self, '_save_in_progress', False)
            worker_exists = hasattr(self, '_save_worker') and self._save_worker is not None
            worker_running = False
            if worker_exists:
                try:
                    # Double-check worker is still valid (might have been deleted)
                    if self._save_worker is not None:
                        worker_running = self._save_worker.isRunning()
                except RuntimeError:
                    # Worker was deleted, treat as not running
                    worker_running = False
                    self._save_worker = None
                    worker_exists = False
        
        if in_progress or worker_running:
            if blocking:
                # Wait for the existing background worker to finish before proceeding
                if worker_exists and worker_running and self._save_worker is not None:
                    logger.info("[SAVE SESSION] Waiting for existing background save to finish (blocking request)...")
                    self._save_worker.quit()  # Request thread to stop
                    if not self._save_worker.wait(5000):
                        logger.warning("[SAVE SESSION WARNING] Background save did not finish within timeout; forcing termination.")
                        self._save_worker.terminate()
                        self._save_worker.wait(1000)
                if worker_exists and self._save_worker is not None:
                    try:
                        if not self._save_worker.isRunning():
                            self._save_worker.deleteLater()
                    except RuntimeError:
                        pass
                    self._save_worker = None
                self._save_in_progress = False
            else:
                # Non-blocking call while a save is already in progress.
                # Don't silently drop — flag the session as dirty so the
                # current save's finished handler kicks off one more.
                # This implements the trailing-edge of a "fire once now,
                # fire once at the end" coalesce: rapid bursts during
                # auto-start, multi-cell edits, etc. collapse into at
                # most two writes regardless of how many triggers fire.
                self._save_pending = True
                return

        # Throttle rapid save calls (only for non-blocking saves).
        # Widened from 500ms to 2s because the previous window let
        # auto-start's stream-state cascade through two complete saves
        # ~350ms apart — both expensive (ServerManager rebuild + JSON
        # write). Throttled calls now arm a single deferred follow-up
        # via the dirty flag instead of being silently dropped.
        SAVE_THROTTLE_S = 2.0
        if not blocking:
            if hasattr(self, '_last_save_time') and (current_time - self._last_save_time) < SAVE_THROTTLE_S:
                self._save_pending = True
                self._schedule_trailing_save()
                return
        # Update timestamp for throttling (both blocking and non-blocking)
        self._last_save_time = current_time
        # Clear pending — we're about to satisfy it.
        self._save_pending = False
        
        # Re-evaluate worker flags after potential cleanup above
        with self._save_lock:
            worker_exists = hasattr(self, '_save_worker') and self._save_worker is not None
            worker_running = False
            if worker_exists:
                try:
                    if self._save_worker is not None:
                        worker_running = self._save_worker.isRunning()
                except RuntimeError:
                    worker_running = False
                    self._save_worker = None
                    worker_exists = False
        
        # CRITICAL: Extract PyQt widget data in main thread before starting worker
        # PyQt widgets cannot be accessed from worker threads
        protocol_data = {}
        if hasattr(self, "devices_tab") and self.devices_tab is not None:
            if hasattr(self.devices_tab, "bgp_table"):
                protocol_data["bgp"] = self._extract_table_data(self.devices_tab.bgp_table)
            if hasattr(self.devices_tab, "ospf_table"):
                protocol_data["ospf"] = self._extract_table_data(self.devices_tab.ospf_table)
            if hasattr(self.devices_tab, "isis_table"):
                protocol_data["isis"] = self._extract_table_data(self.devices_tab.isis_table)
            if hasattr(self.devices_tab, "dhcp_table"):
                protocol_data["dhcp"] = self._extract_table_data(self.devices_tab.dhcp_table)
        
        # If we're running a blocking save during shutdown, wait for any existing worker
        if blocking:
            try:
                if worker_exists and worker_running and self._save_worker is not None:
                    logger.info("[SAVE SESSION] Waiting for existing background save to finish...")
                    self._save_worker.quit()  # Request thread to stop
                    if not self._save_worker.wait(5000):
                        logger.warning("[SAVE SESSION WARNING] Background save did not finish within timeout; forcing termination.")
                        self._save_worker.terminate()
                        self._save_worker.wait(1000)
                if worker_exists and self._save_worker is not None:
                    try:
                        if not self._save_worker.isRunning():
                            self._save_worker.deleteLater()
                    except RuntimeError:
                        pass
                    self._save_worker = None
                self._save_in_progress = True
                success, message = self._save_session_impl(protocol_data)
            except Exception as e:
                traceback.print_exc()
                success, message = False, str(e)
            finally:
                self._save_in_progress = False
            if success:
                # Same hash-skip handling as the async path — keep
                # no-op blocking saves (close-handler tick on an
                # already-clean session) silent.
                if message == "no changes — skipped":
                    logger.debug(f"[SAVE SESSION] {message}")
                else:
                    logger.info(f"[SAVE SESSION] {message}")
                # v0.3.11: blocking-save success also clears the
                # unsaved-edits marker (mirror of the async
                # _on_save_finished path) — closeEvent prompt and
                # title asterisk go away.
                self._has_unsaved_edits = False
                try:
                    self._update_window_title()
                except Exception:
                    pass
            else:
                logger.error(f"[SAVE SESSION ERROR] {message}")
            return success, message

        self._save_in_progress = True
        # v0.3.11: title bar shows the dirty marker (`*`) for the
        # duration of the in-flight save. _on_save_finished clears it
        # when the worker reports done.
        try:
            self._update_window_title()
        except Exception:
            pass

        # Run save operation in separate thread to avoid blocking UI
        from PyQt5.QtCore import QThread, pyqtSignal
        
        class SaveSessionWorker(QThread):
            finished = pyqtSignal(bool, str)  # success, message
            
            def __init__(self, main_window, protocol_data):
                super().__init__()
                self.main_window = main_window
                self.protocol_data = protocol_data
            
            def run(self):
                """Run the actual save operation in this thread."""
                try:
                    success, message = self.main_window._save_session_impl(self.protocol_data)
                    self.finished.emit(success, message)
                except Exception as e:
                    self.finished.emit(False, str(e))
        
        # Start the save worker thread
        # CRITICAL: Check if worker exists and is valid before checking isRunning()
        # The worker might have been deleted via deleteLater() but not yet cleaned up
        # Clean up previous worker if it exists and is finished
        with self._save_lock:
            if worker_exists and not worker_running:
                try:
                    # Previous worker finished, ensure it's fully stopped before cleanup
                    worker = self._save_worker
                    self._save_worker = None  # Clear reference first
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    if not worker.isRunning():
                        worker.deleteLater()
                except RuntimeError:
                    # Object already deleted, ignore
                    pass
                except Exception as exc:
                    logger.info(f"[SAVE SESSION] Error cleaning up previous worker: {exc}")
            
            # CRITICAL: Don't create a new worker if one is already running
            # This prevents multiple workers from being created during rapid save calls
            if worker_exists and worker_running:
                # Worker already running, skip creating a new one
                logger.info("[SAVE SESSION] Save worker already running, skipping duplicate save request")
                return True, "Save already in progress"
            
            # Create new worker if needed
            if not worker_exists or self._save_worker is None:
                self._save_worker = SaveSessionWorker(self, protocol_data)
                # CRITICAL: Set parent to ensure proper cleanup
                self._save_worker.setParent(self)
                # CRITICAL: Connect finished signal to our handler (NOT deleteLater - handle cleanup manually)
                self._save_worker.finished.connect(self._on_save_finished)
                self._save_worker.start()
            else:
                # Worker already running, just mark as in progress
                pass
    
    def _on_save_finished(self, success, message):
        """Handle save completion (called from worker thread via signal)."""
        self._save_in_progress = False
        if success:
            # The "no changes" sentinel comes from the content-hash
            # short-circuit in _save_session_impl — log it at debug so
            # the trailing-edge no-ops don't show up in the console.
            if message == "no changes — skipped":
                logger.debug(f"[SAVE SESSION] {message}")
            else:
                logger.info(f"[SAVE SESSION] {message}")
                # v0.3.11: post a brief status-bar toast on real saves
                # only (skip the hash-no-op so the toast doesn't fire
                # for the trailing-edge tick that wrote nothing). The
                # summary fields are populated by _save_session_impl
                # at write time so we don't re-walk the structures.
                self._post_save_toast()
            # v0.3.11: a successful write clears the unsaved-edits
            # marker — closeEvent prompt and title asterisk go away.
            # The hash-no-op path also counts as "in sync with disk",
            # so clear in both branches.
            self._has_unsaved_edits = False
        else:
            logger.error(f"[SAVE SESSION ERROR] {message}")
        # v0.3.11: dirty marker reflects in-flight/pending saves +
        # the unsaved-edits flag — refresh here on completion so the
        # title-bar asterisk re-renders against the new state.
        try:
            self._update_window_title()
        except Exception:
            pass
        
        # CRITICAL: Properly clean up the worker thread
        # Wait for thread to finish, then schedule deletion
        if hasattr(self, '_save_worker') and self._save_worker is not None:
            worker = self._save_worker
            self._save_worker = None  # Clear reference immediately to prevent reuse
            
            # Ensure thread has finished before scheduling deletion
            if worker.isRunning():
                # Thread should have finished by now, but wait just in case
                worker.wait(100)  # Short wait to ensure thread is done
            
            # Schedule deletion in main thread
            from PyQt5.QtCore import QTimer
            def cleanup_worker():
                try:
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    worker.deleteLater()
                except RuntimeError:
                    # Worker already deleted, ignore
                    pass
            
            QTimer.singleShot(50, cleanup_worker)

        # Trailing-edge of the coalesce: if anyone called save_session()
        # while this one was in flight (or got throttled), honour that
        # request now by scheduling exactly one follow-up. The throttle
        # window in save_session() prevents a tight loop here.
        if getattr(self, "_save_pending", False):
            self._save_pending = False
            self._schedule_trailing_save()

    def _schedule_trailing_save(self, delay_ms: int = 2100):
        """Arm a single deferred save call, honouring the throttle window.

        Multiple rapid triggers collapse into one timer — if the timer
        is already armed, this is a no-op. The delay is just over the
        throttle window so the trailing save isn't itself re-throttled.
        Used both when save_session() is called while a save is in
        flight, and when it's called inside the rate-limit window.
        """
        if getattr(self, "_is_closing", False):
            return
        if getattr(self, "_trailing_save_armed", False):
            return
        self._trailing_save_armed = True
        from PyQt5.QtCore import QTimer

        def _fire():
            self._trailing_save_armed = False
            # _is_closing may have flipped while the timer was pending.
            if getattr(self, "_is_closing", False):
                return
            try:
                self.save_session()
            except Exception as exc:
                logger.debug(f"[SAVE SESSION] trailing-save fired with error: {exc}")

        QTimer.singleShot(delay_ms, _fire)

    # ─────────────────────────── v0.3.11: Save As / Load From / Recent

    def save_session_as(self):
        """File menu → Save Session As… — pick a destination, write
        the session there, and make that path the new active session
        so subsequent Ctrl+S writes to it too.

        Blocking save so the file is on disk before we update the
        recent-sessions list and refresh the title — async would leak
        the trio (active path, file contents, recent list) out of
        sync if the worker thread finishes after the menu rebuilds.
        """
        from PyQt5.QtWidgets import QFileDialog
        from utils.path_utils import get_session_file_path
        start_dir = (
            getattr(self, "_current_session_path", None)
            or get_session_file_path()
        )
        path, _filt = QFileDialog.getSaveFileName(
            self, "Save Session As",
            start_dir,
            "Session JSON (*.json);;All files (*)",
        )
        if not path:
            return  # user cancelled
        # Ensure .json suffix — Qt's file dialog doesn't enforce it
        # cross-platform, and a missing suffix breaks the file-picker
        # filter on the next Load From… (the operator types "stress"
        # and gets no JSON file in the list).
        if not path.lower().endswith(".json"):
            path = path + ".json"

        self._current_session_path = path
        # v0.4.7: persist the active session path to QSettings so
        # the next launch reopens it. Without this, Save As is a
        # one-shot — restarting the app silently snaps back to the
        # default ~/Documents/OSTG/session.json (which is usually
        # empty), and the operator's work appears to vanish.
        _persist_current_session_path(path)
        # Force a fresh write: the content-hash short-circuit in
        # _save_session_impl would otherwise treat this as a no-op
        # if the in-memory state matches the previous saved hash
        # (common when Save As is invoked right after a regular save).
        self._last_session_hash = None
        # Blocking save so the chronology is: file written → recent
        # list updated → title refreshed. async would race. Marked
        # manual=True so the auto-save-disabled gate doesn't suppress
        # this — Save As is always a deliberate user action.
        try:
            self.save_session(blocking=True, manual=True)
        except Exception as exc:
            logger.error(f"[SAVE AS] save failed: {exc}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save Session As",
                                f"Failed to save session:\n{exc}")
            return

        # Update recent list + menu + title bar.
        try:
            from utils.recent_sessions import add_recent
            add_recent(path)
        except Exception as exc:
            logger.debug(f"[SAVE AS] recent-list update failed: {exc}")
        try:
            self._rebuild_recent_sessions_menu()
        except Exception:
            pass
        try:
            self._update_window_title()
        except Exception:
            pass

    def load_session_from(self):
        """File menu → Load Session From… — pick an existing session
        file, load it, and make that path the new active session so
        subsequent Ctrl+S writes back to it.

        The actual reload triggers UI rebuilds via `load_session`'s
        existing post-load handlers (stream table, devices, server
        tree, etc.) — we just thread the path through and refresh
        the title + recent list.
        """
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        from utils.path_utils import get_session_file_path
        start_dir = (
            getattr(self, "_current_session_path", None)
            or get_session_file_path()
        )
        path, _filt = QFileDialog.getOpenFileName(
            self, "Load Session From",
            start_dir,
            "Session JSON (*.json);;All files (*)",
        )
        if not path:
            return  # user cancelled

        try:
            self.load_session(session_file_path=path)
        except Exception as exc:
            logger.error(f"[LOAD FROM] load failed: {exc}")
            QMessageBox.warning(self, "Load Session From",
                                f"Failed to load session:\n{exc}")
            return

        try:
            from utils.recent_sessions import add_recent
            add_recent(path)
        except Exception as exc:
            logger.debug(f"[LOAD FROM] recent-list update failed: {exc}")
        try:
            self._rebuild_recent_sessions_menu()
        except Exception:
            pass
        try:
            self._update_window_title()
        except Exception:
            pass

    def _rebuild_recent_sessions_menu(self):
        """Repopulate the File → Recent Sessions submenu from the
        on-disk recent-list. Called after Save As / Load From and at
        boot. No-op if the menu wasn't constructed (defensive — the
        menu lives in main.py and is wired in early; mixin order
        could in theory change).
        """
        menu = getattr(self, "recent_sessions_menu", None)
        if menu is None:
            return
        try:
            from utils.recent_sessions import load_recent
            paths = load_recent()
        except Exception:
            paths = []

        menu.clear()
        if not paths:
            from PyQt5.QtWidgets import QAction
            empty = QAction("(no recent sessions)", self)
            empty.setEnabled(False)
            menu.addAction(empty)
            return

        from PyQt5.QtWidgets import QAction
        import os

        def _make_loader(p):
            # Closure captures `p` by value at action-creation time —
            # without this every menu item would load whichever path
            # the loop variable happens to hold when it fires.
            def _load():
                try:
                    self.load_session(session_file_path=p)
                except Exception as exc:
                    logger.error(f"[RECENT] load {p!r} failed: {exc}")
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "Recent Sessions",
                        f"Failed to load:\n{p}\n\n{exc}",
                    )
                    return
                try:
                    from utils.recent_sessions import add_recent
                    add_recent(p)
                except Exception:
                    pass
                try:
                    self._rebuild_recent_sessions_menu()
                except Exception:
                    pass
                try:
                    self._update_window_title()
                except Exception:
                    pass
            return _load

        for p in paths:
            label = os.path.basename(p)
            act = QAction(label, self)
            act.setStatusTip(p)
            act.setToolTip(p)
            act.triggered.connect(_make_loader(p))
            menu.addAction(act)
        # Clear-list affordance — operators sometimes want to drop a
        # stale entry without manually editing recent_sessions.json.
        menu.addSeparator()
        clear_act = QAction("Clear Recent List", self)

        def _clear():
            try:
                from utils.recent_sessions import clear_recent
                clear_recent()
            except Exception:
                pass
            self._rebuild_recent_sessions_menu()
        clear_act.triggered.connect(_clear)
        menu.addAction(clear_act)

    def _update_window_title(self):
        """Bake the active session file basename + dirty marker into
        ``self._base_window_title``, then trigger the existing
        section-size readout to render the full title.

        Title format:
          ``Netgen Traffic Generator — <basename>[ *]  —  <sizes>``

        The asterisk shows whenever a save is in flight or pending
        (the trailing-edge of the coalesce). Auto-save makes it brief
        but it's an accurate signal that in-memory state hasn't been
        persisted yet.
        """
        import os
        app = "Netgen Traffic Generator"
        path = getattr(self, "_current_session_path", None)
        name = os.path.basename(path) if path else ""
        # v0.3.11: dirty reflects three signals:
        #   * _save_in_progress / _save_pending — save worker active
        #     (brief flicker during auto-save tick)
        #   * _has_unsaved_edits — edit happened but auto-save is OFF
        #     so the write was suppressed. Stays True until a manual
        #     Ctrl+S / Save As… clears it.
        dirty = (getattr(self, "_save_in_progress", False)
                 or getattr(self, "_save_pending", False)
                 or getattr(self, "_has_unsaved_edits", False))
        marker = " *" if dirty else ""
        if name:
            self._base_window_title = f"{app} — {name}{marker}"
        else:
            self._base_window_title = app
        # _update_section_size_readout (defined in main.py) appends
        # dimensions and calls setWindowTitle. Fall back to setting
        # the base directly if it isn't wired in.
        readout = getattr(self, "_update_section_size_readout", None)
        if callable(readout):
            try:
                readout()
                return
            except Exception:
                pass
        try:
            self.setWindowTitle(self._base_window_title)
        except Exception:
            pass

    def toggle_auto_save_enabled(self, checked: bool):
        """Wire-up handler for the File → Auto-save Session checkable
        menu action. Flips ``self._auto_save_enabled`` and persists to
        QSettings so the choice survives a restart.

        Default is OFF (opt-in). When ON, every edit handler that
        calls ``save_session()`` writes through to the active session
        file (BGP / OSPF / ISIS / DHCP / VXLAN applies, stream
        add/remove, inline cell edits). When OFF, only deliberate
        user actions (Ctrl+S, Save Session As…) write.
        """
        self._auto_save_enabled = bool(checked)
        try:
            from PyQt5.QtCore import QSettings
            QSettings().setValue("auto_save_enabled", self._auto_save_enabled)
        except Exception as exc:
            logger.debug(f"[SAVE SESSION] QSettings persist failed: {exc}")
        state = "enabled" if self._auto_save_enabled else "disabled"
        logger.info(f"[SAVE SESSION] auto-save {state}")
        # Brief status-bar nudge so the operator sees the toggle
        # took effect (the menu checkmark is easy to miss).
        try:
            sb = self.statusBar() if callable(getattr(self, "statusBar", None)) else None
            if sb is not None:
                msg = (
                    "Auto-save ON — edits will write to the active "
                    "session file"
                    if self._auto_save_enabled
                    else "Auto-save OFF — use Ctrl+S / Save As to save"
                )
                sb.showMessage(msg, 3500)
        except Exception:
            pass

    def _post_save_toast(self):
        """Brief status-bar message after a successful save.

        Reads `_last_save_summary` populated by `_save_session_impl`
        (devices count, streams count, written path). Silent fallback
        when the status bar isn't available or the summary wasn't
        populated (legacy code path, partial init).
        """
        import os
        sb = getattr(self, "statusBar", None)
        bar = sb() if callable(sb) else None
        if bar is None:
            return
        summary = getattr(self, "_last_save_summary", None) or {}
        path = summary.get("path") or ""
        name = os.path.basename(path) if path else "session.json"
        ndev = summary.get("devices", 0)
        nstr = summary.get("streams", 0)
        msg = f"Saved {ndev} device{'' if ndev == 1 else 's'}, " \
              f"{nstr} stream{'' if nstr == 1 else 's'} to {name}"
        try:
            bar.showMessage(msg, 4000)  # 4 second timeout
        except Exception:
            pass

    def _save_session_impl(self, protocol_data=None):
        """Internal implementation of save_session (runs in worker thread).
        
        Args:
            protocol_data: Pre-extracted protocol table data (extracted in main thread)
        """
        import traceback
        import time
        
        updated_streams = {}
        if hasattr(self, "ensure_unique_stream_ids"):
            self.ensure_unique_stream_ids()
        # Serialize stream data
        for port, stream_list in self.streams.items():
            updated_streams[port] = []
            for stream in stream_list:
                if hasattr(stream, "get_stream_details"):
                    stream_data = stream.get_stream_details()
                else:
                    stream_data = stream
                updated_streams[port].append(sanitize_for_json(stream_data))

        # ✅ Extract device data from all_devices data structure including protocol configurations
        device_rows = {}
        if hasattr(self, "devices_tab") and self.devices_tab is not None:
            # Use the all_devices data structure which has the complete device information
            # Get the current state directly from the main_window (same as device removal uses)
            all_devices = getattr(self, "all_devices", {})
            # v0.3.11 diagnostic: log what the save sees from both
            # main_window.all_devices and devices_tab.all_devices so
            # the user-reported "devices in GUI but 0 in saved file"
            # bug surfaces the discrepancy if those two have drifted.
            # Cheap to compute, prints once per save, makes the next
            # bug-report log paste self-explanatory.
            _mw_count = sum(
                len(v) if isinstance(v, list) else 0
                for v in all_devices.values()
            )
            _dt_all_devices = getattr(self.devices_tab, "all_devices", {})
            _dt_count = sum(
                len(v) if isinstance(v, list) else 0
                for v in (_dt_all_devices or {}).values()
            )
            logger.info(
                f"[SAVE SESSION] all_devices snapshot: "
                f"main_window has {_mw_count} device(s) across "
                f"{len(all_devices)} iface(s); devices_tab has "
                f"{_dt_count} device(s) across "
                f"{len(_dt_all_devices or {})} iface(s)"
            )
            # If the GUI's devices_tab has devices but main_window
            # doesn't (the drift case), fall back to devices_tab's
            # copy so the save captures what the operator actually
            # sees. Logged so the discrepancy isn't silent.
            if _mw_count == 0 and _dt_count > 0:
                logger.warning(
                    f"[SAVE SESSION] main_window.all_devices is "
                    f"empty but devices_tab has {_dt_count} "
                    f"device(s). Using devices_tab.all_devices as "
                    f"the save source — investigate the drift."
                )
                all_devices = _dt_all_devices
            # v0.3.11: canonicalize the device's Interface field
            # before persisting. Historically the in-memory key and
            # the device's own Interface attribute could drift — a
            # server returning a malformed truthy iface landed both
            # under the malformed bucket, and naive save persisted
            # the malformed value. Once that file ever loaded
            # somewhere without the load-time repair, the device
            # vanished. Sanitize on the way out so the on-disk
            # format is always the canonical "TG N - port" form,
            # regardless of how it got into memory.
            from utils.iface_naming import (
                canonical_iface_key, looks_canonical,
            )
            # Build a quick iface→tg_id map from server_interfaces so
            # the sanitizer can repair malformed keys when we can
            # infer the right TG.
            _tg_by_address = {}
            for _s in getattr(self, "server_interfaces", []):
                _addr = _s.get("address")
                if _addr:
                    _tg_by_address[_addr] = _s.get("tg_id", 0)

            # Convert from interface-based structure to device name-based structure
            for iface, devices in all_devices.items():
                for device in devices:
                    device_name = device.get("Device Name", "")
                    if device_name:
                        # If the bucket key is malformed, try to
                        # repair it using the device's own tg_id /
                        # server_url field, else fall back to the
                        # bucket key as-is (the load-time repair
                        # will still try to fix it on the next
                        # round-trip).
                        if not looks_canonical(iface):
                            _tg = device.get("tg_id")
                            if _tg is None:
                                _srv_url = device.get("server_url")
                                if _srv_url:
                                    _tg = _tg_by_address.get(_srv_url)
                            _canon = canonical_iface_key(iface, tg_id=_tg)
                            if _canon and looks_canonical(_canon):
                                device["Interface"] = _canon
                        else:
                            device["Interface"] = iface
                        # Use device name as key instead of MAC address
                        device_rows[device_name] = device
            
            # Use pre-extracted protocol data (extracted in main thread to avoid PyQt widget access from worker thread)
            if protocol_data is None:
                protocol_data = {}
        else:
            # No devices_tab found
            if protocol_data is None:
                protocol_data = {}

        # Track removed devices for session synchronization
        removed_devices = getattr(self, 'removed_devices', [])
        # Found removed devices to save

        # Get BGP route pools if defined
        bgp_route_pools = getattr(self, 'bgp_route_pools', [])
        
        # Determine which servers to save:
        # - If server was provided via CLI, check if servers were modified
        # - If servers were added/removed via UI, save current server_interfaces
        # - Otherwise, preserve original servers from session.json (CLI mode)
        # - Ensure ServerManager is in sync before saving
        if hasattr(self, "server_manager") and self.server_interfaces:
            # Fingerprint the server list so we only rebuild the
            # ServerManager when something actually changed. Previously
            # every save re-registered every server from scratch — that
            # caused noisy "Registered server: ..." log spam on every
            # auto-save and burned cycles tearing down + rebuilding
            # state that was already correct.
            fp = tuple(sorted(
                (s.get("address", ""), int(s.get("tg_id", 0)), bool(s.get("online", True)))
                for s in self.server_interfaces
            ))
            last_fp = getattr(self, "_last_server_fp", None)
            if fp != last_fp:
                self.server_manager.initialize_from_server_interfaces(
                    self.server_interfaces, clear_existing=True,
                )
                self._last_server_fp = fp
                logger.info(
                    f"[SAVE SESSION] Synced ServerManager with "
                    f"{len(self.server_interfaces)} server(s)"
                )
            # Else: silently skip — same servers, same TG ids, same
            # online flags as last save.
        
        # Check if we should preserve original servers (CLI mode without modifications)
        preserve_original = False
        if getattr(self, 'server_url_from_cli', False) and hasattr(self, 'original_session_servers'):
            # In CLI mode, only preserve original if server count hasn't changed
            # This allows saving when user adds/removes servers via UI
            original_count = len(self.original_session_servers)
            current_count = len(self.server_interfaces)
            if original_count == current_count:
                # Count matches, check if addresses are the same
                original_addresses = {s.get("address") for s in self.original_session_servers}
                current_addresses = {s.get("address") for s in self.server_interfaces}
                if original_addresses == current_addresses:
                    preserve_original = True
        
        if preserve_original:
            # CLI mode: preserve original servers from session.json (no changes made)
            servers_to_save = self.original_session_servers
        else:
            # Normal mode or CLI mode with modifications: save current servers
            # Clean server_interfaces to remove PyQt widget objects before saving
            servers_to_save = []
            for server in self.server_interfaces:
                # Create a clean copy without PyQt objects
                clean_server = {
                    "tg_id": server.get("tg_id", 0),
                    "address": server.get("address", ""),
                    "online": server.get("online", True),
                    "interfaces": server.get("interfaces", [])
                }
                # Only include interfaces if it's a list/dict (not a PyQt object)
                if isinstance(clean_server["interfaces"], (list, dict)):
                    servers_to_save.append(clean_server)
                else:
                    clean_server["interfaces"] = []
                    servers_to_save.append(clean_server)
            
            # Demoted to debug. The "Saving N current server(s)" line
            # used to fire on every save — including the trailing-edge
            # no-op saves that follow real ones — making it look like
            # the GUI was thrashing. The actual "✅ Session saved"
            # log below is now the single user-visible signal that a
            # write occurred.
            if getattr(self, 'server_url_from_cli', False):
                logger.debug(f"[SAVE SESSION] Saving {len(servers_to_save)} current server(s) (CLI mode, servers modified)")
            else:
                logger.debug(f"[SAVE SESSION] Saving {len(servers_to_save)} current server(s)")

        # Clean up removed_servers - remove any servers that are currently in server_interfaces
        # This ensures that if a server was previously removed but then re-added, it won't be in removed_servers
        current_server_addresses = {s.get("address") for s in servers_to_save}
        cleaned_removed_servers = {addr for addr in self.removed_servers if addr not in current_server_addresses}

        # Assemble session data.
        #
        # NB: every container derived from a `set` is sorted before
        # serialization. Python's `set` iteration order is not stable
        # across calls (hash randomization, insertion/removal pattern),
        # so `list(set)` would produce different orderings on otherwise
        # identical saves — making the content hash differ even though
        # nothing meaningful changed, which defeated the no-op
        # short-circuit. Sorting makes the JSON canonical.
        session_data = {
            "servers": sanitize_for_json(servers_to_save),
            "removed_interfaces": sorted(self.removed_interfaces),
            "removed_servers": sorted(cleaned_removed_servers),
            "selected_servers": sorted(
                s["address"] for s in getattr(self, "selected_servers", [])
            ),
            "streams": updated_streams,
            "devices": sanitize_for_json(device_rows),
            "removed_devices": sanitize_for_json(removed_devices),
            "protocols": sanitize_for_json(protocol_data) if protocol_data else {},
            "bgp_route_pools": sanitize_for_json(bgp_route_pools),  # Save global route pools
        }

        # Store current device state for change tracking
        self.last_saved_devices = device_rows.copy()

        # Content-hash short-circuit. The trailing-edge coalesce in
        # save_session() was firing a follow-up write after every real
        # save, even when nothing had changed in the 2 s gap. Now we
        # serialize once, hash it, and only touch the disk when the
        # hash differs from the last successful write. Identity-saves
        # are silent: no log, no I/O, no chain re-arm.
        try:
            payload = json.dumps(session_data, indent=2, sort_keys=True, default=str)
        except Exception as exc:
            return False, f"[❌] Failed to serialize session: {exc}"
        import hashlib
        current_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if getattr(self, "_last_session_hash", None) == current_hash:
            # No-op: matches the last successful write byte-for-byte.
            # Return success so callers don't think the save failed,
            # but skip the disk I/O and the "✅ Session saved" log so
            # the user-visible noise floor stays at zero.
            return True, "no changes — skipped"

        # Save to disk. v0.3.11: write to `_current_session_path` so
        # Save As / Load From can swap the active file; fall back to
        # the default ostg data-dir path if the attr was never set
        # (defensive — pre-mixin code paths or unit-test mocks).
        try:
            from utils.path_utils import get_session_file_path
            session_file = (
                getattr(self, "_current_session_path", None)
                or get_session_file_path()
            )
            # If a Save As pointed us at a directory that doesn't
            # exist yet (e.g. ~/sessions/), create it. Mirrors the
            # behaviour of the data-dir default which auto-creates.
            import os
            os.makedirs(os.path.dirname(session_file) or ".",
                        exist_ok=True)
            with open(session_file, "w") as f:
                f.write(payload)
            self._last_session_hash = current_hash
            # Stash counts so the post-save toast can quote them
            # without re-walking the structures (the worker thread
            # already iterated them above).
            self._last_save_summary = {
                "path": session_file,
                "devices": len(device_rows),
                "streams": sum(len(v) for v in updated_streams.values()),
            }
            return True, "✅ Session saved successfully"
        except Exception as e:
            return False, f"[❌] Failed to save session: {e}"

    def _cleanup_removed_devices_from_server(self, removed_device_ids, all_loaded_devices):
        """Clean up removed devices from server during session loading."""
        try:
            logger.debug(f"[DEBUG CLEANUP] Starting server cleanup for {len(removed_device_ids)} removed devices")
            
            # Get server URL
            if not hasattr(self, "devices_tab") or not self.devices_tab:
                logger.debug(f"[DEBUG CLEANUP] No devices_tab available")
                return
            
            server_url = self.devices_tab.get_server_url(silent=True)
            if not server_url:
                logger.debug(f"[DEBUG CLEANUP] No server URL available")
                return
            
            import requests
            
            # Track devices that were successfully cleaned up or don't exist anymore
            devices_to_remove_from_session = []
            
            for device_id in removed_device_ids:
                try:
                    # Find device info for this ID
                    device_info = None
                    device_name = "Unknown"
                    for name, info in all_loaded_devices.items():
                        if info.get("device_id") == device_id:
                            device_info = info
                            device_name = name
                            break
                    
                    # If device info not found, it means the device was already removed from session
                    # or doesn't exist. We should remove it from removed_devices list.
                    if not device_info:
                        logger.debug(f"[DEBUG CLEANUP] Device info not found for ID: {device_id} - already removed or doesn't exist")
                        devices_to_remove_from_session.append(device_id)
                        continue
                    
                    logger.debug(f"[DEBUG CLEANUP] Cleaning up removed device: {device_name} (ID: {device_id})")
                    
                    # Clean up device-specific IPs from server
                    iface_label = device_info.get("Interface", "")
                    iface_norm = self.devices_tab._normalize_iface_label(iface_label)
                    vlan = device_info.get("VLAN", "0")
                    
                    cleanup_payload = {
                        "interface": iface_norm,
                        "vlan": vlan,
                        "cleanup_only": True,
                        "device_specific": True,
                        "device_id": device_id,
                        "device_name": device_name
                    }
                    
                    cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=cleanup_payload, timeout=10)
                    cleanup_success = cleanup_resp.status_code == 200
                    if cleanup_success:
                        logger.debug(f"[DEBUG CLEANUP] Successfully cleaned up IPs for device: {device_name}")
                    else:
                        # If device doesn't exist (404), it's already cleaned up
                        if cleanup_resp.status_code == 404:
                            logger.debug(f"[DEBUG CLEANUP] Device {device_name} doesn't exist on server (already removed)")
                            cleanup_success = True  # Treat as success since device is already gone
                        else:
                            logger.error(f"[DEBUG CLEANUP] Cleanup failed for {device_name}: {cleanup_resp.status_code}")
                    
                    # Also call the device remove API for protocol cleanup
                    remove_payload = {
                        "device_id": device_id,
                        "device_name": device_name,
                        "interface": iface_norm,
                        "vlan": vlan,
                        "ipv4": device_info.get("IPv4", ""),
                        "ipv6": device_info.get("IPv6", ""),
                        "protocols": device_info.get("Protocols", "").split(",") if device_info.get("Protocols") else []
                    }
                    
                    remove_resp = requests.post(f"{server_url}/api/device/remove", json=remove_payload, timeout=10)
                    remove_success = remove_resp.status_code == 200
                    if remove_success:
                        logger.debug(f"[DEBUG CLEANUP] Successfully removed device protocols: {device_name}")
                    else:
                        # If device doesn't exist (404), it's already removed
                        if remove_resp.status_code == 404:
                            logger.debug(f"[DEBUG CLEANUP] Device {device_name} doesn't exist on server (already removed)")
                            remove_success = True  # Treat as success since device is already gone
                        else:
                            logger.error(f"[DEBUG CLEANUP] Remove API failed for {device_name}: {remove_resp.status_code}")
                    
                    # If cleanup was successful (or device doesn't exist), remove from session
                    if cleanup_success or remove_success:
                        devices_to_remove_from_session.append(device_id)
                        
                except Exception as e:
                    logger.error(f"Failed to cleanup device {device_id}: {e}")
            
            # Remove successfully cleaned devices from session.json
            if devices_to_remove_from_session:
                try:
                    from utils.path_utils import get_session_file_path
                    session_file = get_session_file_path()
                    with open(session_file, "r") as f:
                        session_data = json.load(f)
                    
                    removed_devices = session_data.get("removed_devices", [])
                    # Remove devices that were successfully cleaned up
                    original_count = len(removed_devices)
                    session_data["removed_devices"] = [
                        dev_id for dev_id in removed_devices 
                        if dev_id not in devices_to_remove_from_session
                    ]
                    removed_count = original_count - len(session_data["removed_devices"])
                    
                    if removed_count > 0:
                        with open(session_file, "w") as f:
                            json.dump(session_data, f, indent=2)
                        logger.debug(f"[DEBUG CLEANUP] Removed {removed_count} device(s) from session.json removed_devices list")
                except Exception as e:
                    logger.error(f"Failed to update session.json: {e}")
            
            logger.debug(f"[DEBUG CLEANUP] Completed server cleanup for removed devices")
            
        except Exception as e:
            logger.error(f"Failed to cleanup removed devices from server: {e}")

    def _extract_table_data(self, table):
        """Extract data from a QTableWidget and return as list of dictionaries."""
        if not table:
            return []
        
        data = []
        headers = []
        
        # Get headers
        for col in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(col)
            headers.append(header_item.text() if header_item else f"Column_{col}")
        
        # Get row data
        for row in range(table.rowCount()):
            row_data = {}
            for col in range(table.columnCount()):
                item = table.item(row, col)
                row_data[headers[col]] = item.text() if item else ""
            data.append(row_data)
        
        return data

    def _load_protocol_data(self, protocol_data):
        """Load protocol data into the respective tables."""
        if not hasattr(self, "devices_tab") or not self.devices_tab:
            return
        
        # Load BGP data
        if "bgp" in protocol_data and hasattr(self.devices_tab, "bgp_table"):
            self._populate_table_from_data(self.devices_tab.bgp_table, protocol_data["bgp"])
        
        # Load OSPF data
        if "ospf" in protocol_data and hasattr(self.devices_tab, "ospf_table"):
            self._populate_table_from_data(self.devices_tab.ospf_table, protocol_data["ospf"])
        
        # Load ISIS data
        if "isis" in protocol_data and hasattr(self.devices_tab, "isis_table"):
            self._populate_table_from_data(self.devices_tab.isis_table, protocol_data["isis"])

    def _populate_table_from_data(self, table, data):
        """Populate a QTableWidget with data from a list of dictionaries."""
        if not table or not data:
            return
        
        # Clear existing data
        table.setRowCount(0)
        
        # Set row count
        table.setRowCount(len(data))
        
        # Get headers
        headers = []
        for col in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(col)
            headers.append(header_item.text() if header_item else f"Column_{col}")
        
        # Populate data
        for row, row_data in enumerate(data):
            for col, header in enumerate(headers):
                if header in row_data:
                    item = QTableWidgetItem(str(row_data[header]))
                    table.setItem(row, col, item)

    def _sanitize_loaded_streams(self, valid_ports: set):
        """
        In-place cleanup on self.streams after JSON load:
          - Drop ports not in valid_ports (if valid_ports is non-empty)
          - Ensure each stream has protocol_selection + name
          - Ensure unique stream_id (create if missing; repair if dup)
        """
        # 1) Filter ports to only valid_ports (if we were able to discover any)
        # Handle both formats: "TG 0 - ens5np0" and "TG 0 - Port: ens5np0"
        if valid_ports:
            # Create normalized set that includes both formats
            normalized_valid_ports = set(valid_ports)
            for p in valid_ports:
                if " - " in p:
                    parts = p.split(" - ", 1)
                    if len(parts) == 2:
                        # Add format with "Port:" prefix
                        normalized_valid_ports.add(f"{parts[0]} - Port: {parts[1]}")
                        # Add format without "Port:" prefix (if it has "Port:")
                        if parts[1].startswith("Port: "):
                            normalized_valid_ports.add(f"{parts[0]} - {parts[1].replace('Port: ', '')}")
            
            # Filter streams, matching against normalized port names
            filtered_streams = {}
            for port_key, stream_list in self.streams.items():
                # Check if port_key matches any normalized valid port
                if port_key in normalized_valid_ports:
                    filtered_streams[port_key] = stream_list
                else:
                    # Try to normalize port_key and match
                    if " - " in port_key:
                        parts = port_key.split(" - ", 1)
                        if len(parts) == 2:
                            # Create normalized versions
                            key_with_port = f"{parts[0]} - Port: {parts[1].replace('Port: ', '')}"
                            key_without_port = f"{parts[0]} - {parts[1].replace('Port: ', '')}"
                            if key_with_port in normalized_valid_ports or key_without_port in normalized_valid_ports:
                                filtered_streams[port_key] = stream_list
            
            self.streams = filtered_streams

        # 2) Normalize structure + IDs
        seen_ids = set()
        repaired = 0

        for port, lst in list(self.streams.items()):
            if not isinstance(lst, list):
                # Corrupt shape; drop it to be safe
                logger.warning(f"[WARN] Streams for '{port}' not a list; dropping.")
                del self.streams[port]
                continue

            for s in lst:
                # Ensure protocol_selection dict
                ps = s.setdefault("protocol_selection", {})

                # Ensure name
                nm = ps.get("name") or s.get("name")
                if not nm:
                    # Assign a readable default if missing
                    nm = f"str{len(seen_ids) + 1}"
                ps["name"] = nm
                s["name"] = nm

                # Ensure status (nice-to-have)
                s.setdefault("status", "stopped")

                # Ensure stream_id uniqueness
                sid = s.get("stream_id")
                if not sid or sid in seen_ids:
                    # allocate a new id
                    if hasattr(self, "_alloc_stream_id"):
                        sid = self._alloc_stream_id()
                    else:
                        import uuid
                        sid = str(uuid.uuid4())
                    s["stream_id"] = sid
                    repaired += 1
                seen_ids.add(sid)

        if repaired:
            logger.info(f"[STREAM-ID] Repaired/created {repaired} stream_id(s) during load.")

    def _fetch_interfaces_async(self, address, server):
        """Fetch interfaces from server in a real background thread.

        Previously this name was misleading — the function was called
        from QTimer.singleShot which runs on the Qt event loop (the UI
        thread), and the body did a synchronous requests.get with a
        2s timeout. Offline / unreachable servers (especially ones
        that RST the connection mid-handshake — see user-reported
        "Connection reset by peer") froze the UI for the full timeout.

        Now: real QThread off the UI thread, results delivered via a
        signal back to the main thread.
        """
        from PyQt5.QtCore import QThread, pyqtSignal, QTimer

        class _MenuFetchWorker(QThread):
            done = pyqtSignal(bool, int, list, str)  # (ok, status, interfaces, err)

            def __init__(self, url, conn_mgr):
                super().__init__()
                self._url = url
                self._conn_mgr = conn_mgr

            def run(self):
                try:
                    if self._conn_mgr is not None:
                        r = self._conn_mgr.get(
                            f"{self._url}/api/interfaces", timeout=2
                        )
                    else:
                        import requests
                        r = requests.get(
                            f"{self._url}/api/interfaces", timeout=2
                        )
                    if r.status_code == 200:
                        try:
                            from traffic_client.server_section import _filter_internal_ifaces
                            self.done.emit(True, 200, _filter_internal_ifaces(r.json()) or [], "")
                            return
                        except Exception as fexc:
                            self.done.emit(False, r.status_code, [], f"parse: {fexc}")
                            return
                    self.done.emit(False, r.status_code, [], "")
                except Exception as exc:
                    self.done.emit(False, 0, [], str(exc))

        conn_mgr = getattr(self, "connection_manager", None)
        worker = _MenuFetchWorker(address, conn_mgr)
        # Permanent keepalive — see _keepalive_worker for why setParent
        # + deleteLater don't suffice on PyQt5 5.15.11 + Python 3.14.
        self._keepalive_worker(worker)

        def _on_done(ok, status, ifaces, err, srv=server, addr=address):
            if ok:
                srv["interfaces"] = ifaces
                srv["online"] = True
                if hasattr(self, "update_server_tree"):
                    QTimer.singleShot(0, self.update_server_tree)
                logger.info(f"Fetched {len(ifaces)} interfaces from {addr} (async)")
            else:
                srv["online"] = False
                if hasattr(self, "failed_servers"):
                    if srv not in self.failed_servers:
                        self.failed_servers.append(srv)
                if err:
                    logger.warning(f"Error fetching interfaces from {addr} (async): {err}")
                elif status:
                    logger.warning(f"{addr} returned HTTP {status}")
                if hasattr(self, "update_server_tree"):
                    QTimer.singleShot(0, self.update_server_tree)

        worker.done.connect(_on_done)
        worker.start()
    
    def load_session(self, skip_servers=False, session_file_path=None):
        """Load the session from a JSON file.

        Args:
            skip_servers: If True, skip loading servers from session.json
                (useful when server is provided via CLI).
            session_file_path: Optional path override. When supplied
                (Load From… picker), the helper also updates
                ``self._current_session_path`` so subsequent Save
                operations write to the same file. When None, falls
                back to ``self._current_session_path`` (Save As may
                have changed it) and finally to the default data-dir
                path.
        """
        try:
            from utils.path_utils import get_session_file_path
            session_file = (
                session_file_path
                or getattr(self, "_current_session_path", None)
                or get_session_file_path()
            )
            # If the caller passed an explicit path, point the active
            # session at it. Save As / Load From rely on this. Also
            # treat it as a full REPLACE rather than the startup merge
            # the original implementation was built around — without
            # this, opening a second snapshot mid-session leaves
            # ghosts from the previous one (server list keeps the
            # stale entries because the loader below only appends new
            # ones; same hazard for the removed_* sets).
            is_replace_load = bool(session_file_path)
            if session_file_path:
                self._current_session_path = session_file_path
                # v0.4.7: persist so the next launch reopens this
                # file instead of snapping back to the default.
                _persist_current_session_path(session_file_path)
                # Wipe state that the startup merge path would
                # otherwise leave dangling. Streams + all_devices
                # already get cleared further down, but the server
                # list, removed_* sets, and the content-hash
                # short-circuit need to be reset here so the rest of
                # the load behaves like a fresh boot against the
                # chosen file.
                self.server_interfaces = []
                self.removed_servers = set()
                self.removed_interfaces = set()
                # Clear the save-hash so the next save (which will
                # write the freshly-loaded state back to the new
                # active file) doesn't get short-circuited by a
                # stale hash from the previous session.
                self._last_session_hash = None
            with open(session_file, "r") as f:
                session_data = json.load(f)
            
            logger.debug(f"[DEBUG SESSION] Loaded session.json with {len(session_data.get('devices', {}))} devices")

            # Load removed servers and removed interfaces
            self.removed_servers = set(session_data.get("removed_servers", []))
            removed_interfaces_raw = session_data.get("removed_interfaces", [])
            
            # Clean up old format entries (e.g., " - ens14f1" without TG ID)
            # These are invalid and should be removed
            self.removed_interfaces = set()
            for iface in removed_interfaces_raw:
                # Only keep entries that have the correct format "TG X - portname"
                if iface and "TG " in iface and " - " in iface and not iface.startswith(" - "):
                    self.removed_interfaces.add(iface)
                else:
                    logger.debug(f"[DEBUG LOAD] Skipping invalid removed interface format: {iface}")
            
            # Always preserve original servers from session.json (for saving later in CLI mode)
            session_servers = session_data.get("servers", [])
            self.original_session_servers = session_servers.copy()
            
            # Only load servers from session if skip_servers is False
            if not skip_servers:
                # Load servers from session, but preserve any servers added via command line
                # and exclude servers that were previously removed
                existing_server_urls = {server["address"] for server in self.server_interfaces}
            
                # Add session servers that aren't already present and weren't removed
                for server in session_servers:
                    server_address = server["address"]
                    if server_address not in existing_server_urls and server_address not in self.removed_servers:
                        self.server_interfaces.append(server)
                        logger.debug(f"[DEBUG LOAD] Added server {server_address} from session")
                        
                        # Update ServerManager if available (will be initialized later, but this ensures consistency)
                        if hasattr(self, "server_manager"):
                            from utils.server_manager import ServerManager
                            server_id = ServerManager._extract_server_id_from_url(server_address)
                            tg_id = server.get("tg_id", 0)
                            self.server_manager.register_server(
                                server_id=server_id,
                                address=server_address,
                                tg_id=tg_id,
                                online=server.get("online", True),
                                interfaces=server.get("interfaces", [])
                            )
                    elif server_address in self.removed_servers:
                        logger.debug(f"[DEBUG LOAD] Skipped removed server {server_address}")
            else:
                logger.debug(f"[DEBUG LOAD] Skipped loading servers from session.json (server provided via CLI)")
                logger.debug(f"[DEBUG LOAD] Preserved {len(self.original_session_servers)} original server(s) from session.json for future saves")
            
            # Cancel pending auto-stop timers BEFORE clearing self.streams
            # — otherwise they fire later against vanished stream_ids.
            if hasattr(self, "_cancel_all_auto_stop_timers"):
                self._cancel_all_auto_stop_timers()
            self.streams = {}
            self.failed_servers = []
            self.all_devices = {}  # Initialize all_devices

            # Discover valid interfaces from online servers (deferred to avoid blocking startup)
            # Use cached interfaces from session.json if available, otherwise fetch asynchronously
            valid_ports = set()
            for server in self.server_interfaces:
                tg_id = f"TG {server.get('tg_id', '0')}"
                address = server.get("address")
                
                # First, try to use cached interfaces from session.json (fast, no network call)
                cached_interfaces = server.get("interfaces", [])
                if cached_interfaces:
                    # Use cached interfaces immediately (non-blocking)
                    server["online"] = True
                    for iface in cached_interfaces:
                        iface_name = iface if isinstance(iface, str) else iface.get('name', '')
                        if iface_name:
                            port_name_simple = f"{tg_id} - {iface_name}"
                            port_name_with_port = f"{tg_id} - Port: {iface_name}"
                            if port_name_simple not in self.removed_interfaces and port_name_with_port not in self.removed_interfaces:
                                valid_ports.add(port_name_simple)
                                valid_ports.add(port_name_with_port)
                    # Schedule async refresh of interfaces (non-blocking)
                    from PyQt5.QtCore import QTimer
                    def refresh_interfaces():
                        self._fetch_interfaces_async(address, server)
                    QTimer.singleShot(100, refresh_interfaces)
                else:
                    # No cached interfaces - mark as offline for now, will be fetched asynchronously
                    server["online"] = False
                    # Schedule async fetch (non-blocking)
                    from PyQt5.QtCore import QTimer
                    def fetch_interfaces():
                        self._fetch_interfaces_async(address, server)
                    QTimer.singleShot(100, fetch_interfaces)

            # Load streams (raw)
            loaded_streams = session_data.get("streams", {})
            if not isinstance(loaded_streams, dict):
                logger.warning("Session 'streams' malformed; starting with empty.")
                loaded_streams = {}

            self.streams = loaded_streams

            # 🔧 Sanitize + filter + de-dup IDs
            self._sanitize_loaded_streams(valid_ports)

            # Extra safety sweep (your existing guard)
            if hasattr(self, "ensure_unique_stream_ids"):
                self.ensure_unique_stream_ids()

            # Load BGP route pools from session
            self.bgp_route_pools = session_data.get("bgp_route_pools", [])
            logger.debug(f"[DEBUG LOAD] Loaded {len(self.bgp_route_pools)} BGP route pool(s)")
            
            # Load devices from session
            loaded_devices = session_data.get("devices", {})
            removed_devices = session_data.get("removed_devices", [])  # List of device IDs that were removed
            
            if isinstance(loaded_devices, dict):
                # Convert from device name-based structure back to interface-based structure
                self.all_devices = {}
                loaded_count = 0
                removed_count = 0
                
                # v0.3.11: build a (port-name → "TG X - port-name") map
                # from the server list so we can repair malformed device
                # Interface fields. Old session files (and at least one
                # bug path the user reported on load) wrote the device's
                # Interface as just " - ens5np0" instead of the canonical
                # "TG 0 - ens5np0" — the loader would dutifully bucket
                # the device under that key, then update_device_table /
                # update_bgp_table couldn't find it because every other
                # lookup uses the "TG X - port" form. Result: a device
                # in `all_devices` but invisible in every tab.
                #
                # The repair: walk server_interfaces, expand each
                # server's interfaces into both the bare and canonical
                # forms, and remember which TG owns each suffix. On
                # load, any device whose Interface lacks "TG " gets
                # rewritten to the canonical form (single suffix
                # match) or left alone (ambiguous / no match). Logged
                # so an operator debugging "where did my device go"
                # sees the rewrite.
                _suffix_to_canonical = {}
                for _srv in self.server_interfaces:
                    _tg = f"TG {_srv.get('tg_id', '0')}"
                    for _i in _srv.get("interfaces", []) or []:
                        _name = _i if isinstance(_i, str) else _i.get("name", "")
                        if not _name:
                            continue
                        # Track every shape the suffix might appear in.
                        for _suffix in (
                            _name,
                            f" - {_name}",
                            f" - Port: {_name}",
                        ):
                            _canon = f"{_tg} - {_name}"
                            _suffix_to_canonical.setdefault(
                                _suffix, set(),
                            ).add(_canon)

                for device_name, device_info in loaded_devices.items():
                    device_id = device_info.get("device_id", "")

                    # Skip devices that were marked as removed
                    if device_id in removed_devices:
                        logger.debug(f"[DEBUG LOAD] Skipping removed device: {device_name} (ID: {device_id})")
                        removed_count += 1
                        continue

                    # Convert old single-tunnel VXLAN config to tunnels format for consistency
                    vxlan_config = device_info.get("vxlan_config", {})
                    if vxlan_config and isinstance(vxlan_config, dict):
                        if "tunnels" not in vxlan_config:
                            # Old format: single tunnel dict, convert to tunnels format
                            logger.debug(f"[DEBUG LOAD] Converting old VXLAN config format to tunnels format for {device_name}")
                            device_info["vxlan_config"] = {"tunnels": [vxlan_config]}

                    iface = device_info.get("Interface", "")
                    # v0.3.11: repair malformed Interface — see the
                    # `_suffix_to_canonical` comment above. Trigger is
                    # "looks like an iface but missing a TG prefix"
                    # (leading " - " or no "TG " at all). Three-tier
                    # repair, most-specific-first:
                    #
                    #   1. Suffix matches a known server-reported
                    #      interface exactly → use the canonical
                    #      "TG X - port" form.
                    #   2. Single-server topology → prepend that
                    #      server's TG even if the port isn't in its
                    #      reported interfaces (covers the case where
                    #      the device's saved port doesn't exist on
                    #      the current server — operator may have
                    #      renamed it, or the session was saved
                    #      against a different host. Device still
                    #      lands in `all_devices` under a canonical
                    #      key so it RENDERS instead of vanishing).
                    #   3. Multi-server + no match → log warning and
                    #      keep the malformed key. Better visible
                    #      with stale data than silently dropped.
                    if iface and "TG " not in iface:
                        _candidates = _suffix_to_canonical.get(iface)
                        _new_iface = None
                        if _candidates and len(_candidates) == 1:
                            _new_iface = next(iter(_candidates))
                        elif _candidates:
                            # Ambiguous — pick the first deterministic
                            # candidate, but warn so the operator can
                            # spot it.
                            _new_iface = sorted(_candidates)[0]
                            logger.warning(
                                f"[LOAD SESSION] Interface {iface!r} "
                                f"ambiguous across servers; defaulting "
                                f"to {_new_iface!r} (candidates: "
                                f"{sorted(_candidates)})"
                            )
                        elif len(self.server_interfaces) == 1:
                            # Single-server fallback. Strip any leading
                            # " - " / "Port: " noise off the saved key
                            # before composing the canonical name.
                            _bare = iface
                            for _prefix in (" - Port: ", " - ", "Port: "):
                                if _bare.startswith(_prefix):
                                    _bare = _bare[len(_prefix):]
                                    break
                            _tg = f"TG {self.server_interfaces[0].get('tg_id', '0')}"
                            _new_iface = f"{_tg} - {_bare}"
                        if _new_iface:
                            logger.info(
                                f"[LOAD SESSION] Repaired malformed "
                                f"Interface for device {device_name!r}: "
                                f"{iface!r} → {_new_iface!r}"
                            )
                            iface = _new_iface
                            device_info["Interface"] = _new_iface
                        else:
                            logger.warning(
                                f"[LOAD SESSION] Cannot repair "
                                f"Interface {iface!r} for device "
                                f"{device_name!r} (multi-server, no "
                                f"suffix match). Device kept under "
                                f"original key; will render via "
                                f"all_devices fallback when no tree "
                                f"selection is active."
                            )
                    if iface:
                        if iface not in self.all_devices:
                            self.all_devices[iface] = []
                        self.all_devices[iface].append(device_info)
                        loaded_count += 1
                
                # Clean up removed devices from server if any were found
                if removed_devices and hasattr(self, "devices_tab") and self.devices_tab:
                    logger.debug(f"[DEBUG LOAD] Found {len(removed_devices)} removed devices in session - cleaning up from server")
                    self._cleanup_removed_devices_from_server(removed_devices, loaded_devices)
                
                # Load BGP protocols from session
                session_bgp_protocols = session_data.get("protocols", {}).get("bgp", [])
                if session_bgp_protocols:
                    logger.debug(f"[DEBUG LOAD] Found {len(session_bgp_protocols)} BGP protocols in session")
                
                # Update devices tab with loaded devices
                if hasattr(self, "devices_tab") and self.devices_tab:
                    self.devices_tab.all_devices = self.all_devices.copy()
                    self.devices_tab.update_device_table(self.all_devices)
                    # v0.3.11: refresh EVERY protocol sub-tab, not just
                    # BGP. The pre-v0.3.11 code only called
                    # `update_bgp_table()`, so a Load From… (or even a
                    # startup load on a session with OSPF / ISIS /
                    # DHCP / VXLAN configs) left those tabs stale — the
                    # operator's saved configs were in memory but not
                    # rendered until something else triggered a
                    # rebuild.
                    #
                    # BGP / OSPF / ISIS have wrapper methods on
                    # devices_tab that delegate to the handler. DHCP
                    # and VXLAN have no such wrappers — the refresh
                    # method only lives on the handler instance. The
                    # routing table below covers both shapes so a
                    # silent no-op (getattr returning None against a
                    # missing wrapper) can't hide the bug again.
                    _refreshers = [
                        # (host_attr, method_name)
                        ("self",         "update_bgp_table"),
                        ("self",         "update_ospf_table"),
                        ("self",         "update_isis_table"),
                        ("dhcp_handler", "refresh_dhcp_status"),
                        ("vxlan_handler", "refresh_vxlan_table"),
                    ]
                    for _host_attr, _method in _refreshers:
                        if _host_attr == "self":
                            _target = self.devices_tab
                        else:
                            _target = getattr(self.devices_tab, _host_attr, None)
                        if _target is None:
                            continue
                        _refresh_fn = getattr(_target, _method, None)
                        if callable(_refresh_fn):
                            try:
                                _refresh_fn()
                            except Exception as _e:
                                logger.warning(
                                    f"[LOAD SESSION] {_method} "
                                    f"raised during refresh: {_e}"
                                )
                    logger.debug(f"[DEBUG LOAD] Loaded {loaded_count} devices from session, skipped {removed_count} removed devices")
            else:
                logger.debug("[DEBUG LOAD] No valid devices found in session")
                self.all_devices = {}

            # Restore selected servers
            sel_addrs = set(session_data.get("selected_servers", []))
            self.selected_servers = [s for s in self.server_interfaces if s.get("address") in sel_addrs]

            # Refresh UI
            if hasattr(self, "update_server_tree"):
                self.update_server_tree()
            if hasattr(self, "update_stream_table"):
                self.update_stream_table()

            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            # Note: Repairs are done in memory only, user can save manually if needed

            stream_count = sum(len(v) for v in self.streams.values())
            port_count = len(self.streams)
            logger.info(f"Session loaded: {stream_count} streams across {port_count} ports.")
            if stream_count > 0:
                logger.debug(f"[DEBUG LOAD] Stream port keys: {list(self.streams.keys())}")
            if valid_ports:
                logger.debug(f"[DEBUG LOAD] Valid ports discovered: {sorted(valid_ports)}")
            
            # v0.5.183: auto-start on launch is OFF by default. The
            # client used to auto-fire every enabled/running stream
            # from session.json 3 s after boot — but operator asked
            # for the app to sit at the loaded (paused) state until
            # the user hits Start explicitly. This matches how every
            # other traffic-gen GUI (Ixia, Spirent) behaves.
            #
            # Power users who want the old behaviour back can flip
            # a QSettings key:
            #     QSettings("Netgen", "netgen-client")
            #     .setValue("auto_start_streams_on_launch", True)
            # or from a terminal on macOS:
            #     defaults write com.netgen.netgen-client \
            #       auto_start_streams_on_launch -bool true
            if stream_count > 0 and hasattr(self, "start_all_streams"):
                from PyQt5.QtCore import QSettings, QTimer
                auto_start_enabled = QSettings(
                    "Netgen", "netgen-client"
                ).value(
                    "auto_start_streams_on_launch",
                    False, type=bool,
                )
                # Count enabled streams that WOULD auto-start so the
                # log is honest about what we skipped.
                enabled_streams_count = 0
                for port_label, stream_list in self.streams.items():
                    for stream in stream_list:
                        is_enabled = stream.get("enabled", False)
                        if not is_enabled:
                            ps = stream.get("protocol_selection", {})
                            is_enabled = ps.get("enabled", False)
                        was_running = (
                            stream.get("status", "").lower()
                            == "running")
                        if is_enabled or was_running:
                            enabled_streams_count += 1

                if enabled_streams_count == 0:
                    logger.info(
                        "[AUTO-START] No enabled streams found "
                        "to auto-start")
                elif not auto_start_enabled:
                    logger.info(
                        f"[AUTO-START] Skipping auto-start of "
                        f"{enabled_streams_count} enabled stream(s) "
                        f"— auto-start on launch is disabled "
                        f"(default). Set "
                        f"auto_start_streams_on_launch=True in "
                        f"QSettings to re-enable.")
                else:
                    logger.info(
                        f"[AUTO-START] Found "
                        f"{enabled_streams_count} enabled/running "
                        f"stream(s) to auto-start")
                    # Delay to give servers + UI + stats polling
                    # time to settle.
                    QTimer.singleShot(
                        3000,
                        lambda: self._auto_start_streams_from_session())
        
        except FileNotFoundError:
            logger.info("No session file found. Starting fresh.")
            self._initialize_empty_session()
    
    def _auto_start_streams_from_session(self):
        """Auto-start enabled streams that were loaded from session.json."""
        # Safety checks: ensure UI is fully initialized before attempting auto-start
        if not hasattr(self, "start_all_streams"):
            logger.info("[AUTO-START] start_all_streams method not available")
            return
        
        # Ensure stream_table exists and is initialized
        if not hasattr(self, "stream_table") or self.stream_table is None:
            logger.info("[AUTO-START] Stream table not initialized yet, skipping auto-start")
            return
        
        # Check if any servers are online
        online_servers = [s for s in getattr(self, "server_interfaces", []) if s.get("online", False)]
        if not online_servers:
            logger.info("[AUTO-START] No online servers available, skipping auto-start")
            return
        
        # Check if there are any enabled streams to start
        enabled_count = 0
        streams_to_start = []
        for port_label, stream_list in getattr(self, "streams", {}).items():
            for stream in stream_list:
                # Check if stream is enabled
                is_enabled = stream.get("enabled", False)
                if not is_enabled:
                    ps = stream.get("protocol_selection", {})
                    is_enabled = ps.get("enabled", False)
                # Also check if stream was running (status="running")
                was_running = stream.get("status", "").lower() == "running"
                if is_enabled or was_running:
                    enabled_count += 1
                    streams_to_start.append((port_label, stream))
        
        if enabled_count > 0:
            logger.info(f"[AUTO-START] Found {enabled_count} enabled stream(s) to auto-start")
            # Use QTimer to defer execution to next event loop cycle to ensure UI is ready
            from PyQt5.QtCore import QTimer
            try:
                # Defer to next event loop cycle to ensure UI is fully ready
                QTimer.singleShot(100, lambda: self._do_auto_start_streams(streams_to_start))
            except Exception as e:
                logger.info(f"[AUTO-START] ❌ Error scheduling auto-start: {e}")
                import traceback
                traceback.print_exc()
        else:
            logger.info(f"[AUTO-START] No enabled streams to auto-start")
    
    def _do_auto_start_streams(self, streams_to_start):
        """Actually perform the auto-start of streams."""
        try:
            # Double-check UI is ready
            if not hasattr(self, "stream_table") or self.stream_table is None:
                logger.info("[AUTO-START] Stream table still not ready, aborting auto-start")
                return
            
            logger.info(f"[AUTO-START] Auto-starting {len(streams_to_start)} enabled stream(s) from session.json...")
            self.start_all_streams()
            logger.info(f"[AUTO-START] ✅ Auto-start completed")
        except Exception as e:
            logger.info(f"[AUTO-START] ❌ Error during auto-start: {e}")
            import traceback
            traceback.print_exc()
    def reset_session(self):
        """Reset the session data to default."""
        if hasattr(self, "_cancel_all_auto_stop_timers"):
            self._cancel_all_auto_stop_timers()
        self.server_interfaces = []
        self.streams = {}
        self.removed_interfaces = set()
        self.selected_servers = []
        if hasattr(self, "update_server_tree"):
            self.update_server_tree()
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()
    def save_removed_interfaces(self):
        """Save removed interfaces to a file."""
        try:
            with open("removed_interfaces.txt", "w") as f:
                for interface in self.removed_interfaces:
                    f.write(f"{interface}\n")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not save removed interfaces: {e}")
    def save_interfaces(self):
        """Save the current list of interfaces to a file."""
        try:
            # Collect current interfaces from the statistics table
            interfaces = [self.statistics_table.horizontalHeaderItem(col).text()
                          for col in range(self.statistics_table.columnCount())]

            # Save the interfaces to a file
            with open("interfaces.txt", "w") as f:
                for interface in interfaces:
                    f.write(f"{interface}\n")

            QMessageBox.information(self, "Save Successful", "Current interfaces have been saved.")
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"An error occurred while saving: {str(e)}")
    def load_removed_interfaces(self):
        """Load removed interfaces from a file."""
        try:
            with open("removed_interfaces.txt", "r") as f:
                self.removed_interfaces = {line.strip() for line in f.readlines()}
        except FileNotFoundError:
            self.removed_interfaces = set()
    def get_server_interfaces_for_tg(self, tg_id):
        """Filter the full server_interfaces list to get ports only for the selected TG."""
        for server in getattr(self, "server_interfaces", []):
            if str(server.get("tg_id")) == str(tg_id):
                return [server]
        return []
    def mark_selected_servers_offline(self):
        """v0.3.10: inverse of `make_failed_servers_online`. Operator
        asked for a way to manually mark a server offline — useful
        for silencing polling/stats on a specific TG without
        removing it from the chassis list. Sets `server["online"]
        = False`, adds to `failed_servers`, calls
        `update_server_status_icon(server, False)` which triggers
        the v0.3.9 iface-children cascade.

        Not persistent: the next health probe will flip the server
        back online if it's actually reachable. That's by design —
        the action is "mark", not "block reconnects". Operators
        who want a permanently-silent TG can remove it from the
        chassis list via File → Remove Server.
        """
        from PyQt5.QtWidgets import QMessageBox
        selected_servers = getattr(self, "selected_servers", []) or []
        if not selected_servers:
            QMessageBox.information(
                self, "No Servers Selected",
                "Tick one or more TG chassis in the server pane, then "
                "try again.",
            )
            return
        # Filter to currently-online servers — no point marking an
        # already-offline server.
        online_selected = [
            s for s in selected_servers
            if s.get("online", True) is True
        ]
        if not online_selected:
            QMessageBox.information(
                self, "No Online Servers Selected",
                "Every selected TG is already marked offline. Use "
                "'Make Selected Servers Online' to reconnect.",
            )
            return
        names = ", ".join(s.get("address", "?") for s in online_selected)
        confirm = QMessageBox.question(
            self,
            "Mark TGs offline",
            f"Mark <b>{len(online_selected)}</b> TG chassis as "
            f"offline?\n\n  {names}\n\n"
            "This:\n"
            "  • Stops the GUI from showing live data for these TGs\n"
            "  • Greys out their iface child dots (server-offline cascade)\n"
            "  • Does NOT block automatic reconnect — the next health "
            "probe will flip them back if they're reachable\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if not hasattr(self, "failed_servers") or self.failed_servers is None:
            self.failed_servers = []
        for server in online_selected:
            try:
                server["online"] = False
                if hasattr(self, "update_server_status_icon"):
                    self.update_server_status_icon(server, False)
                if server not in self.failed_servers:
                    self.failed_servers.append(server)
                logger.info(
                    f"[MANUAL OFFLINE] Marked "
                    f"{server.get('address', '?')} as offline"
                )
            except Exception as exc:
                logger.warning(
                    f"[MANUAL OFFLINE] Failed for "
                    f"{server.get('address', '?')}: {exc}"
                )
        # Flip the inverse action's enablement now that at least one
        # server is in the failed-servers list.
        try:
            if hasattr(self, "make_server_online_action"):
                self.make_server_online_action.setEnabled(True)
        except Exception:
            pass
        # Refresh the server tree so the parent LED + the cascaded
        # iface children re-render together.
        try:
            if hasattr(self, "update_server_tree"):
                self.update_server_tree()
        except Exception:
            pass

    def make_failed_servers_online(self):
        """Retry connection to offline servers manually via menu - only for selected servers."""
        if not hasattr(self, "failed_servers") or not self.failed_servers:
            QMessageBox.information(self, "No Servers Recovered", "No offline servers were recorded as failed.")
            return
        
        # Get selected servers from checkboxes
        selected_servers = getattr(self, "selected_servers", [])
        selected_tg_ids = self.get_selected_tg_ids()
        logger.info(f"[MAKE SERVER ONLINE] Selected TG IDs: {selected_tg_ids}")
        logger.info(f"[MAKE SERVER ONLINE] Selected servers count: {len(selected_servers)}")
        
        if not selected_servers:
            QMessageBox.information(self, "No Servers Selected", "Please select servers using the checkboxes to retry connections.")
            return
        
        # Filter failed servers to only include selected ones
        selected_failed_servers = []
        for server in self.failed_servers:
            if server in selected_servers:
                selected_failed_servers.append(server)
        
        if not selected_failed_servers:
            QMessageBox.information(self, "No Selected Servers Failed", "None of the selected servers are currently offline.")
            return
        
        logger.info(f"Attempting reconnection to {len(selected_failed_servers)} selected failed server(s): " +
              ", ".join([s.get('address', 'Unknown') for s in selected_failed_servers]))
        
        any_reconnected = False

        # Probe every selected failed server in parallel on background
        # threads — the previous implementation did sync requests.get
        # with a 2s timeout on the UI thread, so retrying N offline
        # servers froze the app for N × 2s. Deliver the per-server
        # results back to the main thread via a signal and tally once
        # all probes have completed.
        from PyQt5.QtCore import QThread, pyqtSignal

        class _RetryAllWorker(QThread):
            done = pyqtSignal(object, bool)  # (server_dict, ok)

            def __init__(self, server, conn_mgr):
                super().__init__()
                self._server = server
                self._conn_mgr = conn_mgr

            def run(self):
                url = self._server.get("address")
                try:
                    if self._conn_mgr is not None:
                        r = self._conn_mgr.get(
                            f"{url}/api/interfaces", timeout=2
                        )
                    else:
                        r = requests.get(
                            f"{url}/api/interfaces", timeout=2
                        )
                    self.done.emit(self._server, r.status_code == 200)
                except Exception as exc:
                    logger.debug(f"Retry probe failed for {url}: {exc}")
                    self.done.emit(self._server, False)

        if not hasattr(self, "_menu_retry_workers"):
            self._menu_retry_workers = []
        # Track completion + outcome so we can show a single summary
        # dialog once all probes return.
        progress = {"done": 0, "total": len(selected_failed_servers), "ok": 0}

        def _on_one_done(server, ok, w=None):
            progress["done"] += 1
            address = server.get("address")
            if ok:
                server["online"] = True
                self.update_server_status_icon(server, True)
                progress["ok"] += 1
                logger.info(f"Selected server {address} is now online.")
                if server in self.failed_servers:
                    self.failed_servers.remove(server)
                if hasattr(self, "server_retry_worker") and self.server_retry_worker:
                    self.server_retry_worker.remove_failed_server(server)
            else:
                logger.error(f"Still failed to connect to selected server {address}")
            if progress["done"] == progress["total"]:
                if progress["ok"]:
                    QMessageBox.information(self, "Servers Updated", "Some servers are now back online.")
                    self.update_server_tree()
                    self.fetch_and_update_statistics()
                else:
                    QMessageBox.information(self, "No Servers Recovered", "No offline servers could be brought online.")

        conn_mgr = getattr(self, "connection_manager", None)
        for server in selected_failed_servers[:]:
            address = server.get("address")
            logger.info(f"Trying to bring selected server {address} online...")
            worker = _RetryAllWorker(server, conn_mgr)
            # Permanent keepalive — see _keepalive_worker docstring.
            self._keepalive_worker(worker)
            worker.done.connect(_on_one_done)
            worker.start()
    
    def get_selected_tg_ids(self):
        """Get list of TG IDs for currently selected servers."""
        selected_servers = getattr(self, "selected_servers", [])
        tg_ids = []
        for server in selected_servers:
            tg_id = server.get("tg_id")
            if tg_id is not None:
                tg_ids.append(f"TG {tg_id}")
        return tg_ids

    def _initialize_empty_session(self):
        """Initialize an empty session with default values."""
        logger.info("Initializing an empty session.")
        if hasattr(self, "_cancel_all_auto_stop_timers"):
            self._cancel_all_auto_stop_timers()
        # Don't overwrite server_interfaces if servers were added via command line
        if not self.server_interfaces:
            self.server_interfaces = []
        self.streams = {}
        self.removed_interfaces = set()
        self.removed_servers = set()  # Initialize removed servers
        self.selected_servers = []

        # Check if servers are reachable and update their status
        if self.server_interfaces:
            logger.info(f"Checking {len(self.server_interfaces)} server(s) for connectivity...")
            for server in self.server_interfaces:
                address = server.get("address")
                if self.is_reachable(address):
                    logger.info(f"Server {address} is reachable")
                    server["online"] = True
                    try:
                        # Use shorter timeout to prevent hanging during initialization
                        r = requests.get(f"{address}/api/interfaces", timeout=2)
                        r.raise_for_status()
                        interfaces = r.json()
                        server["interfaces"] = interfaces  # Store interfaces for update_server_tree
                        logger.info(f"Fetched {len(interfaces)} interfaces from {address}")
                    except Exception as e:
                        logger.error(f"Error fetching interfaces from {address}: {e}")
                        server["online"] = False
                else:
                    logger.error(f"Server {address} is unreachable")
                    server["online"] = False

        # Update UI components to reflect the reset state
        if hasattr(self, "update_server_tree"):
            self.update_server_tree()
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()
        logger.info("Empty session initialized.")
    def is_reachable(self, server_url, timeout=2):
        """Check if a traffic generator server is reachable."""
        try:
            response = requests.get(f"{server_url}/api/ping", timeout=timeout)
            return response.status_code == 200
        except Exception:
            return False
    def restart_server(self):
        """Restart TGEN service on selected server(s) via SSH/systemctl."""
        if not hasattr(self, "server_interfaces") or not self.server_interfaces:
            QMessageBox.warning(self, "No Servers", "No servers are configured.")
            return

        # Get selected servers from tree or use all servers
        selected_items = self.server_tree.selectedItems() if hasattr(self, "server_tree") else []
        
        servers_to_restart = []
        
        if selected_items:
            # Get servers from selected items
            for item in selected_items:
                parent = item.parent()
                if parent:
                    # This is an interface item, get the server from parent
                    parent_index = self.server_tree.indexOfTopLevelItem(parent)
                    if parent_index >= 0 and parent_index < len(self.server_interfaces):
                        server = self.server_interfaces[parent_index]
                        if server not in servers_to_restart:
                            servers_to_restart.append(server)
                else:
                    # This is a server item
                    server_index = self.server_tree.indexOfTopLevelItem(item)
                    if server_index >= 0 and server_index < len(self.server_interfaces):
                        server = self.server_interfaces[server_index]
                        if server not in servers_to_restart:
                            servers_to_restart.append(server)
        
        # If no servers selected, show dialog to select servers
        if not servers_to_restart:
            dialog = QDialog(self)
            dialog.setWindowTitle("Restart TGEN Service")
            dialog.setGeometry(300, 300, 500, 400)
            
            layout = QVBoxLayout(dialog)
            
            label = QLabel("Select server(s) to restart TGEN service:")
            layout.addWidget(label)
            
            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
            layout.addWidget(list_widget)
            
            # Populate with all servers
            for server in self.server_interfaces:
                address = server.get("address", "Unknown")
                tg_id = server.get("tg_id", "?")
                online_status = "Online" if server.get("online", False) else "Offline"
                item_text = f"TG {tg_id}: {address} ({online_status})"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, server)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                list_widget.addItem(item)
            
            button_layout = QHBoxLayout()
            restart_button = QPushButton("Restart Selected")
            restart_button.clicked.connect(lambda: self._restart_selected_servers(list_widget, dialog))
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(dialog.reject)
            button_layout.addWidget(restart_button)
            button_layout.addWidget(cancel_button)
            layout.addLayout(button_layout)
            
            dialog.exec()
            return
        
        # Restart selected servers
        self._restart_servers_list(servers_to_restart)

    def _restart_selected_servers(self, list_widget, dialog):
        """Restart servers selected in the dialog."""
        servers_to_restart = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                server = item.data(Qt.UserRole)
                if server:
                    servers_to_restart.append(server)
        
        dialog.accept()
        
        if not servers_to_restart:
            QMessageBox.information(self, "No Selection", "Please select at least one server to restart.")
            return
        
        self._restart_servers_list(servers_to_restart)

    def _restart_servers_list(self, servers):
        """Restart a list of servers via SSH/systemctl."""
        if not servers:
            return
        
        # Confirm restart
        server_names = [f"TG {s.get('tg_id', '?')}: {s.get('address', 'Unknown')}" for s in servers]
        reply = QMessageBox.question(
            self,
            "Confirm TGEN Restart",
            f"Are you sure you want to restart the TGEN service on the following server(s)?\n\n" + "\n".join(server_names) + "\n\nThis will:\n• Restart the netgen-server service\n• Stop all running streams\n• Service will be back online in a few seconds",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Restart each server
        results = []
        for server in servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            
            # Extract hostname from URL (e.g., "http://svl-hp-ai-srv04:5051" -> "svl-hp-ai-srv04")
            try:
                parsed = urlparse(address)
                hostname = parsed.hostname
                if not hostname:
                    # Fallback: try to extract from address string
                    if "://" in address:
                        hostname = address.split("://")[1].split(":")[0]
                    else:
                        hostname = address.split(":")[0]
            except Exception as e:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not parse server address '{address}': {e}")
                continue
            
            if not hostname:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not extract hostname from '{address}'")
                continue
            
            # Restart via SSH. Try the canonical netgen-server unit first;
            # fall back to the legacy ostg-server name for hosts that haven't
            # been migrated yet (kept for at least one release of overlap).
            try:
                # Server-side: prefer netgen-server, fall back to ostg-server
                # if that unit isn't installed. Single SSH round-trip.
                remote = (
                    "if systemctl list-unit-files netgen-server.service "
                    "| grep -q netgen-server; then "
                    "  systemctl restart netgen-server; "
                    "elif systemctl list-unit-files ostg-server.service "
                    "| grep -q ostg-server; then "
                    "  systemctl restart ostg-server; "
                    "else "
                    "  echo 'Neither netgen-server nor ostg-server unit found' >&2; exit 1; "
                    "fi"
                )
                cmd = ["ssh", f"root@{hostname}", remote]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    results.append(f"✅ TG {tg_id} ({hostname}): Restarted successfully")
                    logger.info(f"[RESTART SERVER] Successfully restarted server TG {tg_id} on {hostname}")
                else:
                    error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    results.append(f"❌ TG {tg_id} ({hostname}): Failed - {error_msg}")
                    logger.error(f"[RESTART SERVER] Failed to restart server TG {tg_id} on {hostname}: {error_msg}")
            except subprocess.TimeoutExpired:
                results.append(f"⏱️ TG {tg_id} ({hostname}): Timeout (server may be restarting)")
                logger.info(f"[RESTART SERVER] Timeout restarting server TG {tg_id} on {hostname}")
            except FileNotFoundError:
                results.append(f"❌ TG {tg_id} ({hostname}): SSH not found (install OpenSSH client)")
                logger.info(f"[RESTART SERVER] SSH command not found - OpenSSH client may not be installed")
            except Exception as e:
                results.append(f"❌ TG {tg_id} ({hostname}): Error - {str(e)}")
                logger.info(f"[RESTART SERVER] Error restarting server TG {tg_id} on {hostname}: {e}")
        
        # Show results
        result_text = "\n".join(results)
        QMessageBox.information(
            self,
            "TGEN Restart Results",
            f"TGEN service restart results:\n\n{result_text}\n\nNote: Services may take a few seconds to come back online."
        )
        
        # Refresh server status after a delay
        if hasattr(self, "update_server_tree"):
            QTimer.singleShot(3000, self.update_server_tree)
    
    def reboot_server(self):
        """Reboot selected server(s) via SSH/reboot command."""
        if not hasattr(self, "server_interfaces") or not self.server_interfaces:
            QMessageBox.warning(self, "No Servers", "No servers are configured.")
            return
        
        # Get selected servers from tree or use all servers
        selected_items = self.server_tree.selectedItems() if hasattr(self, "server_tree") else []
        
        servers_to_reboot = []
        
        if selected_items:
            # Get servers from selected items
            for item in selected_items:
                parent = item.parent()
                if parent:
                    # This is an interface item, get the server from parent
                    parent_index = self.server_tree.indexOfTopLevelItem(parent)
                    if parent_index >= 0 and parent_index < len(self.server_interfaces):
                        server = self.server_interfaces[parent_index]
                        if server not in servers_to_reboot:
                            servers_to_reboot.append(server)
                else:
                    # This is a server item
                    server_index = self.server_tree.indexOfTopLevelItem(item)
                    if server_index >= 0 and server_index < len(self.server_interfaces):
                        server = self.server_interfaces[server_index]
                        if server not in servers_to_reboot:
                            servers_to_reboot.append(server)
        
        # If no servers selected, show dialog to select servers
        if not servers_to_reboot:
            dialog = QDialog(self)
            dialog.setWindowTitle("Reboot Physical Server")
            dialog.setGeometry(300, 300, 500, 400)
            
            layout = QVBoxLayout(dialog)
            
            label = QLabel("Select physical server(s) to reboot:")
            layout.addWidget(label)
            
            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
            layout.addWidget(list_widget)
            
            # Populate with all servers
            for server in self.server_interfaces:
                address = server.get("address", "Unknown")
                tg_id = server.get("tg_id", "?")
                online_status = "Online" if server.get("online", False) else "Offline"
                item_text = f"TG {tg_id}: {address} ({online_status})"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, server)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                list_widget.addItem(item)
            
            button_layout = QHBoxLayout()
            reboot_button = QPushButton("Reboot")
            reboot_button.clicked.connect(lambda: self._reboot_selected_servers(list_widget, dialog))
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(dialog.reject)
            button_layout.addWidget(reboot_button)
            button_layout.addWidget(cancel_button)
            layout.addLayout(button_layout)
            
            dialog.exec()
            return
        
        # Reboot selected servers
        self._reboot_servers_list(servers_to_reboot)
    
    def _reboot_selected_servers(self, list_widget, dialog):
        """Reboot servers selected in the dialog."""
        servers_to_reboot = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                server = item.data(Qt.UserRole)
                if server:
                    servers_to_reboot.append(server)
        
        dialog.accept()
        
        if not servers_to_reboot:
            QMessageBox.information(self, "No Selection", "Please select at least one server to reboot.")
            return
        
        self._reboot_servers_list(servers_to_reboot)
    
    def _reboot_servers_list(self, servers):
        """Reboot a list of servers via SSH/reboot command."""
        if not servers:
            return
        
        # Confirm reboot with strong warning
        server_names = [f"TG {s.get('tg_id', '?')}: {s.get('address', 'Unknown')}" for s in servers]
        reply = QMessageBox.warning(
            self,
            "Confirm Physical Server Reboot",
            f"⚠️ WARNING: This will REBOOT the entire physical server(s)!\n\n"
            f"Are you sure you want to reboot the following physical server(s)?\n\n" + 
            "\n".join(server_names) + 
            "\n\nThis will:\n"
            "• Stop all running streams\n"
            "• Disconnect all network connections\n"
            "• Require several minutes (3-5 minutes) for the server to come back online\n"
            "• Reset all hardware and firmware\n\n"
            "This is typically needed to fix hardware/firmware issues (e.g., Broadcom NIC firmware).",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Reboot each server
        results = []
        for server in servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            
            # Extract hostname from URL
            try:
                parsed = urlparse(address)
                hostname = parsed.hostname
                if not hostname:
                    if "://" in address:
                        hostname = address.split("://")[1].split(":")[0]
                    else:
                        hostname = address.split(":")[0]
            except Exception as e:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not parse server address '{address}': {e}")
                continue
            
            if not hostname:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not extract hostname from '{address}'")
                continue
            
            # v0.5.2: reboot via HTTP POST to /api/system/reboot. The
            # server runs as root and self-schedules the reboot.
            # NO SSH credentials needed.
            #
            # Pre-v0.5.2 this path was `ssh root@host reboot` which
            # assumed passwordless root SSH (rarely available) and
            # treated SSH exit code 255 as SUCCESS — operators saw
            # "✅ Reboot initiated successfully" with no actual
            # reboot. Operator-reported pattern; classic silent-
            # failure trap.
            #
            # On v0.5.1+ servers the new endpoint exists; on older
            # servers we get 404 and fall back to the legacy SSH
            # path. Operators on mixed-version fleets get the right
            # behavior without per-host configuration.
            try:
                # The server entry may carry a scheme already (e.g.
                # http://srv01:5050); reconstruct the API URL
                # robustly even if the address is bare host:port.
                api_base = address.rstrip("/")
                if "://" not in api_base:
                    api_base = f"http://{api_base}"
                reboot_url = f"{api_base}/api/system/reboot"
                # 5-second timeout — the endpoint returns quickly
                # (Popen-and-return, doesn't wait for the actual
                # reboot to start).
                r = requests.post(reboot_url, json={"delay_s": 5}, timeout=5)
                if r.status_code == 200:
                    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                    delay = body.get("delay_s", 5)
                    results.append(
                        f"✅ TG {tg_id} ({hostname}): Reboot scheduled in {delay}s (HTTP API)"
                    )
                    logger.info(
                        f"[REBOOT SERVER] HTTP API scheduled reboot for "
                        f"TG {tg_id} on {hostname} in {delay}s"
                    )
                    continue
                elif r.status_code == 404:
                    # Pre-v0.5.1 server — endpoint doesn't exist.
                    # Fall through to legacy SSH path.
                    logger.info(
                        f"[REBOOT SERVER] /api/system/reboot 404 on "
                        f"TG {tg_id} — falling back to SSH (legacy server)"
                    )
                else:
                    # Server-side failure; surface to operator instead
                    # of silently falling back (the SSH path would
                    # probably ALSO fail).
                    results.append(
                        f"❌ TG {tg_id} ({hostname}): HTTP reboot failed "
                        f"(HTTP {r.status_code} — {r.text[:120]})"
                    )
                    logger.error(
                        f"[REBOOT SERVER] HTTP API returned {r.status_code} "
                        f"for TG {tg_id}: {r.text[:300]}"
                    )
                    continue
            except requests.exceptions.RequestException as exc:
                # Network failure — could mean server is already
                # offline. Try SSH as a fallback only if it might
                # actually work (the operator may have keys); the
                # fallback below distinguishes real "Permission
                # denied" from network errors.
                logger.info(
                    f"[REBOOT SERVER] HTTP /api/system/reboot failed for "
                    f"TG {tg_id} ({exc}); trying SSH fallback"
                )

            # Legacy SSH path — kept for v0.4.x servers that don't
            # have /api/system/reboot. v0.5.2 makes the rc=255 check
            # honest: rc=255 with stderr containing "Permission
            # denied" / "Connection refused" is FAILURE, not the
            # expected "SSH disconnected during reboot" success.
            try:
                cmd = ["ssh",
                       "-o", "BatchMode=yes",
                       "-o", "ConnectTimeout=5",
                       f"root@{hostname}", "reboot"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                stderr_lc = (result.stderr or "").lower()
                # Real-success markers
                hard_fail_markers = (
                    "permission denied",
                    "host key verification failed",
                    "connection refused",
                    "no route to host",
                    "could not resolve hostname",
                    "operation timed out",
                )
                hard_fail = any(m in stderr_lc for m in hard_fail_markers)
                if result.returncode == 0 and not hard_fail:
                    results.append(
                        f"✅ TG {tg_id} ({hostname}): SSH reboot initiated"
                    )
                    logger.info(f"[REBOOT SERVER] SSH-init reboot for TG {tg_id}")
                elif result.returncode == 255 and not hard_fail:
                    # rc=255 WITHOUT a known-failure marker is the
                    # genuine "SSH disconnected mid-reboot" case.
                    results.append(
                        f"✅ TG {tg_id} ({hostname}): SSH reboot initiated "
                        f"(SSH disconnected — expected)"
                    )
                    logger.info(f"[REBOOT SERVER] SSH-init reboot rc=255 OK")
                else:
                    err = (result.stderr or result.stdout or "Unknown").strip()
                    results.append(
                        f"❌ TG {tg_id} ({hostname}): SSH reboot FAILED — "
                        f"{err[:200]}\n"
                        f"   Upgrade the server to v0.5.1+ to use the HTTP "
                        f"reboot endpoint (no SSH required)."
                    )
                    logger.error(
                        f"[REBOOT SERVER] SSH reboot failed for TG {tg_id}: {err}"
                    )
            except subprocess.TimeoutExpired:
                results.append(
                    f"✅ TG {tg_id} ({hostname}): SSH reboot initiated "
                    f"(SSH timed out — expected)"
                )
                logger.info(f"[REBOOT SERVER] SSH timeout for TG {tg_id} — expected")
            except FileNotFoundError:
                results.append(
                    f"❌ TG {tg_id} ({hostname}): SSH not found AND HTTP "
                    f"endpoint unreachable. Upgrade the server to v0.5.1+ "
                    f"OR install OpenSSH client locally."
                )
            except Exception as e:
                results.append(f"❌ TG {tg_id} ({hostname}): Error - {str(e)}")
                logger.info(f"[REBOOT SERVER] Error rebooting TG {tg_id}: {e}")
        
        # Show results
        result_text = "\n".join(results)
        QMessageBox.information(
            self,
            "Physical Server Reboot Results",
            result_text + "\n\n⚠️ IMPORTANT:\n"
            "• Physical servers are now rebooting\n"
            "• This will take 3-5 minutes\n"
            "• All network connections will be lost\n"
            "• Wait 3-5 minutes before checking server status\n"
            "• After reboot, hardware/firmware issues should be resolved\n"
            "• Interfaces should appear in 'ip link show' after reboot"
        )