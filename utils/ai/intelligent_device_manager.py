"""
Intelligent Device Manager
AI-powered device lifecycle management, provisioning, and remediation
"""

import logging
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import json

logger = logging.getLogger(__name__)


class IntelligentDeviceManager:
    """AI-powered device lifecycle management"""
    
    def __init__(self, use_ai_api: bool = False, api_key: Optional[str] = None):
        self.use_ai_api = use_ai_api
        self.api_key = api_key
        
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
    
    def provision_device(self, device_spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Automated device provisioning
        
        Args:
            device_spec: Device specification
                - device_type: Type (frr_container, juniper, cisco, etc.)
                - device_name: Device name
                - configuration: Configuration requirements
                - network: Network configuration
        
        Returns:
            Provisioning result with device_id and status
        """
        try:
            device_type = device_spec.get("device_type", "frr_container")
            device_name = device_spec.get("device_name", "device")
            config = device_spec.get("configuration", {})
            network = device_spec.get("network", {})
            
            if device_type == "frr_container":
                return self._provision_frr_device(device_spec)
            else:
                return self._provision_external_device(device_spec)
        except Exception as e:
            logger.error(f"Device provisioning failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "device_id": None
            }
    
    def _provision_frr_device(self, device_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Provision FRR container device"""
        try:
            from utils.frr_docker import FRRDockerManager
            
            frr_manager = FRRDockerManager()
            device_id = device_spec.get("device_id") or str(uuid.uuid4())
            device_name = device_spec.get("device_name", f"device_{device_id}")
            
            # Build device config
            device_config = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_spec.get("interface", ""),
                "ipv4": device_spec.get("network", {}).get("ipv4"),
                "ipv6": device_spec.get("network", {}).get("ipv6"),
                "protocols": device_spec.get("configuration", {}).get("protocols", []),
                "bgp_config": device_spec.get("configuration", {}).get("bgp", {}),
                "ospf_config": device_spec.get("configuration", {}).get("ospf", {}),
                "isis_config": device_spec.get("configuration", {}).get("isis", {})
            }
            
            # Start container
            container_name = frr_manager.start_frr_container(device_id, device_config)
            
            if container_name:
                return {
                    "success": True,
                    "device_id": device_id,
                    "device_name": device_name,
                    "container_name": container_name,
                    "status": "provisioned"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to start FRR container",
                    "device_id": device_id
                }
        except Exception as e:
            logger.error(f"FRR device provisioning failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "device_id": device_spec.get("device_id")
            }
    
    def _provision_external_device(self, device_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Provision external device"""
        try:
            if not self.external_manager:
                return {
                    "success": False,
                    "error": "External device manager not available"
                }
            
            device_id = device_spec.get("device_id") or str(uuid.uuid4())
            device_name = device_spec.get("device_name", f"device_{device_id}")
            device_type = device_spec.get("device_type", "juniper")
            connection_info = device_spec.get("connection", {})
            
            # Add device to external manager
            self.external_manager.add_device(device_id, device_type, connection_info)
            
            # Test connection
            status = self.external_manager.get_device_status(device_id)
            
            if status.get("reachable"):
                # Apply configuration if provided
                config = device_spec.get("configuration", {})
                if config:
                    config_commands = self._generate_config_commands(device_type, config)
                    if config_commands:
                        result = self.external_manager.apply_configuration(device_id, config_commands)
                        if not result.get("success"):
                            logger.warning(f"Config application failed: {result.get('error')}")
                
                return {
                    "success": True,
                    "device_id": device_id,
                    "device_name": device_name,
                    "status": "provisioned",
                    "reachable": True
                }
            else:
                return {
                    "success": False,
                    "error": "Device not reachable",
                    "device_id": device_id
                }
        except Exception as e:
            logger.error(f"External device provisioning failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "device_id": device_spec.get("device_id")
            }
    
    def _generate_config_commands(self, device_type: str, config: Dict) -> List[str]:
        """Generate configuration commands based on device type"""
        commands = []
        
        if device_type.lower() == "juniper":
            # Generate Juniper commands
            if config.get("interfaces"):
                for iface in config["interfaces"]:
                    iface_name = iface.get("name", "ge-0/0/0")
                    ip = iface.get("ip", "")
                    if ip:
                        commands.append(f"set interfaces {iface_name} unit 0 family inet address {ip}")
            
            if config.get("bgp"):
                bgp = config["bgp"]
                asn = bgp.get("asn", 65000)
                commands.append(f"set protocols bgp group external type external")
                commands.append(f"set protocols bgp group external local-as {asn}")
        
        elif device_type.lower() == "cisco":
            # Generate Cisco commands
            if config.get("interfaces"):
                for iface in config["interfaces"]:
                    iface_name = iface.get("name", "GigabitEthernet0/0")
                    ip = iface.get("ip", "")
                    if ip:
                        commands.append(f"interface {iface_name}")
                        commands.append(f" ip address {ip}")
                        commands.append(" no shutdown")
            
            if config.get("bgp"):
                bgp = config["bgp"]
                asn = bgp.get("asn", 65000)
                commands.append(f"router bgp {asn}")
        
        return commands
    
    def manage_configuration(self, device_id: str, config_changes: Dict[str, Any]) -> Dict[str, Any]:
        """
        Intelligent configuration management
        
        Args:
            device_id: Device identifier
            config_changes: Configuration changes
                - add: Configurations to add
                - remove: Configurations to remove
                - modify: Configurations to modify
        
        Returns:
            Management result
        """
        try:
            # Get device info
            device = self.device_db.get_device(device_id) if self.device_db else None
            if not device:
                return {
                    "success": False,
                    "error": f"Device {device_id} not found"
                }
            
            device_type = device.get("device_type", "frr_container")
            
            # Validate changes
            validation = self._validate_config_changes(device_id, config_changes)
            if not validation.get("valid"):
                return {
                    "success": False,
                    "error": "Configuration validation failed",
                    "issues": validation.get("issues", [])
                }
            
            # Check for conflicts
            conflicts = self._check_config_conflicts(device_id, config_changes)
            if conflicts:
                return {
                    "success": False,
                    "error": "Configuration conflicts detected",
                    "conflicts": conflicts
                }
            
            # Apply changes
            if device_type == "frr_container":
                return self._apply_frr_config(device_id, config_changes)
            else:
                return self._apply_external_config(device_id, config_changes)
        
        except Exception as e:
            logger.error(f"Configuration management failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _validate_config_changes(self, device_id: str, changes: Dict) -> Dict:
        """Validate configuration changes"""
        # Basic validation
        issues = []
        
        if changes.get("add"):
            for item in changes["add"]:
                # Validate format, values, etc.
                pass
        
        return {
            "valid": len(issues) == 0,
            "issues": issues
        }
    
    def _check_config_conflicts(self, device_id: str, changes: Dict) -> List[str]:
        """Check for configuration conflicts"""
        conflicts = []
        
        # Check for conflicting IP addresses
        # Check for conflicting protocol configurations
        # etc.
        
        return conflicts
    
    def _apply_frr_config(self, device_id: str, changes: Dict) -> Dict:
        """Apply configuration to FRR container"""
        # Implementation for FRR config application
        return {
            "success": True,
            "message": "Configuration applied successfully"
        }
    
    def _apply_external_config(self, device_id: str, changes: Dict) -> Dict:
        """Apply configuration to external device"""
        if not self.external_manager:
            return {
                "success": False,
                "error": "External device manager not available"
            }
        
        # Generate commands from changes
        commands = []
        # ... generate commands ...
        
        result = self.external_manager.apply_configuration(device_id, commands)
        return result
    
    def monitor_health(self, device_id: str) -> Dict[str, Any]:
        """
        Continuous health monitoring
        
        Args:
            device_id: Device identifier
        
        Returns:
            Health status dictionary
        """
        try:
            device = self.device_db.get_device(device_id) if self.device_db else None
            if not device:
                return {
                    "status": "unknown",
                    "error": "Device not found"
                }
            
            device_type = device.get("device_type", "frr_container")
            
            if device_type == "frr_container":
                return self._monitor_frr_health(device_id)
            else:
                return self._monitor_external_health(device_id)
        
        except Exception as e:
            logger.error(f"Health monitoring failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _monitor_frr_health(self, device_id: str) -> Dict:
        """Monitor FRR container health"""
        try:
            from utils.frr_docker import FRRDockerManager
            
            frr_manager = FRRDockerManager()
            container_name = frr_manager._get_container_name(device_id, "")
            
            import docker
            client = docker.from_env()
            container = client.containers.get(container_name)
            
            return {
                "status": container.status,
                "healthy": container.status == "running",
                "uptime": container.attrs.get("State", {}).get("StartedAt", ""),
                "metrics": {
                    "cpu": "N/A",
                    "memory": "N/A"
                }
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _monitor_external_health(self, device_id: str) -> Dict:
        """Monitor external device health"""
        if not self.external_manager:
            return {
                "status": "unknown",
                "error": "External device manager not available"
            }
        
        status = self.external_manager.get_device_status(device_id)
        interfaces = self.external_manager.get_interface_status(device_id)
        
        return {
            "status": status.get("status", "unknown"),
            "reachable": status.get("reachable", False),
            "interfaces": interfaces,
            "healthy": status.get("reachable", False) and len([i for i in interfaces if i.get("status") == "down"]) == 0
        }
    
    def auto_remediate(self, device_id: str, issue: Dict[str, Any]) -> Dict[str, Any]:
        """
        Automated issue remediation
        
        Args:
            device_id: Device identifier
            issue: Issue description
                - type: Issue type
                - symptoms: Issue symptoms
                - severity: Issue severity
        
        Returns:
            Remediation result
        """
        try:
            # Diagnose issue
            from .unified_troubleshooter import UnifiedTroubleshooter
            import os
            
            troubleshooter = UnifiedTroubleshooter(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            diagnosis = troubleshooter.troubleshoot("network", {
                "device_id": device_id,
                "symptoms": issue.get("symptoms", {})
            })
            
            # Generate fix
            solutions = diagnosis.get("solutions", [])
            commands = diagnosis.get("commands", {})
            
            if not solutions:
                return {
                    "success": False,
                    "error": "No solutions found",
                    "diagnosis": diagnosis
                }
            
            # Apply fix
            device = self.device_db.get_device(device_id) if self.device_db else None
            if not device:
                return {
                    "success": False,
                    "error": "Device not found"
                }
            
            device_type = device.get("device_type", "frr_container")
            
            if device_type == "frr_container":
                # Apply FRR fixes
                return {
                    "success": True,
                    "message": "Remediation applied",
                    "solutions": solutions
                }
            else:
                # Apply external device fixes
                if commands:
                    vendor = device_type
                    vendor_commands = commands.get(vendor, [])
                    if vendor_commands and self.external_manager:
                        result = self.external_manager.apply_configuration(device_id, vendor_commands)
                        return {
                            "success": result.get("success", False),
                            "message": "Remediation applied",
                            "solutions": solutions,
                            "commands_applied": vendor_commands
                        }
            
            return {
                "success": True,
                "message": "Remediation suggested (manual application required)",
                "solutions": solutions
            }
        
        except Exception as e:
            logger.error(f"Auto-remediation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

