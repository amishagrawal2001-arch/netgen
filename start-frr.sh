#!/bin/bash

# FRR Startup Script for OSTG
# Note: We don't use 'set -e' here because we want to continue even if some daemons fail to start

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

# Ensure /var/run/frr exists
mkdir -p /var/run/frr
chown frr:frr /var/run/frr 2>/dev/null || true

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
    if $daemon_path -d -A 127.0.0.1 -f /etc/frr/frr.conf 2>&1; then
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
