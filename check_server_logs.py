#!/usr/bin/env python3
"""
Check server logs for device start/stop operations
Usage: python3 check_server_logs.py [server_url] [device_name]
"""

import requests
import sys
import json

SERVER_URL = "http://localhost:5051"

def check_device_start_logs(server_url, device_name=None):
    """Check for device start related logs."""
    print("=" * 80)
    print("Checking Server Logs")
    print("=" * 80)
    print(f"Server: {server_url}")
    print()
    
    # Check if server is accessible
    try:
        response = requests.get(f"{server_url}/api/health", timeout=5)
        if response.status_code == 200:
            print("✅ Server is accessible")
        else:
            print(f"⚠️  Server health check returned: {response.status_code}")
    except Exception as e:
        print(f"❌ Server not accessible: {e}")
        return
    
    print()
    print("=" * 80)
    print("Server Log Analysis")
    print("=" * 80)
    print()
    print("Note: This script checks server status and provides guidance on")
    print("where to find logs. Server logs are typically located in:")
    print("  - System logs (journalctl for systemd)")
    print("  - Application log files")
    print("  - Docker container logs")
    print()
    
    # Check if we can get device info
    if device_name:
        print(f"Checking device: {device_name}")
        print()
        try:
            response = requests.get(f"{server_url}/api/device/database/devices", timeout=5)
            if response.status_code == 200:
                devices_data = response.json()
                if isinstance(devices_data, dict):
                    devices = devices_data.get("devices", [])
                elif isinstance(devices_data, list):
                    devices = devices_data
                else:
                    devices = []
                
                for device in devices:
                    if device.get("device_name") == device_name:
                        device_id = device.get("device_id")
                        print(f"✅ Found device: {device_name} (ID: {device_id[:8]}...)")
                        print()
                        print("To check logs on server, run:")
                        print(f"  docker logs <container_id> | grep -i '{device_name}'")
                        print(f"  docker logs <container_id> | grep -i 'DEVICE START'")
                        print(f"  docker logs <container_id> | grep -i 'BGP\\|OSPF\\|ISIS'")
                        print()
                        print("Or check system logs:")
                        print(f"  journalctl -u ostg-server | grep -i '{device_name}'")
                        print(f"  journalctl -u ostg-server | grep -i 'DEVICE START'")
                        break
        except Exception as e:
            print(f"⚠️  Could not get device info: {e}")
    
    print()
    print("=" * 80)
    print("Key Log Messages to Look For:")
    print("=" * 80)
    print()
    print("When device starts, you should see:")
    print("  [DEVICE START] Protocol configs - BGP: True/False, OSPF: True/False, ISIS: True/False")
    print("  [DEVICE START] Configuring BGP in container")
    print("  [DEVICE START] Configuring OSPF in container")
    print("  [DEVICE START] Configuring ISIS in container")
    print("  [FRR] Configuring BGP in container")
    print("  [FRR] Configuring IPv4 BGP neighbor ...")
    print("  [FRR] Configuring IPv6 BGP neighbor ...")
    print()
    print("If BGP/OSPF are not configured, check for:")
    print("  - Missing bgp_config/ospf_config in payload")
    print("  - Empty or None configs")
    print("  - Configuration errors")
    print("  - Container not ready")
    print()

def main():
    """Main function."""
    server_url = sys.argv[1] if len(sys.argv) > 1 else SERVER_URL
    device_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    check_device_start_logs(server_url, device_name)

if __name__ == "__main__":
    main()
