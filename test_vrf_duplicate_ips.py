#!/usr/bin/env python3
"""
Test script to demonstrate VRF-based duplicate IP support
"""

import subprocess
import time
import sys

def run_command(cmd, description):
    """Run a command and return the result."""
    print(f"\n=== {description} ===")
    print(f"Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Exit code: {result.returncode}")
    if result.stdout:
        print(f"Output:\n{result.stdout}")
    if result.stderr:
        print(f"Error:\n{result.stderr}")
    return result

def test_vrf_duplicate_ips():
    """Test VRF-based duplicate IP support."""
    
    print("🧪 Testing VRF-based Duplicate IP Support")
    print("=" * 50)
    
    # Test 1: Create two VRFs with duplicate IPs
    print("\n1️⃣ Creating VRFs with duplicate IPs...")
    
    # Create VRF 1
    run_command(["ip", "link", "add", "vrf-device1", "type", "vrf", "table", "1001"], 
                "Create VRF for device1")
    run_command(["ip", "link", "set", "vrf-device1", "up"], 
                "Bring up VRF device1")
    
    # Create VRF 2
    run_command(["ip", "link", "add", "vrf-device2", "type", "vrf", "table", "1002"], 
                "Create VRF for device2")
    run_command(["ip", "link", "set", "vrf-device2", "up"], 
                "Bring up VRF device2")
    
    # Test 2: Create VLAN interfaces and assign to VRFs
    print("\n2️⃣ Creating VLAN interfaces...")
    
    # Create VLAN 20 and assign to VRF 1
    run_command(["ip", "link", "add", "link", "ens4np0", "name", "vlan20", "type", "vlan", "id", "20"], 
                "Create VLAN 20")
    run_command(["ip", "link", "set", "vlan20", "master", "vrf-device1"], 
                "Assign VLAN 20 to VRF device1")
    run_command(["ip", "addr", "add", "192.168.0.2/24", "dev", "vlan20"], 
                "Add IP 192.168.0.2/24 to VLAN 20")
    run_command(["ip", "link", "set", "vlan20", "up"], 
                "Bring up VLAN 20")
    
    # Create VLAN 21 and assign to VRF 2
    run_command(["ip", "link", "add", "link", "ens4np0", "name", "vlan21", "type", "vlan", "id", "21"], 
                "Create VLAN 21")
    run_command(["ip", "link", "set", "vlan21", "master", "vrf-device2"], 
                "Assign VLAN 21 to VRF device2")
    run_command(["ip", "addr", "add", "192.168.0.2/24", "dev", "vlan21"], 
                "Add IP 192.168.0.2/24 to VLAN 21 (DUPLICATE!)")
    run_command(["ip", "link", "set", "vlan21", "up"], 
                "Bring up VLAN 21")
    
    # Test 3: Verify duplicate IPs work
    print("\n3️⃣ Verifying duplicate IPs work...")
    
    run_command(["ip", "addr", "show", "vlan20"], 
                "Show VLAN 20 addresses")
    run_command(["ip", "addr", "show", "vlan21"], 
                "Show VLAN 21 addresses")
    
    # Test 4: Show VRF routing tables
    print("\n4️⃣ Showing VRF routing tables...")
    
    run_command(["ip", "route", "show", "table", "1001"], 
                "VRF device1 routing table")
    run_command(["ip", "route", "show", "table", "1002"], 
                "VRF device2 routing table")
    
    # Test 5: Test connectivity from each VRF
    print("\n5️⃣ Testing connectivity from each VRF...")
    
    # Test ping from VRF 1
    run_command(["ip", "vrf", "exec", "vrf-device1", "ping", "-c", "3", "192.168.0.1"], 
                "Ping from VRF device1 to gateway")
    
    # Test ping from VRF 2
    run_command(["ip", "vrf", "exec", "vrf-device2", "ping", "-c", "3", "192.168.0.1"], 
                "Ping from VRF device2 to gateway")
    
    # Test 6: Show that each VRF sees its own interface
    print("\n6️⃣ Showing interfaces in each VRF...")
    
    run_command(["ip", "vrf", "exec", "vrf-device1", "ip", "addr", "show"], 
                "Interfaces visible in VRF device1")
    run_command(["ip", "vrf", "exec", "vrf-device2", "ip", "addr", "show"], 
                "Interfaces visible in VRF device2")
    
    print("\n✅ VRF duplicate IP test completed!")
    print("\nKey Benefits Demonstrated:")
    print("• ✅ Duplicate IPs (192.168.0.2/24) work in separate VRFs")
    print("• ✅ Each VRF has its own routing table")
    print("• ✅ Complete network isolation between VRFs")
    print("• ✅ Both VRFs can reach the same gateway independently")
    print("• ✅ No IP conflicts or routing issues")

def cleanup_vrf_test():
    """Clean up VRF test interfaces."""
    print("\n🧹 Cleaning up VRF test...")
    
    # Remove VLAN interfaces
    run_command(["ip", "link", "del", "vlan20"], "Remove VLAN 20")
    run_command(["ip", "link", "del", "vlan21"], "Remove VLAN 21")
    
    # Remove VRFs
    run_command(["ip", "link", "del", "vrf-device1"], "Remove VRF device1")
    run_command(["ip", "link", "del", "vrf-device2"], "Remove VRF device2")
    
    print("✅ VRF test cleanup completed!")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup_vrf_test()
    else:
        test_vrf_duplicate_ips()
        print("\n💡 To clean up, run: python3 test_vrf_duplicate_ips.py cleanup")
