#!/bin/bash
# Script to verify static route is added to FRR container

echo "════════════════════════════════════════════════════════════════════════════════"
echo "            FRR Container Static Route Verification Script"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""

# Check if device_id or device_name is provided
if [ -z "$1" ]; then
    echo "❌ ERROR: Please provide device ID or device name"
    echo ""
    echo "Usage: $0 <device_id_or_name>"
    echo ""
    echo "Example:"
    echo "  $0 device1"
    echo "  $0 abc123-def456-ghi789"
    echo ""
    echo "Available FRR containers:"
    docker ps --filter "name=frr_device_" --format "table {{.Names}}\t{{.Status}}" | head -20
    exit 1
fi

DEVICE_ID="$1"

# Find matching container
echo "🔍 Searching for FRR container matching: $DEVICE_ID"
CONTAINER=$(docker ps --filter "name=frr_device_" --format "{{.Names}}" | grep -i "$DEVICE_ID" | head -1)

if [ -z "$CONTAINER" ]; then
    echo "❌ ERROR: No FRR container found matching '$DEVICE_ID'"
    echo ""
    echo "Available FRR containers:"
    docker ps --filter "name=frr_device_" --format "table {{.Names}}\t{{.Status}}"
    exit 1
fi

echo "✅ Found container: $CONTAINER"
echo ""

# Check if container is running
STATUS=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null)
if [ "$STATUS" != "running" ]; then
    echo "❌ ERROR: Container $CONTAINER is not running (status: $STATUS)"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 FRR ROUTING TABLE:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
docker exec "$CONTAINER" vtysh -c 'show ip route' 2>/dev/null

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 CHECKING FOR DEFAULT ROUTE (0.0.0.0/0):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

DEFAULT_ROUTE=$(docker exec "$CONTAINER" vtysh -c 'show ip route' 2>/dev/null | grep "0.0.0.0/0")

if [ -n "$DEFAULT_ROUTE" ]; then
    echo "✅ DEFAULT ROUTE FOUND:"
    echo "   $DEFAULT_ROUTE"
    echo ""
    echo "   This means static route is properly configured! 🎉"
else
    echo "❌ NO DEFAULT ROUTE FOUND"
    echo "   Static route (0.0.0.0/0) is missing!"
    echo ""
    echo "   This could mean:"
    echo "   - Gateway was not configured when device was added"
    echo "   - Route addition failed (check server logs)"
    echo "   - FRR hasn't fully initialized yet"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 FRR RUNNING CONFIGURATION (STATIC ROUTES):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
docker exec "$CONTAINER" vtysh -c 'show running-config' 2>/dev/null | grep -A 5 "^ip route" || echo "   No static routes configured"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 TESTING CONNECTIVITY (if default route exists):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -n "$DEFAULT_ROUTE" ]; then
    # Extract gateway from default route
    GATEWAY=$(echo "$DEFAULT_ROUTE" | grep -oP 'via \K[0-9.]+')
    
    if [ -n "$GATEWAY" ]; then
        echo "🔍 Pinging gateway: $GATEWAY"
        docker exec "$CONTAINER" ping -c 3 -W 2 "$GATEWAY" 2>/dev/null && \
            echo "   ✅ Gateway is reachable!" || \
            echo "   ⚠️  Gateway is not responding (check network configuration)"
    fi
else
    echo "   ⏭️  Skipped (no default route to test)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════════════════"
echo "                              Verification Complete"
echo "════════════════════════════════════════════════════════════════════════════════"


