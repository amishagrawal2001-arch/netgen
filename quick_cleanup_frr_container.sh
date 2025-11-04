#!/bin/bash

# Quick OSTG Cleanup Script
# Simple version for quick cleanup of OSTG resources

echo "Quick OSTG Cleanup..."

# Stop and remove all OSTG containers
echo "Removing OSTG containers..."
docker ps -a --filter "name=ostg" --format "{{.Names}}" | xargs -r docker rm -f

# Remove all VLAN interfaces
echo "Removing VLAN interfaces..."
ip link show | grep -E "vlan[0-9]+@" | sed 's/.*: \([^:]*\)@.*/\1/' | xargs -r -I {} ip link delete {}

# Remove OSTG networks
echo "Removing OSTG networks..."
docker network ls --filter "name=ostg" --format "{{.Name}}" | xargs -r docker network rm

echo "Quick cleanup completed!"
