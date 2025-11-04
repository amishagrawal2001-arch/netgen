#!/bin/bash

# Quick OSTG Wheel Rebuild Script
# Simple version for quick rebuilds

set -e

echo "🔄 Rebuilding OSTG wheel package..."

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf build/ dist/ *.egg-info/

# Build the wheel
echo "🔨 Building wheel package..."
python3 -m build

# Copy to build_image directory (primary location for deployment)
echo "📦 Copying wheel to build_image directory..."
cp dist/ostg_trafficgen-0.1.52-py3-none-any.whl build_image/

# Also copy to root directory as fallback
echo "📦 Copying wheel to root directory (fallback)..."
cp dist/ostg_trafficgen-0.1.52-py3-none-any.whl .

echo "✅ Rebuild completed!"
echo "📁 Wheel file: ostg_trafficgen-0.1.52-py3-none-any.whl"
echo ""
echo "🚀 Ready for deployment:"
echo "   ./deploy.sh -t wheel-only"
echo "   or"
echo "   ./deploy_quick.sh"
echo ""
echo "📋 For comprehensive rebuild with validation:"
echo "   ./rebuild_wheel.sh"
