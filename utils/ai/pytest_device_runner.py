"""
Pytest Device Runner
Execute pytest scripts against external network devices (Cisco, Juniper, Arista, etc.)
"""

import logging
import subprocess
import json
import tempfile
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class PytestDeviceRunner:
    """Execute pytest scripts against external network devices"""
    
    def __init__(self):
        # Initialize external device manager
        try:
            from utils.external_device_manager import ExternalDeviceManager
            self.external_manager = ExternalDeviceManager()
        except Exception as e:
            logger.warning(f"External device manager not available: {e}")
            self.external_manager = None
        
        # Initialize device database
        try:
            from utils.device_database import DeviceDatabase
            self.device_db = DeviceDatabase()
        except Exception as e:
            logger.error(f"Device database not available: {e}")
            self.device_db = None
    
    def execute_pytest_for_devices(self, pytest_script: str, device_ids: List[str],
                                   test_config: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Execute pytest script against multiple external devices
        
        Args:
            pytest_script: Pytest script code
            device_ids: List of device IDs to test
            test_config: Optional test configuration
                - parallel: Run tests in parallel (default: False)
                - timeout: Test timeout in seconds (default: 300)
                - verbose: Verbose output (default: True)
        
        Returns:
            Execution results dictionary
        """
        try:
            # Extract optional, ad-hoc device credentials passed from client
            # Format (from Test Framework CSV):
            #   test_config = {
            #       "device_credentials": {
            #           "device_id_1": {"username": "...", "password": "..."},
            #           ...
            #       },
            #       ...
            #   }
            device_credentials = {}
            if test_config and isinstance(test_config, dict):
                device_credentials = test_config.get("device_credentials") or {}
                if not isinstance(device_credentials, dict):
                    device_credentials = {}
            
            # Validate devices
            devices = self._validate_devices(device_ids, device_credentials=device_credentials)
            if not devices:
                return {
                    "success": False,
                    "error": "No valid devices found",
                    "results": []
                }
            
            # Create temporary pytest file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                # Inject device information into pytest script
                enhanced_script = self._enhance_pytest_script(pytest_script, devices, test_config)
                f.write(enhanced_script)
                pytest_file = f.name
            
            try:
                # Execute pytest
                raw_results = self._run_pytest(pytest_file, devices, test_config)
                
                # Parse results from pytest (per-test details)
                parsed_results = self._parse_pytest_results(raw_results, devices)
                summary = self._generate_summary(parsed_results)
                
                # Shape results per device so the UI can render per-device status.
                # For now we apply the same summary to each device, since pytest is
                # executed once for the whole script across all devices.
                per_device_results: Dict[str, Dict[str, Any]] = {}
                overall_status = "success" if summary.get("failed", 0) == 0 and summary.get("error", 0) == 0 else "failed"
                combined_output = (raw_results.get("stdout") or "") + ("\n" + raw_results.get("stderr") if raw_results.get("stderr") else "")
                
                for dev in devices:
                    dev_id = dev.get("device_id")
                    if not dev_id:
                        continue
                    per_device_results[dev_id] = {
                        "status": overall_status,
                        "passed": summary.get("passed", 0),
                        "failed": summary.get("failed", 0) + summary.get("error", 0),
                        "total": summary.get("total_tests", 0),
                        "summary": summary,
                        "output": combined_output.strip(),
                    }
                
                return {
                    "success": True,
                    "devices_tested": device_ids,
                    "results": per_device_results,
                    "summary": summary,
                }
            finally:
                # Clean up temporary file
                try:
                    os.unlink(pytest_file)
                except Exception:
                    pass
        
        except Exception as e:
            logger.error(f"Pytest execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "results": []
            }
    
    def _validate_devices(self, device_ids: List[str],
                          device_credentials: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict]:
        """
        Validate and get device information.
        
        This function now supports two modes:
        1) Devices defined in the DeviceDatabase (existing behavior)
        2) Ad-hoc devices provided via Test Framework (device ID + username/password),
           completely independent of the Devices tab / database.
        """
        valid_devices = []

        device_credentials = device_credentials or {}
        has_device_db = self.device_db is not None

        for device_id in device_ids:
            creds = device_credentials.get(device_id) or {}
            device: Optional[Dict[str, Any]] = None

            # First, try to resolve from DeviceDatabase if available
            if has_device_db:
                try:
                    device = self.device_db.get_device(device_id)
                except Exception as e:
                    logger.warning(f"[PYTEST DEVICE RUNNER] Error looking up device '{device_id}' in database: {e}")
                    device = None
            
            if device:
                # Existing, database-backed device
                device_type = device.get("device_type", "frr_container")
                # Only include external devices as before
                if device_type != "frr_container":
                    conn_info = self._get_connection_info(device)
                    # Allow ad-hoc credentials from Test Framework to override DB values
                    if creds:
                        username = creds.get("username")
                        password = creds.get("password")
                        if username:
                            conn_info["username"] = username
                        if password:
                            conn_info["password"] = password
                    # Ensure connection_method/host defaults
                    conn_info.setdefault("connection_method", "ssh")
                    conn_info.setdefault("host", device.get("management_ip") or device.get("ip") or device_id)
                    
                    valid_devices.append({
                        "device_id": device_id,
                        "device_name": device.get("device_name", device_id),
                        "device_type": device_type,
                        "connection_info": conn_info
                    })
            else:
                # Ad-hoc device path – independent of Devices tab / database
                # We only require that user provided some credentials; otherwise we
                # still create a minimal entry and let the pytest script / fixtures decide.
                conn_info: Dict[str, Any] = {}

                if creds:
                    # Treat device_id as host/hostname by default
                    conn_info = {
                        "connection_method": "ssh",
                        "host": device_id,
                        "port": 22,
                        "username": creds.get("username"),
                        "password": creds.get("password"),
                    }
                else:
                    # Minimal connection info; user pytest script is expected to use
                    # device_id however it wants (for fully custom tests).
                    conn_info = {
                        "connection_method": "ssh",
                        "host": device_id,
                    }

                # Include device_type in connection_info so ExternalDeviceManager
                # can handle device-specific CLI entry (e.g., "cli" for Juniper)
                conn_info["device_type"] = "external"  # Default, but can be overridden
                valid_devices.append({
                    "device_id": device_id,
                    "device_name": device_id,
                    "device_type": "external",  # Generic external device
                    "connection_info": conn_info,
                })

        return valid_devices
    
    def _get_connection_info(self, device: Dict) -> Dict:
        """Get connection information for device"""
        connection_info_str = device.get("connection_info", "{}")
        try:
            if isinstance(connection_info_str, str):
                return json.loads(connection_info_str)
            return connection_info_str
        except Exception:
            return {}
    
    def _enhance_pytest_script(self, pytest_script: str, devices: List[Dict],
                               test_config: Optional[Dict]) -> str:
        """Enhance pytest script with device fixtures and helpers"""
        
        # Device fixtures
        # NOTE: We intentionally do NOT depend on DeviceDatabase here so that
        # pytest execution can work with devices that are not present in the
        # main device database (pure Test Framework / CSV devices).
        device_fixtures = """
