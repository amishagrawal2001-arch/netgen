"""
External Device Manager
Manages communication with external network devices (non-FRR containers)
Supports SSH, SNMP, REST API, and other protocols
"""

import logging
import subprocess
import json
from typing import Dict, List, Optional, Any
import paramiko
from pathlib import Path

logger = logging.getLogger(__name__)


class ExternalDeviceManager:
    """Manage external network devices"""
    
    def __init__(self):
        self.ssh_configs = {}  # Store SSH credentials per device
        self.snmp_configs = {}  # Store SNMP credentials per device
    
    def add_device(self, device_id: str, device_type: str, connection_info: Dict):
        """
        Add external device
        
        Args:
            device_id: Device identifier
            device_type: Device type (juniper, cisco, arista, etc.)
            connection_info: Connection details
                {
                    "connection_method": "ssh" | "snmp" | "rest" | "netconf",
                    "host": "192.168.1.1",
                    "port": 22,
                    "username": "admin",
                    "password": "password",
                    "ssh_key": "/path/to/key",  # Optional
                    "snmp_community": "public",  # For SNMP
                    "api_key": "key",  # For REST API
                }
        """
        device_info = {
            "device_id": device_id,
            "device_type": device_type,
            "connection_info": connection_info
        }
        
        if connection_info.get("connection_method") == "ssh":
            self.ssh_configs[device_id] = device_info
        elif connection_info.get("connection_method") == "snmp":
            self.snmp_configs[device_id] = device_info
        
        logger.info(f"Added external device: {device_id} ({device_type})")
        return device_info
    
    def execute_command(self, device_id: str, command: str, 
                       connection_method: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute command on external device
        
        Args:
            device_id: Device identifier
            command: Command to execute
            connection_method: Override connection method
        
        Returns:
            {
                "success": bool,
                "output": str,
                "error": str
            }
        """
        device_info = self.ssh_configs.get(device_id) or self.snmp_configs.get(device_id)
        if not device_info:
            return {
                "success": False,
                "error": f"Device {device_id} not found"
            }
        
        conn_info = device_info["connection_info"]
        method = connection_method or conn_info.get("connection_method", "ssh")
        
        if method == "ssh":
            return self._execute_ssh(device_id, command, conn_info)
        elif method == "snmp":
            return self._execute_snmp(device_id, command, conn_info)
        elif method == "rest":
            return self._execute_rest(device_id, command, conn_info)
        else:
            return {
                "success": False,
                "error": f"Unsupported connection method: {method}"
            }
    
    def _execute_ssh(self, device_id: str, command: str, conn_info: Dict) -> Dict[str, Any]:
        """Execute command via SSH"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            host = conn_info.get("host")
            port = conn_info.get("port", 22)
            username = conn_info.get("username")
            password = conn_info.get("password")
            ssh_key = conn_info.get("ssh_key")
            device_type = conn_info.get("device_type", "external").lower()
            
            # Connect
            if ssh_key:
                ssh.connect(host, port=port, username=username, key_filename=ssh_key)
            else:
                ssh.connect(host, port=port, username=username, password=password)
            
            # For network devices, we need to handle CLI mode entry.
            # Many network CLI commands (show, configure, set, delete, commit, etc.)
            # are NOT valid in the default Unix shell, so we detect them and
            # route through an interactive CLI session.
            import time
            cmd_stripped = command.strip()
            first_word = cmd_stripped.split()[0] if cmd_stripped else ""
            cli_keywords = {"show", "configure", "edit", "set", "delete", "commit", "load"}

            needs_cli = ("\n" in command) or (first_word in cli_keywords)

            if needs_cli:
                # Use interactive shell for CLI-style commands
                shell = ssh.invoke_shell()
                shell.settimeout(10)

                # Wait for prompt and clear initial banner
                time.sleep(0.5)
                try:
                    _ = shell.recv(4096).decode('utf-8', errors='ignore')
                except Exception:
                    # Ignore if nothing to read yet
                    pass

                # Try to enter CLI mode - for Juniper, "cli" is standard
                shell.send("cli\n")
                time.sleep(0.3)
                try:
                    _ = shell.recv(4096).decode('utf-8', errors='ignore')
                except Exception:
                    pass

                # Send the actual command(s)
                shell.send(command + "\n")
                time.sleep(1.5)  # Wait for command execution

                # Read output with safety limits
                output = ""
                max_reads = 10  # Prevent infinite loop
                read_count = 0
                while read_count < max_reads:
                    if shell.recv_ready():
                        chunk = shell.recv(4096).decode('utf-8', errors='ignore')
                        output += chunk
                        time.sleep(0.3)
                        read_count += 1
                    else:
                        time.sleep(0.2)
                        read_count += 1
                        if read_count >= 3:  # A few idle cycles, then stop
                            break

                shell.close()
                error = None
            else:
                # Simple single command execution in the default shell
                stdin, stdout, stderr = ssh.exec_command(command)
                output = stdout.read().decode()
                error = stderr.read().decode()
            
            ssh.close()
            
            return {
                "success": len(error) == 0 if error else True,
                "output": output,
                "error": error if error else None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _execute_snmp(self, device_id: str, command: str, conn_info: Dict) -> Dict[str, Any]:
        """Execute SNMP query"""
        try:
            host = conn_info.get("host")
            community = conn_info.get("snmp_community", "public")
            
            # Use snmpwalk or snmpget based on command
            # This is simplified - would need proper SNMP library
            result = subprocess.run(
                ["snmpwalk", "-v2c", "-c", community, host],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _execute_rest(self, device_id: str, command: str, conn_info: Dict) -> Dict[str, Any]:
        """Execute REST API call"""
        try:
            import requests
            
            host = conn_info.get("host")
            port = conn_info.get("port", 443)
            api_key = conn_info.get("api_key")
            
            url = f"https://{host}:{port}/api/{command}"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, verify=False, timeout=10)
            
            return {
                "success": response.status_code == 200,
                "output": response.text,
                "error": None if response.status_code == 200 else f"HTTP {response.status_code}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_device_status(self, device_id: str) -> Dict[str, Any]:
        """Get device status"""
        device_info = self.ssh_configs.get(device_id) or self.snmp_configs.get(device_id)
        if not device_info:
            return {"status": "unknown", "error": "Device not found"}
        
        conn_info = device_info["connection_info"]
        method = conn_info.get("connection_method", "ssh")
        
        # Try to ping first
        host = conn_info.get("host")
        ping_result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
            timeout=5
        )
        
        if ping_result.returncode != 0:
            return {"status": "down", "reachable": False}
        
        # Try to get device info
        if method == "ssh":
            result = self.execute_command(device_id, "show version", method="ssh")
            if result["success"]:
                return {
                    "status": "up",
                    "reachable": True,
                    "info": result["output"][:200]  # First 200 chars
                }
        
        return {"status": "up", "reachable": True}
    
    def get_interface_status(self, device_id: str) -> List[Dict[str, Any]]:
        """Get interface status from external device"""
        device_info = self.ssh_configs.get(device_id)
        if not device_info:
            return []
        
        device_type = device_info.get("device_type", "").lower()
        
        # Device-specific commands
        if "juniper" in device_type:
            command = "show interfaces terse"
        elif "cisco" in device_type:
            command = "show ip interface brief"
        else:
            command = "show interfaces"
        
        result = self.execute_command(device_id, command)
        if not result["success"]:
            return []
        
        # Parse output (simplified - would need proper parsing)
        interfaces = []
        for line in result["output"].split("\n"):
            if "up" in line.lower() or "down" in line.lower():
                # Parse interface line (device-specific)
                interfaces.append({
                    "name": line.split()[0] if line.split() else "unknown",
                    "status": "up" if "up" in line.lower() else "down"
                })
        
        return interfaces
    
    def get_configuration(self, device_id: str) -> str:
        """Get device configuration"""
        device_info = self.ssh_configs.get(device_id)
        if not device_info:
            return ""
        
        device_type = device_info.get("device_type", "").lower()
        
        # Device-specific commands
        if "juniper" in device_type:
            command = "show configuration"
        elif "cisco" in device_type:
            command = "show running-config"
        else:
            command = "show config"
        
        result = self.execute_command(device_id, command)
        if result["success"]:
            return result["output"]
        return ""
    
    def apply_configuration(self, device_id: str, config_commands: List[str]) -> Dict[str, Any]:
        """Apply configuration to external device"""
        device_info = self.ssh_configs.get(device_id)
        if not device_info:
            return {"success": False, "error": "Device not found"}
        
        device_type = device_info.get("device_type", "").lower()
        results = []
        
        for cmd in config_commands:
            result = self.execute_command(device_id, cmd)
            results.append({
                "command": cmd,
                "success": result["success"],
                "output": result.get("output", ""),
                "error": result.get("error")
            })
        
        # Commit if Juniper
        if "juniper" in device_type:
            commit_result = self.execute_command(device_id, "commit")
            results.append({
                "command": "commit",
                "success": commit_result["success"],
                "output": commit_result.get("output", ""),
                "error": commit_result.get("error")
            })
        
        all_success = all(r["success"] for r in results)
        return {
            "success": all_success,
            "results": results
        }




