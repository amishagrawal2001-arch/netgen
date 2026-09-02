"""
SONiC-specific troubleshooting utilities
"""

import logging
import re
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class SONiCTroubleshooter:
    """SONiC-specific troubleshooting helper"""
    
    @staticmethod
    def diagnose_empty_interfaces(device_output: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Diagnose why interfaces are not showing up in SONiC
        
        Args:
            device_output: Dictionary with command outputs
                - 'show_interfaces_status': Output of 'show interfaces status'
                - 'ip_link_show': Output of 'ip link show'
                - 'show_platform_summary': Output of 'show platform summary'
                - 'swss_status': Output of 'systemctl status swss'
                - 'redis_keys': Output of 'redis-cli KEYS PORT*'
        
        Returns:
            List of diagnostic findings and recommendations
        """
        findings = []
        
        # Check 1: Kernel-level interface detection
        ip_link_output = device_output.get('ip_link_show', '')
        if ip_link_output:
            # v0.5.245-followup (audit AI-*): accept VLAN/veth/bond sub-interface names
            # (e.g. "eth0@if4", "bond0.100@bond0") and strip the "@peer" suffix so that
            # sub-interfaces don't trigger a false "no interfaces detected" critical alert.
            # Tests: "2: eth0@if4:" -> "eth0"; "3: bond0.100@bond0:" -> "bond0.100".
            raw_interfaces = re.findall(r'\d+:\s+([\w.@-]+):', ip_link_output)
            interfaces = [name.split('@', 1)[0] for name in raw_interfaces]
            if interfaces:
                findings.append({
                    "severity": "info",
                    "finding": f"Kernel detects {len(interfaces)} interface(s): {', '.join(interfaces)}",
                    "recommendation": "Interfaces are detected at kernel level. Check SONiC configuration."
                })
            else:
                findings.append({
                    "severity": "critical",
                    "finding": "No interfaces detected at kernel level",
                    "recommendation": "Check hardware connections, drivers, and platform configuration"
                })
        
        # Check 2: SONiC service status
        swss_status = device_output.get('swss_status', '')
        if swss_status:
            if 'active (running)' in swss_status.lower():
                findings.append({
                    "severity": "info",
                    "finding": "SONiC SWSS service is running",
                    "recommendation": "Service is healthy"
                })
            elif 'inactive' in swss_status.lower() or 'failed' in swss_status.lower():
                findings.append({
                    "severity": "critical",
                    "finding": "SONiC SWSS service is not running",
                    "recommendation": "Run: sudo systemctl restart swss"
                })
        
        # Check 3: Redis database entries
        redis_keys = device_output.get('redis_keys', '')
        if redis_keys:
            port_keys = re.findall(r'PORT_TABLE:', redis_keys)
            if port_keys:
                findings.append({
                    "severity": "info",
                    "finding": f"Found {len(port_keys)} port entries in database",
                    "recommendation": "Database has port entries. Check interface configuration."
                })
            else:
                findings.append({
                    "severity": "warning",
                    "finding": "No PORT_TABLE entries in Redis database",
                    "recommendation": "Run: sudo systemctl restart swss to rebuild database"
                })
        
        # Check 4: Platform detection
        platform_summary = device_output.get('show_platform_summary', '')
        if platform_summary:
            if 'Platform' in platform_summary or 'ASIC' in platform_summary:
                findings.append({
                    "severity": "info",
                    "finding": "Platform information available",
                    "recommendation": "Platform is detected correctly"
                })
            else:
                findings.append({
                    "severity": "warning",
                    "finding": "Platform information not available",
                    "recommendation": "Check /host/machine.conf and platform detection"
                })
        
        return findings
    
    @staticmethod
    def generate_troubleshooting_commands() -> List[str]:
        """Generate list of diagnostic commands for SONiC"""
        return [
            "show interfaces status",
            "show interfaces",
            "ip link show",
            "ls /sys/class/net/",
            "show platform summary",
            "show platform syseeprom",
            "sudo systemctl status swss",
            "sudo systemctl status syncd",
            "sudo systemctl status teamd",
            "redis-cli KEYS PORT*",
            "redis-cli HGETALL 'PORT_TABLE:*'",
            "sudo journalctl -u swss --since '10 minutes ago' | tail -50",
            "cat /host/machine.conf",
            "show runningconfiguration | grep -i interface"
        ]
    
    @staticmethod
    def generate_fix_commands(issue_type: str) -> List[str]:
        """Generate fix commands based on issue type"""
        fixes = {
            "service_down": [
                "sudo systemctl restart swss",
                "sudo systemctl restart syncd",
                "sudo systemctl restart teamd",
                "sleep 10",
                "show interfaces status"
            ],
            "database_corrupt": [
                "sudo systemctl stop swss",
                "sudo rm -rf /var/run/redis/*",
                "sudo systemctl start swss",
                "sleep 15",
                "show interfaces status"
            ],
            "interface_not_configured": [
                "config interface startup Ethernet0",
                "config interface ip add Ethernet0 <ip>/<mask>",
                "show interfaces status"
            ],
            "hardware_issue": [
                "show platform summary",
                "show platform syseeprom",
                "dmesg | grep -i error",
                "lsmod | grep -i mlx"
            ]
        }
        return fixes.get(issue_type, [])




