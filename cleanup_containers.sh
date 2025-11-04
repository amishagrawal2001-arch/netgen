#!/bin/bash
# cleanup_containers.sh
# Script to clean up OSTG FRR containers from remote server

# Parse command line arguments
HOST="svl-hp-ai-srv04"
USER="root"
PASSWORD="Embe1mpls"

while [[ $# -gt 0 ]]; do
    case $1 in
        --server)
            HOST="$2"
            shift 2
            ;;
        --user)
            USER="$2"
            shift 2
            ;;
        --password)
            PASSWORD="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--server HOST] [--user USER] [--password PASSWORD]"
            exit 1
            ;;
    esac
done

echo "🧹 Cleaning up OSTG FRR containers from $HOST..."

# Create SSH command with password
ssh_command() {
    /opt/homebrew/bin/sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$USER@$HOST" "$@"
}

# Check if sshpass is available, if not provide alternative
if ! command -v /opt/homebrew/bin/sshpass &> /dev/null; then
    echo "❌ sshpass not found. Please install it or run the commands manually:"
    echo ""
    echo "Connect to the server:"
    echo "ssh $USER@$HOST"
    echo ""
    echo "Then run these commands:"
    echo "docker stop e07d4d70c799 3e33532f0320"
    echo "docker rm e07d4d70c799 3e33532f0320"
    echo "docker image prune -f"
    echo "docker volume prune -f"
    echo "docker network prune -f"
    echo ""
    echo "Or install sshpass:"
    echo "brew install sshpass  # on macOS"
    echo "apt-get install sshpass  # on Ubuntu/Debian"
    echo "yum install sshpass  # on CentOS/RHEL"
    exit 1
fi

# List current containers
echo "📋 Current OSTG FRR containers:"
# Use multiple methods to find containers:
# 1. Filter by name prefix (more reliable)
# 2. List all and grep for pattern (fallback)
CONTAINERS=$(ssh_command "docker ps -a --filter 'name=ostg-frr' --format '{{.Names}} {{.ID}}' 2>/dev/null" || echo "")

# If filter didn't work, try listing all and filtering
if [ -z "$CONTAINERS" ]; then
    echo "   Trying alternative method to find containers..."
    CONTAINERS=$(ssh_command "docker ps -a --format '{{.Names}} {{.ID}}' 2>/dev/null | grep 'ostg-frr' || true")
fi

# Extract container names and IDs from the output (format: name id)
# Store both names and IDs for more reliable cleanup
CONTAINER_NAMES=$(echo "$CONTAINERS" | awk '{print $1}' | grep -v '^$' | grep -v 'NAMES' || true)
CONTAINER_IDS=$(echo "$CONTAINERS" | awk '{print $NF}' | grep -v '^$' | grep -v 'CONTAINER' || true)

if [ -z "$CONTAINER_IDS" ] && [ -z "$CONTAINER_NAMES" ]; then
    echo "✅ No OSTG FRR containers found."
    exit 0
fi

echo "Found containers:"
echo "$CONTAINERS" | while read line; do
    if [ -n "$line" ] && echo "$line" | grep -q "ostg-frr"; then
        echo "  $line"
    fi
done

# Stop and remove containers (prefer names, fallback to IDs)
echo ""
echo "🛑 Stopping and removing containers..."

# Use names if available (more reliable), otherwise use IDs
if [ -n "$CONTAINER_NAMES" ]; then
    # Use container names
    for container_name in $CONTAINER_NAMES; do
        echo "  Stopping and removing: $container_name..."
        ssh_command "docker stop $container_name" || true
        ssh_command "docker rm -f $container_name" || true
    done
elif [ -n "$CONTAINER_IDS" ]; then
    # Fallback to IDs if names not available
    for container_id in $CONTAINER_IDS; do
        echo "  Stopping and removing: $container_id..."
        ssh_command "docker stop $container_id" || true
        ssh_command "docker rm -f $container_id" || true
    done
fi

# Verify containers are removed
echo ""
echo "✅ Verifying containers are removed:"
REMAINING=$(ssh_command "docker ps -a --format '{{.Names}}' 2>/dev/null | grep 'ostg-frr' || true")
if [ -z "$REMAINING" ]; then
    echo "✅ All OSTG FRR containers have been removed."
else
    echo "⚠️  Some containers still remain:"
    echo "$REMAINING" | while read name; do
        if [ -n "$name" ]; then
            echo "  - $name"
        fi
    done
fi

# Clean up unused resources
echo ""
echo "🧽 Cleaning up unused Docker resources..."
ssh_command "docker image prune -f"
ssh_command "docker volume prune -f"
ssh_command "docker network prune -f"

echo ""
echo "✅ Cleanup completed!"
