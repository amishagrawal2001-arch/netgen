#!/usr/bin/env python3
"""
Simple script to query device database and display device information
Usage: python3 query_device_database.py [options]
"""

import requests
import json
import sys
import argparse
from datetime import datetime
from typing import Dict, List, Optional

# Server configuration
SERVER_URL = "http://svl-hp-ai-srv04:5051"

def print_header(title: str):
    """Print a formatted header"""
    print(f"\n{'='*60}")
    print(f"📊 {title}")
    print(f"{'='*60}")

def print_device_info(device: Dict, detailed: bool = False):
    """Print device information in a formatted way"""
    print(f"\n📋 Device: {device.get('device_name', 'Unknown')}")
    print(f"   ID: {device.get('device_id', 'N/A')}")
    print(f"   Interface: {device.get('interface', 'N/A')}")
    print(f"   Server Interface: {device.get('server_interface', 'N/A')}")
    print(f"   VLAN: {device.get('vlan', 'N/A')}")
    print(f"   Status: {device.get('status', 'N/A')}")
    
    if detailed:
        print(f"   IPv4 Address: {device.get('ipv4_address', 'N/A')}")
        print(f"   IPv4 Mask: {device.get('ipv4_mask', 'N/A')}")
        print(f"   IPv6 Address: {device.get('ipv6_address', 'N/A')}")
        print(f"   IPv6 Mask: {device.get('ipv6_mask', 'N/A')}")
        print(f"   IPv4 Gateway: {device.get('ipv4_gateway', 'N/A')}")
        print(f"   IPv6 Gateway: {device.get('ipv6_gateway', 'N/A')}")
        print(f"   MAC Address: {device.get('mac_address', 'N/A')}")
        print(f"   Protocols: {device.get('protocols', 'N/A')}")
        
        # Parse and display BGP config
        bgp_config = device.get('bgp_config')
        if bgp_config and bgp_config != '{}':
            try:
                if isinstance(bgp_config, str):
                    bgp_config = json.loads(bgp_config)
                print(f"   BGP Config: {json.dumps(bgp_config, indent=6)}")
            except:
                print(f"   BGP Config: {bgp_config}")
        
        # Parse and display OSPF config
        ospf_config = device.get('ospf_config')
        if ospf_config and ospf_config != '{}':
            try:
                if isinstance(ospf_config, str):
                    ospf_config = json.loads(ospf_config)
                print(f"   OSPF Config: {json.dumps(ospf_config, indent=6)}")
            except:
                print(f"   OSPF Config: {ospf_config}")
        
        # Parse and display ISIS config
        isis_config = device.get('isis_config')
        if isis_config and isis_config != '{}':
            try:
                if isinstance(isis_config, str):
                    # Handle single or double-encoded JSON strings
                    try:
                        isis_config = json.loads(isis_config)
                        if isinstance(isis_config, str):
                            isis_config = json.loads(isis_config)
                    except Exception:
                        pass
                print(f"   ISIS Config: {json.dumps(isis_config, indent=6)}")
            except:
                print(f"   ISIS Config: {isis_config}")
        
        print(f"   Created: {device.get('created_at', 'N/A')}")
        print(f"   Updated: {device.get('updated_at', 'N/A')}")
        print(f"   Last ARP Check: {device.get('last_arp_check', 'N/A')}")
        print(f"   ARP Status: {device.get('arp_status', 'N/A')}")
        print(f"   ARP IPv4 Resolved: {device.get('arp_ipv4_resolved', 'N/A')}")
        print(f"   ARP IPv6 Resolved: {device.get('arp_ipv6_resolved', 'N/A')}")
        print(f"   ARP Gateway Resolved: {device.get('arp_gateway_resolved', 'N/A')}")
        print(f"   BGP Established: {device.get('bgp_established', 'N/A')}")
        print(f"   BGP IPv4 Established: {device.get('bgp_ipv4_established', 'N/A')}")
        print(f"   BGP IPv6 Established: {device.get('bgp_ipv6_established', 'N/A')}")
        print(f"   BGP IPv4 State: {device.get('bgp_ipv4_state', 'N/A')}")
        print(f"   BGP IPv6 State: {device.get('bgp_ipv6_state', 'N/A')}")
        print(f"   Last BGP Check: {device.get('last_bgp_check', 'N/A')}")
        print(f"   OSPF Established: {device.get('ospf_established', 'N/A')}")
        print(f"   OSPF State: {device.get('ospf_state', 'N/A')}")
        print(f"   OSPF IPv4 Running: {device.get('ospf_ipv4_running', 'N/A')}")
        print(f"   OSPF IPv6 Running: {device.get('ospf_ipv6_running', 'N/A')}")
        print(f"   OSPF IPv4 Established: {device.get('ospf_ipv4_established', 'N/A')}")
        print(f"   OSPF IPv6 Established: {device.get('ospf_ipv6_established', 'N/A')}")
        print(f"   OSPF IPv4 Uptime: {device.get('ospf_ipv4_uptime', 'N/A')}")
        print(f"   OSPF IPv6 Uptime: {device.get('ospf_ipv6_uptime', 'N/A')}")
        print(f"   OSPF Neighbors: {device.get('ospf_neighbors', 'N/A')}")
        print(f"   Last OSPF Check: {device.get('last_ospf_check', 'N/A')}")
        print(f"   ISIS Running: {device.get('isis_running', 'N/A')}")
        print(f"   ISIS Established: {device.get('isis_established', 'N/A')}")
        print(f"   ISIS State: {device.get('isis_state', 'N/A')}")
        print(f"   ISIS System ID: {device.get('isis_system_id', 'N/A')}")
        print(f"   ISIS NET: {device.get('isis_net', 'N/A')}")
        print(f"   ISIS Uptime: {device.get('isis_uptime', 'N/A')}")
        print(f"   ISIS Neighbors: {device.get('isis_neighbors', 'N/A')}")
        print(f"   ISIS Areas: {device.get('isis_areas', 'N/A')}")
        print(f"   Last ISIS Check: {device.get('last_isis_check', 'N/A')}")