# Device fixtures for pytest execution
import pytest
from utils.external_device_manager import ExternalDeviceManager

# Initialize manager (shared across fixtures)
device_manager = ExternalDeviceManager()

# Device fixtures
"""
        
        # Create fixture for each device
        for device in devices:
            device_id = device["device_id"]
            device_name = device["device_name"]
            device_type = device["device_type"]
            # Connection info was already prepared in _validate_devices()
            connection_info = device.get("connection_info") or {}
            # Use repr() so we embed a valid Python dict literal directly
            connection_info_literal = repr(connection_info)
            fixture_name = device_name.lower().replace(" ", "_").replace("-", "_")
            
            device_fixtures += f'''
@pytest.fixture(scope="module")
def {fixture_name}_device():
    """Fixture for device: {device_name} ({device_type})"""
    connection_info = {connection_info_literal}
    device_manager.add_device("{device_id}", "{device_type}", connection_info)
    return {{
        "device_id": "{device_id}",
        "device_name": "{device_name}",
        "device_type": "{device_type}",
        "connection_info": connection_info,
        "manager": device_manager
    }}
'''
        
        # Helper functions
        helpers = """
# Helper functions for device testing
def execute_device_command(device_fixture, command):
    \"\"\"Execute command on device\"\"\"
    return device_fixture["manager"].execute_command(
        device_fixture["device_id"],
        command
    )

