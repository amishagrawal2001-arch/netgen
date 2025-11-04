#!/usr/bin/env python3
"""
Test script for the AddDeviceDialog to verify the new protocol selection layout.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from widgets.add_device_dialog import AddDeviceDialog

def test_dialog():
    """Test the AddDeviceDialog with the new protocol selection layout."""
    app = QApplication(sys.argv)
    
    # Create the dialog
    dialog = AddDeviceDialog(default_iface="TG1 - Port: ens4np0")
    
    # Show the dialog
    dialog.show()
    
    print("✅ AddDeviceDialog created successfully!")
    print("📋 Features to test:")
    print("   - Interface Configuration (Device Name, then Interface/VLAN-ID/MAC in one row)")
    print("   - IP Configuration (IPv4/IPv6 with properly sized Address, Mask, Gateway fields)")
    print("   - Protocol Configuration with three subsections:")
    print("     * Top: Enable Protocol checkboxes (BGP, OSPF, ISIS, DHCP, ROCEv2)")
    print("     * Left: Compact protocol dropdown (populated with enabled protocols)")
    print("     * Right: Protocol-specific configuration panels with multi-column layout")
    print("   - Dynamic dropdown population based on enabled protocols")
    print("   - Multi-column protocol configuration panels for better space utilization")
    print("   - Editable protocol configuration fields (enabled when protocol is selected)")
    print("   - Increment options with Enable All checkbox and count in same row")
    print("   - Default increment count: 2, Default position: 2nd for IPv4/IPv6/MAC/Gateway")
    print("   - Scrollable content (no overlapping)")
    print("   - Optimized dialog width (1000px) to fit all fields with generous spacing")
    
    # Run the application
    sys.exit(app.exec_())

if __name__ == "__main__":
    test_dialog()
