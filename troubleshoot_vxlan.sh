#!/bin/bash

# VXLAN Troubleshooting Script
# Usage: ./troubleshoot_vxlan.sh [server] <bridge_name> [container_id]
# Examples:
#   ./troubleshoot_vxlan.sh br5000                                    # Run locally
#   ./troubleshoot_vxlan.sh br5000 7f958d0d2c66                      # Run locally with container ID
#   ./troubleshoot_vxlan.sh svl-hp-ai-srv04 br5000                   # Run on remote server
#   ./troubleshoot_vxlan.sh root@svl-hp-ai-srv04 br5000 7f958d0d2c66 # Run on remote server with container ID

set +e  # Don't exit on error - we want to continue even if some commands fail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
SERVER=""
BRIDGE_NAME=""
CONTAINER_ID=""

# Check arguments
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: Bridge name is required${NC}"
    echo "Usage: $0 [server] <bridge_name> [container_id]"
    echo "Examples:"
    echo "  $0 br5000                                    # Run locally"
    echo "  $0 br5000 7f958d0d2c66                      # Run locally with container ID"
    echo "  $0 svl-hp-ai-srv04 br5000                   # Run on remote server"
    echo "  $0 root@svl-hp-ai-srv04 br5000 7f958d0d2c66 # Run on remote server with container ID"
    exit 1
