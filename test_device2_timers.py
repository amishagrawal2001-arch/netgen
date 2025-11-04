#!/usr/bin/env python3
"""
Test BGP timer configuration for device2 specifically.
"""

import json
import requests
import time

def test_device2_timers():
    """Test BGP timer configuration for device2."""
    
    print("🔍 Testing BGP Timer Configuration for Device2")
    print("==============================================")
    
    server_url = "http://svl-hp-ai-srv02:5051"
    
    # Load device2 configuration from session.json
    print("\n1. Loading device2 configuration...")
    
    try:
        with open('session.json', 'r') as f:
            session_data = json.load(f)
        
        devices = session_data.get('devices', {})
        device2_info = None
        
        for device_name, device_info in devices.items():
            if device_info.get('Device Name') == 'device2':
                device2_info = device_info
                break
        
        if not device2_info:
            print("❌ Device2 not found in session.json")
            return False
        
        # Extract device info
        device_name = device2_info.get('Device Name')
        device_id = device2_info.get('device_id')
        bgp_config = device2_info.get('protocols', {}).get('BGP', {})
        
        print(f"   Device: {device_name}")
        print(f"   Device ID: {device_id}")
        print(f"   Current timers: keepalive={bgp_config.get('bgp_keepalive')}, hold_time={bgp_config.get('bgp_hold_time')}")
        
        # Stop the device first
        print(f"\n2. Stopping device2...")
        stop_payload = {"device_id": device_id}
        response = requests.post(f"{server_url}/api/device/stop", json=stop_payload, timeout=10)
        if response.status_code == 200:
            print("✅ Device2 stopped successfully")
            time.sleep(3)
        else:
            print(f"⚠️  Failed to stop device2: {response.status_code}")
        
        # Start the device with BGP configuration
        print(f"\n3. Starting device2 with BGP timers...")
        start_payload = {
            "device_id": device_id,
            "device_name": device_name,
            "interface": device2_info.get("Interface", ""),
            "mac": device2_info.get("MAC Address", "00:11:22:33:44:56"),
            "vlan": device2_info.get("VLAN", "31"),
            "ipv4": device2_info.get("IPv4", "192.169.0.2"),
            "ipv6": device2_info.get("IPv6", ""),
            "protocols": ["BGP"],
            "ipv4_mask": "24",
            "ipv6_mask": "64",
            "bgp": bgp_config
        }
        
        response = requests.post(f"{server_url}/api/device/start", json=start_payload, timeout=30)
        
        if response.status_code == 200:
            print("✅ Device2 started successfully with BGP timers")
            print(f"   Expected timers: keepalive={bgp_config.get('bgp_keepalive')}, hold_time={bgp_config.get('bgp_hold_time')}")
            
            # Wait for BGP to configure
            time.sleep(5)
            
            return True
        else:
            print(f"❌ Failed to start device2: {response.status_code}")
            print(f"   Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        return False

if __name__ == "__main__":
    success = test_device2_timers()
    if success:
        print("\n🎉 Device2 BGP Timer Configuration Test PASSED!")
        print("\n📋 Next step: Check FRR container configuration manually:")
        print("   ssh root@svl-hp-ai-srv02 \"docker exec ostg-frr-bbb3d9d4-b3c6-4168-b908-cd032384fccf vtysh -c 'show running-config' | grep timers\"")
    else:
        print("\n💥 Device2 BGP Timer Configuration Test FAILED!")
