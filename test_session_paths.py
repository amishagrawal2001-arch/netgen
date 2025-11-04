#!/usr/bin/env python3
"""
Test script to verify session.json saving works correctly
in both development and packaged macOS app environments.
"""

import json
import os
import sys
from utils.path_utils import get_session_file_path, get_app_info


def test_session_saving():
    """Test session saving functionality."""
    print("🧪 Testing OSTG Session Saving")
    print("=" * 50)
    
    # Get app info
    app_info = get_app_info()
    print(f"Environment: {'Packaged App' if app_info['is_packaged'] else 'Development'}")
    print(f"Platform: {app_info['platform']}")
    print(f"Data Directory: {app_info['data_directory']}")
    print()
    
    # Test session file path
    session_file = get_session_file_path()
    print(f"📄 Session file path: {session_file}")
    
    # Test data directory creation
    session_dir = os.path.dirname(session_file)
    if not os.path.exists(session_dir):
        print(f"❌ Session directory does not exist: {session_dir}")
        return False
    else:
        print(f"✅ Session directory exists: {session_dir}")
    
    # Test write permissions
    if not os.access(session_dir, os.W_OK):
        print(f"❌ Cannot write to session directory: {session_dir}")
        return False
    else:
        print(f"✅ Can write to session directory: {session_dir}")
    
    # Test session file creation
    test_data = {
        "test": True,
        "timestamp": "2024-01-01T00:00:00Z",
        "devices": [],
        "servers": [],
        "streams": []
    }
    
    try:
        # Write test session file
        with open(session_file, 'w') as f:
            json.dump(test_data, f, indent=2)
        print(f"✅ Successfully wrote test session file")
        
        # Read it back
        with open(session_file, 'r') as f:
            loaded_data = json.load(f)
        
        if loaded_data == test_data:
            print(f"✅ Successfully read test session file")
        else:
            print(f"❌ Session file data mismatch")
            return False
        
        # Clean up test file
        os.remove(session_file)
        print(f"✅ Cleaned up test session file")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing session file: {e}")
        return False


def test_client_session_import():
    """Test that the client can import and use the session functionality."""
    print(f"\n🔧 Testing Client Session Import")
    print("-" * 30)
    
    try:
        # Test importing the updated menu_actions
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        
        # This would normally be imported by the client
        from traffic_client.menu_actions import TrafficGenClientMenuAction
        print("✅ Successfully imported TrafficGenClientMenuAction")
        
        # Test that the class has the updated methods
        if hasattr(TrafficGenClientMenuAction, 'save_session'):
            print("✅ save_session method exists")
        else:
            print("❌ save_session method missing")
            return False
            
        if hasattr(TrafficGenClientMenuAction, 'load_session'):
            print("✅ load_session method exists")
        else:
            print("❌ load_session method missing")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Error importing client session functionality: {e}")
        return False


def main():
    """Main test function."""
    print("🚀 OSTG Session Path Testing")
    print("=" * 50)
    
    # Test session saving
    session_ok = test_session_saving()
    
    # Test client imports
    import_ok = test_client_session_import()
    
    # Summary
    print(f"\n📊 Test Results")
    print("=" * 50)
    print(f"Session Saving: {'✅ PASS' if session_ok else '❌ FAIL'}")
    print(f"Client Import: {'✅ PASS' if import_ok else '❌ FAIL'}")
    
    if session_ok and import_ok:
        print(f"\n🎉 All tests passed! Session saving should work correctly.")
        print(f"\nFor macOS DMG apps, session.json will be saved to:")
        print(f"  {get_session_file_path()}")
        return True
    else:
        print(f"\n❌ Some tests failed. Please check the issues above.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
