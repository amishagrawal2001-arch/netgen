"""
AI Chat Dialog - Conversational interface for AI
Allows natural language communication with AI
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QScrollArea, QWidget, QMessageBox, QApplication
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QMetaObject, pyqtSlot
from PyQt5.QtGui import QFont, QTextCharFormat, QColor
import requests
import json
import logging
import threading

logger = logging.getLogger(__name__)


class AIChatWorker(QThread):
    """Worker thread for AI chat"""
    response_received = pyqtSignal(dict)  # Emit dict with response, model, source
    error = pyqtSignal(str)
    
    def __init__(self, server_url, message, context=None):
        super().__init__()
        self.server_url = server_url
        self.message = message
        self.context = context or {}
    
    def run(self):
        try:
            # Load AI mode preference to send to server
            ai_mode_preference = "hybrid"  # default
            try:
                import os
                import json
                settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                if os.path.exists(settings_file):
                    with open(settings_file, 'r') as f:
                        client_settings = json.load(f)
                        ai_mode_preference = client_settings.get("preferred_ai_mode", "hybrid")
            except Exception:
                pass
            
            # Use LLM-based chat endpoint for all messages
            response = requests.post(
                f"{self.server_url}/api/ai/chat",
                json={
                    "message": self.message,
                    "context": self.context,
                    "ai_mode_preference": ai_mode_preference  # Send preference to server
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_response = result.get("response", "")
                
                # Handle empty response
                if not ai_response or not ai_response.strip():
                    ai_response = "I'm sorry, I couldn't generate a response. The server returned an empty response."
                
                model = result.get("model", "unknown")
                source = result.get("source", "unknown")
                
                # Debug logging
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"[AI CHAT CLIENT] Received response - length: {len(ai_response)}, model: {model}, source: {source}")
                
                # Emit response with all info as dict
                logger.info(f"[AI CHAT CLIENT] Emitting response signal - length: {len(ai_response)}")
                self.response_received.emit({
                    "response": ai_response,
                    "model": model,
                    "source": source
                })
            else:
                error_msg = f"Server error: {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", error_msg)
                except Exception:
                    pass
                self.error.emit(error_msg)
        except requests.exceptions.RequestException as e:
            self.error.emit(f"Network error: {str(e)}")
        except Exception as e:
            self.error.emit(str(e))
    
    def _parse_intent(self, message):
        """Parse user intent from message"""
        message_lower = message.lower()
        
        if any(word in message_lower for word in ["troubleshoot", "diagnose", "fix", "issue", "problem"]):
            return "troubleshoot"
        elif any(word in message_lower for word in ["generate", "create", "code", "function", "script"]):
            return "generate_code"
        elif any(word in message_lower for word in ["test", "run test", "execute test"]):
            return "run_tests"
        elif any(word in message_lower for word in ["explain", "what does", "how does"]):
            return "explain"
        else:
            return "general"
    
    def _handle_troubleshoot(self):
        """Handle troubleshooting request"""
        # Extract device and symptoms from message
        device_id = self.context.get("device_id", "unknown")
        
        # Call troubleshooting API
        try:
            response = requests.post(
                f"{self.server_url}/api/ai/troubleshoot",
                json={
                    "device_id": device_id,
                    "symptoms": self._extract_symptoms(self.message)
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return self._format_troubleshooting_response(result)
            else:
                return f"Error: {response.status_code}"
        except Exception as e:
            return f"Failed to troubleshoot: {str(e)}"
    
    def _handle_code_generation(self):
        """Handle code generation request"""
        try:
            response = requests.post(
                f"{self.server_url}/api/ai/code/generate",
                json={
                    "prompt": self.message,
                    "code_type": "python"
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                code = result.get("code", "")
                return f"Here's the generated code:\n\n```python\n{code}\n```"
            else:
                return f"Error: {response.status_code}"
        except Exception as e:
            return f"Failed to generate code: {str(e)}"
    
    def _handle_test_execution(self):
        """Handle test execution request"""
        device_id = self.context.get("device_id", "unknown")
        
        try:
            # First, get test suggestions
            suggest_response = requests.post(
                f"{self.server_url}/api/ai/test/suggest",
                json={"device_id": device_id},
                timeout=30
            )
            
            if suggest_response.status_code == 200:
                suggestions = suggest_response.json().get("suggestions", [])
                test_ids = [s["test_id"] for s in suggestions[:5]]  # Top 5
                
                # Run tests
                run_response = requests.post(
                    f"{self.server_url}/api/ai/test/run",
                    json={
                        "device_id": device_id,
                        "test_ids": test_ids
                    },
                    timeout=120
                )
                
                if run_response.status_code == 200:
                    result = run_response.json()
                    return self._format_test_results(result)
                else:
                    return f"Error running tests: {run_response.status_code}"
            else:
                return f"Error getting suggestions: {suggest_response.status_code}"
        except Exception as e:
            return f"Failed to run tests: {str(e)}"
    
    def _handle_explanation(self):
        """Handle explanation request"""
        # This would extract code/config from message and explain it
        return "Explanation feature - to be implemented"
    
    def _handle_general_query(self):
        """Handle general queries"""
        return f"I understand you're asking: {self.message}\n\nHow can I help?\n- Troubleshoot a device\n- Generate code\n- Run tests\n- Explain something"
    
    def _extract_symptoms(self, message):
        """Extract symptoms from natural language"""
        symptoms = {}
        message_lower = message.lower()
        
        if "interface down" in message_lower or "interface is down" in message_lower:
            symptoms["interface_down"] = True
        if "link down" in message_lower:
            symptoms["link_down"] = True
        if "packet loss" in message_lower:
            # Try to extract percentage
            import re
            loss_match = re.search(r'(\d+(?:\.\d+)?)%', message)
            if loss_match:
                symptoms["packet_loss"] = float(loss_match.group(1)) / 100
        if "bgp" in message_lower and ("not" in message_lower or "down" in message_lower):
            symptoms["bgp_not_established"] = True
        if "ospf" in message_lower and ("not" in message_lower or "down" in message_lower):
            symptoms["ospf_not_established"] = True
        
        return symptoms
    
    def _format_troubleshooting_response(self, result):
        """Format troubleshooting response for chat"""
        lines = []
        lines.append(f"🔍 **Diagnosis**")
        lines.append(f"Root Cause: {result.get('root_cause', 'Unknown')}")
        lines.append(f"Confidence: {result.get('confidence', 0) * 100:.1f}%")
        lines.append("")
        lines.append("💡 **Solutions:**")
        for i, solution in enumerate(result.get('solutions', []), 1):
            lines.append(f"{i}. {solution}")
        
        if result.get('commands'):
            lines.append("")
            lines.append("⚙️ **Commands:**")
            for vendor, commands in result['commands'].items():
                lines.append(f"{vendor.upper()}:")
                for cmd in commands:
                    lines.append(f"  {cmd}")
        
        return "\n".join(lines)
    
    def _format_test_results(self, result):
        """Format test results for chat"""
        lines = []
        lines.append("🧪 **Test Results**")
        lines.append(f"Total: {result.get('total_tests', 0)}")
        lines.append(f"✅ Passed: {result.get('passed_tests', 0)}")
        lines.append(f"❌ Failed: {result.get('failed_tests', 0)}")
        lines.append("")
        
        if result.get('test_results'):
            lines.append("**Details:**")
            for test_result in result['test_results']:
                status = "✅" if test_result.get('passed') else "❌"
                lines.append(f"{status} {test_result.get('test_name', 'Unknown')}")
        
        if result.get('recommendations'):
            lines.append("")
            lines.append("💡 **Recommendations:**")
            for rec in result['recommendations']:
                lines.append(f"- {rec}")
        
        return "\n".join(lines)


class AIChatDialog(QDialog):
    """AI Chat Dialog - Conversational interface"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NetGenAI Chat")
        # Make the chat window more compact by default
        self.setMinimumSize(520, 450)
        self.resize(600, 520)
        
        self.server_url = getattr(parent, 'server_url', 'http://localhost:5051')
        self.context = self._get_context(parent)
        self.worker = None
        self.conversation_history = []
        
        self.setup_ui()
        
        # Set focus to input field so Enter key works immediately
        self.message_input.setFocus()
    
    def _get_context(self, parent):
        """Get context from parent window"""
        context = {}
        
        # Get selected device
        if hasattr(parent, 'devices_tab'):
            try:
                selected_device = parent.devices_tab.get_selected_device()
                if selected_device:
                    context['device_id'] = selected_device.get('device_id')
                    context['device_name'] = selected_device.get('Device Name')
            except Exception:
                pass
        
        return context
    
    def setup_ui(self):
        """Setup UI components"""
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("🤖 NetGenAI")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Context info
        if self.context.get('device_id'):
            context_label = QLabel(
                f"Context: Device {self.context.get('device_name', self.context.get('device_id'))}"
            )
            context_label.setStyleSheet("color: #666; font-style: italic;")
            layout.addWidget(context_label)
        
        # Chat display area
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("AI: How can I help you today?")
        
        # Add welcome message
        self.add_message("AI", "Hello! I'm your AI assistant. How can I help you?\n\nYou can:\n- Troubleshoot devices\n- Generate code\n- Run tests\n- Ask questions")
        
        layout.addWidget(self.chat_display)
        
        # Input area
        input_layout = QHBoxLayout()
        
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type your message here... (e.g., 'Troubleshoot device-123', 'Generate pytest script')")
        self.message_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.message_input)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.setDefault(True)  # Make Send button the default (responds to Enter)
        self.send_btn.setAutoDefault(True)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 8px 20px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_btn)
        
        layout.addLayout(input_layout)
        
        # Button row with Examples and Close
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # Examples button - less prominent, smaller
        examples_btn = QPushButton("💡 Examples")
        examples_btn.setDefault(False)  # Explicitly NOT the default button
        examples_btn.setAutoDefault(False)  # Don't auto-select as default
        examples_btn.setFocusPolicy(Qt.NoFocus)  # Prevent focus via Tab key
        examples_btn.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                padding: 6px 12px;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
                border: 1px solid #a1a1a6;
            }
        """)
        examples_btn.clicked.connect(self.show_examples)
        button_layout.addWidget(examples_btn)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
    
    def add_message(self, sender, message):
        """Add message to chat display"""
        if sender == "User":
            format_str = f"<b>You:</b> {message}<br><br>"
            self.chat_display.append(format_str)
        else:
            # Check if we have a source badge to display
            source_badge = getattr(self, '_current_source_badge', '')
            if source_badge:
                # Add badge with proper spacing - QTextEdit compatible format
                format_str = f"<b>AI:</b> {source_badge} {message}<br><br>"
                logger.debug(f"Adding message with badge: {source_badge[:50]}...")
                # Clear the badge after using it
                if hasattr(self, '_current_source_badge'):
                    delattr(self, '_current_source_badge')
            else:
                format_str = f"<b>AI:</b> {message}<br><br>"
            self.chat_display.append(format_str)
        
        # Scroll to bottom
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
    
    def send_message(self):
        """Send message to AI"""
        message = self.message_input.text().strip()
        if not message:
            return
        
        # Add user message to display
        self.add_message("User", message)
        self.conversation_history.append({"role": "user", "content": message})
        
        # Clear input
        self.message_input.clear()
        
        # Disable input
        self.send_btn.setEnabled(False)
        self.message_input.setEnabled(False)
        
        # Show "AI is thinking..."
        thinking_html = "<b>AI:</b> Thinking...<br><br>"
        self.chat_display.append(thinking_html)
        
        # Scroll to bottom
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
        
        # Use background thread to avoid UI freezing
        # Stop any existing worker
        if hasattr(self, '_worker') and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        
        # Create and start worker thread
        self._worker = AIChatWorker(self.server_url, message, self.context)
        self._worker.response_received.connect(self._handle_worker_response)
        self._worker.error.connect(self.on_ai_error)
        self._worker.start()
    
    def _handle_worker_response(self, result):
        """Handle response from worker thread"""
        try:
            ai_response = result.get("response", "")
            model = result.get("model", "unknown")
            source = result.get("source", "unknown")
            
            logger.debug(f"Received response from worker - length: {len(ai_response)}, model: {model}, source: {source}")
            
            # Remove "Thinking..." message
            current_html = self.chat_display.toHtml()
            if "Thinking..." in current_html:
                thinking_html = "<b>AI:</b> Thinking...<br><br>"
                current_html = current_html.replace(thinking_html, "")
                self.chat_display.setHtml(current_html)
                logger.debug("Removed 'Thinking...' message")
            
            # Create source badge - simplified CSS for QTextEdit compatibility
            if source == "cloud_api":
                source_badge = f"<span style='background-color: #28a745; color: white; padding: 2px 8px; font-size: 10px; font-weight: bold;'>☁️ Cloud ({model})</span>"
            elif source == "local_llm":
                source_badge = f"<span style='background-color: #0056b3; color: white; padding: 2px 8px; font-size: 10px; font-weight: bold;'>🖥️ Local ({model})</span>"
            elif source and source != "unknown":
                source_badge = f"<span style='background-color: #6c757d; color: white; padding: 2px 8px; font-size: 10px; font-weight: bold;'>⚙️ {source} ({model})</span>"
            else:
                if model and model != "unknown":
                    source_badge = f"<span style='background-color: #6c757d; color: white; padding: 2px 8px; font-size: 10px; font-weight: bold;'>⚙️ {model}</span>"
                else:
                    source_badge = ""
            
            # Store source badge for use in add_message
            self._current_source_badge = source_badge
            logger.debug(f"Created badge: {source_badge[:80]}...")
            
            # Process and display response progressively
            self._display_response_progressively(ai_response)
        except Exception as e:
            import logging
            import traceback
            logging.error(f"Error handling worker response: {e}", exc_info=True)
            self.on_ai_error(f"Error processing response: {str(e)}")
    
    def _handle_ai_response(self):
        """Handle AI response from background thread (called via QTimer)"""
        if hasattr(self, '_pending_response'):
            response = self._pending_response
            delattr(self, '_pending_response')
            self.on_ai_response(response)
    
    def _handle_ai_error(self):
        """Handle AI error from background thread (called via QTimer)"""
        if hasattr(self, '_pending_error'):
            error = self._pending_error
            delattr(self, '_pending_error')
            self.on_ai_error(error)
    
    def _display_response_progressively(self, response):
        """Display response progressively by chunking and appending"""
        try:
            # Remove "Thinking..." message
            current_html = self.chat_display.toHtml()
            if "Thinking..." in current_html:
                thinking_html = "<b>AI:</b> Thinking...<br><br>"
                current_html = current_html.replace(thinking_html, "")
                self.chat_display.setHtml(current_html)
            
            # Ensure response is not empty
            if not response or not response.strip():
                response = "I'm sorry, I received an empty response from the server."
            
            # First, unescape any HTML entities that might be in the response
            # This fixes issues where responses contain &quot; &amp; etc. that should be displayed as text
            import html
            import re
            try:
                # Unescape HTML entities (like &quot; -> ", &amp; -> &, etc.)
                response = html.unescape(response)
            except Exception:
                pass  # If unescaping fails, continue with original
            
            # Format response first (to handle code blocks properly)
            # Step 1: Process code blocks first
            code_block_pattern = r'```(\w+)?\n(.*?)```'
            
            def format_code_block(match):
                lang = match.group(1) or ""
                code_content = match.group(2)
                # Escape HTML entities in code content only (for safety)
                code_content = html.escape(code_content)
                lang_label = f"<span style='color: #666; font-size: 11px; font-weight: 600; text-transform: uppercase;'>{lang}</span><br>" if lang else ""
                return f"<pre style='background-color: #f5f5f7; padding: 12px; border-radius: 6px; overflow-x: auto; margin: 8px 0; border-left: 3px solid #007aff;'><code style='font-family: monospace; font-size: 12px;'>{lang_label}{code_content}</code></pre>"
            
            formatted_response = re.sub(code_block_pattern, format_code_block, response, flags=re.DOTALL)
            
            # Step 2: Format markdown in text parts (but preserve code blocks)
            parts = re.split(r'(<pre[^>]*>.*?</pre>)', formatted_response, flags=re.DOTALL)
            result_parts = []
            for i, part in enumerate(parts):
                if i % 2 == 0:  # Outside code blocks
                    # Escape HTML entities in text parts to prevent XSS
                    part = html.escape(part)
                    part = part.replace('\n', '<br>')
                    # Apply markdown formatting (after escaping)
                    part = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', part)
                    part = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<em>\1</em>', part)
                    part = re.sub(r'<br>(- |\* )', r'<br>• ', part)
                    part = re.sub(r'<br>(\d+\. )', r'<br><span style="margin-left: 4px;">\1</span>', part)
                result_parts.append(part)
            formatted_response = ''.join(result_parts)
            
            # Start progressive display with formatted HTML
            source_badge = getattr(self, '_current_source_badge', '')
            if source_badge:
                initial_html = f"<b>AI:</b> {source_badge} "
            else:
                initial_html = "<b>AI:</b> "
            
            self.chat_display.append(initial_html)
            
            # Store formatted response and original for progressive display
            self._progressive_response = formatted_response
            self._progressive_original_response = response  # Store original for history
            self._progressive_index = 0
            self._progressive_chunk_size = 15  # Characters per chunk (smaller for smoother display)
            
            # Start timer for progressive display
            if not hasattr(self, '_progressive_timer'):
                self._progressive_timer = QTimer()
                self._progressive_timer.timeout.connect(self._append_progressive_chunk)
            
            self._progressive_timer.start(15)  # Update every 15ms for smooth display
            
        except Exception as e:
            import logging
            import traceback
            logger = logging.getLogger(__name__)
            logger.error(f"Error starting progressive display: {e}", exc_info=True)
            # Fallback to regular display
            self.on_ai_response(response)
    
    def _append_progressive_chunk(self):
        """Append next chunk of formatted response progressively"""
        try:
            if not hasattr(self, '_progressive_response') or not hasattr(self, '_progressive_index'):
                self._progressive_timer.stop()
                self._finalize_progressive_response()
                return
            
            formatted_response = self._progressive_response
            index = self._progressive_index
            
            if index >= len(formatted_response):
                # Done
                self._progressive_timer.stop()
                self._finalize_progressive_response()
                return
            
            # Get next chunk - try to break at word boundaries for better display
            chunk_size = self._progressive_chunk_size
            chunk = formatted_response[index:index + chunk_size]
            
            # If we're in the middle of an HTML tag, extend chunk to end of tag
            if '<' in chunk and '>' not in chunk[index:index+chunk_size]:
                # Find next '>'
                next_gt = formatted_response.find('>', index)
                if next_gt > index:
                    chunk = formatted_response[index:next_gt + 1]
            
            self._progressive_index += len(chunk)
            
            # Append chunk as HTML
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.End)
            cursor.insertHtml(chunk)
            self.chat_display.setTextCursor(cursor)
            
            # Scroll to bottom
            self.chat_display.verticalScrollBar().setValue(
                self.chat_display.verticalScrollBar().maximum()
            )
            
            # Process events to update UI
            QApplication.processEvents()
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in progressive chunk display: {e}", exc_info=True)
            self._progressive_timer.stop()
            self._finalize_progressive_response()
    
    def _finalize_progressive_response(self):
        """Finalize progressive response"""
        try:
            # Store in conversation history (get original response if available)
            if hasattr(self, '_progressive_original_response'):
                self.conversation_history.append({"role": "assistant", "content": self._progressive_original_response})
                delattr(self, '_progressive_original_response')
            
            # Add final line break
            self.chat_display.append("")
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error finalizing progressive response: {e}", exc_info=True)
        finally:
            # Clean up
            if hasattr(self, '_progressive_response'):
                delattr(self, '_progressive_response')
            if hasattr(self, '_progressive_index'):
                delattr(self, '_progressive_index')
            
            # Re-enable input
            self.send_btn.setEnabled(True)
            self.message_input.setEnabled(True)
            self.message_input.setFocus()
    
    def on_ai_response(self, response):
        """Handle AI response (fallback method - displays all at once)"""
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            # Remove "Thinking..." message
            current_html = self.chat_display.toHtml()
            if "Thinking..." in current_html:
                thinking_html = "<b>AI:</b> Thinking...<br><br>"
                current_html = current_html.replace(thinking_html, "")
                self.chat_display.setHtml(current_html)
            
            # Ensure response is not empty
            if not response or not response.strip():
                response = "I'm sorry, I received an empty response from the server."
            
            # First, unescape any HTML entities that might be in the response
            import html
            import re
            try:
                # Unescape HTML entities (like &quot; -> ", &amp; -> &, etc.)
                response = html.unescape(response)
            except Exception:
                pass  # If unescaping fails, continue with original
            
            # Format response (preserve code blocks and markdown)
            # Step 1: Process code blocks
            code_block_pattern = r'```(\w+)?\n(.*?)```'
            
            def format_code_block(match):
                lang = match.group(1) or ""
                code_content = match.group(2)
                # Escape HTML entities in code content only
                code_content = html.escape(code_content)
                lang_label = f"<span style='color: #666; font-size: 11px; font-weight: 600; text-transform: uppercase;'>{lang}</span><br>" if lang else ""
                return f"<pre style='background-color: #f5f5f7; padding: 12px; border-radius: 6px; overflow-x: auto; margin: 8px 0; border-left: 3px solid #007aff;'><code style='font-family: monospace; font-size: 12px;'>{lang_label}{code_content}</code></pre>"
            
            formatted_response = re.sub(code_block_pattern, format_code_block, response, flags=re.DOTALL)
            
            # Step 2: Format markdown in text parts
            parts = re.split(r'(<pre[^>]*>.*?</pre>)', formatted_response, flags=re.DOTALL)
            result_parts = []
            for i, part in enumerate(parts):
                if i % 2 == 0:  # Outside code blocks
                    # Escape HTML entities in text parts
                    part = html.escape(part)
                    part = part.replace('\n', '<br>')
                    # Apply markdown formatting
                    part = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', part)
                    part = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<em>\1</em>', part)
                    part = re.sub(r'<br>(- |\* )', r'<br>• ', part)
                    part = re.sub(r'<br>(\d+\. )', r'<br><span style="margin-left: 4px;">\1</span>', part)
                result_parts.append(part)
            formatted_response = ''.join(result_parts)
            
            # Add AI response
            self.add_message("AI", formatted_response)
            self.conversation_history.append({"role": "assistant", "content": response})
            
        except Exception as e:
            import logging
            import traceback
            logger = logging.getLogger(__name__)
            logger.error(f"Error displaying AI response: {e}", exc_info=True)
            self.add_message("AI", f"Error displaying response: {str(e)}")
        finally:
            # Re-enable input
            self.send_btn.setEnabled(True)
            self.message_input.setEnabled(True)
            self.message_input.setFocus()
    
    def on_ai_error(self, error_msg):
        """Handle AI error"""
        # Remove "Thinking..." message using HTML replacement (more reliable)
        current_html = self.chat_display.toHtml()
        if "Thinking..." in current_html:
            thinking_html = "<b>AI:</b> Thinking...<br><br>"
            current_html = current_html.replace(thinking_html, "")
            self.chat_display.setHtml(current_html)
        
        # Add error message
        self.add_message("AI", f"❌ Error: {error_msg}")
        
        # Re-enable input
        self.send_btn.setEnabled(True)
        self.message_input.setEnabled(True)
        self.message_input.setFocus()
    
    def show_examples(self):
        """Show example prompts"""
        examples = [
            "Troubleshoot device-123 - interface is down",
            "Generate pytest script for connectivity tests",
            "Create a function to parse network configs",
            "Run tests on device-123",
            "Explain what BGP does",
            "Fix this code: [paste code]",
            "Optimize stream for 1000 pps"
        ]
        
        msg = "Example prompts:\n\n" + "\n".join(f"• {ex}" for ex in examples)
        QMessageBox.information(self, "Example Prompts", msg)


