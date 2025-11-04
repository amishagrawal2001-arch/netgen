# packet_capture.py #
import os
from PyQt5.QtWidgets import QMessageBox, QFileDialog

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

        tg_id = parent_item.text(0)
        port_name = selected_item.text(0).strip()  # No longer need to remove "Port:" prefix
        full_interface = f"{tg_id} - {port_name}"
        server_url = next((s["address"] for s in self.server_interfaces if f"TG {s['tg_id']}" == tg_id), None)
        if not server_url:
            QMessageBox.critical(self, "Error", f"Could not determine server URL for {tg_id}")
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