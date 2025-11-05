#!/usr/bin/env python3
"""
Test stop/start on existing container and check BGP, OSPF, ISIS configs
Usage: python3 test_existing_container.py [server_url] [container_id]
"""

import requests
import json
import sys
import time

# Configuration
SERVER_URL = "http://localhost:5051"
CONTAINER_ID = "eed4fa662f47"

def find_device_for_container(server_url, container_id):
    """Find device associated with container ID."""
    print(f"Finding device for container: {container_id}")
    print("=" * 80)
    
    try:
        # Get all devices
        response = requests.get(f"{server_url}/api/device/database/devices", timeout=5)
        if response.status_code != 200:
            print(f"❌ Failed to get devices: {response.status_code}")
            return None
        
        devices_data = response.json()
        if isinstance(devices_data, dict):
            devices = devices_data.get("devices", [])
        elif isinstance(devices_data, list):
            devices = devices_data
        else:
            print(f"❌ Unexpected response format")
            return None
        
        # Find device - we'll check by container name pattern
        for device in devices:
            if not isinstance(device, dict):
                continue
            
            device_id = device.get("device_id", "")
            device_name = device.get("device_name", "")
            
            # Container name pattern: ostg-frr-{device_id}
            if device_id:
                print(f"Checking device: {device_name} (ID: {device_id[:8]}...)")
        
        # For now, let's try to find device1 or check all devices
        # We'll need to check the container name on the server
        print("⚠️  Need to check container name on server to match device")
        print("   Trying device1 first...")
        
        for device in devices:
            if device.get("device_name") == "device1":
                return device
        
        # Return first device if device1 not found
        if devices:
            return devices[0]
        
        return None
        
    except Exception as e:
        print(f"❌ Error finding device: {e}")
        return None

def check_frr_config(server_url, device_id, device_name):
    """Check FRR configuration via API if available."""
    print(f"\nChecking FRR configuration for {device_name}...")
    print("=" * 80)
    
    # Check device status
    try:
        response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
        if response.status_code == 200:
            device_data = response.json()
            
            print(f"Device Status: {device_data.get('status', 'Unknown')}")
            print(f"\nBGP Status:")
            print(f"  IPv4 State: {device_data.get('bgp_ipv4_state', 'Unknown')}")
            print(f"  IPv6 State: {device_data.get('bgp_ipv6_state', 'Unknown')}")
            print(f"  IPv4 Established: {device_data.get('bgp_ipv4_established', False)}")
            print(f"  IPv6 Established: {device_data.get('bgp_ipv6_established', False)}")
            
            print(f"\nOSPF Status:")
            print(f"  State: {device_data.get('ospf_state', 'Unknown')}")
            print(f"  Established: {device_data.get('ospf_established', False)}")
            
            print(f"\nISIS Status:")
            print(f"  State: {device_data.get('isis_state', 'Unknown')}")
            print(f"  Established: {device_data.get('isis_established', False)}")
            
            # Check configs
            bgp_config = device_data.get('bgp_config', {})
            if isinstance(bgp_config, str):
                try:
                    bgp_config = json.loads(bgp_config)
                except:
                    bgp_config = {}
            
            ospf_config = device_data.get('ospf_config', {})
            if isinstance(ospf_config, str):
                try:
                    ospf_config = json.loads(ospf_config)
                except:
                    ospf_config = {}
            
            isis_config = device_data.get('isis_config', {})
            if isinstance(isis_config, str):
                try:
                    isis_config = json.loads(isis_config)
                except:
                    isis_config = {}
            
            print(f"\nConfigurations:")
            print(f"  BGP Config: {bool(bgp_config)}")
            if bgp_config:
                print(f"    Neighbor IPv4: {bgp_config.get('bgp_neighbor_ipv4', 'N/A')}")
                print(f"    Neighbor IPv6: {bgp_config.get('bgp_neighbor_ipv6', 'N/A')}")
            
            print(f"  OSPF Config: {bool(ospf_config)}")
            if ospf_config:
                print(f"    Area ID: {ospf_config.get('area_id', 'N/A')}")
            
            print(f"  ISIS Config: {bool(isis_config)}")
            if isis_config:
                print(f"    Area ID: {isis_config.get('area_id', 'N/A')}")
            
            return device_data
        else:
            print(f"❌ Failed to get device data: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error checking FRR config: {e}")
        return None