def print_protocol_summary(devices: List[Dict]):
    """Print a consolidated summary of all BGP, OSPF, and ISIS neighbor IP addresses"""
    print(f"\n{'='*80}")
    print(f"🔗 PROTOCOL NEIGHBOR SUMMARY")
    print(f"{'='*80}")
    
    # Collect all BGP neighbor IPs
    bgp_neighbors = []
    ospf_neighbors = []
    isis_neighbors = []
    
    # Track which devices we've processed for protocol summaries
    devices_seen = set()
    
    for device in devices:
        device_name = device.get('device_name', 'Unknown')
        device_ipv4 = device.get('ipv4_address', '')
        device_ipv6 = device.get('ipv6_address', '')
        
        # Parse BGP config
        bgp_config = device.get('bgp_config')
        if bgp_config and bgp_config != '{}':
            try:
                if isinstance(bgp_config, str):
                    bgp_config = json.loads(bgp_config)
                
                # Collect IPv4 BGP neighbors
                ipv4_neighbors = bgp_config.get('bgp_neighbor_ipv4', '')
                ipv4_neighbors_list = []
                if ipv4_neighbors:
                    for neighbor_ip in ipv4_neighbors.split(','):
                        neighbor_ip = neighbor_ip.strip()
                        if neighbor_ip:
                            ipv4_neighbors_list.append(neighbor_ip)
                
                # Collect IPv6 BGP neighbors
                ipv6_neighbors = bgp_config.get('bgp_neighbor_ipv6', '')
                ipv6_neighbors_list = []
                if ipv6_neighbors:
                    for neighbor_ip in ipv6_neighbors.split(','):
                        neighbor_ip = neighbor_ip.strip()
                        if neighbor_ip:
                            ipv6_neighbors_list.append(neighbor_ip)
                
                # Group BGP neighbors by device (similar to OSPF/ISIS)
                if ipv4_neighbors_list or ipv6_neighbors_list:
                    device_id_key = f"{device_name}:BGP"
                    if device_id_key not in devices_seen:
                        devices_seen.add(device_id_key)
                        bgp_neighbors.append({
                            'device': device_name,
                            'device_ipv4': device_ipv4,
                            'device_ipv6': device_ipv6,
                            'ipv4_neighbors': ipv4_neighbors_list,
                            'ipv6_neighbors': ipv6_neighbors_list,
                            'local_as': bgp_config.get('bgp_asn', ''),
                            'remote_as': bgp_config.get('bgp_remote_asn', ''),
                            'ipv4_established': device.get('bgp_ipv4_established', False),
                            'ipv6_established': device.get('bgp_ipv6_established', False),
                            'ipv4_state': device.get('bgp_ipv4_state', 'Unknown'),
                            'ipv6_state': device.get('bgp_ipv6_state', 'Unknown'),
                            'bgp_established': device.get('bgp_established', False)
                        })
            except Exception as e:
                print(f"   ⚠️  Error parsing BGP config for {device_name}: {e}")
        
        # Parse OSPF config
        ospf_config = device.get('ospf_config')
        if ospf_config and ospf_config != '{}':
            try:
                if isinstance(ospf_config, str):
                    ospf_config = json.loads(ospf_config)
                
                # OSPF doesn't have explicit neighbor IPs, but we can show the interface and area
                if ospf_config.get('ipv4_enabled') or ospf_config.get('ipv6_enabled'):
                    device_id_key = f"{device_name}:OSPF"  # Create unique key per device+protocol
                    if device_id_key not in devices_seen:
                        devices_seen.add(device_id_key)
                    
                    # Support both old format (area_id) and new format (ipv4_area_id, ipv6_area_id)
                    legacy_area_id = ospf_config.get('area_id', '')
                    ipv4_area_id = ospf_config.get('ipv4_area_id', legacy_area_id)
                    ipv6_area_id = ospf_config.get('ipv6_area_id', legacy_area_id)
                    
                    ospf_neighbors.append({
                        'device': device_name,
                        'device_ipv4': device_ipv4,
                        'device_ipv6': device_ipv6,
                        'interface': ospf_config.get('interface', ''),
                        'area_id': legacy_area_id,  # Keep for backward compatibility
                        'ipv4_area_id': ipv4_area_id,
                        'ipv6_area_id': ipv6_area_id,
                        'router_id': ospf_config.get('router_id', ''),
                        'ipv4_enabled': ospf_config.get('ipv4_enabled', False),
                        'ipv6_enabled': ospf_config.get('ipv6_enabled', False),
                        'ipv4_running': device.get('ospf_ipv4_running', False),
                        'ipv6_running': device.get('ospf_ipv6_running', False),
                        'ipv4_established': device.get('ospf_ipv4_established', False),
                        'ipv6_established': device.get('ospf_ipv6_established', False)
                    })
            except Exception as e:
                print(f"   ⚠️  Error parsing OSPF config for {device_name}: {e}")
        
        # Parse ISIS config
        isis_config = device.get('isis_config')
        if isis_config and isis_config != '{}':
            try:
                if isinstance(isis_config, str):
                    # Handle single or double-encoded JSON strings
                    try:
                        isis_config = json.loads(isis_config)
                        if isinstance(isis_config, str):
                            isis_config = json.loads(isis_config)
                    except Exception:
                        pass
                
                # ISIS doesn't have explicit neighbor IPs, but we can show the configuration
                # ISIS IPv4/IPv6 enabled is determined by whether device has IPv4/IPv6 addresses
                if isis_config:
                    device_id_key = f"{device_name}:ISIS"  # Create unique key per device+protocol
                    if device_id_key not in devices_seen:
                        devices_seen.add(device_id_key)
                        isis_neighbors.append({
                            'device': device_name,
                            'device_ipv4': device_ipv4,
                            'device_ipv6': device_ipv6,
                            'interface': isis_config.get('interface', ''),
                            'area_id': isis_config.get('area_id', ''),
                            'system_id': isis_config.get('system_id', ''),
                            'level': isis_config.get('level', ''),
                            # Determine IPv4/IPv6 enabled based on device addresses (ISIS enables based on IPs configured)
                            'ipv4_enabled': bool(device_ipv4 and device_ipv4.strip()),
                            'ipv6_enabled': bool(device_ipv6 and device_ipv6.strip()),
                            'running': device.get('isis_running', False),
                            'established': device.get('isis_established', False),
                            'state': device.get('isis_state', 'Unknown')
                        })
            except Exception as e:
                print(f"   ⚠️  Error parsing ISIS config for {device_name}: {e}")
    
    # Display BGP neighbors
    if bgp_neighbors:
        # Count total BGP neighbor IPs (not devices)
        total_bgp_neighbors = sum(len(n['ipv4_neighbors']) + len(n['ipv6_neighbors']) for n in bgp_neighbors)
        print(f"\n🌐 BGP NEIGHBORS ({total_bgp_neighbors} total)")
        print(f"{'-'*80}")
        for neighbor in bgp_neighbors:
            print(f"   Device: {neighbor['device']}")
            
            # IPv4 BGP neighbors and status
            if neighbor['ipv4_neighbors']:
                ipv4_status = "✗"
                if neighbor['ipv4_established']:
                    ipv4_status = "✓ Established"
                elif neighbor['ipv4_state'] not in ['Unknown', 'Idle']:
                    ipv4_status = f"⚠️ {neighbor['ipv4_state']}"
                else:
                    ipv4_status = f"⚠️ {neighbor['ipv4_state']}"
                
                print(f"   IPv4: {neighbor['device_ipv4']} (BGP: {ipv4_status})")
                for n in neighbor['ipv4_neighbors']:
                    print(f"      Neighbor: {n} (AS: {neighbor['local_as']} -> {neighbor['remote_as']})")
            
            # IPv6 BGP neighbors and status
            if neighbor['ipv6_neighbors']:
                ipv6_status = "✗"
                if neighbor['ipv6_established']:
                    ipv6_status = "✓ Established"
                elif neighbor['ipv6_state'] not in ['Unknown', 'Idle']:
                    ipv6_status = f"⚠️ {neighbor['ipv6_state']}"
                else:
                    ipv6_status = f"⚠️ {neighbor['ipv6_state']}"
                
                print(f"   IPv6: {neighbor['device_ipv6']} (BGP: {ipv6_status})")
                for n in neighbor['ipv6_neighbors']:
                    print(f"      Neighbor: {n} (AS: {neighbor['local_as']} -> {neighbor['remote_as']})")
            
            if not neighbor['ipv4_neighbors'] and not neighbor['ipv6_neighbors']:
                print(f"   ⚠️  No BGP neighbors configured")
            
            print()
    else:
        print(f"\n🌐 BGP NEIGHBORS: None configured")
    
    # Display OSPF neighbors
    if ospf_neighbors:
        print(f"\n🔗 OSPF NEIGHBORS ({len(ospf_neighbors)} total)")
        print(f"{'-'*80}")
        for neighbor in ospf_neighbors:
            print(f"   Device: {neighbor['device']}")
            
            # IPv4 OSPF status
            ipv4_status = "✗"
            if neighbor['ipv4_enabled']:
                if neighbor['ipv4_established']:
                    ipv4_status = "✓ Established"
                elif neighbor['ipv4_running']:
                    ipv4_status = "✓ Running"
                else:
                    ipv4_status = "⚠️ Enabled but not running"
            print(f"   IPv4: {neighbor['device_ipv4']} (OSPF: {ipv4_status})")
            
            # IPv6 OSPF status
            ipv6_status = "✗"
            if neighbor['ipv6_enabled']:
                if neighbor['ipv6_established']:
                    ipv6_status = "✓ Established"
                elif neighbor['ipv6_running']:
                    ipv6_status = "✓ Running"
                else:
                    ipv6_status = "⚠️ Enabled but not running"
            print(f"   IPv6: {neighbor['device_ipv6']} (OSPF: {ipv6_status})")
            
            print(f"   Interface: {neighbor['interface']}")
            # Display separate IPv4 and IPv6 area IDs if they're different, otherwise show single area
            ipv4_area = neighbor.get('ipv4_area_id', neighbor.get('area_id', ''))
            ipv6_area = neighbor.get('ipv6_area_id', neighbor.get('area_id', ''))
            if ipv4_area == ipv6_area:
                print(f"   Area: {ipv4_area}")
            else:
                print(f"   IPv4 Area: {ipv4_area}")
                print(f"   IPv6 Area: {ipv6_area}")
            print(f"   Router ID: {neighbor['router_id']}")
            print()
    else:
        print(f"\n🔗 OSPF NEIGHBORS: None configured")
    
    # Display ISIS neighbors
    if isis_neighbors:
        print(f"\n🔷 ISIS NEIGHBORS ({len(isis_neighbors)} total)")
        print(f"{'-'*80}")
        for neighbor in isis_neighbors:
            print(f"   Device: {neighbor['device']}")
            
            # Determine status (ISIS has single status for both IPv4 and IPv6)
            status = "✗"
            if neighbor['established']:
                status = "✓ Established"
            elif neighbor['running']:
                status = "✓ Running"
            else:
                status = "⚠️ Enabled but not running"
            
            # IPv4 ISIS status
            if neighbor['device_ipv4']:
                ipv4_status = "✗"
                if neighbor['ipv4_enabled']:
                    ipv4_status = status  # Use the same status as global ISIS status
                print(f"   IPv4: {neighbor['device_ipv4']} (ISIS: {ipv4_status})")
            
            # IPv6 ISIS status
            if neighbor['device_ipv6']:
                ipv6_status = "✗"
                if neighbor['ipv6_enabled']:
                    ipv6_status = status  # Use the same status as global ISIS status
                print(f"   IPv6: {neighbor['device_ipv6']} (ISIS: {ipv6_status})")
            
            print(f"   Interface: {neighbor['interface']}")
            print(f"   Area: {neighbor['area_id']}")
            print(f"   System ID: {neighbor['system_id']}")
            print(f"   Level: {neighbor['level']}")
            print(f"   State: {neighbor['state']}")
            print()
    else:
        print(f"\n🔷 ISIS NEIGHBORS: None configured")
    
    print(f"{'='*80}")

