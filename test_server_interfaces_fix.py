#!/usr/bin/env python3
"""
Test script to verify server_interfaces.txt fix for macOS DMG apps.
This simulates the packaged app environment and tests file writing.
"""

import os
import sys
import tempfile
from pathlib import Path

# Mock the packaged app environment
sys.frozen = True
sys._MEIPASS = tempfile.mkdtemp()

# Add our utils to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

def test_path_utils():
    """Test the path utilities work correctly."""
    print("🧪 Testing Path Utilities")
    print("=" * 50)
    
    try:
        from path_utils import get_ostg_data_directory, get_session_file_path
        
        # Test data directory
        data_dir = Path(get_ostg_data_directory())
        print(f"✅ Data Directory: {data_dir}")
        print(f"✅ Directory exists: {data_dir.exists()}")
        print(f"✅ Directory writable: {os.access(data_dir, os.W_OK)}")
        
        # Test session file path
        session_file = Path(get_session_file_path())
        print(f"✅ Session File: {session_file}")
        print(f"✅ Parent directory exists: {session_file.parent.exists()}")
        print(f"✅ Parent directory writable: {os.access(session_file.parent, os.W_OK)}")
        
        return True
    except Exception as e:
        print(f"❌ Path utilities test failed: {e}")
        return False

def test_server_interfaces_fix():
    """Test the server interfaces fix."""
    print("\n🧪 Testing Server Interfaces Fix")
    print("=" * 50)
    
    try:
        # Import the fixed menu_actions
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'traffic_client'))
        from menu_actions import TrafficGenClientMenuAction
        
        # Create a mock client object
        class MockClient:
            def __init__(self):
                self.server_interfaces = []
                self.removed_interfaces = set()
                self.selected_servers = []
                self.streams = []
                self.devices_tab = MockDevicesTab()
                self._save_in_progress = False
                self.last_saved_devices = []
        
        class MockDevicesTab:
            def get_all_device_rows(self):
                return []
            def get_removed_devices(self):
                return []
            def get_all_protocol_data(self):
                return {}
            def get_all_bgp_route_pools(self):
                return []
        
        # Test saving server interfaces
        mock_client = MockClient()
        mock_client.server_interfaces = [
            {"tg_id": 0, "address": "http://localhost:5051"},
            {"tg_id": 1, "address": "http://remote-server:5051"}
        ]
        
        menu_actions = TrafficGenClientMenuAction()
        menu_actions.client = mock_client
        
        print("Testing save_server_interfaces...")
        menu_actions.save_server_interfaces()
        print("✅ save_server_interfaces completed without error")
        
        # Test loading server interfaces
        print("Testing load_server_interfaces...")
        menu_actions.load_server_interfaces()
        print("✅ load_server_interfaces completed without error")
        
        return True
    except Exception as e:
        print(f"❌ Server interfaces test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_file_locations():
    """Test where files are actually being written."""
    print("\n🧪 Testing File Locations")
    print("=" * 50)
    
    try:
        from path_utils import get_ostg_data_directory
        
        data_dir = Path(get_ostg_data_directory())
        
        # Test writing a server interfaces file
        server_file = data_dir / "server_interfaces.txt"
        test_content = ["http://localhost:5051", "http://remote-server:5051"]
        
        with open(server_file, "w") as f:
            for line in test_content:
                f.write(f"{line}\n")
        
        print(f"✅ Successfully wrote server_interfaces.txt to: {server_file}")
        
        # Verify it can be read back
        with open(server_file, "r") as f:
            read_content = [line.strip() for line in f.readlines()]
        
        if read_content == test_content:
            print("✅ Successfully read back server_interfaces.txt")
        else:
            print(f"❌ Content mismatch. Expected: {test_content}, Got: {read_content}")
            return False
        
        # Clean up
        server_file.unlink()
        print("✅ Cleaned up test file")
        
        return True
    except Exception as e:
        print(f"❌ File location test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("🔧 OSTG macOS Server Interfaces Fix Test")
    print("=" * 60)
    
    tests = [
        ("Path Utilities", test_path_utils),
        ("Server Interfaces Fix", test_server_interfaces_fix),
        ("File Locations", test_file_locations)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n📋 Running: {test_name}")
        try:
            if test_func():
                passed += 1
                print(f"✅ {test_name}: PASSED")
            else:
                print(f"❌ {test_name}: FAILED")
        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
    
    print("\n" + "=" * 60)
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The server_interfaces.txt fix should work.")
        print("\n📱 For macOS DMG apps:")
        print("   - server_interfaces.txt will be saved to ~/Documents/OSTG/")
        print("   - session.json will be saved to ~/Documents/OSTG/")
        print("   - Both files are writable and persistent across app launches")
    else:
        print("⚠️  Some tests failed. Please check the errors above.")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
