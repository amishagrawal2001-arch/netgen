#!/bin/bash

# Quick Deployment Wrapper Script
# Common deployment scenarios for OSTG project

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

echo "🚀 OSTG Quick Deployment Options"
echo "================================="
echo ""
echo "1) Full Deployment (rebuild + deploy everything)"
echo "2) Source Code Only (deploy code changes)"
echo "3) Wheel Package Only (deploy built package)"
echo "4) Configuration Only (deploy config files)"
echo "5) Force Rebuild & Deploy (rebuild even if no changes)"
echo "6) Deploy Without Backup (faster deployment)"
echo "7) Custom deployment with options"
echo "8) Deploy to different server"
echo ""

read -p "Select deployment type (1-8): " choice

case $choice in
    1)
        log "Starting Full Deployment..."
        ./deploy.sh --type full
        ;;
    2)
        log "Starting Source Code Deployment..."
        ./deploy.sh --type source-only
        ;;
    3)
        log "Starting Wheel Package Deployment..."
        ./deploy.sh --type wheel-only
        ;;
    4)
        log "Starting Configuration Deployment..."
        ./deploy.sh --type config-only
        ;;
    5)
        log "Starting Force Rebuild & Deploy..."
        ./deploy.sh --type full --force-rebuild
        ;;
    6)
        log "Starting Deployment Without Backup..."
        ./deploy.sh --type full --no-backup --no-verify
        ;;
    7)
        echo ""
        info "Available options:"
        echo "  --type TYPE        (full|wheel-only|source-only|config-only)"
        echo "  --no-backup        Skip backup"
        echo "  --no-verify        Skip verification"
        echo "  --no-start         Don't start server"
        echo "  --no-clean         Don't clean temp files"
        echo "  --force-rebuild    Force rebuild"
        echo ""
        read -p "Enter deployment options: " options
        log "Starting Custom Deployment..."
        ./deploy.sh $options
        ;;
    8)
        echo ""
        info "Deploy to different server"
        read -p "Enter server hostname/IP: " server_host
        read -p "Enter SSH username: " server_user
        read -s -p "Enter SSH password: " server_pass
        echo ""
        read -p "Enter deployment type (full|wheel-only|source-only): " deploy_type
        log "Starting deployment to $server_host..."
        ./deploy.sh --host "$server_host" --user "$server_user" --pass "$server_pass" --type "$deploy_type"
        ;;
    *)
        echo "Invalid option. Exiting."
        exit 1
        ;;
esac

echo ""
log "✅ Deployment completed!"
info "Connect to: http://svl-hp-ai-srv02:5051"
