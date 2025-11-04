#!/bin/bash
# install_ostg_client_env.sh
# Installation script for OSTG client environment with Qt platform plugin fix

set -e  # Exit on any error

echo "🚀 Installing OSTG Client Environment..."

# Create virtual environment
echo "📦 Creating virtual environment..."
python3 -m venv ostg_client_env

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source ostg_client_env/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install PyQt5 from Qt's official wheels (fixes macOS platform plugin issues)
echo "🎨 Installing PyQt5 with macOS platform plugin support..."
pip install --find-links https://download.qt.io/snapshots/ci/pyqt5/5.15/wheels/ PyQt5

# Install other requirements
echo "📋 Installing other requirements..."
pip install -r requirements.txt

# Install the package in development mode
echo "🔨 Installing ostg-trafficgen package in development mode..."
pip install -e .

# Make wrapper script executable
echo "🔧 Making wrapper script executable..."
chmod +x ostg_client_wrapper.sh

echo ""
echo "✅ Installation completed successfully!"
echo ""
echo "To use the OSTG client:"
echo "1. Activate environment: source ostg_client_env/bin/activate"
echo "2. Run GUI client: python3 run_tgen_client.py"
echo "3. Or use wrapper: ./ostg_client_wrapper.sh"
echo "4. Or use installed command: ostg-client"
echo ""
echo "The Qt platform plugin issue has been automatically fixed for macOS."

