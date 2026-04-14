"""
Unified AI Assistant Dialog
Comprehensive AI interface with all capabilities
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QTextEdit, QTextBrowser, QLineEdit, QPushButton, QComboBox,
    QFormLayout, QGroupBox, QMessageBox, QProgressBar,
    QFileDialog, QCheckBox, QSpinBox, QListWidget, QListWidgetItem,
    QSplitter, QSizePolicy, QPlainTextEdit, QMenu, QApplication
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QTextDocument
import requests
import json
import logging
import os
import subprocess
import tempfile
import textwrap
import ast
import html

logger = logging.getLogger(__name__)


class CodeGenerationWorker(QThread):
    """Background worker for code generation to prevent UI freezing"""
    
    code_received = pyqtSignal(str)  # Emitted when code is received
    error_received = pyqtSignal(str)  # Emitted on error
    finished = pyqtSignal()  # Emitted when worker finishes
    
    def __init__(self, server_url, prompt, language, code_type):
        super().__init__()
        self.server_url = server_url
        self.prompt = prompt
        self.language = language
        self.code_type = code_type
    
    def run(self):
        """Generate code in background thread"""
        try:
            # Validate server URL before making request
            if not self.server_url:
                self.error_received.emit("Server URL is not configured. Please ensure a server is selected.")
                return
            
            response = requests.post(
                f"{self.server_url}/api/ai/code/generate",
                json={
                    "prompt": self.prompt,
                    "language": self.language,
                    "code_type": self.code_type
                },
                timeout=90  # Longer timeout for LLM generation
            )
            
            if response.status_code == 200:
                result = response.json()
                code = result.get("code", "")
                if code:
                    self.code_received.emit(code)
                else:
                    self.error_received.emit("No code generated")
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", f"HTTP {response.status_code}")
                except Exception:
                    error_msg = f"HTTP {response.status_code}"
                self.error_received.emit(error_msg)
        except requests.exceptions.Timeout:
            self.error_received.emit("Request timeout. The LLM may be taking longer than expected. Please try again.")
        except Exception as e:
            self.error_received.emit(f"Error: {str(e)}")
        finally:
            self.finished.emit()


class TestPlanGenerationWorker(QThread):
    """Background worker for test plan generation to prevent UI freezing"""
    
    test_plan_received = pyqtSignal(dict)  # Emitted when test plan is received
    error_received = pyqtSignal(str)  # Emitted on error
    finished = pyqtSignal()  # Emitted when worker finishes
    
    def __init__(self, server_url, functional_spec=None, user_message=None, agent_mode=False, ai_mode_preference="hybrid", agent_model=None):
        super().__init__()
        self.server_url = server_url
        self.functional_spec = functional_spec
        self.user_message = user_message
        self.agent_mode = agent_mode
        self.ai_mode_preference = ai_mode_preference
        self.agent_model = agent_model  # User-selected model for agent mode
    
    def run(self):
        """Generate test plan in background thread"""
        try:
            # Validate server URL before making request
            if not self.server_url:
                self.error_received.emit("Server URL is not configured. Please ensure a server is selected.")
                return
            
            if self.agent_mode:
                # Use agent endpoint
                if not self.user_message:
                    # If no message but have functional_spec, convert to message
                    if self.functional_spec:
                        title = self.functional_spec.get("title", "")
                        desc = self.functional_spec.get("description", "")
                        reqs = self.functional_spec.get("requirements", [])
                        self.user_message = f"Generate a test plan for: {title}. Description: {desc}. Requirements: {', '.join(reqs)}"
                    else:
                        self.error_received.emit("Agent mode requires a user message or functional specification.")
                        return
                
                # Prepare request payload
                payload = {
                    "message": self.user_message,
                    "context": {},
                    "ai_mode_preference": self.ai_mode_preference
                }
                # Add model if specified
                if self.agent_model:
                    payload["agent_model"] = self.agent_model
                
                response = requests.post(
                    f"{self.server_url}/api/ai/test/plan/agent",
                    json=payload,
                    timeout=120  # Longer timeout for agent execution
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Check for error first
                    if "error" in result and result.get("error"):
                        self.error_received.emit(result.get("error", "Unknown error"))
                    else:
                        test_plan = result.get("test_plan")
                        if test_plan and isinstance(test_plan, dict) and len(test_plan) > 0:
                            self.test_plan_received.emit(test_plan)
                        else:
                            # Agent mode may not always return test_plan in same format
                            # Check if response contains test plan info
                            if result.get("response"):
                                self.error_received.emit(f"Agent completed but no test plan in response. Response: {result.get('response')[:200]}")
                            else:
                                self.error_received.emit("No test plan generated. The agent response was empty or invalid.")
                else:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", f"HTTP {response.status_code}")
                        hint = error_data.get("hint", "")
                        # Enhance error message with hint if available
                        if hint:
                            error_msg = f"{error_msg}\n\n💡 {hint}"
                        # Special handling for LLM client unavailable error
                        if "No LLM client available" in error_msg:
                            error_msg = (
                                f"{error_msg}\n\n"
                                f"To fix this:\n"
                                f"1. For Agent Mode: Configure OpenAI API key in settings, OR\n"
                                f"2. Disable Agent Mode and use Standard Mode (works without LLM)\n"
                                f"3. For Local LLM: Start Ollama service (https://ollama.ai)"
                            )
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"
                    self.error_received.emit(error_msg)
            else:
                # Use regular endpoint
                if not self.functional_spec:
                    self.error_received.emit("Functional specification is required in standard mode.")
                    return
                
                # Get AI mode preference for standard mode too
                import os
                ai_mode_preference = self.ai_mode_preference if hasattr(self, 'ai_mode_preference') else "hybrid"
                try:
                    settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                    if os.path.exists(settings_file):
                        with open(settings_file, 'r') as f:
                            client_settings = json.load(f)
                            ai_mode_preference = client_settings.get("preferred_ai_mode", ai_mode_preference)
                            logger.info(f"[TEST PLAN WORKER] Read AI mode preference from settings: {ai_mode_preference}")
                    else:
                        logger.warning(f"[TEST PLAN WORKER] Settings file not found: {settings_file}, using default: {ai_mode_preference}")
                except Exception as e:
                    logger.warning(f"[TEST PLAN WORKER] Failed to read settings file: {e}, using default: {ai_mode_preference}")
                
                logger.info(f"[TEST PLAN WORKER] Sending request with ai_mode_preference: {ai_mode_preference}")
                response = requests.post(
                    f"{self.server_url}/api/ai/test/plan/generate",
                    json={
                        "functional_spec": self.functional_spec,
                        "ai_mode_preference": ai_mode_preference
                    },
                    timeout=90  # Longer timeout for LLM generation
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Check for error first
                    if "error" in result and result.get("error"):
                        self.error_received.emit(result.get("error", "Unknown error"))
                    else:
                        test_plan = result.get("test_plan", {})
                        if test_plan and isinstance(test_plan, dict) and len(test_plan) > 0:
                            self.test_plan_received.emit(test_plan)
                        else:
                            self.error_received.emit("No test plan generated. The response was empty or invalid.")
                else:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", f"HTTP {response.status_code}")
                        hint = error_data.get("hint", "")
                        # Enhance error message with hint if available
                        if hint:
                            error_msg = f"{error_msg}\n\n💡 {hint}"
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"
                    self.error_received.emit(error_msg)
        except requests.exceptions.Timeout:
            self.error_received.emit("Request timeout. The LLM may be taking longer than expected. Please try again.")
        except Exception as e:
            self.error_received.emit(f"Error: {str(e)}")
        finally:
            self.finished.emit()


# Removed PythonToPytestConverterWorker - users provide their own pytest scripts
class _RemovedPythonToPytestConverterWorker:
    """Background worker for Python to Pytest conversion using AI"""
    
    conversion_received = pyqtSignal(str)  # Emitted when conversion is received
    error_received = pyqtSignal(str)  # Emitted on error
    finished = pyqtSignal()  # Emitted when worker finishes
    
    def __init__(self, server_url, python_script):
        super().__init__()
        self.server_url = server_url
        self.python_script = python_script
        self.python_script = python_script
    
    def run(self):
        """Convert Python script to pytest format in background thread"""
        try:
            if not self.server_url:
                self.error_received.emit("Server URL is not configured. Please ensure a server is selected.")
                return
            
            # Load AI mode preference
            import os
            ai_mode_preference = "hybrid"  # default
            try:
                settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                if os.path.exists(settings_file):
                    with open(settings_file, 'r') as f:
                        client_settings = json.load(f)
                        ai_mode_preference = client_settings.get("preferred_ai_mode", "hybrid")
            except Exception:
                pass
            
            # Create prompt for AI conversion
            # Use string concatenation to avoid any format specifier issues with curly braces
            prompt_part1 = """Convert the following Python script to pytest format. Follow these rules EXACTLY:

REQUIREMENTS:
1. Use pytest fixtures (with @pytest.fixture decorator) for setup/teardown (like SSH connections, device connections, log files, subprocess mocks)
2. For command-line scripts with main() function:
   - Create test functions that test main() with different argument combinations
   - Use @pytest.mark.parametrize for testing different argument sets
   - Mock subprocess calls, file I/O, and external dependencies
   - Use pytest.raises() if main() exits with SystemExit
3. For individual functions:
   - Create test functions for each function (e.g., disable_interfaces_junos() -> test_disable_interfaces_junos())
   - Use fixtures to provide dependencies (mocks, test data)
   - Use assertions (assert) instead of return values or print statements
4. Include 'import pytest' at the top
5. Use proper pytest conventions:
   - @pytest.fixture for setup/teardown
   - def test_*() for test functions
   - assert statements for validation
   - @pytest.mark.parametrize for multiple test cases
   - unittest.mock.patch or pytest monkeypatch for mocking
6. Preserve ALL original logic and functionality
7. Mock external dependencies:
   - subprocess.run, subprocess.check_output -> use unittest.mock.patch
   - file I/O -> use tmp_path fixture or mock
   - argparse -> mock sys.argv or use pytest's monkeypatch
8. If the script uses SSH connections, create a fixture for the SSH connection
9. If the script has setup/cleanup code, move it to fixtures
10. For functions that call other functions, mock the dependencies
11. Make sure the code is syntactically correct and executable

CRITICAL - AVOID TYPOS:
- Use '@pytest.fixture' (NOT '@pvtest.fixture', NOT '@pytestfixture', NOT any other variation)
- Use 'import pytest' (NOT 'import pvtest', NOT 'import ptest', NOT any other variation)
- All test functions MUST start with 'test_'
- Use 'from unittest.mock import patch, MagicMock' for mocking (NOT 'from mock import')

CONVERSION STRATEGY:
- If script has main() function: Create test_main() with parametrize for different argument combinations
- For each helper function: Create a corresponding test function
- Mock all subprocess calls, file operations, and external commands
- Use fixtures for common setup (log files, test data, mocks)
- Preserve function names and logic, just wrap in test functions

CRITICAL INSTRUCTIONS - READ CAREFULLY:

1. YOU MUST COPY ALL FUNCTION DEFINITIONS FROM THE SCRIPT:
   - Copy the COMPLETE function body (everything from "def function_name" to the next "def" or end of file)
   - Place ALL function definitions BEFORE the test functions
   - DO NOT use imports like "from interface_control import" or "from your_module import"
   - The functions MUST be defined in the test file itself
   - Include ALL helper functions, not just the ones being tested

2. ABSOLUTELY NO IMPORTS OF FUNCTIONS:
   - DO NOT write "from interface_control import ..."
   - DO NOT write "from your_module import ..."
   - DO NOT write "from script_name import ..."
   - DO NOT use ANY import statement for functions from the script
   - The ONLY imports should be: pytest, unittest.mock, and standard library modules
   - Functions from the script MUST be copied directly into the test file

3. CREATE MEANINGFUL TESTS:
   - Test actual function behavior, not just exceptions
   - Use proper mocking for subprocess, file I/O, time.sleep
   - Assert actual return values and side effects
   - Test both success and failure cases

4. STRUCTURE YOUR OUTPUT LIKE THIS:
```python
import pytest
from unittest.mock import patch, MagicMock
import subprocess
import time

# ===== COPY ALL FUNCTION DEFINITIONS FROM THE SCRIPT HERE =====
def oir_ports_from_tokens(tokens):
    # ... copy the actual implementation from the script ...

def disable_interfaces_junos(ifaces):
    # ... copy the actual implementation from the script ...

def enable_interfaces_junos(ifaces):
    # ... copy the actual implementation from the script ...

# ... copy ALL other functions from the script ...

# ===== NOW CREATE TEST FUNCTIONS =====
@pytest.fixture
def mock_subprocess():
    with patch('subprocess.run') as mock_run, \\
         patch('subprocess.check_output') as mock_check:
        mock_run.return_value = MagicMock(returncode=0)
        mock_check.return_value = "mock output"
        yield {{'run': mock_run, 'check_output': mock_check}}

def test_oir_ports_from_tokens():
    # Test with actual input/output
    result = oir_ports_from_tokens("1/0:2/0,3/0:4/0")
    assert result == ["1/0", "2/0", "3/0", "4/0"]

def test_disable_interfaces_junos(mock_subprocess):
    # Mock subprocess calls and test actual behavior
    with patch('subprocess.check_output', return_value="ok"):
        result = disable_interfaces_junos(["et-0/0/1"])
        # Assert based on what the function actually does
        assert result is not None  # or whatever it returns
```

5. FOR EACH FUNCTION IN THE SCRIPT:
   - Copy its complete definition (including docstrings if present)
   - Create a test function that tests its actual behavior
   - Mock external dependencies (subprocess, file I/O, time.sleep)
   - Write assertions that verify the function's logic works correctly

MANDATORY: For the provided script, you MUST:
1. Analyze ALL functions in the script and create test functions for each
2. Use the ACTUAL function names from the script (not placeholders like "your_module")
3. Create meaningful test assertions that verify actual function behavior
4. Mock external dependencies (subprocess, file I/O, time) but test real logic
5. Include fixtures for common setup (mock_subprocess, mock_logfile, etc.)

Original Python Script:
```python
"""
            prompt_part2 = """
```

Provide ONLY the converted pytest code in a markdown code block. Include test functions for ALL functions in the script. No explanations, no comments outside code. Make sure it's valid, executable pytest code."""
            
            # Concatenate parts to avoid format specifier issues
            prompt = prompt_part1 + self.python_script + prompt_part2

            response = requests.post(
                f"{self.server_url}/api/ai/chat",
                json={
                    "message": prompt,
                    "context": {},
                    "ai_mode_preference": ai_mode_preference,
                    "normalize_response": False  # Don't normalize code
                },
                timeout=90  # Longer timeout for code generation
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_response = result.get("response", "")
                
                logger.info(f"[PYTHON TO PYTEST] AI response length: {len(ai_response)} chars")
                logger.debug(f"[PYTHON TO PYTEST] AI response preview: {ai_response[:500]}")
                
                # Extract code from markdown code blocks if present
                import re
                code_blocks = re.findall(r'```(?:python)?\n(.*?)```', ai_response, re.DOTALL)
                if code_blocks:
                    converted_code = code_blocks[0].strip()
                    logger.info(f"[PYTHON TO PYTEST] Extracted code block: {len(converted_code)} chars")
                else:
                    # If no code blocks, assume entire response is code
                    converted_code = ai_response.strip()
                    logger.info(f"[PYTHON TO PYTEST] Using entire response as code: {len(converted_code)} chars")
                
                # Post-process to fix common issues
                converted_code = self._fix_common_pytest_issues(converted_code)
                
                # Fix placeholder imports and extract functions
                converted_code = self._fix_placeholder_imports(converted_code)
                
                # Fix invalid imports like "from interface_control import"
                converted_code = self._fix_invalid_function_imports(converted_code)
                
                # Validate and fix syntax errors
                converted_code = self._validate_and_fix_syntax(converted_code)
                
                if converted_code:
                    self.conversion_received.emit(converted_code)
                else:
                    self.error_received.emit("No converted code received from AI")
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", f"HTTP {response.status_code}")
                except Exception:
                    error_msg = f"HTTP {response.status_code}"
                self.error_received.emit(error_msg)
        except requests.exceptions.Timeout:
            self.error_received.emit("Request timeout. The AI may be taking longer than expected. Please try again.")
        except Exception as e:
            logger.error(f"Error in PythonToPytestConverterWorker: {e}", exc_info=True)
            self.error_received.emit(f"Error: {str(e)}")
        finally:
            self.finished.emit()
    
    def _fix_common_pytest_issues(self, code):
        """Fix common issues in AI-generated pytest code"""
        import re
        
        # Fix common typos
        fixes = [
            (r'@pvtest\.fixture', '@pytest.fixture'),  # Fix pvtest -> pytest typo
            (r'@pytest\.fixture\s*\(\)', '@pytest.fixture'),  # Remove empty parentheses if needed
            (r'import pvtest', 'import pytest'),  # Fix import typo
            (r'from pvtest import', 'from pytest import'),  # Fix from import typo
            (r'def test_([a-zA-Z_][a-zA-Z0-9_]*)\(\):', r'def test_\1():'),  # Ensure test functions have proper format
        ]
        
        for pattern, replacement in fixes:
            code = re.sub(pattern, replacement, code)
        
        # Ensure pytest import is present
        if 'import pytest' not in code and '@pytest.fixture' in code:
            # Add import at the top if missing
            lines = code.split('\n')
            import_line = -1
            for i, line in enumerate(lines):
                if line.strip().startswith('import ') or line.strip().startswith('from '):
                    import_line = i
                    break
            
            if import_line >= 0:
                lines.insert(import_line + 1, 'import pytest')
            else:
                lines.insert(0, 'import pytest')
            code = '\n'.join(lines)
        
        return code
    
    def _fix_placeholder_imports(self, code):
        """Fix placeholder imports and broken import statements by extracting functions from script"""
        import re
        
        lines = code.split('\n')
        broken_import_detected = False
        broken_import_start = -1
        broken_import_end = -1
        func_names = []
        
        # Detect broken import pattern: comment + import pytest + function names listed
        for i, line in enumerate(lines):
            if ('# NOTE: Functions' in line or '# Functions' in line) and i + 1 < len(lines):
                if 'import pytest' in lines[i + 1]:
                    # Look for function names listed after import pytest
                    j = i + 2
                    while j < len(lines):
                        line_stripped = lines[j].strip()
                        if not line_stripped or line_stripped.startswith('#'):
                            j += 1
                            continue
                        # Check if it's a function name (word followed by comma or closing paren)
                        match = re.match(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[,\)]', line_stripped)
                        if match:
                            func_names.append(match.group(1))
                            j += 1
                            if ')' in line_stripped:
                                broken_import_start = i
                                broken_import_end = j
                                broken_import_detected = True
                                break
                        elif line_stripped.startswith('def test_'):
                            # Reached test functions, stop looking
                            break
                        else:
                            break
                    if broken_import_detected:
                        break
        
        if broken_import_detected and func_names:
            logger.warning(f"[PYTHON TO PYTEST] Detected broken import pattern, extracting {len(func_names)} functions from script")
            logger.info(f"[PYTHON TO PYTEST] Functions to extract: {func_names}")
            
            # Extract function definitions from original script
            extracted_functions = []
            for func_name in func_names:
                # Find function definition - match from def to next def or end of file
                pattern = rf'(^def\s+{re.escape(func_name)}\s*\([^)]*\):.*?)(?=^def\s+|\Z)'
                match = re.search(pattern, self.python_script, re.MULTILINE | re.DOTALL)
                if match:
                    func_def = match.group(1).strip()
                    # Clean up trailing whitespace
                    func_def = '\n'.join(line.rstrip() for line in func_def.split('\n'))
                    extracted_functions.append((func_name, func_def))
                    logger.info(f"[PYTHON TO PYTEST] Extracted function: {func_name} ({len(func_def)} chars)")
                else:
                    logger.warning(f"[PYTHON TO PYTEST] Could not find function definition for: {func_name}")
            
            if extracted_functions:
                # Remove broken import lines (from comment to closing paren)
                new_lines = lines[:broken_import_start]
                if broken_import_end < len(lines):
                    new_lines.extend(lines[broken_import_end:])
                
                # Find where to insert functions (before first test function)
                insert_idx = len(new_lines)
                for i, line in enumerate(new_lines):
                    if line.strip().startswith('def test_'):
                        insert_idx = i
                        break
                
                # Insert extracted functions
                new_lines.insert(insert_idx, '')
                new_lines.insert(insert_idx, '# ===== Functions extracted from original script =====')
                for func_name, func_def in reversed(extracted_functions):
                    new_lines.insert(insert_idx, '')
                    # Split function into lines and insert
                    func_lines = func_def.split('\n')
                    for func_line in reversed(func_lines):
                        new_lines.insert(insert_idx, func_line)
                new_lines.insert(insert_idx, '')
                
                code = '\n'.join(new_lines)
                logger.info(f"[PYTHON TO PYTEST] Fixed broken import by extracting {len(extracted_functions)} functions")
        
        # Also check for placeholder imports
        if 'from your_module import' in code or 'import your_module' in code:
            logger.warning("[PYTHON TO PYTEST] Detected placeholder imports, removing...")
            code = re.sub(r'from your_module import[^\n]+\n', '', code)
            code = re.sub(r'import your_module[^\n]*\n', '', code)
            code = re.sub(r'your_module\.', '', code)
            code = re.sub(r"patch\(['\"]your_module\.", "patch('", code)
        
        return code
    
    def _fix_invalid_function_imports(self, code):
        """Fix invalid imports like 'from interface_control import' by extracting functions"""
        import re
        
        # Pattern to match imports like "from interface_control import (...)"
        invalid_import_pattern = r'from\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+import\s*\(([^)]+)\)'
        matches = list(re.finditer(invalid_import_pattern, code))
        
        if matches:
            logger.warning(f"[PYTHON TO PYTEST] Detected {len(matches)} invalid function imports, fixing...")
            
            for match in matches:
                module_name = match.group(1)
                import_list = match.group(2)
                
                # Extract function names from import list
                func_names = [f.strip() for f in re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]+)\b', import_list)]
                func_names = [f for f in func_names if f not in ['from', 'import']]
                
                logger.info(f"[PYTHON TO PYTEST] Removing import from '{module_name}', extracting functions: {func_names}")
                
                # Remove the import line
                code = code.replace(match.group(0), '')
                
                # Extract function definitions from script
                extracted_functions = []
                for func_name in func_names:
                    pattern = rf'(^def\s+{re.escape(func_name)}\s*\([^)]*\):.*?)(?=^def\s+|\Z)'
                    match_func = re.search(pattern, self.python_script, re.MULTILINE | re.DOTALL)
                    if match_func:
                        func_def = match_func.group(1).strip()
                        func_def = '\n'.join(line.rstrip() for line in func_def.split('\n'))
                        extracted_functions.append((func_name, func_def))
                        logger.info(f"[PYTHON TO PYTEST] Extracted function: {func_name}")
                
                if extracted_functions:
                    # Insert before first test function
                    lines = code.split('\n')
                    insert_idx = len(lines)
                    for i, line in enumerate(lines):
                        if line.strip().startswith('def test_'):
                            insert_idx = i
                            break
                    
                    # Only add if functions aren't already present
                    existing_funcs = set()
                    for line in lines[:insert_idx]:
                        match = re.match(r'^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', line)
                        if match:
                            existing_funcs.add(match.group(1))
                    
                    for func_name, func_def in reversed(extracted_functions):
                        if func_name not in existing_funcs:
                            lines.insert(insert_idx, '')
                            func_lines = func_def.split('\n')
                            for func_line in reversed(func_lines):
                                lines.insert(insert_idx, func_line)
                    
                    if extracted_functions:
                        lines.insert(insert_idx, '')
                        lines.insert(insert_idx, '# ===== Functions extracted from original script =====')
                    
                    code = '\n'.join(lines)
        
        return code
    
    def _validate_and_fix_syntax(self, code):
        """Validate and fix common syntax errors in generated code"""
        import re
        import ast
        
        # Fix invalid assignment syntax like "function_call(...) = value"
        # This is invalid Python - remove these lines
        invalid_assignment_pattern = r'^\s*\w+\([^)]+\)\s*=\s*[A-Z][a-zA-Z0-9_]*\(\)\s*$'
        lines = code.split('\n')
        fixed_lines = []
        removed_lines = []
        
        for line in lines:
            if re.match(invalid_assignment_pattern, line):
                logger.warning(f"[PYTHON TO PYTEST] Removing invalid assignment: {line[:60]}")
                removed_lines.append(line)
                continue
            fixed_lines.append(line)
        
        if removed_lines:
            code = '\n'.join(fixed_lines)
            logger.info(f"[PYTHON TO PYTEST] Removed {len(removed_lines)} invalid assignment lines")
        
        # Try to parse the code to catch syntax errors
        try:
            ast.parse(code)
            logger.info("[PYTHON TO PYTEST] Code passed syntax validation")
        except SyntaxError as e:
            logger.warning(f"[PYTHON TO PYTEST] Syntax error detected: {e} at line {e.lineno}")
            # Try to fix by removing the problematic line
            if e.lineno and e.lineno <= len(fixed_lines):
                logger.warning(f"[PYTHON TO PYTEST] Attempting to remove problematic line {e.lineno}")
                fixed_lines.pop(e.lineno - 1)
                code = '\n'.join(fixed_lines)
                try:
                    ast.parse(code)
                    logger.info("[PYTHON TO PYTEST] Code fixed and passed validation")
                except SyntaxError as e2:
                    logger.error(f"[PYTHON TO PYTEST] Still has syntax errors: {e2}")
        
        return code


