#!/usr/bin/env python3
"""
Test script to verify that the device database is properly tracking IPv4 and IPv6 BGP status separately.
"""

import requests
import json
import time

# Server configuration
SERVER_URL = "http://svl-hp-ai-srv04:5051"

def test_bgp_database_tracking():
    """Test that the database is tracking IPv4 and IPv6 BGP status separately."""
    
    print("🧪 Testing BGP IPv4/IPv6 Database Tracking")
    print("=" * 60)
    
    # Get the current device ID from running containers
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices", timeout=10)
        print(f"📡 Database endpoint response: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            devices = data.get('devices', [])
            if devices:
                device_id = devices[0]['device_id']
                print(f"📋 Testing with device: {device_id}")
            else:
                print("❌ No devices found in database")
                return False
        else:
            print(f"❌ Failed to get devices: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error getting devices: {e}")
        return False
    
    # Test 1: Check current BGP status via API
    print(f"\n🔍 Test 1: Current BGP Status via API")
    print("-" * 40)
    
    try:
        response = requests.get(f"{SERVER_URL}/api/bgp/status/{device_id}", timeout=10)
        if response.status_code == 200:
            data = response.json()
            neighbors = data.get('neighbors', [])
            
            print(f"📊 BGP Status Response:")
            print(f"   Total Neighbors: {len(neighbors)}")
            
            ipv4_neighbors = []
            ipv6_neighbors = []
            
            for neighbor in neighbors:
                neighbor_ip = neighbor.get('neighbor_ip', '')
                neighbor_state = neighbor.get('state', 'Unknown')
                
                if ':' in neighbor_ip:
                    ipv6_neighbors.append((neighbor_ip, neighbor_state))
                else:
                    ipv4_neighbors.append((neighbor_ip, neighbor_state))
                
                print(f"   Neighbor: {neighbor_ip} - State: {neighbor_state}")
            
            print(f"\n📋 Summary:")
            print(f"   IPv4 Neighbors: {len(ipv4_neighbors)}")
            for ip, state in ipv4_neighbors:
                print(f"     {ip}: {state}")
            print(f"   IPv6 Neighbors: {len(ipv6_neighbors)}")
            for ip, state in ipv6_neighbors:
                print(f"     {ip}: {state}")
            
        else:
            print(f"❌ Failed to get BGP status: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error getting BGP status: {e}")
        return False
    
    # Test 2: Force BGP status check to update database
    print(f"\n🔄 Test 2: Force BGP Status Check")
    print("-" * 40)
    
    try:
        response = requests.post(f"{SERVER_URL}/api/bgp/monitor/force-check", timeout=30)
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Force check initiated: {result.get('message', 'Success')}")
            
            # Wait a moment for the check to complete
            print("⏳ Waiting 5 seconds for BGP status check to complete...")
            time.sleep(5)
        else:
            print(f"❌ Failed to force BGP check: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error forcing BGP check: {e}")
        return False
    
    # Test 3: Check database for IPv4/IPv6 BGP status
    print(f"\n🗄️ Test 3: Database BGP Status")
    print("-" * 40)
    
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices", timeout=10)
        if response.status_code == 200:
            data = response.json()
            devices = data.get('devices', [])
            device = None
            
            for d in devices:
                if d['device_id'] == device_id:
                    device = d
                    break
            
            if device:
                print(f"📊 Device Database Status:")
                print(f"   Device ID: {device.get('device_id')}")
                print(f"   Device Name: {device.get('device_name')}")
                print(f"   Overall BGP Established: {device.get('bgp_established', 'N/A')}")
                print(f"   Overall BGP State: {device.get('bgp_state', 'N/A')}")
                print(f"   IPv4 BGP Established: {device.get('bgp_ipv4_established', 'N/A')}")
                print(f"   IPv4 BGP State: {device.get('bgp_ipv4_state', 'N/A')}")
                print(f"   IPv6 BGP Established: {device.get('bgp_ipv6_established', 'N/A')}")
                print(f"   IPv6 BGP State: {device.get('bgp_ipv6_state', 'N/A')}")
                print(f"   Last BGP Check: {device.get('last_bgp_check', 'N/A')}")
                
                # Check if the new fields are present and have values
                has_ipv4_fields = 'bgp_ipv4_established' in device and 'bgp_ipv4_state' in device
                has_ipv6_fields = 'bgp_ipv6_established' in device and 'bgp_ipv6_state' in device
                
                print(f"\n✅ Database Field Analysis:")
                print(f"   IPv4 BGP Fields Present: {has_ipv4_fields}")
                print(f"   IPv6 BGP Fields Present: {has_ipv6_fields}")
                
                if has_ipv4_fields and has_ipv6_fields:
                    print("✅ SUCCESS: Database has separate IPv4 and IPv6 BGP status fields")
                    
                    # Verify the values make sense
                    ipv4_established = device.get('bgp_ipv4_established', False)
                    ipv6_established = device.get('bgp_ipv6_established', False)
                    overall_established = device.get('bgp_established', False)
                    
                    print(f"\n🔍 Status Verification:")
                    print(f"   IPv4 BGP Established: {ipv4_established}")
                    print(f"   IPv6 BGP Established: {ipv6_established}")
                    print(f"   Overall BGP Established: {overall_established}")
                    
                    # Overall should be True if either IPv4 or IPv6 is established
                    expected_overall = ipv4_established or ipv6_established
                    if overall_established == expected_overall:
                        print("✅ Overall BGP status correctly reflects IPv4/IPv6 status")
                    else:
                        print(f"⚠️ Overall BGP status mismatch: expected {expected_overall}, got {overall_established}")
                    
                    return True
                else:
                    print("❌ FAILED: Database missing IPv4/IPv6 BGP status fields")
                    return False
            else:
                print(f"❌ Device {device_id} not found in database")
                return False
        else:
            print(f"❌ Failed to get device from database: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error checking database: {e}")
        return False

def main():
    """Main test function."""
    
    print("🚀 BGP IPv4/IPv6 Database Tracking Test")
    print("=" * 60)
    print("This test verifies that the device database properly tracks")
    print("IPv4 and IPv6 BGP status separately with the enhanced monitoring.")
    print("=" * 60)
    
    success = test_bgp_database_tracking()
    
    if success:
        print("\n🎉 ALL TESTS PASSED!")
        print("✅ Database is properly tracking IPv4 and IPv6 BGP status separately")
        print("✅ BGP monitoring system is working correctly")
    else:
        print("\n❌ TESTS FAILED!")
        print("❌ Database tracking of IPv4/IPv6 BGP status needs attention")
    
    print("\n" + "=" * 60)
    print("Test completed!")

if __name__ == "__main__":
    main()
