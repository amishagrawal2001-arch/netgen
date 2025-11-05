#!/bin/bash
# Script to restart OSTG server and clear Python cache

SERVER_HOST="${SERVER_HOST:-svl-hp-ai-srv04}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_PATH="${SERVER_PATH:-/opt/OSTG}"

echo "Restarting OSTG server and clearing Python cache on $SERVER_HOST..."

sshpass -p "Embe1mpls" ssh -o StrictHostKeyChecking=no $SERVER_USER@$SERVER_HOST << 'EOF'
    echo "Stopping OSTG server..."
    systemctl stop ostg-server
    
    echo "Clearing Python cache..."
    find /opt/OSTG -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
    find /opt/OSTG -name "*.pyc" -delete 2>/dev/null || true
    find /opt/OSTG -name "*.pyo" -delete 2>/dev/null || true
    
    echo "Waiting 2 seconds..."
    sleep 2
    
    echo "Starting OSTG server..."
    systemctl start ostg-server
    
    echo "Waiting 3 seconds for server to start..."
    sleep 3
    
    echo "Checking server status..."
    systemctl status ostg-server --no-pager | head -15
    
    echo ""
    echo "Server restart completed!"
EOF

echo "Done!"

