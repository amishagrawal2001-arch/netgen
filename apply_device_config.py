#!/usr/bin/env python3
"""
Script to apply device configuration to restore broken configs.
Usage: python3 apply_device_config.py [server_url] [device_name]
"""

import requests
import json
import sys

# Configuration
SERVER_URL = "http://localhost:5051"  # Change to your server URL if different
TEST_DEVICE_NAME = "device1"  # Change to a device name that exists in your system

def apply_device_config(server_url, device_name):
    """Apply device configuration to restore broken configs."""
    print("=" * 80)
    print("Device Configuration Apply")
    print("=" * 80)
    print(f"Server URL: {server_url}")
    print(f"Device: {device_name}")
    print()
    
    # Step 1: Get device information from database
    print("Step 1: Fetching device information from database...")
    try:
        # First, get all devices to find the device_id
        response = requests.get(f"{server_url}/api/device/database/devices", timeout=5)
        if response.status_code != 200:
            print(f"❌ Failed to get devices list: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
        
        devices_data = response.json()
        # Handle both list and dict responses
        if isinstance(devices_data, dict):
            devices = devices_data.get("devices", [])
        elif isinstance(devices_data, list):
            devices = devices_data
        else:
            print(f"❌ Unexpected response format: {type(devices_data)}")
            return False
        
        device_id = None
        device_info = None
        
        for device in devices:
            if not isinstance(device, dict):
                continue
            if device.get("device_name") == device_name or device.get("name") == device_name:
                device_id = device.get("device_id")
                device_info = device
                break
        
        if not device_id:
            print(f"❌ Device '{device_name}' not found in database")
            return False
        
        print(f"✅ Found device: {device_name} (ID: {device_id})")
        print(f"   Protocols: {device_info.get('protocols', [])}")
        print(f"   BGP Config: {bool(device_info.get('bgp_config'))}")
        print(f"   OSPF Config: {bool(device_info.get('ospf_config'))}")
        print(f"   ISIS Config: {bool(device_info.get('isis_config'))}")
        print()
        
        # Show BGP config details
        if device_info.get('bgp_config'):
            bgp_config = device_info.get('bgp_config')
            if isinstance(bgp_config, str):
                try:
                    bgp_config = json.loads(bgp_config)
                except:
                    bgp_config = {}
            print(f"   BGP Config Details:")
            print(f"      ASN: {bgp_config.get('bgp_asn', 'N/A')}")
            print(f"      Neighbor IPv4: {bgp_config.get('bgp_neighbor_ipv4', 'N/A')}")
            print(f"      Neighbor IPv6: {bgp_config.get('bgp_neighbor_ipv6', 'N/A')}")
            print(f"      Update Source IPv4: {bgp_config.get('bgp_update_source_ipv4', 'N/A')}")
            print(f"      Update Source IPv6: {bgp_config.get('bgp_update_source_ipv6', 'N/A')}")
            print()
        
        # Show OSPF config details
        if device_info.get('ospf_config'):
            ospf_config = device_info.get('ospf_config')
            if isinstance(ospf_config, str):
                try:
                    ospf_config = json.loads(ospf_config)
                except:
                    ospf_config = {}
            print(f"   OSPF Config Details:")
            print(f"      Area ID: {ospf_config.get('area_id', 'N/A')}")
            print(f"      IPv4 Enabled: {ospf_config.get('ipv4_enabled', 'N/A')}")
            print(f"      IPv6 Enabled: {ospf_config.get('ipv6_enabled', 'N/A')}")
            print()
        
        # Show ISIS config details
        if device_info.get('isis_config'):
            isis_config = device_info.get('isis_config')
            if isinstance(isis_config, str):
                try:
                    isis_config = json.loads(isis_config)
                except:
                    isis_config = {}
            print(f"   ISIS Config Details:")
            print(f"      Area ID: {isis_config.get('area_id', 'N/A')}")
            print(f"      System ID: {isis_config.get('system_id', 'N/A')}")
            print(f"      IPv4 Enabled: {isis_config.get('ipv4_enabled', 'N/A')}")
            print(f"      IPv6 Enabled: {isis_config.get('ipv6_enabled', 'N/A')}")
            print()
        
    except Exception as e:
        print(f"❌ Error fetching device info: {e}")
        return False
    
    # Step 2: Apply device configuration
    print("Step 2: Applying device configuration...")
    try:
        # Parse configs if they're strings
        bgp_config = device_info.get('bgp_config', {})
        if isinstance(bgp_config, str) and bgp_config:
            try:
                bgp_config = json.loads(bgp_config)
            except:
                bgp_config = {}
        
        ospf_config = device_info.get('ospf_config', {})
        if isinstance(ospf_config, str) and ospf_config:
            try:
                ospf_config = json.loads(ospf_config)
            except:
                ospf_config = {}
        
        isis_config = device_info.get('isis_config', {}) or device_info.get('is_is_config', {})
        if isinstance(isis_config, str) and isis_config:
            try:
                isis_config = json.loads(isis_config)
            except:
                isis_config = {}
        
        # Build apply payload
        apply_payload = {
            "device_id": device_id,
            "device_name": device_name,
            "interface": device_info.get("interface", ""),
            "vlan": device_info.get("vlan", "0"),
            "ipv4": device_info.get("ipv4_address", ""),
            "ipv6": device_info.get("ipv6_address", ""),
            "ipv4_mask": device_info.get("ipv4_mask", "24"),
            "ipv6_mask": device_info.get("ipv6_mask", "64"),
            "ipv4_gateway": device_info.get("ipv4_gateway", ""),
            "ipv6_gateway": device_info.get("ipv6_gateway", ""),
            "loopback_ipv4": device_info.get("loopback_ipv4", ""),
            "loopback_ipv6": device_info.get("loopback_ipv6", ""),
            "protocols": device_info.get("protocols", []),
            "bgp_config": bgp_config if bgp_config else None,
            "ospf_config": ospf_config if ospf_config else None,
            "isis_config": isis_config if isis_config else None,
        }
        
        print(f"   Apply Payload: protocols={apply_payload.get('protocols')}, "
              f"bgp_config={'present' if apply_payload.get('bgp_config') else 'missing'}, "
              f"ospf_config={'present' if apply_payload.get('ospf_config') else 'missing'}, "
              f"isis_config={'present' if apply_payload.get('isis_config') else 'missing'}")
        
        response = requests.post(f"{server_url}/api/device/apply", json=apply_payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Device configuration applied successfully")
            print(f"   Details: {result.get('details', {})}")
            print()
            return True
        else:
            print(f"❌ Failed to apply device configuration: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error applying device configuration: {e}")
        return False

def main():
    """Main function."""
    # Get server URL from command line or use default
    server_url = sys.argv[1] if len(sys.argv) > 1 else SERVER_URL
    device_name = sys.argv[2] if len(sys.argv) > 2 else TEST_DEVICE_NAME
    
    print(f"Applying device configuration")
    print(f"Server: {server_url}")
    print(f"Device: {device_name}")
    print()
    
    success = apply_device_config(server_url, device_name)
    
    if success:
        print("\n✅ Configuration applied successfully")
        print("   You can now test device stop/start functionality")
        sys.exit(0)
    else:
        print("\n❌ Failed to apply configuration")
        sys.exit(1)

if __name__ == "__main__":
    main()

