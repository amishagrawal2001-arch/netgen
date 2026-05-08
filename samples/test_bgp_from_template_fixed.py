"""
Pytest Test Suite - BGP Test Plan Implementation
Generated from Test Plan: BGP test plan
Test Plan ID: e0062339-df61-480f-8a9b-14529a786106
Converted to use NetGenAI Test Framework
"""

import pytest

# NOTE: The framework automatically injects:
# 1. Device fixture: la_q5130_05_device (for device_name "la-q5130-05")
# 2. Helper function: execute_device_command(device_fixture, command)


def _get_output(device_fixture, command: str) -> str:
    """Run a command on the device and return stdout text."""
    result = execute_device_command(device_fixture, command)
    assert result["success"], f"Command failed: {result.get('error')}"
    return result["output"]


def _check_bgp_neighbor_established(device_fixture, neighbor_ip: str):
    """Check if BGP neighbor is Established."""
    output = _get_output(device_fixture, f"show bgp neighbor {neighbor_ip}")
    assert "Establ" in output or "Established" in output, \
        f"BGP neighbor {neighbor_ip} not Established. Output: {output[:300]}"


def _check_bgp_route_advertised(device_fixture, neighbor_ip: str, route: str):
    """Check if BGP route is advertised to neighbor."""
    output = _get_output(device_fixture, f"show bgp neighbor {neighbor_ip} advertised-routes")
    assert route in output or route.split("/")[0] in output, \
        f"Route {route} not advertised to {neighbor_ip}. Output: {output[:300]}"


def _check_bgp_route_filtered(device_fixture, neighbor_ip: str, route: str):
    """Check if BGP route is filtered (not advertised)."""
    output = _get_output(device_fixture, f"show bgp neighbor {neighbor_ip} advertised-routes")
    assert route not in output, f"Route {route} should be filtered but is advertised to {neighbor_ip}"


# Test Cases from Test Plan

def test_tc_001(la_q5130_05_device):
    """
    BGP Neighbor Configuration
    
    Description: Verify BGP neighbor configuration on Juniper switch
    Category: BGP Configuration
    Priority: High
    Expected Result: BGP neighbor is up and established, and advertising routes
    """
    neighbor_ip = "192.168.1.1"
    peer_as = "65000"
    
    # Step 1: Configure BGP neighbor IP address on Juniper switch
    config_cmd = (
        "configure\n"
        f"set protocols bgp group external type external\n"
        f"set protocols bgp group external neighbor {neighbor_ip} peer-as {peer_as}\n"
        "commit\n"
        "exit\n"
    )
    _get_output(la_q5130_05_device, config_cmd)
    
    # Step 2: Verify BGP neighbor is up and established
    _check_bgp_neighbor_established(la_q5130_05_device, neighbor_ip)
    
    # Step 3: Check BGP neighbor is advertising routes (verify neighbor appears in summary)
    summary_output = _get_output(la_q5130_05_device, "show bgp summary")
    assert neighbor_ip in summary_output, f"Neighbor {neighbor_ip} not found in BGP summary"


def test_tc_002(la_q5130_05_device):
    """
    BGP Route Advertisement
    
    Description: Verify BGP route advertisement on Juniper switch
    Category: BGP Route Advertisement
    Priority: Medium
    Expected Result: BGP route is advertised to neighbor and received by neighbor
    """
    neighbor_ip = "192.168.1.1"
    route = "192.168.10.0/24"
    
    # Step 1: Configure BGP route advertisement on Juniper switch
    config_cmd = (
        "configure\n"
        f"set policy-options policy-statement export-route term 1 from route-filter {route} exact\n"
        "set policy-options policy-statement export-route term 1 then accept\n"
        f"set protocols bgp group external neighbor {neighbor_ip} export export-route\n"
        "commit\n"
        "exit\n"
    )
    _get_output(la_q5130_05_device, config_cmd)
    
    # Step 2: Verify BGP route is advertised to neighbor
    _check_bgp_route_advertised(la_q5130_05_device, neighbor_ip, route)
    
    # Step 3: Check BGP route is received by neighbor (verify in routing table)
    route_output = _get_output(la_q5130_05_device, f"show route {route}")
    assert route in route_output or route.split("/")[0] in route_output, \
        f"Route {route} not found in routing table"


