"""
AI-Powered Pytest Script Generator
Generates ready-to-use pytest scripts for network device testing
"""

import ast
import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


# v0.5.245-followup (audit AI-*): safe literal helper — user-supplied test_params
# values MUST NOT be interpolated raw into generated Python source (code injection
# risk + breakage on quotes/backslashes). Use `repr()` for strings so they land as
# safely-quoted Python literals, and validate types for everything else.
def _py_literal(value: Any, *, allow: tuple = (str, int, float, bool, type(None))) -> str:
    """Return `value` as a Python-source literal, or raise ValueError.

    - Strings pass through `repr()` (produces valid, quote-safe literal).
    - Numeric/bool/None are validated by type and rendered with `repr()`.
    - Lists/tuples of the above are recursively rendered.
    - Anything else is rejected rather than silently interpolated.
    """
    if isinstance(value, bool):
        # bool is a subclass of int; check first
        return repr(value)
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_py_literal(v, allow=allow) for v in value)
        return "[" + inner + "]" if isinstance(value, list) else "(" + inner + ")"
    if isinstance(value, allow):
        return repr(value)
    raise ValueError(
        f"Refusing to interpolate value of type {type(value).__name__} into generated source"
    )


class PytestGenerator:
    """Generate pytest scripts for network testing"""
    
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
    
    def generate_pytest_script(self, test_requirements: Dict[str, Any], 
                              device_config: Optional[Dict] = None) -> str:
        """
        Generate a pytest script based on requirements
        
        Args:
            test_requirements: Dictionary with test requirements
                - test_name: Name of the test
                - test_type: Type of test (connectivity, performance, protocol, etc.)
                - device_id: Device ID to test
                - test_params: Test parameters
                - assertions: Expected assertions
            device_config: Optional device configuration
        
        Returns:
            Complete pytest script as string
        """
        test_name = test_requirements.get("test_name", "test_network_device")
        test_type = test_requirements.get("test_type", "connectivity")
        device_id = test_requirements.get("device_id", "device-123")
        test_params = test_requirements.get("test_params", {})
        assertions = test_requirements.get("assertions", [])
        
        # Use AI if available, otherwise use template
        if self.use_ai_api and self.ai_client:
            return self._ai_generate_pytest_script(test_requirements, device_config)
        else:
            return self._template_generate_pytest_script(
                test_name, test_type, device_id, test_params, assertions, device_config
            )
    
    def _template_generate_pytest_script(self, test_name: str, test_type: str,
                                         device_id: str, test_params: Dict,
                                         assertions: List[str],
                                         device_config: Optional[Dict]) -> str:
        """Generate pytest script from template"""
        
        # Base imports
        imports = """import pytest
import subprocess
import time
import json
from typing import Dict, Optional
from utils.device_database import DeviceDatabase
from utils.ai import NetworkTestFramework, ConfigKnowledgeBase
"""
        
        # Fixtures
        fixtures = f"""
@pytest.fixture(scope="module")
def device_id():
    return "{device_id}"

@pytest.fixture(scope="module")
def device_config():
    device_db = DeviceDatabase()
    device = device_db.get_device("{device_id}")
    return device

@pytest.fixture(scope="module")
def test_framework():
    kb = ConfigKnowledgeBase()
    return NetworkTestFramework(knowledge_base=kb)
"""
        
        # Test functions based on type
        test_functions = self._generate_test_functions(test_type, test_params, assertions)
        
        # Combine into complete script
        script = f"""{imports}

# Test Configuration
TEST_DEVICE_ID = "{device_id}"
TEST_TYPE = "{test_type}"

{fixtures}

{test_functions}
"""
        
        return script
    
    def _generate_test_functions(self, test_type: str, test_params: Dict,
                                assertions: List[str]) -> str:
        """Generate test functions based on test type"""
        
        if test_type == "connectivity":
            return self._generate_connectivity_tests(test_params, assertions)
        elif test_type == "performance":
            return self._generate_performance_tests(test_params, assertions)
        elif test_type == "protocol":
            return self._generate_protocol_tests(test_params, assertions)
        elif test_type == "configuration":
            return self._generate_configuration_tests(test_params, assertions)
        else:
            return self._generate_generic_tests(test_params, assertions)
    
    def _generate_connectivity_tests(self, test_params: Dict, assertions: List[str]) -> str:
        """Generate connectivity test functions"""
        # v0.5.245-followup (audit AI-*): render values as safe Python literals
        target_ip_lit = _py_literal(test_params.get("target_ip", "192.168.1.1"))

        return f"""
def test_ping_connectivity(device_id, device_config):
    \"\"\"Test basic ping connectivity\"\"\"
    target_ip = {target_ip_lit}

    result = subprocess.run(
        ["ping", "-c", "5", target_ip],
        capture_output=True,
        text=True,
        timeout=10
    )

    assert result.returncode == 0, f"Ping to {{target_ip}} failed"
    assert "0% packet loss" in result.stdout, "Packet loss detected"

def test_interface_status(device_id, device_config):
    \"\"\"Test interface status\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    interface = device_config.get("interface")
    assert interface is not None, "Interface not configured"
    
    # Check interface is up (simplified - would need actual interface check)
    assert interface != "", "Interface name is empty"

def test_mtu_consistency(device_id, device_config):
    \"\"\"Test MTU consistency\"\"\"
    # This would check MTU across interfaces
    # Simplified for template
    pass
"""
    
    def _generate_performance_tests(self, test_params: Dict, assertions: List[str]) -> str:
        """Generate performance test functions"""
        # v0.5.245-followup (audit AI-*): render values as safe Python literals
        target_ip_lit = _py_literal(test_params.get("target_ip", "192.168.1.1"))
        max_latency_lit = _py_literal(test_params.get("max_latency_ms", 50))
        max_packet_loss_lit = _py_literal(test_params.get("max_packet_loss_percent", 1.0))

        # v0.5.245-followup (audit AI-*): BSD/macOS ping and non-English locales
        # produce output the Linux-style regexes don't match. Previously the
        # assertions lived inside `if <regex>_match:` so the test PASSED silently
        # even at 100% packet loss. Now we try alternate patterns and fail
        # explicitly if none match — never accept unparsed output as success.
        return f"""
def test_latency(device_id, device_config):
    \"\"\"Test network latency\"\"\"
    target_ip = {target_ip_lit}
    max_latency_ms = {max_latency_lit}

    result = subprocess.run(
        ["ping", "-c", "10", target_ip],
        capture_output=True,
        text=True,
        timeout=30
    )

    assert result.returncode == 0, "Ping failed"

    # Parse average latency (Linux "min/avg/max/mdev" and BSD "min/avg/max/stddev")
    import re
    latency_match = (
        re.search(r'min/avg/max.*?= [\\d.]+/([\\d.]+)/[\\d.]+', result.stdout)
        or re.search(r'round-trip min/avg/max.*?= [\\d.]+/([\\d.]+)/[\\d.]+', result.stdout)
    )
    if not latency_match:
        pytest.fail(
            f"Could not parse ping output - SLA verification skipped. "
            f"Raw: {{result.stdout[:500]!r}}"
        )
    avg_latency = float(latency_match.group(1))
    assert avg_latency < max_latency_ms, f"Latency {{avg_latency}}ms exceeds max {{max_latency_ms}}ms"

def test_packet_loss(device_id, device_config):
    \"\"\"Test packet loss\"\"\"
    target_ip = {target_ip_lit}
    max_loss_percent = {max_packet_loss_lit}

    result = subprocess.run(
        ["ping", "-c", "100", target_ip],
        capture_output=True,
        text=True,
        timeout=120
    )

    # Parse packet loss
    import re
    loss_match = re.search(r'(\\d+(?:\\.\\d+)?)% packet loss', result.stdout)
    if not loss_match:
        pytest.fail(
            f"Could not parse ping output - packet-loss SLA skipped. "
            f"Raw: {{result.stdout[:500]!r}}"
        )
    packet_loss = float(loss_match.group(1))
    assert packet_loss < max_loss_percent, f"Packet loss {{packet_loss}}% exceeds max {{max_loss_percent}}%"

def test_jitter(device_id, device_config):
    \"\"\"Test packet jitter\"\"\"
    target_ip = {target_ip_lit}

    result = subprocess.run(
        ["ping", "-c", "100", target_ip],
        capture_output=True,
        text=True,
        timeout=120
    )

    # Parse jitter (min/max spread)
    import re
    stats_match = (
        re.search(r'min/avg/max.*?= ([\\d.]+)/([\\d.]+)/([\\d.]+)', result.stdout)
        or re.search(r'round-trip min/avg/max.*?= ([\\d.]+)/([\\d.]+)/([\\d.]+)', result.stdout)
    )
    if not stats_match:
        pytest.fail(
            f"Could not parse ping output - jitter SLA skipped. "
            f"Raw: {{result.stdout[:500]!r}}"
        )
    min_latency = float(stats_match.group(1))
    max_latency = float(stats_match.group(3))
    jitter = max_latency - min_latency
    assert jitter < 10, f"Jitter {{jitter}}ms is too high"
"""
    
    def _generate_protocol_tests(self, test_params: Dict, assertions: List[str]) -> str:
        """Generate protocol test functions"""
        protocols = test_params.get("protocols", ["bgp", "ospf"])
        
        test_functions = []
        for protocol in protocols:
            if protocol.lower() == "bgp":
                test_functions.append("""
def test_bgp_neighbor_status(device_id, device_config):
    \"\"\"Test BGP neighbor status\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    bgp_established = device_config.get("bgp_established", False)
    assert bgp_established, "BGP neighbor is not established"
    
    bgp_state = device_config.get("bgp_state", "Unknown")
    assert bgp_state == "Established", f"BGP state is {{bgp_state}}, expected Established"
""")
            elif protocol.lower() == "ospf":
                test_functions.append("""
def test_ospf_neighbor_status(device_id, device_config):
    \"\"\"Test OSPF neighbor status\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    ospf_established = device_config.get("ospf_established", False)
    assert ospf_established, "OSPF neighbor is not established"
    
    ospf_state = device_config.get("ospf_state", "Unknown")
    assert ospf_state in ["Full", "2-Way"], f"OSPF state is {{ospf_state}}, expected Full or 2-Way"
""")
            elif protocol.lower() == "isis":
                test_functions.append("""
def test_isis_neighbor_status(device_id, device_config):
    \"\"\"Test ISIS neighbor status\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    isis_established = device_config.get("isis_established", False)
    assert isis_established, "ISIS neighbor is not established"
""")
        
        return "\n".join(test_functions) if test_functions else """
def test_protocol_status(device_id, device_config):
    \"\"\"Generic protocol test\"\"\"
    assert device_config is not None, "Device configuration not found"
    pass
"""
    
    def _generate_configuration_tests(self, test_params: Dict, assertions: List[str]) -> str:
        """Generate configuration test functions"""
        return """
def test_mtu_consistency(device_id, device_config):
    \"\"\"Test MTU consistency across interfaces\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    # Check MTU values (simplified)
    # In real implementation, would check all interfaces
    pass

def test_vlan_configuration(device_id, device_config):
    \"\"\"Test VLAN configuration\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    vlan = device_config.get("vlan", "0")
    vlan_id = int(vlan) if vlan and vlan != "0" else None
    
    if vlan_id:
        assert 1 <= vlan_id <= 4094, f"Invalid VLAN ID: {vlan_id}"

def test_interface_configuration(device_id, device_config):
    \"\"\"Test interface configuration\"\"\"
    assert device_config is not None, "Device configuration not found"
    
    interface = device_config.get("interface")
    assert interface is not None and interface != "", "Interface not configured"
    
    ipv4 = device_config.get("ipv4_address")
    assert ipv4 is not None and ipv4 != "", "IPv4 address not configured"
"""
    
    def _generate_generic_tests(self, test_params: Dict, assertions: List[str]) -> str:
        """Generate generic test functions"""
        return """
def test_device_connectivity(device_id, device_config):
    \"\"\"Generic device connectivity test\"\"\"
    assert device_config is not None, "Device configuration not found"
    assert device_id is not None, "Device ID is required"
    pass
"""
    
    def _ai_generate_pytest_script(self, test_requirements: Dict,
                                   device_config: Optional[Dict]) -> str:
        """Use AI to generate pytest script"""
        if not self.ai_client:
            return self._template_generate_pytest_script(
                test_requirements.get("test_name", "test_network"),
                test_requirements.get("test_type", "connectivity"),
                test_requirements.get("device_id", "device-123"),
                test_requirements.get("test_params", {}),
                test_requirements.get("assertions", []),
                device_config
            )
        
        prompt = f"""
Generate a complete pytest script for network device testing.

Test Requirements:
{json.dumps(test_requirements, indent=2)}

Device Configuration:
{json.dumps(device_config, indent=2) if device_config else "Not provided"}

Requirements:
1. Use pytest framework
2. Include proper fixtures for device_id and device_config
3. Import necessary modules (subprocess, DeviceDatabase, NetworkTestFramework, etc.)
4. Write test functions with descriptive names and docstrings
5. Include assertions based on test requirements
6. Handle errors gracefully
7. Use the device_id and device_config fixtures

Generate a complete, ready-to-run pytest script.
"""
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert Python developer specializing in pytest and network testing. Generate complete, production-ready pytest scripts."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            
            script = response.choices[0].message.content
            
            # Extract code from markdown if present
            import re
            code_match = re.search(r'```python\n(.*?)\n```', script, re.DOTALL)
            if code_match:
                script = code_match.group(1)
            else:
                code_match = re.search(r'```\n(.*?)\n```', script, re.DOTALL)
                if code_match:
                    script = code_match.group(1)
            
            return script
        except Exception as e:
            logger.error(f"AI pytest generation failed: {e}")
            return self._template_generate_pytest_script(
                test_requirements.get("test_name", "test_network"),
                test_requirements.get("test_type", "connectivity"),
                test_requirements.get("device_id", "device-123"),
                test_requirements.get("test_params", {}),
                test_requirements.get("assertions", []),
                device_config
            )
    
    def generate_from_test_case(self, test_case: Any, device_id: str) -> str:
        """Generate pytest script from a TestCase object"""
        test_requirements = {
            "test_name": f"test_{test_case.test_id}",
            "test_type": test_case.category,
            "device_id": device_id,
            "test_params": test_case.parameters,
            "assertions": [test_case.expected_result] if test_case.expected_result else []
        }
        
        return self.generate_pytest_script(test_requirements)
    
    def save_pytest_script(self, script: str, file_path: str) -> bool:
        """Save pytest script to file"""
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w') as f:
                f.write(script)
            logger.info(f"Saved pytest script to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save pytest script: {e}")
            return False


