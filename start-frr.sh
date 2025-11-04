#!/bin/bash

# FRR Startup Script for OSTG
set -e

echo "Starting FRR for device: ${DEVICE_NAME:-unknown}"

# Generate FRR configuration from template
if [ -f "/etc/frr/frr.conf.template" ]; then
    echo "Generating FRR configuration..."
    
    # Replace template variables
    sed -e "s/{{DEVICE_NAME}}/${DEVICE_NAME:-frr-device}/g" \
        -e "s/{{LOCAL_ASN}}/${LOCAL_ASN:-65001}/g" \
        -e "s/{{ROUTER_ID}}/${ROUTER_ID:-192.168.0.2}/g" \
        -e "s/{{NETWORK}}/${NETWORK:-192.168.0.0}/g" \
        -e "s/{{NETMASK}}/${NETMASK:-24}/g" \
        -e "s/{{INTERFACE}}/${INTERFACE:-eth0}/g" \
        -e "s/{{IP_ADDRESS}}/${IP_ADDRESS:-192.168.0.2}/g" \
        -e "s/{{IP_MASK}}/${IP_MASK:-24}/g" \
        /etc/frr/frr.conf.template > /etc/frr/frr.conf
    
    echo "FRR configuration generated successfully"
else
    echo "Warning: FRR configuration template not found, using defaults"
fi

# Start FRR daemons
echo "Starting FRR daemons..."

# Function to start daemon safely
start_daemon() {
    local daemon=$1
    local daemon_path="/usr/lib/frr/$daemon"
    
    if [ -f "$daemon_path" ]; then
        echo "Starting $daemon..."
        $daemon_path -d -A 127.0.0.1 -f /etc/frr/frr.conf
        sleep 1
    else
        echo "Warning: $daemon not found at $daemon_path"
    fi
}

# Start zebra first (required for all other daemons)
start_daemon "zebra"

# Wait for zebra to be ready
sleep 2

# Start CRITICAL daemons only (for fast startup)
start_daemon "staticd"  # MUST be early for static routes!
start_daemon "bgpd"

# Wait for critical daemons to initialize
sleep 2

# Start other routing daemons (on-demand, can be slower)
start_daemon "ospfd"
start_daemon "ospf6d"
start_daemon "isisd"

# Optional: Start other daemons in background
# These are only started if needed by configuration
# start_daemon "ripd"
# start_daemon "ripngd"
# start_daemon "pimd"
# start_daemon "pim6d"
# start_daemon "ldpd"
# start_daemon "nhrpd"
# start_daemon "eigrpd"
# start_daemon "babeld"
# start_daemon "bfdd"
# start_daemon "fabricd"
# start_daemon "vrrpd"
# start_daemon "pathd"
# start_daemon "pbrd"

echo "FRR critical daemons started successfully"

# List of daemons to monitor (only critical ones)
DAEMONS=("zebra" "staticd" "bgpd" "ospfd" "ospf6d" "isisd")

# Keep container running and monitor daemons
while true; do
    for daemon in "${DAEMONS[@]}"; do
        if ! pgrep -f "$daemon" > /dev/null; then
            echo "$daemon daemon died, restarting..."
            start_daemon "$daemon"
        fi
    done
    
    sleep 10
done
