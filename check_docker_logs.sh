#!/bin/bash
# Script to check Docker container logs for device operations
# Usage: ./check_docker_logs.sh [container_id] [device_name]

CONTAINER_ID="${1:-eed4fa662f47}"
DEVICE_NAME="${2:-device1}"
SERVER="svl-hp-ai-srv04"

echo "=========================================="
echo "Checking Docker Container Logs"
echo "=========================================="
echo "Container ID: $CONTAINER_ID"
echo "Device Name: $DEVICE_NAME"
echo "Server: $SERVER"
echo ""

echo "To check logs on server, SSH to server and run:"
echo ""
echo "  # Check container logs"
echo "  docker logs $CONTAINER_ID | grep -i '$DEVICE_NAME'"
echo ""
echo "  # Check for device start operations"
echo "  docker logs $CONTAINER_ID | grep -i 'DEVICE START'"
echo ""
echo "  # Check for BGP configuration"
echo "  docker logs $CONTAINER_ID | grep -i 'BGP' | tail -20"
echo ""
echo "  # Check for OSPF configuration"
echo "  docker logs $CONTAINER_ID | grep -i 'OSPF' | tail -20"
echo ""
echo "  # Check for ISIS configuration"
echo "  docker logs $CONTAINER_ID | grep -i 'ISIS' | tail -20"
echo ""
echo "  # Check for FRR configuration"
echo "  docker logs $CONTAINER_ID | grep -i 'FRR' | tail -20"
echo ""
echo "  # Check for protocol configs"
echo "  docker logs $CONTAINER_ID | grep -i 'protocol configs'"
echo ""
echo "  # Check recent logs (last 50 lines)"
echo "  docker logs $CONTAINER_ID --tail 50"
echo ""
echo "=========================================="
echo ""
echo "If you can SSH to the server, run these commands:"
echo ""
echo "ssh root@$SERVER 'docker logs $CONTAINER_ID | grep -i \"DEVICE START\" | tail -30'"
echo "ssh root@$SERVER 'docker logs $CONTAINER_ID | grep -i \"BGP\\|OSPF\\|ISIS\" | tail -30'"
echo "ssh root@$SERVER 'docker logs $CONTAINER_ID --tail 100 | grep -i \"protocol configs\"'"