# v0.5.245-followup (audit AI-*): quote-injection round-trip check
def _selftest_quote_injection() -> None:
    """Show that a target_ip with an embedded quote survives round-trip via ast.parse.

    Before the fix, `target_ip = "{target_ip}"` would splat the raw value into
    the generated source, breaking the string literal (or worse, injecting code).
    After the fix, values pass through repr() so the generated source is always
    a valid Python literal.

    Invoke manually: `python -c "from utils.ai.pytest_generator import _selftest_quote_injection; _selftest_quote_injection()"`
    """
    gen = PytestGenerator()
    hostile = 'evil"; import os; os.system("touch /tmp/pwned"); x = "'
    script = gen.generate_pytest_script({
        "test_name": "test_x",
        "test_type": "connectivity",
        "device_id": "dev-1",
        "test_params": {"target_ip": hostile},
    })
    # Must parse cleanly — if repr() weren't used the injected code would either
    # execute at parse time or produce a SyntaxError.
    ast.parse(script)
    # And the hostile string must survive verbatim (as a literal) in the source.
    assert repr(hostile) in script, "hostile value did not appear as a repr() literal"
    # Also confirm the injected system call isn't sitting outside of a string.
    tree = ast.parse(script)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "system":
                raise AssertionError("Injected os.system() call landed in generated AST")




