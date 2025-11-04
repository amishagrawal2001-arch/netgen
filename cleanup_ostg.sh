#!/bin/bash

# OSTG Cleanup Script
# This script removes all OSTG containers, VLAN interfaces, and networks

set -e

echo "=========================================="
echo "OSTG Cleanup Script"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root"
        exit 1
    fi
}

# Function to stop and remove OSTG containers
cleanup_containers() {
    print_status "Cleaning up OSTG containers..."
    
    # Get all OSTG containers (running and stopped)
    containers=$(docker ps -a --filter "name=ostg" --format "{{.Names}}" 2>/dev/null || true)
    
    if [ -z "$containers" ]; then
        print_status "No OSTG containers found"
        return 0
    fi
    
    echo "Found OSTG containers:"
    echo "$containers"
    echo
    
    # Stop and remove each container
    for container in $containers; do
        print_status "Stopping and removing container: $container"
        
        # Stop container if running
        if docker ps --format "{{.Names}}" | grep -q "^${container}$"; then
            docker stop "$container" 2>/dev/null || print_warning "Failed to stop $container"
        fi
        
        # Remove container
        docker rm -f "$container" 2>/dev/null || print_warning "Failed to remove $container"
    done
    
    print_status "Container cleanup completed"
}

# Function to remove VLAN interfaces
cleanup_vlan_interfaces() {
    print_status "Cleaning up VLAN interfaces..."
    
    # Get all VLAN interfaces
    vlan_interfaces=$(ip link show | grep -E "vlan[0-9]+@" | sed 's/.*: \([^:]*\)@.*/\1/' 2>/dev/null || true)
    
    if [ -z "$vlan_interfaces" ]; then
        print_status "No VLAN interfaces found"
        return 0
    fi
    
    echo "Found VLAN interfaces:"
    echo "$vlan_interfaces"
    echo
    
    # Remove each VLAN interface
    for interface in $vlan_interfaces; do
        print_status "Removing VLAN interface: $interface"
        
        # Bring down interface first
        ip link set "$interface" down 2>/dev/null || print_warning "Failed to bring down $interface"
        
        # Remove interface
        ip link delete "$interface" 2>/dev/null || print_warning "Failed to remove $interface"
    done
    
    print_status "VLAN interface cleanup completed"
}

# Function to remove OSTG Docker networks
cleanup_networks() {
    print_status "Cleaning up OSTG Docker networks..."
    
    # Get all OSTG networks
    networks=$(docker network ls --filter "name=ostg" --format "{{.Name}}" 2>/dev/null || true)
    
    if [ -z "$networks" ]; then
        print_status "No OSTG networks found"
        return 0
    fi
    
    echo "Found OSTG networks:"
    echo "$networks"
    echo
    
    # Remove each network
    for network in $networks; do
        print_status "Removing network: $network"
        docker network rm "$network" 2>/dev/null || print_warning "Failed to remove network $network"
    done
    
    print_status "Network cleanup completed"
}

# Function to clean up routes that might have been added for OSTG
cleanup_routes() {
    print_status "Cleaning up OSTG-related routes..."
    
    # Remove routes that were added for OSTG bridge networks
    # These are typically routes via Docker bridge gateways
    routes_to_remove=(
        "192.168.0.0/24"
        "192.168.100.0/24" 
        "192.168.33.0/24"
    )
    
    for route in "${routes_to_remove[@]}"; do
        # Check if route exists and remove it
        if ip route show "$route" >/dev/null 2>&1; then
            print_status "Removing route: $route"
            ip route del "$route" 2>/dev/null || print_warning "Failed to remove route $route"
        fi
    done
    
    print_status "Route cleanup completed"
}

# Function to clean up any remaining OSTG processes
cleanup_processes() {
    print_status "Checking for OSTG-related processes..."
    
    # Look for any remaining FRR processes that might be related to OSTG
    frr_processes=$(ps aux | grep -E "(bgpd|ospfd|zebra)" | grep -v grep | awk '{print $2}' 2>/dev/null || true)
    
    if [ -n "$frr_processes" ]; then
        print_warning "Found FRR processes that might be related to OSTG:"
        ps aux | grep -E "(bgpd|ospfd|zebra)" | grep -v grep
        print_warning "These processes are not automatically killed as they might be system services"
    else
        print_status "No OSTG-related processes found"
    fi
}

# Function to show cleanup summary
show_summary() {
    echo
    echo "=========================================="
    echo "Cleanup Summary"
    echo "=========================================="
    
    # Check remaining containers
    remaining_containers=$(docker ps -a --filter "name=ostg" --format "{{.Names}}" 2>/dev/null || true)
    if [ -z "$remaining_containers" ]; then
        print_status "✓ No OSTG containers remaining"
    else
        print_warning "⚠ Remaining OSTG containers:"
        echo "$remaining_containers"
    fi
    
    # Check remaining VLAN interfaces
    remaining_vlans=$(ip link show | grep -E "vlan[0-9]+@" | sed 's/.*: \([^:]*\)@.*/\1/' 2>/dev/null || true)
    if [ -z "$remaining_vlans" ]; then
        print_status "✓ No VLAN interfaces remaining"
    else
        print_warning "⚠ Remaining VLAN interfaces:"
        echo "$remaining_vlans"
    fi
    
    # Check remaining networks
    remaining_networks=$(docker network ls --filter "name=ostg" --format "{{.Name}}" 2>/dev/null || true)
    if [ -z "$remaining_networks" ]; then
        print_status "✓ No OSTG networks remaining"
    else
        print_warning "⚠ Remaining OSTG networks:"
        echo "$remaining_networks"
    fi
    
    echo
    print_status "Cleanup completed!"
}

# Main execution
main() {
    echo "Starting OSTG cleanup..."
    echo
    
    # Check if running as root
    check_root
    
    # Perform cleanup steps
    cleanup_containers
    echo
    
    cleanup_vlan_interfaces
    echo
    
    cleanup_networks
    echo
    
    cleanup_routes
    echo
    
    cleanup_processes
    echo
    
    # Show summary
    show_summary
}

# Run main function
main "$@"