def get_database_info() -> Optional[Dict]:
    """Get database information"""
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/info")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get database info: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting database info: {e}")
        return None

def get_all_devices() -> Optional[List[Dict]]:
    """Get all devices from database"""
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices")
        if response.status_code == 200:
            devices_data = response.json()
            # Handle different response formats
            if isinstance(devices_data, list):
                return devices_data
            elif isinstance(devices_data, dict) and 'devices' in devices_data:
                return devices_data['devices']
            else:
                return [devices_data] if devices_data else []
        else:
            print(f"❌ Failed to get devices: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting devices: {e}")
        return None

def get_device_by_id(device_id: str) -> Optional[Dict]:
    """Get specific device by ID"""
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices/{device_id}")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get device {device_id}: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting device {device_id}: {e}")
        return None

def get_device_events(device_id: str) -> Optional[List[Dict]]:
    """Get device events"""
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices/{device_id}/events")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get events for {device_id}: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting events for {device_id}: {e}")
        return None

def get_device_statistics(device_id: str) -> Optional[Dict]:
    """Get device statistics"""
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/devices/{device_id}/statistics")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get statistics for {device_id}: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting statistics for {device_id}: {e}")
        return None

def get_all_route_pools() -> Optional[List[Dict]]:
    """Get all BGP route pools from database"""
    try:
        response = requests.get(f"{SERVER_URL}/api/bgp/pools")
        if response.status_code == 200:
            pools_data = response.json()
            return pools_data.get('pools', [])
        else:
            print(f"❌ Failed to get route pools: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting route pools: {e}")
        return None

