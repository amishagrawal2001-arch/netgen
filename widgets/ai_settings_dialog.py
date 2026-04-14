"""
AI Settings Dialog
Configure AI settings and preferences
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox,
    QGroupBox, QMessageBox, QTextEdit, QWidget
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
import json
import os
import logging
import requests

logger = logging.getLogger(__name__)


class AISettingsDialog(QDialog):
    """AI Settings Dialog"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Settings")
        self.setMinimumSize(600, 500)
        self.resize(700, 600)
        
        self.settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
        self.settings = self.load_settings()
        
        self.setup_ui()
    
    def load_settings(self):
        """Load settings from file"""
        default_settings = {
            "use_ai_api": False,
            "openai_api_key": "",
            "openai_api_base": "",  # For Groq and other OpenAI-compatible APIs
            "cloud_model": "",  # User-selected cloud model (for agent mode)
            "use_local_llm": True,
            "ollama_url": "http://localhost:11434",
            "ollama_model": "llama2",
            "use_local_ml": True,
            "preferred_ai_mode": "local",
            "test_timeout": 300,
            "code_generation_language": "python"
        }
        
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    loaded = json.load(f)
                    default_settings.update(loaded)
            except Exception as e:
                logger.warning(f"Failed to load AI settings: {e}")
        
        return default_settings
    
    def save_settings(self):
        """Save settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
            logger.info(f"AI settings saved successfully to {self.settings_file}")
            logger.info(f"  - preferred_ai_mode: {self.settings.get('preferred_ai_mode', 'not set')}")
            return True
        except Exception as e:
            logger.error(f"Failed to save AI settings: {e}")
            return False
    
    def setup_ui(self):
        """Setup UI components"""
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("🤖 AI Settings")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # AI Mode Selection
        mode_group = QGroupBox("AI Mode")
        mode_layout = QFormLayout()
        
        self.ai_mode = QComboBox()
        self.ai_mode.addItems(["Local Only", "Cloud Only", "Hybrid (Local + Cloud)"])
        mode_index = {"local": 0, "cloud": 1, "hybrid": 2}.get(
            self.settings.get("preferred_ai_mode", "local"), 0
        )
        self.ai_mode.setCurrentIndex(mode_index)
        # Log the initial loaded value
        logger.info(f"AI Settings Dialog loaded with preferred_ai_mode: {self.settings.get('preferred_ai_mode', 'local')} (index: {mode_index})")
        mode_layout.addRow("Preferred Mode:", self.ai_mode)
        
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # Cloud AI Settings
        cloud_group = QGroupBox("Cloud AI (OpenAI)")
        cloud_layout = QFormLayout()
        
        self.use_cloud_ai = QCheckBox("Enable Cloud AI")
        self.use_cloud_ai.setChecked(self.settings.get("use_ai_api", False))
        cloud_layout.addRow(self.use_cloud_ai)
        
        self.openai_key = QLineEdit()
        self.openai_key.setEchoMode(QLineEdit.Password)
        self.openai_key.setText(self.settings.get("openai_api_key", ""))
        self.openai_key.setPlaceholderText("sk-... or gsk-... (for Groq)")
        cloud_layout.addRow("API Key:", self.openai_key)
        
        self.openai_base_url = QLineEdit()
        self.openai_base_url.setText(self.settings.get("openai_api_base", ""))
        self.openai_base_url.setPlaceholderText("https://api.groq.com/openai/v1 (for Groq) or leave empty for OpenAI")
        self.openai_base_url.textChanged.connect(self.update_cloud_models)  # Update models when URL changes
        cloud_layout.addRow("API Base URL:", self.openai_base_url)
        
        # Cloud Model Selection (for agent mode)
        self.cloud_model = QComboBox()
        self.cloud_model.setEditable(True)  # Allow manual entry
        self.cloud_model.setMinimumWidth(250)
        self.update_cloud_models()  # Initialize with available models
        # Restore saved model selection
        saved_cloud_model = self.settings.get("cloud_model", "")
        if saved_cloud_model:
            # Check if saved model is in the list, if not add it
            if self.cloud_model.findText(saved_cloud_model) == -1:
                self.cloud_model.addItem(saved_cloud_model)
            self.cloud_model.setCurrentText(saved_cloud_model)
        cloud_layout.addRow("Cloud Model (Agent):", self.cloud_model)
        
        # Add info label
        info_label = QLabel("💡 For Groq: Use gsk-... key and set Base URL to https://api.groq.com/openai/v1\n💡 Model selection is used for Agent Mode test plan generation")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 10pt;")
        cloud_layout.addRow("", info_label)
        
        cloud_group.setLayout(cloud_layout)
        layout.addWidget(cloud_group)
        
        # Local AI Settings
        local_group = QGroupBox("Local AI")
        local_layout = QFormLayout()
        
        self.use_local_llm = QCheckBox("Enable Local LLM (Ollama)")
        self.use_local_llm.setChecked(self.settings.get("use_local_llm", True))
        local_layout.addRow(self.use_local_llm)
        
        self.ollama_url = QLineEdit()
        self.ollama_url.setText(self.settings.get("ollama_url", "http://localhost:11434"))
        self.ollama_url.textChanged.connect(self.refresh_models)  # Refresh when URL changes
        local_layout.addRow("Ollama URL:", self.ollama_url)
        
        # Model selection with dropdown
        model_layout = QHBoxLayout()
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)  # Allow manual entry if model not in list
        self.ollama_model.setMinimumWidth(250)
        self.refresh_models()  # Load available models
        model_layout.addWidget(self.ollama_model)
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setToolTip("Refresh list of available Ollama models")
        refresh_btn.clicked.connect(self.refresh_models)
        refresh_btn.setMaximumWidth(80)
        model_layout.addWidget(refresh_btn)
        
        model_widget = QWidget()
        model_widget.setLayout(model_layout)
        local_layout.addRow("Ollama Model:", model_widget)
        
        self.use_local_ml = QCheckBox("Enable Local ML (scikit-learn)")
        self.use_local_ml.setChecked(self.settings.get("use_local_ml", True))
        local_layout.addRow(self.use_local_ml)
        
        local_group.setLayout(local_layout)
        layout.addWidget(local_group)
        
        # Advanced Settings
        advanced_group = QGroupBox("Advanced")
        advanced_layout = QFormLayout()
        
        self.test_timeout = QLineEdit()
        self.test_timeout.setText(str(self.settings.get("test_timeout", 300)))
        advanced_layout.addRow("Test Timeout (seconds):", self.test_timeout)
        
        self.code_language = QComboBox()
        self.code_language.addItems(["python", "bash", "go", "yaml", "json"])
        self.code_language.setCurrentText(
            self.settings.get("code_generation_language", "python")
        )
        advanced_layout.addRow("Default Code Language:", self.code_language)
        
        advanced_group.setLayout(advanced_layout)
        layout.addWidget(advanced_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_and_close)
        button_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def update_cloud_models(self):
        """Update cloud model dropdown - show all available models"""
        try:
            base_url = self.openai_base_url.text().strip().lower()
            current_text = self.cloud_model.currentText()
            self.cloud_model.clear()
            
            # Add all available models (both Groq and OpenAI)
            # Groq models
            groq_models = [
                "llama-3.1-8b-instant",  # Fast, good for agent
                "llama-3.3-70b-versatile"  # Better quality
            ]
            
            # OpenAI models
            openai_models = [
                "gpt-4",
                "gpt-4-turbo",
                "gpt-4o",
                "gpt-3.5-turbo"
            ]
            
            # Add Groq models with prefix
            self.cloud_model.addItem("--- Groq Models ---")
            for model in groq_models:
                self.cloud_model.addItem(model)
            
            # Add OpenAI models with prefix
            self.cloud_model.addItem("--- OpenAI Models ---")
            for model in openai_models:
                self.cloud_model.addItem(model)
            
            # Set default based on API base URL or saved model
            all_models = groq_models + openai_models
            saved_model = self.settings.get("cloud_model", "")
            if saved_model and saved_model in all_models:
                self.cloud_model.setCurrentText(saved_model)
            elif current_text and current_text in all_models:
                self.cloud_model.setCurrentText(current_text)
            else:
                # Default based on API base URL
                if "groq" in base_url:
                    self.cloud_model.setCurrentText("llama-3.1-8b-instant")
                else:
                    self.cloud_model.setCurrentText("gpt-4")
        except Exception as e:
            logger.error(f"Error updating cloud models: {e}")
    
    def refresh_models(self):
        """Refresh list of available Ollama models"""
        try:
            ollama_url = self.ollama_url.text().strip() or "http://localhost:11434"
            url = f"{ollama_url}/api/tags"
            
            # Clear existing items
            current_text = self.ollama_model.currentText()
            self.ollama_model.clear()
            
            try:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    models_data = response.json()
                    available_models = [m.get("name", "") for m in models_data.get("models", [])]
                    
                    if available_models:
                        # Add models to dropdown
                        for model in sorted(available_models):
                            self.ollama_model.addItem(model)
                        
                        # Restore previous selection if it exists
                        if current_text and current_text in available_models:
                            self.ollama_model.setCurrentText(current_text)
                        elif self.settings.get("ollama_model"):
                            # Try to set from saved settings
                            saved_model = self.settings.get("ollama_model", "")
                            if saved_model in available_models:
                                self.ollama_model.setCurrentText(saved_model)
                            else:
                                # Use first available or saved value
                                self.ollama_model.setCurrentText(available_models[0] if available_models else saved_model)
                        else:
                            # Default to first available
                            if available_models:
                                self.ollama_model.setCurrentText(available_models[0])
                        
                        logger.info(f"Loaded {len(available_models)} Ollama models")
                    else:
                        # No models available, add default
                        self.ollama_model.addItem("llama3.2:latest")
                        self.ollama_model.setCurrentText("llama3.2:latest")
                        logger.warning("No Ollama models found, using default")
                else:
                    logger.warning(f"Failed to fetch Ollama models: HTTP {response.status_code}")
                    # Add default models
                    default_models = ["llama3.2:latest", "llama3.3:latest", "llama2"]
                    for model in default_models:
                        self.ollama_model.addItem(model)
                    if current_text:
                        self.ollama_model.setCurrentText(current_text)
            except requests.exceptions.RequestException as e:
                logger.warning(f"Could not connect to Ollama at {ollama_url}: {e}")
                # Add default models as fallback
                default_models = ["llama3.2:latest", "llama3.3:latest", "llama2"]
                for model in default_models:
                    self.ollama_model.addItem(model)
                if current_text:
                    self.ollama_model.setCurrentText(current_text)
        except Exception as e:
            logger.error(f"Error refreshing models: {e}")
            # Add default as fallback
            if self.ollama_model.count() == 0:
                self.ollama_model.addItem("llama3.2:latest")
    
    def save_and_close(self):
        """Save settings and close"""
        # Update settings
        self.settings["use_ai_api"] = self.use_cloud_ai.isChecked()
        self.settings["openai_api_key"] = self.openai_key.text().strip()
        self.settings["openai_api_base"] = self.openai_base_url.text().strip()
        self.settings["cloud_model"] = self.cloud_model.currentText().strip()
        self.settings["use_local_llm"] = self.use_local_llm.isChecked()
        self.settings["ollama_url"] = self.ollama_url.text().strip()
        self.settings["ollama_model"] = self.ollama_model.currentText().strip()
        self.settings["use_local_ml"] = self.use_local_ml.isChecked()
        
        mode_map = {0: "local", 1: "cloud", 2: "hybrid"}
        selected_index = self.ai_mode.currentIndex()
        selected_mode = mode_map.get(selected_index, "local")
        self.settings["preferred_ai_mode"] = selected_mode
        logger.info(f"Saving preferred_ai_mode: {selected_mode} (index: {selected_index})")
        
        try:
            self.settings["test_timeout"] = int(self.test_timeout.text())
        except ValueError:
            self.settings["test_timeout"] = 300
        
        self.settings["code_generation_language"] = self.code_language.currentText()
        
        # Save to local file
        if not self.save_settings():
            QMessageBox.warning(self, "Save Failed", "Failed to save settings. Please check permissions.")
            return
        
        # Send settings to all available servers if cloud AI is enabled
        if self.use_cloud_ai.isChecked() and self.openai_key.text().strip():
            try:
                import requests
                
                # Get all available servers
                server_list = []
                
                # Try to get from parent window's server_interfaces
                if hasattr(self.parent(), 'server_interfaces'):
                    server_list = self.parent().server_interfaces
                elif hasattr(self.parent(), 'main_window') and hasattr(self.parent().main_window, 'server_interfaces'):
                    server_list = self.parent().main_window.server_interfaces
                elif hasattr(self.parent(), 'server_manager'):
                    # Use ServerManager if available
                    from utils.server_manager import ServerManager
                    if isinstance(self.parent().server_manager, ServerManager):
                        all_servers = self.parent().server_manager.get_all_servers()
                        server_list = [{"address": s.address, "tg_id": s.tg_id} for s in all_servers]
                elif hasattr(self.parent(), 'main_window') and hasattr(self.parent().main_window, 'server_manager'):
                    from utils.server_manager import ServerManager
                    if isinstance(self.parent().main_window.server_manager, ServerManager):
                        all_servers = self.parent().main_window.server_manager.get_all_servers()
                        server_list = [{"address": s.address, "tg_id": s.tg_id} for s in all_servers]
                
                # If no servers found, try single server URL as fallback
                if not server_list:
                    server_url = None
                    if hasattr(self.parent(), 'server_url'):
                        server_url = self.parent().server_url
                    elif hasattr(self.parent(), 'main_window') and hasattr(self.parent().main_window, 'server_url'):
                        server_url = self.parent().main_window.server_url
                    else:
                        import os
                        server_url = os.environ.get("OSTG_SERVER_URL", "http://localhost:5051")
                    
                    if server_url:
                        server_list = [{"address": server_url, "tg_id": 0}]
                
                # Prepare payload
                payload = {
                    "openai_api_key": self.openai_key.text().strip(),
                    "openai_api_base": self.openai_base_url.text().strip() if self.openai_base_url.text().strip() else None
                }
                
                # Send to all servers
                success_count = 0
                failed_servers = []
                
                for server in server_list:
                    server_url = server.get("address", "")
                    tg_id = server.get("tg_id", "?")
                    
                    if not server_url:
                        continue
                    
                    try:
                        api_url = f"{server_url}/api/ai/settings"
                        logger.info(f"Attempting to send AI settings to TG {tg_id}: {api_url}")
                        
                        response = requests.post(
                            api_url,
                            json=payload,
                            timeout=5
                        )
                        
                        if response.status_code == 200:
                            logger.info(f"AI settings sent to TG {tg_id} ({server_url}) successfully")
                            success_count += 1
                        elif response.status_code == 404:
                            logger.warning(f"AI settings endpoint not found (404) for TG {tg_id}. Server may need to be restarted.")
                            failed_servers.append(f"TG {tg_id} ({server_url}): Endpoint not found (404)")
                        else:
                            logger.warning(f"Failed to send AI settings to TG {tg_id}: {response.status_code}")
                            try:
                                error_detail = response.json().get("error", response.text[:100])
                            except Exception:
                                error_detail = response.text[:100] if hasattr(response, 'text') else "Unknown error"
                            failed_servers.append(f"TG {tg_id} ({server_url}): {response.status_code} - {error_detail}")
                    except requests.exceptions.ConnectionError as e:
                        logger.warning(f"Could not connect to TG {tg_id} ({server_url}): {e}")
                        failed_servers.append(f"TG {tg_id} ({server_url}): Connection failed")
                    except Exception as e:
                        logger.warning(f"Error sending to TG {tg_id} ({server_url}): {e}")
                        failed_servers.append(f"TG {tg_id} ({server_url}): {str(e)}")
                
                # Show summary message
                if success_count > 0 and not failed_servers:
                    QMessageBox.information(
                        self,
                        "Settings Saved",
                        f"AI settings have been saved successfully to all {success_count} server(s)."
                    )
                elif success_count > 0:
                    message = f"AI settings saved to {success_count} server(s).\n\n"
                    message += f"Failed to update {len(failed_servers)} server(s):\n"
                    message += "\n".join(failed_servers[:5])  # Show first 5 failures
                    if len(failed_servers) > 5:
                        message += f"\n... and {len(failed_servers) - 5} more"
                    QMessageBox.warning(self, "Partial Success", message)
                elif failed_servers:
                    message = f"Settings saved locally but failed to update all {len(failed_servers)} server(s):\n\n"
                    message += "\n".join(failed_servers[:5])  # Show first 5 failures
                    if len(failed_servers) > 5:
                        message += f"\n... and {len(failed_servers) - 5} more"
                    message += "\n\nYou may need to set environment variables on servers manually."
                    QMessageBox.warning(self, "Server Update Failed", message)
                else:
                    QMessageBox.warning(
                        self,
                        "No Servers Found",
                        "Settings saved locally but no servers found to update.\n\n"
                        "Please ensure servers are configured in the server tree."
                    )
            except Exception as e:
                logger.warning(f"Could not send AI settings to servers: {e}")
                QMessageBox.warning(
                    self,
                    "Server Update Warning",
                    f"Settings saved locally but could not update servers.\n\n"
                    f"Error: {str(e)}\n\n"
                    f"You may need to set environment variables on servers manually:\n"
                    f"OPENAI_API_KEY and OPENAI_API_BASE"
                )
        
        # Show summary message (only if we didn't already show one for server updates)
        if not (self.use_cloud_ai.isChecked() and self.openai_key.text().strip()):
            QMessageBox.information(self, "Settings Saved", "AI settings have been saved successfully.")
        self.accept()




