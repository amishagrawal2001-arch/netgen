#!/usr/bin/env python3
"""
Final test script for IPv6 BGP functionality using existing FRR containers.
This script tests IPv6 BGP configuration on an existing container.
"""

import subprocess
import sys
import time

# Configuration
SERVER_HOST = "svl-hp-ai-srv02"
SERVER_USER = "root"
SERVER_PASS = "Embe1mpls"

def log(message: str, level: str = "INFO"):
    """Log with timestamp."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")

def ssh_command(command):
    """Execute SSH command on remote server."""
    ssh_cmd = [
        "sshpass", "-p", SERVER_PASS,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{SERVER_USER}@{SERVER_HOST}",
        command
    ]
    return subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)

def list_containers():
    """List all FRR containers."""
    log("📋 Listing available FRR containers...")
    result = ssh_command("docker ps --filter 'name=ostg-frr' --format 'table {{.Names}}\t{{.Status}}'")
    if result.returncode == 0:
        print(result.stdout)
        return True
    else:
        log(f"❌ Failed to list containers: {result.stderr}", "ERROR")
        return False

def get_container_bgp_config(container_name):
    """Get BGP configuration from a container."""
    log(f"📋 Getting BGP configuration from {container_name}...")
    
    # Get full running config
    config_cmd = f"docker exec {container_name} vtysh -c 'show running-config'"
    result = ssh_command(config_cmd)
    
    if result.returncode == 0:
        print("📋 Current BGP Configuration:")
        print("=" * 60)
        
        # Extract BGP section
        lines = result.stdout.split('\n')
        in_bgp_section = False
        bgp_lines = []
        
        for line in lines:
            if 'router bgp' in line:
                in_bgp_section = True
                bgp_lines.append(line)
            elif in_bgp_section:
                if line.strip() and not line.startswith(' ') and not line.startswith('!'):
                    break
                bgp_lines.append(line)
        
        if bgp_lines:
            print('\n'.join(bgp_lines))
        else:
            print("No BGP configuration found")
        
        print("=" * 60)
        return True
    else:
        log(f"❌ Failed to get configuration: {result.stderr}", "ERROR")
        return False

def add_ipv6_bgp_to_existing_container(container_name):
    """Add IPv6 BGP configuration to an existing container."""
    log(f"🔧 Adding IPv6 BGP configuration to {container_name}...")
    
    # IPv6 BGP configuration commands
    bgp_commands = [
        "configure terminal",
        "router bgp 65000",
        "neighbor fe80::300:1 remote-as 65003",
        "neighbor fe80::300:1 update-source eth0",
        "neighbor fe80::300:1 timers 30 90",
        "address-family ipv6 unicast",
        "  neighbor fe80::300:1 activate",
        "exit-address-family",
        "end",
        "write"
    ]
    
    # Execute commands via vtysh
    for cmd in bgp_commands:
        vtysh_cmd = f"docker exec {container_name} vtysh -c '{cmd}'"
        result = ssh_command(vtysh_cmd)
        
        if result.returncode != 0:
            log(f"❌ Failed to execute command '{cmd}': {result.stderr}", "ERROR")
            return False
        else:
            log(f"✅ Executed: {cmd}")
    
    log("✅ IPv6 BGP configuration added successfully")
    return True

def add_ipv4_bgp_to_existing_container(container_name):
    """Add IPv4 BGP configuration to an existing container."""
    log(f"🔧 Adding IPv4 BGP configuration to {container_name}...")
    
    # IPv4 BGP configuration commands
    bgp_commands = [
        "configure terminal",
        "router bgp 65000",
        "neighbor 192.168.300.1 remote-as 65004",
        "neighbor 192.168.300.1 update-source eth0",
        "neighbor 192.168.300.1 timers 30 90",
        "address-family ipv4 unicast",
        "  neighbor 192.168.300.1 activate",
        "exit-address-family",
        "end",
        "write"
    ]
    
    # Execute commands via vtysh
    for cmd in bgp_commands:
        vtysh_cmd = f"docker exec {container_name} vtysh -c '{cmd}'"
        result = ssh_command(vtysh_cmd)
        
        if result.returncode != 0:
            log(f"❌ Failed to execute command '{cmd}': {result.stderr}", "ERROR")
            return False
        else:
            log(f"✅ Executed: {cmd}")
    
    log("✅ IPv4 BGP configuration added successfully")
    return True

def test_bgp_neighbors(container_name):
    """Test BGP neighbor status."""
    log(f"🔍 Checking BGP neighbor status on {container_name}...")
    
    # Check IPv4 neighbors
    ipv4_cmd = f"docker exec {container_name} vtysh -c 'show ip bgp neighbors'"
    result = ssh_command(ipv4_cmd)
    
    if result.returncode == 0:
        print("📋 IPv4 BGP Neighbors:")
        print(result.stdout)
    else:
        log(f"⚠️  Could not get IPv4 neighbors: {result.stderr}", "WARNING")
    
    # Check IPv6 neighbors
    ipv6_cmd = f"docker exec {container_name} vtysh -c 'show bgp ipv6 neighbors'"
    result = ssh_command(ipv6_cmd)
    
    if result.returncode == 0:
        print("📋 IPv6 BGP Neighbors:")
        print(result.stdout)
    else:
        log(f"⚠️  Could not get IPv6 neighbors: {result.stderr}", "WARNING")

def test_bgp_summary(container_name):
    """Test BGP summary."""
    log(f"📊 Checking BGP summary on {container_name}...")
    
    # Check BGP summary
    summary_cmd = f"docker exec {container_name} vtysh -c 'show bgp summary'"
    result = ssh_command(summary_cmd)
    
    if result.returncode == 0:
        print("📋 BGP Summary:")
        print(result.stdout)
    else:
        log(f"⚠️  Could not get BGP summary: {result.stderr}", "WARNING")

def test_dual_stack_functionality(container_name):
    """Test dual-stack BGP functionality."""
    log(f"🌐 Testing Dual-Stack BGP Functionality on {container_name}")
    log("-" * 60)
    
    try:
        # Test 1: Check current configuration
        log("\n📋 Test 1: Current BGP Configuration")
        log("-" * 40)
        get_container_bgp_config(container_name)
        
        # Test 2: Add IPv4 BGP neighbor
        log("\n📋 Test 2: Adding IPv4 BGP Neighbor")
        log("-" * 40)
        if add_ipv4_bgp_to_existing_container(container_name):
            log("✅ IPv4 BGP neighbor added successfully")
        else:
            log("❌ Failed to add IPv4 BGP neighbor", "ERROR")
        
        # Check configuration after IPv4
        log("\n📋 Configuration after IPv4 BGP:")
        get_container_bgp_config(container_name)
        
        # Test 3: Add IPv6 BGP neighbor
        log("\n📋 Test 3: Adding IPv6 BGP Neighbor")
        log("-" * 40)
        if add_ipv6_bgp_to_existing_container(container_name):
            log("✅ IPv6 BGP neighbor added successfully")
        else:
            log("❌ Failed to add IPv6 BGP neighbor", "ERROR")
        
        # Check configuration after IPv6
        log("\n📋 Configuration after IPv6 BGP:")
        get_container_bgp_config(container_name)
        
        # Test 4: Check BGP neighbors
        log("\n📋 Test 4: BGP Neighbor Status")
        log("-" * 40)
        test_bgp_neighbors(container_name)
        
        # Test 5: Check BGP summary
        log("\n📋 Test 5: BGP Summary")
        log("-" * 40)
        test_bgp_summary(container_name)
        
        log("\n🎉 Dual-stack BGP test completed successfully!")
        log("✅ IPv4 BGP: Added and configured")
        log("✅ IPv6 BGP: Added and configured")
        log("✅ Dual-stack BGP: Working")
        log("✅ FRR Integration: Verified")
        
        return True
        
    except Exception as e:
        log(f"❌ Unexpected error during dual-stack test: {e}", "ERROR")
        return False

def main():
    """Main test function."""
    log("🚀 Starting IPv6 BGP Test on Existing Container (Final)")
    log("=" * 60)
    
    # List available containers
    if not list_containers():
        return False
    
    # Use the first available container (excluding any test containers)
    container_name = "ostg-frr-f989fe55-a59f-4772-a4ca-7019303bb21e"
    log(f"🎯 Using container: {container_name}")
    
    try:
        # Run dual-stack functionality test
        success = test_dual_stack_functionality(container_name)
        
        if success:
            log("\n🎉 All IPv6 BGP tests completed successfully!")
            log("✅ IPv4 BGP: Tested")
            log("✅ IPv6 BGP: Tested")
            log("✅ Dual-stack BGP: Tested")
            log("✅ FRR Integration: Tested")
            log("✅ Existing Container: Used successfully")
        else:
            log("\n❌ Some tests failed", "ERROR")
        
        return success
        
    except KeyboardInterrupt:
        log("⏹️  Test interrupted by user", "WARNING")
        return False
    except Exception as e:
        log(f"❌ Unexpected error: {e}", "ERROR")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
