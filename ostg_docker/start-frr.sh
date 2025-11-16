#!/bin/bash

# FRR Startup Script for OSTG
# Note: We don't use 'set -e' here because we want to continue even if some daemons fail to start

echo "Starting FRR for device: ${DEVICE_NAME:-unknown}"

# Generate FRR configuration from template
if [ -f "/etc/frr/frr.conf.template" ]; then
    echo "Generating FRR configuration..."
    
    # Normalize DHCP mode hint
    DHCP_MODE_NORMALIZED=$(echo "${DHCP_MODE:-}" | tr '[:upper:]' '[:lower:]')
    if [ "${DHCP_MODE_NORMALIZED}" = "client" ]; then
        IS_DHCP_CLIENT=1
    else
        IS_DHCP_CLIENT=0
    fi
    
    # Replace template variables
    # Derive effective interface name: INTERFACE > vlan${VLAN} > eth0
    if [ -n "${INTERFACE:-}" ]; then
        INTERFACE_EFFECTIVE="${INTERFACE}"
    elif [ -n "${VLAN:-}" ]; then
        INTERFACE_EFFECTIVE="vlan${VLAN}"
    else
        INTERFACE_EFFECTIVE="eth0"
    fi
    # Handle IPv6 address line conditionally
    if [ -n "${IPV6_ADDRESS}" ] && [ -n "${IPV6_MASK}" ]; then
        IPV6_LINE=" ipv6 address ${IPV6_ADDRESS}/${IPV6_MASK}"
    else
        IPV6_LINE=""
    fi
    
    # Handle loopback IPs conditionally
    if [ "${IS_DHCP_CLIENT}" -eq 1 ]; then
        LOOPBACK_IPV4_LINE=""
    else
        LOOPBACK_IPV4_VALUE="${LOOPBACK_IPV4:-${ROUTER_ID:-1.1.1.1}}"
        LOOPBACK_IPV4_LINE=" ip address ${LOOPBACK_IPV4_VALUE}/32"
    fi
    if [ -n "${LOOPBACK_IPV6}" ] && [ "${IS_DHCP_CLIENT}" -ne 1 ]; then
        LOOPBACK_IPV6_LINE=" ipv6 address ${LOOPBACK_IPV6}/128"
    else
        LOOPBACK_IPV6_LINE=""
    fi
    
    # Interface IPv4 configuration (optional)
    if [ -n "${IP_ADDRESS}" ] && [ -n "${IP_MASK}" ]; then
        INTERFACE_IPV4_LINE=" ip address ${IP_ADDRESS}/${IP_MASK}"
    else
        INTERFACE_IPV4_LINE=""
    fi
    
    # BGP / OSPF network statements (optional)
    if [ -n "${NETWORK}" ] && [ -n "${NETMASK}" ]; then
        BGP_NETWORK_LINE=" network ${NETWORK}/${NETMASK}"
        OSPF_NETWORK_LINE=" network ${NETWORK}/${NETMASK} area 0.0.0.0"
    else
        BGP_NETWORK_LINE=""
        OSPF_NETWORK_LINE=""
    fi
    if [ "${IS_DHCP_CLIENT}" -ne 1 ]; then
        if [ -n "${LOOPBACK_IPV4_VALUE:-}" ]; then
            BGP_LOOPBACK_NETWORK_LINE=" network ${LOOPBACK_IPV4_VALUE}/32"
        elif [ -n "${LOOPBACK_IPV4:-}" ]; then
            BGP_LOOPBACK_NETWORK_LINE=" network ${LOOPBACK_IPV4}/32"
        else
            BGP_LOOPBACK_NETWORK_LINE=""
        fi
    else
        BGP_LOOPBACK_NETWORK_LINE=""
    fi
    
    if [ "${IS_DHCP_CLIENT}" -ne 1 ]; then
        ROUTER_ID_VALUE="${ROUTER_ID:-1.1.1.1}"
        GLOBAL_ROUTER_ID_LINE="ip router-id ${ROUTER_ID_VALUE}"
        BGP_ROUTER_ID_LINE=" bgp router-id ${ROUTER_ID_VALUE}"
        OSPF_ROUTER_ID_LINE=" ospf router-id ${ROUTER_ID_VALUE}"
        OSPF6_ROUTER_ID_LINE=" ospf6 router-id ${ROUTER_ID_VALUE}"
        MPLS_ROUTER_ID_LINE=" router-id ${ROUTER_ID_VALUE}"
        MPLS_TRANSPORT_LINE=" discovery transport-address ${ROUTER_ID_VALUE}"
    else
        ROUTER_ID_VALUE="${ROUTER_ID:-}"
        GLOBAL_ROUTER_ID_LINE=""
        BGP_ROUTER_ID_LINE=""
        OSPF_ROUTER_ID_LINE=""
        OSPF6_ROUTER_ID_LINE=""
        MPLS_ROUTER_ID_LINE=""
        MPLS_TRANSPORT_LINE=""
    fi
    
    # Convert router ID to dotted format for IS-IS NET (e.g., 192.168.0.2 -> 192.168.000.002)
    if [ -n "${ROUTER_ID_VALUE}" ]; then
        ROUTER_ID_DOTTED=$(echo "${ROUTER_ID_VALUE}" | awk -F. '{printf "%04d.%04d.%04d.%04d", $1, $2, $3, $4}')
    else
        ROUTER_ID_DOTTED="0000.0000.0000.0001"
    fi
    
    # Handle BGP neighbor config lines (empty by default, will be added dynamically via vtysh)
    BGP_NEIGHBOR_LINES="${BGP_NEIGHBOR_CONFIG_LINES:-}"
    
    # Handle VXLAN config (empty by default)
    VXLAN_LINES="${VXLAN_CONFIG_LINE:-}"
    
    sed -e "s/{{DEVICE_NAME}}/${DEVICE_NAME:-frr-device}/g" \
        -e "s/{{LOCAL_ASN}}/${LOCAL_ASN:-65001}/g" \
        -e "s/{{ROUTER_ID}}/${ROUTER_ID_VALUE:-}/g" \
        -e "s/{{ROUTER_ID_REPLACED_WITH_DOTTED_FORMAT}}/${ROUTER_ID_DOTTED}/g" \
        -e "s/{{INTERFACE}}/${INTERFACE_EFFECTIVE}/g" \
        -e "s|{{GLOBAL_ROUTER_ID_LINE}}|${GLOBAL_ROUTER_ID_LINE}|g" \
        -e "s|{{LOOPBACK_IPV4_LINE}}|${LOOPBACK_IPV4_LINE}|g" \
        -e "s|{{LOOPBACK_IPV6_LINE}}|${LOOPBACK_IPV6_LINE}|g" \
        -e "s|{{INTERFACE_IPV4_LINE}}|${INTERFACE_IPV4_LINE}|g" \
        -e "s|{{IPV6_ADDRESS_LINE}}|${IPV6_LINE}|g" \
        -e "s|{{BGP_NEIGHBOR_CONFIG_LINES}}|${BGP_NEIGHBOR_LINES}|g" \
        -e "s|{{BGP_NETWORK_LINE}}|${BGP_NETWORK_LINE}|g" \
        -e "s|{{BGP_LOOPBACK_NETWORK_LINE}}|${BGP_LOOPBACK_NETWORK_LINE}|g" \
        -e "s|{{OSPF_NETWORK_LINE}}|${OSPF_NETWORK_LINE}|g" \
        -e "s|{{BGP_ROUTER_ID_LINE}}|${BGP_ROUTER_ID_LINE}|g" \
        -e "s|{{OSPF_ROUTER_ID_LINE}}|${OSPF_ROUTER_ID_LINE}|g" \
        -e "s|{{OSPF6_ROUTER_ID_LINE}}|${OSPF6_ROUTER_ID_LINE}|g" \
        -e "s|{{MPLS_ROUTER_ID_LINE}}|${MPLS_ROUTER_ID_LINE}|g" \
        -e "s|{{MPLS_TRANSPORT_LINE}}|${MPLS_TRANSPORT_LINE}|g" \
        -e "s|{{VXLAN_CONFIG_LINE}}|${VXLAN_LINES}|g" \
        /etc/frr/frr.conf.template > /etc/frr/frr.conf
    
    echo "FRR configuration generated successfully"
