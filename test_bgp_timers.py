#!/usr/bin/env python3
"""
Test script to verify BGP timer configuration is working correctly.
This script tests the BGP timer configuration flow from UI to FRR container.
"""

import json
import requests
import time
import sys

def test_bgp_timer_configuration():
    """Test BGP timer configuration with custom timer values."""
    
    print("🔍 BGP Timer Configuration Test")
    print("===============================")
    
    # Test configuration
    server_url = "http://svl-hp-ai-srv02:5051"
    test_device_id = "test-bgp-timer-device"
    test_device_name = "test-bgp-timer"
    
    # Custom timer values for testing
    test_keepalive = "15"
    test_hold_time = "45"
    
    print(f"Testing with custom timers: keepalive={test_keepalive}s, hold_time={test_hold_time}s")
    
    try:
        # Step 1: Start a test device
        print("\n1. Starting test device...")
        start_payload = {
            "device_id": test_device_id,
            "device_name": test_device_name,
            "interface": "ens5f1np1",
            "mac": "00:11:22:33:44:99",
            "vlan": "99",
            "ipv4": "192.168.99.2",
            "ipv6": "",
            "protocols": ["BGP"],
            "ipv4_mask": "24",
            "ipv6_mask": "64",
            "gateway": "192.168.99.1"
        }
        
        response = requests.post(f"{server_url}/api/device/start", json=start_payload, timeout=10)
        if response.status_code != 200:
            print(f"❌ Failed to start device: {response.text}")
            return False
        
        print("✅ Test device started successfully")
        
        # Step 2: Configure BGP with custom timers
        print("\n2. Configuring BGP with custom timers...")
        bgp_payload = {
            "device_id": test_device_id,
            "device_name": test_device_name,
            "interface": "ens5f1np1",
            "vlan": "99",
            "ipv4": "192.168.99.2",
            "ipv6": "",
            "gateway": "192.168.99.1",
            "bgp": {
                "bgp_asn": "65099",
                "bgp_remote_asn": "65098",
                "bgp_neighbor_ipv4": "192.168.99.1",
                "bgp_update_source_ipv4": "192.168.99.2",
                "mode": "eBGP",
                "protocol": "ipv4",
                "route_pools": {},
                "bgp_keepalive": test_keepalive,
                "bgp_hold_time": test_hold_time
            },
            "all_route_pools": []
        }
        
        response = requests.post(f"{server_url}/api/device/bgp/configure", json=bgp_payload, timeout=10)
        if response.status_code != 200:
            print(f"❌ Failed to configure BGP: {response.text}")
            return False
        
        print("✅ BGP configured successfully")
        
        # Step 3: Wait for configuration to take effect
        print("\n3. Waiting for BGP configuration to take effect...")
        time.sleep(5)
        
        # Step 4: Check BGP configuration in FRR container
        print("\n4. Checking BGP configuration in FRR container...")
        
        try:
            import docker
            client = docker.from_env()
            
            # Find the container
            container_name = f"ostg-frr-{test_device_name}"
            try:
                container = client.containers.get(container_name)
                
                # Check BGP configuration
                result = container.exec_run("vtysh -c 'show running-config' | grep -A 10 'router bgp'")
                if result.exit_code == 0:
                    config_output = result.output.decode('utf-8')
                    print("📋 BGP Configuration in FRR container:")
                    print(config_output)
                    
                    # Check if timer values are present
                    if f"timers {test_keepalive} {test_hold_time}" in config_output:
                        print(f"✅ Timer configuration found: timers {test_keepalive} {test_hold_time}")
                        return True
                    else:
                        print(f"❌ Timer configuration not found. Expected: timers {test_keepalive} {test_hold_time}")
                        return False
                else:
                    print(f"❌ Failed to get BGP configuration: {result.output.decode('utf-8')}")
                    return False
                    
            except docker.errors.NotFound:
                print(f"❌ Container {container_name} not found")
                return False
                
        except ImportError:
            print("❌ Docker Python library not available for testing")
            return False
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        return False
    
    finally:
        # Cleanup: Stop the test device
        print("\n5. Cleaning up test device...")
        try:
            cleanup_payload = {"device_id": test_device_id}
            response = requests.post(f"{server_url}/api/device/stop", json=cleanup_payload, timeout=10)
            if response.status_code == 200:
                print("✅ Test device stopped successfully")
            else:
                print(f"⚠️  Failed to stop test device: {response.text}")
        except Exception as e:
            print(f"⚠️  Error during cleanup: {e}")

if __name__ == "__main__":
    success = test_bgp_timer_configuration()
    if success:
        print("\n🎉 BGP Timer Configuration Test PASSED!")
        sys.exit(0)
    else:
        print("\n💥 BGP Timer Configuration Test FAILED!")
        sys.exit(1)
