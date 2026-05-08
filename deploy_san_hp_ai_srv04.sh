#!/usr/bin/env bash
# deploy_san_hp_ai_srv04.sh
# Deploy Tgen to san-hp-ai-srv04

set -e

SERVER_HOST="san-hp-ai-srv04.cluster.local"
SERVER_USER="root"
SERVER_PASS="Embe1mpls"
SERVER_PATH="/opt/OSTG"
WHEEL_FILE="ostg_trafficgen-0.1.52-py3-none-any.whl"

echo "=========================================="
echo "Deploying Tgen to $SERVER_HOST"
echo "=========================================="
echo ""

# Check if wheel file exists
if [[ ! -f "$WHEEL_FILE" ]]; then
    echo "Error: Wheel file not found: $WHEEL_FILE"
    echo "Looking for wheel files..."
    ls -la *.whl 2>/dev/null || echo "No wheel files found in current directory"
    exit 1
fi

echo "✅ Found wheel file: $WHEEL_FILE"
echo ""

# Check if sshpass is available
if ! command -v sshpass >/dev/null 2>&1; then
    echo "Error: sshpass is required but not installed."
    echo "Install with: brew install hudochenkov/sshpass/sshpass (macOS) or apt-get install sshpass (Linux)"
    exit 1
fi

echo "Step 1: Copying wheel file to server..."
sshpass -p "$SERVER_PASS" scp "$WHEEL_FILE" "$SERVER_USER@$SERVER_HOST:/tmp/" || {
    echo "Error: Failed to copy wheel file to server"
    exit 1
}
echo "✅ Wheel file copied"
echo ""

echo "Step 2: Installing on server..."
sshpass -p "$SERVER_PASS" ssh "$SERVER_USER@$SERVER_HOST" << EOF
    set -e
    
    echo "  → Stopping OSTG server..."
    systemctl stop ostg-server 2>/dev/null || true
    pkill -f run_tgen_server.py 2>/dev/null || true
    sleep 2
    
    echo "  → Creating target directory..."
    mkdir -p $SERVER_PATH
    cd $SERVER_PATH
    
    echo "  → Setting up Python virtual environment (if needed)..."
    if [[ ! -d "ostg_env" ]]; then
        python3 -m venv ostg_env
    fi
    
    echo "  → Activating virtual environment..."
    source ostg_env/bin/activate
    
    echo "  → Installing/updating OSTG package..."
    pip install --upgrade pip
    pip install --force-reinstall /tmp/$WHEEL_FILE
    
    echo "  → Copying server files..."
    if [[ ! -f "run_tgen_server.py" ]]; then
        echo "    Warning: run_tgen_server.py not found. You may need to copy it manually."
    fi
    
    echo "  → Starting OSTG server..."
    systemctl start ostg-server 2>/dev/null || {
        echo "    Systemd service not found, starting manually..."
        nohup python3 run_tgen_server.py > ostg_server.log 2>&1 &
        echo \$! > ostg_server.pid
    }
    
    sleep 3
    
    echo "  → Checking server status..."
    if systemctl is-active --quiet ostg-server; then
        echo "    ✅ Server is running (systemd)"
        systemctl status ostg-server --no-pager -l | head -10
    elif pgrep -f run_tgen_server.py > /dev/null; then
        echo "    ✅ Server is running (manual)"
        ps aux | grep -E "[r]un_tgen_server" | head -2
    else
        echo "    ⚠️  Server may not be running. Check logs:"
        echo "       journalctl -u ostg-server -n 20"
        echo "       or: tail -50 $SERVER_PATH/ostg_server.log"
    fi
    
    echo ""
    echo "  ✅ Deployment completed!"
EOF

if [[ $? -eq 0 ]]; then
    echo ""
    echo "=========================================="
    echo "✅ Deployment Successful!"
    echo "=========================================="
    echo ""
    echo "Server: $SERVER_HOST"
    echo "URL: http://$SERVER_HOST:5051"
    echo ""
    echo "Next steps:"
    echo "  1. Verify server is running:"
    echo "     ssh $SERVER_USER@$SERVER_HOST 'systemctl status ostg-server'"
    echo ""
    echo "  2. Check server logs:"
    echo "     ssh $SERVER_USER@$SERVER_HOST 'journalctl -u ostg-server -f'"
    echo ""
    echo "  3. Test API endpoint:"
    echo "     curl http://$SERVER_HOST:5051/api/health"
    echo ""
else
    echo ""
    echo "=========================================="
    echo "❌ Deployment Failed"
    echo "=========================================="
    echo "Check the error messages above for details."
    exit 1
fi

