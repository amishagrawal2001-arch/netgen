#!/bin/bash
# Script to check FRR configuration in container
# Usage: ./check_frr_config.sh [device_name]

DEVICE_NAME="${1:-device1}"
SERVER="root@svl-hp-ai-srv04"

echo "Checking FRR configuration for device: $DEVICE_NAME"
echo "=========================================="
echo ""

# Find container
CONTAINER_ID=$(ssh $SERVER "docker ps --filter 'name=ostg-frr' --format '{{.ID}} {{.Names}}' | grep -i $DEVICE_NAME | head -1 | awk '{print \$1}'" 2>/dev/null)

if [ -z "$CONTAINER_ID" ]; then
    echo "❌ Container not found for device $DEVICE_NAME"
    exit 1
fi

echo "✅ Found container: $CONTAINER_ID"
echo ""
echo "FRR Configuration:"
echo "=========================================="

# Get FRR configuration
ssh $SERVER "docker exec $CONTAINER_ID vtysh -c 'sh run'" 2>&1 | head -100

echo ""
echo "BGP Neighbors:"
echo "=========================================="
ssh $SERVER "docker exec $CONTAINER_ID vtysh -c 'sh bgp summary'" 2>&1 | head -20

echo ""
echo "OSPF Neighbors:"
echo "=========================================="
ssh $SERVER "docker exec $CONTAINER_ID vtysh -c 'sh ip ospf neighbor'" 2>&1 | head -20

echo ""
echo "ISIS Neighbors:"
echo "=========================================="
ssh $SERVER "docker exec $CONTAINER_ID vtysh -c 'sh isis neighbor'" 2>&1 | head -20




