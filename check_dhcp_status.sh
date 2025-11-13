#!/bin/bash
# Script to check DHCP service status for devices

DEVICE_ID=$1
SERVER_URL=${SERVER_URL:-"http://svl-hp-ai-srv04:5051"}
SERVER_HOST=${SERVER_HOST:-"svl-hp-ai-srv04"}
SSH_USER=${SSH_USER:-"root"}

# Function to run docker commands (either locally or via SSH)
docker_cmd() {
    if command -v ssh >/dev/null 2>&1 && [ -n "$SERVER_HOST" ]; then
        ssh "$SSH_USER@$SERVER_HOST" "docker $*"
    else
        docker "$@"
    fi
}

if [ -z "$DEVICE_ID" ]; then
    echo "Usage: $0 <device_id>"
    echo ""
    echo "To check all devices, use:"
    echo "  curl -s $SERVER_URL/api/device/dhcp/status | jq '.'"
    echo ""
    echo "To list all DHCP containers:"
    echo "  docker ps | grep -E 'dhcp-client|dhcp-server|dhcp-frr'"
    echo ""
    echo "Environment variables:"
    echo "  SERVER_HOST - Server hostname (default: svl-hp-ai-srv04)"
    echo "  SSH_USER - SSH user (default: root)"
    echo "  SERVER_URL - API URL (default: http://svl-hp-ai-srv04:5051)"
    exit 1
fi

echo "=========================================="
echo "Checking DHCP Status for Device: $DEVICE_ID"
echo "=========================================="

# Method 1: Check database
echo -e "\n📊 1. Database Status:"
echo "----------------------------------------"
python3 query_device_database.py --device "$DEVICE_ID" 2>/dev/null | grep -A 15 -i "DHCP" || echo "Device not found in database"

# Method 2: Check container
echo -e "\n🐳 2. Docker Container Status:"
echo "----------------------------------------"

