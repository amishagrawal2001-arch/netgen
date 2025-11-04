#!/usr/bin/env python3
"""
Test script to verify route pool generation functionality
"""

import ipaddress

def generate_host_routes_from_pool(network, count):
    """Generate individual host routes from a network pool."""
    try:
        # Get all host addresses from the network
        hosts = list(network.hosts())
        
        if network.version == 6:
            # For IPv6, use all addresses (no broadcast)
            hosts = list(network)
            # Remove the network address (first address)
            if len(hosts) > 1:
                hosts = hosts[1:]
        
        if len(hosts) < count:
            raise ValueError(f"Not enough host addresses in network {network}")
        
        # Take the first 'count' host addresses and format as /32 or /128 routes
        selected_hosts = hosts[:count]
        
        if network.version == 4:
            # IPv4: use /32 for individual host routes
            return [f"{host}/32" for host in selected_hosts]
        else:
            # IPv6: use /128 for individual host routes
            return [f"{host}/128" for host in selected_hosts]
            
    except Exception as e:
        print(f"Error generating host routes: {e}")
        return []

def generate_host_ips(network, count):
    """Generate first and last host IPs from a network for the specified count."""
    try:
        # Get all host addresses from the network
        hosts = list(network.hosts())
        
        if network.version == 6:
            # For IPv6, use all addresses (no broadcast)
            hosts = list(network)
            # Remove the network address (first address)
            if len(hosts) > 1:
                hosts = hosts[1:]
        
        if len(hosts) < count:
            raise ValueError(f"Not enough host addresses in network {network}")
        
        # Take the first 'count' host addresses
        selected_hosts = hosts[:count]
        
        first_host = selected_hosts[0]
        last_host = selected_hosts[-1]
        
        return first_host, last_host
        
    except Exception as e:
        raise ValueError(f"Error generating host IPs: {str(e)}")

def test_route_pool_generation():
    """Test the route pool generation with various scenarios."""
    
    print("=== Route Pool Generation Test ===\n")
    
    # Test cases
    test_cases = [
        {
            "name": "IPv4 /24 subnet with 10 routes",
            "subnet": "192.168.1.0/24",
            "count": 10
        },
        {
            "name": "IPv4 /30 subnet with 2 routes",
            "subnet": "10.0.0.0/30",
            "count": 2
        },
        {
            "name": "IPv6 /64 subnet with 5 routes",
            "subnet": "2001:db8::/64",
            "count": 5
        },
        {
            "name": "IPv4 /28 subnet with 10 routes",
            "subnet": "172.16.0.0/28",
            "count": 10
        }
    ]
    
    for test_case in test_cases:
        print(f"Test: {test_case['name']}")
        print(f"Subnet: {test_case['subnet']}")
        print(f"Requested routes: {test_case['count']}")
        
        try:
            # Parse the subnet
            network = ipaddress.ip_network(test_case['subnet'], strict=False)
            
            # Check available addresses
            available_hosts = network.num_addresses - 2  # Subtract network and broadcast addresses
            if network.version == 6:
                available_hosts = network.num_addresses - 1  # IPv6 doesn't have broadcast
            
            print(f"Available host addresses: {available_hosts}")
            
            if test_case['count'] > available_hosts:
                print(f"❌ ERROR: Requested {test_case['count']} routes but only {available_hosts} available")
                print()
                continue
            
            # Generate first and last host IPs
            first_host, last_host = generate_host_ips(network, test_case['count'])
            print(f"First host IP: {first_host}")
            print(f"Last host IP: {last_host}")
            
            # Generate all host routes
            host_routes = generate_host_routes_from_pool(network, test_case['count'])
            print(f"Generated {len(host_routes)} routes:")
            
            # Show first few and last few routes
            if len(host_routes) <= 10:
                for route in host_routes:
                    print(f"  {route}")
            else:
                for route in host_routes[:5]:
                    print(f"  {route}")
                print(f"  ... ({len(host_routes) - 10} more routes) ...")
                for route in host_routes[-5:]:
                    print(f"  {route}")
            
            print("✅ SUCCESS")
            
        except Exception as e:
            print(f"❌ ERROR: {e}")
        
        print()

if __name__ == "__main__":
    test_route_pool_generation()






