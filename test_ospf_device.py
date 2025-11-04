#!/usr/bin/env python3
"""
Test script to create a device with OSPF and verify OSPF status fields in database
"""

import requests
import json
import time
import sys

SERVER_URL = "http://svl-hp-ai-srv04:5051"

def create_test_device():
    """Create a test device with OSPF configuration using /api/device/apply"""
    
    # Device configuration for /api/device/apply
    device_data = {
        "device_id": "test-ospf-device-001",
        "device_name": "test-ospf-device",
        "interface": "ens4np0",
        "vlan": "20",
        "ipv4": "192.168.0.2",
        "ipv6": "2001:db8::2",
        "ipv4_mask": "24",
        "ipv6_mask": "64",
        "ipv4_gateway": "192.168.0.1",
        "ipv6_gateway": "2001:db8::1",
        "protocols": ["OSPF", "BGP"],
        "bgp_config": {
            "bgp_asn": "65000",
            "bgp_remote_asn": "65001",
            "mode": "eBGP",
            "bgp_keepalive": "30",
            "bgp_hold_time": "90",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
            "local_ip": "192.168.0.2",
            "peer_ip": "192.168.0.1"
        },
        "ospf_config": {
            "area_id": "0.0.0.0",
            "dead_interval": "40",
            "graceful_restart": False,
            "hello_interval": "10",
            "interface": "vlan20",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
            "router_id": "192.168.0.2"
        }
    }
    
    print("Creating test device with OSPF configuration...")
    
    # Apply device configuration
    try:
        response = requests.post(f"{SERVER_URL}/api/device/apply", json=device_data, timeout=30)
        if response.status_code == 200:
            print("✅ Device created successfully")
            return device_data["device_id"]
        else:
            print(f"❌ Failed to create device: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Error creating device: {e}")
        return None

def check_device_ospf_fields(device_id):
    """Check if OSPF status fields exist in database"""
    
    print(f"\nChecking OSPF fields for device {device_id}...")
    
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices/{device_id}", timeout=10)
        if response.status_code == 200:
            data = response.json()
            
            # Check for OSPF fields
            ospf_fields = {k: v for k, v in data.items() if 'ospf' in k.lower()}
            
            print("OSPF fields in database:")
            print(json.dumps(ospf_fields, indent=2))
            
            # Check if required OSPF status fields exist
            required_fields = ['ospf_established', 'ospf_state', 'ospf_neighbors', 'last_ospf_check']
            missing_fields = [field for field in required_fields if field not in data]
            
            if missing_fields:
                print(f"❌ Missing OSPF status fields: {missing_fields}")
                return False
            else:
                print("✅ All OSPF status fields present in database")
                return True
                
        else:
            print(f"❌ Failed to get device: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error checking device: {e}")
        return False

def wait_for_ospf_monitor():
    """Wait for OSPF monitor to run and update database"""
    
    print("\nWaiting for OSPF monitor to run...")
    
    # Wait for OSPF monitor to run (30 second interval)
    time.sleep(35)
    
    print("OSPF monitor should have run by now")

def main():
    """Main test function"""
    
    print("🧪 Testing OSPF Database Schema Fix")
    print("=" * 50)
    
    # Step 1: Create test device
    device_id = create_test_device()
    if not device_id:
        print("❌ Test failed: Could not create device")
        sys.exit(1)
    
    # Step 2: Check OSPF fields immediately after creation
    print("\n📋 Step 1: Check OSPF fields immediately after creation")
    fields_exist = check_device_ospf_fields(device_id)
    
    # Step 3: Wait for OSPF monitor to run
    print("\n⏳ Step 2: Wait for OSPF monitor to run")
    wait_for_ospf_monitor()
    
    # Step 4: Check OSPF fields after monitor runs
    print("\n📋 Step 3: Check OSPF fields after OSPF monitor runs")
    fields_exist_after = check_device_ospf_fields(device_id)
    
    # Summary
    print("\n📊 Test Summary:")
    print("=" * 30)
    print(f"Device created: ✅")
    print(f"OSPF fields exist: {'✅' if fields_exist else '❌'}")
    print(f"OSPF monitor working: {'✅' if fields_exist_after else '❌'}")
    
    if fields_exist and fields_exist_after:
        print("\n🎉 SUCCESS: OSPF database schema fix is working!")
    else:
        print("\n❌ FAILED: OSPF database schema fix needs more work")

if __name__ == "__main__":
    main()
