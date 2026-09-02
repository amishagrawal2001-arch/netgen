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

    # v0.5.245-followup (audit ext-*): known stdout error markers.
    # _execute_ssh scans command output for any of these (case-insensitive)
    # and downgrades success=False when they appear. Junk on stderr (banners
    # / MOTD) is no longer a signal, and a clean stderr no longer masks a
    # real error printed to stdout.
    _STDOUT_ERROR_MARKERS = (
        "syntax error",
        "unknown command",
        "% invalid",
        "invalid input",
        "configuration check-out failed",
        "permission denied",
        "commit failed",
        "error: ",
    )

    def __init__(self):
        # v0.5.245-followup (audit ext-*): unified device store keyed by
        # device_id regardless of connection_method. The previous split
        # (ssh_configs / snmp_configs only) silently dropped every "rest"
        # and "netconf" device: add_device built the dict, then nothing
        # persisted it, so subsequent lookups returned None. ssh_configs
        # and snmp_configs kept as aliases populated by add_device for any
        # external readers that still poke at them by name.
        self.devices: Dict[str, Dict] = {}
        self.ssh_configs: Dict[str, Dict] = {}
        self.snmp_configs: Dict[str, Dict] = {}
        self.rest_configs: Dict[str, Dict] = {}
        self.netconf_configs: Dict[str, Dict] = {}

    def _lookup(self, device_id: str) -> Optional[Dict]:
        """Return the stored device record, or None."""
        return self.devices.get(device_id)

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

        # v0.5.245-followup (audit ext-*): persist every device to the
        # unified store, then also mirror into the method-specific dict
        # so callers that read those directly keep working.
        self.devices[device_id] = device_info

        method = (connection_info.get("connection_method") or "").lower()
        if method == "ssh":
            self.ssh_configs[device_id] = device_info
        elif method == "snmp":
            self.snmp_configs[device_id] = device_info
        elif method == "rest":
            self.rest_configs[device_id] = device_info
        elif method == "netconf":
            self.netconf_configs[device_id] = device_info

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
        # v0.5.245-followup (audit ext-*): read from unified store so REST
        # and NETCONF devices are found too.
        device_info = self._lookup(device_id)
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

            # v0.5.245-followup (audit ext-*): track exit code separately
            # from stderr — the interactive-shell path has no exit code, so
            # the shared success check below falls back to a stdout scan.
            exit_status: Optional[int] = None

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
                # v0.5.245-followup (audit ext-*): capture SSH exit code
                # as the primary success signal. Junk on stderr (banners /
                # MOTD) no longer flips success=False, and a clean stderr
                # no longer masks a real error printed to stdout.
                try:
                    exit_status = stdout.channel.recv_exit_status()
                except Exception:
                    exit_status = None

            ssh.close()

            # v0.5.245-followup (audit ext-*): success is:
            #   exit_status == 0 (when available)  AND
            #   no known error marker in stdout.
            # The interactive-shell path has no exit code (exit_status is
            # None); there we rely purely on the stdout scan.
            lower_out = (output or "").lower()
            marker_hit = next(
                (m for m in self._STDOUT_ERROR_MARKERS if m in lower_out),
                None,
            )
            if exit_status is not None:
                success = (exit_status == 0) and (marker_hit is None)
            else:
                success = marker_hit is None

            err_msg = error if error else None
            if marker_hit is not None:
                # Surface the specific marker so callers see WHY we failed.
                err_msg = (err_msg + "\n" if err_msg else "") + \
                    f"stdout error marker: {marker_hit!r}"
            if exit_status not in (None, 0):
                err_msg = (err_msg + "\n" if err_msg else "") + \
                    f"ssh exit_status={exit_status}"

            return {
                "success": success,
                "output": output,
                "error": err_msg,
                "exit_status": exit_status,
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
        # v0.5.245-followup (audit ext-*): read from unified store so REST
        # and NETCONF devices are found here too, not just ssh/snmp.
        device_info = self._lookup(device_id)
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

        # Try to get device info.
        # Audit HIGH #6 / v0.5.245-followup (audit ext-*): the kwarg used
        # to be `method="ssh"` but execute_command's signature is
        # `connection_method=...`. Every external-device SSH status check
        # raised TypeError: got an unexpected keyword argument 'method'
        # and propagated up (no try/except wrapping the call). Kept fixed.
        if method == "ssh":
            result = self.execute_command(device_id, "show version", connection_method="ssh")
            if result["success"]:
                return {
                    "status": "up",
                    "reachable": True,
                    "info": result["output"][:200]  # First 200 chars
                }

        return {"status": "up", "reachable": True}
    
    def get_interface_status(self, device_id: str) -> List[Dict[str, Any]]:
        """Get interface status from external device"""
        # v0.5.245-followup (audit ext-*): use unified lookup.
        device_info = self._lookup(device_id)
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
        # v0.5.245-followup (audit ext-*): use unified lookup.
        device_info = self._lookup(device_id)
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
        """Apply configuration to external device.

        v0.5.245-followup (audit ext-*): the previous implementation sent
        every set / delete / commit as its own execute_command call, which
        opened a fresh SSH session per command. On Juniper (and any device
        whose "configure" state is session-scoped) that means the candidate
        config was empty by the time `commit` ran, so nothing was ever
        applied. The rewrite bundles the whole batch (including `configure`
        and `commit` for Juniper) into ONE payload delivered through a
        single interactive shell, and downgrades success on any known
        error indicator in the combined output.
        """
        # v0.5.245-followup (audit ext-*): use unified lookup.
        device_info = self._lookup(device_id)
        if not device_info:
            return {"success": False, "error": "Device not found"}

        device_type = device_info.get("device_type", "").lower()

        # Build a single newline-joined batch. `needs_cli` in _execute_ssh
        # keys off "\n" in command, so this automatically routes through
        # the interactive-shell path (one session for the entire batch).
        lines: List[str] = []
        if "juniper" in device_type:
            lines.append("configure")
        lines.extend(config_commands)
        if "juniper" in device_type:
            lines.append("commit")
        batch = "\n".join(lines)

        result = self.execute_command(device_id, batch)

        # _execute_ssh already scans stdout for error markers, but we also
        # double-check here because a caller passing a plain command list
        # could have submitted something that produced a device-side error
        # message we didn't include in _STDOUT_ERROR_MARKERS.
        combined_output = result.get("output", "") or ""
        lower_out = combined_output.lower()
        extra_markers = ("mgd:", "error:", "aborted", "commit failed")
        extra_hit = next((m for m in extra_markers if m in lower_out), None)

        success = bool(result.get("success")) and extra_hit is None
        err = result.get("error")
        if extra_hit is not None:
            err = (err + "\n" if err else "") + f"extra error marker: {extra_hit!r}"

        return {
            "success": success,
            "batch": batch,
            "output": combined_output,
            "error": err,
            "exit_status": result.get("exit_status"),
        }




