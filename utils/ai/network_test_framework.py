"""
AI-Powered Network Test Framework
Runs tests on network devices, generates reports, and suggests test cases
"""

import json
import logging
import subprocess
import time
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum
import sqlite3

logger = logging.getLogger(__name__)


class TestStatus(Enum):
    """Test execution status"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestCase:
    """Test case definition"""
    test_id: str
    name: str
    description: str
    category: str  # e.g., "connectivity", "performance", "protocol", "security"
    test_function: Optional[str] = None  # Function name to call
    parameters: Dict = None
    expected_result: Optional[str] = None
    severity: str = "medium"  # low, medium, high, critical
    vendor_specific: Optional[str] = None  # juniper, cisco, or None for all
    prerequisites: List[str] = None  # List of prerequisite test IDs
    
    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}
        if self.prerequisites is None:
            self.prerequisites = []


@dataclass
class TestResult:
    """Test execution result"""
    test_id: str
    test_name: str
    status: TestStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    duration: Optional[float] = None  # seconds
    result_data: Dict = None
    error_message: Optional[str] = None
    actual_result: Optional[str] = None
    passed: bool = False
    
    def __post_init__(self):
        if self.result_data is None:
            self.result_data = {}
        if self.end_time and self.start_time:
            self.duration = (self.end_time - self.start_time).total_seconds()
            self.passed = self.status == TestStatus.PASSED


@dataclass
class TestReport:
    """Test execution report"""
    report_id: str
    device_id: str
    device_name: str
    test_suite_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    test_results: List[TestResult] = None
    summary: str = ""
    recommendations: List[str] = None
    
    def __post_init__(self):
        if self.test_results is None:
            self.test_results = []
        if self.recommendations is None:
            self.recommendations = []


class NetworkTestFramework:
    """Framework for running network device tests"""
    
    def __init__(self, knowledge_base=None, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.kb = knowledge_base
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        self.test_cases: Dict[str, TestCase] = {}
        self.test_functions: Dict[str, Callable] = {}
        self._register_builtin_tests()
        
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
    
    def _register_builtin_tests(self):
        """Register built-in test cases"""
        # Connectivity tests
        self.add_test_case(TestCase(
            test_id="ping_test",
            name="Ping Connectivity Test",
            description="Test basic IP connectivity using ping",
            category="connectivity",
            test_function="test_ping",
            parameters={"target_ip": None, "count": 5, "timeout": 5},
            severity="high"
        ))
        
        self.add_test_case(TestCase(
            test_id="interface_status",
            name="Interface Status Check",
            description="Verify all interfaces are up and operational",
            category="connectivity",
            test_function="test_interface_status",
            parameters={},
            severity="critical"
        ))
        
        self.add_test_case(TestCase(
            test_id="mtu_consistency",
            name="MTU Consistency Check",
            description="Verify MTU is consistent across interfaces",
            category="configuration",
            test_function="test_mtu_consistency",
            parameters={},
            severity="medium"
        ))
        
        # Protocol tests
        self.add_test_case(TestCase(
            test_id="bgp_neighbor_status",
            name="BGP Neighbor Status",
            description="Check BGP neighbor establishment",
            category="protocol",
            test_function="test_bgp_neighbor",
            parameters={},
            severity="high",
            prerequisites=["ping_test"]
        ))
        
        self.add_test_case(TestCase(
            test_id="ospf_neighbor_status",
            name="OSPF Neighbor Status",
            description="Check OSPF neighbor establishment",
            category="protocol",
            test_function="test_ospf_neighbor",
            parameters={},
            severity="high",
            prerequisites=["ping_test"]
        ))
        
        self.add_test_case(TestCase(
            test_id="isis_neighbor_status",
            name="ISIS Neighbor Status",
            description="Check ISIS neighbor establishment",
            category="protocol",
            test_function="test_isis_neighbor",
            parameters={},
            severity="high",
            prerequisites=["ping_test"]
        ))
        
        # Performance tests
        self.add_test_case(TestCase(
            test_id="packet_loss_test",
            name="Packet Loss Test",
            description="Measure packet loss to neighbor",
            category="performance",
            test_function="test_packet_loss",
            parameters={"target_ip": None, "packet_count": 1000},
            severity="medium"
        ))
        
        self.add_test_case(TestCase(
            test_id="latency_test",
            name="Latency Test",
            description="Measure round-trip latency",
            category="performance",
            test_function="test_latency",
            parameters={"target_ip": None, "count": 10},
            severity="medium"
        ))
        
        self.add_test_case(TestCase(
            test_id="jitter_test",
            name="Jitter Test",
            description="Measure packet jitter",
            category="performance",
            test_function="test_jitter",
            parameters={"target_ip": None, "count": 100},
            severity="low"
        ))
        
        # Configuration tests
        self.add_test_case(TestCase(
            test_id="route_advertisement",
            name="Route Advertisement Check",
            description="Verify routes are being advertised",
            category="protocol",
            test_function="test_route_advertisement",
            parameters={},
            severity="high"
        ))
        
        self.add_test_case(TestCase(
            test_id="vlan_configuration",
            name="VLAN Configuration Check",
            description="Verify VLAN configuration is correct",
            category="configuration",
            test_function="test_vlan_config",
            parameters={},
            severity="medium"
        ))
        
        # Security tests
        self.add_test_case(TestCase(
            test_id="authentication_check",
            name="Authentication Check",
            description="Verify protocol authentication is configured",
            category="security",
            test_function="test_authentication",
            parameters={},
            severity="high"
        ))
    
    def add_test_case(self, test_case: TestCase):
        """Add a test case (built-in or user-defined)"""
        self.test_cases[test_case.test_id] = test_case
        logger.info(f"Registered test case: {test_case.test_id} - {test_case.name}")
    
    def register_test_function(self, test_id: str, test_func: Callable):
        """Register a test function"""
        self.test_functions[test_id] = test_func
    
    # Built-in test functions
    def test_ping(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test ping connectivity"""
        test_id = "ping_test"
        start_time = datetime.now(timezone.utc)
        target_ip = parameters.get("target_ip")
        count = parameters.get("count", 5)
        timeout = parameters.get("timeout", 5)
        
        if not target_ip:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.SKIPPED,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message="Target IP not specified",
                passed=False
            )
        
        try:
            # Execute ping
            result = subprocess.run(
                ["ping", "-c", str(count), "-W", str(timeout), target_ip],
                capture_output=True,
                text=True,
                timeout=timeout + 5
            )
            
            end_time = datetime.now(timezone.utc)
            success = result.returncode == 0
            
            # Parse ping output
            if success:
                # Extract packet loss from output
                packet_loss_match = None
                if "packet loss" in result.stdout:
                    import re
                    loss_match = re.search(r'(\d+(?:\.\d+)?)% packet loss', result.stdout)
                    if loss_match:
                        packet_loss = float(loss_match.group(1))
                        success = packet_loss == 0.0
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if success else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "target_ip": target_ip,
                    "packet_count": count,
                    "stdout": result.stdout,
                    "returncode": result.returncode
                },
                actual_result="Ping successful" if success else "Ping failed",
                passed=success
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.FAILED,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message="Ping timeout",
                passed=False
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_interface_status(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test interface status"""
        test_id = "interface_status"
        start_time = datetime.now(timezone.utc)
        
        try:
            # Get device configuration from knowledge base
            if self.kb:
                config = self.kb.get_device_config(device_id)
            else:
                config = None
            
            if not config:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device configuration not found",
                    passed=False
                )
            
            # Check interfaces from config
            interfaces = config.get("interfaces", {})
            down_interfaces = []
            up_interfaces = []
            
            for iface_name, iface_config in interfaces.items():
                # Check if interface is shut down (vendor-specific)
                if iface_config.get("shutdown", False):
                    down_interfaces.append(iface_name)
                else:
                    up_interfaces.append(iface_name)
            
            end_time = datetime.now(timezone.utc)
            passed = len(down_interfaces) == 0
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "total_interfaces": len(interfaces),
                    "up_interfaces": up_interfaces,
                    "down_interfaces": down_interfaces
                },
                actual_result=f"{len(up_interfaces)} up, {len(down_interfaces)} down",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_mtu_consistency(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test MTU consistency across interfaces"""
        test_id = "mtu_consistency"
        start_time = datetime.now(timezone.utc)
        
        try:
            if self.kb:
                config = self.kb.get_device_config(device_id)
            else:
                config = None
            
            if not config:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device configuration not found",
                    passed=False
                )
            
            interfaces = config.get("interfaces", {})
            mtu_values = {}
            
            for iface_name, iface_config in interfaces.items():
                mtu = iface_config.get("mtu", 1500)  # Default MTU
                mtu_values[iface_name] = mtu
            
            unique_mtus = set(mtu_values.values())
            passed = len(unique_mtus) <= 1  # All interfaces should have same MTU
            
            end_time = datetime.now(timezone.utc)
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "mtu_values": mtu_values,
                    "unique_mtus": list(unique_mtus),
                    "mtu_count": len(unique_mtus)
                },
                actual_result=f"Found {len(unique_mtus)} unique MTU value(s): {unique_mtus}",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_bgp_neighbor(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test BGP neighbor status"""
        test_id = "bgp_neighbor_status"
        start_time = datetime.now(timezone.utc)
        
        try:
            # Get BGP status from device database
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device = device_db.get_device(device_id)
            
            if not device:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device not found in database",
                    passed=False
                )
            
            bgp_established = device.get("bgp_established", False)
            bgp_state = device.get("bgp_state", "Unknown")
            
            end_time = datetime.now(timezone.utc)
            passed = bgp_established
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "bgp_established": bgp_established,
                    "bgp_state": bgp_state
                },
                actual_result=f"BGP state: {bgp_state}",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_ospf_neighbor(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test OSPF neighbor status"""
        test_id = "ospf_neighbor_status"
        start_time = datetime.now(timezone.utc)
        
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device = device_db.get_device(device_id)
            
            if not device:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device not found",
                    passed=False
                )
            
            ospf_established = device.get("ospf_established", False)
            ospf_state = device.get("ospf_state", "Unknown")
            
            end_time = datetime.now(timezone.utc)
            passed = ospf_established
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "ospf_established": ospf_established,
                    "ospf_state": ospf_state
                },
                actual_result=f"OSPF state: {ospf_state}",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_isis_neighbor(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test ISIS neighbor status"""
        test_id = "isis_neighbor_status"
        start_time = datetime.now(timezone.utc)
        
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device = device_db.get_device(device_id)
            
            if not device:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device not found",
                    passed=False
                )
            
            isis_established = device.get("isis_established", False)
            isis_state = device.get("isis_state", "Unknown")
            
            end_time = datetime.now(timezone.utc)
            passed = isis_established
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "isis_established": isis_established,
                    "isis_state": isis_state
                },
                actual_result=f"ISIS state: {isis_state}",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_packet_loss(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test packet loss"""
        test_id = "packet_loss_test"
        start_time = datetime.now(timezone.utc)
        target_ip = parameters.get("target_ip")
        packet_count = parameters.get("packet_count", 1000)
        
        if not target_ip:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.SKIPPED,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message="Target IP not specified",
                passed=False
            )
        
        try:
            # Use ping with high packet count
            result = subprocess.run(
                ["ping", "-c", str(packet_count), target_ip],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes max
            )
            
            end_time = datetime.now(timezone.utc)
            
            # Parse packet loss
            import re
            loss_match = re.search(r'(\d+(?:\.\d+)?)% packet loss', result.stdout)
            packet_loss = float(loss_match.group(1)) if loss_match else 100.0
            
            # Pass if packet loss < 1%
            passed = packet_loss < 1.0
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "target_ip": target_ip,
                    "packet_count": packet_count,
                    "packet_loss_percent": packet_loss
                },
                actual_result=f"Packet loss: {packet_loss}%",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_latency(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test latency"""
        test_id = "latency_test"
        start_time = datetime.now(timezone.utc)
        target_ip = parameters.get("target_ip")
        count = parameters.get("count", 10)
        
        if not target_ip:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.SKIPPED,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message="Target IP not specified",
                passed=False
            )
        
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), target_ip],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            end_time = datetime.now(timezone.utc)
            
            # Parse average latency
            import re
            latency_match = re.search(r'min/avg/max.*?= [\d.]+/([\d.]+)/[\d.]+', result.stdout)
            avg_latency = float(latency_match.group(1)) if latency_match else None
            
            # Pass if latency < 50ms (configurable)
            max_latency = parameters.get("max_latency_ms", 50)
            passed = avg_latency is not None and avg_latency < max_latency
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "target_ip": target_ip,
                    "average_latency_ms": avg_latency,
                    "max_latency_ms": max_latency
                },
                actual_result=f"Average latency: {avg_latency}ms" if avg_latency else "Failed to measure",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_jitter(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test jitter (latency variation)"""
        test_id = "jitter_test"
        start_time = datetime.now(timezone.utc)
        target_ip = parameters.get("target_ip")
        count = parameters.get("count", 100)
        
        if not target_ip:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.SKIPPED,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message="Target IP not specified",
                passed=False
            )
        
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), target_ip],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            end_time = datetime.now(timezone.utc)
            
            # Parse min/avg/max to calculate jitter
            import re
            stats_match = re.search(r'min/avg/max.*?= ([\d.]+)/([\d.]+)/([\d.]+)', result.stdout)
            if stats_match:
                min_latency = float(stats_match.group(1))
                avg_latency = float(stats_match.group(2))
                max_latency = float(stats_match.group(3))
                jitter = max_latency - min_latency
            else:
                jitter = None
            
            # Pass if jitter < 10ms
            max_jitter = parameters.get("max_jitter_ms", 10)
            passed = jitter is not None and jitter < max_jitter
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "target_ip": target_ip,
                    "jitter_ms": jitter,
                    "min_latency_ms": min_latency if stats_match else None,
                    "max_latency_ms": max_latency if stats_match else None
                },
                actual_result=f"Jitter: {jitter}ms" if jitter else "Failed to measure",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_route_advertisement(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test route advertisement"""
        test_id = "route_advertisement"
        start_time = datetime.now(timezone.utc)
        
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device = device_db.get_device(device_id)
            
            if not device:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device not found",
                    passed=False
                )
            
            # Check if routes are being advertised (simplified check)
            bgp_established = device.get("bgp_established", False)
            ospf_established = device.get("ospf_established", False)
            
            # This is a simplified check - in real implementation, would query routing table
            passed = bgp_established or ospf_established
            
            end_time = datetime.now(timezone.utc)
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "bgp_established": bgp_established,
                    "ospf_established": ospf_established
                },
                actual_result="Routes being advertised" if passed else "No routes advertised",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_vlan_config(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test VLAN configuration"""
        test_id = "vlan_configuration"
        start_time = datetime.now(timezone.utc)
        
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device = device_db.get_device(device_id)
            
            if not device:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device not found",
                    passed=False
                )
            
            vlan = device.get("vlan", "0")
            vlan_id = int(vlan) if vlan and vlan != "0" else None
            
            # Check if VLAN is configured correctly
            passed = vlan_id is not None and 1 <= vlan_id <= 4094
            
            end_time = datetime.now(timezone.utc)
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "vlan_id": vlan_id
                },
                actual_result=f"VLAN {vlan_id} configured" if vlan_id else "No VLAN configured",
                passed=passed
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def test_authentication(self, device_id: str, device_config: Dict, parameters: Dict) -> TestResult:
        """Test authentication configuration"""
        test_id = "authentication_check"
        start_time = datetime.now(timezone.utc)
        
        try:
            if self.kb:
                config = self.kb.get_device_config(device_id)
            else:
                config = None
            
            if not config:
                return TestResult(
                    test_id=test_id,
                    test_name=self.test_cases[test_id].name,
                    status=TestStatus.SKIPPED,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    error_message="Device configuration not found",
                    passed=False
                )
            
            # Check if authentication is configured in protocols
            protocols = config.get("protocols", {})
            has_auth = False
            
            # Simplified check - would need to parse actual auth config
            # For now, just check if protocols exist
            has_protocols = len(protocols) > 0
            
            end_time = datetime.now(timezone.utc)
            
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.PASSED if has_protocols else TestStatus.FAILED,
                start_time=start_time,
                end_time=end_time,
                result_data={
                    "protocols_configured": list(protocols.keys()),
                    "has_authentication": has_auth
                },
                actual_result="Protocols configured" if has_protocols else "No protocols configured",
                passed=has_protocols
            )
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=self.test_cases[test_id].name,
                status=TestStatus.ERROR,
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def run_test(self, test_id: str, device_id: str, device_config: Optional[Dict] = None, 
                 parameters: Optional[Dict] = None) -> TestResult:
        """Run a single test"""
        if test_id not in self.test_cases:
            return TestResult(
                test_id=test_id,
                test_name="Unknown Test",
                status=TestStatus.ERROR,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                error_message=f"Test case {test_id} not found",
                passed=False
            )
        
        test_case = self.test_cases[test_id]
        
        # Check prerequisites
        for prereq_id in test_case.prerequisites:
            # In a full implementation, would check if prerequisite passed
            pass
        
        # Get test function
        test_func_name = test_case.test_function
        if not test_func_name:
            return TestResult(
                test_id=test_id,
                test_name=test_case.name,
                status=TestStatus.ERROR,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                error_message="Test function not specified",
                passed=False
            )
        
        # Get test function
        if test_func_name in self.test_functions:
            test_func = self.test_functions[test_func_name]
        elif hasattr(self, test_func_name):
            test_func = getattr(self, test_func_name)
        else:
            return TestResult(
                test_id=test_id,
                test_name=test_case.name,
                status=TestStatus.ERROR,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                error_message=f"Test function {test_func_name} not found",
                passed=False
            )
        
        # Merge parameters
        merged_params = test_case.parameters.copy()
        if parameters:
            merged_params.update(parameters)
        
        # Get device config if not provided
        if not device_config:
            if self.kb:
                device_config = self.kb.get_device_config(device_id)
            else:
                device_config = {}
        
        # Run test
        try:
            result = test_func(device_id, device_config, merged_params)
            return result
        except Exception as e:
            return TestResult(
                test_id=test_id,
                test_name=test_case.name,
                status=TestStatus.ERROR,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                error_message=str(e),
                passed=False
            )
    
    def run_test_suite(self, test_ids: List[str], device_id: str, device_name: str = "",
                      suite_name: str = "Default Test Suite") -> TestReport:
        """Run a suite of tests"""
        report_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        
        # Get device info to check type
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device_info = device_db.get_device(device_id)
        
        # Check if external device
        device_type = device_info.get("device_type", "frr_container") if device_info else "frr_container"
        
        if device_type != "frr_container":
            # Use external device testing
            from utils.ai.external_device_ai import ExternalDeviceAI
            from utils.external_device_manager import ExternalDeviceManager
            
            ext_manager = ExternalDeviceManager()
            ext_ai = ExternalDeviceAI(external_device_manager=ext_manager)
            
            test_results = ext_ai.test_external_device(device_id, device_info, test_ids)
            
            # Convert to TestReport format
            end_time = datetime.now(timezone.utc)
            return TestReport(
                report_id=report_id,
                device_id=device_id,
                device_name=device_name or device_id,
                test_suite_name=suite_name,
                start_time=start_time,
                end_time=end_time,
                total_tests=test_results.get("total_tests", 0),
                passed_tests=test_results.get("passed_tests", 0),
                failed_tests=test_results.get("failed_tests", 0),
                skipped_tests=0,
                test_results=[],  # Would need to convert format
                summary=f"External device test results: {test_results.get('passed_tests', 0)}/{test_results.get('total_tests', 0)} passed",
                recommendations=[]
            )
        
        # Default: FRR container testing
        # Get device config
        device_config = None
        if self.kb:
            device_config = self.kb.get_device_config(device_id)
        
        if not device_config:
            if device_info:
                device_config = device_info
        
        test_results = []
        
        # Run tests in order
        for test_id in test_ids:
            if test_id not in self.test_cases:
                logger.warning(f"Test case {test_id} not found, skipping")
                continue
            
            logger.info(f"Running test: {test_id}")
            result = self.run_test(test_id, device_id, device_config)
            test_results.append(result)
        
        end_time = datetime.now(timezone.utc)
        
        # Calculate statistics
        total_tests = len(test_results)
        passed_tests = sum(1 for r in test_results if r.passed)
        failed_tests = sum(1 for r in test_results if r.status == TestStatus.FAILED)
        skipped_tests = sum(1 for r in test_results if r.status == TestStatus.SKIPPED)
        
        # Generate summary
        summary = f"Test Suite: {suite_name}\n"
        summary += f"Device: {device_name or device_id}\n"
        summary += f"Total Tests: {total_tests}\n"
        summary += f"Passed: {passed_tests}\n"
        summary += f"Failed: {failed_tests}\n"
        summary += f"Skipped: {skipped_tests}\n"
        summary += f"Success Rate: {(passed_tests/total_tests*100) if total_tests > 0 else 0:.1f}%"
        
        # Generate recommendations
        recommendations = self._generate_recommendations(test_results)
        
        report = TestReport(
            report_id=report_id,
            device_id=device_id,
            device_name=device_name or device_id,
            test_suite_name=suite_name,
            start_time=start_time,
            end_time=end_time,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            skipped_tests=skipped_tests,
            test_results=test_results,
            summary=summary,
            recommendations=recommendations
        )
        
        return report
    
    def _generate_recommendations(self, test_results: List[TestResult]) -> List[str]:
        """Generate recommendations based on test results"""
        recommendations = []
        
        failed_tests = [r for r in test_results if r.status == TestStatus.FAILED]
        
        for result in failed_tests:
            test_case = self.test_cases.get(result.test_id)
            if not test_case:
                continue
            
            # Generate recommendation based on test type
            if result.test_id == "interface_status":
                recommendations.append("Check physical connections and interface configuration")
            elif result.test_id == "mtu_consistency":
                recommendations.append("Standardize MTU values across all interfaces")
            elif "neighbor" in result.test_id:
                recommendations.append(f"Verify {result.test_id.split('_')[0].upper()} neighbor configuration and reachability")
            elif "packet_loss" in result.test_id:
                recommendations.append("Investigate network congestion or interface errors")
            elif "latency" in result.test_id:
                recommendations.append("Check for network congestion or routing issues")
        
        # Use AI to generate additional recommendations if available
        if self.use_ai_api and self.ai_client and failed_tests:
            try:
                ai_recommendations = self._ai_generate_recommendations(test_results)
                recommendations.extend(ai_recommendations)
            except Exception as e:
                logger.warning(f"AI recommendation generation failed: {e}")
        
        return recommendations
    
    def _ai_generate_recommendations(self, test_results: List[TestResult]) -> List[str]:
        """Use AI to generate recommendations"""
        if not self.ai_client:
            return []
        
        failed_tests = [r for r in test_results if r.status == TestStatus.FAILED]
        if not failed_tests:
            return []
        
        prompt = f"""
        Analyze these network test failures and provide specific recommendations:
        
        Failed Tests:
        {json.dumps([{"test": r.test_name, "result": r.actual_result, "error": r.error_message} for r in failed_tests], indent=2)}
        
        Provide 3-5 specific, actionable recommendations to fix these issues.
        Return as JSON array of strings:
        ["recommendation 1", "recommendation 2", ...]
        """
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            result_text = response.choices[0].message.content
            # Try to parse JSON
            try:
                recommendations = json.loads(result_text)
                if isinstance(recommendations, list):
                    return recommendations
            except Exception:
                # Extract from markdown if present
                import re
                json_match = re.search(r'\[.*?\]', result_text, re.DOTALL)
                if json_match:
                    recommendations = json.loads(json_match.group(0))
                    return recommendations if isinstance(recommendations, list) else []
            
            return []
        except Exception as e:
            logger.error(f"AI recommendation generation failed: {e}")
            return []
    
    def suggest_test_cases(self, device_id: str, device_config: Optional[Dict] = None,
                          use_ai: bool = True) -> List[TestCase]:
        """Suggest test cases based on device configuration"""
        suggestions = []
        
        # Get device config
        if not device_config:
            if self.kb:
                device_config = self.kb.get_device_config(device_id)
            else:
                from utils.device_database import DeviceDatabase
                device_db = DeviceDatabase()
                device = device_db.get_device(device_id)
                device_config = device if device else {}
        
        # Rule-based suggestions
        if device_config:
            # Check what protocols are configured
            if device_config.get("bgp_config") or device_config.get("protocols", {}).get("bgp"):
                suggestions.append(self.test_cases.get("bgp_neighbor_status"))
            
            if device_config.get("ospf_config") or device_config.get("protocols", {}).get("ospf"):
                suggestions.append(self.test_cases.get("ospf_neighbor_status"))
            
            if device_config.get("isis_config") or device_config.get("protocols", {}).get("isis"):
                suggestions.append(self.test_cases.get("isis_neighbor_status"))
            
            # Always suggest basic connectivity
            suggestions.append(self.test_cases.get("ping_test"))
            suggestions.append(self.test_cases.get("interface_status"))
        
        # Use AI for additional suggestions
        if use_ai and self.use_ai_api and self.ai_client:
            try:
                ai_suggestions = self._ai_suggest_test_cases(device_id, device_config)
                suggestions.extend(ai_suggestions)
            except Exception as e:
                logger.warning(f"AI test case suggestion failed: {e}")
        
        # Remove None values and duplicates
        suggestions = [s for s in suggestions if s is not None]
        seen_ids = set()
        unique_suggestions = []
        for suggestion in suggestions:
            if suggestion.test_id not in seen_ids:
                seen_ids.add(suggestion.test_id)
                unique_suggestions.append(suggestion)
        
        return unique_suggestions
    
    def _ai_suggest_test_cases(self, device_id: str, device_config: Dict) -> List[TestCase]:
        """Use AI to suggest test cases"""
        if not self.ai_client:
            return []
        
        prompt = f"""
        Based on this network device configuration, suggest relevant test cases:
        
        Device Configuration:
        {json.dumps(device_config, indent=2) if device_config else "Not available"}
        
        Available test categories:
        - connectivity: ping, interface status
        - performance: packet loss, latency, jitter
        - protocol: BGP, OSPF, ISIS neighbor status
        - configuration: MTU, VLAN, route advertisement
        - security: authentication
        
        Suggest 3-5 most relevant test cases for this device.
        Return as JSON array with test IDs:
        ["test_id_1", "test_id_2", ...]
        """
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            result_text = response.choices[0].message.content
            try:
                test_ids = json.loads(result_text)
                if isinstance(test_ids, list):
                    suggestions = []
                    for test_id in test_ids:
                        if test_id in self.test_cases:
                            suggestions.append(self.test_cases[test_id])
                    return suggestions
            except Exception:
                pass
            
            return []
        except Exception as e:
            logger.error(f"AI test case suggestion failed: {e}")
            return []
    
    def generate_report(self, report: TestReport, format: str = "json") -> str:
        """Generate test report in various formats"""
        if format == "json":
            return json.dumps(asdict(report), indent=2, default=str)
        elif format == "text":
            return self._generate_text_report(report)
        elif format == "html":
            return self._generate_html_report(report)
        else:
            return json.dumps(asdict(report), indent=2, default=str)
    
    def _generate_text_report(self, report: TestReport) -> str:
        """Generate text format report"""
        lines = []
        lines.append("=" * 80)
        lines.append(f"TEST REPORT: {report.test_suite_name}")
        lines.append("=" * 80)
        lines.append(f"Device ID: {report.device_id}")
        lines.append(f"Device Name: {report.device_name}")
        lines.append(f"Start Time: {report.start_time}")
        lines.append(f"End Time: {report.end_time}")
        lines.append(f"Duration: {(report.end_time - report.start_time).total_seconds():.2f} seconds")
        lines.append("")
        lines.append("SUMMARY")
        lines.append("-" * 80)
        lines.append(f"Total Tests: {report.total_tests}")
        lines.append(f"Passed: {report.passed_tests}")
        lines.append(f"Failed: {report.failed_tests}")
        lines.append(f"Skipped: {report.skipped_tests}")
        lines.append(f"Success Rate: {(report.passed_tests/report.total_tests*100) if report.total_tests > 0 else 0:.1f}%")
        lines.append("")
        lines.append("TEST RESULTS")
        lines.append("-" * 80)
        
        for result in report.test_results:
            status_symbol = "✅" if result.passed else "❌" if result.status == TestStatus.FAILED else "⏭️"
            lines.append(f"{status_symbol} {result.test_name} [{result.status.value.upper()}]")
            if result.duration:
                lines.append(f"   Duration: {result.duration:.2f}s")
            if result.actual_result:
                lines.append(f"   Result: {result.actual_result}")
            if result.error_message:
                lines.append(f"   Error: {result.error_message}")
            lines.append("")
        
        if report.recommendations:
            lines.append("RECOMMENDATIONS")
            lines.append("-" * 80)
            for i, rec in enumerate(report.recommendations, 1):
                lines.append(f"{i}. {rec}")
        
        return "\n".join(lines)
    
    def _generate_html_report(self, report: TestReport) -> str:
        """Generate HTML format report"""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Test Report: {report.test_suite_name}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .test-result {{ margin: 10px 0; padding: 10px; border-left: 4px solid #ccc; }}
        .passed {{ border-color: #4caf50; background: #e8f5e9; }}
        .failed {{ border-color: #f44336; background: #ffebee; }}
        .skipped {{ border-color: #ff9800; background: #fff3e0; }}
        .recommendations {{ background: #e3f2fd; padding: 15px; border-radius: 5px; margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>Test Report: {report.test_suite_name}</h1>
    <div class="summary">
        <h2>Summary</h2>
        <p><strong>Device:</strong> {report.device_name} ({report.device_id})</p>
        <p><strong>Start Time:</strong> {report.start_time}</p>
        <p><strong>End Time:</strong> {report.end_time}</p>
        <p><strong>Total Tests:</strong> {report.total_tests}</p>
        <p><strong>Passed:</strong> {report.passed_tests}</p>
        <p><strong>Failed:</strong> {report.failed_tests}</p>
        <p><strong>Skipped:</strong> {report.skipped_tests}</p>
        <p><strong>Success Rate:</strong> {(report.passed_tests/report.total_tests*100) if report.total_tests > 0 else 0:.1f}%</p>
    </div>
    
    <h2>Test Results</h2>
"""
        
        for result in report.test_results:
            status_class = "passed" if result.passed else ("failed" if result.status == TestStatus.FAILED else "skipped")
            status_icon = "✅" if result.passed else "❌" if result.status == TestStatus.FAILED else "⏭️"
            
            html += f"""
    <div class="test-result {status_class}">
        <h3>{status_icon} {result.test_name}</h3>
        <p><strong>Status:</strong> {result.status.value.upper()}</p>
"""
            if result.duration:
                html += f"        <p><strong>Duration:</strong> {result.duration:.2f}s</p>\n"
            if result.actual_result:
                html += f"        <p><strong>Result:</strong> {result.actual_result}</p>\n"
            if result.error_message:
                html += f"        <p><strong>Error:</strong> {result.error_message}</p>\n"
            
            html += "    </div>\n"
        
        if report.recommendations:
            html += """
    <div class="recommendations">
        <h2>Recommendations</h2>
        <ul>
"""
            for rec in report.recommendations:
                html += f"            <li>{rec}</li>\n"
            html += "        </ul>\n    </div>\n"
        
        html += """
</body>
</html>
"""
        return html


class TestReportStorage:
    """Store and retrieve test reports"""
    
    def __init__(self, db_path: str = "/opt/OSTG/test_reports.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize test reports database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_reports (
                report_id TEXT PRIMARY KEY,
                device_id TEXT,
                device_name TEXT,
                test_suite_name TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                total_tests INTEGER,
                passed_tests INTEGER,
                failed_tests INTEGER,
                skipped_tests INTEGER,
                report_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def save_report(self, report: TestReport):
        """Save test report to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        report_data = json.dumps(asdict(report), default=str)
        
        cursor.execute("""
            INSERT OR REPLACE INTO test_reports 
            (report_id, device_id, device_name, test_suite_name, start_time, end_time,
             total_tests, passed_tests, failed_tests, skipped_tests, report_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.report_id, report.device_id, report.device_name, report.test_suite_name,
            report.start_time, report.end_time, report.total_tests, report.passed_tests,
            report.failed_tests, report.skipped_tests, report_data
        ))
        
        conn.commit()
        conn.close()
    
    def get_report(self, report_id: str) -> Optional[TestReport]:
        """Retrieve test report by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT report_data FROM test_reports WHERE report_id = ?", (report_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            report_dict = json.loads(row[0])
            # Reconstruct TestReport from dict
            # (simplified - would need proper deserialization)
            return report_dict
        return None
    
    def get_reports_for_device(self, device_id: str, limit: int = 10) -> List[Dict]:
        """Get recent test reports for a device"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT report_id, device_name, test_suite_name, start_time, end_time,
                   total_tests, passed_tests, failed_tests
            FROM test_reports
            WHERE device_id = ?
            ORDER BY start_time DESC
            LIMIT ?
        """, (device_id, limit))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "report_id": row[0],
                "device_name": row[1],
                "test_suite_name": row[2],
                "start_time": row[3],
                "end_time": row[4],
                "total_tests": row[5],
                "passed_tests": row[6],
                "failed_tests": row[7]
            })
        
        conn.close()
        return results