def get_route_pool(pool_name: str) -> Optional[Dict]:
    """Get specific route pool by name"""
    try:
        response = requests.get(f"{SERVER_URL}/api/bgp/pools/{pool_name}")
        if response.status_code == 200:
            return response.json().get('pool')
        else:
            print(f"❌ Failed to get route pool '{pool_name}': HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting route pool '{pool_name}': {e}")
        return None

def get_device_route_pools(device_id: str) -> Optional[Dict]:
    """Get route pools attached to a specific device"""
    try:
        response = requests.get(f"{SERVER_URL}/api/device/{device_id}/route-pools")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get route pools for device {device_id}: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting route pools for device {device_id}: {e}")
        return None

def get_pool_usage(pool_name: str) -> Optional[Dict]:
    """Get usage information for a specific route pool"""
    try:
        response = requests.get(f"{SERVER_URL}/api/bgp/pools/{pool_name}/usage")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get usage for pool '{pool_name}': HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error getting usage for pool '{pool_name}': {e}")
        return None

def list_devices(detailed: bool = False):
    """List all devices"""
    print_header("Device List")
    
    devices = get_all_devices()
    if devices is None:
        return
    
    if not devices:
        print("📭 No devices found in database")
        return
    
    print(f"📊 Found {len(devices)} device(s)")
    
    for device in devices:
        print_device_info(device, detailed)