elif [ $# -eq 1 ]; then
    # Only bridge name provided - run locally
    BRIDGE_NAME="$1"
elif [ $# -eq 2 ]; then
    # Could be: server+bridge (remote) or bridge+container (local)
    # Check if first arg looks like a server (contains @ or doesn't start with 'br')
    if [[ "$1" =~ @ ]] || [[ ! "$1" =~ ^br ]]; then
        # First arg is server, second is bridge
        SERVER="$1"
        BRIDGE_NAME="$2"
    else
        # First arg is bridge, second is container ID
        BRIDGE_NAME="$1"
        CONTAINER_ID="$2"
    fi
elif [ $# -eq 3 ]; then
    # Server, bridge, and container ID
    SERVER="$1"
    BRIDGE_NAME="$2"
    CONTAINER_ID="$3"
else
    echo -e "${RED}Error: Too many arguments${NC}"
    echo "Usage: $0 [server] <bridge_name> [container_id]"
    exit 1
fi

# Validate bridge name
if [[ ! "$BRIDGE_NAME" =~ ^br[0-9]+$ ]]; then
    echo -e "${YELLOW}Warning: Bridge name '$BRIDGE_NAME' doesn't match pattern 'br<number>'${NC}"
fi

VNI=$(echo "$BRIDGE_NAME" | sed 's/br//')

# Function to run command locally or via SSH
run_remote_cmd() {
    local cmd="$1"
    if [ -n "$SERVER" ]; then
        ssh -o StrictHostKeyChecking=no "$SERVER" "$cmd"
    else
        eval "$cmd"
    fi
}

# Function to get container name or ID
get_container() {
    if [ -n "$CONTAINER_ID" ]; then
        # Use provided container ID
        echo "$CONTAINER_ID"
    else
        # Auto-detect container
        if [ -n "$SERVER" ]; then
            ssh -o StrictHostKeyChecking=no "$SERVER" "docker ps --format '{{.Names}}' | grep -E 'ostg-frr-' | head -n 1"
        else
            docker ps --format "{{.Names}}" | grep -E "ostg-frr-" | head -n 1
        fi
    fi
}

# Find FRR container
echo -e "${BLUE}=== Finding FRR Container ===${NC}"
if [ -n "$SERVER" ]; then
    echo -e "${GREEN}Connecting to server: $SERVER${NC}"
fi

if [ -n "$CONTAINER_ID" ]; then
    echo -e "${GREEN}Using provided container ID: $CONTAINER_ID${NC}"
    CONTAINER="$CONTAINER_ID"
else
    CONTAINER=$(get_container)
    if [ -z "$CONTAINER" ]; then
        echo -e "${RED}Error: No FRR container found${NC}"
        exit 1
    fi
    echo -e "${GREEN}Found container: $CONTAINER${NC}"
fi
echo ""

# Extract VNI from bridge name if not already extracted
if [ -z "$VNI" ] || [ "$VNI" == "$BRIDGE_NAME" ]; then
    echo -e "${YELLOW}Warning: Could not extract VNI from bridge name. Using default checks.${NC}"
    VNI="5000"
fi

echo -e "${BLUE}=== VXLAN Troubleshooting for Bridge: $BRIDGE_NAME (VNI: $VNI) ===${NC}"
echo ""

# Function to run docker exec command locally or via SSH
docker_exec() {
    local cmd="$1"
    if [ -n "$SERVER" ]; then
        ssh -o StrictHostKeyChecking=no "$SERVER" "docker exec $CONTAINER bash -c \"$cmd\"" 2>&1
    else
        docker exec "$CONTAINER" bash -c "$cmd" 2>&1
    fi
}

# Function to run command and show output
run_cmd() {
    local title="$1"
    local cmd="$2"
    echo -e "${YELLOW}--- $title ---${NC}"
    docker_exec "$cmd" || echo "Command failed or returned no output"
    echo ""
}

# 1. VXLAN Interface Status
run_cmd "VXLAN Interface Status" "ip link show type vxlan"

# 2. Bridge Status
run_cmd "Bridge Status" "ip addr show $BRIDGE_NAME"

# 3. ARP Entries on Bridge
run_cmd "ARP Entries (Bridge)" "ip neigh show dev $BRIDGE_NAME"

# 4. ARP Entries on Underlay
UNDERLAY=$(docker_exec "ip route show default" 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}' | head -n 1)
# Alternative: try to find underlay from routes to common VTEP IPs
if [ -z "$UNDERLAY" ]; then
    UNDERLAY=$(docker_exec "ip route show 192.168.0.1 2>/dev/null || ip route show 192.255.0.1 2>/dev/null || ip route show 192.168.250.1 2>/dev/null" | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}' | head -n 1)
fi
# Try common interface names
if [ -z "$UNDERLAY" ]; then
    for iface in ens4np0 vlan20 ens5np0; do
        if docker_exec "ip link show $iface" 2>/dev/null | grep -q "state UP"; then
            UNDERLAY="$iface"
            break
        fi
    done
fi
if [ -n "$UNDERLAY" ]; then
    run_cmd "ARP Entries (Underlay: $UNDERLAY)" "ip neigh show dev $UNDERLAY"
else
    echo -e "${YELLOW}--- ARP Entries (Underlay) ---${NC}"
    echo "Could not determine underlay interface"
    echo ""
fi

# 5. FDB Entries
VXLAN_IFACE=$(docker_exec "ip link show type vxlan" 2>/dev/null | awk '/^[0-9]+:/ {print $2}' | sed 's/:$//' | head -n 1)
# Alternative: try to find VXLAN interface by VNI pattern
if [ -z "$VXLAN_IFACE" ]; then
    VXLAN_IFACE=$(docker_exec "ip link show type vxlan" 2>/dev/null | grep -E "vx${VNI}-" | awk '{print $2}' | sed 's/:$//' | head -n 1)
fi
if [ -n "$VXLAN_IFACE" ]; then
    run_cmd "FDB Entries (VXLAN Interface: $VXLAN_IFACE)" "bridge fdb show dev $VXLAN_IFACE"
else
    echo -e "${YELLOW}--- FDB Entries ---${NC}"
    echo "Could not determine VXLAN interface name"
    echo ""
fi

# 6. EVPN VNI Details
run_cmd "EVPN VNI Details" "vtysh -c 'show evpn vni detail' | grep -A 20 'VNI: $VNI'"

# 7. EVPN MAC Table
run_cmd "EVPN MAC Table" "vtysh -c 'show evpn mac vni $VNI'"

# 8. EVPN Type-2 Routes (MAC/IP)
run_cmd "EVPN Type-2 Routes (MAC/IP)" "vtysh -c 'show bgp l2vpn evpn route type macip'"

# 9. EVPN Type-3 Routes (Multicast)
run_cmd "EVPN Type-3 Routes (Multicast)" "vtysh -c 'show bgp l2vpn evpn route type multicast'"

# 10. Routes
run_cmd "All Routes" "ip route show"

# 11. Routes to common VTEP IPs
echo -e "${YELLOW}--- Routes to Remote VTEPs ---${NC}"
for vtep in "192.255.0.1" "192.168.250.1" "192.168.0.1"; do
    result=$(docker_exec "ip route show $vtep" 2>&1)
    if [ -n "$result" ]; then
        echo "Route to $vtep:"
        echo "$result"
    else
        echo "No route to $vtep"
    fi
done
echo ""

# 12. Bridge Link Info
run_cmd "Bridge Link Info" "bridge link show $BRIDGE_NAME"

# 13. Bridge VLAN Info (if VLAN-aware)
run_cmd "Bridge VLAN Info" "bridge vlan show $BRIDGE_NAME"

# 14. Kernel Parameters
run_cmd "Proxy ARP Setting" "sysctl net.ipv4.conf.$BRIDGE_NAME.proxy_arp 2>/dev/null || echo 'Not set'"
run_cmd "ARP Ignore Setting" "sysctl net.ipv4.conf.$BRIDGE_NAME.arp_ignore 2>/dev/null || echo 'Not set'"

# 15. Connectivity Test
echo -e "${YELLOW}--- Connectivity Tests ---${NC}"
BRIDGE_IP=$(docker_exec "ip addr show $BRIDGE_NAME" | awk '/inet / {print $2}' | sed 's/\/.*//' | head -n 1)
if [ -n "$BRIDGE_IP" ]; then
    echo "Bridge IP: $BRIDGE_IP"
    echo ""
    
    # Get remote SVI IPs from ARP table
    REMOTE_IPS=$(docker_exec "ip neigh show dev $BRIDGE_NAME" | grep -v "$BRIDGE_IP" | awk '{print $1}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)
    
    if [ -n "$REMOTE_IPS" ]; then
        for remote_ip in $REMOTE_IPS; do
            echo -e "${BLUE}Pinging $remote_ip from $BRIDGE_NAME...${NC}"
            docker_exec "ping -I $BRIDGE_NAME -c 2 -W 1 $remote_ip" 2>&1 | head -n 5 || echo "Ping failed"
            echo ""
        done
    else
        echo "No remote IPs found in ARP table for ping test"
    fi
else
    echo "Could not determine bridge IP for connectivity test"
fi

# 16. Summary
echo -e "${BLUE}=== Troubleshooting Summary ===${NC}"
echo "Bridge: $BRIDGE_NAME"
echo "VNI: $VNI"
echo "Container: $CONTAINER"
echo "VXLAN Interface: ${VXLAN_IFACE:-Not found}"
echo "Underlay Interface: ${UNDERLAY:-Not found}"
echo "Bridge IP: ${BRIDGE_IP:-Not configured}"
echo ""

# Check for common issues
echo -e "${YELLOW}=== Common Issues Check ===${NC}"

# Check ARP entries with NOARP flag
NOARP_COUNT=$(docker_exec "ip neigh show dev $BRIDGE_NAME" 2>/dev/null | grep -c "NOARP" 2>/dev/null || echo "0")
NOARP_COUNT=${NOARP_COUNT//[^0-9]/}  # Remove non-numeric characters
NOARP_COUNT=${NOARP_COUNT:-0}  # Default to 0 if empty
if [ "$NOARP_COUNT" -gt 0 ] 2>/dev/null; then
    echo -e "${RED}⚠ Found $NOARP_COUNT ARP entry/entries with NOARP flag (zebra-managed)${NC}"
    echo "   These need to be deleted and recreated as kernel-managed"
fi

# Check FDB entries - BGP next-hop IS the VTEP IP now, so this is correct
# Just verify FDB entries exist
if [ -n "$VXLAN_IFACE" ]; then
    FDB_COUNT=$(docker_exec "bridge fdb show dev $VXLAN_IFACE" 2>/dev/null | grep -c "dst" 2>/dev/null || echo "0")
    FDB_COUNT=${FDB_COUNT//[^0-9]/}  # Remove non-numeric characters
    FDB_COUNT=${FDB_COUNT:-0}  # Default to 0 if empty
    if [ "$FDB_COUNT" -eq 0 ] 2>/dev/null; then
        echo -e "${YELLOW}⚠ No FDB entries found for VXLAN interface $VXLAN_IFACE${NC}"
        echo "   FDB entries are needed for VXLAN forwarding"
    else
        echo -e "${GREEN}✓ Found $FDB_COUNT FDB entry/entries (BGP next-hop is used as VTEP IP)${NC}"
    fi
fi

# Check for FAILED ARP entries
FAILED_ARP=$(docker_exec "ip neigh show" 2>/dev/null | grep -c "FAILED" 2>/dev/null || echo "0")
FAILED_ARP=${FAILED_ARP//[^0-9]/}  # Remove non-numeric characters
FAILED_ARP=${FAILED_ARP:-0}  # Default to 0 if empty
if [ "$FAILED_ARP" -gt 0 ] 2>/dev/null; then
    echo -e "${RED}⚠ Found $FAILED_ARP FAILED ARP entry/entries${NC}"
    echo "   These need to be resolved for VXLAN to work"
fi

# Check if route exists to remote VTEP (check both kernel and FRR routing tables)
# Get remote VTEP IP from EVPN routes (BGP next-hop)
REMOTE_VTEP_IP=""
REMOTE_VTEP_IP=$(docker_exec "vtysh -c 'show bgp l2vpn evpn route type macip'" 2>/dev/null | grep -E "^\s+\*>" | head -n 1 | awk '{for(i=1;i<=NF;i++) if($i=="from") print $(i+1)}' || echo "")

# If we can't get from EVPN, try to get from EVPN VNI details
if [ -z "$REMOTE_VTEP_IP" ]; then
    REMOTE_VTEP_IP=$(docker_exec "vtysh -c 'show evpn vni $VNI detail'" 2>/dev/null | grep -A 5 "Remote VTEPs" | grep -oE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' | head -n 1 || echo "")
fi

# Check route in both kernel and FRR routing tables
if [ -n "$REMOTE_VTEP_IP" ]; then
    # Check kernel route
    KERNEL_ROUTE=$(docker_exec "ip route show $REMOTE_VTEP_IP" 2>&1 | grep -c "$REMOTE_VTEP_IP" 2>/dev/null || echo "0")
    # Check FRR route
    FRR_ROUTE=$(docker_exec "vtysh -c 'show ip route $REMOTE_VTEP_IP'" 2>&1 | grep -c "$REMOTE_VTEP_IP" 2>/dev/null || echo "0")
    
    if [ "$KERNEL_ROUTE" -eq 0 ] && [ "$FRR_ROUTE" -eq 0 ]; then
        echo -e "${YELLOW}⚠ No route found to remote VTEP $REMOTE_VTEP_IP (checked both kernel and FRR tables)${NC}"
        echo "   Route should exist via OSPF or be added manually"
    else
        if [ "$FRR_ROUTE" -gt 0 ]; then
            echo -e "${GREEN}✓ Route to remote VTEP $REMOTE_VTEP_IP exists in FRR routing table${NC}"
        fi
        if [ "$KERNEL_ROUTE" -gt 0 ]; then
            echo -e "${GREEN}✓ Route to remote VTEP $REMOTE_VTEP_IP exists in kernel routing table${NC}"
        fi
    fi
else
    # Fallback: check for common VTEP IPs
    for vtep_ip in "192.255.0.1" "192.168.0.1"; do
        KERNEL_ROUTE=$(docker_exec "ip route show $vtep_ip" 2>&1 | grep -c "$vtep_ip" 2>/dev/null || echo "0")
        FRR_ROUTE=$(docker_exec "vtysh -c 'show ip route $vtep_ip'" 2>&1 | grep -c "$vtep_ip" 2>/dev/null || echo "0")
        
        if [ "$KERNEL_ROUTE" -eq 0 ] && [ "$FRR_ROUTE" -eq 0 ]; then
            echo -e "${YELLOW}⚠ No route found to VTEP $vtep_ip (checked both kernel and FRR tables)${NC}"
        else
            if [ "$FRR_ROUTE" -gt 0 ]; then
                echo -e "${GREEN}✓ Route to VTEP $vtep_ip exists in FRR routing table${NC}"
            fi
            if [ "$KERNEL_ROUTE" -gt 0 ]; then
                echo -e "${GREEN}✓ Route to VTEP $vtep_ip exists in kernel routing table${NC}"
            fi
        fi
    done
fi

echo ""
echo -e "${GREEN}=== Troubleshooting Complete ===${NC}"

