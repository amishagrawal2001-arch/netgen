"""
AI Troubleshooting Dialog
Provides UI for AI-powered network troubleshooting
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QCheckBox, QGroupBox, QScrollArea, QWidget, QMessageBox,
    QProgressBar, QSplitter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
import requests
import json
import logging

logger = logging.getLogger(__name__)


class TroubleshootingWorker(QThread):
    """Worker thread for troubleshooting"""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, server_url, device_id, symptoms):
        super().__init__()
        self.server_url = server_url
        self.device_id = device_id
        self.symptoms = symptoms
    
    def run(self):
        try:
            response = requests.post(
                f"{self.server_url}/api/ai/troubleshoot",
                json={
                    "device_id": self.device_id,
                    "symptoms": self.symptoms
                },
                timeout=30
            )
            
            if response.status_code == 200:
                self.finished.emit(response.json())
            else:
                self.error.emit(f"Server error: {response.status_code}")
        except Exception as e:
            self.error.emit(str(e))


class AITroubleshootingDialog(QDialog):
    """AI Troubleshooting Dialog"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Troubleshooting Assistant")
        self.setMinimumSize(900, 700)
        self.resize(1000, 750)
        
        self.server_url = getattr(parent, 'server_url', 'http://localhost:5051')
        self.worker = None
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI components"""
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("🤖 AI Troubleshooting Assistant")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Splitter for input and output
        splitter = QSplitter(Qt.Horizontal)
        
        # Left: Input section
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        
        # Device selection
        device_group = QGroupBox("Device Information")
        device_layout = QFormLayout()
        
        self.device_id_input = QLineEdit()
        self.device_id_input.setPlaceholderText("Enter device ID or select from list")
        device_layout.addRow("Device ID:", self.device_id_input)
        
        # Get devices from parent if available
        if hasattr(self.parent(), 'devices_tab'):
            devices = self.parent().devices_tab.get_all_devices()
            if devices:
                device_combo = QComboBox()
                device_combo.addItem("Select device...")
                for device in devices:
                    device_id = device.get('device_id', '')
                    device_name = device.get('Device Name', '')
                    device_combo.addItem(f"{device_name} ({device_id})", device_id)
                device_combo.currentIndexChanged.connect(
                    lambda idx: self.device_id_input.setText(device_combo.currentData()) if idx > 0 else None
                )
                device_layout.addRow("Select:", device_combo)
        
        device_group.setLayout(device_layout)
        input_layout.addWidget(device_group)
        
        # Symptoms
        symptoms_group = QGroupBox("Symptoms")
        symptoms_layout = QFormLayout()
        
        self.interface_down = QCheckBox("Interface is down")
        self.link_down = QCheckBox("Link is down")
        self.packet_loss = QLineEdit()
        self.packet_loss.setPlaceholderText("0.0 to 1.0")
        self.bgp_not_established = QCheckBox("BGP not established")
        self.ospf_not_established = QCheckBox("OSPF not established")
        self.isis_not_established = QCheckBox("ISIS not established")
        self.latency = QLineEdit()
        self.latency.setPlaceholderText("Latency in ms")
        
        symptoms_layout.addRow(self.interface_down)
        symptoms_layout.addRow(self.link_down)
        symptoms_layout.addRow("Packet Loss:", self.packet_loss)
        symptoms_layout.addRow(self.bgp_not_established)
        symptoms_layout.addRow(self.ospf_not_established)
        symptoms_layout.addRow(self.isis_not_established)
        symptoms_layout.addRow("Latency (ms):", self.latency)
        
        symptoms_group.setLayout(symptoms_layout)
        input_layout.addWidget(symptoms_group)
        
        # Diagnose button
        self.diagnose_btn = QPushButton("🔍 Diagnose")
        self.diagnose_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.diagnose_btn.clicked.connect(self.start_diagnosis)
        input_layout.addWidget(self.diagnose_btn)
        
        input_layout.addStretch()
        splitter.addWidget(input_widget)
        
        # Right: Output section
        output_widget = QWidget()
        output_layout = QVBoxLayout(output_widget)
        
        output_label = QLabel("Diagnosis Results:")
        output_label.setFont(title_font)
        output_layout.addWidget(output_label)
        
        # Results display
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setPlaceholderText("Diagnosis results will appear here...")
        output_layout.addWidget(self.results_text)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        output_layout.addWidget(self.progress)
        
        splitter.addWidget(output_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        
        layout.addWidget(splitter)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        button_layout.addStretch()
        button_layout.addWidget(self.close_btn)
        layout.addLayout(button_layout)
    
    def get_symptoms(self):
        """Get symptoms from UI"""
        symptoms = {}
        
        if self.interface_down.isChecked():
            symptoms['interface_down'] = True
        if self.link_down.isChecked():
            symptoms['link_down'] = True
        
        packet_loss_text = self.packet_loss.text().strip()
        if packet_loss_text:
            try:
                symptoms['packet_loss'] = float(packet_loss_text)
            except ValueError:
                pass
        
        if self.bgp_not_established.isChecked():
            symptoms['bgp_not_established'] = True
        if self.ospf_not_established.isChecked():
            symptoms['ospf_not_established'] = True
        if self.isis_not_established.isChecked():
            symptoms['isis_not_established'] = True
        
        latency_text = self.latency.text().strip()
        if latency_text:
            try:
                symptoms['latency'] = float(latency_text)
            except ValueError:
                pass
        
        return symptoms
    
    def start_diagnosis(self):
        """Start troubleshooting diagnosis"""
        device_id = self.device_id_input.text().strip()
        if not device_id:
            QMessageBox.warning(self, "Missing Device ID", "Please enter a device ID")
            return
        
        symptoms = self.get_symptoms()
        if not symptoms:
            QMessageBox.warning(self, "No Symptoms", "Please select at least one symptom")
            return
        
        # Disable button and show progress
        self.diagnose_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # Indeterminate
        self.results_text.clear()
        self.results_text.append("🔍 Analyzing symptoms...")
        
        # Start worker thread
        self.worker = TroubleshootingWorker(self.server_url, device_id, symptoms)
        self.worker.finished.connect(self.on_diagnosis_complete)
        self.worker.error.connect(self.on_diagnosis_error)
        self.worker.start()
    
    def on_diagnosis_complete(self, result):
        """Handle diagnosis completion"""
        self.progress.setVisible(False)
        self.diagnose_btn.setEnabled(True)
        
        # Format results
        output = []
        output.append("=" * 60)
        output.append("DIAGNOSIS RESULTS")
        output.append("=" * 60)
        output.append("")
        output.append(f"Root Cause: {result.get('root_cause', 'Unknown')}")
        output.append(f"Confidence: {result.get('confidence', 0) * 100:.1f}%")
        output.append(f"Source: {result.get('source', 'Unknown')}")
        output.append("")
        output.append("Solutions:")
        for i, solution in enumerate(result.get('solutions', []), 1):
            output.append(f"  {i}. {solution}")
        
        if result.get('commands'):
            output.append("")
            output.append("Configuration Commands:")
            for vendor, commands in result['commands'].items():
                output.append(f"  {vendor.upper()}:")
                for cmd in commands:
                    output.append(f"    {cmd}")
        
        self.results_text.clear()
        self.results_text.append("\n".join(output))
    
    def on_diagnosis_error(self, error_msg):
        """Handle diagnosis error"""
        self.progress.setVisible(False)
        self.diagnose_btn.setEnabled(True)
        
        self.results_text.clear()
        self.results_text.append(f"❌ Error: {error_msg}")
        
        QMessageBox.warning(self, "Diagnosis Error", f"Failed to diagnose:\n{error_msg}")




