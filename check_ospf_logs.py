#!/usr/bin/env python3
"""
Check OSPF area ID logs from server
Usage: python3 check_ospf_logs.py [server_url] [device_name]
"""

import requests
import sys
import json

SERVER_URL = "http://svl-hp-ai-srv04:5051"

def check_ospf_config(server_url, device_name=None):
    """Check OSPF configuration from database."""
    print("=" * 80)
    print("Checking OSPF Configuration from Database")
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
    
    # Get all devices
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
            
            print(f"Found {len(devices)} device(s)")
            print()
            
            for device in devices:
                dev_name = device.get("Device Name") or device.get("device_name", "Unknown")
                
                # Filter by device name if provided
                if device_name and dev_name != device_name:
                    continue
                
                print("=" * 80)
                print(f"Device: {dev_name}")
                print("=" * 80)
                
                # Get OSPF config
                ospf_config = device.get("ospf_config", {})
                if isinstance(ospf_config, str):
                    try:
                        ospf_config = json.loads(ospf_config)
                    except:
                        ospf_config = {}
                
                if ospf_config:
                    print("OSPF Configuration:")
                    print(f"  area_id: {ospf_config.get('area_id', 'N/A')}")
                    print(f"  area_id_ipv4: {ospf_config.get('area_id_ipv4', 'N/A')}")
                    print(f"  area_id_ipv6: {ospf_config.get('area_id_ipv6', 'N/A')}")
                    print(f"  ipv4_enabled: {ospf_config.get('ipv4_enabled', 'N/A')}")
                    print(f"  ipv6_enabled: {ospf_config.get('ipv6_enabled', 'N/A')}")
                    print(f"  graceful_restart_ipv4: {ospf_config.get('graceful_restart_ipv4', 'N/A')}")
                    print(f"  graceful_restart_ipv6: {ospf_config.get('graceful_restart_ipv6', 'N/A')}")
                    print()
                    
                    # Check for route pools
                    route_pools = ospf_config.get("route_pools", {})
                    if route_pools:
                        print("Route Pools:")
                        if isinstance(route_pools, dict):
                            print(f"  IPv4: {route_pools.get('IPv4', [])}")
                            print(f"  IPv6: {route_pools.get('IPv6', [])}")
                        else:
                            print(f"  {route_pools}")
                        print()
                else:
                    print("No OSPF configuration found")
                    print()
                
                # Get device ID for direct query
                device_id = device.get("device_id")
                if device_id:
                    print(f"Device ID: {device_id}")
                    print()
                    print("To get latest config from database:")
                    print(f"  curl {server_url}/api/device/database/devices/{device_id}")
                    print()
                
                if device_name:
                    break
        else:
            print(f"❌ Failed to get devices: {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    server_url = sys.argv[1] if len(sys.argv) > 1 else SERVER_URL
    device_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    check_ospf_config(server_url, device_name)


