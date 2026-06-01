# traffic_client/dpdk_menu_actions.py
import logging
import requests
from requests.exceptions import ConnectionError, Timeout
from PyQt5.QtWidgets import (
    QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QLabel, QAbstractItemView, QComboBox,
    QTextEdit, QScrollArea, QCheckBox, QWidget, QApplication, QProgressDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

logger = logging.getLogger(__name__)


class _DpdkApiWorker(QThread):
    """Run a DPDK API request off the GUI thread.

    Long-timeout DPDK operations (bind/unbind up to 60s, verify up to 15s)
    used to call requests.* directly in the main thread, freezing the UI.
    This worker emits exactly one `done` signal carrying:
      data: dict or None — parsed JSON if any, augmented with
            `_status_code` and `_full_text`. None when the request failed.
      error: str — empty on success, human-readable error otherwise.
    """

    done = pyqtSignal(object, str)

    def __init__(self, method: str, url: str, json: dict = None, timeout: float = 15):
        super().__init__()
        self.method = method.upper()
        self.url = url
        self.json_payload = json
        self.timeout = timeout

    def run(self):
        try:
            if self.method == "GET":
                resp = requests.get(self.url, timeout=self.timeout)
            elif self.method == "POST":
                resp = requests.post(self.url, json=self.json_payload, timeout=self.timeout)
            else:
                self.done.emit(None, f"Unsupported HTTP method: {self.method}")
                return
            try:
                data = resp.json()
                if not isinstance(data, dict):
                    data = {"_value": data}
            except Exception:
                data = {}
            data["_status_code"] = resp.status_code
            data["_full_text"] = resp.text or ""
            self.done.emit(data, "")
        except Timeout as e:
            self.done.emit(None, f"Request timed out after {self.timeout}s ({e})")
        except ConnectionError as e:
            self.done.emit(None, f"Could not connect to server ({e})")
        except Exception as e:
            self.done.emit(None, f"Request failed: {e}")


class TrafficGenClientDPDKMenuActions():
    """DPDK menu actions for the traffic generator client."""

    # ---------- async helpers ----------

    def _track_dpdk_worker(self, worker: _DpdkApiWorker):
        """Hold a strong reference until the thread reports done.

        Without this, Python can garbage-collect the QThread mid-flight and the
        request never completes. Workers self-clear when their `done` signal fires.
        """
        if not hasattr(self, "_dpdk_workers"):
            self._dpdk_workers = set()
        self._dpdk_workers.add(worker)
        worker.done.connect(lambda *_args, w=worker: self._dpdk_workers.discard(w))

    def _make_dpdk_progress(self, title: str, label: str) -> QProgressDialog:
        """Spinner-style busy dialog (indeterminate range) with a Cancel button."""
        dlg = QProgressDialog(label, "Cancel", 0, 0, self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(200)  # don't flash on fast requests
        dlg.setValue(0)
        return dlg
    
    def show_dpdk_status(self):
        """Show DPDK status for selected server(s) — async fetch, dialog opens immediately."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return

        # Build the dialog up-front with a "Loading..." placeholder per server.
        # Each server's status arrives via an async worker that updates its row in place.
        from datetime import datetime
        dialog = QDialog(self)
        dialog.setWindowTitle("DPDK Status")
        dialog.setGeometry(300, 300, 700, 500)
        layout = QVBoxLayout(dialog)

        # Top: refresh + last-updated label so the user knows whether data is fresh.
        header_row = QHBoxLayout()
        last_updated_label = QLabel("Loading...")
        last_updated_label.setStyleSheet("color: #6b7280; font-size: 10px;")
        header_row.addWidget(last_updated_label)
        header_row.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setMaximumWidth(90)
        header_row.addWidget(refresh_btn)
        layout.addLayout(header_row)

        # Per-server containers. Filled in async.
        server_rows = {}  # address -> dict(label, text_edit, action_container, action_layout)
        for server in selected_servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            row_label = QLabel(f"TG {tg_id} ({address}):")
            row_label.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(row_label)

            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setFontFamily("Courier")
            text_edit.setFontPointSize(9)
            text_edit.setMaximumHeight(200)
            text_edit.setPlainText("Loading...")
            layout.addWidget(text_edit)

            # Container for IOMMU/VFIO action widgets that get added once status arrives.
            action_container = QWidget()
            action_layout = QVBoxLayout(action_container)
            action_layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(action_container)

            layout.addWidget(QLabel(""))  # Spacer between servers
            server_rows[address] = dict(
                server=server, label=row_label, text_edit=text_edit,
                action_container=action_container, action_layout=action_layout,
            )

        # OK button
        button_row = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        button_row.addStretch()
        button_row.addWidget(ok_button)
        layout.addLayout(button_row)

        # v0.2.97: Ctrl+Return dismisses the dialog — standard Qt
        # shortcut every other modal in the app supports. Power
        # users (read: every time the operator opens this dialog
        # repeatedly to chase a stuck bind) appreciate it.
        try:
            from PyQt5.QtWidgets import QShortcut
            from PyQt5.QtGui import QKeySequence
            _ok_sc = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_Return), dialog)
            _ok_sc.setContext(Qt.WindowShortcut)
            _ok_sc.activated.connect(dialog.accept)
        except Exception:
            pass  # shortcut is convenience; never block dialog open

        # ----- async fetch wiring -----

        def _clear_action_layout(action_layout):
            while action_layout.count():
                item = action_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)

        def _on_status(addr, data, err):
            row = server_rows.get(addr)
            if row is None:
                return
            text_edit = row["text_edit"]
            action_layout = row["action_layout"]
            _clear_action_layout(action_layout)

            if err:
                text_edit.setPlainText(f"Error: {err}")
                row["label"].setStyleSheet("font-weight: bold; font-size: 12px; color: red;")
                return
            if data is None or data.get("_status_code") != 200:
                code = data.get("_status_code") if data else "?"
                text_edit.setPlainText(f"Failed to get status (HTTP {code})")
                return

            text_edit.setPlainText(self._format_dpdk_status(data))

            # IOMMU fix banner — kick off CPU-vendor lookup async so the
            # exact GRUB params are accurate (defaults to "intel" until that returns).
            if not data.get("iommu_enabled", False):
                warning_layout = QHBoxLayout()
                warning_label = QLabel("WARNING: IOMMU is not enabled. Enable in GRUB:")
                warning_label.setStyleSheet("color: red; font-weight: bold;")
                warning_layout.addWidget(warning_label)
                params_label = QLabel("intel_iommu=on iommu=pt")
                params_label.setFont(QFont("Courier"))
                warning_layout.addWidget(params_label)
                fix_button = QPushButton("[FIX]")
                fix_button.setStyleSheet(
                    "background-color: #dc2626; color: white; font-weight: bold; padding: 5px 15px;"
                )
                server_obj = row["server"]
                fix_button.clicked.connect(
                    lambda checked, s=server_obj, d=dialog: self._handle_fix_iommu_from_status(s, d)
                )
                warning_layout.addWidget(fix_button)
                action_layout.addLayout(warning_layout)

                # Async vendor lookup updates the params label when it returns.
                cpu_worker = _DpdkApiWorker("GET", f"{addr}/api/dpdk/cpu-vendor", timeout=3)
                self._track_dpdk_worker(cpu_worker)

                def on_cpu(cd, cerr, label=params_label):
                    if cerr or cd is None or cd.get("_status_code") != 200:
                        return  # leave default
                    vendor = (cd.get("vendor") or "intel").lower()
                    if vendor in ("intel", "amd"):
                        label.setText(f"{vendor}_iommu=on iommu=pt")

                cpu_worker.done.connect(on_cpu)
                cpu_worker.start()

            # VFIO modules section
            if not data.get("vfio_pci_loaded", False):
                modules_layout = QVBoxLayout()
                
                # Warning message with Read More button
                warning_layout = QHBoxLayout()
                warning_label = QLabel("⚠ VFIO modules are not loaded")
                warning_label.setStyleSheet("color: orange; font-weight: bold; font-size: 11px;")
                warning_layout.addWidget(warning_label)
                
                # Read More button
                read_more_button = QPushButton("📖 Read More")
                read_more_button.setStyleSheet("background-color: #3b82f6; color: white; font-size: 9px; padding: 3px 10px; border-radius: 3px;")
                read_more_button.setMaximumWidth(90)
                read_more_button.clicked.connect(lambda: self._show_vfio_info_dialog())
                warning_layout.addWidget(read_more_button)
                warning_layout.addStretch()  # Push button to the left
                
                modules_layout.addLayout(warning_layout)
                
                # Manual command instructions
                manual_label = QLabel("Manual command (SSH to server):")
                manual_label.setStyleSheet("font-size: 10px; color: #666;")
                modules_layout.addWidget(manual_label)
                
                # Command text with copy button
                command_layout = QHBoxLayout()
                command_text = QLabel("sudo modprobe vfio && sudo modprobe vfio-pci")
                command_text.setStyleSheet("font-family: 'Courier New', monospace; font-size: 10px; background-color: #f3f4f6; padding: 5px; border: 1px solid #d1d5db; border-radius: 3px;")
                command_text.setWordWrap(False)
                command_layout.addWidget(command_text)
                
                # Copy to clipboard button
                copy_button = QPushButton("📋 Copy")
                copy_button.setStyleSheet("background-color: #6b7280; color: white; font-size: 9px; padding: 3px 8px; border-radius: 3px;")
                copy_button.setMaximumWidth(60)
                copy_button.clicked.connect(lambda: self._copy_to_clipboard("sudo modprobe vfio && sudo modprobe vfio-pci"))
                command_layout.addWidget(copy_button)
                
                modules_layout.addLayout(command_layout)
                
                # Load Modules button
                button_layout = QHBoxLayout()
                load_label = QLabel("Or load modules automatically:")
                load_label.setStyleSheet("font-size: 10px; color: #666;")
                button_layout.addWidget(load_label)
                
                load_modules_button = QPushButton("[LOAD MODULES]")
                load_modules_button.setStyleSheet("background-color: #f59e0b; color: white; font-weight: bold; padding: 5px 15px; border-radius: 3px;")
                server_obj_for_modules = row["server"]
                load_modules_button.clicked.connect(
                    lambda checked, s=server_obj_for_modules, d=dialog: self._handle_load_modules_from_status(s, d)
                )
                button_layout.addWidget(load_modules_button)

                modules_layout.addLayout(button_layout)
                action_layout.addLayout(modules_layout)

            # v0.2.77: inline Unbind buttons for every interface bound
            # to vfio-pci. Operators no longer have to flip to Tools →
            # Unbind Interface — the action lives next to the listing
            # that prompted them to think about it.
            bound_ifaces = []
            for iface in (data.get("interfaces") or []):
                drv = str(iface.get("driver") or "").lower()
                if drv == "vfio-pci":
                    bound_ifaces.append(iface)
            if bound_ifaces:
                server_obj = row["server"]
                unbind_header = QLabel("Bound to DPDK (vfio-pci):")
                unbind_header.setStyleSheet(
                    "color: #475569; font-size: 11px; font-weight: 600; "
                    "margin-top: 4px;"
                )
                action_layout.addWidget(unbind_header)
                for iface in bound_ifaces:
                    iface_name = (iface.get("name")
                                  or iface.get("interface")
                                  or iface.get("pci")
                                  or "(unknown)")
                    iface_row_layout = QHBoxLayout()
                    iface_lbl = QLabel(
                        f"  • {iface_name}  "
                        f"<span style='color:#6b7280;font-size:10px;'>"
                        f"{iface.get('pci', '')}</span>"
                    )
                    iface_lbl.setTextFormat(Qt.RichText)
                    iface_row_layout.addWidget(iface_lbl)
                    iface_row_layout.addStretch()
                    unbind_btn = QPushButton("Unbind")
                    unbind_btn.setStyleSheet(
                        "background-color: #ffffff; color: #b91c1c; "
                        "border: 1px solid #fca5a5; padding: 2px 12px; "
                        "border-radius: 4px; font-size: 10px;"
                    )
                    unbind_btn.setMaximumWidth(90)
                    # v0.2.97: explicit warning that this drops any
                    # DPDK traffic in flight on this interface. The
                    # confirmation dialog inside _perform_unbind is
                    # the actual gate; this tooltip surfaces the
                    # consequence before the operator commits.
                    unbind_btn.setToolTip(
                        "Unbind from vfio-pci and restore the "
                        "kernel network driver.\n\n"
                        "WARNING: any DPDK traffic currently running "
                        "on this interface will stop."
                    )
                    # Default-arg lambda captures THIS row's iface dict;
                    # without it every button would close over the loop
                    # variable's final value.
                    unbind_btn.clicked.connect(
                        lambda _checked=False, _s=server_obj.get("address"),
                        _i=iface:
                        self._perform_unbind(_s, _i)
                    )
                    iface_row_layout.addWidget(unbind_btn)
                    action_layout.addLayout(iface_row_layout)

        # ----- worker dispatch / refresh wiring -----
        pending = set()

        def fetch_one(addr):
            row = server_rows.get(addr)
            if row is None:
                return
            server = row["server"]
            if not server.get("online", True):
                row["text_edit"].setPlainText("Server is offline or unreachable.")
                row["label"].setStyleSheet("font-weight: bold; font-size: 12px; color: red;")
                return
            row["text_edit"].setPlainText("Loading...")
            _clear_action_layout(row["action_layout"])
            pending.add(addr)
            worker = _DpdkApiWorker("GET", f"{addr}/api/dpdk/status", timeout=3)
            self._track_dpdk_worker(worker)

            def cb(data, err, a=addr):
                _on_status(a, data, err)
                pending.discard(a)
                if not pending:
                    last_updated_label.setText(
                        f"Last updated at {datetime.now().strftime('%H:%M:%S')}"
                    )

            worker.done.connect(cb)
            worker.start()

        def refresh_all():
            last_updated_label.setText("Refreshing...")
            for row_addr in list(server_rows.keys()):
                fetch_one(row_addr)

        refresh_btn.clicked.connect(refresh_all)

        # Kick off the initial fetch for every selected server.
        for addr in list(server_rows.keys()):
            fetch_one(addr)

        dialog.exec()
    
    def _copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(self, "Copied", f"Command copied to clipboard:\n\n{text}")
    
    def _show_vfio_info_dialog(self):
        """Show informational dialog about VFIO modules."""
        dialog = QDialog(self)
        dialog.setWindowTitle("About VFIO Modules")
        dialog.setGeometry(300, 300, 700, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Title
        title_label = QLabel("What is VFIO?")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e40af; margin-bottom: 10px;")
        layout.addWidget(title_label)
        
        # Scrollable content area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(450)
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        
        # What is VFIO section
        what_label = QLabel("What is VFIO?")
        what_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(what_label)
        
        what_text = QLabel(
            "VFIO (Virtual Function I/O) is a Linux kernel framework that enables secure device "
            "passthrough to user-space applications. It provides:\n\n"
            "• Security: IOMMU isolation prevents devices from accessing unauthorized memory\n"
            "• Performance: Direct hardware access reduces kernel overhead\n"
            "• Flexibility: Supports virtualization and user-space drivers like DPDK"
        )
        what_text.setWordWrap(True)
        what_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(what_text)
        
        # VFIO Modules section
        modules_label = QLabel("VFIO Modules")
        modules_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(modules_label)
        
        modules_text = QLabel(
            "Two main modules are required:\n\n"
            "1. vfio (Core Module)\n"
            "   • Base framework for device passthrough\n"
            "   • Manages IOMMU groups and device isolation\n"
            "   • Provides infrastructure for secure device access\n\n"
            "2. vfio-pci (PCI Device Driver)\n"
            "   • PCI-specific driver for VFIO\n"
            "   • Binds to PCI devices (like network cards)\n"
            "   • Required for DPDK to bind NICs to user-space"
        )
        modules_text.setWordWrap(True)
        modules_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(modules_text)
        
        # Why VFIO for DPDK section
        why_label = QLabel("Why VFIO for DPDK?")
        why_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(why_label)
        
        why_text = QLabel(
            "DPDK needs direct hardware access for high-performance packet processing:\n\n"
            "Traditional Kernel Drivers:\n"
            "  Packets → Kernel Network Stack → User Application (Slower)\n\n"
            "DPDK with VFIO:\n"
            "  Packets → Direct to User-Space Application (Much Faster)\n\n"
            "This enables:\n"
            "• Line-rate performance (100Gbps, 400Gbps)\n"
            "• Low latency packet processing\n"
            "• Bypassing kernel overhead"
        )
        why_text.setWordWrap(True)
        why_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(why_text)
        
        # Requirements section
        req_label = QLabel("Requirements")
        req_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(req_label)
        
        req_text = QLabel(
            "To use VFIO with DPDK, you need:\n\n"
            "1. IOMMU Enabled ✓\n"
            "   • Intel: intel_iommu=on iommu=pt in GRUB\n"
            "   • AMD: amd_iommu=on iommu=pt in GRUB\n\n"
            "2. VFIO Modules Loaded\n"
            "   • sudo modprobe vfio\n"
            "   • sudo modprobe vfio-pci\n\n"
            "3. Device Bound to VFIO\n"
            "   • Unbind from kernel driver\n"
            "   • Bind to vfio-pci driver"
        )
        req_text.setWordWrap(True)
        req_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(req_text)
        
        # Note about Mellanox
        note_label = QLabel("Note: Mellanox/NVIDIA NICs")
        note_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(note_label)
        
        note_text = QLabel(
            "Some NICs (like Mellanox/NVIDIA) don't require VFIO:\n"
            "• They use the mlx5_core kernel driver\n"
            "• DPDK's mlx5 PMD works with the kernel driver\n"
            "• No VFIO binding needed\n\n"
            "For Broadcom, Intel, and AMD NICs, VFIO is typically required."
        )
        note_text.setWordWrap(True)
        note_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(note_text)
        
        content_layout.addStretch()
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)
        
        # Close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.setStyleSheet("background-color: #3b82f6; color: white; padding: 8px 20px; border-radius: 4px;")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _handle_load_modules_from_status(self, server, status_dialog):
        """Handle Load Modules button click from status dialog."""
        address = server.get("address", "")
        
        # Confirm action
        reply = QMessageBox.question(
            self,
            "Load VFIO Modules",
            f"This will load the VFIO kernel modules (vfio-pci and vfio) on:\n\n"
            f"Server: {address}\n\n"
            f"These modules are required for DPDK to bind NICs to vfio-pci driver.\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            # Define refresh callback to reopen status dialog
            def refresh_status():
                status_dialog.accept()  # Close current dialog
                # Longer delay to ensure modules are fully registered in kernel
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(1500, lambda: self.show_dpdk_status())
            
            self._perform_load_modules(address, refresh_callback=refresh_status)
    
    def _handle_fix_iommu_from_status(self, server, status_dialog):
        """Handle Fix IOMMU button click from status dialog."""
        status_dialog.accept()  # Close status dialog first
        
        address = server.get("address", "")
        
        # Get current IOMMU status
        try:
            response = requests.get(f"{address}/api/dpdk/status", timeout=3)
            if response.status_code == 200:
                status_data = response.json()
                current_iommu_enabled = status_data.get('iommu_enabled', False)
                iommu_details = status_data.get('iommu_details', '')
                
                # Detect CPU vendor
                cpu_vendor = "intel"  # default
                try:
                    cpu_vendor_response = requests.get(f"{address}/api/dpdk/cpu-vendor", timeout=3)
                    if cpu_vendor_response.status_code == 200:
                        cpu_vendor_data = cpu_vendor_response.json()
                        cpu_vendor = cpu_vendor_data.get('vendor', 'intel').lower()
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    pass
            else:
                QMessageBox.warning(self, "Error", f"Failed to get server status: HTTP {response.status_code}")
                return
        except requests.exceptions.ConnectionError:
            QMessageBox.warning(self, "Error", f"Server is unreachable: {address}")
            return
        except requests.exceptions.Timeout:
            QMessageBox.warning(self, "Error", f"Server request timed out: {address}")
            return
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to get server status: {str(e)}")
            return
        
        # Show IOMMU configuration dialog
        config_dialog = self._create_iommu_dialog(current_iommu_enabled, iommu_details, cpu_vendor)
        if config_dialog.exec() == QDialog.Accepted:
            enable_iommu = config_dialog.enable_iommu
            reboot_after = config_dialog.reboot_after
            if enable_iommu is not None:
                self._perform_configure_iommu(address, enable_iommu, cpu_vendor, reboot_after)
    
    def _format_dpdk_status(self, status_data):
        """Format DPDK status data for display."""
        lines = []
        lines.append(f"DPDK Installed: {'Yes' if status_data.get('dpdk_installed') else 'No'}")
        tx_worker_status = 'Found' if status_data.get('tx_worker_exists') else 'Not Found'
        lines.append(f"DPDK Packet Generator (tx_worker): {tx_worker_status}")
        lines.append(f"Hugepages Configured: {'Yes' if status_data.get('hugepages_configured') else 'No'}")
        
        if status_data.get('hugepages_configured'):
            lines.append(f"  - Available: {status_data.get('hugepages_available', 0)}")
            lines.append(f"  - Size: {status_data.get('hugepage_size', 'N/A')}")
        
        # IOMMU status (critical for vfio-pci binding)
        iommu_enabled = status_data.get('iommu_enabled', False)
        iommu_details = status_data.get('iommu_details', 'Unknown')
        iommu_status = '✓ Enabled' if iommu_enabled else '✗ Not Enabled'
        lines.append(f"\nIOMMU Status: {iommu_status}")
        lines.append(f"  - {iommu_details}")
        
        if not iommu_enabled:
            lines.append(f"")
            lines.append(f"  WARNING: IOMMU is required for vfio-pci binding (Broadcom/Intel/AMD NICs)")
            lines.append(f"  WARNING: Enable in GRUB: intel_iommu=on iommu=pt (Intel) or amd_iommu=on iommu=pt (AMD)")
            lines.append(f"  WARNING: Then reboot the server")
        
        # VFIO module status
        vfio_pci_loaded = status_data.get('vfio_pci_loaded', False)
        vfio_loaded = status_data.get('vfio_loaded', False)
        lines.append(f"\nVFIO Modules:")
        lines.append(f"  - vfio-pci: {'✓ Loaded' if vfio_pci_loaded else '✗ Not Loaded'}")
        lines.append(f"  - vfio: {'✓ Loaded' if vfio_loaded else '✗ Not Loaded'}")
        
        if not vfio_pci_loaded and iommu_enabled:
            lines.append(f"")
            lines.append(f"  WARNING: VFIO modules are not loaded")
            lines.append(f"  Manual command (SSH to server):")
            lines.append(f"    sudo modprobe vfio")
            lines.append(f"    sudo modprobe vfio-pci")
            lines.append(f"  Or use the [LOAD MODULES] button below")
        
        interfaces = status_data.get('interfaces', [])
        if interfaces:
            lines.append(f"\nInterfaces ({len(interfaces)}):")
            for iface in interfaces:
                pci = iface.get('pci', 'N/A')
                driver = iface.get('driver', 'N/A')
                status = iface.get('status', 'N/A')
                vendor = iface.get('vendor', 'N/A')
                lines.append(f"  - {iface.get('name', 'N/A')}: PCI={pci}, Driver={driver}, Status={status}, Vendor={vendor}")
        else:
            # v0.2.97: when the interfaces list is empty (typically a
            # dpdk-devbind.py tooling failure or a host with no NICs)
            # the dialog used to silently render an empty section.
            # Operators interpreted the blank as "all good" and missed
            # the underlying issue. Make the absence loud.
            lines.append("\nInterfaces (0):")
            lines.append(
                "  No interfaces detected. This usually means "
                "`dpdk-devbind.py` is missing on the server or "
                "returned no devices. Check the server's "
                "install_dpdk.sh output."
            )

        return "\n".join(lines)
    
    def bind_interface_to_dpdk(self):
        """Bind an interface to DPDK — pre-checks IOMMU before opening the picker."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return

        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for binding operations.")
            return

        server = selected_servers[0]
        address = server.get("address", "")

        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return

        progress = self._make_dpdk_progress(
            "Checking DPDK Status", f"Checking IOMMU and interfaces on {address}..."
        )

        status_worker = _DpdkApiWorker("GET", f"{address}/api/dpdk/status", timeout=3)
        self._track_dpdk_worker(status_worker)

        def on_status(data, err):
            if err:
                progress.close()
                QMessageBox.critical(self, "Status Check Failed", f"Could not reach {address}:\n\n{err}")
                return
            status_code = (data or {}).get("_status_code", 0)
            if status_code != 200 or data is None:
                progress.close()
                QMessageBox.warning(self, "Status Check Failed", f"HTTP {status_code} from {address}")
                return

            # Pre-check #3: refuse to open the bind dialog when IOMMU is disabled.
            # Binding without IOMMU will only fail downstream and produce a confusing error.
            if not data.get("iommu_enabled", False):
                progress.close()
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("IOMMU Not Enabled")
                msg.setText("DPDK binding requires IOMMU, which is not enabled on this server.")
                msg.setInformativeText(
                    "Use Tools → DPDK → Configure IOMMU... to enable it. The server will need a reboot.\n\n"
                    "After IOMMU is on, retry Bind Interface."
                )
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec()
                return

            # IOMMU is on — fetch bindable interfaces (still async).
            progress.setLabelText(f"Fetching interfaces from {address}...")
            ifaces_worker = _DpdkApiWorker("GET", f"{address}/api/dpdk/interfaces", timeout=3)
            self._track_dpdk_worker(ifaces_worker)

            def on_ifaces(idata, ierr):
                progress.close()
                if ierr:
                    QMessageBox.critical(self, "Error", f"Failed to get interfaces: {ierr}")
                    return
                istatus = (idata or {}).get("_status_code", 0)
                if istatus != 200 or idata is None:
                    QMessageBox.warning(self, "Error", f"Failed to get interfaces: HTTP {istatus}")
                    return
                all_interfaces = idata.get("interfaces", [])
                # Exclude already-bound interfaces; kernel + unbound devices are bindable.
                bindable = [
                    iface for iface in all_interfaces
                    if iface.get("driver", "") not in ("vfio-pci", "uio_pci_generic")
                ]
                if not bindable:
                    QMessageBox.information(
                        self, "No Interfaces",
                        "No interfaces available for DPDK binding.\n\n"
                        "All interfaces are already bound to DPDK, or no interfaces are available.",
                    )
                    return

                dialog = self._create_interface_selection_dialog(bindable, "Bind Interface to DPDK")
                if dialog.exec() == QDialog.Accepted:
                    selected_interface = dialog.selected_interface
                    if selected_interface:
                        self._perform_bind(address, selected_interface, force=False)

            ifaces_worker.done.connect(on_ifaces)
            ifaces_worker.start()

        status_worker.done.connect(on_status)
        status_worker.start()
    
    def unbind_interface_from_dpdk(self):
        """Unbind an interface from DPDK or restore unbound interface to kernel driver."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for unbinding operations.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # Get all interfaces from server (including unbound)
        try:
            response = requests.get(f"{address}/api/dpdk/interfaces", timeout=3)
            if response.status_code != 200:
                QMessageBox.warning(self, "Error", f"Failed to get interfaces: HTTP {response.status_code}")
                return
            
            interfaces_data = response.json()
            interfaces = interfaces_data.get('interfaces', [])
            
            # Filter to DPDK-bound interfaces AND unbound interfaces (for restoration)
            dpdk_interfaces = []
            unbound_interfaces = []
            
            for iface in interfaces:
                driver = iface.get('driver', '')
                status = iface.get('status', '')
                
                if driver in ['vfio-pci', 'uio_pci_generic']:
                    # DPDK-bound interface - can be unbound
                    dpdk_interfaces.append(iface)
                elif status == 'unbound' or (not driver or driver == 'unknown' or driver == ''):
                    # Unbound interface - can be restored to kernel driver
                    if iface.get('kernel_driver'):
                        unbound_interfaces.append(iface)
            
            # Combine both lists for the dialog
            all_interfaces = dpdk_interfaces + unbound_interfaces
            
            if not all_interfaces:
                QMessageBox.information(
                    self, 
                    "No Interfaces", 
                    "No interfaces are currently bound to DPDK, and no unbound interfaces can be restored.\n\n"
                    "To restore an unbound interface, it must have a kernel driver available."
                )
                return
            
            # Show interface selection dialog with appropriate title
            title = "Unbind from DPDK / Restore to Kernel"
            dialog = self._create_interface_selection_dialog(all_interfaces, title)
            if dialog.exec() == QDialog.Accepted:
                selected_interface = dialog.selected_interface
                if selected_interface:
                    self._perform_unbind(address, selected_interface)
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Error", f"Server is unreachable: {address}")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "Error", f"Server request timed out: {address}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get interfaces: {str(e)}")
    
    def verify_dpdk(self):
        """Verify DPDK installation on selected server(s) — async."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return

        # Pre-fill results with offline servers; the async workers fill in the rest.
        results: dict[str, str] = {}
        pending: set[str] = set()

        progress = self._make_dpdk_progress(
            "Verifying DPDK", f"Verifying DPDK on {len(selected_servers)} server(s)..."
        )

        def maybe_finish():
            if pending:
                return
            progress.close()
            ordered = []
            for s in selected_servers:
                key = s.get("address", "")
                ordered.append(results.get(key, f"TG {s.get('tg_id', '?')} ({key}): no response"))
            msg = QMessageBox(self)
            msg.setWindowTitle("DPDK Verification")
            msg.setText("\n\n".join(ordered))
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec()

        for server in selected_servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")

            if not server.get("online", True):
                results[address] = f"TG {tg_id} ({address}): Server is offline or unreachable"
                continue

            pending.add(address)
            worker = _DpdkApiWorker("GET", f"{address}/api/dpdk/verify", timeout=15)
            self._track_dpdk_worker(worker)

            def on_done(data, err, addr=address, tg=tg_id):
                if err:
                    results[addr] = f"TG {tg} ({addr}): Error — {err}"
                elif data is None or data.get("_status_code") != 200:
                    code = data.get("_status_code") if data else "?"
                    results[addr] = f"TG {tg} ({addr}): Failed to verify (HTTP {code})"
                else:
                    results[addr] = f"TG {tg} ({addr}):\n{self._format_verify_results(data)}"
                pending.discard(addr)
                maybe_finish()

            worker.done.connect(on_done)
            worker.start()

        # If everything was offline (no workers spawned), finish immediately.
        if not pending:
            maybe_finish()
    
    def _format_verify_results(self, verify_data):
        """Format DPDK verification results for display."""
        lines = []
        lines.append(f"DPDK Libraries: {'✓' if verify_data.get('dpdk_libraries') else '✗'}")
        lines.append(f"DPDK Packet Generator (tx_worker): {'✓' if verify_data.get('tx_worker_binary') else '✗'}")
        lines.append(f"Hugepages: {'✓' if verify_data.get('hugepages') else '✗'}")
        lines.append(f"Kernel Modules: {'✓' if verify_data.get('kernel_modules') else '✗'}")
        
        if verify_data.get('messages'):
            lines.append("\nDetails:")
            for msg in verify_data.get('messages', []):
                lines.append(f"  - {msg}")
        
        return "\n".join(lines)
    
    def configure_hugepages(self):
        """Configure hugepages on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for hugepage configuration.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Show hugepage configuration dialog
        dialog = self._create_hugepage_dialog()
        if dialog.exec() == QDialog.Accepted:
            num_pages = dialog.num_pages
            if num_pages:
                self._perform_configure_hugepages(address, num_pages)
    
    def load_vfio_modules(self):
        """Load VFIO kernel modules on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for loading VFIO modules.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # Show custom dialog with Read More option
        dialog = self._create_load_vfio_dialog(address)
        if dialog.exec() == QDialog.Accepted:
            # Perform the load operation
            self._perform_load_modules(address)
            # Offer to refresh status after loading
            refresh_reply = QMessageBox.question(
                self,
                "Refresh Status?",
                "Would you like to refresh the DPDK Status to verify modules are loaded?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if refresh_reply == QMessageBox.Yes:
                self.show_dpdk_status()
    
    def _create_load_vfio_dialog(self, server_address):
        """Create a dialog for loading VFIO modules with Read More option."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Load VFIO Modules")
        dialog.setGeometry(300, 300, 550, 300)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info_label = QLabel(
            f"This will load the VFIO kernel modules (vfio-pci and vfio) on:\n\n"
            f"Server: {server_address}\n\n"
            f"These modules are required for DPDK to bind NICs to vfio-pci driver."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
        layout.addWidget(info_label)
        
        # Read More button
        read_more_button = QPushButton("Read More About VFIO Modules")
        read_more_button.setStyleSheet("text-align: left; padding: 5px;")
        read_more_button.clicked.connect(lambda: self._show_vfio_info_dialog())
        layout.addWidget(read_more_button)
        
        # Buttons
        button_layout = QHBoxLayout()
        yes_button = QPushButton("Yes, Load Modules")
        yes_button.clicked.connect(dialog.accept)
        no_button = QPushButton("Cancel")
        no_button.clicked.connect(dialog.reject)
        button_layout.addWidget(yes_button)
        button_layout.addWidget(no_button)
        layout.addLayout(button_layout)
        
        return dialog
    
    def configure_iommu(self):
        """Configure IOMMU on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for IOMMU configuration.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # First, get current IOMMU status from server
        try:
            response = requests.get(f"{address}/api/dpdk/status", timeout=3)
            if response.status_code == 200:
                status_data = response.json()
                current_iommu_enabled = status_data.get('iommu_enabled', False)
                iommu_details = status_data.get('iommu_details', '')
                
                # Detect CPU vendor from server
                cpu_vendor = "intel"  # default
                try:
                    cpu_vendor_response = requests.get(f"{address}/api/dpdk/cpu-vendor", timeout=3)
                    if cpu_vendor_response.status_code == 200:
                        cpu_vendor_data = cpu_vendor_response.json()
                        cpu_vendor = cpu_vendor_data.get('vendor', 'intel').lower()
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    pass
            else:
                current_iommu_enabled = False
                iommu_details = "Could not determine current status"
                cpu_vendor = "intel"
        except requests.exceptions.ConnectionError:
            QMessageBox.warning(self, "Error", f"Server is unreachable: {address}")
            return
        except requests.exceptions.Timeout:
            QMessageBox.warning(self, "Error", f"Server request timed out: {address}")
            return
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to get server status: {str(e)}")
            return
        
        # Show IOMMU configuration dialog
        dialog = self._create_iommu_dialog(current_iommu_enabled, iommu_details, cpu_vendor)
        if dialog.exec() == QDialog.Accepted:
            enable_iommu = dialog.enable_iommu
            reboot_after = dialog.reboot_after
            if enable_iommu is not None:
                self._perform_configure_iommu(address, enable_iommu, cpu_vendor, reboot_after)
    
    def _get_selected_servers(self):
        """Get currently selected servers from the server tree."""
        if not hasattr(self, 'server_tree'):
            return []
        
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            return []
        
        servers = []
        for item in selected_items:
            # Get parent if this is an interface item
            parent = item.parent()
            if parent:
                # This is an interface, get the server from parent
                server_address = parent.text(1)  # Server address column
            else:
                # This is a server item
                server_address = item.text(1)  # Server address column
            
            # Find server in server_interfaces
            for server in getattr(self, 'server_interfaces', []):
                if server.get('address') == server_address:
                    if server not in servers:
                        servers.append(server)
                    break
        
        return servers
    
    def _create_interface_selection_dialog(self, interfaces, title):
        """Create a dialog for selecting an interface."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setGeometry(300, 300, 750, 500)
        
        layout = QVBoxLayout(dialog)
        
        label = QLabel("Select an interface:")
        layout.addWidget(label)
        
        # Add info label explaining what will happen (only for unbind dialog)
        if "Unbind" in title or "Restore" in title:
            info_label = QLabel()
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
            info_label.setText(
                "• DPDK-bound interfaces: Will be unbound and restored to kernel driver (interface will appear in 'ip link show')\n"
                "• Unbound interfaces: Will be restored to kernel driver (interface will appear in 'ip link show')"
            )
            layout.addWidget(info_label)
        elif "Bind" in title:
            info_label = QLabel()
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
            info_label.setText(
                "• Selected interface will be bound to DPDK (vfio-pci)\n"
                "• Interface will disappear from 'ip link show' (bound to DPDK for high-speed packet generation)"
            )
            layout.addWidget(info_label)
        
        list_widget = QListWidget()
        for iface in interfaces:
            name = iface.get('name', 'N/A')
            pci = iface.get('pci', 'N/A')
            driver = iface.get('driver', 'N/A')
            vendor = iface.get('vendor', 'N/A')
            status = iface.get('status', '')
            kernel_driver = iface.get('kernel_driver', '')
            
            # Determine status label based on dialog type
            if "Bind" in title:
                # Bind dialog - show current status
                if driver in ['vfio-pci', 'uio_pci_generic']:
                    status_label = "[Already DPDK-bound]"
                elif status == 'unbound' or (not driver or driver == 'unknown' or driver == ''):
                    status_label = "[Unbound → will bind to DPDK]"
                elif status == 'kernel-bound' or (driver and driver != 'unknown' and driver not in ['vfio-pci', 'uio_pci_generic']):
                    status_label = "[Kernel-bound → will bind to DPDK]"
                else:
                    status_label = "[Kernel-bound → will bind to DPDK]"
            else:
                # Unbind/Restore dialog - show what will happen
                if driver in ['vfio-pci', 'uio_pci_generic']:
                    status_label = "[DPDK-bound → will unbind]"
                elif status == 'unbound' or (not driver or driver == 'unknown' or driver == ''):
                    status_label = "[Unbound → will restore to kernel]"
                else:
                    status_label = "[Kernel-bound]"
            
            # Format display text
            item_text = f"{status_label} {name}"
            item_text += f"\n  PCI: {pci}, Current Driver: {driver}, Vendor: {vendor}"
            if kernel_driver and (status == 'unbound' or driver in ['unknown', '']):
                item_text += f", Restore to: {kernel_driver}"
            
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, iface)
            list_widget.addItem(item)
        
        layout.addWidget(list_widget)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        # Store selected interface
        def on_accept():
            selected_items = list_widget.selectedItems()
            if selected_items:
                dialog.selected_interface = selected_items[0].data(Qt.UserRole)
            else:
                dialog.selected_interface = None
        
        ok_button.clicked.connect(on_accept)
        dialog.selected_interface = None
        
        return dialog
    
    def _create_hugepage_dialog(self):
        """Create a dialog for configuring hugepages."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Configure Hugepages")
        dialog.setGeometry(300, 300, 500, 350)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info_label = QLabel("Hugepages are required for DPDK packet generation.\nThey provide high-performance memory allocation for packet buffers.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
        layout.addWidget(info_label)
        
        label = QLabel("Number of 2MB hugepages:")
        layout.addWidget(label)
        
        combo = QComboBox()
        combo.addItems(["512", "1024", "2048", "4096", "8192", "16384"])
        combo.setEditable(True)
        combo.setCurrentText("4096")
        combo.setToolTip("512 = 1GB, 1024 = 2GB, 2048 = 4GB, 4096 = 8GB, 8192 = 16GB, 16384 = 32GB")
        layout.addWidget(combo)
        
        # Memory calculation helper
        def update_memory_info():
            try:
                pages = int(combo.currentText())
                gb = (pages * 2) / 1024
                memory_label.setText(f"Memory allocation: {gb:.1f} GB ({pages} pages × 2MB)")
            except ValueError:
                memory_label.setText("Memory allocation: Invalid number")
        
        memory_label = QLabel("Memory allocation: 8.0 GB (4096 pages × 2MB)")
        memory_label.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(memory_label)
        
        combo.currentTextChanged.connect(update_memory_info)
        
        # Recommendations
        recommendations = QLabel(
            "Recommendations:\n"
            "  • 100Gbps: 2048 pages (4GB)\n"
            "  • 400Gbps: 4096-8192 pages (8-16GB)\n"
            "  • Multiple instances: 8192+ pages (16GB+)"
        )
        recommendations.setWordWrap(True)
        recommendations.setStyleSheet("color: #666; font-size: 10pt; padding: 5px;")
        layout.addWidget(recommendations)
        
        # Read More button
        read_more_button = QPushButton("Read More About Hugepages")
        read_more_button.setStyleSheet("text-align: left; padding: 5px;")
        read_more_button.clicked.connect(lambda: self._show_hugepage_info_dialog())
        layout.addWidget(read_more_button)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        def on_accept():
            try:
                dialog.num_pages = int(combo.currentText())
            except ValueError:
                dialog.num_pages = None
        
        ok_button.clicked.connect(on_accept)
        dialog.num_pages = None
        
        return dialog
    
    def _show_hugepage_info_dialog(self):
        """Show informational dialog about hugepages."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout
        
        dialog = QDialog(self)
        dialog.setWindowTitle("About Hugepages")
        dialog.setGeometry(300, 300, 700, 600)
        
        layout = QVBoxLayout(dialog)
        
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFontFamily("Courier")
        text_edit.setFontPointSize(10)
        
        hugepage_info = """
WHAT ARE HUGEPAGES?

Hugepages are large memory pages (typically 2MB or 1GB) used by the Linux kernel 
instead of the standard 4KB pages. They are essential for high-performance 
applications like DPDK.

WHY ARE HUGEPAGES NEEDED FOR DPDK?

1. Performance Benefits:
   • Reduces Translation Lookaside Buffer (TLB) misses
   • Improves memory access performance by 10-30%
   • Enables faster packet buffer allocation
   • Reduces memory fragmentation

2. DPDK Requirements:
   • DPDK applications require hugepages to allocate large contiguous memory
   • Without hugepages, DPDK applications will fail to start
   • Multiple DPDK instances need more hugepages

3. Memory Efficiency:
   • Reduces memory overhead for large packet buffers
   • Better cache locality for packet processing
   • Enables zero-copy packet operations

HOW MUCH MEMORY DO YOU NEED?

For Packet Generation Rates:
  • 1-10 Gbps:    512-1024 pages  (1-2 GB)
  • 10-100 Gbps:  1024-2048 pages (2-4 GB)
  • 100-400 Gbps: 2048-8192 pages (4-16 GB)
  • 400+ Gbps:    8192+ pages     (16+ GB)

For Multiple DPDK Instances:
  • Each instance needs ~1-2GB
  • Add 2048 pages (4GB) per additional instance
  • Example: 3 instances = 4096-6144 pages (8-12 GB)

CALCULATION:
  Memory (GB) = (Number of Pages × Page Size) / 1024
  Example: 4096 pages × 2MB = 8192 MB = 8 GB

PERMANENT CONFIGURATION:

To make hugepages persistent across reboots, add to /etc/sysctl.conf:
  vm.nr_hugepages=4096

Or use GRUB kernel parameters:
  default_hugepagesz=2M hugepagesz=2M hugepages=4096

VERIFICATION:

After configuration, verify with:
  cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
  cat /proc/meminfo | grep Huge

TROUBLESHOOTING:

If configuration fails:
  • Check available memory: free -h
  • Ensure sufficient free memory (hugepages reserve memory)
  • Try smaller values first
  • Check kernel logs: dmesg | grep -i huge

If DPDK still fails:
  • Verify hugepages are mounted: mount | grep huge
  • Check DPDK EAL options include hugepage configuration
  • Ensure user has permissions to access hugepages
        """
        
        text_edit.setPlainText(hugepage_info.strip())
        layout.addWidget(text_edit)
        
        button_layout = QHBoxLayout()
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _create_iommu_dialog(self, current_enabled, current_details, cpu_vendor):
        """Create a dialog for configuring IOMMU."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Configure IOMMU")
        dialog.setGeometry(300, 300, 600, 400)
        
        layout = QVBoxLayout(dialog)
        
        # Current status
        status_label = QLabel("Current IOMMU Status:")
        status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(status_label)
        
        status_text = QLabel(f"{'Enabled' if current_enabled else 'Not Enabled'}\n{current_details}")
        status_text.setWordWrap(True)
        layout.addWidget(status_text)
        
        layout.addWidget(QLabel(""))  # Spacer
        
        # Configuration options
        config_label = QLabel("IOMMU Configuration:")
        config_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(config_label)
        
        info_text = QLabel(
            "IOMMU is required for vfio-pci binding (Broadcom/Intel/AMD NICs).\n"
            f"Detected CPU vendor: {cpu_vendor.upper()}\n\n"
            "This will modify GRUB configuration and requires a server reboot."
        )
        info_text.setWordWrap(True)
        layout.addWidget(info_text)
        
        layout.addWidget(QLabel(""))  # Spacer
        
        # Enable/Disable options
        enable_iommu_checkbox = QComboBox()
        enable_iommu_checkbox.addItems(["Enable IOMMU", "Disable IOMMU"])
        enable_iommu_checkbox.setCurrentIndex(0 if not current_enabled else 1)
        layout.addWidget(QLabel("Action:"))
        layout.addWidget(enable_iommu_checkbox)
        
        # Reboot option
        reboot_checkbox = QComboBox()
        reboot_checkbox.addItems(["Reboot server after configuration", "Do not reboot"])
        reboot_checkbox.setCurrentIndex(0)  # Default to reboot
        layout.addWidget(QLabel(""))
        layout.addWidget(reboot_checkbox)
        
        warning_label = QLabel(
            "WARNING: Server will reboot if you choose to enable IOMMU and reboot.\n"
            "All active connections and streams will be interrupted."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(warning_label)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("Configure")
        ok_button.clicked.connect(dialog.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        def on_accept():
            dialog.enable_iommu = (enable_iommu_checkbox.currentIndex() == 0)
            dialog.reboot_after = (reboot_checkbox.currentIndex() == 0)
        
        ok_button.clicked.connect(on_accept)
        dialog.enable_iommu = None
        dialog.reboot_after = False
        
        return dialog
    
    def _perform_configure_iommu(self, server_address, enable_iommu, cpu_vendor, reboot_after):
        """Perform IOMMU configuration via API."""
        try:
            payload = {
                "enable": enable_iommu,
                "cpu_vendor": cpu_vendor,
                "reboot": reboot_after
            }
            
            # Show confirmation dialog
            action = "enable" if enable_iommu else "disable"
            reboot_msg = " and reboot the server" if reboot_after else ""
            reply = QMessageBox.question(
                self,
                "Confirm IOMMU Configuration",
                f"This will {action} IOMMU{reboot_msg}.\n\n"
                f"Server: {server_address}\n"
                f"CPU Vendor: {cpu_vendor.upper()}\n\n"
                f"{'WARNING: Server will reboot and all connections will be lost!' if reboot_after else ''}\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
            response = requests.post(f"{server_address}/api/dpdk/iommu", json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    if reboot_after:
                        QMessageBox.information(
                            self,
                            "Success",
                            f"IOMMU configuration updated successfully.\n\n"
                            f"Server will reboot in a few seconds.\n\n"
                            f"After reboot, check DPDK Status to verify IOMMU is enabled."
                        )
                    else:
                        QMessageBox.information(
                            self,
                            "Success",
                            f"IOMMU configuration updated successfully.\n\n"
                            f"GRUB configuration has been modified.\n\n"
                            f"Please reboot the server manually to apply changes:\n"
                            f"ssh root@<server> 'reboot'"
                        )
                else:
                    QMessageBox.warning(self, "Failed", f"Failed to configure IOMMU: {result.get('message', 'Unknown error')}")
            else:
                QMessageBox.warning(self, "Error", f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to configure IOMMU: {str(e)}")
    
    def _perform_bind(self, server_address, interface, force=False):
        """Perform bind operation via API — runs the POST off the GUI thread.

        15s timeouts used to freeze the UI; the work now happens in a worker
        and the response is dispatched to _handle_bind_result.
        """
        # Only include PCI if it's a valid bus address; otherwise let server resolve by interface name.
        pci = interface.get('pci', '')
        if pci and pci != 'N/A' and ':' in pci:  # Valid PCI format: 0000:XX:XX.X
            payload = {"interface": interface.get('name'), "pci": pci, "force": force}
        else:
            payload = {"interface": interface.get('name'), "force": force}

        progress = self._make_dpdk_progress(
            "Binding to DPDK",
            f"Binding {interface.get('name')} to vfio-pci on {server_address}...",
        )
        worker = _DpdkApiWorker(
            "POST", f"{server_address}/api/dpdk/bind", json=payload, timeout=15
        )
        self._track_dpdk_worker(worker)

        def cb(data, err):
            progress.close()
            self._handle_bind_result(server_address, interface, force, data, err)

        worker.done.connect(cb)
        worker.start()

    def _handle_bind_result(self, server_address, interface, force, data, err):
        """Process the bind API result and surface the appropriate dialog."""
        if err:
            QMessageBox.critical(
                self, "Bind Failed",
                f"Could not bind {interface.get('name')} on {server_address}:\n\n{err}",
            )
            return

        status_code = (data or {}).get("_status_code", 0)
        full_response = (data or {}).get("_full_text", "")
        # Strip the helper keys so the rest of the logic sees the raw API payload
        result = {k: v for k, v in (data or {}).items() if not k.startswith("_")}

        logger.info(f"[DPDK BIND] Response status: {status_code}")
        logger.info(f"[DPDK BIND] Response text (first 300 chars): {full_response[:300]}")
        logger.info(
            f"[DPDK BIND] Parsed JSON - success: {result.get('success')}, has output: {bool(result.get('output'))}"
        )

        error_msg = result.get('message', '')
        output = result.get('output', '')
        all_text = f"{error_msg} {output} {full_response}".lower()
        logger.info(f"[DPDK BIND] force={force}, 'active routes' detected: {'active routes' in all_text}")

        # v0.2.76: explicit pre-flight refusal from the server. The
        # /api/dpdk/bind endpoint now returns 409 + code=BIND_UNSAFE
        # when the candidate iface is the mgmt interface or has an
        # active stream. Surface the reason verbatim + offer a force
        # re-post (server's `force` flag bypasses the safety check).
        if (status_code == 409 and result.get("code") == "BIND_UNSAFE"
                and not force and result.get("can_force")):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Unsafe to Bind")
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setText(f"Refusing to bind '{interface.get('name')}'.")
            msg_box.setInformativeText(
                result.get("error", "Pre-flight safety check failed.") +
                "\n\nBind anyway?"
            )
            yes_btn = msg_box.addButton("Bind anyway",
                                        QMessageBox.DestructiveRole)
            msg_box.addButton("Cancel", QMessageBox.RejectRole)
            msg_box.setDefaultButton(msg_box.buttons()[-1])  # Cancel
            msg_box.activateWindow()
            msg_box.raise_()
            msg_box.exec()
            if msg_box.clickedButton() is yes_btn:
                self._perform_bind(server_address, interface, force=True)
            return

        # Active-routes prompt (only when not already forced)
        if not force and ('active routes' in all_text or 'disrupt network connectivity' in all_text):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Active Routes Detected")
            msg_box.setText(f"Interface {interface.get('name')} has active routes.")
            msg_box.setInformativeText(
                "Binding will disrupt network connectivity.\n\nDo you want to force bind anyway?"
            )
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg_box.setDefaultButton(QMessageBox.No)
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.activateWindow()
            msg_box.raise_()
            if msg_box.exec() == QMessageBox.Yes:
                self._perform_bind(server_address, interface, force=True)
            return

        if status_code == 200 and result.get('success'):
            QMessageBox.information(
                self, "Success",
                f"Interface {interface.get('name')} bound to DPDK successfully.",
            )
            return

        # Failure path — provide IOMMU troubleshooting hint when relevant
        if 'failed to bind to vfio-pci' in all_text or 'failed to bind' in all_text:
            troubleshooting = (
                "\n\nTroubleshooting:\n"
                "1. Check if IOMMU is enabled:\n"
                "   grep -i iommu /proc/cmdline\n"
                "   Should show: intel_iommu=on iommu=pt (Intel) or amd_iommu=on iommu=pt (AMD)\n\n"
                "2. If IOMMU is not enabled, add to GRUB and reboot:\n"
                "   sudo nano /etc/default/grub\n"
                "   Add: GRUB_CMDLINE_LINUX=\"... intel_iommu=on iommu=pt\"\n"
                "   sudo update-grub && sudo reboot\n\n"
                "3. Check vfio-pci module:\n"
                "   lsmod | grep vfio\n"
                "   sudo modprobe vfio-pci\n\n"
                "4. Verify device is in an IOMMU group:\n"
                "   find /sys/kernel/iommu_groups/ -name \"0000:c9:00.0\""
            )
            QMessageBox.warning(
                self, "DPDK Binding Failed",
                f"Failed to bind interface {interface.get('name')} to vfio-pci.\n\n"
                f"Error: {error_msg}\n\n"
                f"Output:\n{output}\n"
                f"{troubleshooting}",
            )
        elif output:
            QMessageBox.warning(
                self, "Failed", f"Failed to bind interface: {error_msg}\n\nOutput:\n{output}"
            )
        else:
            QMessageBox.warning(self, "Error", f"HTTP {status_code}: {full_response}")
    
    def _perform_unbind(self, server_address, interface):
        """Perform unbind operation via API — async; the 60s timeout used to freeze the UI."""
        # Extract interface name and PCI - handle devices without interface names
        interface_name = interface.get('name', '')
        pci = interface.get('pci', '')
        driver = interface.get('driver', '')
        status = interface.get('status', '')
        kernel_driver = interface.get('kernel_driver', '')

        # Determine if this is an unbound device being restored
        is_unbound = status == 'unbound' or (driver in ['unknown', ''] and kernel_driver)

        # If interface name is PCI address format (e.g., "0000:c9:00.0 (no interface)"), extract PCI
        if '(no interface)' in interface_name:
            pci = interface_name.split()[0]
            interface_name = None

        payload = {
            "interface": interface_name if interface_name and interface_name != "N/A" else None,
            "pci": pci if pci and pci != "N/A" else None,
            "kernel_driver": kernel_driver,
        }

        # Pre-validation (synchronous — surface errors before we kick off the worker)
        if not payload.get('pci') and not payload.get('interface'):
            QMessageBox.warning(
                self, "Error",
                "Cannot determine PCI address or interface name for unbinding.",
            )
            return
        if is_unbound and not kernel_driver:
            QMessageBox.warning(
                self, "Error",
                f"Cannot restore device {pci}: kernel driver not available.\n\n"
                "The device may need to be manually bound to a kernel driver.",
            )
            return

        # v0.2.97: confirm before unbinding an ACTIVE vfio-pci binding.
        # Skip the prompt when we're restoring an already-released
        # (is_unbound) device — that's a recovery operation and an
        # extra click adds friction without safety value. Both the
        # inline Unbind button in the status dialog AND the Tools →
        # Unbind menu path land here, so this single confirmation
        # covers both surfaces. Unbinding from vfio-pci RESTORES the
        # kernel driver (it doesn't drop networking like bind does),
        # but it WILL kill any DPDK traffic stream currently using
        # this interface, which is the operator's real concern.
        if not is_unbound:
            display_name = interface_name or pci or "this interface"
            confirm = QMessageBox.question(
                self,
                "Confirm DPDK unbind",
                f"Unbind <b>{display_name}</b> from vfio-pci on "
                f"<code>{server_address}</code>?\n\n"
                f"This will:\n"
                f"  • Stop any DPDK traffic running on this interface\n"
                f"  • Restore the kernel network driver "
                f"({kernel_driver or '(auto)'})\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        progress = self._make_dpdk_progress(
            "Unbinding from DPDK",
            f"Releasing {interface_name or pci} on {server_address}...",
        )
        worker = _DpdkApiWorker(
            "POST", f"{server_address}/api/dpdk/unbind", json=payload, timeout=60,
        )
        self._track_dpdk_worker(worker)

        def cb(data, err):
            progress.close()
            self._handle_unbind_result(
                interface_name, pci, kernel_driver, is_unbound, data, err
            )

        worker.done.connect(cb)
        worker.start()

    def _handle_unbind_result(self, interface_name, pci, kernel_driver, is_unbound, data, err):
        """Process the unbind API result and surface the appropriate dialog."""
        if err:
            QMessageBox.critical(
                self, "Unbind Failed",
                f"Could not unbind interface:\n\n{err}",
            )
            return

        status_code = (data or {}).get("_status_code", 0)
        full_text = (data or {}).get("_full_text", "")
        result = {k: v for k, v in (data or {}).items() if not k.startswith("_")}

        if status_code == 200:
            success = result.get('success', False)
            logger.debug(f"[DPDK UNBIND DEBUG] Response status: {status_code}, success: {success}, type: {type(success)}")
            if success is True or success == True or (isinstance(success, str) and success.lower() == 'true'):
                display_name = interface_name if interface_name else pci
                output = result.get('output', '')
                    
                # Check if output indicates firmware issue (Broadcom NICs)
                has_firmware_issue = 'firmware not responding' in output.lower() or 'firmware issue' in output.lower()
                # Check if script recommends reboot
                reboot_recommended = 'reboot is required' in output.lower() or \
                                   'reboot may be required' in output.lower() or \
                                   'server reboot' in output.lower() or \
                                   ('firmware not responding' in output.lower() and 'all recovery attempts' in output.lower())
                
                if is_unbound:
                    # Check if output indicates binding to kernel driver actually failed
                    binding_failed = 'binding to kernel driver failed' in output.lower() or \
                                    ('bind attempt completed' in output.lower() and 'current driver: unbound' in output.lower()) or \
                                    ('device remains unbound' in output.lower() and 'main goal achieved' in output.lower())
                    
                    if has_firmware_issue or binding_failed:
                        if reboot_recommended:
                            # Show critical message with reboot recommendation
                            QMessageBox.critical(
                                self, 
                                "Reboot Required", 
                                f"Device {display_name} unbound from DPDK successfully.\n\n"
                                f"⚠️ CRITICAL: Binding to kernel driver '{kernel_driver}' failed due to Broadcom firmware issue.\n\n"
                                f"All recovery attempts have been exhausted. The firmware is not responding.\n\n"
                                f"🔄 REBOOT REQUIRED:\n"
                                f"The server must be rebooted to reset the Broadcom NIC firmware.\n"
                                f"After reboot, the interface should appear normally in 'ip link show'.\n\n"
                                f"Current Status:\n"
                                f"• Device is unbound from DPDK ✓\n"
                                f"• Device remains unbound (no driver)\n"
                                f"• Interface will NOT appear in 'ip link show' until reboot\n\n"
                                f"To reboot the server:\n"
                                f"ssh root@<server> 'reboot'"
                            )
                        else:
                            QMessageBox.warning(
                                self, 
                                "Partially Successful", 
                                f"Device {display_name} unbound from DPDK successfully.\n\n"
                                f"⚠️ Warning: Binding to kernel driver '{kernel_driver}' failed.\n\n"
                                f"The device is no longer bound to DPDK (main goal achieved), but binding to "
                                f"kernel driver failed due to a Broadcom firmware initialization issue.\n\n"
                                f"📌 Important:\n"
                                f"• The device remains unbound (no driver)\n"
                                f"• Unbound devices do NOT appear in 'ip link show'\n"
                                f"• The device will still appear in the unbind dialog (this is expected)\n"
                                f"• You can try restoring it again later\n\n"
                                f"To fix and make interface visible:\n"
                                f"1. Reload driver: rmmod {kernel_driver} && modprobe {kernel_driver}\n"
                                f"2. Then bind: echo {pci} > /sys/bus/pci/drivers/{kernel_driver}/bind\n"
                                f"3. Reset PCI device (requires server access)\n"
                                f"4. Reboot if issue persists"
                            )
                    else:
                        QMessageBox.information(
                            self, 
                            "Success", 
                            f"Device {display_name} restored to kernel driver '{kernel_driver}'.\n\n"
                            f"The interface should now appear in 'ip link show'."
                        )
                else:
                    # Check if script recommends reboot
                    reboot_recommended = 'reboot is required' in output.lower() or \
                                       'reboot may be required' in output.lower() or \
                                       'server reboot' in output.lower() or \
                                       ('firmware not responding' in output.lower() and 'all recovery attempts' in output.lower())
                    # Check if binding to kernel driver actually failed
                    binding_failed = 'binding to kernel driver failed' in output.lower() or \
                                    ('bind attempt completed' in output.lower() and 'current driver: unbound' in output.lower()) or \
                                    ('device remains unbound' in output.lower() and 'main goal achieved' in output.lower())
                    
                    if has_firmware_issue or binding_failed:
                        if reboot_recommended:
                            # Show critical message with reboot recommendation
                            QMessageBox.critical(
                                self, 
                                "Reboot Required", 
                                f"Interface {display_name} unbound from DPDK successfully.\n\n"
                                f"⚠️ CRITICAL: Binding to kernel driver '{kernel_driver}' failed due to Broadcom firmware issue.\n\n"
                                f"All recovery attempts have been exhausted. The firmware is not responding.\n\n"
                                f"🔄 REBOOT REQUIRED:\n"
                                f"The server must be rebooted to reset the Broadcom NIC firmware.\n"
                                f"After reboot, the interface should appear normally in 'ip link show'.\n\n"
                                f"Current Status:\n"
                                f"• Device is unbound from DPDK ✓\n"
                                f"• Device remains unbound (no driver)\n"
                                f"• Interface will NOT appear in 'ip link show' until reboot\n\n"
                                f"To reboot the server:\n"
                                f"ssh root@<server> 'reboot'"
                            )
                        else:
                            QMessageBox.warning(
                                self, 
                                "Partially Successful", 
                                f"Interface {display_name} unbound from DPDK successfully.\n\n"
                                f"⚠️ Warning: Binding to kernel driver '{kernel_driver}' failed.\n\n"
                                f"The device is no longer bound to DPDK (main goal achieved), but binding to "
                                f"kernel driver failed due to a Broadcom firmware initialization issue.\n\n"
                                f"📌 Important:\n"
                                f"• The device remains unbound (no driver)\n"
                                f"• Unbound devices do NOT appear in 'ip link show'\n"
                                f"• The device will still appear in the unbind dialog (this is expected)\n"
                                f"• You can try restoring it again later\n\n"
                                f"To fix and make interface visible:\n"
                                f"1. Reload driver: rmmod {kernel_driver} && modprobe {kernel_driver}\n"
                                f"2. Then bind: echo {pci} > /sys/bus/pci/drivers/{kernel_driver}/bind\n"
                                f"3. Reset PCI device (requires server access)\n"
                                f"4. Reboot if issue persists"
                            )
                    else:
                        QMessageBox.information(
                            self, 
                            "Success", 
                            f"Interface {display_name} unbound from DPDK successfully.\n\n"
                            f"The interface should now appear in 'ip link show'."
                        )
                
                # Refresh DPDK status after successful unbind
                # Also refresh the interface list to update the status
                if hasattr(self, 'show_dpdk_status'):
                    QApplication.processEvents()  # Process any pending events
                    # Refresh will happen automatically when user checks status again
                
                # Note: The device may still appear in the unbind dialog if:
                # 1. It was successfully unbound from DPDK but binding to kernel driver failed (firmware issue)
                # 2. In this case, the device is still unbound and can be restored again
                # This is expected behavior - the device is no longer DPDK-bound, which was the main goal
            else:
                error_msg = result.get('message', 'Unknown error')
                output = result.get('output', '')
                full_msg = f"Failed to {'restore' if is_unbound else 'unbind'} interface: {error_msg}"
                if output:
                    full_msg += f"\n\nOutput:\n{output}"
                QMessageBox.warning(self, "Failed", full_msg)
        else:
            QMessageBox.warning(self, "Error", f"HTTP {status_code}: {full_text}")
    
    def _perform_load_modules(self, server_address, refresh_callback=None):
        """Perform VFIO module loading via API."""
        try:
            response = requests.post(f"{server_address}/api/dpdk/load_modules", timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    loaded = result.get('loaded', [])
                    QMessageBox.information(
                        self, 
                        "Success", 
                        f"VFIO modules loaded successfully:\n\n"
                        f"{', '.join(loaded)}\n\n"
                        f"You can now bind interfaces to DPDK."
                    )
                    # If callback provided, refresh the status
                    if refresh_callback:
                        refresh_callback()
                else:
                    # Show detailed error message
                    failed = result.get('failed', [])
                    loaded = result.get('loaded', [])
                    message = result.get('message', 'Unknown error')
                    
                    # Build detailed error message
                    error_details = []
                    for m in failed:
                        error_details.append(f"  • {m['module']}: {m.get('error', 'Unknown error')}")
                    
                    msg = f"{message}\n\n"
                    if error_details:
                        msg += "Module errors:\n" + "\n".join(error_details) + "\n\n"
                    if loaded:
                        msg += f"Successfully loaded: {', '.join(loaded)}\n\n"
                    msg += "Check server logs for more details:\n"
                    msg += f"  journalctl -u netgen-server -n 50"
                    
                    # Use a scrollable dialog for long error messages
                    error_dialog = QDialog(self)
                    error_dialog.setWindowTitle("Failed to Load VFIO Modules")
                    error_dialog.setGeometry(300, 300, 600, 400)
                    
                    layout = QVBoxLayout(error_dialog)
                    
                    error_text = QTextEdit()
                    error_text.setReadOnly(True)
                    error_text.setPlainText(msg)
                    error_text.setFontFamily("Courier")
                    error_text.setFontPointSize(10)
                    layout.addWidget(error_text)
                    
                    button_layout = QHBoxLayout()
                    button_layout.addStretch()
                    ok_button = QPushButton("OK")
                    ok_button.clicked.connect(error_dialog.accept)
                    button_layout.addWidget(ok_button)
                    layout.addLayout(button_layout)
                    
                    error_dialog.exec()
            else:
                # Try to parse error response
                try:
                    error_data = response.json()
                    error_msg = error_data.get('message', response.text)
                except Exception:
                    error_msg = response.text
                
                QMessageBox.critical(
                    self, 
                    "Error", 
                    f"HTTP {response.status_code}\n\n{error_msg[:500]}"
                )
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Error", f"Server is unreachable: {server_address}")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "Error", f"Server request timed out: {server_address}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load modules: {str(e)}")
    
    def _perform_configure_hugepages(self, server_address, num_pages):
        """Perform hugepage configuration via API."""
        try:
            payload = {
                "num_pages": num_pages,
                "page_size": "2MB"
            }
            
            response = requests.post(f"{server_address}/api/dpdk/hugepages", json=payload, timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    # v0.2.77: show requested vs actually-allocated so
                    # operators see when the kernel capped it (memory
                    # fragmentation / NUMA-node imbalance). Falls back
                    # gracefully on pre-0.2.77 servers that don't return
                    # the actual_allocated field.
                    requested = result.get('requested', num_pages)
                    allocated = result.get('actual_allocated')
                    page_sz = result.get('page_size', '2MB')
                    if allocated is None:
                        # Legacy server — just confirm the request went in.
                        body = (f"Hugepages configured successfully: "
                                f"{requested} x {page_sz} pages")
                    elif allocated == requested:
                        body = (f"Allocated {allocated} x {page_sz} "
                                f"hugepages on {server_address}.")
                    else:
                        body = (
                            f"⚠ Partial allocation on {server_address}.\n\n"
                            f"Requested: {requested} x {page_sz}\n"
                            f"Actually allocated: {allocated} x {page_sz}\n\n"
                            f"The kernel couldn't satisfy the full "
                            f"request — usually means memory is "
                            f"fragmented or the NUMA node is short. "
                            f"Reboot may help; or allocate fewer pages."
                        )
                    QMessageBox.information(self, "Hugepages", body)
                else:
                    QMessageBox.warning(self, "Failed", f"Failed to configure hugepages: {result.get('message', 'Unknown error')}")
            else:
                QMessageBox.warning(self, "Error", f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to configure hugepages: {str(e)}")

