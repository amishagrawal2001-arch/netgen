"""
Comprehensive Test Framework
Unit, integration, system, and E2E test generation and execution
"""

import logging
import ast
import inspect
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import uuid

logger = logging.getLogger(__name__)


class ComprehensiveTestFramework:
    """Comprehensive testing framework with multi-level test generation"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        
        # Initialize AI client if using cloud API
        if use_ai_api and api_key:
            try:
                import openai
                import os
                # Support for OpenAI-compatible APIs (Groq, Together AI, etc.)
                base_url = os.environ.get("OPENAI_API_BASE", None)
                if base_url:
                    self.ai_client = openai.OpenAI(api_key=api_key, base_url=base_url)
                    logger.info(f"Using OpenAI-compatible API at: {base_url}")
                else:
                    self.ai_client = openai.OpenAI(api_key=api_key)
            except ImportError:
                logger.warning("OpenAI library not installed")
                self.ai_client = None
        else:
            self.ai_client = None
    
    def generate_unit_tests(self, code: str, test_framework: str = "pytest") -> List[str]:
        """
        Generate unit tests from code analysis
        
        Args:
            code: Source code to test
            test_framework: Test framework (pytest, unittest, nose)
        
        Returns:
            List of test function strings
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.error(f"Syntax error in code: {e}")
            return [f"# Syntax error: {e}"]
        
        test_functions = []
        
        # Extract functions and classes
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                test_func = self._generate_function_test(node, code, test_framework)
                if test_func:
                    test_functions.append(test_func)
            elif isinstance(node, ast.ClassDef):
                class_tests = self._generate_class_tests(node, code, test_framework)
                test_functions.extend(class_tests)
        
        return test_functions
    
    def _generate_function_test(self, func_node: ast.FunctionDef, code: str, 
                               test_framework: str) -> Optional[str]:
        """Generate test for a function"""
        func_name = func_node.name
        
        # Skip private functions and test functions
        if func_name.startswith('_') or func_name.startswith('test_'):
            return None
        
        # Get function signature
        args = [arg.arg for arg in func_node.args.args if arg.arg != 'self']
        
        # Generate test template
        if test_framework == "pytest":
            test_code = f"""
def test_{func_name}():
    \"\"\"Test {func_name} function\"\"\"
    # TODO: Add test cases
    # Test normal operation
    # Test edge cases
    # Test error handling
    pass
"""
        else:
            test_code = f"""
def test_{func_name}(self):
    \"\"\"Test {func_name} function\"\"\"
    # TODO: Add test cases
    pass
"""
        
        return test_code.strip()
    
    def _generate_class_tests(self, class_node: ast.ClassDef, code: str,
                              test_framework: str) -> List[str]:
        """Generate tests for a class"""
        tests = []
        class_name = class_node.name
        
        # Skip test classes
        if class_name.startswith('Test') or 'Test' in class_name:
            return tests
        
        # Generate tests for each method
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef):
                method_name = node.name
                if not method_name.startswith('_'):
                    if test_framework == "pytest":
                        test_code = f"""
def test_{class_name}_{method_name}():
    \"\"\"Test {class_name}.{method_name} method\"\"\"
    # TODO: Create instance and test method
    # instance = {class_name}()
    # result = instance.{method_name}()
    # assert result is not None
    pass
"""
                    else:
                        test_code = f"""
def test_{class_name}_{method_name}(self):
    \"\"\"Test {class_name}.{method_name} method\"\"\"
    # TODO: Create instance and test method
    pass
"""
                    tests.append(test_code.strip())
        
        return tests
    
    def generate_integration_tests(self, components: List[Dict[str, Any]]) -> List[str]:
        """
        Generate integration tests for multiple components
        
        Args:
            components: List of component dictionaries
                - name: Component name
                - type: Component type (api, service, database, etc.)
                - endpoints: API endpoints (if applicable)
                - functions: Functions to test
        
        Returns:
            List of integration test functions
        """
        tests = []
        
        # Generate component interaction tests
        for i, comp1 in enumerate(components):
            for comp2 in components[i+1:]:
                test_code = f"""
def test_integration_{comp1['name']}_{comp2['name']}():
    \"\"\"Integration test: {comp1['name']} <-> {comp2['name']}\"\"\"
    # TODO: Test interaction between {comp1['name']} and {comp2['name']}
    # Setup components
    # Execute interaction
    # Verify results
    pass
"""
                tests.append(test_code.strip())
        
        # Generate end-to-end flow tests
        if len(components) > 2:
            flow_test = f"""
def test_e2e_flow():
    \"\"\"End-to-end test for complete flow\"\"\"
    # TODO: Test complete flow through all components
    # {' -> '.join([c['name'] for c in components])}
    pass
"""
            tests.append(flow_test.strip())
        
        return tests
    
    def generate_system_tests(self, system_config: Dict[str, Any]) -> List[str]:
        """
        Generate system tests based on system configuration
        
        Args:
            system_config: System configuration
                - services: List of services
                - endpoints: API endpoints
                - databases: Database connections
                - external_services: External service dependencies
        
        Returns:
            List of system test functions
        """
        tests = []
        
        # Service availability tests
        services = system_config.get("services", [])
        for service in services:
            test_code = f"""
def test_service_{service}_available():
    \"\"\"Test that {service} service is available\"\"\"
    # TODO: Check service availability
    # import requests
    # response = requests.get('http://{service}:port/health')
    # assert response.status_code == 200
    pass
"""
            tests.append(test_code.strip())
        
        # API endpoint tests
        endpoints = system_config.get("endpoints", [])
        for endpoint in endpoints:
            test_code = f"""
def test_endpoint_{endpoint['name']}():
    \"\"\"Test {endpoint['name']} endpoint\"\"\"
    # TODO: Test endpoint
    # method = '{endpoint.get('method', 'GET')}'
    # url = '{endpoint.get('url', '')}'
    # response = requests.request(method, url)
    # assert response.status_code in [200, 201]
    pass
"""
            tests.append(test_code.strip())
        
        # Database connectivity tests
        databases = system_config.get("databases", [])
        for db in databases:
            test_code = f"""
def test_database_{db['name']}_connection():
    \"\"\"Test {db['name']} database connection\"\"\"
    # TODO: Test database connection
    # import {db.get('driver', 'sqlite3')}
    # conn = {db.get('driver', 'sqlite3')}.connect({db.get('connection_string', '')})
    # assert conn is not None
    pass
"""
            tests.append(test_code.strip())
        
        return tests
    
    def generate_e2e_tests(self, user_flows: List[Dict[str, Any]]) -> List[str]:
        """
        Generate end-to-end tests from user flows
        
        Args:
            user_flows: List of user flow descriptions
                - name: Flow name
                - steps: List of steps in the flow
                - expected_result: Expected outcome
        
        Returns:
            List of E2E test functions
        """
        tests = []
        
        for flow in user_flows:
            flow_name = flow.get("name", "flow")
            steps = flow.get("steps", [])
            expected = flow.get("expected_result", "Success")
            
            test_code = f"""
def test_e2e_{flow_name}():
    \"\"\"End-to-end test: {flow_name}\"\"\"
    # Expected result: {expected}
    # Steps:
"""
            for i, step in enumerate(steps, 1):
                test_code += f"    # {i}. {step}\n"
            
            test_code += """
    # TODO: Implement E2E test
    # Execute each step
    # Verify results
    # Assert final state
    pass
"""
            tests.append(test_code.strip())
        
        return tests
    
    def analyze_test_coverage(self, code: str, tests: List[str]) -> Dict[str, Any]:
        """
        Analyze test coverage
        
        Args:
            code: Source code
            tests: List of test code strings
        
        Returns:
            Coverage analysis dictionary
        """
        try:
            code_tree = ast.parse(code)
        except SyntaxError:
            return {"error": "Syntax error in source code"}
        
        # Extract functions and classes from source
        source_functions = set()
        source_classes = set()
        
        for node in ast.walk(code_tree):
            if isinstance(node, ast.FunctionDef):
                source_functions.add(node.name)
            elif isinstance(node, ast.ClassDef):
                source_classes.add(node.name)
        
        # Extract tested functions from tests
        tested_functions = set()
        tested_classes = set()
        
        for test_code in tests:
            try:
                test_tree = ast.parse(test_code)
                for node in ast.walk(test_tree):
                    if isinstance(node, ast.FunctionDef):
                        func_name = node.name
                        if func_name.startswith('test_'):
                            # v0.5.245-followup (audit AI-*): the previous logic
                            # used str.replace('test_', '') (which strips every
                            # occurrence, so 'test_reset_test_state' became
                            # 'resetstate') and then split on '_' to guess
                            # class-vs-function -- misclassifying every
                            # multi-word test name (e.g. 'test_parse_config'
                            # was counted as class 'parse' + function 'config').
                            # Strip only the 'test_' prefix and treat the whole
                            # remainder as the tested-function name.
                            if hasattr(func_name, 'removeprefix'):
                                stripped = func_name.removeprefix('test_')
                            else:
                                stripped = func_name[5:]
                            if stripped:
                                tested_functions.add(stripped)
            except SyntaxError:
                continue
        
        # Calculate coverage
        function_coverage = len(tested_functions) / len(source_functions) * 100 if source_functions else 0
        class_coverage = len(tested_classes) / len(source_classes) * 100 if source_classes else 0
        
        return {
            "function_coverage": round(function_coverage, 2),
            "class_coverage": round(class_coverage, 2),
            "total_functions": len(source_functions),
            "tested_functions": len(tested_functions),
            "total_classes": len(source_classes),
            "tested_classes": len(tested_classes),
            "missing_functions": list(source_functions - tested_functions),
            "missing_classes": list(source_classes - tested_classes)
        }
    
    def optimize_tests(self, tests: List[str], coverage_target: float = 0.8) -> List[str]:
        """
        Optimize test suite
        
        Args:
            tests: List of test code strings
            coverage_target: Target coverage (0-1)
        
        Returns:
            Optimized test list
        """
        # For now, return tests as-is
        # In future, could:
        # - Remove duplicate tests
        # - Merge similar tests
        # - Prioritize high-value tests
        return tests
    
    def generate_test_suite(self, code: str, test_type: str = "unit",
                           options: Optional[Dict] = None) -> str:
        """
        Generate complete test suite
        
        Args:
            code: Source code
            test_type: Type of tests (unit, integration, system, e2e)
            options: Additional options
        
        Returns:
            Complete test suite as string
        """
        options = options or {}
        test_framework = options.get("framework", "pytest")
        
        if test_type == "unit":
            tests = self.generate_unit_tests(code, test_framework)
        elif test_type == "integration":
            components = options.get("components", [])
            tests = self.generate_integration_tests(components)
        elif test_type == "system":
            system_config = options.get("system_config", {})
            tests = self.generate_system_tests(system_config)
        elif test_type == "e2e":
            user_flows = options.get("user_flows", [])
            tests = self.generate_e2e_tests(user_flows)
        else:
            tests = []
        
        # Generate test suite header
        if test_framework == "pytest":
            header = """import pytest
import sys
import os

# Add source to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

"""
        else:
            header = """import unittest
import sys
import os

# Add source to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

class TestSuite(unittest.TestCase):
"""
        
        # Combine header and tests
        if test_framework == "pytest":
            suite = header + "\n\n".join(tests)
        else:
            # v0.5.245-followup (audit AI-*): the previous join used '\n    '
            # which only indented the FIRST line of each subsequent method --
            # every following line (docstring, body, subsequent statements) sat
            # at column 0, producing an unparseable class body. Indent every
            # line of every method by 4 spaces so each method sits inside the
            # TestSuite class definition.
            indent = "    "
            indented_tests = []
            for test_code in tests:
                indented_tests.append(
                    "\n".join(indent + line if line else line for line in test_code.splitlines())
                )
            suite = (
                header
                + "\n\n".join(indented_tests)
                + "\n\n\nif __name__ == '__main__':\n    unittest.main()"
            )

        return suite




