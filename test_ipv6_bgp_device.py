#!/usr/bin/env python3
"""
Test script to create a device with both IPv4 and IPv6 BGP configuration.
"""

import requests
import json
import uuid

def test_ipv6_bgp_device():
    server_url = "http://svl-hp-ai-srv04:5051"
    
    # Device configuration with both IPv4 and IPv6 BGP
    device_config = {
        "Device Name": "test-ipv6-bgp-device",
        "device_id": str(uuid.uuid4()),
        "Interface": "ens4np0",
        "MAC Address": "00:11:22:33:44:66",
        "IPv4": "192.168.0.3",
        "IPv6": "2001:db8::3",
        "ipv4_mask": "24",
        "ipv6_mask": "64",
        "VLAN": "22",
        "Gateway": "192.168.0.1",
        "IPv4 Gateway": "192.168.0.1",
        "IPv6 Gateway": "2001:db8::1",
        "Status": "Stopped",
        "protocols": {
            "BGP": {
                "bgp_asn": "65000",
                "bgp_remote_asn": "65001",
                "bgp_keepalive": "30",
                "bgp_hold_time": "90",
                "ipv4_enabled": True,
                "ipv6_enabled": True,
                "bgp_neighbor_ipv4": "192.168.0.1",
                "bgp_update_source_ipv4": "192.168.0.3",
                "bgp_neighbor_ipv6": "2001:db8::1",
                "bgp_update_source_ipv6": "2001:db8::3",
                "mode": "eBGP",
                "protocol": "dual-stack"
            }
        },
        "Protocols": "BGP"
    }
    
    print("🚀 Creating device with IPv4 and IPv6 BGP configuration...")
    print(f"📋 Device Config:")
    print(f"   Name: {device_config['Device Name']}")
    print(f"   IPv4: {device_config['IPv4']}/{device_config['ipv4_mask']}")
    print(f"   IPv6: {device_config['IPv6']}/{device_config['ipv6_mask']}")
    print(f"   IPv4 Gateway: {device_config['IPv4 Gateway']}")
    print(f"   IPv6 Gateway: {device_config['IPv6 Gateway']}")
    print(f"   BGP Protocol: {device_config['protocols']['BGP']['protocol']}")
    print(f"   IPv4 BGP Neighbor: {device_config['protocols']['BGP']['bgp_neighbor_ipv4']}")
    print(f"   IPv6 BGP Neighbor: {device_config['protocols']['BGP']['bgp_neighbor_ipv6']}")
    print()
    
    # Start the device
    try:
        response = requests.post(
            f"{server_url}/api/device/start",
            json=device_config,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Device created successfully!")
            print(f"📊 Response: {json.dumps(result, indent=2)}")
            
            # Wait a moment for the device to start
            import time
            time.sleep(5)
            
            # Check BGP status
            print("\n🔍 Checking BGP status...")
            bgp_response = requests.get(
                f"{server_url}/api/bgp/status/{device_config['device_id']}",
                timeout=10
            )
            
            if bgp_response.status_code == 200:
                bgp_status = bgp_response.json()
                print("📊 BGP Status:")
                print(json.dumps(bgp_status, indent=2))
            else:
                print(f"❌ Failed to get BGP status: {bgp_response.status_code}")
                print(bgp_response.text)
                
        else:
            print(f"❌ Failed to create device: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_ipv6_bgp_device()
