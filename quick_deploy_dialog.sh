#!/bin/bash
# Quick deployment script for the updated dialog
echo 'Deploying updated add_bgp_route_dialog.py...'

# Copy the file to the server
scp widgets/add_bgp_route_dialog.py root@svl-hp-ai-srv04:/opt/OSTG/lib/python3.10/site-packages/widgets/ 2>/dev/null || {
    echo 'Direct copy failed, trying alternative method...'
    # Alternative: use the existing deployment script with just the file
    ./deploy.sh --host svl-hp-ai-srv04 --type wheel-only --skip-build
}

echo 'Restarting OSTG server...'
ssh root@svl-hp-ai-srv04 'systemctl restart ostg-server' 2>/dev/null || echo 'Server restart command failed'

echo 'Deployment completed!'