else
    echo "Warning: FRR configuration template not found, using defaults"
fi

# Start FRR daemons
echo "Starting FRR daemons..."

# Ensure /var/run/frr exists
mkdir -p /var/run/frr
chown frr:frr /var/run/frr 2>/dev/null || true

# Ensure frr user is member of frrvty group at runtime
if ! id -nG frr 2>/dev/null | grep -q "\\bfrrvty\\b"; then
    groupadd -f frrvty 2>/dev/null || true
    usermod -a -G frrvty frr 2>/dev/null || true
fi

# Function to start daemon safely with error checking
start_daemon() {
    local daemon=$1
    local daemon_path="/usr/lib/frr/$daemon"
    
    if [ ! -f "$daemon_path" ]; then
        echo "Warning: $daemon not found at $daemon_path"
        return 1
    fi
    
    echo "Starting $daemon..."
    # Start daemon and capture output
    # staticd does not accept -f; start it without a config file argument
    if [ "$daemon" = "staticd" ]; then
        cmd="$daemon_path -d -A 127.0.0.1"
    else
        cmd="$daemon_path -d -A 127.0.0.1 -f /etc/frr/frr.conf"
    fi
    if $cmd 2>&1; then
        sleep 1
        # Verify it's actually running
        if pgrep -f "$daemon" > /dev/null; then
            echo "✅ $daemon started successfully"
            return 0
        else
            echo "❌ $daemon failed to start (process not found)"
            return 1
        fi
    else
        echo "❌ $daemon failed to start (exit code: $?)"
        return 1
    fi
}

