"""
AI Model Management Dialog
UI for managing AI model versions, updates, and rollbacks
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QTableWidget,
    QTableWidgetItem, QMessageBox, QGroupBox, QProgressBar,
    QComboBox, QHeaderView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
import requests
import json
import logging

logger = logging.getLogger(__name__)


# v0.5.245-followup (audit AI-*): numeric-aware version sort so '10.0'
# orders before '2.0' in the versions table. Falls back through three
# strategies; the outermost try/except means a malformed entry can never
# take down the display.
def _sort_versions_desc(items):
    """Return a list of (version, info) sorted newest-first by version."""
    items = list(items)
    try:
        from packaging.version import Version  # type: ignore

        return sorted(items, key=lambda x: Version(x[0]), reverse=True)
    except Exception:
        pass
    try:
        return sorted(
            items,
            key=lambda x: tuple(int(p) for p in str(x[0]).split(".")),
            reverse=True,
        )
    except Exception:
        pass
    try:
        return sorted(items, key=lambda x: str(x[0]), reverse=True)
    except Exception:
        return items


class ModelUpdateWorker(QThread):
    """Worker thread for model updates"""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    
    def __init__(self, server_url, action, version=None):
        super().__init__()
        self.server_url = server_url
        self.action = action
        self.version = version
    
    def run(self):
        try:
            if self.action == "backup":
                response = requests.post(
                    f"{self.server_url}/api/ai/model/backup",
                    timeout=30
                )
            elif self.action == "train":
                self.progress.emit("Training model...")
                response = requests.post(
                    f"{self.server_url}/api/ai/model/train",
                    json={"version": self.version},
                    timeout=300
                )
            elif self.action == "activate":
                response = requests.post(
                    f"{self.server_url}/api/ai/model/activate",
                    json={"version": self.version},
                    timeout=30
                )
            elif self.action == "rollback":
                response = requests.post(
                    f"{self.server_url}/api/ai/model/rollback",
                    json={"version": self.version},
                    timeout=30
                )
            elif self.action == "list":
                response = requests.get(
                    f"{self.server_url}/api/ai/model/versions",
                    timeout=30
                )
            else:
                self.error.emit(f"Unknown action: {self.action}")
                return
            
            if response.status_code == 200:
                self.finished.emit(response.json())
            else:
                self.error.emit(f"Server error: {response.status_code} - {response.text}")
        except Exception as e:
            self.error.emit(str(e))


class AIModelManagementDialog(QDialog):
    """AI Model Management Dialog"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Model Management")
        self.setMinimumSize(800, 600)
        self.resize(900, 700)
        
        self.server_url = getattr(parent, 'server_url', 'http://localhost:5051')
        self.worker = None
        
        self.setup_ui()
        self.load_versions()
    
    def setup_ui(self):
        """Setup UI components"""
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("🤖 AI Model Management")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Current Model Info
        current_group = QGroupBox("Current Model")
        current_layout = QFormLayout()
        
        self.current_version_label = QLabel("Loading...")
        self.current_training_cases_label = QLabel("Loading...")
        self.current_created_label = QLabel("Loading...")
        
        current_layout.addRow("Version:", self.current_version_label)
        current_layout.addRow("Training Cases:", self.current_training_cases_label)
        current_layout.addRow("Created:", self.current_created_label)
        
        current_group.setLayout(current_layout)
        layout.addWidget(current_group)
        
        # Model Versions Table
        versions_group = QGroupBox("Available Versions")
        versions_layout = QVBoxLayout()
        
        self.versions_table = QTableWidget()
        self.versions_table.setColumnCount(4)
        self.versions_table.setHorizontalHeaderLabels([
            "Version", "Training Cases", "Created", "Status"
        ])
        self.versions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.versions_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.versions_table.setSelectionMode(QTableWidget.SingleSelection)
        self.versions_table.itemSelectionChanged.connect(self.on_version_selected)
        
        versions_layout.addWidget(self.versions_table)
        versions_group.setLayout(versions_layout)
        layout.addWidget(versions_group)
        
        # Actions
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout()
        
        # Backup button
        backup_btn = QPushButton("💾 Backup Current Model")
        backup_btn.setToolTip("Create a backup of the current model")
        backup_btn.clicked.connect(self.backup_model)
        actions_layout.addWidget(backup_btn)
        
        # Train new model
        train_layout = QHBoxLayout()
        train_layout.addWidget(QLabel("New Version:"))
        self.new_version_input = QLineEdit()
        self.new_version_input.setPlaceholderText("e.g., 2.0")
        train_layout.addWidget(self.new_version_input)
        
        train_btn = QPushButton("🚀 Train New Model")
        train_btn.setToolTip("Train a new model version from knowledge base")
        train_btn.clicked.connect(self.train_model)
        train_layout.addWidget(train_btn)
        actions_layout.addLayout(train_layout)
        
        # Activate/Rollback
        activate_layout = QHBoxLayout()
        self.selected_version_label = QLabel("No version selected")
        activate_layout.addWidget(self.selected_version_label)
        
        activate_btn = QPushButton("✅ Activate Version")
        activate_btn.setToolTip("Activate selected version")
        activate_btn.clicked.connect(self.activate_model)
        activate_layout.addWidget(activate_btn)
        
        rollback_btn = QPushButton("🔄 Rollback to Version")
        rollback_btn.setToolTip("Rollback to selected version")
        rollback_btn.clicked.connect(self.rollback_model)
        activate_layout.addWidget(rollback_btn)
        
        actions_layout.addLayout(activate_layout)
        
        # Refresh button
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.load_versions)
        actions_layout.addWidget(refresh_btn)
        
        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        
        # Status/Results
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(100)
        self.status_text.setPlaceholderText("Status messages will appear here...")
        layout.addWidget(self.status_text)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
    
    def on_version_selected(self):
        """Handle version selection"""
        selected_items = self.versions_table.selectedItems()
        if selected_items:
            row = selected_items[0].row()
            version_item = self.versions_table.item(row, 0)
            if version_item:
                version = version_item.text()
                self.selected_version_label.setText(f"Selected: v{version}")
    
    def load_versions(self):
        """Load model versions from server"""
        self.status_text.clear()
        self.status_text.append("Loading versions...")
        
        self.worker = ModelUpdateWorker(self.server_url, "list")
        self.worker.finished.connect(self.on_versions_loaded)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_versions_loaded(self, result):
        """Handle versions loaded"""
        self.status_text.clear()
        
        metadata = result.get("troubleshooting_classifier", {})
        current_version = metadata.get("current_version", "Unknown")
        versions = metadata.get("versions", {})
        
        # Update current model info
        self.current_version_label.setText(current_version)
        
        if current_version in versions:
            current_info = versions[current_version]
            self.current_training_cases_label.setText(str(current_info.get("training_cases", "Unknown")))
            # v0.5.245-followup (audit AI-*): server may return null for
            # created_at; coerce to empty string before slicing.
            current_created_at = current_info.get("created_at") or ""
            self.current_created_label.setText(current_created_at[:10] if current_created_at else "Unknown")

        # v0.5.245-followup (audit AI-*): sort versions numerically so
        # '10.0' comes before '2.0'. Prefer packaging.version.Version; fall
        # back to a tuple-of-ints key; last-resort fall back to string sort
        # so a malformed version never crashes the display.
        sorted_versions = _sort_versions_desc(versions.items())

        # Populate versions table
        self.versions_table.setRowCount(len(versions))

        for row, (version, info) in enumerate(sorted_versions):
            # Version
            version_item = QTableWidgetItem(version)
            self.versions_table.setItem(row, 0, version_item)

            # Training Cases
            cases_item = QTableWidgetItem(str(info.get("training_cases", "Unknown")))
            self.versions_table.setItem(row, 1, cases_item)

            # Created (v0.5.245-followup (audit AI-*): null-safe)
            created = info.get("created_at") or ""
            if created:
                display_created = created[:10] if len(created) > 10 else created
            else:
                display_created = "Unknown"
            created_item = QTableWidgetItem(display_created)
            self.versions_table.setItem(row, 2, created_item)
            
            # Status
            status = "← CURRENT" if version == current_version else ""
            status_item = QTableWidgetItem(status)
            if version == current_version:
                status_item.setForeground(Qt.darkGreen)
            self.versions_table.setItem(row, 3, status_item)
        
        self.status_text.append(f"✅ Loaded {len(versions)} version(s)")
    
    def backup_model(self):
        """Backup current model"""
        reply = QMessageBox.question(
            self,
            "Backup Model",
            "Create a backup of the current model?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_text.clear()
        self.status_text.append("Creating backup...")
        
        self.worker = ModelUpdateWorker(self.server_url, "backup")
        self.worker.finished.connect(self.on_backup_complete)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_backup_complete(self, result):
        """Handle backup completion"""
        self.progress.setVisible(False)
        self.status_text.append("✅ Backup created successfully")
        QMessageBox.information(self, "Backup Complete", "Model backup created successfully.")
    
    def train_model(self):
        """Train new model"""
        version = self.new_version_input.text().strip()
        if not version:
            QMessageBox.warning(self, "Missing Version", "Please enter a version number (e.g., 2.0)")
            return
        
        reply = QMessageBox.question(
            self,
            "Train New Model",
            f"Train new model version {version}?\n\nThis may take several minutes.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_text.clear()
        self.status_text.append(f"Training model version {version}...")
        self.status_text.append("This may take several minutes. Please wait...")
        
        self.worker = ModelUpdateWorker(self.server_url, "train", version)
        self.worker.finished.connect(self.on_train_complete)
        self.worker.error.connect(self.on_error)
        self.worker.progress.connect(self.on_progress)
        self.worker.start()
    
    def on_progress(self, message):
        """Handle progress update"""
        self.status_text.append(message)
    
    def on_train_complete(self, result):
        """Handle training completion"""
        self.progress.setVisible(False)
        self.status_text.append("✅ Model training completed!")
        self.status_text.append(f"Training cases: {result.get('training_cases', 'Unknown')}")
        
        QMessageBox.information(
            self,
            "Training Complete",
            f"Model version {result.get('version', 'Unknown')} trained successfully!\n\n"
            f"Training cases: {result.get('training_cases', 'Unknown')}\n\n"
            "You can now activate this version."
        )
        
        # Refresh versions
        self.load_versions()
    
    def activate_model(self):
        """Activate selected model version"""
        selected_items = self.versions_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a version to activate")
            return
        
        row = selected_items[0].row()
        version_item = self.versions_table.item(row, 0)
        if not version_item:
            return
        
        version = version_item.text()
        
        reply = QMessageBox.question(
            self,
            "Activate Model",
            f"Activate model version {version}?\n\n"
            "The current model will be backed up automatically.\n"
            "Server restart may be required.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_text.clear()
        self.status_text.append(f"Activating version {version}...")
        
        self.worker = ModelUpdateWorker(self.server_url, "activate", version)
        self.worker.finished.connect(self.on_activate_complete)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_activate_complete(self, result):
        """Handle activation completion"""
        self.progress.setVisible(False)
        self.status_text.append("✅ Model activated successfully!")
        self.status_text.append("Please restart the server to use the new model.")
        
        QMessageBox.information(
            self,
            "Model Activated",
            f"Model version {result.get('version', 'Unknown')} activated successfully!\n\n"
            "Please restart the server to use the new model."
        )
        
        # Refresh versions
        self.load_versions()
    
    def rollback_model(self):
        """Rollback to selected model version"""
        selected_items = self.versions_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a version to rollback to")
            return
        
        row = selected_items[0].row()
        version_item = self.versions_table.item(row, 0)
        if not version_item:
            return
        
        version = version_item.text()
        
        reply = QMessageBox.question(
            self,
            "Rollback Model",
            f"Rollback to model version {version}?\n\n"
            "The current model will be backed up automatically.\n"
            "Server restart may be required.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_text.clear()
        self.status_text.append(f"Rolling back to version {version}...")
        
        self.worker = ModelUpdateWorker(self.server_url, "rollback", version)
        self.worker.finished.connect(self.on_rollback_complete)
        self.worker.error.connect(self.on_error)
        self.worker.start()
    
    def on_rollback_complete(self, result):
        """Handle rollback completion"""
        self.progress.setVisible(False)
        self.status_text.append("✅ Model rolled back successfully!")
        self.status_text.append("Please restart the server to use the rolled back model.")
        
        QMessageBox.information(
            self,
            "Rollback Complete",
            f"Rolled back to model version {result.get('version', 'Unknown')} successfully!\n\n"
            "Please restart the server to use the rolled back model."
        )
        
        # Refresh versions
        self.load_versions()
    
    def on_error(self, error_msg):
        """Handle errors"""
        self.progress.setVisible(False)
        self.status_text.append(f"❌ Error: {error_msg}")
        QMessageBox.warning(self, "Error", f"Operation failed:\n{error_msg}")

    # v0.5.245-followup (audit AI-*): stop the worker thread cleanly so
    # signals do not fire against a deleted dialog.
    def closeEvent(self, event):
        """Stop the worker thread cleanly before closing."""
        worker = getattr(self, "worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    for signal_name, slot in (
                        ("finished", None),
                        ("error", self.on_error),
                        ("progress", self.on_progress),
                    ):
                        try:
                            getattr(worker, signal_name).disconnect()
                        except (TypeError, RuntimeError):
                            pass
                    try:
                        worker.requestInterruption()
                    except (AttributeError, RuntimeError):
                        pass
                    worker.quit()
                    worker.wait(2000)
            except RuntimeError:
                pass
        super().closeEvent(event)




