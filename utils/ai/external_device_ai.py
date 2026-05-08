"""
AI Support for External Devices
Handles troubleshooting and testing of external network devices
"""

import logging
from typing import Dict, List, Optional, Any
import requests

logger = logging.getLogger(__name__)


class ExternalDeviceAI:
    """AI support for external network devices"""
    
    def __init__(self, external_device_manager=None):
        self.external_manager = external_device_manager
    
    def diagnose_external_device(self, device_id: str, device_info: Dict, 
                                 symptoms: Dict) -> Dict[str, Any]:
        """
        Diagnose issues on external device
        
        Args:
            device_id: Device identifier
            device_info: Device information from database
            symptoms: Dictionary of symptoms
        
        Returns:
            Diagnosis dictionary
        """
        device_type = device_info.get("device_type", "frr_container")
        
        if device_type == "frr_container":
            # Use existing FRR troubleshooting
            return self._diagnose_frr_device(device_id, device_info, symptoms)
        else:
            # Use external device troubleshooting
            return self._diagnose_external_device(device_id, device_info, symptoms)
    
    def _diagnose_frr_device(self, device_id: str, device_info: Dict, 
                             symptoms: Dict) -> Dict[str, Any]:
        """Diagnose FRR container device"""
        # Use existing troubleshooting logic
        from utils.ai import NetworkTroubleshooter, ConfigKnowledgeBase
        
        kb = ConfigKnowledgeBase()
        troubleshooter = NetworkTroubleshooter(knowledge_base=kb, use_local_ai=True)
        
        return troubleshooter.diagnose(device_id, symptoms)
    
    def _diagnose_external_device(self, device_id: str, device_info: Dict,
                                 symptoms: Dict) -> Dict[str, Any]:
        """Diagnose external device"""
        if not self.external_manager:
            from utils.external_device_manager import ExternalDeviceManager
            self.external_manager = ExternalDeviceManager()
        
        # Get device status
        device_status = self.external_manager.get_device_status(device_id)
        
        # Get interface status
        interfaces = self.external_manager.get_interface_status(device_id)
        
        # Build diagnosis
        diagnosis = {
            "diagnosis": "External device analysis",
            "root_cause": "Unknown",
            "solutions": [],
            "confidence": 0.7,
            "source": "external_device",
            "device_status": device_status,
            "interfaces": interfaces
        }
        
        # Check symptoms
        if symptoms.get("interface_down"):
            down_interfaces = [iface for iface in interfaces if iface.get("status") == "down"]
            if down_interfaces:
                diagnosis["root_cause"] = f"Interface(s) down: {', '.join([i['name'] for i in down_interfaces])}"
                diagnosis["solutions"] = [
                    "Check physical cable connections",
                    "Verify interface is not administratively shut down",
                    f"Check interface status: 'show interfaces' (vendor-specific)"
                ]
                diagnosis["confidence"] = 0.9
        
        if not device_status.get("reachable"):
            diagnosis["root_cause"] = "Device is not reachable"
            diagnosis["solutions"] = [
                "Check network connectivity",
                "Verify device IP address",
                "Check firewall rules",
                "Verify device is powered on"
            ]
            diagnosis["confidence"] = 0.95
        
        # Get device configuration for context
        config = self.external_manager.get_configuration(device_id)
        if config:
            diagnosis["config_available"] = True
            # Could parse config for more detailed diagnosis
        
        return diagnosis
    
    def test_external_device(self, device_id: str, device_info: Dict,
                            test_ids: List[str]) -> Dict[str, Any]:
        """Run tests on external device"""
        if not self.external_manager:
            from utils.external_device_manager import ExternalDeviceManager
            self.external_manager = ExternalDeviceManager()
        
        device_type = device_info.get("device_type", "frr_container")
        
        if device_type == "frr_container":
            # Use existing test framework
            from utils.ai import NetworkTestFramework, ConfigKnowledgeBase
            kb = ConfigKnowledgeBase()
            framework = NetworkTestFramework(knowledge_base=kb)
            return framework.run_test_suite(test_ids, device_id, device_info.get("device_name", ""))
        else:
            # Run tests on external device
            return self._test_external_device(device_id, device_info, test_ids)
    
    def _test_external_device(self, device_id: str, device_info: Dict,
                              test_ids: List[str]) -> Dict[str, Any]:
        """Run tests on external device"""
        results = []
        
        for test_id in test_ids:
            if test_id == "ping_test":
                result = self._test_ping_external(device_id)
            elif test_id == "interface_status":
                result = self._test_interface_status_external(device_id)
            else:
                result = {
                    "test_id": test_id,
                    "status": "skipped",
                    "message": "Test not supported for external devices"
                }
            results.append(result)
        
        return {
            "device_id": device_id,
            "test_results": results,
            "total_tests": len(results),
            "passed_tests": sum(1 for r in results if r.get("status") == "passed"),
            "failed_tests": sum(1 for r in results if r.get("status") == "failed")
        }
    
    def _test_ping_external(self, device_id: str) -> Dict[str, Any]:
        """Test ping to external device"""
        if not self.external_manager:
            return {"status": "error", "error": "External device manager not available"}
        
        device_info = self.external_manager.ssh_configs.get(device_id) or \
                     self.external_manager.snmp_configs.get(device_id)
        if not device_info:
            return {"status": "error", "error": "Device not found"}
        
        host = device_info["connection_info"].get("host")
        if not host:
            return {"status": "error", "error": "Host not configured"}
        
        import subprocess
        result = subprocess.run(
            ["ping", "-c", "5", host],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        return {
            "test_id": "ping_test",
            "status": "passed" if result.returncode == 0 else "failed",
            "output": result.stdout,
            "error": result.stderr if result.returncode != 0 else None
        }
    
    def _test_interface_status_external(self, device_id: str) -> Dict[str, Any]:
        """Test interface status on external device"""
        if not self.external_manager:
            return {"status": "error", "error": "External device manager not available"}
        
        interfaces = self.external_manager.get_interface_status(device_id)
        down_interfaces = [iface for iface in interfaces if iface.get("status") == "down"]
        
        return {
            "test_id": "interface_status",
            "status": "passed" if len(down_interfaces) == 0 else "failed",
            "total_interfaces": len(interfaces),
            "down_interfaces": len(down_interfaces),
            "interfaces": interfaces
        }




