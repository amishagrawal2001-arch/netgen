"""
AI menu actions for the Netgen client.
Adds the AI Assistant submenu to the menu bar.
"""

from PyQt5.QtWidgets import QMenu, QAction, QMessageBox
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt
import logging

logger = logging.getLogger(__name__)


class TrafficGenClientAIMenuActions:
    """AI menu actions for Traffic Generator Client"""
    
    @staticmethod
    def setup_ai_menu(client_window):
        """Setup AI menu in menu bar"""
        # Create AI menu
        client_window.ai_menu = QMenu("&AI Assistant", client_window.menuBar())
        client_window.menuBar().addMenu(client_window.ai_menu)
        
        # Troubleshooting
        troubleshoot_action = QAction("&Troubleshooting Assistant", client_window)
        troubleshoot_action.setShortcut("Ctrl+T")
        troubleshoot_action.setStatusTip("AI-powered network troubleshooting")
        troubleshoot_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_ai_troubleshooting(client_window))
        client_window.ai_menu.addAction(troubleshoot_action)
        
        # Test Framework
        test_action = QAction("&Test Framework", client_window)
        test_action.setShortcut("Ctrl+Shift+T")
        test_action.setStatusTip("Run automated tests on devices")
        test_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_ai_test_framework(client_window))
        client_window.ai_menu.addAction(test_action)
        
        client_window.ai_menu.addSeparator()
        
        # Code Generator
        code_action = QAction("&Code Generator", client_window)
        code_action.setShortcut("Ctrl+Shift+C")
        code_action.setStatusTip("Generate code using AI")
        code_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_ai_code_generator(client_window))
        client_window.ai_menu.addAction(code_action)
        
        # Pytest Generator
        pytest_action = QAction("&Pytest Generator", client_window)
        pytest_action.setStatusTip("Generate pytest scripts")
        pytest_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_ai_pytest_generator(client_window))
        client_window.ai_menu.addAction(pytest_action)
        
        client_window.ai_menu.addSeparator()
        
        # Model Training
        train_action = QAction("&Train Models", client_window)
        train_action.setStatusTip("Train AI models from knowledge base")
        train_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_ai_training(client_window))
        client_window.ai_menu.addAction(train_action)
        
        client_window.ai_menu.addSeparator()
        
        # Unified AI Assistant
        unified_action = QAction("&Unified AI Assistant", client_window)
        unified_action.setShortcut("Ctrl+Shift+A")
        unified_action.setStatusTip("Open unified AI assistant with all features")
        unified_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_unified_ai(client_window))
        client_window.ai_menu.addAction(unified_action)
        
        client_window.ai_menu.addSeparator()
        
        # Test Plan Generator
        test_plan_action = QAction("&Test Plan Generator", client_window)
        test_plan_action.setStatusTip("Generate test plans from functional specifications")
        test_plan_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_test_plan_generator(client_window))
        client_window.ai_menu.addAction(test_plan_action)
        
        # Device Testing
        device_test_action = QAction("&Device Testing", client_window)
        device_test_action.setStatusTip("Execute pytest on external devices")
        device_test_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_device_testing(client_window))
        client_window.ai_menu.addAction(device_test_action)
        
        client_window.ai_menu.addSeparator()
        
        # Model Management
        model_mgmt_action = QAction("Model &Management", client_window)
        model_mgmt_action.setStatusTip("Manage AI model versions and updates")
        model_mgmt_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_model_management(client_window))
        client_window.ai_menu.addAction(model_mgmt_action)
        
        client_window.ai_menu.addSeparator()
        
        # AI Settings
        settings_action = QAction("AI &Settings", client_window)
        settings_action.setStatusTip("Configure AI settings")
        settings_action.triggered.connect(lambda: TrafficGenClientAIMenuActions.open_ai_settings(client_window))
        client_window.ai_menu.addAction(settings_action)
    
    @staticmethod
    def open_ai_troubleshooting(client_window):
        """Open AI troubleshooting dialog"""
        try:
            from widgets.ai_troubleshooting_dialog import AITroubleshootingDialog
            
            dialog = AITroubleshootingDialog(client_window)
            dialog.exec_()
        except ImportError:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                "AI troubleshooting dialog not available.\n"
                "Install dependencies: pip install scikit-learn"
            )
        except Exception as e:
            logger.error(f"Failed to open AI troubleshooting: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open AI troubleshooting:\n{str(e)}"
            )
    
    @staticmethod
    def open_ai_test_framework(client_window):
        """Open AI test framework dialog"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            
            dialog = AIUnifiedDialog(client_window)
            # Switch to Test Framework tab (index 2)
            dialog.tabs.setCurrentIndex(2)
            dialog.exec_()
        except ImportError as e:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                f"AI test framework not available.\n{str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to open AI test framework: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open AI test framework:\n{str(e)}"
            )
    
    @staticmethod
    def open_ai_code_generator(client_window):
        """Open AI code generator dialog"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            
            dialog = AIUnifiedDialog(client_window)
            # Switch to Code Generator tab (index 4)
            dialog.tabs.setCurrentIndex(4)
            dialog.exec_()
        except ImportError as e:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                f"AI code generator not available.\n{str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to open AI code generator: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open AI code generator:\n{str(e)}"
            )
    
    @staticmethod
    def open_ai_pytest_generator(client_window):
        """Open AI pytest generator dialog"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            
            dialog = AIUnifiedDialog(client_window)
            # Switch to Test Plan tab (index 3) which has pytest generation
            dialog.tabs.setCurrentIndex(3)
            dialog.exec_()
        except ImportError as e:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                f"AI pytest generator not available.\n{str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to open AI pytest generator: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open AI pytest generator:\n{str(e)}"
            )
    
    @staticmethod
    def open_ai_training(client_window):
        """Open AI model training dialog"""
        try:
            from widgets.ai_model_management_dialog import AIModelManagementDialog
            
            dialog = AIModelManagementDialog(client_window)
            dialog.exec_()
        except ImportError as e:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                f"AI model training not available.\n{str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to open AI training: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open AI training:\n{str(e)}"
            )
    
    @staticmethod
    def open_unified_ai(client_window):
        """Open unified AI assistant dialog"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            
            dialog = AIUnifiedDialog(client_window)
            dialog.exec_()
        except ImportError as e:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                f"Unified AI assistant not available.\n{str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to open unified AI: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open unified AI:\n{str(e)}"
            )
    
    @staticmethod
    def open_test_plan_generator(client_window):
        """Open test plan generator dialog"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            
            dialog = AIUnifiedDialog(client_window)
            # Switch to test plan tab
            dialog.tabs.setCurrentIndex(3)  # Test Plan tab
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open test plan generator: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open test plan generator:\n{str(e)}"
            )
    
    @staticmethod
    def open_device_testing(client_window):
        """Open device testing dialog"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            
            dialog = AIUnifiedDialog(client_window)
            # Switch to device testing tab
            dialog.tabs.setCurrentIndex(5)  # Device Testing tab
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open device testing: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open device testing:\n{str(e)}"
            )
    
    @staticmethod
    def open_model_management(client_window):
        """Open AI model management dialog"""
        try:
            from widgets.ai_model_management_dialog import AIModelManagementDialog
            
            dialog = AIModelManagementDialog(client_window)
            dialog.exec_()
        except ImportError as e:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                f"AI model management not available.\n{str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to open model management: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open model management:\n{str(e)}"
            )
    
    @staticmethod
    def open_ai_settings(client_window):
        """Open AI settings dialog"""
        try:
            from widgets.ai_settings_dialog import AISettingsDialog
            
            dialog = AISettingsDialog(client_window)
            dialog.exec_()
        except ImportError:
            QMessageBox.warning(
                client_window,
                "AI Not Available",
                "AI settings not available."
            )
        except Exception as e:
            logger.error(f"Failed to open AI settings: {e}")
            QMessageBox.critical(
                client_window,
                "Error",
                f"Failed to open AI settings:\n{str(e)}"
            )

