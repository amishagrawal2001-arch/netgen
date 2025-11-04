#!/usr/bin/env python3
"""
Script to add interface status check to ARP API
"""

import re

def add_interface_check():
    """Add interface status check to the ARP API."""
    
    # Read the server file
    with open('/opt/OSTG/run_tgen_server.py', 'r') as f:
        content = f.read()
    
    # Check if interface check is already present
    if 'Check if the network interface is actually up' in content:
        print("Interface check already present")
        return True
    
    # Find the location to insert the interface check
    # Look for the pattern after device running check
    pattern = r'(        }\), 200\n        \n        # Get device IP addresses)'
    
    # Interface check code to insert
    interface_check = '''        }), 200
        
        # Check if the network interface is actually up
        server_interface = device.get('server_interface')
        if server_interface:
            try:
                import subprocess
                # Check interface status using ip link show
                result = subprocess.run(["ip", "link", "show", server_interface], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # Check if interface is UP
                    if "state DOWN" in result.stdout:
                        return jsonify({
                            "arp_resolved": False,
                            "arp_ipv4_resolved": False,
                            "arp_ipv6_resolved": False,
                            "arp_gateway_resolved": False,
                            "arp_status": "Interface down",
                            "details": {"error": f"Interface {server_interface} is down"}
                        }), 200
                else:
                    # Interface doesn't exist
                    return jsonify({
                        "arp_resolved": False,
                        "arp_ipv4_resolved": False,
                        "arp_ipv6_resolved": False,
                        "arp_gateway_resolved": False,
                        "arp_status": "Interface not found",
                        "details": {"error": f"Interface {server_interface} not found"}
                    }), 200
            except Exception as e:
                logging.warning(f"[ARP STATUS] Failed to check interface status for {server_interface}: {e}")
                # Continue with ARP checks even if interface check fails
        
        # Get device IP addresses'''
    
    # Apply the replacement
    new_content = re.sub(pattern, interface_check, content)
    
    if new_content == content:
        print("Pattern not found, trying alternative approach")
        # Try a different pattern
        pattern2 = r'(        }\), 200\n        \n        # Get device IP addresses\n        ipv4_address = device\.get\(\'ipv4_address\'\))'
        replacement2 = '''        }), 200
        
        # Check if the network interface is actually up
        server_interface = device.get('server_interface')
        if server_interface:
            try:
                import subprocess
                # Check interface status using ip link show
                result = subprocess.run(["ip", "link", "show", server_interface], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # Check if interface is UP
                    if "state DOWN" in result.stdout:
                        return jsonify({
                            "arp_resolved": False,
                            "arp_ipv4_resolved": False,
                            "arp_ipv6_resolved": False,
                            "arp_gateway_resolved": False,
                            "arp_status": "Interface down",
                            "details": {"error": f"Interface {server_interface} is down"}
                        }), 200
                else:
                    # Interface doesn't exist
                    return jsonify({
                        "arp_resolved": False,
                        "arp_ipv4_resolved": False,
                        "arp_ipv6_resolved": False,
                        "arp_gateway_resolved": False,
                        "arp_status": "Interface not found",
                        "details": {"error": f"Interface {server_interface} not found"}
                    }), 200
            except Exception as e:
                logging.warning(f"[ARP STATUS] Failed to check interface status for {server_interface}: {e}")
                # Continue with ARP checks even if interface check fails
        
        # Get device IP addresses
        ipv4_address = device.get('ipv4_address')'''
        
        new_content = re.sub(pattern2, replacement2, content)
    
    if new_content != content:
        # Write the updated content
        with open('/opt/OSTG/run_tgen_server.py', 'w') as f:
            f.write(new_content)
        print("Interface check added successfully")
        return True
    else:
        print("Could not find insertion point")
        return False

if __name__ == "__main__":
    add_interface_check()






