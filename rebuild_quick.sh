#!/bin/bash

# Quick wheel rebuild — used by deploy.sh as a recovery step + by
# operators who just want to bump the wheel without the heavier
# rebuild_wheel.sh validation. Auto-detects the version from
# pyproject.toml so a `version = "0.2.4"` bump doesn't need a
# parallel edit here (which is exactly the bug the 0.1.52 hardcoding
# caused twice before).

set -e

# Pick the wheel version straight from pyproject.toml.
VERSION="$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: Could not parse version from pyproject.toml" >&2
    exit 1
fi
WHEEL_FILE="ostg_trafficgen-${VERSION}-py3-none-any.whl"

echo "🔄 Rebuilding Netgen wheel ($VERSION)..."

# Clean previous builds. egg-info is the worst offender — it caches
# the old version even when pyproject.toml has been updated.
echo "🧹 Cleaning previous builds..."
rm -rf build/ dist/ *.egg-info/

echo "🔨 Building wheel package..."
python3 -m build

# Verify the wheel actually came out at the version we expect.
if [[ ! -f "dist/$WHEEL_FILE" ]]; then
    echo "ERROR: Expected dist/$WHEEL_FILE but it's not there." >&2
    echo "Built files:" >&2
    ls -1 dist/ >&2
    exit 1
fi

# Copy to build_image directory (primary location for deployment).
mkdir -p build_image
echo "📦 Copying wheel to build_image/..."
cp "dist/$WHEEL_FILE" build_image/

# Also copy to root for legacy deploy paths.
echo "📦 Copying wheel to repo root (fallback)..."
cp "dist/$WHEEL_FILE" .

echo "✅ Rebuild completed."
echo "📁 Wheel: $WHEEL_FILE"
echo ""
echo "🚀 Ready for deployment:"
echo "   SERVER_HOST=<host> ./deploy.sh -t wheel-only"
echo ""
echo "📋 For comprehensive rebuild with validation:"
echo "   ./rebuild_wheel.sh"
