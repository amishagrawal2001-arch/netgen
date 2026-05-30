# packet_capture.py #
import os
from PyQt5.QtWidgets import QMessageBox, QFileDialog, QLabel

class TrafficGenClientPacketCapture():
    def start_packet_capture(self):
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a TG port to capture.")
            return

        selected_item = selected_items[0]
        parent_item = selected_item.parent()
        if parent_item is None:
            QMessageBox.warning(self, "Invalid Selection", "Please select a port under a TG server.")
            return

        # Resolve TG ID. The TG node in the server tree no longer puts
        # text into text(0) — update_server_tree wires a custom itemWidget
        # (pixmap-only status icon + a separate "TG N" QLabel). The old
        # `parent_item.text(0)` returned "", so the server_url lookup
        # below ("TG " + tg_id == "TG ") never matched and packet capture
        # silently bailed with "Could not determine server URL". Mirror
        # the 3-tier resolution paste_stream_to_interface uses.
        tg_id = ""
        try:
            tg_widget = self.server_tree.itemWidget(parent_item, 0)
            if tg_widget:
                for lbl in tg_widget.findChildren(QLabel):
                    txt = (lbl.text() or "").strip()
                    if txt:
                        tg_id = txt
                        break
        except Exception:
            pass
        if not tg_id:
            try:
                idx = self.server_tree.indexOfTopLevelItem(parent_item)
                if 0 <= idx < len(getattr(self, "server_interfaces", [])):
                    srv = self.server_interfaces[idx]
                    tg_id = f"TG {srv.get('tg_id', '0')}"
            except Exception:
                pass
        if not tg_id:
            tg_id = parent_item.text(0).strip()

        port_name = selected_item.text(0).strip()  # No longer need to remove "Port:" prefix
        full_interface = f"{tg_id} - {port_name}"
        server_url = next((s["address"] for s in self.server_interfaces if f"TG {s['tg_id']}" == tg_id), None)
        if not server_url:
            QMessageBox.critical(self, "Error", f"Could not determine server URL for {tg_id!r}. "
                                                f"Re-select the port from the server tree and try again.")
            return

        self.capture_client.server_url = server_url
        result = self.capture_client.start_capture(port_name)
        if "error" in result:
            QMessageBox.critical(self, "Capture Failed", result["error"])
            return

        self.capturing_interface = port_name
        self.capture_filepath = result.get("filepath")
        self.start_capture_action.setEnabled(False)
        self.stop_capture_action.setEnabled(True)
        QMessageBox.information(self, "Capture Started", f"Capture started on {port_name}")
    def stop_packet_capture(self):
        if not self.capturing_interface:
            QMessageBox.warning(self, "No Capture Running", "No interface is currently capturing.")
            return

        result = self.capture_client.stop_capture(self.capturing_interface)
        if "error" in result:
            QMessageBox.critical(self, "Stop Failed", result["error"])
            return

        if self.capture_filepath:
            # 🆕 Default save directory and filename
            default_dir = os.path.expanduser("~/Downloads")
            default_filename = f"{self.capturing_interface}.pcap"
            default_path = os.path.join(default_dir, default_filename)

            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Capture File",
                default_path,
                "PCAP Files (*.pcap)"
            )

            if save_path:
                download_result = self.capture_client.download_capture(self.capture_filepath, save_path)
                if "error" in download_result:
                    QMessageBox.warning(self, "Download Failed", download_result["error"])
                else:
                    QMessageBox.information(self, "Download Complete", f"File saved to: {save_path}")

        self.capturing_interface = None
        self.capture_filepath = None
        self.start_capture_action.setEnabled(True)
        self.stop_capture_action.setEnabled(False)