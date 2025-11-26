#!/bin/bash
# Script to fix default route on server when VXLAN device breaks connectivity
# Usage: Run this on the server (svl-hp-ai-srv04) via console/out-of-band access

echo "Checking current default route..."
ip route | grep '^default' || echo "No default route found"

echo ""
echo "Removing problematic default routes..."
# Remove default routes that might be pointing to wrong interface
ip route del default via 192.168.0.1 2>/dev/null || true
ip route del default via 192.169.0.1 2>/dev/null || true
ip route del default dev vlan20 2>/dev/null || true
ip route del default dev vlan21 2>/dev/null || true

echo ""
echo "Checking for management interface..."
# Find management interface (usually ens14f0 or similar)
MGMT_IFACE=$(ip route | grep -E '^default.*ens14' | awk '{print $5}' | head -1)
if [ -z "$MGMT_IFACE" ]; then
    MGMT_IFACE=$(ip addr show | grep -E 'inet.*10\.' | grep -v '127.0.0.1' | head -1 | awk '{print $NF}')
fi

if [ -n "$MGMT_IFACE" ]; then
    echo "Found management interface: $MGMT_IFACE"
    MGMT_GW=$(ip route | grep "$MGMT_IFACE" | grep default | awk '{print $3}' | head -1)
    if [ -z "$MGMT_GW" ]; then
        # Try to get gateway from interface's network
        MGMT_IP=$(ip addr show "$MGMT_IFACE" | grep 'inet ' | awk '{print $2}' | cut -d'/' -f1)
        if [ -n "$MGMT_IP" ]; then
            # Assume gateway is .1 of the subnet
            MGMT_GW=$(echo "$MGMT_IP" | sed 's/\.[0-9]*$/.1/')
        fi
    fi
    if [ -n "$MGMT_GW" ]; then
        echo "Restoring default route via $MGMT_GW on $MGMT_IFACE..."
        ip route add default via "$MGMT_GW" dev "$MGMT_IFACE" 2>/dev/null || \
        ip route replace default via "$MGMT_GW" dev "$MGMT_IFACE"
        echo "✅ Default route restored"
    else
        echo "⚠️  Could not determine management gateway. Please set manually:"
        echo "   ip route add default via <gateway> dev <interface>"
    fi
else
    echo "⚠️  Could not find management interface. Please set default route manually."
fi

echo ""
echo "Current default route:"
ip route | grep '^default' || echo "No default route"