class PytestGenerationWorker(QThread):
    """Background worker for pytest generation from test plan to prevent UI freezing"""
    
    pytest_received = pyqtSignal(str)  # Emitted when pytest script is received
    error_received = pyqtSignal(str)  # Emitted on error
    finished = pyqtSignal()  # Emitted when worker finishes
    
    def __init__(self, server_url, test_plan=None, functional_spec=None, agent_mode=False, ai_mode_preference="hybrid", agent_model=None):
        super().__init__()
        self.server_url = server_url
        self.test_plan = test_plan
        self.functional_spec = functional_spec  # For direct generation from spec
        self.agent_mode = agent_mode
        self.ai_mode_preference = ai_mode_preference
        self.agent_model = agent_model
    
    def run(self):
        """Generate pytest script in background thread"""
        try:
            # Validate server URL before making request
            if not self.server_url:
                self.error_received.emit("Server URL is not configured. Please ensure a server is selected.")
                return
            
            # Validate we have either test_plan or functional_spec
            if not self.test_plan and not self.functional_spec:
                self.error_received.emit("Either test plan or functional specification is required.")
                return
            
            if self.agent_mode:
                # Use agent endpoint for intelligent pytest generation
                if self.functional_spec:
                    # Generate from functional spec
                    title = self.functional_spec.get("title", "Test")
                    requirements = self.functional_spec.get("requirements", [])
                    req_text = ", ".join(requirements[:3]) if requirements else "test requirements"
                    user_message = f"Generate an executable pytest script for: {title}. Requirements: {req_text}. Create a complete, runnable pytest file with proper fixtures, device connection logic, and test implementations."
                    
                    payload = {
                        "message": user_message,
                        "context": {
                            "functional_spec": self.functional_spec
                        },
                        "ai_mode_preference": self.ai_mode_preference
                    }
                else:
                    # Generate from test plan
                    test_plan_title = self.test_plan.get("title", "Test Plan")
                    user_message = f"Generate an executable pytest script from this test plan: {test_plan_title}. The test plan includes test cases, unit tests, and integration tests. Create a complete, runnable pytest file with proper fixtures and implementations."
                    
                    payload = {
                        "message": user_message,
                        "context": {
                            "test_plan": self.test_plan  # Pass test plan in context
                        },
                        "ai_mode_preference": self.ai_mode_preference
                    }
                
                if self.agent_model:
                    payload["agent_model"] = self.agent_model
                
                logger.info(f"[PYTEST AGENT] Generating pytest with agent_mode=True, ai_mode_preference={self.ai_mode_preference}, agent_model={self.agent_model}")
                
                response = requests.post(
                    f"{self.server_url}/api/ai/test/plan/agent",
                    json=payload,
                    timeout=120  # Longer timeout for agent execution
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Agent response structure: {"response": "...", "test_plan": {...}, "steps": [...]}
                    # The agent might return pytest_script in response or in tool results (steps)
                    agent_response = result.get("response", "")
                    steps = result.get("steps", [])
                    
                    # Try to extract pytest_script from agent's tool results in steps
                    pytest_script = None
                    for step in steps:
                        # Check if this step has a result with pytest_script
                        step_result = step.get("result")
                        if isinstance(step_result, dict):
                            pytest_script = step_result.get("pytest_script")
                            if pytest_script:
                                logger.info(f"[PYTEST AGENT] Found pytest_script in step: {step.get('tool_name')}")
                                break
                        # Also check if result is directly the pytest script (string)
                        elif isinstance(step_result, str) and ("def test_" in step_result or "import pytest" in step_result):
                            pytest_script = step_result
                            logger.info(f"[PYTEST AGENT] Found pytest_script as string in step: {step.get('tool_name')}")
                            break
                    
                    # If not found in tool results, check if agent_response contains pytest code
                    if not pytest_script:
                        # Check if agent_response looks like pytest code
                        if "def test_" in agent_response or "import pytest" in agent_response:
                            pytest_script = agent_response
                            logger.info("[PYTEST AGENT] Found pytest_script in agent response")
                        else:
                            # Try to extract from agent_response if it contains code blocks
                            import re
                            # Try different code block patterns
                            code_blocks = re.findall(r'```python\n(.*?)\n```', agent_response, re.DOTALL)
                            if not code_blocks:
                                code_blocks = re.findall(r'```\n(.*?)\n```', agent_response, re.DOTALL)
                            if code_blocks:
                                pytest_script = code_blocks[0].strip()
                                logger.info("[PYTEST AGENT] Extracted pytest_script from code block")
                    
                    if pytest_script:
                        self.pytest_received.emit(pytest_script)
                    else:
                        # Provide more detailed error message
                        error_msg = f"Agent did not generate pytest script.\n"
                        error_msg += f"Agent response: {agent_response[:300]}\n"
                        error_msg += f"Steps executed: {len(steps)}\n"
                        if steps:
                            error_msg += f"Last tool: {steps[-1].get('tool_name', 'unknown')}\n"
                        logger.warning(f"[PYTEST AGENT] {error_msg}")
                        self.error_received.emit(error_msg)
                else:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", f"HTTP {response.status_code}")
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"
                    self.error_received.emit(error_msg)
            else:
                # Use direct pytest generation endpoint
                if self.functional_spec:
                    # Generate directly from functional spec
                    response = requests.post(
                        f"{self.server_url}/api/ai/test/plan/generate-pytest-from-spec",
                        json={
                            "functional_spec": self.functional_spec,
                            "framework": "pytest",
                            "ai_mode_preference": self.ai_mode_preference
                        },
                        timeout=90  # Longer timeout for LLM generation
                    )
                else:
                    # Generate from test plan
                    response = requests.post(
                        f"{self.server_url}/api/ai/test/plan/generate-pytest",
                        json={"test_plan": self.test_plan},
                        timeout=90  # Longer timeout for LLM generation
                    )
                
                if response.status_code == 200:
                    result = response.json()
                    pytest_script = result.get("pytest_script", "")
                    if pytest_script:
                        self.pytest_received.emit(pytest_script)
                    else:
                        self.error_received.emit("No pytest script generated")
                else:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", f"HTTP {response.status_code}")
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"
                    self.error_received.emit(error_msg)
        except requests.exceptions.Timeout:
            self.error_received.emit("Request timeout. The LLM may be taking longer than expected. Please try again.")
        except Exception as e:
            self.error_received.emit(f"Error: {str(e)}")
        finally:
            self.finished.emit()


class UnifiedAIChatWorker(QThread):
    """Background worker for Unified AI chat to prevent UI freezing"""
    
    response_received = pyqtSignal(dict)  # Emit dict with response, model, source
    error = pyqtSignal(str)
    
    def __init__(self, server_url, message, context=None, normalize_response=True):
        super().__init__()
        self.server_url = server_url
        self.message = message
        self.context = context or {}
        self.normalize_response = normalize_response
    
    def run(self):
        try:
            # Validate server URL before making request
            if not self.server_url:
                self.error.emit("Server URL is not configured. Please ensure a server is selected.")
                return
            
            import os
            # Load AI mode preference to send to server
            ai_mode_preference = "hybrid"  # default
            try:
                settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                if os.path.exists(settings_file):
                    with open(settings_file, 'r') as f:
                        client_settings = json.load(f)
                        ai_mode_preference = client_settings.get("preferred_ai_mode", "hybrid")
            except Exception:
                pass
            
            response = requests.post(
                f"{self.server_url}/api/ai/chat",
                json={
                    "message": self.message,
                    "context": self.context,
                    "ai_mode_preference": ai_mode_preference,
                    "normalize_response": self.normalize_response
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_response = result.get("response", "I'm sorry, I couldn't generate a response.")
                model = result.get("model", "unknown")
                source = result.get("source", "unknown")
                
                logger.info(f"[UNIFIED AI] Received response - length: {len(ai_response)}, model: {model}, source: {source}")
                
                # Emit response with all info
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
            error_str = str(e)
            # Provide more helpful error message for connection refused
            if "Connection refused" in error_str or "Failed to establish" in error_str:
                self.error.emit(f"Server connection failed: The server at {self.server_url} is not running or not reachable. Please ensure the server is started and accessible.")
            else:
                self.error.emit(f"Network error: {error_str}")
        except Exception as e:
            logger.error(f"Error in UnifiedAIChatWorker: {e}", exc_info=True)
            self.error.emit(str(e))


class AIUnifiedDialog(QDialog):
    """Unified AI Assistant Dialog with all capabilities"""
    
    def __init__(self, parent=None, device_id=None):
        super().__init__(parent)
        self.setWindowTitle("NetGenAI")
        # Make the unified chat window more compact by default
        self.setMinimumSize(1200, 900)
        self.resize(980, 700)
        
        # Get server URL from parent, but ensure it's not None
        parent_url = getattr(parent, 'server_url', None) if parent else None
        
        # If parent URL is None, try to get it from selected server in tree
        if not parent_url and parent:
            try:
                if hasattr(parent, 'server_tree') and parent.server_tree:
                    selected_items = parent.server_tree.selectedItems()
                    if selected_items:
                        selected_item = selected_items[0]
                        server_item = selected_item.parent() if selected_item.parent() else selected_item
                        server_address = server_item.text(1)
                        if server_address and server_address.startswith(("http://", "https://")):
                            parent_url = server_address
            except Exception:
                pass
        
        # If still None, try to get from server_interfaces (first online server)
        if not parent_url and parent:
            try:
                if hasattr(parent, 'server_interfaces') and parent.server_interfaces:
                    for server in parent.server_interfaces:
                        server_addr = server.get("address")
                        if server_addr and server_addr.startswith(("http://", "https://")):
                            parent_url = server_addr
                            break
            except Exception:
                pass
        
        # Fallback to default if still None
        self.server_url = parent_url if parent_url else 'http://localhost:5051'
        
        # Initialize empty context - AI assistant works independently
        self.context = {}
        
        # If device_id is provided, store it for optional use in tabs (user can still change it)
        if device_id:
            self.context['device_id'] = device_id
        
        # Track last user message for potential retries
        self._last_user_message = ""
        self._code_retry_active = False
        self._code_retry_count = 0
        
        # Initialize pytest script tracking for Test Framework integration
        self.current_pytest_script = None
        self.current_pytest_script_file = None
        
        # Track if pytest script came from Test Plan (for indicator)
        self._pytest_from_test_plan = False
        
        # Load preferences (e.g., formatting)
        self.format_python_with_black = self._load_client_preference("format_python_with_black", True)
        
        # Apply professional styling
        self._apply_professional_styling()
        
        self.setup_ui()
    
    def _apply_professional_styling(self):
        """Apply professional styling to the dialog"""
        self.setStyleSheet("""
            QDialog {
                background-color: #ecf0f3;
                font-family: 'Inter', 'Segoe UI', 'SF Pro Display', -apple-system, sans-serif;
                color: #1f2933;
            }
            
            QLabel {
                color: #1f2933;
                font-size: 13px;
            }
            
            QLineEdit, QComboBox {
                background-color: #ffffff;
                border: 1px solid #d7dce3;
                border-radius: 8px;
                padding: 10px 12px;
                font-size: 13px;
                color: #1f2933;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #d7dce3;
                border-radius: 8px;
                padding: 10px 12px;
                font-size: 13px;
                /* Don't set text color - let HTML control it */
            }
            
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 1.5px solid #2563eb;
                outline: none;
            }
            
            QLineEdit:hover, QTextEdit:hover, QComboBox:hover {
                border: 1px solid #c1c7d0;
            }
            
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 10px 22px;
                font-size: 13px;
                font-weight: 600;
                min-height: 36px;
                letter-spacing: 0.2px;
            }
            
            QPushButton:hover {
                background-color: #1d4fd8;
            }
            
            QPushButton:pressed {
                background-color: #153e9f;
            }
            
            QPushButton:disabled {
                background-color: #d4dae2;
                color: #8a94a5;
            }
            
            QTabWidget::pane {
                border: 1px solid #d7dce3;
                border-radius: 10px;
                background-color: #ffffff;
                top: -1px;
            }
            
            QTabBar::tab {
                background-color: #eef1f5;
                color: #5a6472;
                border: 1px solid #d7dce3;
                border-bottom: none;
                padding: 12px 24px;
                margin-right: 4px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                font-size: 13px;
                font-weight: 600;
                letter-spacing: 0.2px;
            }
            
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #2563eb;
                border-bottom: 2px solid #2563eb;
                font-weight: 600;
            }
            
            QTabBar::tab:hover:!selected {
                background-color: #ffffff;
                color: #1f2933;
            }
            
            QTextEdit {
                font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
                font-size: 12px;
                line-height: 1.5;
            }
            
            QGroupBox {
                font-weight: 600;
                font-size: 14px;
                color: #1f2933;
                border: 1px solid #d7dce3;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 16px;
                background-color: #ffffff;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                background-color: #ffffff;
            }
            
            QFormLayout {
                spacing: 16px;
            }
            
            QFormLayout QLabel {
                font-weight: 500;
                color: #1f2933;
                min-width: 120px;
            }
            
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #d7dce3;
                border-radius: 10px;
                padding: 16px;
            }
            
            QListWidget {
                background-color: #ffffff;
                border: 1px solid #d7dce3;
                border-radius: 8px;
                padding: 8px;
            }
            
            QScrollBar:vertical {
                width: 10px;
                background: #f4f6f9;
                margin: 0px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #c7ceda;
                min-height: 24px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #9aa6b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
    
    def _get_context(self, parent):
        """Get context from parent - not used, kept for compatibility"""
        # AI assistant works independently, no device context needed
        return {}
    
    def _load_client_preference(self, key, default_value):
        """Load a boolean preference from the client settings file."""
        try:
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, "r") as f:
                    data = json.load(f)
                    return bool(data.get(key, default_value))
        except Exception:
            pass
        return default_value
    
    def _save_client_preference(self, key, value):
        """Persist a preference to the client settings file."""
        try:
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            data = {}
            if os.path.exists(settings_file):
                with open(settings_file, "r") as f:
                    data = json.load(f) or {}
            data[key] = value
            with open(settings_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Unable to save preference {key}: {e}")

    def _toggle_black_preference(self, state):
        """Enable/disable Black-based formatting for Python code."""
        self.format_python_with_black = bool(state)
        self._save_client_preference("format_python_with_black", self.format_python_with_black)
    
    def setup_ui(self):
        """Setup UI with tabs for different AI features"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        
        # Professional header
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(6)
        header_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #eef2f7, stop:1 #ffffff);
                border: 1px solid #d7dce3;
                border-radius: 14px;
            }
        """)
        
        # Title with icon
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(12)
        
        title_icon = QLabel("🤖")
        title_icon.setStyleSheet("font-size: 28px;")
        title_layout.addWidget(title_icon)
        
        title = QLabel("NetGenAI")
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setWeight(QFont.Bold)
        title.setFont(title_font)
        title.setStyleSheet("color: #1d1d1f; margin: 0;")
        title_layout.addWidget(title)
        title_layout.addStretch()
        
        header_layout.addLayout(title_layout)
        
        # Subtitle
        subtitle = QLabel("Unified automation, testing, and troubleshooting partner")
        subtitle_font = QFont()
        subtitle_font.setPointSize(13)
        subtitle.setFont(subtitle_font)
        subtitle.setStyleSheet("color: #5a6472; margin-left: 40px;")
        header_layout.addWidget(subtitle)
        
        layout.addWidget(header_widget)
        
        # Tab widget for different AI features
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                background-color: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-bottom: none;
                padding: 6px 12px;
                margin-right: 2px;
                font-size: 11px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #007aff;
                border-bottom: 2px solid #007aff;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e5e5ea;
                color: #1d1d1f;
            }
            QTabBar::tab:first {
                border-top-left-radius: 4px;
            }
            QTabBar::tab:last {
                border-top-right-radius: 4px;
            }
        """)
        # Get the tab bar and ensure tabs size to content (don't truncate text)
        tab_bar = self.tabs.tabBar()
        tab_bar.setExpanding(False)  # Don't expand to fill space, size to content
        tab_bar.setElideMode(Qt.ElideNone)  # Don't elide (truncate) text
        
        # 1. Chat Tab
        self.tabs.addTab(self.create_chat_tab(), "💬 Chat")
        
        # 2. Troubleshooting Tab
        self.tabs.addTab(self.create_troubleshooting_tab(), "🔍 Troubleshooting")
        
        # 3. Test Framework Tab
        self.test_framework_widget = self.create_test_framework_tab()
        self.tabs.addTab(self.test_framework_widget, "🧪 Test Framework")
        
        # 4. Test Plan Generator Tab
        self.tabs.addTab(self.create_test_plan_tab(), "📋 Test Plans")
        
        # 5. Code Generator Tab
        self.tabs.addTab(self.create_code_generator_tab(), "💻 Code Generator")
        
        # 6. Device Testing Tab
        self.tabs.addTab(self.create_device_testing_tab(), "🔧 Device Testing")
        
        layout.addWidget(self.tabs)
        
        # Footer with Clear and Close buttons
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 16, 0, 0)
        footer_layout.setSpacing(10)
        footer_layout.addStretch()
        
        # Clear conversation button
        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                min-height: 24px;
                max-height: 24px;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
                border: 1px solid #a1a1a6;
            }
            QPushButton:pressed {
                background-color: #d2d2d7;
            }
        """)
        clear_btn.clicked.connect(self.clear_conversation)
        footer_layout.addWidget(clear_btn)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                min-height: 24px;
                max-height: 24px;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
                border: 1px solid #a1a1a6;
            }
            QPushButton:pressed {
                background-color: #d2d2d7;
            }
        """)
        close_btn.clicked.connect(self.accept)
        footer_layout.addWidget(close_btn)
        layout.addLayout(footer_layout)
    
    def create_chat_tab(self):
        """Create chat interface tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        
        # Quick prompts row
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        quick_prompts = [
            "Summarize device logs",
            "Generate BGP test plan",
            "Diagnose interface flaps",
            "Draft pytest for topology"
        ]
        for prompt in quick_prompts:
            btn = QPushButton(prompt)
            btn.setObjectName("PillButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, p=prompt: self._prefill_prompt(p))
            btn.setStyleSheet("""
                QPushButton#PillButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 0px;
                    padding: 0px;
                    font-size: 11px;
                    color: #2563eb;
                    text-decoration: underline;
                }
                QPushButton#PillButton:hover {
                    color: #1d4fd8;
                }
            """)
            quick_row.addWidget(btn)
        quick_row.addStretch()
        layout.addLayout(quick_row)
        
        # Chat container card
        chat_card = QWidget()
        chat_card.setObjectName("ChatCard")
        chat_card_layout = QVBoxLayout(chat_card)
        chat_card_layout.setContentsMargins(14, 14, 14, 14)
        chat_card_layout.setSpacing(12)
        chat_card.setStyleSheet("""
            QWidget#ChatCard {
                background-color: #ffffff;
                border: 1px solid #d7dce3;
                border-radius: 12px;
            }
        """)
        
        # Chat header row (badge and normalization toggle)
        chat_header = QHBoxLayout()
        chat_header.setSpacing(8)
        model_badge = QLabel("AI assistant active")
        model_badge.setStyleSheet("""
            QLabel {
                background-color: #2563eb22;
                color: #2563eb;
                border: 1px solid #2563eb44;
                border-radius: 10px;
                padding: 4px 10px;
                font-weight: 600;
                font-size: 11px;
            }
        """)
        chat_header.addWidget(model_badge)
        
        # Normalize response checkbox
        self.normalize_checkbox = QCheckBox("Normalize response")
        self.normalize_checkbox.setChecked(True)  # Default to enabled
        self.normalize_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 11px;
                color: #5a6472;
                padding: 4px 8px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
        """)
        chat_header.addWidget(self.normalize_checkbox)
        chat_header.addStretch()
        chat_card_layout.addLayout(chat_header)
        
        # Chat display area
        # Use QTextBrowser instead of QTextEdit - it's better for rich text display
        from PyQt5.QtWidgets import QTextBrowser
        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("Start a conversation with the AI assistant...")
        # Don't set text color in stylesheet - let HTML control colors
        self.chat_display.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #d7dce3;
                border-radius: 12px;
                padding: 18px;
                font-size: 13px;
                line-height: 1.6;
                color: #1f2933;
            }
        """)
        # QTextBrowser respects HTML colors better than QTextEdit
        self.chat_display.setHtml("")  # Initialize with empty HTML
        
        # Welcome message
        welcome_html = """
        <div style='padding: 14px 16px; background-color: #f7f9fc; border-radius: 10px; margin-bottom: 12px; border: 1px solid #e1e6ee;'>
            <p style='margin: 0; color: #1f2933; font-size: 14px; font-weight: 700;'>NetGenAI</p>
            <p style='margin: 8px 0 0 0; color: #5a6472; font-size: 13px;'>I can help with troubleshooting, test plans, and automation. Tell me what you need.</p>
        </div>
        """
        self.chat_display.setHtml(welcome_html)
        chat_card_layout.addWidget(self.chat_display)
        
        # Input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask a question, request a config, or describe a symptom...")
        self.chat_input.returnPressed.connect(self.send_chat_message)
        input_layout.addWidget(self.chat_input)
        
        send_btn = QPushButton("Send")
        send_btn.setMinimumWidth(60)
        send_btn.setMaximumWidth(60)
        send_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 12px;
                font-size: 12px;
            }
        """)
        send_btn.clicked.connect(self.send_chat_message)
        input_layout.addWidget(send_btn)
        
        chat_card_layout.addLayout(input_layout)
        
        layout.addWidget(chat_card)
        
        return widget
    
    def create_troubleshooting_tab(self):
        """Create troubleshooting tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        
        # Input group
        input_group = QGroupBox("Diagnosis Input")
        form = QFormLayout(input_group)
        form.setSpacing(16)
        form.setContentsMargins(20, 24, 20, 20)
        
        self.troubleshoot_device = QLineEdit()
        if self.context.get('device_id'):
            self.troubleshoot_device.setText(self.context.get('device_id'))
        self.troubleshoot_device.setPlaceholderText("Enter device ID or name")
        form.addRow("Device ID:", self.troubleshoot_device)
        
        self.troubleshoot_symptoms = QTextEdit()
        self.troubleshoot_symptoms.setPlaceholderText("Describe the symptoms, issues, or problems you're experiencing...")
        self.troubleshoot_symptoms.setMinimumHeight(120)
        form.addRow("Symptoms:", self.troubleshoot_symptoms)
        
        layout.addWidget(input_group)
        
        # Action button
        troubleshoot_btn = QPushButton("🔍 Start Diagnosis")
        troubleshoot_btn.setMinimumHeight(44)
        troubleshoot_btn.clicked.connect(self.start_troubleshooting)
        layout.addWidget(troubleshoot_btn)
        
        # Results group
        results_group = QGroupBox("Diagnosis Results")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(20, 24, 20, 20)
        
        self.troubleshoot_results = QTextEdit()
        self.troubleshoot_results.setReadOnly(True)
        self.troubleshoot_results.setPlaceholderText("Diagnosis results will appear here...")
        results_layout.addWidget(self.troubleshoot_results)
        
        layout.addWidget(results_group)
        
        return widget
    
    def create_test_framework_tab(self):
        """Create test framework tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Test Configuration and Results
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        
        # Test Configuration group
        input_group = QGroupBox("Test Configuration")
        input_group.setStyleSheet("""
            QGroupBox {
                font-size: 12px;
                font-weight: 600;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        form = QFormLayout(input_group)
        form.setSpacing(6)
        form.setContentsMargins(10, 12, 10, 10)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        
        # First row: Device ID and Pytest Script side by side
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.setContentsMargins(0, 0, 0, 0)
        
        # Device ID
        device_label = QLabel("Device ID:")
        device_label.setMinimumWidth(70)
        device_label.setMaximumWidth(70)
        self.test_device = QLineEdit()
        # Pre-fill with device_id if provided, but user can change it
        if self.context.get('device_id'):
            self.test_device.setText(self.context.get('device_id'))
        self.test_device.setPlaceholderText("Enter device ID (optional if CSV provided)")
        self.test_device.setFixedHeight(24)
        self.test_device.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.test_device.setStyleSheet("""
            QLineEdit {
                padding: 3px 6px;
                font-size: 11px;
            }
        """)
        row1.addWidget(device_label)
        row1.addWidget(self.test_device)
        
        # Pytest script file upload
        pytest_label = QLabel("Pytest Script:")
        pytest_label.setMinimumWidth(85)
        pytest_label.setMaximumWidth(85)
        self.pytest_file_path = QLineEdit()
        self.pytest_file_path.setPlaceholderText("No file selected")
        self.pytest_file_path.setReadOnly(True)
        self.pytest_file_path.setFixedHeight(24)
        self.pytest_file_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pytest_file_path.setStyleSheet("""
            QLineEdit {
                padding: 3px 6px;
                font-size: 11px;
            }
        """)
        
        browse_pytest_btn = QPushButton("📁 Browse...")
        browse_pytest_btn.setFixedHeight(24)
        browse_pytest_btn.setFixedWidth(75)
        browse_pytest_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                border-radius: 2px;
                padding: 1px 6px;
                font-size: 10px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
            QPushButton:pressed {
                background-color: #545b62;
            }
        """)
        browse_pytest_btn.clicked.connect(self.browse_pytest_file)
        
        row1.addWidget(pytest_label)
        row1.addWidget(self.pytest_file_path)
        row1.addWidget(browse_pytest_btn)
        
        form.addRow(row1)
        
        # Second row: CSV file for device info
        csv_file_layout = QHBoxLayout()
        csv_file_layout.setSpacing(6)
        csv_file_layout.setContentsMargins(0, 0, 0, 0)
        
        csv_label = QLabel("Device CSV:")
        csv_label.setMinimumWidth(70)
        csv_label.setMaximumWidth(70)
        self.device_csv_path = QLineEdit()
        self.device_csv_path.setPlaceholderText("Optional: CSV with device name, user, password")
        self.device_csv_path.setReadOnly(True)
        self.device_csv_path.setFixedHeight(24)
        self.device_csv_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.device_csv_path.setStyleSheet("""
            QLineEdit {
                padding: 3px 6px;
                font-size: 11px;
            }
        """)
        
        browse_csv_btn = QPushButton("📁 Browse...")
        browse_csv_btn.setFixedHeight(24)
        browse_csv_btn.setFixedWidth(75)
        browse_csv_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                border-radius: 2px;
                padding: 1px 6px;
                font-size: 10px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
            QPushButton:pressed {
                background-color: #545b62;
            }
        """)
        browse_csv_btn.clicked.connect(self.browse_device_csv)
        
        csv_file_layout.addWidget(csv_label)
        csv_file_layout.addWidget(self.device_csv_path)
        csv_file_layout.addWidget(browse_csv_btn)
        
        form.addRow(csv_file_layout)
        
        # Remove Test Type - not needed when using pytest scripts
        # self.test_type = QComboBox()
        # self.test_type.addItems(["Connectivity", "BGP", "OSPF", "ISIS", "Interface", "Custom"])
        # self.test_type.setMaximumHeight(28)
        # form.addRow("Test Type:", self.test_type)
        
        left_layout.addWidget(input_group)
        
        # Action button - compact, not full width
        run_test_btn_layout = QHBoxLayout()
        run_test_btn_layout.setContentsMargins(0, 0, 0, 0)
        run_test_btn_layout.addStretch()  # Push button to center/right
        
        run_test_btn = QPushButton("▶ Run Tests")
        run_test_btn.setFixedHeight(24)
        run_test_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        run_test_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                border: none;
                border-radius: 2px;
                padding: 1px 10px;
                font-size: 10px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #0051d5;
            }
            QPushButton:pressed {
                background-color: #0040a8;
            }
        """)
        run_test_btn.clicked.connect(self.run_tests)
        run_test_btn_layout.addWidget(run_test_btn)
        run_test_btn_layout.addStretch()  # Balance on both sides
        
        left_layout.addLayout(run_test_btn_layout)
        
        # Results group
        results_group = QGroupBox("Test Results")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(12, 12, 12, 12)
        results_layout.setSpacing(8)
        
        self.test_results = QTextEdit()
        self.test_results.setReadOnly(True)
        self.test_results.setPlaceholderText("Test results will appear here...")
        results_layout.addWidget(self.test_results)
        
        left_layout.addWidget(results_group)
        
        # Remove splitter - just use single column layout
        layout.addWidget(left_widget)
        
        return widget
    
    def create_test_plan_tab(self):
        """Create test plan generator tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        # Input group - simplified layout (Title/Description in same row, Requirements below)
        input_group = QGroupBox("Test Plan Specification")
        input_main_layout = QVBoxLayout(input_group)
        input_main_layout.setSpacing(8)
        input_main_layout.setContentsMargins(12, 12, 12, 12)
        
        # First row: Title and Description side by side
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        
        title_label = QLabel("Title*:")
        title_label.setMinimumWidth(80)
        self.test_plan_title = QLineEdit()
        self.test_plan_title.setPlaceholderText("e.g., BGP Route Advertisement Test Plan")
        self.test_plan_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.test_plan_title.setStyleSheet("""
            QLineEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
                selection-background-color: #007aff;
                selection-color: #ffffff;
            }
            QLineEdit:focus {
                border: 2px solid #007aff;
                background-color: #ffffff;
            }
            QLineEdit::placeholder {
                color: #86868b;
            }
        """)
        
        title_container = QVBoxLayout()
        title_container.setSpacing(2)
        title_container.setContentsMargins(0, 0, 0, 0)
        title_container.addWidget(title_label)
        title_container.addWidget(self.test_plan_title)
        row1.addLayout(title_container)
        
        desc_label = QLabel("Description:")
        desc_label.setMinimumWidth(80)
        self.test_plan_description = QTextEdit()
        self.test_plan_description.setPlaceholderText("Describe the feature or functionality to be tested...")
        self.test_plan_description.setMaximumHeight(50)
        self.test_plan_description.setMinimumHeight(40)
        self.test_plan_description.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.test_plan_description.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
                selection-background-color: #007aff;
                selection-color: #ffffff;
            }
            QTextEdit:focus {
                border: 2px solid #007aff;
                background-color: #ffffff;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        
        desc_container = QVBoxLayout()
        desc_container.setSpacing(2)
        desc_container.setContentsMargins(0, 0, 0, 0)
        desc_container.addWidget(desc_label)
        desc_container.addWidget(self.test_plan_description)
        row1.addLayout(desc_container)
        
        input_main_layout.addLayout(row1)
        
        # Second row: Requirements (full width)
        req_label = QLabel("Requirements:")
        self.test_plan_requirements = QTextEdit()
        self.test_plan_requirements.setPlaceholderText("Enter requirements (one per line). Use cases and acceptance criteria will be derived automatically.")
        self.test_plan_requirements.setMaximumHeight(50)
        self.test_plan_requirements.setMinimumHeight(40)
        self.test_plan_requirements.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.test_plan_requirements.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
                selection-background-color: #007aff;
                selection-color: #ffffff;
            }
            QTextEdit:focus {
                border: 2px solid #007aff;
                background-color: #ffffff;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        
        req_container = QVBoxLayout()
        req_container.setSpacing(2)
        req_container.setContentsMargins(0, 0, 0, 0)
        req_container.addWidget(req_label)
        req_container.addWidget(self.test_plan_requirements)
        input_main_layout.addLayout(req_container)
        
        # Action buttons with export/save/load - simple flat style like Clear/Close buttons
        button_style = """
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                min-height: 24px;
                max-height: 24px;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
                border: 1px solid #a1a1a6;
            }
            QPushButton:pressed {
                background-color: #d2d2d7;
            }
            QPushButton:disabled {
                background-color: #f5f5f7;
                color: #999999;
                border: 1px solid #d2d2d7;
            }
        """
        
        # Agent mode checkbox and model selection
        agent_mode_layout = QHBoxLayout()
        agent_mode_layout.setSpacing(8)
        self.test_plan_agent_checkbox = QCheckBox("🤖 Agent Mode (Autonomous)")
        self.test_plan_agent_checkbox.setToolTip("Enable agent mode for intelligent, autonomous test plan generation.\nAgent can reason about requirements and use multiple tools.")
        self.test_plan_agent_checkbox.setChecked(False)
        self.test_plan_agent_checkbox.toggled.connect(self._update_agent_model_dropdown_visibility)
        agent_mode_layout.addWidget(self.test_plan_agent_checkbox)
        
        # Model selection dropdown (only visible when agent mode is enabled)
        self.test_plan_agent_model_label = QLabel("Model:")
        self.test_plan_agent_model_label.setVisible(False)  # Hidden by default
        agent_mode_layout.addWidget(self.test_plan_agent_model_label)
        
        self.test_plan_agent_model = QComboBox()
        self.test_plan_agent_model.setEditable(True)  # Allow manual entry
        self.test_plan_agent_model.setMinimumWidth(200)
        self.test_plan_agent_model.setToolTip("Select the AI model to use for agent mode test plan generation")
        self.test_plan_agent_model.setVisible(False)  # Hidden by default
        # Remove padding from dropdown
        self.test_plan_agent_model.setStyleSheet("""
            QComboBox {
                padding: 0px;
                padding-left: 4px;
                padding-right: 4px;
            }
        """)
        self._update_agent_model_dropdown()  # Initialize with available models
        agent_mode_layout.addWidget(self.test_plan_agent_model)
        
        agent_mode_layout.addStretch()
        input_main_layout.addLayout(agent_mode_layout)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        
        self.test_plan_generate_btn = QPushButton("📋 Generate Test Plan")
        self.test_plan_generate_btn.setStyleSheet(button_style)
        self.test_plan_generate_btn.clicked.connect(self.generate_test_plan)
        button_layout.addWidget(self.test_plan_generate_btn)
        
        self.test_plan_pytest_btn = QPushButton("🐍 Generate Pytest")
        self.test_plan_pytest_btn.setStyleSheet(button_style)
        self.test_plan_pytest_btn.clicked.connect(self.generate_pytest_from_plan)
        self.test_plan_pytest_btn.setEnabled(False)
        button_layout.addWidget(self.test_plan_pytest_btn)
        
        button_layout.addStretch()
        
        # Save/Load buttons
        self.test_plan_save_btn = QPushButton("💾 Save")
        self.test_plan_save_btn.setStyleSheet(button_style)
        self.test_plan_save_btn.setEnabled(False)
        self.test_plan_save_btn.clicked.connect(self.save_test_plan)
        button_layout.addWidget(self.test_plan_save_btn)
        
        self.test_plan_load_btn = QPushButton("📂 Load")
        self.test_plan_load_btn.setStyleSheet(button_style)
        self.test_plan_load_btn.clicked.connect(self.load_test_plan)
        button_layout.addWidget(self.test_plan_load_btn)
        
        # Export button with menu
        self.test_plan_export_btn = QPushButton("📄 Export ▼")
        self.test_plan_export_btn.setStyleSheet(button_style)
        self.test_plan_export_btn.setEnabled(False)
        export_menu = QMenu()
        export_menu.addAction("Export as Markdown", self.export_test_plan_markdown)
        export_menu.addAction("Export as JSON", self.export_test_plan_json)
        export_menu.addAction("Export as HTML", self.export_test_plan_html)
        self.test_plan_export_btn.setMenu(export_menu)
        button_layout.addWidget(self.test_plan_export_btn)
        
        input_main_layout.addLayout(button_layout)
        
        # Use splitter to control space distribution
        splitter = QSplitter(Qt.Vertical)
        
        # Input section (top) - set smaller stretch
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.addWidget(input_group)
        splitter.addWidget(input_widget)
        
        # Results group (bottom) - tabbed interface
        results_group = QGroupBox("Generated Test Plan")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(16, 20, 16, 16)
        
        # Create tabbed widget for results
        self.test_plan_tabs = QTabWidget()
        self.test_plan_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                background-color: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-bottom: none;
                padding: 6px 12px;
                margin-right: 2px;
                font-size: 11px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #007aff;
                border-bottom: 2px solid #007aff;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e5e5ea;
                color: #1d1d1f;
            }
            QTabBar::tab:first {
                border-top-left-radius: 4px;
            }
            QTabBar::tab:last {
                border-top-right-radius: 4px;
            }
        """)
        # Set tab bar to use document mode and expand tabs to fit content
        self.test_plan_tabs.setDocumentMode(False)
        self.test_plan_tabs.setUsesScrollButtons(True)
        
        # Get the tab bar and ensure tabs size to content (don't truncate text)
        tab_bar = self.test_plan_tabs.tabBar()
        tab_bar.setExpanding(False)  # Don't expand to fill space, size to content
        tab_bar.setElideMode(Qt.ElideNone)  # Don't elide (truncate) text
        
        # Overview tab
        self.test_plan_overview = QTextEdit()
        self.test_plan_overview.setReadOnly(True)
        self.test_plan_overview.setPlaceholderText("Test plan overview and summary will appear here...")
        self.test_plan_overview.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: none;
                padding: 12px;
                font-size: 13px;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        self.test_plan_tabs.addTab(self.test_plan_overview, "📊 Overview")
        
        # Test Cases tab
        self.test_plan_test_cases = QTextEdit()
        self.test_plan_test_cases.setReadOnly(True)
        self.test_plan_test_cases.setPlaceholderText("Test cases will appear here...")
        self.test_plan_test_cases.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: none;
                padding: 12px;
                font-size: 13px;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        self.test_plan_tabs.addTab(self.test_plan_test_cases, "📝 Test Cases")
        
        # Unit Tests tab
        self.test_plan_unit_tests = QTextEdit()
        self.test_plan_unit_tests.setReadOnly(True)
        self.test_plan_unit_tests.setPlaceholderText("Unit tests will appear here...")
        self.test_plan_unit_tests.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: none;
                padding: 12px;
                font-size: 13px;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        self.test_plan_tabs.addTab(self.test_plan_unit_tests, "🔬 Unit Tests")
        
        # Integration Tests tab
        self.test_plan_integration_tests = QTextEdit()
        self.test_plan_integration_tests.setReadOnly(True)
        self.test_plan_integration_tests.setPlaceholderText("Integration tests will appear here...")
        self.test_plan_integration_tests.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: none;
                padding: 12px;
                font-size: 13px;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        self.test_plan_tabs.addTab(self.test_plan_integration_tests, "🔗 Integration Tests")
        
        # Pytest Script tab with button
        pytest_script_container = QWidget()
        pytest_script_layout = QVBoxLayout(pytest_script_container)
        pytest_script_layout.setContentsMargins(0, 0, 0, 0)
        pytest_script_layout.setSpacing(6)
        
        # Button layout for "Use in Test Framework"
        pytest_button_layout = QHBoxLayout()
        pytest_button_layout.setContentsMargins(0, 0, 0, 0)
        pytest_button_layout.addStretch()
        
        self.use_in_test_framework_btn = QPushButton("▶ Use in Test Framework")
        self.use_in_test_framework_btn.setEnabled(False)  # Disabled until script is generated
        self.use_in_test_framework_btn.setFixedHeight(28)
        self.use_in_test_framework_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 16px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #0051d5;
            }
            QPushButton:pressed {
                background-color: #0040a8;
            }
            QPushButton:disabled {
                background-color: #d2d2d7;
                color: #86868b;
            }
        """)
        self.use_in_test_framework_btn.clicked.connect(self.use_pytest_in_test_framework)
        self.use_in_test_framework_btn.setToolTip("Transfer this pytest script to the Test Framework tab and run it on devices")
        pytest_button_layout.addWidget(self.use_in_test_framework_btn)
        pytest_button_layout.addStretch()
        
        pytest_script_layout.addLayout(pytest_button_layout)
        
        self.test_plan_pytest_script = QPlainTextEdit()
        self.test_plan_pytest_script.setReadOnly(True)
        self.test_plan_pytest_script.setPlaceholderText("Generated pytest script will appear here...")
        # Use monospace font for code
        font = QFont("Courier", 11)
        font.setStyleHint(QFont.Monospace)
        self.test_plan_pytest_script.setFont(font)
        self.test_plan_pytest_script.setStyleSheet("""
            QPlainTextEdit {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: none;
                padding: 12px;
                font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
                font-size: 11px;
            }
            QPlainTextEdit::placeholder {
                color: #86868b;
            }
        """)
        pytest_script_layout.addWidget(self.test_plan_pytest_script)
        
        self.test_plan_tabs.addTab(pytest_script_container, "🐍 Pytest Script")
        
        # Keep old results widget for backward compatibility during transition
        self.test_plan_results = QTextEdit()
        self.test_plan_results.setReadOnly(True)
        self.test_plan_results.setPlaceholderText("Generated test plan will appear here...")
        self.test_plan_results.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #1d1d1f;
                border: none;
                padding: 12px;
                font-size: 13px;
            }
            QTextEdit::placeholder {
                color: #86868b;
            }
        """)
        self.test_plan_tabs.addTab(self.test_plan_results, "📄 Full View")
        
        results_layout.addWidget(self.test_plan_tabs)
        
        results_widget = QWidget()
        results_widget_layout = QVBoxLayout(results_widget)
        results_widget_layout.setContentsMargins(0, 0, 0, 0)
        results_widget_layout.addWidget(results_group)
        splitter.addWidget(results_widget)
        
        # Set splitter sizes: input gets ~20%, results get ~80% (more space for results)
        splitter.setSizes([150, 750])  # Give more space to results section
        splitter.setStretchFactor(0, 0)  # Input section doesn't stretch
        splitter.setStretchFactor(1, 1)  # Results section stretches
        splitter.setCollapsible(0, False)  # Prevent input section from collapsing
        splitter.setCollapsible(1, False)  # Prevent results section from collapsing
        
        layout.addWidget(splitter)
        
        return widget
    
    def create_code_generator_tab(self):
        """Create code generator tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Input group - more compact
        input_group = QGroupBox("Code Generation Input")
        form = QFormLayout(input_group)
        form.setSpacing(12)
        form.setContentsMargins(16, 16, 16, 12)
        
        self.code_prompt = QTextEdit()
        self.code_prompt.setPlaceholderText("Describe what code you want to generate...\nExample: Create a function to parse network configuration files")
        self.code_prompt.setMaximumHeight(80)
        self.code_prompt.setMinimumHeight(60)
        self.code_prompt.setMinimumWidth(900)
        self.code_prompt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        form.addRow("Prompt:", self.code_prompt)
        
        # Language, Type, and Generate Code button in one row
        options_row = QHBoxLayout()
        options_row.setSpacing(12)
        
        # Language - dropdown
        language_label = QLabel("Language:")
        self.code_language = QComboBox()
        self.code_language.addItems(["Python", "Bash", "Go", "YAML", "JSON"])
        # Set minimum width and size adjustment policy to show full text in dropdown
        self.code_language.setMinimumWidth(100)
        self.code_language.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        # Remove padding from combobox
        self.code_language.setStyleSheet("""
            QComboBox {
                padding: 0px;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                background-color: white;
                font-size: 11px;
            }
            QComboBox::drop-down {
                padding: 0px 4px;
            }
            QComboBox QAbstractItemView {
                min-width: 120px;
                padding: 2px;
            }
            QComboBox QAbstractItemView::item {
                padding: 4px 8px;
                min-height: 20px;
            }
        """)
        options_row.addWidget(language_label)
        options_row.addWidget(self.code_language)
        
        # Type
        type_label = QLabel("Type:")
        self.code_type = QComboBox()
        self.code_type.addItems(["Function", "Class", "Script", "Configuration", "Template"])
        # Set minimum width and size adjustment policy to show full text in dropdown
        self.code_type.setMinimumWidth(120)
        self.code_type.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        # Remove padding from combobox
        self.code_type.setStyleSheet("""
            QComboBox {
                padding: 0px;
                border: 1px solid #d2d2d7;
                border-radius: 4px;
                background-color: white;
                font-size: 11px;
            }
            QComboBox::drop-down {
                padding: 0px 4px;
            }
            QComboBox QAbstractItemView {
                min-width: 150px;
                padding: 2px;
            }
            QComboBox QAbstractItemView::item {
                padding: 4px 8px;
                min-height: 20px;
            }
        """)
        options_row.addWidget(type_label)
        options_row.addWidget(self.code_type)
        
        # Black formatting toggle
        self.black_checkbox = QCheckBox("Format Python with Black")
        self.black_checkbox.setChecked(self.format_python_with_black)
        self.black_checkbox.stateChanged.connect(self._toggle_black_preference)
        options_row.addWidget(self.black_checkbox)
        
        # Add stretch to push button to the right
        options_row.addStretch()
        
        # Generate Code button - link-style format
        self.code_generate_btn = QPushButton("💻 Generate Code")
        self.code_generate_btn.setFlat(True)  # Flat button style
        self.code_generate_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                padding: 2px 8px;
                font-size: 11px;
                color: #007aff;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
                border-radius: 4px;
            }
            QPushButton:pressed {
                background-color: #e0e0e0;
            }
            QPushButton:disabled {
                color: #999999;
                background-color: transparent;
            }
        """)
        self.code_generate_btn.clicked.connect(self.generate_code)
        options_row.addWidget(self.code_generate_btn)
        
        # Add the row to form layout
        form.addRow("", options_row)
        
        # Use splitter to control space distribution
        splitter = QSplitter(Qt.Vertical)
        
        # Input section (top) - set smaller stretch
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.addWidget(input_group)
        splitter.addWidget(input_widget)
        
        # Results group (bottom) - will get more space
        results_group = QGroupBox("Generated Code")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(16, 20, 16, 16)
        
        self.code_results = QPlainTextEdit()
        self.code_results.setReadOnly(True)
        self.code_results.setPlaceholderText("Generated code will appear here...")
        self.code_results.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.code_results.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
                font-size: 12px;
                background-color: #f5f5f7;
                border: 1px solid #d7dce3;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        results_layout.addWidget(self.code_results)
        
        results_widget = QWidget()
        results_widget_layout = QVBoxLayout(results_widget)
        results_widget_layout.setContentsMargins(0, 0, 0, 0)
        results_widget_layout.addWidget(results_group)
        splitter.addWidget(results_widget)
        
        # Set splitter sizes: input gets ~30%, results get ~70%
        splitter.setSizes([200, 600])  # Approximate pixel heights
        splitter.setStretchFactor(0, 0)  # Input section doesn't stretch
        splitter.setStretchFactor(1, 1)  # Results section stretches
        
        layout.addWidget(splitter)
        
        return widget
    
    def create_device_testing_tab(self):
        """Create device testing tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        
        # Input group
        input_group = QGroupBox("Device Test Configuration")
        form = QFormLayout(input_group)
        form.setSpacing(16)
        form.setContentsMargins(20, 24, 20, 20)
        
        self.device_test_devices = QLineEdit()
        # Pre-fill with device_id if provided, but user can change it
        if self.context.get('device_id'):
            self.device_test_devices.setText(self.context.get('device_id'))
        self.device_test_devices.setPlaceholderText("device-1,device-2 or leave empty for all devices")
        form.addRow("Device IDs:", self.device_test_devices)
        
        self.device_test_type = QComboBox()
        self.device_test_type.addItems(["juniper", "cisco", "arista", "all"])
        form.addRow("Device Type:", self.device_test_type)
        
        self.device_test_script = QTextEdit()
        self.device_test_script.setPlaceholderText("Pytest script or leave empty to generate from test plan")
        self.device_test_script.setMinimumHeight(200)
        self.device_test_script.setStyleSheet("""
            QTextEdit {
                font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
                font-size: 12px;
            }
        """)
        form.addRow("Pytest Script:", self.device_test_script)
        
        layout.addWidget(input_group)
        
        # Action button
        execute_test_btn = QPushButton("▶ Execute Tests")
        execute_test_btn.setMinimumHeight(44)
        execute_test_btn.clicked.connect(self.execute_device_tests)
        layout.addWidget(execute_test_btn)
        
        # Results group
        results_group = QGroupBox("Test Execution Results")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(20, 24, 20, 20)
        
        self.device_test_results = QTextEdit()
        self.device_test_results.setReadOnly(True)
        self.device_test_results.setPlaceholderText("Test execution results will appear here...")
        results_layout.addWidget(self.device_test_results)
        
        layout.addWidget(results_group)
        
        return widget
    
    # Action methods
    def _prefill_prompt(self, prompt_text):
        """Insert a quick prompt into the chat input."""
        self.chat_input.setText(prompt_text)
        self.chat_input.setFocus()
        self.chat_input.selectAll()
    
    def clear_conversation(self):
        """Clear the conversation history and reset to welcome message"""
        # Stop any running chat worker
        if hasattr(self, '_chat_worker') and self._chat_worker.isRunning():
            self._chat_worker.terminate()
            self._chat_worker.wait()
        
        # Clear conversation state
        self._last_user_message = ""
        self._code_retry_active = False
        self._code_retry_count = 0
        
        # Reset chat display to welcome message
        welcome_html = """
        <div style='padding: 14px 16px; background-color: #f7f9fc; border-radius: 10px; margin-bottom: 12px; border: 1px solid #e1e6ee;'>
            <p style='margin: 0; color: #1f2933; font-size: 14px; font-weight: 700;'>NetGenAI</p>
            <p style='margin: 8px 0 0 0; color: #5a6472; font-size: 13px;'>I can help with troubleshooting, test plans, and automation. Tell me what you need.</p>
        </div>
        """
        self.chat_display.setHtml(welcome_html)
        
        # Clear input field
        self.chat_input.clear()
        
        # Scroll to top
        self.chat_display.verticalScrollBar().setValue(0)
    
    def send_chat_message(self):
        """Send chat message"""
        message = self.chat_input.text().strip()
        if not message:
            return
        
        # Track the last user message so we can retry code generation if needed
        self._last_user_message = message
        # Reset retry flag for each new user message
        self._code_retry_active = False
        self._code_retry_count = 0
        
        # If a previous stream is in progress, stop and finalize it
        try:
            if hasattr(self, '_progress_timer') and self._progress_timer.isActive():
                self._progress_timer.stop()
            if hasattr(self, '_progress_full_text'):
                self._finalize_unified_progressive_response()
        except Exception:
            pass
        
        #region agent log
        try:
            import time, json
            palette_color = ""
            try:
                palette_color = self.chat_display.palette().text().color().name()
            except Exception:
                palette_color = "unavailable"
            with open("/Users/surajsharma/OSTG/.cursor/debug.log", "a") as f:
                f.write(json.dumps({
                    "id": f"log_{int(time.time()*1000)}_chat_entry",
                    "timestamp": int(time.time() * 1000),
                    "location": "ai_unified_dialog.py:send_chat_message",
                    "message": "entry send_chat_message",
                    "data": {
                        "msg_preview": message[:120],
                        "palette_text_color": palette_color
                    },
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A"
                }) + "\n")
        except Exception:
            pass
        #endregion
        
        # Format user message with explicit colors for visibility
        # Use !important to override any QTextEdit default styles
        import html
        escaped_message = html.escape(message)
        user_html = f"""
        <div style='margin: 12px 0; padding: 14px 18px; background-color: #e6f0ff; border-radius: 12px; max-width: 80%; margin-left: auto; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border: 1px solid #b0c7ff; display: block;'>
            <p style='margin: 0; font-weight: 700; font-size: 13px; color: #000000;'>You:</p>
            <p style='margin: 6px 0 0 0; font-size: 14px; line-height: 1.6; color: #000000; font-weight: 500; white-space: pre-wrap; word-break: break-word;'>{escaped_message}</p>
        </div>
        """
        #region agent log
        try:
            import time, json
            with open("/Users/surajsharma/OSTG/.cursor/debug.log", "a") as f:
                f.write(json.dumps({
                    "id": f"log_{int(time.time()*1000)}_chat_user_html",
                    "timestamp": int(time.time() * 1000),
                    "location": "ai_unified_dialog.py:send_chat_message",
                    "message": "user_html constructed",
                    "data": {
                        "user_html_preview": user_html[:200]
                    },
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "B"
                }) + "\n")
        except Exception:
            pass
        #endregion
        # Append user message using insertHtml
        # QTextBrowser respects HTML colors better than QTextEdit
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertBlock()  # ensure separation from previous content
        cursor.insertHtml(user_html)
        self.chat_display.setTextCursor(cursor)
        # Add a spacer after the user bubble to prevent merging with next AI bubble
        spacer_cursor = self.chat_display.textCursor()
        spacer_cursor.movePosition(spacer_cursor.End)
        spacer_cursor.insertHtml("<div style='margin: 6px 0;'></div>")
        self.chat_display.setTextCursor(spacer_cursor)
        #region agent log
        try:
            import time, json
            post_html = self.chat_display.toHtml()
            with open("/Users/surajsharma/OSTG/.cursor/debug.log", "a") as f:
                f.write(json.dumps({
                    "id": f"log_{int(time.time()*1000)}_chat_after_set",
                    "timestamp": int(time.time() * 1000),
                    "location": "ai_unified_dialog.py:send_chat_message",
                    "message": "after setHtml",
                    "data": {
                        "html_contains_user": "You" in post_html,
                        "html_snippet": post_html[-300:]
                    },
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "C"
                }) + "\n")
        except Exception:
            pass
        #endregion
        self.chat_input.clear()
        
        # Show thinking indicator (without AI label) - ensure it's on a new line
        thinking_html = """
        <div style='margin: 12px 0; padding: 12px 16px; background-color: #f5f5f7; border-radius: 12px; max-width: 80%; display: block; clear: both;'>
            <p style='margin: 0; font-size: 13px; color: #6e6e73 !important; font-style: italic;'>Thinking...</p>
        </div>
        """
        # Append thinking message using insertHtml - ensure it's on a new line
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        # Insert a line break before the thinking indicator to ensure it's on a new line
        cursor.insertHtml("<div style='margin: 6px 0;'></div>")
        cursor.insertHtml(thinking_html)
        self.chat_display.setTextCursor(cursor)
        
        # Scroll to bottom
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
        
        # Validate server URL before creating worker
        if not self.server_url:
            # Use a very visible error style - light yellow/cream background with dark red text for maximum visibility
            error_html = """
            <div style='margin: 12px 0; padding: 18px 22px; background-color: #fff3cd; border-radius: 12px; max-width: 85%; border: 3px solid #d32f2f; box-shadow: 0 4px 12px rgba(211, 47, 47, 0.3);'>
                <p style='margin: 0; font-weight: 900; font-size: 18px; color: #d32f2f; letter-spacing: 0.5px;'>⚠️ Configuration Error</p>
                <p style='margin: 12px 0 0 0; font-size: 15px; color: #721c24; line-height: 1.8; font-weight: 700; letter-spacing: 0.2px;'>Server URL is not configured. Please select a server from the server tree.</p>
            </div>
            """
            # Remove thinking message
            current_html = self.chat_display.toHtml()
            if "Thinking..." in current_html:
                import re
                current_html = re.sub(r'<div[^>]*>.*?Thinking\.\.\..*?</div>', '', current_html, flags=re.DOTALL)
            # Append error properly
            if not current_html.strip():
                current_html = "<html><body></body></html>"
            if "</body>" in current_html:
                current_html = current_html.replace("</body>", error_html + "</body>")
            else:
                current_html += error_html
            self.chat_display.setHtml(current_html)
            self.chat_display.verticalScrollBar().setValue(
                self.chat_display.verticalScrollBar().maximum()
            )
            return
        
        # Use background thread to avoid UI freezing
        # Stop any existing worker
        if hasattr(self, '_chat_worker') and self._chat_worker.isRunning():
            self._chat_worker.terminate()
            self._chat_worker.wait()
        
        # Create and start worker thread
        normalize = self.normalize_checkbox.isChecked() if hasattr(self, 'normalize_checkbox') else True
        self._chat_worker = UnifiedAIChatWorker(self.server_url, message, self.context, normalize_response=normalize)
        self._chat_worker.response_received.connect(self._handle_chat_worker_response)
        self._chat_worker.error.connect(self._handle_chat_error)
        self._chat_worker.start()
    
    def _handle_chat_worker_response(self, result):
        """Handle response from chat worker thread"""
        retry_was_active = self._code_retry_active
        try:
            ai_response = result.get("response", "I'm sorry, I couldn't generate a response.")
            model = result.get("model", "unknown")
            source = result.get("source", "unknown")
            
            # Debug: Print source and model values
            logger.debug(f"Source: {source}, Model: {model}")
            logger.info(f"[UNIFIED AI] Response from {source} using model {model}")
            
            # Remove the "Thinking..." message - use a more robust method
            current_html = self.chat_display.toHtml()
            #region agent log
            try:
                import time, json
                with open("/Users/surajsharma/OSTG/.cursor/debug.log", "a") as f:
                    f.write(json.dumps({
                        "id": f"log_{int(time.time()*1000)}_before_thinking_remove",
                        "timestamp": int(time.time() * 1000),
                        "location": "ai_unified_dialog.py:_handle_chat_worker_response",
                        "message": "before thinking removal",
                        "data": {
                            "contains_thinking": "Thinking..." in current_html,
                            "html_snippet": current_html[-300:]
                        },
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "D"
                    }) + "\n")
            except Exception:
                pass
            #endregion
            if "Thinking..." in current_html:
                import re
                # Attempt multiple cleanup patterns (Qt converts div/p to spans)
                cleaned_html = current_html
                # Remove any block containing Thinking...
                cleaned_html = re.sub(r'<div[^>]*>[^<]*Thinking\.\.\.[^<]*</div>', '', cleaned_html, flags=re.DOTALL)
                cleaned_html = re.sub(r'<p[^>]*>[^<]*Thinking\.\.\.[^<]*</p>', '', cleaned_html, flags=re.DOTALL)
                # Remove span-based Thinking... with optional preceding <br> spans
                cleaned_html = re.sub(r'<span[^>]*>Thinking\.\.\.</span>', '', cleaned_html, flags=re.DOTALL)
                cleaned_html = re.sub(r'<span[^>]*> <br /></span>', '', cleaned_html, flags=re.DOTALL)
                cleaned_html = re.sub(r'<span[^>]*><br /></span>', '', cleaned_html, flags=re.DOTALL)
                cleaned_html = re.sub(r'<br ?/?>\s*Thinking\.\.\.', '', cleaned_html, flags=re.IGNORECASE)
                self.chat_display.setHtml(cleaned_html)
                #region agent log
                try:
                    import time, json
                    with open("/Users/surajsharma/OSTG/.cursor/debug.log", "a") as f:
                        f.write(json.dumps({
                            "id": f"log_{int(time.time()*1000)}_after_thinking_remove",
                            "timestamp": int(time.time() * 1000),
                            "location": "ai_unified_dialog.py:_handle_chat_worker_response",
                            "message": "after thinking removal",
                            "data": {
                                "contains_thinking": "Thinking..." in self.chat_display.toHtml(),
                                "html_snippet": self.chat_display.toHtml()[-300:]
                            },
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "E"
                        }) + "\n")
                except Exception:
                    pass
                #endregion
            
            # If this looks like code, render as plain-code block to preserve spacing
            if self._looks_like_code(ai_response):
                # If the user asked for a test plan and there are no fenced code blocks, prefer text rendering
                user_msg = getattr(self, "_last_user_message", "") or ""
                has_fence = "```" in ai_response
                # If there are multiple fenced code blocks, render everything as prose+markdown to preserve all blocks
                if has_fence:
                    import re
                    fence_count = len(re.findall(r"```", ai_response))
                    if fence_count > 2:
                        self._display_unified_response_all_at_once(ai_response, model, source, force_prose=True)
                        return
                if "test plan" in user_msg.lower() and not has_fence:
                    self._display_unified_response_progressively(ai_response, model, source)
                    return
                prose, code_only = self._split_text_and_code(ai_response)
                if prose:
                    # Treat prose as prose only so it doesn't get re-flagged as code
                    self._display_unified_response_all_at_once(prose, model, source, force_prose=True)
                self._display_code_plain_bubble(code_only, original_text=ai_response, has_fence=has_fence, model=model, source=source)
            else:
                # Display response progressively, then finalize formatted output
                self._display_unified_response_progressively(ai_response, model, source)
        except Exception as e:
            logger.error(f"Error handling chat response: {e}", exc_info=True)
            # Remove thinking message
            current_html = self.chat_display.toHtml()
            if "Thinking..." in current_html:
                import re
                current_html = re.sub(r'<div[^>]*>.*?Thinking\.\.\..*?</div>', '', current_html, flags=re.DOTALL)
                self.chat_display.setHtml(current_html)
            
            # Use a very visible error style - light yellow/cream background with dark red text for maximum visibility
            import html
            escaped_error = html.escape(str(e))
            error_html = f"""
            <div style='margin: 12px 0; padding: 18px 22px; background-color: #fff3cd; border-radius: 12px; max-width: 85%; border: 3px solid #d32f2f; box-shadow: 0 4px 12px rgba(211, 47, 47, 0.3);'>
                <p style='margin: 0; font-weight: 900; font-size: 18px; color: #d32f2f; letter-spacing: 0.5px;'>⚠️ AI Error</p>
                <p style='margin: 12px 0 0 0; font-size: 15px; color: #721c24; line-height: 1.8; font-weight: 700; letter-spacing: 0.2px;'>Error: {escaped_error}</p>
            </div>
            """
            # Get current HTML and append error properly
            current_content = self.chat_display.toHtml()
            if not current_content.strip():
                current_content = "<html><body></body></html>"
            if "</body>" in current_content:
                current_content = current_content.replace("</body>", error_html + "</body>")
            else:
                current_content += error_html
            self.chat_display.setHtml(current_content)
        
        # Scroll to bottom
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
        # Clear retry flag once we have handled a response
        if retry_was_active:
            self._code_retry_active = False
    
    def _handle_chat_error(self, error_msg):
        """Handle error from chat worker thread"""
        logger.error(f"Chat error: {error_msg}")
        # Stop any active streaming state
        try:
            if hasattr(self, '_progress_timer') and self._progress_timer.isActive():
                self._progress_timer.stop()
            for attr in ("_progress_base_html", "_progress_full_text", "_progress_index", "_progress_chunk_size"):
                if hasattr(self, attr):
                    delattr(self, attr)
        except Exception:
            pass
        # Remove thinking message
        current_html = self.chat_display.toHtml()
        if "Thinking..." in current_html:
            import re
            current_html = re.sub(r'<div[^>]*>.*?Thinking\.\.\..*?</div>', '', current_html, flags=re.DOTALL)
            self.chat_display.setHtml(current_html)
        
        # Use a very visible error style - light yellow/cream background with dark red text for maximum visibility
        import html
        escaped_error = html.escape(str(error_msg))
        error_html = f"""
        <div style='margin: 12px 0; padding: 18px 22px; background-color: #fff3cd; border-radius: 12px; max-width: 85%; border: 3px solid #d32f2f; box-shadow: 0 4px 12px rgba(211, 47, 47, 0.3);'>
            <p style='margin: 0; font-weight: 900; font-size: 18px; color: #d32f2f; letter-spacing: 0.5px;'>⚠️ AI Error</p>
            <p style='margin: 12px 0 0 0; font-size: 15px; color: #721c24; line-height: 1.8; font-weight: 700; letter-spacing: 0.2px;'>{escaped_error}</p>
        </div>
        """
        # Append error properly using setHtml to ensure styles are applied
        if not current_html.strip():
            current_html = "<html><body></body></html>"
        if "</body>" in current_html:
            current_html = current_html.replace("</body>", error_html + "</body>")
        else:
            current_html += error_html
        self.chat_display.setHtml(current_html)
        # Clear retry flag if this was a retry attempt
        self._code_retry_active = False
        
        # Scroll to bottom
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
    
    def _format_ai_response_html(self, ai_response, force_prose=False):
        """Format AI response text and convert it to HTML safely.
        
        Note: Normalization is handled server-side. The client just renders
        the markdown response received from the server.
        """
        text = ai_response or ""

        # If reply looks like raw code without fences, render as code block
        if not force_prose and self._looks_like_code(text):
            code_only = self._extract_fenced_or_code_block(text)
            return self._format_code_block(code_only, normalize=False, try_format=False)

        # Server-side normalization is already comprehensive, so we just render the markdown
        # No client-side normalization needed to avoid redundancy and potential conflicts
        return self._wrap_ai_body_styles(self._render_markdown(text))

    def _separate_adjacent_bold(self, text):
        """Insert separation between consecutive bold blocks for cleaner headings."""
        import re
        if not text:
            return text
        # Add a line break between back-to-back bold spans like **Title****Subtitle**
        text = re.sub(r'(\*\*[^*]+?\*\*)(\*\*)', r'\1\n\2', text)
        # Ensure a space after bold if immediately followed by alphanumeric text
        text = re.sub(r'(\*\*[^*]+?\*\*)([A-Za-z0-9])', r'\1 \2', text)
        return text

    def _wrap_ai_body_styles(self, html):
        """Wrap AI HTML with modest heading styles to avoid oversized fonts."""
        if not html:
            return ""
        style = """
        <style>
            .ai-body h1 { font-size: 16px; margin: 6px 0 8px 0; }
            .ai-body h2 { font-size: 15px; margin: 6px 0 8px 0; }
            .ai-body h3 { font-size: 14px; margin: 6px 0 6px 0; }
            .ai-body h4, .ai-body h5, .ai-body h6 { font-size: 13px; margin: 6px 0 4px 0; }
            .ai-body p { margin: 6px 0; }
            .ai-body ul, .ai-body ol { margin: 6px 0 6px 18px; }
            .ai-body code { font-size: 12px; }
        </style>
        """
        return f"{style}<div class='ai-body'>{html}</div>"

    def _format_code_fences(self, text):
        """Auto-format fenced code blocks (Python -> black/isort-lite) before markdown render."""
        import re
        if not text or "```" not in text:
            return text

        def _format_block(match):
            lang = (match.group(1) or "").strip().lower()
            code = match.group(2) or ""
            # Only normalize/format for python/blank blocks; otherwise just trim
            if lang in ("python", "py", ""):
                formatted = self._format_code_plain(code, normalize=True, try_format=True, require_format=False)
            else:
                formatted = self._normalize_code_text(code)
            return f"```{lang}\n{formatted}\n```"

        return re.sub(r"```([a-zA-Z0-9_]*)\s*(.*?)```", _format_block, text, flags=re.DOTALL)

    def _render_markdown(self, text):
        """Render markdown with a robust parser, with graceful fallback."""
        import html as html_lib
        # Prefer markdown-it-py if available for better spacing
        try:
            from markdown_it import MarkdownIt  # type: ignore

            md = MarkdownIt("commonmark", {"breaks": True}).enable("table").enable("strikethrough").enable("fence")
            return md.render(text)
        except Exception:
            pass

        # Fallback to python-markdown if installed
        try:
            import markdown

            md = markdown.Markdown(extensions=["fenced_code", "nl2br", "tables", "sane_lists"])
            return md.convert(text)
        except Exception:
            escaped = html_lib.escape(text or "")
            return escaped.replace("\n", "<br>")
    
    def _normalize_code_text(self, code):
        """Lightly reflow single-line AI code outputs into readable blocks."""
        import re
        text = (code or "").replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        rules = [
            (r'(?<!\n)(from\s+\S+\s+import\s+)', r'\n\1'),
            (r'(?<!\n)(?<!from\s)(import\s+\S+)', r'\n\1'),
            (r'(import\s+\S+)(from\s+)', r'\1\n\2'),
            (r'pytestfrom', 'pytest\nfrom '),
            (r'(\w)(from\s+[^\s]+\s+import\s+)', r'\1\n\2'),
            (r'(?<!\n)(@[\w\.]+)', r'\n\1'),
            (r'(?<!\n)(class\s+[^:]+:)', r'\n\1\n'),
            (r'(?<!\n)(def\s+[^:]+:)', r'\n\1\n'),
            (r'(?<!\n)(#\s*)', r'\n\1'),
            (r':\s*(?=[A-Za-z@#])', ':\n'),
            (r'\s+pytest\s*\.\s*fixture\s*def', r'\n@pytest.fixture\ndef'),
            (r'(?<!\n)(with\s+pytest\.raises)', r'\n\1'),
            (r'(?<!\n)(if\s+__name__\s*==\s*["\']__main__["\']\s*:)', r'\n\1'),
            (r'(?<!\n)(pytest\.main)', r'\n\1'),
            (r'(?<!\n)(assert\s+)', r'\n    \1'),
            (r'\s*\.\s*', '.'),
            (r'\s*,\s*', ', '),
            (r'from\s+([A-Za-z0-9_\.]+)\s*\n\s*import\s+([A-Za-z0-9_,\s]+)', r'from \1 import \2'),
            (r'import\s+([A-Za-z0-9_\.]+)\s*\n\s*import\s+([A-Za-z0-9_\.]+)', r'import \1\nimport \2'),
            (r'class\s+([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)\s*:', r'class \1\2:'),
            (r'class\s+([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)\s*(\()', r'class \1\2\3'),
            (r'(?<!\n)(class\s+[^:]+)', r'\n\1'),
            (r'(?<!\n)(elif\s+)', r'\n\1'),
            (r'(?<!\n)(else\s*:)', r'\n\1'),
            (r'\(\s*', '('),
            (r'\s*\)', ')'),
            (r'\b([A-Za-z]+)\s+Not\s+Found\s+Error\b', r'\1NotFoundError'),
            (r'\bValue\s+Error\b', 'ValueError'),
            (r'\bRecursion\s+Error\b', 'RecursionError'),
            (r'\bOstg\s+Topology\b', 'OstgTopology'),
            (r'\bOstg\s+Client\b', 'OstgClient'),
            (r' {2,}', ' '),
            # Collapse Mininet-style add Switch/Host/Link into addSwitch/addHost/addLink
            (r'\badd\s+([A-Z][A-Za-z0-9_]*)', r'add\1'),
        ]
        for pattern, repl in rules:
            text = re.sub(pattern, repl, text)
        # Collapse spaced identifiers like "Ostg Device" -> "OstgDevice" or "Test Network Topology" -> "TestNetworkTopology"
        text = self._collapse_spaced_identifiers(text)
        # Trim spaces inside quoted strings
        text = re.sub(r'"\\s*([^"]*?)\\s*"', r'"\1"', text)
        # Fix dotted numeric/IP tokens and attributes that may still have spaces
        text = re.sub(r'(\d+)\s*\.\s*(\d+)', r'\1.\2', text)
        text = re.sub(r'([A-Za-z0-9_])\s*\.\s*([A-Za-z0-9_])', r'\1.\2', text)
        text = textwrap.dedent(text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = self._apply_pytest_best_practices(text)
        return text.strip("\n")

    def _collapse_spaced_identifiers(self, text):
        """Collapse spaced CamelCase tokens to reduce syntax errors."""
        import re
        if not text:
            return text
        prev = None
        while prev != text:
            prev = text
            text = re.sub(r'\b([A-Z][A-Za-z0-9_]+)\s+([A-Z][A-Za-z0-9_]+)\b', r'\1\2', text)
        return text

    def _apply_pytest_best_practices(self, text):
        """Light tweaks to improve pytest outputs from the AI."""
        if not text or "pytest" not in text:
            return text
        lines = text.splitlines()
        lines = self._ensure_topology_import(lines)
        lines = self._replace_dict_topology(lines)
        lines = self._inject_topology_fixture(lines)
        lines = self._move_len_assert_out_of_device_config_loop(lines)
        lines = self._inject_cfg_lookup_and_fix_asserts(lines)
        lines = self._fix_topology_link_asserts(lines)
        lines = self._normalize_device_name_usage(lines)
        lines = self._fix_empty_topology_assert(lines)
        lines = self._ensure_ostg_exception_import(lines)
        lines = self._normalize_device_dict_keys(lines)
        lines = self._promote_create_topology_fixture(lines)
        lines = self._fix_device_module_topology(lines)
        lines = self._ensure_ostg_topology_import(lines)
        return "\n".join(lines)

    def _ensure_topology_import(self, lines):
        """Ensure Topology/OstgTopology import exists if used."""
        text = "\n".join(lines)
        needs_topology = "Topology(" in text or "OstgTopology(" in text
        has_import = any("import Topology" in l or "OstgTopology" in l for l in lines if l.strip().startswith("from ostg"))
        if not needs_topology or has_import:
            return lines
        new_lines = []
        inserted = False
        for l in lines:
            new_lines.append(l)
            if not inserted and l.startswith("import pytest"):
                new_lines.append("from ostg import Topology")
                inserted = True
        if not inserted:
            new_lines.insert(0, "from ostg import Topology")
        return new_lines

    def _replace_dict_topology(self, lines):
        """Replace dict-based topology stubs with Topology() if import is present."""
        has_topology_import = any("Topology" in l and "import" in l for l in lines)
        if not has_topology_import:
            return lines
        return [l.replace("topology = {}", "topology = Topology()") for l in lines]

    def _inject_topology_fixture(self, lines):
        """Introduce a simple topology fixture when multiple tests build Topology()."""
        topo_assign = [idx for idx, l in enumerate(lines) if "topology = Topology()" in l]
        if len(topo_assign) < 2:
            return lines
        fixture = [
            "@pytest.fixture",
            "def topology():",
            "    return Topology()",
            "",
        ]
        # Insert fixture after imports
        new_lines = []
        inserted = False
        for l in lines:
            new_lines.append(l)
            if not inserted and l.strip() == "" and any("import pytest" in x for x in lines):
                new_lines.extend(fixture)
                inserted = True
        if not inserted:
            new_lines = fixture + new_lines
        # Replace per-test assignments and add param if missing
        cleaned = []
        for l in new_lines:
            if "topology = Topology()" in l:
                continue
            if l.startswith("def test_") and "(" in l and "topology" not in l:
                l = l.replace("):", ", topology):")
            cleaned.append(l)
        return cleaned

    def _fix_topology_link_asserts(self, lines):
        """Adjust link assertions to prefer source/destination IDs instead of device_id duplicates."""
        fixed = []
        for l in lines:
            if "assert link.device_id" in l:
                l = l.replace("link.device_id", "link.source_id")
            if "assert link.device2_id" in l:
                l = l.replace("link.device2_id", "link.destination_id")
            fixed.append(l)
        return fixed

    def _normalize_device_name_usage(self, lines):
        """If tests use r1/r2 for creation but later reference device1/device2, align names."""
        creation_names = set()
        for l in lines:
            if "add_device(" in l and '"' in l:
                import re
                m = re.search(r'add_device\(\s*"([^"]+)"', l)
                if m:
                    creation_names.add(m.group(1))
        needs_fix = {"device1", "device2"} & creation_names
        if not needs_fix and creation_names:
            return lines
        if "r1" in creation_names and "device1" in "\n".join(lines):
            lines = [l.replace("device1", "r1") for l in lines]
        if "r2" in creation_names and "device2" in "\n".join(lines):
            lines = [l.replace("device2", "r2") for l in lines]
        return lines

    def _fix_empty_topology_assert(self, lines):
        """Change len(topo.devices)>0 on new Topology() to ==0 or add a device first."""
        out = []
        for l in lines:
            if "len(topo.devices) > 0" in l:
                out.append(l.replace("> 0", "== 0"))
                continue
            out.append(l)
        return out

    def _ensure_ostg_exception_import(self, lines):
        """Ensure OstgException is imported when referenced."""
        text = "\n".join(lines)
        if "OstgException" not in text:
            return lines
        has_import = any("OstgException" in l and "import" in l for l in lines)
        if has_import:
            return lines
        new_lines = []
        inserted = False
        for l in lines:
            new_lines.append(l)
            if not inserted and l.startswith("import pytest"):
                new_lines.append("from ostg import OstgException")
                inserted = True
        if not inserted:
            new_lines.insert(0, "from ostg import OstgException")
        return new_lines

    def _normalize_device_dict_keys(self, lines):
        """Normalize device dictionaries to use ip_address keys and matching asserts."""
        normalized = []
        for l in lines:
            if "'ip':" in l:
                l = l.replace("'ip':", "'ip_address':")
            if '"ip":' in l:
                l = l.replace('"ip":', '"ip_address":')
            if '["ip"]' in l:
                l = l.replace('["ip"]', '["ip_address"]')
            normalized.append(l)
        return normalized

    def _fix_device_module_topology(self, lines):
        """Replace device.Topology usage with Topology and adjust imports."""
        new_lines = []
        for l in lines:
            new_lines.append(l.replace("device.Topology", "Topology"))
        # adjust imports
        adjusted = []
        for l in new_lines:
            if "from ostg import device" in l:
                adjusted.append("from ostg import Topology")
                continue
            adjusted.append(l)
        return adjusted

    def _ensure_ostg_topology_import(self, lines):
        """Ensure OstgTopology is imported when referenced without instantiation."""
        text = "\n".join(lines)
        if "OstgTopology" not in text:
            return lines
        has_import = any("OstgTopology" in l and "import" in l for l in lines)
        if has_import:
            return lines
        new_lines = []
        inserted = False
        for l in lines:
            new_lines.append(l)
            if not inserted and l.startswith("import pytest"):
                new_lines.append("from ostg import OstgTopology")
                inserted = True
        if not inserted:
            new_lines.insert(0, "from ostg import OstgTopology")
        return new_lines

    def _promote_create_topology_fixture(self, lines):
        """If create_topology() factory exists, add a topology fixture that uses it."""
        text = "\n".join(lines)
        if "def create_topology" not in text:
            return lines
        has_fixture = any("def topology(" in l and "@pytest.fixture" in "\n".join(lines[max(0, idx-2):idx+1]) for idx, l in enumerate(lines))
        if has_fixture:
            return lines
        fixture_block = [
            "@pytest.fixture",
            "def topology():",
            "    return create_topology()",
            "",
        ]
        # Append fixture at end for simplicity
        return lines + [""] + fixture_block

    def _move_len_assert_out_of_device_config_loop(self, lines):
        """If len(topo.devices) assert sits inside the device_config loop, pull it out."""
        out = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if "for device_config in TEST_DEVICE_CONFIGS.values():" in line:
                out.append(line)
                loop_indent = len(line) - len(line.lstrip(" "))
                to_move = None
                block = []
                i += 1
                while i < len(lines):
                    l = lines[i]
                    cur_indent = len(l) - len(l.lstrip(" "))
                    if l.strip() == "":
                        block.append(l)
                        i += 1
                        continue
                    if cur_indent <= loop_indent:
                        break
                    if "assert len(topo.devices)" in l and to_move is None:
                        to_move = l
                        i += 1
                        continue
                    block.append(l)
                    i += 1
                out.extend(block)
                if to_move:
                    out.append(" " * loop_indent + to_move.lstrip())
                continue
            out.append(line)
            i += 1
        return out

    def _inject_cfg_lookup_and_fix_asserts(self, lines):
        """Ensure per-device asserts reference the matching config rather than the last loop value."""
        out = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if "for device in topo.devices" in line and "TEST_DEVICE_CONFIGS" in "\n".join(lines):
                out.append(line)
                indent = len(line) - len(line.lstrip(" "))
                cfg_line = " " * (indent + 4) + 'cfg = TEST_DEVICE_CONFIGS.get(getattr(device, "device", getattr(device, "name", "")))'
                out.append(cfg_line)
                i += 1
                while i < len(lines):
                    l = lines[i]
                    cur_indent = len(l) - len(l.lstrip(" "))
                    if cur_indent <= indent and l.strip():
                        break
                    if "device_config[" in l:
                        l = l.replace("device_config[", "cfg[")
                    out.append(l)
                    i += 1
                continue
            out.append(line)
            i += 1
        return out
    
    def _looks_like_code(self, text):
        """Heuristic to detect code-like responses."""
        if not text:
            return False
        keywords = ("def ", "class ", "import ", "pytest", "assert ", "pytest.", "Ostg", "Topology", "fixture")
        if "```" in text:
            return True
        # Only treat as code if there is a fence or clear code structure
        structural = ("def ", "class ", "import ", "from ", "@pytest", "assert ", "pytest.", "fixture")
        return any(k in text for k in structural)
    
    def _extract_code_segment(self, text):
        """Extract the most code-like trailing segment to feed into a formatter."""
        import re
        lines = (text or "").splitlines()
        code_start = None
        pattern = re.compile(r'^\s*(import|from|class\s|\w+\s*=\s*|\@|def\s|pytest\.|assert|with\s+pytest|if\s+__name__)', re.IGNORECASE)
        for idx, line in enumerate(lines):
            if pattern.search(line):
                code_start = idx
                break
        if code_start is None:
            return text or ""
        return "\n".join(lines[code_start:])
    
    def _extract_fenced_or_code_block(self, text):
        """Prefer fenced code; otherwise return the most code-like trailing segment."""
        import re
        if not text:
            return ""
        fence_match = re.search(r"```(?:[a-zA-Z]+)?\s*(.*?)```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()
        return self._extract_code_segment(text)

    def _split_text_and_code(self, text):
        """Split response into leading prose and fenced code (if any)."""
        import re
        if not text:
            return "", ""
        # Capture prose before and after the first fenced block so we don't drop trailing explanations
        fence_match = re.search(r"(.*?)(```(?:[a-zA-Z]+)?\s*(.*?)```)(.*)", text, re.DOTALL)
        if fence_match:
            prose_before = fence_match.group(1).strip()
            code = fence_match.group(3).strip()
            prose_after = fence_match.group(4).strip()
            prose_parts = [p for p in (prose_before, prose_after) if p]
            prose = "\n\n".join(prose_parts)
            return prose, code
        return "", text.strip()
    
    def _format_code_block(self, code, normalize=False, try_format=False):
        """Render code as a monospace, preserved block."""
        import html as html_lib
        
        snippet = (code or "")
        if normalize:
            snippet = self._normalize_code_text(snippet)
        if try_format and self.format_python_with_black:
            candidate = self._extract_code_segment(snippet)
            formatted = self._format_with_black(candidate)
            if formatted:
                snippet = formatted
        snippet = snippet.strip("\n")
        escaped = html_lib.escape(snippet)
        return f"""
        <pre style='margin: 0; padding: 12px; background: #0b10211a; border: 1px solid #d7dce3; border-radius: 8px; font-family: \"SF Mono\", \"Monaco\", \"Menlo\", \"Consolas\", monospace; font-size: 12px; white-space: pre; word-break: normal; overflow-x: auto;'>
{escaped}
        </pre>
        """
    
    def _format_code_plain(self, code, normalize=False, try_format=False, require_format=False):
        """Return plain text code (optionally normalized/formatted)."""
        snippet = (code or "")
        if normalize:
            snippet = self._normalize_code_text(snippet)
        if try_format and self.format_python_with_black:
            candidate = self._extract_code_segment(snippet)
            formatted = self._format_with_black(candidate)
            if formatted:
                snippet = formatted
            else:
                # Try a simple indent pass then retry black
                indented = self._basic_indent_fix(candidate)
                formatted = self._format_with_black(indented)
                if formatted:
                    snippet = formatted
                elif require_format:
                    snippet = indented
                else:
                    snippet = indented
        return snippet.strip("\n")
    
    def _display_code_plain_bubble(self, code_text, original_text=None, has_fence=False, model="unknown", source="unknown"):
        """Append a plain-text code bubble to the chat display."""
        import html as html_lib
        
        formatted = self._format_code_plain(code_text, normalize=True, try_format=True, require_format=False)
        if not self._validate_python_syntax(formatted):
            # Try an aggressive repair before warning/retry
            repaired = self._aggressive_code_repair(formatted)
            if repaired and self._validate_python_syntax(repaired):
                formatted = repaired
            else:
                # On failure, prefer showing the original response instead of regenerating
                if original_text:
                    try:
                        self._display_unified_response_all_at_once(original_text, model, source, force_prose=True)
                        return
                    except Exception:
                        pass
                # As a final fallback, surface the raw code with a warning
                self._show_code_warning_with_code("Code failed a quick syntax check. Showing raw output for review.", formatted)
                return
        escaped = html_lib.escape(formatted)
        code_html = f"""
        <div style='margin: 12px 0; padding: 12px 16px; background-color: #f5f5f7; border-radius: 12px; max-width: 95%;'>
            <p style='margin: 0 0 6px 0; font-weight: 700; font-size: 13px; color: #1d1d1f;'>AI (code):</p>
            <pre style='margin: 0; padding: 10px; background: #0b10211a; border: 1px solid #d7dce3; border-radius: 8px; font-family: "SF Mono", "Monaco", "Menlo", "Consolas", monospace; font-size: 12px; white-space: pre; word-break: normal; overflow-x: auto;'>{escaped}</pre>
        </div>
        """
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertBlock()
        cursor.insertHtml(code_html)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

    def _show_code_error(self, message):
        """Display a visible error bubble in the chat display."""
        import html as html_lib
        msg = html_lib.escape(message)
        err_html = f"""
        <div style='margin: 12px 0; padding: 12px 16px; background-color: #b71c1c; border-radius: 12px; max-width: 85%; border: 3px solid #8b0000;'>
            <p style='margin: 0; font-weight: 800; font-size: 13px; color: #ffffff;'>⚠️ Code Error</p>
            <p style='margin: 6px 0 0 0; font-size: 12px; color: #ffffff; line-height: 1.5;'>{msg}</p>
        </div>
        """
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertBlock()
        cursor.insertHtml(err_html)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
    
    def _show_code_warning_with_code(self, message, code_text):
        """Display warning plus raw code in a bubble."""
        import html as html_lib
        msg = html_lib.escape(message or "")
        escaped = html_lib.escape(code_text or "")
        warn_html = f"""
        <div style='margin: 12px 0; padding: 12px 16px; background-color: #fff7e6; border-radius: 12px; max-width: 95%; border: 2px solid #ffb74d;'>
            <p style='margin: 0 0 6px 0; font-weight: 800; font-size: 13px; color: #9c5800;'>⚠️ Code Warning</p>
            <p style='margin: 0 0 8px 0; font-size: 12px; color: #9c5800; line-height: 1.4;'>{msg}</p>
            <pre style='margin: 0; padding: 10px; background: #0b10211a; border: 1px solid #d7dce3; border-radius: 8px; font-family: "SF Mono", "Monaco", "Menlo", "Consolas", monospace; font-size: 12px; white-space: pre; word-break: normal; overflow-x: auto;'>{escaped}</pre>
        </div>
        """
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertBlock()
        cursor.insertHtml(warn_html)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

    def _start_chat_retry(self, reason=None):
        """Retry chat with stricter code-only instructions after a syntax failure."""
        import html as html_lib
        # Allow up to 2 retries to avoid loops
        if self._code_retry_active or self._code_retry_count >= 2:
            return
        if not self._last_user_message:
            return
        if not self.server_url:
            self._show_code_error("Cannot retry code generation: server URL is not configured.")
            return
        
        self._code_retry_active = True
        self._code_retry_count += 1
        notice = reason or "Regenerating code with stricter syntax enforcement..."
        info_html = f"""
        <div style='margin: 10px 0; padding: 10px 14px; background-color: #e6f0ff; border-radius: 10px; max-width: 85%; border: 1px solid #b0c7ff;'>
            <p style='margin: 0; font-size: 12px; color: #1d1d1f; font-weight: 600;'>🔁 {html_lib.escape(notice)}</p>
        </div>
        """
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertBlock()
        cursor.insertHtml(info_html)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
        
        # Build a stricter prompt to force code-only, syntactically valid output
        if self._code_retry_count == 1:
            strict_prompt = (
                f"{self._last_user_message}\n\n"
                "Return valid Python code only, inside a single ```python``` fenced block. "
                "Fix any syntax issues and avoid any prose or commentary."
            )
        else:
            strict_prompt = (
                f"{self._last_user_message}\n\n"
                "Return valid Python code only, inside a single ```python``` fenced block. "
                "Do not include classes unless needed. Use top-level pytest functions and fixtures. "
                "Ensure the code passes ast.parse without errors. No prose."
            )
        
        # Stop any existing retry worker
        try:
            if hasattr(self, "_chat_retry_worker") and self._chat_retry_worker.isRunning():
                self._chat_retry_worker.terminate()
                self._chat_retry_worker.wait()
        except Exception:
            pass
        
        normalize = self.normalize_checkbox.isChecked() if hasattr(self, 'normalize_checkbox') else True
        self._chat_retry_worker = UnifiedAIChatWorker(self.server_url, strict_prompt, self.context, normalize_response=normalize)
        self._chat_retry_worker.response_received.connect(self._handle_chat_worker_response)
        self._chat_retry_worker.error.connect(self._handle_chat_error)
        self._chat_retry_worker.start()

    def _validate_python_syntax(self, code_text):
        """Quick syntax check using ast.parse; returns True/False."""
        try:
            ast.parse(code_text or "")
            return True
        except SyntaxError as e:
            logger.debug(f"Syntax validation failed: {e}")
            return False

    def _format_with_black(self, code_text):
        """Attempt to format Python code with black."""
        try:
            import black
            return black.format_str(code_text, mode=black.FileMode())
        except Exception as e:
            logger.debug(f"Black in-process formatting skipped or failed: {e}")
        
        # Fallback: try calling black via subprocess with a temp file
        try:
            with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=True) as tmp:
                tmp.write(code_text)
                tmp.flush()
                result = subprocess.run(
                    ["python3", "-m", "black", "-q", tmp.name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                )
                if result.returncode == 0:
                    tmp.seek(0)
                    return tmp.read()
                else:
                    logger.debug(f"Black subprocess failed: {result.stderr.decode(errors='ignore')}")
        except Exception as e:
            logger.debug(f"Black subprocess formatting failed: {e}")
        
        return None

    def _basic_indent_fix(self, code_text):
        """Very simple indentation pass to help Black succeed on smashed code."""
        lines = (code_text or "").splitlines()
        indent = 0
        output = []
        for raw in lines:
            line = raw.strip()
            if not line:
                output.append("")
                continue
            # Reset indent for top-level statements
            if line.startswith(("import ", "from ", "def ", "class ", "if __name__")):
                indent = 0
            # Dedent for elif/else/except/finally
            if line.startswith(("elif ", "else:", "except", "finally:")) and indent > 0:
                indent -= 1
            padded = (" " * (indent * 4)) + line
            output.append(padded)
            if line.endswith(":"):
                indent += 1
        return "\n".join(output)

    def _aggressive_code_repair(self, code_text):
        """Attempt to repair common syntax issues from smashed AI output."""
        import re
        text = code_text or ""
        lines = []
        for raw in text.splitlines():
            stripped = raw.lstrip()
            # Comment out shell/CLI invocations that break syntax
            if re.match(r'^(pytest\\b|python\\b|python3\\b|!pytest)', stripped):
                lines.append("# " + stripped)
                continue
            # Dedent decorators to top-level
            if stripped.startswith("@"):
                lines.append(stripped)
                continue
            lines.append(raw)
        text = "\n".join(lines)
        # Collapse spaced identifiers again
        text = self._collapse_spaced_identifiers(text)
        # Merge broken string literals split by newlines (e.g., "username:\\n    admin")
        text = re.sub(r'"([^"\\n]+):\\s*\\n\\s*([^"\\n]+)"', r'\"\\1: \\2\"', text)
        # Normalize decorator spacing
        text = re.sub(r'^\\s*@\\s*pytest\\.', '@pytest.', text, flags=re.MULTILINE)
        # Basic indent reconstruction
        text = self._basic_indent_fix(text)
        text = re.sub(r'\\n{3,}', '\\n\\n', text)
        text = self._repair_unbalanced_triple_quotes(text)
        # Final attempt with black if available
        if self.format_python_with_black:
            try:
                formatted = self._format_with_black(text)
                if formatted:
                    text = formatted
            except Exception:
                pass
        return text.strip("\\n")

    def _repair_unbalanced_triple_quotes(self, text):
        """Balance unmatched triple quotes by appending closures."""
        if not text:
            return text
        double_count = text.count('"""')
        single_count = text.count("'''")
        if double_count % 2 != 0:
            text = text.rstrip() + '\n"""'
        if single_count % 2 != 0:
            text = text.rstrip() + "\n'''"
        return text
    
    def _display_unified_response_progressively(self, ai_response, model, source):
        """Display Unified AI response progressively with a final formatted pass."""
        try:
            # Stop any existing stream
            if hasattr(self, '_progress_timer') and getattr(self, '_progress_timer').isActive():
                self._progress_timer.stop()
        except Exception:
            pass
        
        self._progress_base_html = self.chat_display.toHtml() or "<html><body></body></html>"
        self._progress_full_text = ai_response or ""
        self._progress_index = 0
        self._progress_chunk_size = 30
        
        if not hasattr(self, '_progress_timer'):
            self._progress_timer = QTimer(self)
            self._progress_timer.timeout.connect(self._append_unified_progressive_chunk)
        
        # Kick off first chunk immediately
        self._append_unified_progressive_chunk()
        if len(self._progress_full_text) > self._progress_chunk_size:
            self._progress_timer.start(18)

    def _append_html_to_body(self, base_html, addition_html):
        """Safely append HTML before </body> if present."""
        base = base_html or "<html><body></body></html>"
        spacer = "<div style='height: 10px;'></div>"
        addition = spacer + (addition_html or "")
        if "</body>" in base:
            return base.replace("</body>", addition + "</body>")
        return base + addition

    def _escape_partial_text(self, text):
        """Escape partial text for streaming display."""
        import html as html_lib
        return html_lib.escape(text or "").replace("\n", "<br>")

    def _append_unified_progressive_chunk(self):
        """Append the next chunk of the AI response."""
        try:
            if not hasattr(self, '_progress_full_text'):
                return
            
            text = self._progress_full_text
            if not text:
                self._finalize_unified_progressive_response()
                return
            
            self._progress_index = min(len(text), self._progress_index + self._progress_chunk_size)
            visible = text[:self._progress_index]
            content_html = self._escape_partial_text(visible)
            if not content_html.strip():
                content_html = "<span style='color: #9aa6b8;'>...</span>"

            bubble_html = self._build_progress_bubble(content_html)
            full_html = self._append_html_to_body(self._progress_base_html, bubble_html)
            self.chat_display.setHtml(full_html)
            self.chat_display.verticalScrollBar().setValue(
                self.chat_display.verticalScrollBar().maximum()
            )
            
            if self._progress_index >= len(text):
                if hasattr(self, '_progress_timer'):
                    self._progress_timer.stop()
                self._finalize_unified_progressive_response()
        except Exception as e:
            logger.error(f"Error in progressive chunk display: {e}", exc_info=True)
            if hasattr(self, '_progress_timer'):
                self._progress_timer.stop()
            self._finalize_unified_progressive_response(fallback=True)

    def _build_progress_bubble(self, content_html):
        """Build streaming bubble markup."""
        return f"""
        <div style='margin: 12px 0; padding: 12px 16px; background-color: #f5f5f7; border-radius: 12px; max-width: 80%;'>
            <div style='height: 4px;'></div>
            <p style='margin: 0 0 4px 0; font-weight: 700; font-size: 13px; color: #1f2933;'>AI (typing...)</p>
            <div style='margin: 4px 0 0 0; font-size: 13px; color: #1f2933; line-height: 1.6; white-space: pre-wrap; word-break: break-word;'>{content_html}</div>
        </div>
        """

    def _finalize_unified_progressive_response(self, fallback=False):
        """Replace streaming bubble with the fully formatted response."""
        try:
            final_text = getattr(self, '_progress_full_text', "")
            formatted_response = self._format_ai_response_html(final_text)
            ai_html = f"""
            <div style='margin: 6px 0;'></div>
            <div style='margin: 12px 0; padding: 12px 16px; background-color: #f5f5f7; border-radius: 12px; max-width: 80%;'>
                <div style='height: 4px;'></div>
                <p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>AI:</p>
                <div style='margin: 4px 0 0 0; font-size: 13px; color: #1d1d1f; line-height: 1.6; white-space: pre-wrap; word-break: break-word;'>{formatted_response}</div>
            </div>
            <div style='margin: 8px 0;'></div>
            """
            # Use base_html (from before progressive display started) and append final response
            # This ensures we replace the progressive bubble instead of duplicating
            base_html = getattr(self, '_progress_base_html', self.chat_display.toHtml())
            # Remove any progressive bubble that might have been added to base_html
            import re
            # Remove progressive bubble pattern - look for div containing "AI (typing...)"
            cleaned_base = re.sub(
                r'<div[^>]*>.*?AI\s*\(typing\.\.\.\).*?</div>',
                '',
                base_html,
                flags=re.DOTALL | re.IGNORECASE
            )
            # Append final response to cleaned base HTML
            full_html = self._append_html_to_body(cleaned_base, ai_html)
            self.chat_display.setHtml(full_html)
            self.chat_display.verticalScrollBar().setValue(
                self.chat_display.verticalScrollBar().maximum()
            )
        except Exception as e:
            logger.error(f"Error finalizing progressive response: {e}", exc_info=True)
        finally:
            for attr in ("_progress_base_html", "_progress_full_text", "_progress_index", "_progress_chunk_size"):
                if hasattr(self, attr):
                    delattr(self, attr)

    def _display_unified_response_all_at_once(self, ai_response, model, source, force_prose=False):
        """Display Unified AI response all at once for stable formatting"""
        try:
            formatted_response = self._format_ai_response_html(ai_response, force_prose=force_prose)
            ai_html = f"""
            <div style='margin: 6px 0;'></div>
            <div style='margin: 12px 0; padding: 12px 16px; background-color: #f5f5f7; border-radius: 12px; max-width: 80%;'>
                <div style='height: 4px;'></div>
                <p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>AI:</p>
                <div style='margin: 4px 0 0 0; font-size: 13px; color: #1d1d1f; line-height: 1.6; white-space: pre-wrap; word-break: break-word;'>{formatted_response}</div>
            </div>
            <div style='margin: 8px 0;'></div>
            """
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.End)
            # Force a new block so the AI header never merges with prior text
            cursor.insertBlock()
            cursor.insertHtml(ai_html)
            cursor.insertBlock()
            self.chat_display.setTextCursor(cursor)
            
            # Scroll to bottom so the newest message is visible
            self.chat_display.verticalScrollBar().setValue(
                self.chat_display.verticalScrollBar().maximum()
            )
        except Exception as e:
            logger.error(f"Error displaying response: {e}", exc_info=True)

    def start_troubleshooting(self):
        """Start troubleshooting"""
        device_id = self.troubleshoot_device.text().strip()
        symptoms_text = self.troubleshoot_symptoms.toPlainText().strip()
        
        if not device_id:
            QMessageBox.warning(self, "Missing Device", "Please enter a device ID")
            return
        
        if not symptoms_text:
            QMessageBox.warning(self, "No Symptoms", "Please describe symptoms")
            return
        
        # Extract symptoms
        symptoms = {"description": symptoms_text}
        if "interface down" in symptoms_text.lower():
            symptoms["interface_down"] = True
        if "link down" in symptoms_text.lower():
            symptoms["link_down"] = True
        if "bgp" in symptoms_text.lower() and "not" in symptoms_text.lower():
            symptoms["bgp_not_established"] = True
        
        self.troubleshoot_results.clear()
        self.troubleshoot_results.append("🔍 Diagnosing...")
        
        try:
            response = requests.post(
                f"{self.server_url}/api/ai/troubleshoot",
                json={"device_id": device_id, "symptoms": symptoms},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                self.display_troubleshooting_results(result)
            else:
                self.troubleshoot_results.append(f"❌ Error: {response.status_code}")
        except Exception as e:
            self.troubleshoot_results.append(f"❌ Error: {str(e)}")
    
    def display_troubleshooting_results(self, result):
        """Display troubleshooting results"""
        self.troubleshoot_results.clear()
        
        html_output = []
        html_output.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
        
        # Header
        html_output.append("<div style='background-color: #007aff; color: white; padding: 16px; border-radius: 8px; margin-bottom: 16px;'>")
        html_output.append("<h2 style='margin: 0; font-size: 18px; font-weight: 600;'>🔍 Diagnosis Results</h2>")
        html_output.append("</div>")
        
        # Root Cause
        root_cause = result.get('root_cause', 'Unknown')
        confidence = result.get('confidence', 0) * 100
        
        html_output.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
        html_output.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>Root Cause</p>")
        html_output.append(f"<p style='margin: 0; font-size: 13px; color: #1d1d1f; line-height: 1.6;'>{root_cause}</p>")
        html_output.append(f"<p style='margin: 8px 0 0 0; font-size: 12px; color: #6e6e73;'>Confidence: <strong>{confidence:.1f}%</strong></p>")
        html_output.append("</div>")
        
        # Solutions
        solutions = result.get('solutions', [])
        if solutions:
            html_output.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px;'>")
            html_output.append("<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>💡 Recommended Solutions</p>")
            html_output.append("<ol style='margin: 0; padding-left: 20px;'>")
            for solution in solutions:
                html_output.append(f"<li style='margin: 8px 0; font-size: 13px; color: #1d1d1f; line-height: 1.6;'>{solution}</li>")
            html_output.append("</ol>")
            html_output.append("</div>")
        
        html_output.append("</div>")
        
        self.troubleshoot_results.setHtml("".join(html_output))
        self.troubleshoot_results.verticalScrollBar().setValue(0)
    
    # Removed converter methods - users provide their own pytest scripts
    # AI pytest generation is done via Test Plan tab, then transferred via "Use in Test Framework" button
    
    def browse_pytest_file(self):
        """Generate pytest script directly in Test Framework tab using AI"""
        # Get inputs from AI section
        title = self.test_framework_title.text().strip()
        description = self.test_framework_description.toPlainText().strip()
        requirements_text = self.test_framework_requirements.toPlainText().strip()
        
        # Validate inputs
        if not title:
            QMessageBox.warning(self, "Missing Title", "Please enter a test title.")
            return
        
        if not description and not requirements_text:
            QMessageBox.warning(self, "Missing Requirements", "Please enter a description or requirements.")
            return
        
        # Parse requirements (one per line)
        requirements = [r.strip() for r in requirements_text.split('\n') if r.strip()] if requirements_text else []
        
        # Build functional spec
        functional_spec = {
            "title": title,
            "description": description if description else f"Test for {title}"
        }
        if requirements:
            functional_spec["requirements"] = requirements
        
        # Check agent mode
        agent_mode = self.test_framework_agent_checkbox.isChecked() if hasattr(self, 'test_framework_agent_checkbox') else False
        
        # Get AI mode preference
        import os
        ai_mode_preference = "hybrid"
        try:
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    client_settings = json.load(f)
                    ai_mode_preference = client_settings.get("preferred_ai_mode", "hybrid")
        except Exception as e:
            logger.debug(f"Could not read AI mode preference: {e}")
        
        # Get agent model if agent mode
        agent_model = None
        if agent_mode:
            try:
                settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                if os.path.exists(settings_file):
                    with open(settings_file, 'r') as f:
                        client_settings = json.load(f)
                        agent_model = client_settings.get("cloud_model", "").strip()
            except Exception:
                pass
        
        # Validate server URL
        if not self.server_url:
            QMessageBox.warning(self, "No Server", "Server URL is not configured. Please select a server from the server tree.")
            return
        
        # Sync API key to server if we have one (like in test plan generation)
        import os
        api_key = None
        api_base = None
        try:
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    client_settings = json.load(f)
                    api_key = client_settings.get("openai_api_key", "").strip()
                    api_base = client_settings.get("openai_api_base", "").strip()
        except Exception as e:
            logger.debug(f"Could not read API key from settings: {e}")
        
        # Sync API key to server if we have one and server URL is available
        if api_key and self.server_url:
            try:
                # Check if server has the API key
                check_response = requests.get(
                    f"{self.server_url}/api/ai/settings",
                    timeout=5
                )
                if check_response.status_code == 200:
                    server_settings = check_response.json()
                    if not server_settings.get("has_api_key", False):
                        # Server doesn't have API key, send it
                        logger.info("[TEST FRAMEWORK] Syncing API key to server...")
                        sync_response = requests.post(
                            f"{self.server_url}/api/ai/settings",
                            json={
                                "openai_api_key": api_key,
                                "openai_api_base": api_base if api_base else None
                            },
                            timeout=5
                        )
                        if sync_response.status_code == 200:
                            logger.info("[TEST FRAMEWORK] API key synced to server successfully")
                        else:
                            logger.warning(f"[TEST FRAMEWORK] Failed to sync API key to server: {sync_response.status_code}")
            except Exception as e:
                logger.warning(f"[TEST FRAMEWORK] Could not sync API key to server: {e}")
        
        # Disable button during generation
        self.test_framework_generate_btn.setEnabled(False)
        
        # Clean up any existing worker
        if hasattr(self, 'test_framework_pytest_worker') and self.test_framework_pytest_worker:
            try:
                # Disconnect signals to prevent accumulation
                try:
                    self.test_framework_pytest_worker.pytest_received.disconnect()
                    self.test_framework_pytest_worker.error_received.disconnect()
                    self.test_framework_pytest_worker.finished.disconnect()
                except Exception:
                    pass  # Signals may not be connected
                
                if self.test_framework_pytest_worker.isRunning():
                    self.test_framework_pytest_worker.terminate()
                    self.test_framework_pytest_worker.wait(1000)  # Wait up to 1 second
                
                # Clean up worker reference
                self.test_framework_pytest_worker = None
            except Exception as e:
                logger.warning(f"[TEST FRAMEWORK] Error cleaning up previous worker: {e}")
        
        # Show status in test results
        status_html = """
        <div style='background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 12px; border-radius: 4px;'>
            <p style='margin: 0; font-size: 13px; color: #856404;'>
                <strong>🤖 Generating pytest script with AI...</strong><br>
                Mode: {mode}<br>
                This may take 10-30 seconds. Please wait...
            </p>
        </div>
        """.format(mode="🤖 Agent Mode" if agent_mode else "Standard Mode")
        self.test_results.setHtml(status_html)
        
        # Create worker
        self.test_framework_pytest_worker = PytestGenerationWorker(
            self.server_url,
            functional_spec=functional_spec,
            agent_mode=agent_mode,
            ai_mode_preference=ai_mode_preference,
            agent_model=agent_model
        )
        self.test_framework_pytest_worker.pytest_received.connect(self.on_test_framework_pytest_received)
        self.test_framework_pytest_worker.error_received.connect(self.on_test_framework_pytest_error)
        self.test_framework_pytest_worker.finished.connect(self.on_test_framework_pytest_finished)
        self.test_framework_pytest_worker.start()
    
    def on_test_framework_pytest_received(self, pytest_script):
        """Handle pytest script received for Test Framework"""
        import tempfile
        import os
        
        # Save to temp file
        try:
            title = self.test_framework_title.text().strip() or "test"
            safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
            safe_title = safe_title.replace(' ', '_')[:30]
            
            temp_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                prefix=f'test_framework_{safe_title}_',
                dir=tempfile.gettempdir()
            )
            temp_file.write(pytest_script)
            temp_file.close()
            
            # Auto-populate file path
            if hasattr(self, 'pytest_file_path'):
                self.pytest_file_path.setText(temp_file.name)
                # Clear "from Test Plan" indicator since this is generated in Test Framework
                if hasattr(self, '_pytest_from_test_plan'):
                    self._pytest_from_test_plan = False
            
            # Show success message (escape HTML in pytest script preview)
            script_preview = html.escape(pytest_script[:500])
            success_html = f"""
            <div style='background-color: #d4edda; border-left: 4px solid #28a745; padding: 12px; border-radius: 4px; margin-bottom: 12px;'>
                <p style='margin: 0; font-size: 13px; color: #155724;'>
                    <strong>✅ Pytest script generated successfully!</strong><br>
                    File: {html.escape(os.path.basename(temp_file.name))}<br>
                    Path: {html.escape(temp_file.name)}
                </p>
            </div>
            <div style='background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; padding: 12px;'>
                <p style='margin: 0 0 8px 0; font-weight: 600; font-size: 12px;'>Generated Pytest Script Preview:</p>
                <pre style='margin: 0; font-size: 10px; white-space: pre-wrap; font-family: monospace;'>{script_preview}{'...' if len(pytest_script) > 500 else ''}</pre>
            </div>
            """
            self.test_results.setHtml(success_html)
            
            logger.info(f"[TEST FRAMEWORK] Pytest script generated and saved to: {temp_file.name}")
        except Exception as e:
            logger.error(f"[TEST FRAMEWORK] Failed to save pytest script: {e}")
            QMessageBox.warning(self, "Save Error", f"Failed to save pytest script: {str(e)}")
    
    def on_test_framework_pytest_error(self, error_message):
        """Handle pytest generation error for Test Framework"""
        escaped_error = html.escape(str(error_message))
        error_html = f"""
        <div style='background-color: #f8d7da; border-left: 4px solid #dc3545; padding: 12px; border-radius: 4px;'>
            <p style='margin: 0; font-size: 13px; color: #721c24;'>
                <strong>❌ Error generating pytest script:</strong><br>
                {escaped_error}
            </p>
        </div>
        """
        self.test_results.setHtml(error_html)
        # Re-enable button on error
        if hasattr(self, 'test_framework_generate_btn'):
            self.test_framework_generate_btn.setEnabled(True)
    
    def on_test_framework_pytest_finished(self):
        """Re-enable button after generation"""
        if hasattr(self, 'test_framework_generate_btn'):
            self.test_framework_generate_btn.setEnabled(True)
        
        # Clean up worker reference after completion
        if hasattr(self, 'test_framework_pytest_worker') and self.test_framework_pytest_worker:
            try:
                # Disconnect signals
                try:
                    self.test_framework_pytest_worker.pytest_received.disconnect()
                    self.test_framework_pytest_worker.error_received.disconnect()
                    self.test_framework_pytest_worker.finished.disconnect()
                except Exception:
                    pass  # Signals may already be disconnected
            except Exception as e:
                logger.debug(f"[TEST FRAMEWORK] Error cleaning up worker signals: {e}")
            # Note: Don't set to None here as it might still be referenced elsewhere
    
    def browse_pytest_file(self):
        """Open file dialog to select pytest script"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Pytest Script",
            "",
            "Python Files (*.py);;All Files (*)"
        )
        
        if file_path:
            self.pytest_file_path.setText(file_path)
            # Clear indicator when user manually selects a file (not from Test Plan)
            if hasattr(self, '_pytest_from_test_plan') and self._pytest_from_test_plan:
                self._pytest_from_test_plan = False
                # Clear the indicator if it exists
                if hasattr(self, 'test_results'):
                    current_html = self.test_results.toHtml()
                    if "Using pytest script from Test Plan" in current_html:
                        # Clear only the indicator
                        self.test_results.clear()
    
    def browse_device_csv(self):
        """Open file dialog to select device CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Device CSV File",
            "",
            "CSV Files (*.csv);;All Files (*)"
        )
        
        if file_path:
            self.device_csv_path.setText(file_path)
    
    def parse_device_csv(self, csv_file):
        """Parse CSV file containing device information"""
        import csv
        
        devices = []
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Expected columns: device_name (or device_id, name), user (or username), password
                    device_name = row.get('device_name') or row.get('device_id') or row.get('name') or row.get('device')
                    username = row.get('user') or row.get('username') or row.get('user_name')
                    password = row.get('password') or row.get('pass')
                    
                    if device_name:
                        devices.append({
                            'device_id': device_name,
                            'username': username or '',
                            'password': password or ''
                        })
        except Exception as e:
            raise Exception(f"Failed to parse CSV file: {str(e)}")
        
        return devices
    
    def run_tests(self):
        """Run pytest script on device(s)"""
        device_id = self.test_device.text().strip()
        pytest_file = self.pytest_file_path.text().strip()
        csv_file = self.device_csv_path.text().strip()
        
        if not pytest_file:
            QMessageBox.warning(self, "Missing Script", "Please select a pytest script file")
            return
        
        # Determine device IDs and credentials
        device_ids = []
        device_credentials = {}
        
        if csv_file:
            # Parse CSV file for device information
            try:
                devices = self.parse_device_csv(csv_file)
                if not devices:
                    QMessageBox.warning(self, "Empty CSV", "CSV file does not contain any devices")
                    return
                
                for device in devices:
                    device_id_from_csv = device['device_id']
                    device_ids.append(device_id_from_csv)
                    if device['username'] or device['password']:
                        device_credentials[device_id_from_csv] = {
                            'username': device['username'],
                            'password': device['password']
                        }
            except Exception as e:
                QMessageBox.critical(self, "CSV Error", str(e))
                return
        elif device_id:
            # Use single device ID from input field
            device_ids = [device_id]
        else:
            QMessageBox.warning(self, "Missing Device", "Please enter a device ID or provide a CSV file with device information")
            return
        
        # Check if file exists
        import os
        if not os.path.exists(pytest_file):
            QMessageBox.critical(self, "File Not Found", f"Pytest script file not found:\n{pytest_file}")
            return
        
        # Read pytest script from file
        try:
            with open(pytest_file, 'r', encoding='utf-8') as f:
                pytest_script = f.read()
        except Exception as e:
            QMessageBox.critical(self, "File Error", f"Failed to read pytest script:\n{str(e)}")
            return
        
        if not pytest_script.strip():
            QMessageBox.warning(self, "Empty Script", "The selected file is empty")
            return
        
        # Clear previous results and indicator flag
        self.test_results.clear()
        if hasattr(self, '_pytest_from_test_plan'):
            self._pytest_from_test_plan = False
        
        self.test_results.append(f"🧪 Running pytest script on {len(device_ids)} device(s)...")
        self.test_results.append(f"📄 Script: {pytest_file}")
        if csv_file:
            self.test_results.append(f"📋 Devices from CSV: {', '.join(device_ids)}")
        self.test_results.append("⏳ Please wait...\n")
        
        try:
            # Prepare test config with device credentials if available
            test_config = {}
            if device_credentials:
                test_config['device_credentials'] = device_credentials
            
            response = requests.post(
                f"{self.server_url}/api/ai/pytest/execute-devices",
                json={
                    "pytest_script": pytest_script,
                    "device_ids": device_ids,
                    "test_config": test_config
                },
                timeout=300  # Longer timeout for test execution
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Handle pytest execution results
                html_output = []
                html_output.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
                
                # Check if there's an error in the result
                if result.get('error'):
                    html_output.append("<div style='background-color: #ff3b30; color: white; padding: 16px; border-radius: 8px; margin-bottom: 16px;'>")
                    html_output.append(f"<h2 style='margin: 0; font-size: 18px; font-weight: 600;'>❌ Test Execution Error</h2>")
                    html_output.append(f"<p style='margin: 8px 0 0 0;'>{result.get('error')}</p>")
                    html_output.append("</div>")
                else:
                    # Extract results for each device
                    results_per_device = result.get('results', {})
                    
                    for device_id_result, device_result in results_per_device.items():
                        device_status = device_result.get('status', 'unknown')
                        passed = device_result.get('passed', 0)
                        failed = device_result.get('failed', 0)
                        total = passed + failed
                        
                        status_color = '#34c759' if device_status == 'success' else '#ff3b30'
                        status_text = '✅ Tests Completed' if device_status == 'success' else '❌ Tests Failed'
                        
                        html_output.append(f"<div style='background-color: {status_color}; color: white; padding: 16px; border-radius: 8px; margin-bottom: 16px;'>")
                        html_output.append(f"<h2 style='margin: 0; font-size: 18px; font-weight: 600;'>{status_text}</h2>")
                        html_output.append(f"<p style='margin: 8px 0 0 0; font-size: 14px;'>Device: <strong>{device_id_result}</strong></p>")
                        html_output.append("</div>")
                        
                        html_output.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
                        html_output.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>Test Summary</p>")
                        html_output.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Total Tests: <strong>{total}</strong></p>")
                        html_output.append(f"<p style='margin: 4px 0; font-size: 13px; color: #34c759;'>✅ Passed: <strong>{passed}</strong></p>")
                        html_output.append(f"<p style='margin: 4px 0; font-size: 13px; color: #ff3b30;'>❌ Failed: <strong>{failed}</strong></p>")
                        
                        # Add test output if available
                        output = device_result.get('output', '')
                        if output:
                            import html
                            escaped_output = html.escape(str(output))
                            html_output.append("<hr style='border: none; border-top: 1px solid #d2d2d7; margin: 16px 0;'>")
                            html_output.append("<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>Test Output</p>")
                            html_output.append(f"<pre style='background-color: #f5f5f7; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 11px; font-family: monospace; white-space: pre-wrap; word-wrap: break-word;'>{escaped_output}</pre>")
                        
                        html_output.append("</div>")
                    
                    html_output.append("</div>")
                    
                    html_output.append("</div>")
                
                self.test_results.setHtml("".join(html_output))
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", f"HTTP {response.status_code}")
                except Exception:
                    error_msg = f"HTTP {response.status_code}"
                
                error_html = f"<div style='background-color: #ff3b30; color: white; padding: 16px; border-radius: 8px; margin-bottom: 16px;'><h2 style='margin: 0; font-size: 18px; font-weight: 600;'>❌ Test Execution Error</h2><p style='margin: 8px 0 0 0;'>{error_msg}</p></div>"
                self.test_results.setHtml(error_html)
        except Exception as e:
            error_html = f"<div style='background-color: #ff3b30; color: white; padding: 16px; border-radius: 8px;'><h2 style='margin: 0; font-size: 18px; font-weight: 600;'>❌ Error</h2><p style='margin: 8px 0 0 0;'>{str(e)}</p></div>"
            self.test_results.setHtml(error_html)
    
    def generate_test_plan(self):
        """Generate test plan asynchronously"""
        # Get title and description
        title_text = self.test_plan_title.text().strip()
        description_text = self.test_plan_description.toPlainText().strip()
        requirements_text = self.test_plan_requirements.toPlainText().strip()
        
        # Validate input - need at least title or requirements
        if not title_text and not requirements_text:
            QMessageBox.warning(self, "Missing Input", "Please enter a title or requirements")
            return
        
        # Generate default title if not provided
        if not title_text:
            if requirements_text:
                first_req = requirements_text.split("\n")[0].strip()
                # Remove leading dashes/bullets if present
                first_req = first_req.lstrip("-*• ").strip()
                title_text = first_req[:50] if len(first_req) > 50 else first_req
            else:
                title_text = "Test Plan"
        
        # Parse requirements
        requirements = [r.strip().lstrip("-*• ") for r in requirements_text.split("\n") if r.strip()]
        
        # Auto-derive use cases from requirements
        use_cases = []
        for req in requirements:
            if req:
                # Create use case: "Verify that <requirement>" or similar
                req_lower = req.lower()
                if req_lower.startswith(('verify', 'test', 'check', 'validate', 'ensure')):
                    use_cases.append(req)
                else:
                    # Add "Verify that" prefix if not already present
                    use_cases.append(f"Verify that {req}")
        
        # Auto-derive acceptance criteria from requirements
        acceptance_criteria = []
        for req in requirements:
            if req:
                # Create acceptance criterion: "Requirement satisfied: <requirement>"
                acceptance_criteria.append(f"Requirement satisfied: {req}")
        
        functional_spec = {
            "title": title_text,
            "description": description_text,
            "requirements": requirements,
            "use_cases": use_cases,
            "acceptance_criteria": acceptance_criteria
        }
        
        # Store functional spec for save/export
        self.current_test_plan_spec = functional_spec
        
        # Disable button during generation
        self.test_plan_generate_btn.setEnabled(False)
        
        # Validate server URL before creating worker
        if not self.server_url:
            self.test_plan_overview.clear()
            self.test_plan_overview.setHtml("<div style='color: #d32f2f; padding: 16px;'>❌ Error: Server URL is not configured. Please select a server from the server tree.</div>")
            self.test_plan_tabs.setCurrentIndex(0)
            return
        
        # Clear all tabs and show loading message
        loading_html = """
        <div style='font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 16px;'>
            <h3 style='color: #007aff;'>📋 Generating test plan...</h3>
            <p>This may take 10-30 seconds...</p>
            <p>⏳ Please wait, the UI will remain responsive...</p>
        </div>
        """
        self.test_plan_overview.setHtml(loading_html)
        self.test_plan_test_cases.clear()
        self.test_plan_unit_tests.clear()
        self.test_plan_integration_tests.clear()
        self.test_plan_results.clear()
        self.test_plan_tabs.setCurrentIndex(0)
        
        # Check if agent mode is enabled
        agent_mode = self.test_plan_agent_checkbox.isChecked() if hasattr(self, 'test_plan_agent_checkbox') else False
        
        # Get AI mode preference and sync API key to server if needed
        import os
        ai_mode_preference = "hybrid"  # default
        api_key = None
        api_base = None
        try:
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    client_settings = json.load(f)
                    ai_mode_preference = client_settings.get("preferred_ai_mode", "hybrid")
                    api_key = client_settings.get("openai_api_key", "").strip()
                    api_base = client_settings.get("openai_api_base", "").strip()
                    logger.info(f"[TEST PLAN] Read AI mode preference from settings: {ai_mode_preference} (file: {settings_file})")
            else:
                logger.warning(f"[TEST PLAN] Settings file not found: {settings_file}, using default: {ai_mode_preference}")
        except Exception as e:
            logger.warning(f"[TEST PLAN] Failed to read settings file: {e}, using default: {ai_mode_preference}")
        
        # Sync API key to server if we have one and server URL is available
        if api_key and self.server_url:
            try:
                # Check if server has the API key
                check_response = requests.get(
                    f"{self.server_url}/api/ai/settings",
                    timeout=5
                )
                if check_response.status_code == 200:
                    server_settings = check_response.json()
                    if not server_settings.get("has_api_key", False):
                        # Server doesn't have API key, send it
                        logger.info("[TEST PLAN] Syncing API key to server...")
                        sync_response = requests.post(
                            f"{self.server_url}/api/ai/settings",
                            json={
                                "openai_api_key": api_key,
                                "openai_api_base": api_base if api_base else None
                            },
                            timeout=5
                        )
                        if sync_response.status_code == 200:
                            logger.info("[TEST PLAN] API key synced to server successfully")
                        else:
                            logger.warning(f"[TEST PLAN] Failed to sync API key to server: {sync_response.status_code}")
            except Exception as e:
                logger.warning(f"[TEST PLAN] Could not sync API key to server: {e}")
        
        # For agent mode, construct user message from inputs
        user_message = None
        if agent_mode:
            # Construct natural language message for agent
            msg_parts = []
            if title_text:
                msg_parts.append(f"Title: {title_text}")
            if description_text:
                msg_parts.append(f"Description: {description_text}")
            if requirements:
                msg_parts.append(f"Requirements: {', '.join(requirements)}")
            
            user_message = "Generate a comprehensive test plan. " + ". ".join(msg_parts)
        
        # Get selected agent model if agent mode is enabled
        agent_model = None
        if agent_mode and hasattr(self, 'test_plan_agent_model'):
            agent_model = self.test_plan_agent_model.currentText().strip()
            if not agent_model:
                # Fallback to settings if nothing selected
                try:
                    settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                    if os.path.exists(settings_file):
                        with open(settings_file, 'r') as f:
                            client_settings = json.load(f)
                            agent_model = client_settings.get("cloud_model", "").strip()
                except Exception:
                    pass
        
        logger.info(f"[TEST PLAN] Creating worker with ai_mode_preference: {ai_mode_preference}, agent_mode: {agent_mode}, agent_model: {agent_model}")
        # Use worker thread to prevent UI freezing
        self.test_plan_worker = TestPlanGenerationWorker(
            self.server_url,
            functional_spec=functional_spec if not agent_mode else None,
            user_message=user_message if agent_mode else None,
            agent_mode=agent_mode,
            ai_mode_preference=ai_mode_preference,
            agent_model=agent_model
        )
        self.test_plan_worker.test_plan_received.connect(self.display_test_plan)
        self.test_plan_worker.error_received.connect(self.display_test_plan_error)
        self.test_plan_worker.finished.connect(self.on_test_plan_generation_finished)
        self.test_plan_worker.start()
    
    def display_test_plan(self, test_plan):
        """Display generated test plan in tabs"""
        # Store test plan for save/export
        self.current_test_plan = test_plan
        
        title = test_plan.get('title', '')
        test_cases_count = len(test_plan.get('test_cases', []))
        unit_tests_count = len(test_plan.get('unit_tests', []))
        integration_tests_count = len(test_plan.get('integration_tests', []))
        
        # Display generation method
        generation_method = test_plan.get('generation_method', 'template')
        if generation_method == 'llm_ollama':
            method_text = "🤖 Generated by: Ollama LLM (Local AI)"
        elif generation_method == 'llm_api':
            method_text = "🤖 Generated by: OpenAI API"
        elif generation_method == 'llm_template_hybrid':
            method_text = "🤖 Generated by: Ollama LLM + Template (Hybrid)"
        else:
            method_text = "📋 Generated by: Template (AI unavailable)"
        
        # Populate Overview Tab
        overview_html = []
        overview_html.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
        overview_html.append("<div style='padding: 12px 0; margin-bottom: 16px; border-bottom: 1px solid #d2d2d7;'>")
        overview_html.append(f"<h2 style='margin: 0 0 6px 0; font-size: 12px; font-weight: 600; color: #1d1d1f;'>✅ Test Plan Generated: {title}</h2>")
        overview_html.append(f"<p style='margin: 0; font-size: 12px; color: #6e6e73;'>{method_text}</p>")
        overview_html.append("</div>")
        
        overview_html.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
        overview_html.append("<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>📊 Summary</p>")
        overview_html.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Test Cases: <strong>{test_cases_count}</strong></p>")
        overview_html.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Unit Tests: <strong>{unit_tests_count}</strong></p>")
        if integration_tests_count > 0:
            overview_html.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Integration Tests: <strong>{integration_tests_count}</strong></p>")
        overview_html.append("</div>")
        
        if test_plan.get('overview'):
            overview_html.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
            overview_html.append("<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>📋 Overview</p>")
            overview_raw = test_plan.get('overview', '')
            # Handle both string and dict formats
            if isinstance(overview_raw, dict):
                # If it's a dict, try to extract text or convert to string
                overview = str(overview_raw).replace('\n', '<br>')
            else:
                overview = str(overview_raw).replace('\n', '<br>')
            overview_html.append(f"<p style='margin: 0; font-size: 13px; color: #1d1d1f; line-height: 1.6;'>{overview}</p>")
            overview_html.append("</div>")
        
        overview_html.append("</div>")
        self.test_plan_overview.setHtml("".join(overview_html))
        
        # Populate Test Cases Tab
        test_cases = test_plan.get('test_cases', [])
        test_cases_html = []
        test_cases_html.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
        if test_cases:
            test_cases_html.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 16px; color: #1d1d1f;'>📝 Test Cases ({len(test_cases)})</p>")
            for i, tc in enumerate(test_cases, 1):
                # Test cases can have 'name' or 'title' field
                test_name = tc.get('name') or tc.get('title') or tc.get('test_id') or f'Test Case {i}'
                test_cases_html.append(f"<div style='margin: 0 0 16px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                test_cases_html.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {test_name}</p>")
                
                if tc.get('description'):
                    test_cases_html.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Description:</strong> {tc.get('description', '')}</p>")
                if tc.get('category'):
                    test_cases_html.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Category:</strong> {tc.get('category', '')}</p>")
                if tc.get('priority'):
                    test_cases_html.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Priority:</strong> {tc.get('priority', '')}</p>")
                if tc.get('expected_result'):
                    test_cases_html.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Expected:</strong> {tc.get('expected_result', '')}</p>")
                
                # Display test steps if available
                if tc.get('steps'):
                    steps = tc.get('steps', [])
                    if isinstance(steps, list) and len(steps) > 0:
                        test_cases_html.append("<p style='margin: 8px 0 4px 0; font-size: 12px; font-weight: 600; color: #1d1d1f;'>Steps:</p>")
                        test_cases_html.append("<ul style='margin: 0; padding-left: 20px;'>")
                        for step in steps:
                            test_cases_html.append(f"<li style='margin: 2px 0; font-size: 12px; color: #6e6e73;'>{step}</li>")
                        test_cases_html.append("</ul>")
                
                test_cases_html.append("</div>")
        else:
            test_cases_html.append("<p style='color: #6e6e73;'>No test cases generated.</p>")
        test_cases_html.append("</div>")
        self.test_plan_test_cases.setHtml("".join(test_cases_html))
        
        # Populate Unit Tests Tab
        unit_tests = test_plan.get('unit_tests', [])
        unit_tests_html = []
        unit_tests_html.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
        if unit_tests:
            unit_tests_html.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 16px; color: #1d1d1f;'>🧪 Unit Tests ({len(unit_tests)})</p>")
            for i, ut in enumerate(unit_tests, 1):
                # Handle different unit test structures
                test_name = (ut.get('name') or ut.get('test_id') or ut.get('title') or f'Unit Test {i}')
                
                unit_tests_html.append(f"<div style='margin: 0 0 16px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                unit_tests_html.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {test_name}</p>")
                
                # Try different field names for description
                description = None
                if ut.get('description'):
                    description = ut.get('description')
                elif ut.get('requirement'):
                    req = ut.get('requirement')
                    description = str(req)
                elif ut.get('use_case'):
                    uc = ut.get('use_case')
                    description = str(uc)
                elif ut.get('acceptance_criterion'):
                    ac = ut.get('acceptance_criterion')
                    description = str(ac)
                
                if description:
                    unit_tests_html.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Description:</strong> {description}</p>")
                
                # Function name or test ID
                function_name = ut.get('function_name') or ut.get('test_id')
                if function_name and function_name != test_name:
                    unit_tests_html.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Function:</strong> {function_name}</p>")
                
                # Test code (full code in unit tests tab)
                test_code = ut.get('test_code')
                if test_code and isinstance(test_code, str) and test_code.strip():
                    unit_tests_html.append("<p style='margin: 8px 0 4px 0; font-size: 12px; font-weight: 600; color: #1d1d1f;'>Test Code:</p>")
                    unit_tests_html.append("<pre style='background-color: #ffffff; padding: 12px; border-radius: 4px; font-size: 11px; overflow-x: auto; margin: 0; border: 1px solid #d2d2d7;'>")
                    # Escape HTML
                    escaped_code = test_code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    unit_tests_html.append(escaped_code)
                    unit_tests_html.append("</pre>")
                
                unit_tests_html.append("</div>")
        else:
            unit_tests_html.append("<p style='color: #6e6e73;'>No unit tests generated.</p>")
        unit_tests_html.append("</div>")
        self.test_plan_unit_tests.setHtml("".join(unit_tests_html))
        
        # Populate Integration Tests Tab
        integration_tests = test_plan.get('integration_tests', [])
        integration_html = []
        integration_html.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
        if integration_tests:
            integration_html.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 16px; color: #1d1d1f;'>🔗 Integration Tests ({len(integration_tests)})</p>")
            for i, it in enumerate(integration_tests, 1):
                integration_html.append(f"<div style='margin: 0 0 12px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                # Handle both dict and string formats
                if isinstance(it, dict):
                    integration_html.append(f"<p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {it.get('name', f'Integration Test {i}')}</p>")
                    if it.get('description'):
                        integration_html.append(f"<p style='margin: 0; font-size: 12px; color: #6e6e73;'>{it.get('description', '')}</p>")
                else:
                    # it is a string
                    integration_html.append(f"<p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {str(it)}</p>")
                integration_html.append("</div>")
        else:
            integration_html.append("<p style='color: #6e6e73;'>No integration tests generated.</p>")
        integration_html.append("</div>")
        self.test_plan_integration_tests.setHtml("".join(integration_html))
        
        # Populate Full View Tab (backward compatibility)
        # Build complete HTML for full view tab
        html_parts = []
        html_parts.append("<div style='font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;'>")
        html_parts.append("<div style='padding: 12px 0; margin-bottom: 16px; border-bottom: 1px solid #d2d2d7;'>")
        html_parts.append(f"<h2 style='margin: 0 0 6px 0; font-size: 12px; font-weight: 600; color: #1d1d1f;'>✅ Test Plan Generated: {title}</h2>")
        html_parts.append(f"<p style='margin: 0; font-size: 12px; color: #6e6e73;'>{method_text}</p>")
        html_parts.append("</div>")
        
        # Add overview sections (summary and overview text)
        html_parts.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
        html_parts.append("<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>📊 Summary</p>")
        html_parts.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Test Cases: <strong>{test_cases_count}</strong></p>")
        html_parts.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Unit Tests: <strong>{unit_tests_count}</strong></p>")
        if integration_tests_count > 0:
            html_parts.append(f"<p style='margin: 4px 0; font-size: 13px; color: #1d1d1f;'>Integration Tests: <strong>{integration_tests_count}</strong></p>")
        html_parts.append("</div>")
        
        if test_plan.get('overview'):
            html_parts.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
            html_parts.append("<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>📋 Overview</p>")
            overview_raw = test_plan.get('overview', '')
            # Handle both string and dict formats
            if isinstance(overview_raw, dict):
                # If it's a dict, try to extract text or convert to string
                overview = str(overview_raw).replace('\n', '<br>')
            else:
                overview = str(overview_raw).replace('\n', '<br>')
            html_parts.append(f"<p style='margin: 0; font-size: 13px; color: #1d1d1f; line-height: 1.6;'>{overview}</p>")
            html_parts.append("</div>")
        
        # Add test cases section
        if test_cases:
            html_parts.append(f"<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
            html_parts.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>📝 Test Cases ({len(test_cases)})</p>")
            # Reuse test cases HTML but remove outer div
            for i, tc in enumerate(test_cases, 1):
                # Handle both dict and string formats
                if isinstance(tc, dict):
                    test_name = tc.get('name') or tc.get('title') or tc.get('test_id') or f'Test Case {i}'
                    html_parts.append(f"<div style='margin: 0 0 16px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                    html_parts.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {test_name}</p>")
                    if tc.get('description'):
                        html_parts.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Description:</strong> {tc.get('description', '')}</p>")
                    if tc.get('category'):
                        html_parts.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Category:</strong> {tc.get('category', '')}</p>")
                    if tc.get('priority'):
                        html_parts.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Priority:</strong> {tc.get('priority', '')}</p>")
                    if tc.get('expected_result'):
                        html_parts.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Expected:</strong> {tc.get('expected_result', '')}</p>")
                    if tc.get('steps'):
                        steps = tc.get('steps', [])
                        if isinstance(steps, list) and len(steps) > 0:
                            html_parts.append("<p style='margin: 8px 0 4px 0; font-size: 12px; font-weight: 600; color: #1d1d1f;'>Steps:</p>")
                            html_parts.append("<ul style='margin: 0; padding-left: 20px;'>")
                            for step in steps:
                                html_parts.append(f"<li style='margin: 2px 0; font-size: 12px; color: #6e6e73;'>{step}</li>")
                            html_parts.append("</ul>")
                    html_parts.append("</div>")
                else:
                    # tc is a string
                    html_parts.append(f"<div style='margin: 0 0 16px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                    html_parts.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {str(tc)}</p>")
                    html_parts.append("</div>")
            html_parts.append("</div>")
        
        # Add unit tests section
        if unit_tests:
            html_parts.append(f"<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
            html_parts.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>🧪 Unit Tests ({len(unit_tests)})</p>")
            for i, ut in enumerate(unit_tests, 1):
                # Handle both dict and string formats
                if isinstance(ut, dict):
                    test_name = (ut.get('name') or ut.get('test_id') or ut.get('title') or f'Unit Test {i}')
                    html_parts.append(f"<div style='margin: 0 0 16px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                    html_parts.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {test_name}</p>")
                    if ut.get('description'):
                        html_parts.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>Description:</strong> {ut.get('description')}</p>")
                    test_code = ut.get('test_code')
                    if test_code and isinstance(test_code, str) and test_code.strip():
                        html_parts.append("<pre style='background-color: #ffffff; padding: 12px; border-radius: 4px; font-size: 11px; overflow-x: auto; margin: 8px 0 0 0; border: 1px solid #d2d2d7;'>")
                        escaped_code = test_code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        html_parts.append(escaped_code)
                        html_parts.append("</pre>")
                    html_parts.append("</div>")
                else:
                    # ut is a string
                    html_parts.append(f"<div style='margin: 0 0 16px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                    html_parts.append(f"<p style='margin: 0 0 8px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {str(ut)}</p>")
                    html_parts.append("</div>")
            html_parts.append("</div>")
        
        # Add integration tests section
        if integration_tests_count > 0:
            integration_tests = test_plan.get('integration_tests', [])
            html_parts.append(f"<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
            html_parts.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>🔗 Integration Tests ({len(integration_tests)})</p>")
            for i, it in enumerate(integration_tests, 1):
                html_parts.append(f"<div style='margin: 0 0 12px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                # Handle both dict and string formats
                if isinstance(it, dict):
                    html_parts.append(f"<p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {it.get('name', f'Integration Test {i}')}</p>")
                    if it.get('description'):
                        html_parts.append(f"<p style='margin: 0; font-size: 12px; color: #6e6e73;'>{it.get('description', '')}</p>")
                else:
                    # it is a string
                    html_parts.append(f"<p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {str(it)}</p>")
                html_parts.append("</div>")
            html_parts.append("</div>")
        
        # Add test data and environment to full view if available
        test_data = test_plan.get('test_data', [])
        if test_data:
            html_parts.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>")
            html_parts.append(f"<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>📊 Test Data Requirements ({len(test_data)})</p>")
            for i, td in enumerate(test_data, 1):
                html_parts.append(f"<div style='margin: 0 0 12px 0; padding: 12px; background-color: #f5f5f7; border-radius: 6px;'>")
                # Handle both dict and string formats
                if isinstance(td, dict):
                    html_parts.append(f"<p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {td.get('name', f'Test Data {i}')}</p>")
                    if td.get('description'):
                        html_parts.append(f"<p style='margin: 0; font-size: 12px; color: #6e6e73;'>{td.get('description', '')}</p>")
                else:
                    # td is a string
                    html_parts.append(f"<p style='margin: 0 0 4px 0; font-weight: 600; font-size: 13px; color: #1d1d1f;'>{i}. {str(td)}</p>")
                html_parts.append("</div>")
            html_parts.append("</div>")
        
        test_env = test_plan.get('test_environment', {})
        if test_env:
            html_parts.append("<div style='background-color: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; padding: 16px;'>")
            html_parts.append("<p style='margin: 0 0 12px 0; font-weight: 600; font-size: 14px; color: #1d1d1f;'>🌐 Test Environment</p>")
            for key, value in test_env.items():
                html_parts.append(f"<p style='margin: 4px 0; font-size: 12px; color: #6e6e73;'><strong>{key}:</strong> {value}</p>")
            html_parts.append("</div>")
        
        html_parts.append("</div>")
        self.test_plan_results.setHtml("".join(html_parts))
        
        # Switch to Overview tab and enable buttons
        self.test_plan_tabs.setCurrentIndex(0)
        self.test_plan_pytest_btn.setEnabled(True)
        self.test_plan_save_btn.setEnabled(True)
        self.test_plan_export_btn.setEnabled(True)
    
    def display_test_plan_error(self, error_message):
        """Display test plan generation error"""
        # Check if this is an LLM client error and provide helpful guidance
        is_llm_error = (
            "No LLM client available" in error_message or 
            "LLM client" in error_message or
            "Cloud-only mode" in error_message or
            "Cloud API" in error_message or
            "API key" in error_message
        )
        
        # Format error message with better styling
        import html
        escaped_message = html.escape(error_message)
        # Convert newlines to <br> for HTML display
        formatted_message = escaped_message.replace('\n', '<br>')
        
        error_html = f"""
        <div style='font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 16px;'>
            <div style='background-color: #ff3b30; color: white; padding: 16px; border-radius: 8px; margin-bottom: 16px;'>
                <h2 style='margin: 0; font-size: 18px; font-weight: 600;'>❌ Test Plan Generation Error</h2>
                <p style='margin: 8px 0 0 0; white-space: pre-wrap;'>{formatted_message}</p>
            </div>
        """
        
        # Add helpful guidance for LLM errors
        if is_llm_error:
            # Check if it's specifically a cloud-only mode error
            is_cloud_only_error = "Cloud-only mode" in error_message or "Cloud API" in error_message
            
            if is_cloud_only_error:
                error_html += """
                <div style='background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>
                    <h3 style='margin: 0 0 8px 0; font-size: 14px; font-weight: 600; color: #856404;'>💡 Cloud-Only Mode Error - Quick Fix:</h3>
                    <ul style='margin: 0; padding-left: 20px; color: #856404;'>
                        <li style='margin-bottom: 8px;'><strong>Option 1 (Recommended):</strong> Disable "🤖 Agent Mode" checkbox and use Standard Mode (works without LLM)</li>
                        <li style='margin-bottom: 8px;'><strong>Option 2:</strong> Change AI mode preference from "Cloud Only" to "Hybrid" in settings</li>
                        <li style='margin-bottom: 8px;'><strong>Option 3:</strong> Configure OPENAI_API_KEY environment variable on the server</li>
                        <li style='margin-bottom: 8px;'><strong>Option 4:</strong> Verify API key is valid and has credits/quota available</li>
                    </ul>
                    <p style='margin: 8px 0 0 0; font-size: 12px; color: #856404;'>
                        <strong>Note:</strong> Agent Mode requires cloud API with function calling. Standard Mode works without any LLM.
                    </p>
                </div>
                """
            else:
                error_html += """
                <div style='background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 16px; margin-bottom: 16px;'>
                    <h3 style='margin: 0 0 8px 0; font-size: 14px; font-weight: 600; color: #856404;'>💡 Quick Fix Options:</h3>
                    <ul style='margin: 0; padding-left: 20px; color: #856404;'>
                        <li style='margin-bottom: 8px;'><strong>Option 1:</strong> Disable "🤖 Agent Mode" checkbox and use Standard Mode (works without LLM)</li>
                        <li style='margin-bottom: 8px;'><strong>Option 2:</strong> Configure OpenAI API key in settings for cloud LLM</li>
                        <li style='margin-bottom: 8px;'><strong>Option 3:</strong> Start Ollama service for local LLM (https://ollama.ai)</li>
                    </ul>
                    <p style='margin: 8px 0 0 0; font-size: 12px; color: #856404;'>
                        <strong>Note:</strong> Standard Mode can generate test plans using templates even without LLM.
                    </p>
                </div>
                """
        
        error_html += "</div>"
        
        self.test_plan_overview.setHtml(error_html)
        self.test_plan_test_cases.clear()
        self.test_plan_unit_tests.clear()
        self.test_plan_integration_tests.clear()
        self.test_plan_results.clear()
        self.test_plan_tabs.setCurrentIndex(0)
        
        # If it's an LLM error and agent mode is enabled, offer to disable it
        if is_llm_error and hasattr(self, 'test_plan_agent_checkbox') and self.test_plan_agent_checkbox.isChecked():
            # Auto-disable agent mode to help user
            # But first ask user if they want to disable it
            reply = QMessageBox.question(
                self,
                "Disable Agent Mode?",
                "Agent Mode requires LLM but none is available.\n\n"
                "Would you like to disable Agent Mode and use Standard Mode instead?\n\n"
                "Standard Mode works without LLM and can still generate test plans.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                self.test_plan_agent_checkbox.setChecked(False)
                QMessageBox.information(
                    self,
                    "Agent Mode Disabled",
                    "Agent Mode has been disabled.\n\n"
                    "You can now click 'Generate Test Plan' again to use Standard Mode.\n\n"
                    "Standard Mode will generate test plans using templates even without LLM."
                )
    
    def on_test_plan_generation_finished(self):
        """Re-enable button after generation"""
        self.test_plan_generate_btn.setEnabled(True)
    
    def save_test_plan(self):
        """Save current test plan to file"""
        if not hasattr(self, 'current_test_plan') or not self.current_test_plan:
            QMessageBox.warning(self, "No Test Plan", "Please generate a test plan first")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Test Plan", "", "JSON Files (*.json);;All Files (*)"
        )
        
        if filename:
            try:
                data = {
                    "functional_spec": getattr(self, 'current_test_plan_spec', {}),
                    "test_plan": self.current_test_plan
                }
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                QMessageBox.information(self, "Success", f"Test plan saved to {filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save test plan: {str(e)}")
    
    def load_test_plan(self):
        """Load test plan from file"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Load Test Plan", "", "JSON Files (*.json);;All Files (*)"
        )
        
        if filename:
            try:
                with open(filename, 'r') as f:
                    data = json.load(f)
                
                # Load functional spec
                spec = data.get("functional_spec", {})
                if spec:
                    self.test_plan_title.setText(spec.get("title", ""))
                    self.test_plan_description.setPlainText(spec.get("description", ""))
                    self.test_plan_requirements.setPlainText("\n".join(spec.get("requirements", [])))
                    # Note: Use cases and acceptance criteria are auto-derived from requirements
                
                # Load test plan and display it
                test_plan = data.get("test_plan")
                if test_plan:
                    self.current_test_plan = test_plan
                    self.current_test_plan_spec = spec
                    self.display_test_plan(test_plan)
                    self.test_plan_save_btn.setEnabled(True)
                    self.test_plan_export_btn.setEnabled(True)
                    self.test_plan_pytest_btn.setEnabled(True)
                    QMessageBox.information(self, "Success", f"Test plan loaded from {filename}")
                else:
                    QMessageBox.warning(self, "Warning", "File does not contain a test plan")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load test plan: {str(e)}")
    
    def export_test_plan_markdown(self):
        """Export test plan as Markdown"""
        if not hasattr(self, 'current_test_plan') or not self.current_test_plan:
            QMessageBox.warning(self, "No Test Plan", "Please generate a test plan first")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Test Plan as Markdown", "", "Markdown Files (*.md);;All Files (*)"
        )
        
        if filename:
            try:
                tp = self.current_test_plan
                md_lines = []
                md_lines.append(f"# {tp.get('title', 'Test Plan')}\n")
                md_lines.append(f"\n**Generated:** {tp.get('test_plan_id', 'N/A')}\n")
                
                if tp.get('overview'):
                    md_lines.append(f"\n## Overview\n\n{tp.get('overview')}\n")
                
                # Test Cases
                test_cases = tp.get('test_cases', [])
                if test_cases:
                    md_lines.append(f"\n## Test Cases ({len(test_cases)})\n")
                    for i, tc in enumerate(test_cases, 1):
                        test_name = tc.get('name') or tc.get('title') or tc.get('test_id') or f'Test Case {i}'
                        md_lines.append(f"\n### {i}. {test_name}\n")
                        if tc.get('description'):
                            md_lines.append(f"**Description:** {tc.get('description')}\n")
                        if tc.get('priority'):
                            md_lines.append(f"**Priority:** {tc.get('priority')}\n")
                        if tc.get('steps'):
                            md_lines.append("\n**Steps:**\n")
                            for step in tc.get('steps', []):
                                md_lines.append(f"1. {step}\n")
                        if tc.get('expected_result'):
                            md_lines.append(f"\n**Expected Result:** {tc.get('expected_result')}\n")
                
                # Unit Tests
                unit_tests = tp.get('unit_tests', [])
                if unit_tests:
                    md_lines.append(f"\n## Unit Tests ({len(unit_tests)})\n")
                    for i, ut in enumerate(unit_tests, 1):
                        test_name = ut.get('name') or ut.get('test_id') or f'Unit Test {i}'
                        md_lines.append(f"\n### {i}. {test_name}\n")
                        if ut.get('description'):
                            md_lines.append(f"**Description:** {ut.get('description')}\n")
                        if ut.get('test_code'):
                            md_lines.append("\n```python\n")
                            md_lines.append(ut.get('test_code'))
                            md_lines.append("\n```\n")
                
                with open(filename, 'w') as f:
                    f.write(''.join(md_lines))
                QMessageBox.information(self, "Success", f"Test plan exported to {filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export test plan: {str(e)}")
    
    def export_test_plan_json(self):
        """Export test plan as JSON"""
        if not hasattr(self, 'current_test_plan') or not self.current_test_plan:
            QMessageBox.warning(self, "No Test Plan", "Please generate a test plan first")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Test Plan as JSON", "", "JSON Files (*.json);;All Files (*)"
        )
        
        if filename:
            try:
                data = {
                    "functional_spec": getattr(self, 'current_test_plan_spec', {}),
                    "test_plan": self.current_test_plan
                }
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                QMessageBox.information(self, "Success", f"Test plan exported to {filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export test plan: {str(e)}")
    
    def export_test_plan_html(self):
        """Export test plan as HTML"""
        if not hasattr(self, 'current_test_plan') or not self.current_test_plan:
            QMessageBox.warning(self, "No Test Plan", "Please generate a test plan first")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Test Plan as HTML", "", "HTML Files (*.html);;All Files (*)"
        )
        
        if filename:
            try:
                # Use the same HTML format as display_test_plan
                tp = self.current_test_plan
                html = self.test_plan_results.toHtml()
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{tp.get('title', 'Test Plan')}</title></head><body>{html}</body></html>")
                QMessageBox.information(self, "Success", f"Test plan exported to {filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export test plan: {str(e)}")
    
    def generate_pytest_from_plan(self):
        """Generate pytest from test plan asynchronously"""
        if not hasattr(self, 'current_test_plan') or not self.current_test_plan:
            QMessageBox.warning(self, "No Test Plan", "Please generate a test plan first")
            return
        
        # Disable button during generation
        self.test_plan_pytest_btn.setEnabled(False)
        
        # Validate server URL before creating worker
        if not self.server_url:
            self.test_plan_results.append("\n❌ Error: Server URL is not configured. Please select a server from the server tree.")
            self.test_plan_pytest_btn.setEnabled(True)
            return
        
        # Check agent mode checkbox state
        agent_mode = self.test_plan_agent_checkbox.isChecked() if hasattr(self, 'test_plan_agent_checkbox') else False
        
        # Get AI mode preference
        ai_mode_preference = "hybrid"
        try:
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    client_settings = json.load(f)
                    ai_mode_preference = client_settings.get("preferred_ai_mode", "hybrid")
        except Exception as e:
            logger.debug(f"Could not read AI mode preference: {e}")
        
        # Get agent model if agent mode is enabled
        agent_model = None
        if agent_mode and hasattr(self, 'test_plan_agent_model'):
            agent_model = self.test_plan_agent_model.currentText().strip()
            if not agent_model:
                # Try to get from settings file
                try:
                    settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                    if os.path.exists(settings_file):
                        with open(settings_file, 'r') as f:
                            client_settings = json.load(f)
                            agent_model = client_settings.get("cloud_model", "").strip()
                except Exception as e:
                    logger.debug(f"Could not read agent model preference: {e}")
        
        mode_text = "🤖 Agent Mode" if agent_mode else "Standard Mode"
        # Show status in pytest script tab
        status_text = f"🐍 Generating pytest script ({mode_text})...\nThis may take 10-30 seconds...\n⏳ Please wait, the UI will remain responsive..."
        self.test_plan_pytest_script.setPlainText(status_text)
        
        # Switch to the Pytest Script tab to show status
        parent_widget = self.test_plan_pytest_script.parent()
        if parent_widget:
            pytest_tab_index = self.test_plan_tabs.indexOf(parent_widget)
            if pytest_tab_index >= 0:
                self.test_plan_tabs.setCurrentIndex(pytest_tab_index)
        
        # Use worker thread to prevent UI freezing
        self.pytest_worker = PytestGenerationWorker(
            self.server_url, 
            self.current_test_plan,
            agent_mode=agent_mode,
            ai_mode_preference=ai_mode_preference,
            agent_model=agent_model
        )
        self.pytest_worker.pytest_received.connect(self.display_pytest_script)
        self.pytest_worker.error_received.connect(self.display_pytest_error)
        self.pytest_worker.finished.connect(self.on_pytest_generation_finished)
        self.pytest_worker.start()
    
    def _update_agent_model_dropdown(self):
        """Update agent model dropdown - show all available models"""
        try:
            if not hasattr(self, 'test_plan_agent_model'):
                return
                
            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
            base_url = ""
            saved_model = ""
            
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    settings = json.load(f)
                    base_url = settings.get("openai_api_base", "").strip().lower()
                    saved_model = settings.get("cloud_model", "").strip()
            
            current_text = self.test_plan_agent_model.currentText()
            self.test_plan_agent_model.clear()
            
            # Add all available models (both Groq and OpenAI)
            # Groq models
            groq_models = [
                "llama-3.1-8b-instant",
                "llama-3.3-70b-versatile"
            ]
            
            # OpenAI models
            openai_models = [
                "gpt-4",
                "gpt-4-turbo",
                "gpt-4o",
                "gpt-3.5-turbo"
            ]
            
            # Add Groq models with prefix
            self.test_plan_agent_model.addItem("--- Groq Models ---")
            for model in groq_models:
                self.test_plan_agent_model.addItem(model)
            
            # Add OpenAI models with prefix
            self.test_plan_agent_model.addItem("--- OpenAI Models ---")
            for model in openai_models:
                self.test_plan_agent_model.addItem(model)
            
            # Set saved model or default based on API base URL
            all_models = groq_models + openai_models
            if saved_model and saved_model in all_models:
                self.test_plan_agent_model.setCurrentText(saved_model)
            elif current_text and current_text in all_models:
                self.test_plan_agent_model.setCurrentText(current_text)
            else:
                # Default based on API base URL
                if "groq" in base_url:
                    self.test_plan_agent_model.setCurrentText("llama-3.1-8b-instant")
                else:
                    self.test_plan_agent_model.setCurrentText("gpt-4")
        except Exception as e:
            logger.error(f"Error updating agent model dropdown: {e}")
    
    def _update_agent_model_dropdown_visibility(self, checked):
        """Show/hide model dropdown based on agent mode checkbox"""
        if hasattr(self, 'test_plan_agent_model'):
            self.test_plan_agent_model.setVisible(checked)
        if hasattr(self, 'test_plan_agent_model_label'):
            self.test_plan_agent_model_label.setVisible(checked)
        if checked:
            # Update models when shown
            self._update_agent_model_dropdown()
    
    def display_pytest_script(self, pytest_script):
        """Display generated pytest script"""
        # Clear and set the pytest script in the dedicated tab
        self.test_plan_pytest_script.setPlainText(pytest_script)
        self.current_pytest_script = pytest_script
        
        # Auto-save to temp file for Test Framework integration
        import tempfile
        import os
        try:
            # Create temp file with meaningful prefix
            test_plan_title = self.current_test_plan.get("title", "test_plan") if self.current_test_plan else "test_plan"
            # Sanitize title for filename
            safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in test_plan_title)
            safe_title = safe_title.replace(' ', '_')[:30]  # Limit length
            
            temp_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                prefix=f'test_plan_{safe_title}_',
                dir=tempfile.gettempdir()
            )
            temp_file.write(pytest_script)
            temp_file.close()
            
            self.current_pytest_script_file = temp_file.name
            logger.info(f"[TEST PLAN] Saved pytest script to temp file: {temp_file.name}")
            
            # Enable "Use in Test Framework" button only if file was saved successfully
            if hasattr(self, 'use_in_test_framework_btn'):
                self.use_in_test_framework_btn.setEnabled(True)
        except Exception as e:
            logger.warning(f"[TEST PLAN] Failed to save pytest script to temp file: {e}")
            self.current_pytest_script_file = None
            # Don't enable button if file save failed
            if hasattr(self, 'use_in_test_framework_btn'):
                self.use_in_test_framework_btn.setEnabled(False)
        
        # Switch to the Pytest Script tab
        parent_widget = self.test_plan_pytest_script.parent()
        if parent_widget:
            pytest_tab_index = self.test_plan_tabs.indexOf(parent_widget)
            if pytest_tab_index >= 0:
                self.test_plan_tabs.setCurrentIndex(pytest_tab_index)
        
        # Scroll to top
        self.test_plan_pytest_script.verticalScrollBar().setValue(0)
    
    def display_pytest_error(self, error_message):
        """Display pytest generation error"""
        # Display error in the pytest script tab
        error_text = f"❌ Error: {error_message}\n\nPlease check your settings and try again."
        self.test_plan_pytest_script.setPlainText(error_text)
        
        # Clear pytest script tracking on error
        self.current_pytest_script = None
        self.current_pytest_script_file = None
        
        # Disable "Use in Test Framework" button on error
        if hasattr(self, 'use_in_test_framework_btn'):
            self.use_in_test_framework_btn.setEnabled(False)
        
        # Switch to the Pytest Script tab
        parent_widget = self.test_plan_pytest_script.parent()
        if parent_widget:
            pytest_tab_index = self.test_plan_tabs.indexOf(parent_widget)
            if pytest_tab_index >= 0:
                self.test_plan_tabs.setCurrentIndex(pytest_tab_index)
    
    def on_pytest_generation_finished(self):
        """Re-enable button after generation"""
        self.test_plan_pytest_btn.setEnabled(True)
    
    def use_pytest_in_test_framework(self):
        """Transfer pytest script to Test Framework tab"""
        if not hasattr(self, 'current_pytest_script_file') or not self.current_pytest_script_file:
            # Fallback: create temp file from current script if available
            if hasattr(self, 'current_pytest_script') and self.current_pytest_script:
                import tempfile
                try:
                    test_plan_title = self.current_test_plan.get("title", "test_plan") if self.current_test_plan else "test_plan"
                    safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in test_plan_title)
                    safe_title = safe_title.replace(' ', '_')[:30]
                    
                    temp_file = tempfile.NamedTemporaryFile(
                        mode='w',
                        suffix='.py',
                        delete=False,
                        prefix=f'test_plan_{safe_title}_',
                        dir=tempfile.gettempdir()
                    )
                    temp_file.write(self.current_pytest_script)
                    temp_file.close()
                    self.current_pytest_script_file = temp_file.name
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to prepare pytest script: {str(e)}")
                    return
            else:
                QMessageBox.warning(self, "No Script", "No pytest script available. Please generate a pytest script first.")
                return
        
        # Verify file exists
        import os
        if not os.path.exists(self.current_pytest_script_file):
            QMessageBox.warning(self, "File Not Found", f"Pytest script file not found: {self.current_pytest_script_file}")
            return
        
        # Switch to Test Framework tab
        test_framework_index = -1
        if hasattr(self, 'test_framework_widget') and self.test_framework_widget:
            test_framework_index = self.tabs.indexOf(self.test_framework_widget)
        
        if test_framework_index < 0:
            # Find the test framework tab by name
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "🧪 Test Framework":
                    test_framework_index = i
                    break
        
        if test_framework_index >= 0:
            self.tabs.setCurrentIndex(test_framework_index)
            
            # Set the file path in Test Framework tab
            if hasattr(self, 'pytest_file_path'):
                self.pytest_file_path.setText(self.current_pytest_script_file)
                
                # Show indicator that script came from Test Plan
                if hasattr(self, 'test_results'):
                    indicator_html = f"""
                    <div style='background-color: #e3f2fd; border-left: 4px solid #007aff; padding: 8px 12px; margin-bottom: 8px; border-radius: 4px;'>
                        <p style='margin: 0; font-size: 12px; color: #1976d2;'>
                            <strong>📋 Using pytest script from Test Plan:</strong> {os.path.basename(self.current_pytest_script_file)}
                        </p>
                    </div>
                    """
                    # Store flag to track that script came from Test Plan
                    self._pytest_from_test_plan = True
                    # Clear previous results and show indicator
                    self.test_results.setHtml(indicator_html)
                
                QMessageBox.information(
                    self,
                    "Script Transferred",
                    f"Pytest script has been transferred to Test Framework tab.\n\n"
                    f"File: {os.path.basename(self.current_pytest_script_file)}\n\n"
                    f"You can now:\n"
                    f"1. Enter Device ID or upload Device CSV\n"
                    f"2. Click 'Run Tests' to execute"
                )
            else:
                QMessageBox.warning(self, "Error", "Test Framework tab not properly initialized.")
        else:
            QMessageBox.warning(self, "Error", "Test Framework tab not found.")
    
    def generate_code(self):
        """Generate code asynchronously"""
        prompt = self.code_prompt.toPlainText().strip()
        language = self.code_language.currentText().lower()
        code_type = self.code_type.currentText().lower()
        
        if not prompt:
            QMessageBox.warning(self, "Missing Prompt", "Please enter a prompt")
            return
        
        # Disable button during generation
        if hasattr(self, 'code_generate_btn'):
            self.code_generate_btn.setEnabled(False)
        
        # Validate server URL before creating worker
        if not self.server_url:
            self.code_results.clear()
            self.code_results.append("❌ Error: Server URL is not configured. Please select a server from the server tree.")
            if hasattr(self, 'code_generate_btn'):
                self.code_generate_btn.setEnabled(True)
            return
        
        self.code_results.clear()
        self.code_results.append("💻 Generating code... This may take 10-30 seconds...")
        self.code_results.append("⏳ Please wait, the UI will remain responsive...")
        
        # Use worker thread to prevent UI freezing
        self.code_worker = CodeGenerationWorker(
            self.server_url, prompt, language, code_type
        )
        self._last_code_language = language
        self.code_worker.code_received.connect(self.display_generated_code)
        self.code_worker.error_received.connect(self.display_code_error)
        self.code_worker.finished.connect(self.on_code_generation_finished)
        self.code_worker.start()
    
    def display_generated_code(self, code):
        """Display generated code"""
        self.code_results.clear()
        try_format = getattr(self, "_last_code_language", "") == "python"
        formatted = self._format_code_plain(code, normalize=True, try_format=try_format, require_format=False)
        if try_format and not self._validate_python_syntax(formatted):
            self.code_results.setPlainText("⚠️ Code failed a quick syntax check. Showing raw output for review.\n\n" + formatted)
            return
        self.code_results.setPlainText(formatted)
        self.code_results.verticalScrollBar().setValue(0)
    
    def display_code_error(self, error_message):
        """Display code generation error"""
        self.code_results.appendPlainText(f"\n❌ Error: {error_message}")

    def on_code_generation_finished(self):
        """Re-enable button after generation"""
        if hasattr(self, 'code_generate_btn'):
            self.code_generate_btn.setEnabled(True)
    
    def execute_device_tests(self):
        """Execute device tests"""
        devices_text = self.device_test_devices.text().strip()
        device_type = self.device_test_type.currentText()
        pytest_script = self.device_test_script.toPlainText().strip()
        
        if not pytest_script:
            QMessageBox.warning(self, "No Script", "Please provide a pytest script or generate one from test plan")
            return
        
        device_ids = [d.strip() for d in devices_text.split(",") if d.strip()] if devices_text else []
        
        self.device_test_results.clear()
        self.device_test_results.append("▶ Executing tests on devices...")
        
        try:
            if device_ids:
                # Execute on specific devices
                response = requests.post(
                    f"{self.server_url}/api/ai/pytest/execute-devices",
                    json={
                        "pytest_script": pytest_script,
                        "device_ids": device_ids
                    },
                    timeout=300
                )
            else:
                # Execute on all devices of type
                response = requests.post(
                    f"{self.server_url}/api/ai/pytest/execute-device-type",
                    json={
                        "pytest_script": pytest_script,
                        "device_type": device_type
                    },
                    timeout=300
                )
            
            if response.status_code == 200:
                result = response.json()
                self.device_test_results.clear()
                summary = result.get("summary", {})
                self.device_test_results.append(f"✅ Tests Completed!")
                self.device_test_results.append(f"Total: {summary.get('total_tests', 0)}")
                self.device_test_results.append(f"Passed: {summary.get('passed', 0)}")
                self.device_test_results.append(f"Failed: {summary.get('failed', 0)}")
            else:
                self.device_test_results.append(f"❌ Error: {response.status_code}")
        except Exception as e:
            self.device_test_results.append(f"❌ Error: {str(e)}")