def show_device(device_id: str, show_events: bool = False, show_stats: bool = False):
    """Show specific device information"""
    print_header(f"Device Information: {device_id}")
    
    device = get_device_by_id(device_id)
    if device is None:
        return
    
    print_device_info(device, detailed=True)
    
    if show_events:
        print(f"\n📅 Events for {device_id}:")
        events = get_device_events(device_id)
        if events:
            for event in events:
                if isinstance(event, dict):
                    print(f"   - {event.get('event_type', 'Unknown')}: {event.get('description', 'N/A')} "
                          f"({event.get('timestamp', 'N/A')})")
                else:
                    print(f"   - {event}")
        else:
            print("   No events found")
    
    if show_stats:
        print(f"\n📈 Statistics for {device_id}:")
        stats = get_device_statistics(device_id)
        if stats:
            print(f"   {json.dumps(stats, indent=2)}")
        else:
            print("   No statistics available")

def show_database_info():
    """Show database information"""
    print_header("Database Information")
    
    db_info = get_database_info()
    if db_info is None:
        return
    
    print(f"📁 Database Path: {db_info.get('database_path', 'N/A')}")
    print(f"📊 Database Status: {db_info.get('status', 'N/A')}")
    print(f"💾 Database Size: {db_info.get('database_size_mb', 'N/A')} MB")
    print(f"🔢 Total Devices: {db_info.get('total_devices', 'N/A')}")
    print(f"🏃 Running Devices: {db_info.get('running_devices', 'N/A')}")
    print(f"⏹️  Stopped Devices: {db_info.get('stopped_devices', 'N/A')}")

