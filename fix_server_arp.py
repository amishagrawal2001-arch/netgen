#!/usr/bin/env python3
"""
Script to fix the ARP API on the server to check interface status
"""

import subprocess
import sys

def fix_server_arp():
    """Fix the ARP API on the server to check interface status."""
    
    # SSH command to fix the server file
    fix_command = '''
# Backup the current file
cp /opt/OSTG/run_tgen_server.py /opt/OSTG/run_tgen_server.py.backup

# Create a Python script to fix the ARP API
cat > /tmp/fix_arp_api.py << 'EOF'
import re

# Read the current server file
with open('/opt/OSTG/run_tgen_server.py', 'r') as f:
    content = f.read()

# Check if interface check is already present
if 'Check if the network interface is actually up' in content:
    print("Interface check already present")
    exit(0)

# Find the location to insert the interface check
# Look for the line after the device running check
pattern = r'(        }\), 200\n        \n        # Get device IP addresses)'
replacement = r'''        }), 200
        
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

# Apply the fix
new_content = re.sub(pattern, replacement, content)

# Write the updated content
with open('/opt/OSTG/run_tgen_server.py', 'w') as f:
    f.write(new_content)

print("Interface check added to ARP API")
EOF

# Run the fix script
python3 /tmp/fix_arp_api.py

# Restart the server
systemctl restart ostg-server

# Check if server is running
sleep 2
systemctl status ostg-server --no-pager -l
'''
    
    # Execute the fix on the server
    try:
        result = subprocess.run([
            'sshpass', '-p', 'Embe1mpls', 'ssh', 'root@svl-hp-ai-srv04', fix_command
        ], capture_output=True, text=True, timeout=60)
        
        print("STDOUT:", result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        if result.returncode == 0:
            print("✅ Server ARP API fixed successfully")
            return True
        else:
            print(f"❌ Fix failed with return code: {result.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Fix timed out")
        return False
    except Exception as e:
        print(f"❌ Fix failed: {e}")
        return False

if __name__ == "__main__":
    if fix_server_arp():
        print("✅ Server fix completed")
        sys.exit(0)
    else:
        print("❌ Server fix failed")
        sys.exit(1)






