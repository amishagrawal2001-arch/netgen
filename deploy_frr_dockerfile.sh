#!/bin/bash
# Copy updated Dockerfile.frr to server and build FRR image with alternate Alpine mirror
# when the default CDN (dl-cdn.alpinelinux.org) is unreachable.
# Usage: ./deploy_frr_dockerfile.sh [user@]host
# Example: ./deploy_frr_dockerfile.sh root@san-ft-ai-srv01

set -e
HOST="${1:?Usage: $0 user@host}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Copying Dockerfile.frr to $HOST:/opt/OSTG/"
scp "$SCRIPT_DIR/Dockerfile.frr" "$HOST:/opt/OSTG/"

echo "Building ostg-frr:latest on $HOST with alternate Alpine mirror..."
ssh "$HOST" 'docker build --build-arg ALPINE_MIRROR=https://ftp.halifax.rwth-aachen.de/alpine -t ostg-frr:latest -f /opt/OSTG/Dockerfile.frr /opt/OSTG'

echo "Done. Verify with: ssh $HOST docker images ostg-frr"