# Start zebra first (required for all other daemons)
start_daemon "zebra" || echo "CRITICAL: zebra failed to start!"

# Wait for zebra to be ready
sleep 3

# Start staticd (MUST be early for static routes!)
start_daemon "staticd" || echo "WARNING: staticd failed to start"

# Start bgpd
start_daemon "bgpd" || echo "WARNING: bgpd failed to start"

# Wait for critical daemons to initialize
sleep 2

# Start OSPF and ISIS daemons (required for routing protocols)
start_daemon "ospfd" || echo "WARNING: ospfd failed to start"
start_daemon "ospf6d" || echo "WARNING: ospf6d failed to start"
start_daemon "isisd" || echo "WARNING: isisd failed to start"

# Wait a bit for all daemons to stabilize
sleep 2

# Verify critical daemons are running
echo ""
echo "=== FRR Daemon Status ==="
for daemon in zebra staticd bgpd ospfd ospf6d isisd; do
    if pgrep -f "$daemon" > /dev/null; then
        echo "✅ $daemon is running (PID: $(pgrep -f "$daemon" | head -1))"
    else
        echo "❌ $daemon is NOT running"
    fi
done
echo "========================"
echo ""

# List of daemons to monitor (only critical ones)
DAEMONS=("zebra" "staticd" "bgpd" "ospfd" "ospf6d" "isisd")

# Keep container running and monitor daemons
echo "Monitoring FRR daemons..."
while true; do
    for daemon in "${DAEMONS[@]}"; do
        if ! pgrep -f "$daemon" > /dev/null; then
            echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $daemon daemon died, attempting restart..."
            start_daemon "$daemon"
        fi
    done
    sleep 10
done
