#!/usr/bin/env python3
"""
Simple script to add a device with IPv4 and IPv6 configuration.
This will test the basic device creation functionality.
"""

import sys
import os
import json
import requests
import uuid

def add_test_device():
    """Add a test device with IPv4 and IPv6."""
    print("🔧 Adding Test Device with IPv4 and IPv6")
    print("=" * 40)
    
    # Server URL
    server_url = "http://svl-hp-ai-srv04:5051"
    
    # Generate unique device data
    device_id = str(uuid.uuid4())
    device_name = f"test-device-{device_id[:8]}"
    
    device_data = {
        "device_id": device_id,
        "device_name": device_name,
        "interface": "ens5np0",
        "mac": "00:11:22:33:44:55",
        "ipv4": "192.168.0.2",
        "ipv6": "2001:db8::100:1",
        "ipv4_mask": "24",
        "ipv6_mask": "64",
        "vlan": "100",
        "ipv4_gateway": "192.168.0.1",
        "ipv6_gateway": "2001:db8::100:254"
    }
    
    print(f"📋 Device Details:")
    print(f"   Name: {device_name}")
    print(f"   ID: {device_id}")
    print(f"   Interface: {device_data['interface']}")
    print(f"   MAC: {device_data['mac']}")
    print(f"   IPv4: {device_data['ipv4']}/{device_data['ipv4_mask']}")
    print(f"   IPv6: {device_data['ipv6']}/{device_data['ipv6_mask']}")
    print(f"   VLAN: {device_data['vlan']}")
    print(f"   IPv4 Gateway: {device_data['ipv4_gateway']}")
    print(f"   IPv6 Gateway: {device_data['ipv6_gateway']}")
    print()
    
    print("🚀 Creating device...")
    try:
        response = requests.post(f"{server_url}/api/device/resolve", json=device_data, timeout=30)
        if response.status_code == 200:
            print("   ✅ Device created successfully!")
            result = response.json()
            print(f"   Result: {json.dumps(result, indent=2)}")
            
            print("\n🎉 Device addition completed!")
            print("\n📝 Next steps:")
            print("   1. Check the OSTG UI to see the device")
            print("   2. Configure BGP for IPv4 and IPv6")
            print("   3. Start the device to create FRR container")
            print("   4. Test BGP neighbor configuration")
            
            return True
        else:
            print(f"   ❌ Device creation failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ❌ Exception creating device: {e}")
        return False

if __name__ == "__main__":
    success = add_test_device()
    sys.exit(0 if success else 1)