def test_stop_start(server_url, device_info):
    """Test stop and start on device."""
    device_id = device_info.get("device_id")
    device_name = device_info.get("device_name", "")
    
    print(f"\n{'=' * 80}")
    print(f"Testing Stop/Start for {device_name}")
    print(f"{'=' * 80}\n")
    
    # Step 1: Check initial state
    print("Step 1: Checking initial state...")
    initial_state = check_frr_config(server_url, device_id, device_name)
    if not initial_state:
        return False
    
    # Step 2: Stop device
    print(f"\nStep 2: Stopping device...")
    try:
        stop_payload = {
            "device_id": device_id,
            "device_name": device_name,
            "interface": device_info.get("interface", ""),
            "vlan": device_info.get("vlan", "0"),
            "ipv4": device_info.get("ipv4_address", ""),
            "ipv6": device_info.get("ipv6_address", ""),
            "protocols": device_info.get("protocols", [])
        }
        
        # Add configs if they exist
        if device_info.get("bgp_config"):
            stop_payload["bgp_config"] = device_info.get("bgp_config")
        if device_info.get("ospf_config"):
            stop_payload["ospf_config"] = device_info.get("ospf_config")
        if device_info.get("isis_config"):
            stop_payload["isis_config"] = device_info.get("isis_config")
        
        response = requests.post(f"{server_url}/api/device/stop", json=stop_payload, timeout=30)
        
        if response.status_code == 200:
            print("✅ Device stopped successfully")
            time.sleep(3)
        else:
            print(f"❌ Failed to stop device: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error stopping device: {e}")
        return False
    
    # Step 3: Start device
    print(f"\nStep 3: Starting device...")
    try:
        start_payload = {
            "device_id": device_id,
            "device_name": device_name,
            "interface": device_info.get("interface", ""),
            "vlan": device_info.get("vlan", "0"),
            "ipv4": device_info.get("ipv4_address", ""),
            "ipv6": device_info.get("ipv6_address", ""),
            "protocols": device_info.get("protocols", [])
        }
        
        # Add configs if they exist
        if device_info.get("bgp_config"):
            start_payload["bgp_config"] = device_info.get("bgp_config")
        if device_info.get("ospf_config"):
            start_payload["ospf_config"] = device_info.get("ospf_config")
        if device_info.get("isis_config"):
            start_payload["isis_config"] = device_info.get("isis_config")
        
        response = requests.post(f"{server_url}/api/device/start", json=start_payload, timeout=30)
        
        if response.status_code == 200:
            print("✅ Device started successfully")
            print(f"   Waiting 10 seconds for protocols to start...")
            time.sleep(10)
        else:
            print(f"❌ Failed to start device: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error starting device: {e}")
        return False
    
    # Step 4: Check final state
    print(f"\nStep 4: Checking final state after start...")
    final_state = check_frr_config(server_url, device_id, device_name)
    if not final_state:
        return False
    
    # Step 5: Compare states
    print(f"\n{'=' * 80}")
    print("Comparison:")
    print(f"{'=' * 80}")
    
    print(f"\nBGP:")
    print(f"  Before: IPv4={initial_state.get('bgp_ipv4_state')}, IPv6={initial_state.get('bgp_ipv6_state')}")
    print(f"  After:  IPv4={final_state.get('bgp_ipv4_state')}, IPv6={final_state.get('bgp_ipv6_state')}")
    
    print(f"\nOSPF:")
    print(f"  Before: {initial_state.get('ospf_state')}")
    print(f"  After:  {final_state.get('ospf_state')}")
    
    print(f"\nISIS:")
    print(f"  Before: {initial_state.get('isis_state')}")
    print(f"  After:  {final_state.get('isis_state')}")
    
    # Check if protocols are running
    bgp_ok = final_state.get('bgp_ipv4_state') in ['Established', 'Active'] or final_state.get('bgp_ipv6_state') in ['Established', 'Active']
    ospf_ok = final_state.get('ospf_state') in ['Full', '2-Way', 'Running', 'Established']
    isis_ok = final_state.get('isis_state') in ['Up', 'Running', 'Established']
    
    print(f"\n{'=' * 80}")
    if bgp_ok:
        print("✅ BGP: Running")
    else:
        print("❌ BGP: Not running")
    
    if ospf_ok:
        print("✅ OSPF: Running")
    else:
        print("❌ OSPF: Not running")
    
    if isis_ok:
        print("✅ ISIS: Running")
    else:
        print("❌ ISIS: Not running")
    print(f"{'=' * 80}")
    
    return bgp_ok and ospf_ok and isis_ok

def main():
    """Main function."""
    server_url = sys.argv[1] if len(sys.argv) > 1 else SERVER_URL
    container_id = sys.argv[2] if len(sys.argv) > 2 else CONTAINER_ID
    
    print(f"Testing existing container: {container_id}")
    print(f"Server: {server_url}\n")
    
    # Find device for container
    device_info = find_device_for_container(server_url, container_id)
    
    if not device_info:
        print("❌ Could not find device for container")
        print("   Please specify device name manually")
        return
    
    device_name = device_info.get("device_name", "")
    device_id = device_info.get("device_id", "")
    
    print(f"✅ Found device: {device_name} (ID: {device_id[:8]}...)")
    
    # Check initial state
    print("\n" + "=" * 80)
    print("Initial State Check")
    print("=" * 80)
    check_frr_config(server_url, device_id, device_name)
    
    # Test stop/start
    success = test_stop_start(server_url, device_info)
    
    if success:
        print("\n✅ All protocols are running after stop/start")
        sys.exit(0)
    else:
        print("\n⚠️  Some protocols did not start properly")
        sys.exit(1)

if __name__ == "__main__":
    main()

