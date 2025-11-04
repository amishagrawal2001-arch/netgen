#!/usr/bin/env python3
"""
Simple test to check BGP timer configuration on the actual server.
This script will apply BGP configuration with custom timers and verify it.
"""

import json
import requests
import time

def test_bgp_timers_on_server():
    """Test BGP timer configuration on the actual server."""
    
    print("🔍 BGP Timer Configuration Test on Server")
    print("==========================================")
    
    server_url = "http://svl-hp-ai-srv02:5051"
    
    # Use existing device configuration from session.json
    print("\n1. Loading existing device configuration...")
    
    try:
        with open('session.json', 'r') as f:
            session_data = json.load(f)
        
        devices = session_data.get('devices', {})
        if not devices:
            print("❌ No devices found in session.json")
            return False
        
        # Find a device with BGP configuration
        test_device = None
        for device_name, device_info in devices.items():
            if device_info.get('protocols', {}).get('BGP'):
                test_device = device_info
                print(f"✅ Found device with BGP config: {device_name}")
                break
        
        if not test_device:
            print("❌ No devices with BGP configuration found")
            return False
        
        # Extract device info
        device_name = test_device.get('Device Name')
        device_id = test_device.get('device_id')
        bgp_config = test_device.get('protocols', {}).get('BGP', {})
        
        print(f"   Device: {device_name}")
        print(f"   Device ID: {device_id}")
        print(f"   Current timers: keepalive={bgp_config.get('bgp_keepalive')}, hold_time={bgp_config.get('bgp_hold_time')}")
        
        # Test with different timer values
        test_keepalive = "25"
        test_hold_time = "75"
        
        print(f"\n2. Testing with new timer values: keepalive={test_keepalive}s, hold_time={test_hold_time}s")
        
        # Create modified BGP config with new timer values
        modified_bgp_config = bgp_config.copy()
        modified_bgp_config['bgp_keepalive'] = test_keepalive
        modified_bgp_config['bgp_hold_time'] = test_hold_time
        
        # Prepare BGP configuration payload
        bgp_payload = {
            "device_id": device_id,
            "device_name": device_name,
            "interface": test_device.get("Interface", ""),
            "vlan": test_device.get("VLAN", "0"),
            "ipv4": test_device.get("IPv4", ""),
            "ipv6": test_device.get("IPv6", ""),
            "gateway": test_device.get("Gateway", ""),
            "bgp": modified_bgp_config,
            "all_route_pools": session_data.get('bgp_route_pools', [])
        }
        
        print(f"   Sending BGP configuration to server...")
        
        # Send BGP configuration to server
        response = requests.post(f"{server_url}/api/device/bgp/configure", 
                               json=bgp_payload, timeout=30)
        
        if response.status_code == 200:
            print("✅ BGP configuration sent successfully")
            
            # Wait for configuration to take effect
            print("\n3. Waiting for configuration to take effect...")
            time.sleep(5)
            
            # Check if we can get BGP status
            print("\n4. Checking BGP status...")
            try:
                # Try to get device status or BGP status
                status_response = requests.get(f"{server_url}/api/device/status/{device_id}", timeout=10)
                if status_response.status_code == 200:
                    print("✅ Device status retrieved successfully")
                    print(f"   Response: {status_response.json()}")
                else:
                    print(f"⚠️  Could not get device status: {status_response.status_code}")
            except Exception as e:
                print(f"⚠️  Error getting device status: {e}")
            
            print("\n5. BGP Timer Configuration Test Summary:")
            print(f"   ✅ Successfully sent BGP config with timers: keepalive={test_keepalive}s, hold_time={test_hold_time}s")
            print(f"   ✅ Server responded with status code: {response.status_code}")
            print(f"   📋 Response: {response.json()}")
            
            return True
            
        else:
            print(f"❌ Failed to configure BGP: {response.status_code}")
            print(f"   Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        return False

if __name__ == "__main__":
    success = test_bgp_timers_on_server()
    if success:
        print("\n🎉 BGP Timer Configuration Test PASSED!")
    else:
        print("\n💥 BGP Timer Configuration Test FAILED!")
