#!/bin/bash
# Script to check DPDK-related logs from the OSTG server

SERVER_HOST="${SERVER_HOST:-svl-hp-ai-srv04}"
SERVER_USER="${SERVER_USER:-root}"

echo "=========================================="
echo "Checking DPDK Logs on Server: $SERVER_HOST"
echo "=========================================="
echo ""

# Check if we can SSH to the server
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$SERVER_USER@$SERVER_HOST" exit 2>/dev/null; then
    echo "⚠️  Cannot SSH to server automatically."
    echo "Please run these commands manually on the server:"
    echo ""
    echo "  journalctl -u ostg-server -n 200 --no-pager | grep -i dpdk"
    echo ""
    echo "Or for all recent logs:"
    echo ""
    echo "  journalctl -u ostg-server -n 200 --no-pager"
    exit 1
fi

echo "📋 Recent DPDK-related logs:"
echo "----------------------------------------"
ssh "$SERVER_USER@$SERVER_HOST" "journalctl -u ostg-server -n 200 --no-pager | grep -i dpdk" || {
    echo "No DPDK logs found. Showing all recent logs:"
    echo ""
    ssh "$SERVER_USER@$SERVER_HOST" "journalctl -u ostg-server -n 50 --no-pager"
}

echo ""
echo "=========================================="
echo "📋 Checking for errors:"
echo "----------------------------------------"
ssh "$SERVER_USER@$SERVER_HOST" "journalctl -u ostg-server -n 200 --no-pager | grep -iE '(error|failed|fail|cannot|unable)'" || echo "No errors found in recent logs"

echo ""
echo "=========================================="
echo "📋 Last 20 lines of server logs:"
echo "----------------------------------------"
ssh "$SERVER_USER@$SERVER_HOST" "journalctl -u ostg-server -n 20 --no-pager"

echo ""
echo "=========================================="
echo "✅ Log check complete!"
echo ""
echo "To follow logs in real-time, run on server:"
echo "  journalctl -u ostg-server -f"