def search_devices(search_term: str, detailed: bool = False):
    """Search devices by name or ID"""
    print_header(f"Search Results for: '{search_term}'")
    
    devices = get_all_devices()
    if devices is None:
        return
    
    matching_devices = []
    search_term_lower = search_term.lower()
    
    for device in devices:
        device_name = device.get('device_name', '').lower()
        device_id = device.get('device_id', '').lower()
        
        if search_term_lower in device_name or search_term_lower in device_id:
            matching_devices.append(device)
    
    if not matching_devices:
        print(f"🔍 No devices found matching '{search_term}'")
        return
    
    print(f"📊 Found {len(matching_devices)} matching device(s)")
    
    for device in matching_devices:
        print_device_info(device, detailed)

def print_route_pool_info(pool: Dict, detailed: bool = False):
    """Print route pool information in a formatted way"""
    print(f"\n🏊 Route Pool: {pool.get('name', 'Unknown')}")
    print(f"   Subnet: {pool.get('subnet', 'N/A')}")
    print(f"   Route Count: {pool.get('count', 'N/A')}")
    print(f"   First Host: {pool.get('first_host', 'N/A')}")
    print(f"   Last Host: {pool.get('last_host', 'N/A')}")
    
    if detailed:
        print(f"   Created: {pool.get('created_at', 'N/A')}")
        print(f"   Updated: {pool.get('updated_at', 'N/A')}")

def list_route_pools(detailed: bool = False):
    """List all BGP route pools"""
    print_header("BGP Route Pools")
    
    pools = get_all_route_pools()
    if pools is None:
        return
    
    if not pools:
        print("📭 No route pools found in database")
        return
    
    print(f"📊 Found {len(pools)} route pool(s)")
    
    for pool in pools:
        print_route_pool_info(pool, detailed)

def show_route_pool(pool_name: str, show_usage: bool = False):
    """Show specific route pool information"""
    print_header(f"Route Pool Information: {pool_name}")
    
    pool = get_route_pool(pool_name)
    if pool is None:
        return
    
    print_route_pool_info(pool, detailed=True)
    
    if show_usage:
        print(f"\n📈 Usage for {pool_name}:")
        usage = get_pool_usage(pool_name)
        if usage:
            print(f"   Device Count: {usage.get('device_count', 'N/A')}")
            print(f"   Neighbor Count: {usage.get('neighbor_count', 'N/A')}")
            print(f"   Usage Details:")
            for usage_item in usage.get('usage', []):
                print(f"     - Device: {usage_item.get('device_id', 'N/A')}")
                print(f"       Neighbor: {usage_item.get('neighbor_ip', 'N/A')}")
                print(f"       Attached: {usage_item.get('attached_at', 'N/A')}")
        else:
            print("   No usage information available")

