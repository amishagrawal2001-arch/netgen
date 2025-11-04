#!/usr/bin/env python3
"""
Simple script to add interface status check to the ARP API
"""

import subprocess
import sys

def apply_interface_check_fix():
    """Apply the interface status check fix to the server file."""
    
    # Read the current server file
    with open('run_tgen_server.py', 'r') as f:
        content = f.read()
    
    # Find the ARP status API function
    if 'def get_device_arp_status(device_id):' not in content:
        print("❌ ARP status API function not found")
        return False
    
    # Check if the interface check is already present
    if 'Check if the network interface is actually up' in content:
        print("✅ Interface status check already present")
        return True
    
    # Find the location to insert the interface check
    # Look for the line after "Check if device is running"
    lines = content.split('\n')
    insert_index = -1
    
    for i, line in enumerate(lines):
        if 'Check if device is running' in line:
            # Find the end of the device running check block
            for j in range(i, len(lines)):
                if '}), 200' in lines[j] and 'Device not running' in lines[j-1]:
                    insert_index = j + 1
                    break
            break
    
    if insert_index == -1:
        print("❌ Could not find insertion point for interface check")
        return False
    
    # Interface check code to insert
    interface_check_code = '''        
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
                # Continue with ARP checks even if interface check fails'''
    
    # Insert the interface check code
    lines.insert(insert_index, interface_check_code)
    
    # Write the updated content
    with open('run_tgen_server.py', 'w') as f:
        f.write('\n'.join(lines))
    
    print("✅ Interface status check added to ARP API")
    return True

if __name__ == "__main__":
    if apply_interface_check_fix():
        print("✅ Fix applied successfully")
        sys.exit(0)
    else:
        print("❌ Fix failed")
        sys.exit(1)