def test_tc_003(la_q5130_05_device):
    """
    BGP Route Filtering
    
    Description: Verify BGP route filtering on Juniper switch
    Category: BGP Route Filtering
    Priority: Low
    Expected Result: BGP route is filtered by Juniper switch and not advertised to neighbor
    """
    neighbor_ip = "192.168.1.1"
    route = "192.168.20.0/24"
    
    # Step 1: Configure BGP route filtering on Juniper switch
    config_cmd = (
        "configure\n"
        f"set policy-options policy-statement filter-route term 1 from route-filter {route} exact\n"
        "set policy-options policy-statement filter-route term 1 then reject\n"
        f"set protocols bgp group external neighbor {neighbor_ip} import filter-route\n"
        "commit\n"
        "exit\n"
    )
    _get_output(la_q5130_05_device, config_cmd)
    
    # Step 2: Verify BGP route is filtered by Juniper switch (check configuration)
    config_output = _get_output(la_q5130_05_device, "show configuration protocols bgp")
    assert "filter-route" in config_output, "Route filter policy not found in configuration"
    
    # Step 3: Check BGP route is not advertised to neighbor
    _check_bgp_route_filtered(la_q5130_05_device, neighbor_ip, route)


def test_tc_004(la_q5130_05_device):
    """
    BGP Neighbor Authentication
    
    Description: Verify BGP neighbor authentication on Juniper switch
    Category: BGP Neighbor Authentication
    Priority: High
    Expected Result: BGP neighbor authentication is successful, and neighbor is up and established
    """
    neighbor_ip = "192.168.1.1"
    auth_key = "my_secret_key"
    
    # Step 1: Configure BGP neighbor authentication on Juniper switch
    config_cmd = (
        "configure\n"
        f"set protocols bgp group external neighbor {neighbor_ip} authentication-key {auth_key}\n"
        "commit\n"
        "exit\n"
    )
    _get_output(la_q5130_05_device, config_cmd)
    
    # Step 2: Verify BGP neighbor authentication is successful (check configuration)
    config_output = _get_output(la_q5130_05_device, "show configuration protocols bgp")
    assert neighbor_ip in config_output, f"Neighbor {neighbor_ip} authentication not configured"
    assert "authentication-key" in config_output, "Authentication key not found in configuration"
    
    # Step 3: Check BGP neighbor is up and established
    _check_bgp_neighbor_established(la_q5130_05_device, neighbor_ip)


def test_tc_005(la_q5130_05_device):
    """
    BGP Session Establishment
    
    Description: Verify BGP session establishment on Juniper switch
    Category: BGP Session Establishment
    Priority: Medium
    Expected Result: BGP session is established and stable
    """
    neighbor_ip = "192.168.1.1"
    peer_as = "65000"
    
    # Step 1: Configure BGP session establishment on Juniper switch
    config_cmd = (
        "configure\n"
        f"set protocols bgp group external type external\n"
        f"set protocols bgp group external neighbor {neighbor_ip} peer-as {peer_as}\n"
        "commit\n"
        "exit\n"
    )
    _get_output(la_q5130_05_device, config_cmd)
    
    # Step 2: Verify BGP session is established
    _check_bgp_neighbor_established(la_q5130_05_device, neighbor_ip)
    
    # Step 3: Check BGP session is stable (verify in summary - check for Established state)
    summary_output = _get_output(la_q5130_05_device, "show bgp summary")
    assert neighbor_ip in summary_output, f"Neighbor {neighbor_ip} not found in BGP summary"
    assert "Establ" in summary_output or "Established" in summary_output, \
        "No Established BGP sessions found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