def show_device_route_pools(device_id: str):
    """Show route pools attached to a specific device"""
    print_header(f"Route Pools for Device: {device_id}")
    
    device_pools = get_device_route_pools(device_id)
    if device_pools is None:
        return
    
    route_pools = device_pools.get('route_pools', {})
    neighbor_count = device_pools.get('neighbor_count', 0)
    
    if not route_pools:
        print("📭 No route pools attached to this device")
        return
    
    print(f"📊 Device has route pools for {neighbor_count} neighbor(s)")
    
    for neighbor_ip, pool_names in route_pools.items():
        print(f"\n🔗 Neighbor: {neighbor_ip}")
        print(f"   Attached Pools: {', '.join(pool_names)}")
        
        # Show details for each pool
        for pool_name in pool_names:
            pool = get_route_pool(pool_name)
            if pool:
                print(f"     - {pool_name}: {pool.get('subnet', 'N/A')} ({pool.get('count', 'N/A')} routes)")

def search_route_pools(search_term: str, detailed: bool = False):
    """Search route pools by name or subnet"""
    print_header(f"Route Pool Search Results for: '{search_term}'")
    
    pools = get_all_route_pools()
    if pools is None:
        return
    
    matching_pools = []
    search_term_lower = search_term.lower()
    
    for pool in pools:
        pool_name = pool.get('name', '').lower()
        subnet = pool.get('subnet', '').lower()
        
        if search_term_lower in pool_name or search_term_lower in subnet:
            matching_pools.append(pool)
    
    if not matching_pools:
        print(f"🔍 No route pools found matching '{search_term}'")
        return
    
    print(f"📊 Found {len(matching_pools)} matching route pool(s)")
    
    for pool in matching_pools:
        print_route_pool_info(pool, detailed)

def main():
    """Main function"""
    global SERVER_URL
    
    parser = argparse.ArgumentParser(description="Query OSTG Device Database and BGP Route Pools")
    parser.add_argument("--list", "-l", action="store_true", help="List all devices")
    parser.add_argument("--detailed", "-d", action="store_true", help="Show detailed information")
    parser.add_argument("--device", "-i", type=str, help="Show specific device by ID")
    parser.add_argument("--events", "-e", action="store_true", help="Show device events (requires --device)")
    parser.add_argument("--stats", "-s", action="store_true", help="Show device statistics (requires --device)")
    parser.add_argument("--search", "-q", type=str, help="Search devices by name or ID")
    parser.add_argument("--info", action="store_true", help="Show database information")
    parser.add_argument("--protocols", action="store_true", help="Show protocol neighbor summary")
    parser.add_argument("--server", type=str, default=SERVER_URL, help=f"Server URL (default: {SERVER_URL})")
    
    # Route pool specific arguments
    parser.add_argument("--pools", "-p", action="store_true", help="List all BGP route pools")
    parser.add_argument("--pool", type=str, help="Show specific route pool by name")
    parser.add_argument("--pool-usage", action="store_true", help="Show pool usage information (requires --pool)")
    parser.add_argument("--device-pools", type=str, help="Show route pools attached to specific device")
    parser.add_argument("--search-pools", type=str, help="Search route pools by name or subnet")
    
    args = parser.parse_args()
    
    # Update server URL if provided
    SERVER_URL = args.server
    
    print(f"🌐 Connecting to server: {SERVER_URL}")
    
    # Check server connectivity
    try:
        response = requests.get(f"{SERVER_URL}/api/device/database/info", timeout=5)
        if response.status_code != 200:
            print(f"❌ Server not responding: HTTP {response.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Cannot connect to server: {e}")
        sys.exit(1)
    
    print("✅ Connected to server successfully")
    
    # Execute requested action
    if args.info:
        show_database_info()
    elif args.protocols:
        # Show only protocol neighbor summary
        devices = get_all_devices()
        if devices:
            print_protocol_summary(devices)
        else:
            print("❌ No devices found")
    elif args.pool:
        show_route_pool(args.pool, args.pool_usage)
    elif args.device_pools:
        show_device_route_pools(args.device_pools)
    elif args.search_pools:
        search_route_pools(args.search_pools, args.detailed)
    elif args.pools:
        list_route_pools(args.detailed)
    elif args.device:
        show_device(args.device, args.events, args.stats)
    elif args.search:
        search_devices(args.search, args.detailed)
    elif args.list:
        list_devices(args.detailed)
    else:
        # Default action: show database info, list devices, and show protocol summary
        show_database_info()
        list_devices(args.detailed)
        
        # Show protocol neighbor summary
        devices = get_all_devices()
        if devices:
            print_protocol_summary(devices)
    
    print(f"\n🏁 Query completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
