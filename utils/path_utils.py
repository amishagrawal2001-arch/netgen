#!/usr/bin/env python3
"""
OSTG Path Utilities
Handles file paths for both development and packaged macOS applications.
"""

import os
import sys
from pathlib import Path


def get_ostg_data_directory():
    """
    Get the appropriate data directory for OSTG based on the environment.
    
    For development: Uses project directory
    For macOS app: Uses ~/Documents/OSTG or ~/Library/Application Support/OSTG
    For other platforms: Uses appropriate user data directory
    
    Returns:
        str: Path to the OSTG data directory
    """
    # Check if we're running from a PyInstaller bundle (macOS app)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # We're running from a PyInstaller bundle
        if sys.platform == 'darwin':  # macOS
            # Try Documents first, then Application Support
            documents_dir = Path.home() / "Documents" / "OSTG"
            app_support_dir = Path.home() / "Library" / "Application Support" / "OSTG"
            
            # Prefer Documents as it's more visible to users
            data_dir = documents_dir
        elif sys.platform == 'win32':  # Windows
            data_dir = Path.home() / "Documents" / "OSTG"
        else:  # Linux and others
            data_dir = Path.home() / ".local" / "share" / "OSTG"
    else:
        # Development environment - use project directory
        # Look for common project locations
        possible_dirs = [
            os.getcwd(),  # Current working directory
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # Two levels up from this file
            os.path.expanduser("~/FLASK/OSTG"),  # Common project location
            "/Users/surajsharma/FLASK/OSTG"  # Absolute path
        ]
        
        data_dir = None
        for dir_path in possible_dirs:
            if os.path.exists(dir_path) and os.path.isdir(dir_path):
                data_dir = dir_path
                break
        
        if not data_dir:
            data_dir = os.getcwd()  # Fallback to current directory
    
    # Convert to Path object and ensure directory exists
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    
    return str(data_dir)


def get_session_file_path():
    """
    Get the full path to the session.json file.
    
    Returns:
        str: Full path to session.json
    """
    data_dir = get_ostg_data_directory()
    return os.path.join(data_dir, "session.json")


def get_logs_directory():
    """
    Get the directory for log files.
    
    Returns:
        str: Path to the logs directory
    """
    data_dir = get_ostg_data_directory()
    logs_dir = os.path.join(data_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def get_config_directory():
    """
    Get the directory for configuration files.
    
    Returns:
        str: Path to the config directory
    """
    data_dir = get_ostg_data_directory()
    config_dir = os.path.join(data_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_temp_directory():
    """
    Get the directory for temporary files.
    
    Returns:
        str: Path to the temp directory
    """
    data_dir = get_ostg_data_directory()
    temp_dir = os.path.join(data_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def is_packaged_app():
    """
    Check if the application is running from a packaged bundle.
    
    Returns:
        bool: True if running from PyInstaller bundle, False otherwise
    """
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_app_info():
    """
    Get information about the current application environment.
    
    Returns:
        dict: Information about the app environment
    """
    info = {
        'is_packaged': is_packaged_app(),
        'platform': sys.platform,
        'data_directory': get_ostg_data_directory(),
        'session_file': get_session_file_path(),
        'logs_directory': get_logs_directory(),
        'config_directory': get_config_directory(),
        'temp_directory': get_temp_directory()
    }
    
    if is_packaged_app():
        info['bundle_path'] = sys._MEIPASS
        info['executable_path'] = sys.executable
    else:
        info['development_mode'] = True
        info['working_directory'] = os.getcwd()
    
    return info


# Test function to verify path utilities
def test_path_utilities():
    """Test function to verify path utilities work correctly."""
    print("🔍 OSTG Path Utilities Test")
    print("=" * 50)
    
    info = get_app_info()
    
    print(f"Platform: {info['platform']}")
    print(f"Packaged App: {info['is_packaged']}")
    print(f"Data Directory: {info['data_directory']}")
    print(f"Session File: {info['session_file']}")
    print(f"Logs Directory: {info['logs_directory']}")
    
    # Test directory creation
    data_dir = get_ostg_data_directory()
    session_file = get_session_file_path()
    logs_dir = get_logs_directory()
    
    print(f"\n✅ Data directory exists: {os.path.exists(data_dir)}")
    print(f"✅ Logs directory exists: {os.path.exists(logs_dir)}")
    print(f"✅ Can write to data directory: {os.access(data_dir, os.W_OK)}")
    
    # Test session file path
    print(f"\n📄 Session file path: {session_file}")
    print(f"✅ Session file directory exists: {os.path.exists(os.path.dirname(session_file))}")
    print(f"✅ Can write to session directory: {os.access(os.path.dirname(session_file), os.W_OK)}")


if __name__ == "__main__":
    test_path_utilities()
