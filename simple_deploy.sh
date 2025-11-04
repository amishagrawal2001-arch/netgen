#!/bin/bash

# Simple deployment script for OSTG server update
SERVER_HOST="svl-hp-ai-srv02"
SERVER_USER="root"
SERVER_PASS="Embe1mpls"
WHEEL_FILE="ostg_trafficgen-0.1.52-py3-none-any.whl"

echo "Starting deployment to server $SERVER_HOST..."

# Copy wheel file to server
echo "Copying wheel file to server..."
sshpass -p "$SERVER_PASS" scp "$WHEEL_FILE" "$SERVER_USER@$SERVER_HOST:/tmp/"

# Execute deployment commands on server
echo "Installing updated package on server..."
sshpass -p "$SERVER_PASS" ssh "$SERVER_USER@$SERVER_HOST" << 'EOF'
    set -e
    cd /tmp
    
    echo "Stopping OSTG server..."
    systemctl stop ostg-server || true
    
    echo "Installing updated OSTG package..."
    source ostg_env/bin/activate
    pip install --force-reinstall ostg_trafficgen-0.1.52-py3-none-any.whl
    
    echo "Starting OSTG server..."
    systemctl start ostg-server
    
    echo "Checking server status..."
    systemctl status ostg-server --no-pager
    
    echo "Deployment completed successfully!"
EOF

echo "Deployment to server completed!"

