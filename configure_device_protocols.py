#!/usr/bin/env python3
"""
Configure BGP, OSPF, and ISIS protocols for the device
"""

import requests
import json
import time
import sys

SERVER_URL = "http://svl-hp-ai-srv04:5051"

def configure_protocols():
    """Configure BGP, OSPF, and ISIS for the device"""
    
    device_id = "device-bgp-ospf-isis-001"
    device_name = "device-bgp-ospf-isis"
    
    print("=" * 60)
    print("Configuring Protocols for Device")
    print("=" * 60)
    print(f"Device ID: {device_id}")
    print(f"Device Name: {device_name}")
    print()
    
    # Step 1: Configure BGP
    print("Step 1: Configuring BGP...")
    bgp_config = {
        "device_id": device_id,
        "device_name": device_name,
        "bgp_config": {
            "bgp_asn": "65000",
            "bgp_remote_asn": "65001",
            "bgp_neighbor_ipv4": "192.168.20.1",
            "bgp_neighbor_ipv6": "2001:db8:20::1",
            "bgp_keepalive": "30",
            "bgp_hold_time": "90",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
            "bgp_update_source_ipv4": "192.168.20.2",
            "bgp_update_source_ipv6": "2001:db8:20::2"
        },
        "ipv4": "192.168.20.2",
        "ipv6": "2001:db8:20::2",
        "ipv4_mask": "24",
        "ipv6_mask": "64"
    }
    
    try:
        response = requests.post(f"{SERVER_URL}/api/device/bgp/configure", json=bgp_config, timeout=30)
        if response.status_code == 200:
            print("✅ BGP configured successfully")
        else:
            print(f"❌ Failed to configure BGP: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error configuring BGP: {e}")
        return False
    
    # Wait a bit
    time.sleep(2)
    
    # Step 2: Configure OSPF
    print("\nStep 2: Configuring OSPF...")
    ospf_config = {
        "device_id": device_id,
        "device_name": device_name,
        "ospf_config": {
            "area_id": "0.0.0.0",
            "dead_interval": "40",
            "graceful_restart": False,
            "hello_interval": "10",
            "interface": "vlan20",
            "ipv4_enabled": True,
            "ipv6_enabled": True,
            "router_id": "192.168.20.100"
        },
        "ipv4": "192.168.20.2",
        "ipv6": "2001:db8:20::2",
        "ipv4_mask": "24",
        "ipv6_mask": "64"
    }
    
    try:
        response = requests.post(f"{SERVER_URL}/api/device/ospf/configure", json=ospf_config, timeout=30)
        if response.status_code == 200:
            print("✅ OSPF configured successfully")
        else:
            print(f"❌ Failed to configure OSPF: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error configuring OSPF: {e}")
        return False
    
    # Wait a bit
    time.sleep(2)
    
    # Step 3: Configure ISIS
    print("\nStep 3: Configuring ISIS...")
    isis_config = {
        "device_id": device_id,
        "device_name": device_name,
        "isis_config": {
            "area_id": "49.0001.0000.0000.0001.00",
            "system_id": "0000.0000.0001",
            "level": "Level-2",
            "hello_interval": "10",
            "hello_multiplier": "3",
            "metric": "10",
            "interface": "vlan20",
            "ipv4_enabled": True,
            "ipv6_enabled": True
        },
        "ipv4": "192.168.20.2",
        "ipv6": "2001:db8:20::2",
        "ipv4_mask": "24",
        "ipv6_mask": "64"
    }
    
    try:
        response = requests.post(f"{SERVER_URL}/api/device/isis/configure", json=isis_config, timeout=30)
        if response.status_code == 200:
            print("✅ ISIS configured successfully")
        else:
            print(f"❌ Failed to configure ISIS: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error configuring ISIS: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ Protocol configuration completed!")
    print("=" * 60)
    
    # Wait a bit for configuration to take effect
    print("\n⏳ Waiting 3 seconds for configuration to take effect...")
    time.sleep(3)
    
    # Verify configuration
    print("\nVerifying FRR configuration...")
    print("You can check the configuration with:")
    print(f"  docker exec ostg-frr-{device_id} vtysh -c 'show running-config'")
    
    return True

if __name__ == "__main__":
    success = configure_protocols()
    sys.exit(0 if success else 1)