# Determine DHCP mode from database
DHCP_MODE=$(python3 query_device_database.py --device "$DEVICE_ID" 2>/dev/null | grep -i "DHCP Mode:" | awk '{print $3}' | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')

# Try to find container based on mode
CONTAINER=""
if [ "$DHCP_MODE" = "server" ]; then
    CONTAINER=$(docker_cmd ps --filter "name=dhcp-server-$DEVICE_ID" --format "{{.Names}}" 2>/dev/null | head -1)
elif [ "$DHCP_MODE" = "client" ]; then
    # Try both dhcp-client and dhcp-frr naming
    CONTAINER=$(docker_cmd ps --filter "name=dhcp-client-$DEVICE_ID" --format "{{.Names}}" 2>/dev/null | head -1)
    if [ -z "$CONTAINER" ]; then
        CONTAINER=$(docker_cmd ps --filter "name=dhcp-frr-$DEVICE_ID" --format "{{.Names}}" 2>/dev/null | head -1)
    fi
fi

# Fallback: search for any container with device ID
if [ -z "$CONTAINER" ]; then
    CONTAINER=$(docker_cmd ps --format "{{.Names}}" 2>/dev/null | grep -E "(dhcp-server|dhcp-client|dhcp-frr)-$DEVICE_ID" | head -1)
fi

if [ -z "$CONTAINER" ]; then
    echo "❌ No DHCP container found for device $DEVICE_ID"
    echo "   Expected container names:"
    if [ "$DHCP_MODE" = "server" ]; then
        echo "     - dhcp-server-$DEVICE_ID"
    elif [ "$DHCP_MODE" = "client" ]; then
        echo "     - dhcp-client-$DEVICE_ID or dhcp-frr-$DEVICE_ID"
    else
        echo "     - dhcp-server-$DEVICE_ID (for server mode)"
        echo "     - dhcp-client-$DEVICE_ID or dhcp-frr-$DEVICE_ID (for client mode)"
    fi
    echo "   Checking all containers with device ID..."
    docker_cmd ps 2>/dev/null | grep "$DEVICE_ID" || echo "   No containers found"
else
    echo "✅ Container found: $CONTAINER"
    docker_cmd ps --filter "name=$CONTAINER" --format "   Status: {{.Status}}" 2>/dev/null
    
    # Method 3: Check processes inside container
    echo -e "\n⚙️  3. Processes in Container:"
    echo "----------------------------------------"
    
    # Check for dnsmasq (server)
    # First check if process is running
    DNSMASQ_PROCESS=$(docker_cmd exec "$CONTAINER" ps aux 2>/dev/null | grep dnsmasq | grep -v grep)
    if [ ! -z "$DNSMASQ_PROCESS" ]; then
        DNSMASQ_PID=$(echo "$DNSMASQ_PROCESS" | awk '{print $2}')
        echo "✅ DHCP Server (dnsmasq) running with PID: $DNSMASQ_PID"
        echo "$DNSMASQ_PROCESS" | sed 's/^/   /'
        
        # Show config
        INTERFACE=$(docker_cmd exec "$CONTAINER" ls /etc/dnsmasq.d/ostg-*.conf 2>/dev/null | sed 's|.*ostg-\(.*\)\.conf|\1|' | head -1)
        if [ ! -z "$INTERFACE" ]; then
            echo "   Interface: $INTERFACE"
            echo "   Config file: /etc/dnsmasq.d/ostg-$INTERFACE.conf"
            # Check PID file
            PID_FILE="/run/dnsmasq-$INTERFACE.pid"
            if docker_cmd exec "$CONTAINER" test -f "$PID_FILE" 2>/dev/null; then
                PID_FROM_FILE=$(docker_cmd exec "$CONTAINER" cat "$PID_FILE" 2>/dev/null)
                echo "   PID file: $PID_FILE (contains: $PID_FROM_FILE)"
            fi
        fi
    else
        echo "❌ DHCP Server (dnsmasq) not running"
        # Check if config file exists but process is not running
        INTERFACE=$(docker_cmd exec "$CONTAINER" ls /etc/dnsmasq.d/ostg-*.conf 2>/dev/null | sed 's|.*ostg-\(.*\)\.conf|\1|' | head -1)
        if [ ! -z "$INTERFACE" ]; then
            echo "   ⚠️  Config file exists: /etc/dnsmasq.d/ostg-$INTERFACE.conf"
            echo "   ⚠️  But dnsmasq process is not running - may have crashed"
            # Check logs
            LOG_FILE="/var/log/dnsmasq-$INTERFACE.log"
            if docker_cmd exec "$CONTAINER" test -f "$LOG_FILE" 2>/dev/null; then
                echo "   Recent log entries:"
                docker_cmd exec "$CONTAINER" tail -5 "$LOG_FILE" 2>/dev/null | sed 's/^/      /' || echo "      (Could not read log file)"
            fi
        fi
    fi
    
    # Check for dhclient (client)
    # First check if process is running
    DHCLIENT_PROCESS=$(docker_cmd exec "$CONTAINER" ps aux 2>/dev/null | grep dhclient | grep -v grep | head -1)
    if [ ! -z "$DHCLIENT_PROCESS" ]; then
        DHCLIENT_PID=$(echo "$DHCLIENT_PROCESS" | awk '{print $2}')
        echo "✅ DHCP Client (dhclient) running with PID: $DHCLIENT_PID"
        echo "$DHCLIENT_PROCESS" | sed 's/^/   /'
        
        # Extract interface from process command line (last word after dhclient command)
        INTERFACE=$(echo "$DHCLIENT_PROCESS" | awk '{for(i=1;i<=NF;i++) if($i=="dhclient" || $i~/dhclient/) {for(j=i+1;j<=NF;j++) if($j!~/^-/ && $j!~/^\/run/ && $j!~/^\/var/) {print $j; exit}}; print $NF}')
        if [ -z "$INTERFACE" ] || [ "$INTERFACE" = "$DHCLIENT_PROCESS" ]; then
            # Fallback: try to get from lease file
            INTERFACE=$(docker_cmd exec "$CONTAINER" ls /var/lib/dhcp/dhclient-*.leases 2>/dev/null | sed 's|.*dhclient-\(.*\)\.leases|\1|' | head -1)
        fi
        # Also try to get from database if still empty
        if [ -z "$INTERFACE" ]; then
            INTERFACE=$(python3 query_device_database.py --device "$DEVICE_ID" 2>/dev/null | grep -i "interface:" | grep -i "vlan\|eth\|ens" | awk '{print $2}' | head -1)
        fi
        
        if [ ! -z "$INTERFACE" ]; then
            echo "   Interface: $INTERFACE"
            echo "   Lease file: /var/lib/dhcp/dhclient-$INTERFACE.leases"
            # Check PID file
            PID_FILE="/run/dhclient-$INTERFACE.pid"
            if docker_cmd exec "$CONTAINER" test -f "$PID_FILE" 2>/dev/null; then
                PID_FROM_FILE=$(docker_cmd exec "$CONTAINER" cat "$PID_FILE" 2>/dev/null)
                echo "   PID file: $PID_FILE (contains: $PID_FROM_FILE)"
            fi
            # Check interface IP
            IFACE_IP=$(docker_cmd exec "$CONTAINER" ip -4 addr show "$INTERFACE" 2>/dev/null | grep "inet " | awk '{print $2}')
            if [ ! -z "$IFACE_IP" ]; then
                echo "   Interface IP: $IFACE_IP"
            else
                echo "   ⚠️  Interface has no IPv4 address (waiting for DHCP lease)"
            fi
            # Show lease details
            LEASE_FILE="/var/lib/dhcp/dhclient-$INTERFACE.leases"
            if docker_cmd exec "$CONTAINER" test -f "$LEASE_FILE" 2>/dev/null; then
                LEASE_INFO=$(docker_cmd exec "$CONTAINER" cat "$LEASE_FILE" 2>/dev/null)
                if [ ! -z "$LEASE_INFO" ]; then
                    echo "   Lease info:"
                    echo "$LEASE_INFO" | sed 's/^/      /' | head -10
                else
                    echo "   ⚠️  Lease file is empty (no lease obtained yet)"
                fi
            else
                echo "   ⚠️  Lease file does not exist"
            fi
        fi
    else
        echo "❌ DHCP Client (dhclient) not running"
    fi
    
    # Check interface IP
    echo -e "\n🌐 4. Interface IP Address:"
    echo "----------------------------------------"
    # Get all interfaces (including VLAN interfaces with @ notation)
    ALL_INTERFACES=$(docker_cmd exec "$CONTAINER" ip link show 2>/dev/null | grep -E "^[0-9]+:" | awk '{print $2}' | sed 's/:$//' | sed 's/@.*$//' | sort -u | grep -E "vlan|eth|ens|lo" | head -20)
    if [ ! -z "$ALL_INTERFACES" ]; then
        for IFACE in $ALL_INTERFACES; do
            # Get IPv4 address (handle both vlan30 and vlan30@ens4np0 formats)
            IPV4=$(docker_cmd exec "$CONTAINER" ip -4 addr show "$IFACE" 2>/dev/null | grep "inet " | awk '{print $2}' | head -1)
            # Get IPv6 address (global scope)
            IPV6=$(docker_cmd exec "$CONTAINER" ip -6 addr show "$IFACE" 2>/dev/null | grep "inet6 " | grep "scope global" | awk '{print $2}' | head -1)
            # Get interface state
            STATE=$(docker_cmd exec "$CONTAINER" ip link show "$IFACE" 2>/dev/null | grep -oE "state [A-Z]+" | awk '{print $2}' | head -1)
            
            # Only show interfaces that are UP or have an IP address
            if [ "$STATE" = "UP" ] || [ ! -z "$IPV4" ] || [ ! -z "$IPV6" ]; then
                OUTPUT="   $IFACE"
                if [ ! -z "$STATE" ]; then
                    OUTPUT="$OUTPUT (state: $STATE)"
                fi
                if [ ! -z "$IPV4" ]; then
                    OUTPUT="$OUTPUT - IPv4: $IPV4"
                elif [ "$STATE" = "UP" ]; then
                    OUTPUT="$OUTPUT - IPv4: (none)"
                fi
                if [ ! -z "$IPV6" ] && [ "$IPV6" != "fe80::*" ]; then
                    OUTPUT="$OUTPUT - IPv6: $IPV6"
                fi
                echo "$OUTPUT"
            fi
        done
    else
        echo "   No interfaces found"
    fi
fi

# Method 4: Check via API
echo -e "\n📡 5. API Status:"
echo "----------------------------------------"
API_RESPONSE=$(curl -s "$SERVER_URL/api/device/dhcp/status" 2>/dev/null)
if [ ! -z "$API_RESPONSE" ]; then
    DEVICE_STATUS=$(echo "$API_RESPONSE" | jq -r ".[] | select(.device_id == \"$DEVICE_ID\")" 2>/dev/null)
    if [ ! -z "$DEVICE_STATUS" ]; then
        echo "$DEVICE_STATUS" | jq '.' 2>/dev/null || echo "$DEVICE_STATUS"
    else
        echo "   Device not found in API response"
    fi
else
    echo "   Failed to query API"
fi

echo -e "\n=========================================="

