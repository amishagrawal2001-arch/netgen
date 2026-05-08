"""
AI Integration for Devices Tab
Adds AI buttons and features to device management
"""

from PyQt5.QtWidgets import QPushButton, QHBoxLayout, QMessageBox
from PyQt5.QtCore import Qt
import logging

logger = logging.getLogger(__name__)


def add_ai_troubleshoot_button(devices_tab, device_row, device_id):
    """Add AI troubleshoot button to device row"""
    try:
        # Get the button container (assuming it exists)
        # This would need to be integrated into the actual devices_tab implementation
        
        troubleshoot_btn = QPushButton("🤖 AI Troubleshoot")
        troubleshoot_btn.setToolTip("AI-powered troubleshooting for this device")
        troubleshoot_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 5px 10px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        
        troubleshoot_btn.clicked.connect(
            lambda: open_device_troubleshooting(devices_tab, device_id)
        )
        
        return troubleshoot_btn
    except Exception as e:
        logger.error(f"Failed to add AI troubleshoot button: {e}")
        return None


def add_ai_test_button(devices_tab, device_row, device_id):
    """Add AI test button to device row"""
    try:
        test_btn = QPushButton("🧪 AI Test")
        test_btn.setToolTip("Run AI-suggested tests on this device")
        test_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 5px 10px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        
        test_btn.clicked.connect(
            lambda: open_device_testing(devices_tab, device_id)
        )
        
        return test_btn
    except Exception as e:
        logger.error(f"Failed to add AI test button: {e}")
        return None


def open_device_troubleshooting(devices_tab, device_id):
    """Open troubleshooting dialog for device"""
    try:
        from widgets.ai_troubleshooting_dialog import AITroubleshootingDialog
        
        dialog = AITroubleshootingDialog(devices_tab)
        dialog.device_id_input.setText(device_id)
        dialog.exec_()
    except Exception as e:
        logger.error(f"Failed to open troubleshooting: {e}")
        QMessageBox.warning(
            devices_tab,
            "Error",
            f"Failed to open AI troubleshooting:\n{str(e)}"
        )


def open_device_testing(devices_tab, device_id):
    """Open test framework dialog for device"""
    try:
        from widgets.ai_test_dialog import AITestFrameworkDialog
        
        dialog = AITestFrameworkDialog(devices_tab)
        dialog.set_device_id(device_id)
        dialog.exec_()
    except Exception as e:
        logger.error(f"Failed to open testing: {e}")
        QMessageBox.warning(
            devices_tab,
            "Error",
            f"Failed to open AI testing:\n{str(e)}"
        )




