#!/bin/bash
# ostg_client_wrapper.sh
# Wrapper script to fix Qt platform plugin issues on macOS

# Activate virtual environment
source ostg_client_env/bin/activate

# Set Qt plugin path to fix macOS platform plugin issue
export QT_PLUGIN_PATH="$VIRTUAL_ENV/lib/python3.9/site-packages/PyQt5/Qt5/plugins"

# Run the client with all arguments passed through
python3 run_tgen_client.py "$@"