def get_device_status(device_fixture):
    \"\"\"Get device status\"\"\"
    return device_fixture["manager"].get_device_status(
        device_fixture["device_id"]
    )

def get_device_config(device_fixture):
    \"\"\"Get device configuration\"\"\"
    return device_fixture["manager"].get_configuration(
        device_fixture["device_id"]
    )

def apply_device_config(device_fixture, commands):
    \"\"\"Apply configuration to device\"\"\"
    return device_fixture["manager"].apply_configuration(
        device_fixture["device_id"],
        commands
    )

"""
        
        # Combine
        enhanced_script = device_fixtures + helpers + pytest_script
        
        return enhanced_script
    
    def _run_pytest(self, pytest_file: str, devices: List[Dict],
                   test_config: Optional[Dict]) -> Dict[str, Any]:
        """Run pytest and capture results"""
        timeout = test_config.get("timeout", 300) if test_config else 300
        verbose = test_config.get("verbose", True) if test_config else True
        
        # Build pytest command
        # NOTE: We intentionally avoid using the pytest-json-report plugin here,
        # because it may not be installed on all target servers (which would
        # cause errors like: "unrecognized arguments: --json-report").
        cmd = ["pytest", pytest_file, "-v", "--tb=short"]
        
        if verbose and "-v" not in cmd:
            cmd.append("-v")
        
        try:
            # Run pytest
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=os.path.dirname(pytest_file)
            )
            
            # Try to read JSON report if available
            json_report = None
            try:
                with open("/tmp/pytest_report.json", "r") as f:
                    json_report = json.load(f)
            except Exception:
                pass
            
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "json_report": json_report,
                "success": result.returncode == 0
            }
        
        except subprocess.TimeoutExpired:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Test execution timed out after {timeout} seconds",
                "json_report": None,
                "success": False,
                "timeout": True
            }
        except Exception as e:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "json_report": None,
                "success": False,
                "error": str(e)
            }
    
    def _parse_pytest_results(self, results: Dict, devices: List[Dict]) -> List[Dict]:
        """Parse pytest execution results"""
        parsed = []
        
        # Parse JSON report if available
        if results.get("json_report"):
            json_report = results["json_report"]
            tests = json_report.get("tests", [])
            
            for test in tests:
                parsed.append({
                    "test_name": test.get("nodeid", ""),
                    "outcome": test.get("outcome", "unknown"),
                    "duration": test.get("duration", 0),
                    "setup": test.get("setup", {}),
                    "call": test.get("call", {}),
                    "teardown": test.get("teardown", {})
                })
        else:
            # Parse stdout for test results
            stdout = results.get("stdout", "")
            lines = stdout.split("\n")
            
            for line in lines:
                if "PASSED" in line or "FAILED" in line or "ERROR" in line:
                    # Extract test name and result
                    parts = line.split()
                    if len(parts) >= 2:
                        parsed.append({
                            "test_name": parts[0] if parts[0].startswith("test_") else "unknown",
                            "outcome": "passed" if "PASSED" in line else "failed" if "FAILED" in line else "error",
                            "duration": 0,
                            "raw_line": line
                        })
        
        return parsed
    
    def _generate_summary(self, results: List[Dict]) -> Dict[str, Any]:
        """Generate test execution summary"""
        total = len(results)
        passed = sum(1 for r in results if r.get("outcome") == "passed")
        failed = sum(1 for r in results if r.get("outcome") == "failed")
        error = sum(1 for r in results if r.get("outcome") == "error")
        skipped = sum(1 for r in results if r.get("outcome") == "skipped")
        
        return {
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "error": error,
            "skipped": skipped,
            "pass_rate": (passed / total * 100) if total > 0 else 0
        }
    
    def execute_pytest_for_device_type(self, pytest_script: str, device_type: str,
                                      test_config: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Execute pytest script for all devices of a specific type
        
        Args:
            pytest_script: Pytest script code
            device_type: Device type (juniper, cisco, arista, etc.)
            test_config: Optional test configuration
        
        Returns:
            Execution results
        """
        if not self.device_db:
            return {
                "success": False,
                "error": "Device database not available"
            }
        
        # Get all devices of specified type
        all_devices = self.device_db.get_all_devices()
        device_ids = [
            d.get("device_id") for d in all_devices
            if d.get("device_type", "").lower() == device_type.lower()
        ]
        
        if not device_ids:
            return {
                "success": False,
                "error": f"No devices found for type: {device_type}",
                "results": []
            }
        
        return self.execute_pytest_for_devices(pytest_script, device_ids, test_config)
    
    def generate_device_specific_pytest(self, base_pytest_script: str,
                                        device_type: str) -> str:
        """
        Generate vendor-specific pytest script
        
        Args:
            base_pytest_script: Base pytest script
            device_type: Device type (juniper, cisco, arista)
        
        Returns:
            Vendor-specific pytest script
        """
        # Add vendor-specific helpers
        vendor_helpers = self._get_vendor_helpers(device_type)
        
        # Enhance script with vendor-specific functions
        enhanced = vendor_helpers + "\n\n" + base_pytest_script
        
        return enhanced
    
    def _get_vendor_helpers(self, device_type: str) -> str:
        """Get vendor-specific helper functions"""
        if device_type.lower() == "juniper":
            return """
# Juniper-specific helper functions
def juniper_show_command(device_fixture, command):
    \"\"\"Execute Juniper show command\"\"\"
    full_command = f"show {command}"
    return execute_device_command(device_fixture, full_command)

def juniper_set_command(device_fixture, config_path, value):
    \"\"\"Execute Juniper set command\"\"\"
    command = f"set {config_path} {value}"
    return execute_device_command(device_fixture, command)

def juniper_delete_command(device_fixture, config_path):
    \"\"\"Execute Juniper delete command\"\"\"
    command = f"delete {config_path}"
    return execute_device_command(device_fixture, command)

def juniper_commit(device_fixture):
    \"\"\"Commit Juniper configuration\"\"\"
    return execute_device_command(device_fixture, "commit")

def juniper_rollback(device_fixture):
    \"\"\"Rollback Juniper configuration\"\"\"
    return execute_device_command(device_fixture, "rollback")
"""
        
        elif device_type.lower() == "cisco":
            return """
# Cisco-specific helper functions
def cisco_show_command(device_fixture, command):
    \"\"\"Execute Cisco show command\"\"\"
    full_command = f"show {command}"
    return execute_device_command(device_fixture, full_command)

def cisco_config_command(device_fixture, command):
    \"\"\"Execute Cisco configuration command\"\"\"
    commands = ["configure terminal", command, "end"]
    results = []
    for cmd in commands:
        result = execute_device_command(device_fixture, cmd)
        results.append(result)
    return results

def cisco_write_memory(device_fixture):
    \"\"\"Write Cisco configuration to memory\"\"\"
    return execute_device_command(device_fixture, "write memory")
"""
        
        elif device_type.lower() == "arista":
            return """
# Arista-specific helper functions
def arista_show_command(device_fixture, command):
    \"\"\"Execute Arista show command\"\"\"
    full_command = f"show {command}"
    return execute_device_command(device_fixture, full_command)

def arista_config_command(device_fixture, command):
    \"\"\"Execute Arista configuration command\"\"\"
    commands = ["configure", command, "end"]
    results = []
    for cmd in commands:
        result = execute_device_command(device_fixture, cmd)
        results.append(result)
    return results
"""
        
        else:
            return """
# Generic device helper functions
def device_show_command(device_fixture, command):
    \"\"\"Execute show command on device\"\"\"
    return execute_device_command(device_fixture, f"show {command}")
"""

