#!/bin/bash
# restart_ostg_server.sh
# Script to restart the OSTG server service after database migration

HOST="svl-hp-ai-srv04"
USER="root"
PASSWORD="Embe1mpls"

echo "🔄 Restarting OSTG server service on $HOST..."

# Create SSH command with password
ssh_command() {
    /opt/homebrew/bin/sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$USER@$HOST" "$@"
}

# Check current service status
echo "📋 Checking current OSTG server status..."
ssh_command "systemctl status ostg-server.service --no-pager -l"

# Restart the service
echo ""
echo "🔄 Restarting OSTG server service..."
ssh_command "systemctl restart ostg-server.service"

# Wait a moment for the service to start
echo "⏳ Waiting for service to start..."
sleep 3

# Check service status after restart
echo ""
echo "✅ Checking service status after restart..."
ssh_command "systemctl status ostg-server.service --no-pager -l"

# Check if the service is running
echo ""
echo "🔍 Verifying service is running..."
ssh_command "systemctl is-active ostg-server.service"

echo ""
echo "✅ OSTG server service restart completed!"
echo ""
echo "The database migration should now be active and the column error should be resolved."
