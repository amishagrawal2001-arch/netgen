#!/usr/bin/env python3
"""
OSTG Installation Test Script
Tests the complete installation including Docker + FRR functionality
"""

import sys
import subprocess
import importlib
from pathlib import Path

def test_python_imports():
    """Test that all required Python modules can be imported"""
    print("Testing Python imports...")
    
    required_modules = [
        'flask',
        'flask_cors', 
        'scapy',
        'PyQt5',
        'requests',
        'yaml',
        'psutil',
        'docker'
    ]
    
    failed_imports = []
    for module in required_modules:
        try:
            importlib.import_module(module)
            print(f"✓ {module}")
        except ImportError as e:
            print(f"✗ {module}: {e}")
            failed_imports.append(module)
    
    if failed_imports:
        print(f"Failed to import: {', '.join(failed_imports)}")
        return False
    
    print("All Python imports successful")
    return True

def test_ostg_commands():
    """Test that OSTG commands are available"""
    print("\nTesting OSTG commands...")
    
    commands = [
        'ostg-server --help',
        'ostg-client --help', 
        'ostg-docker-install --help'
    ]
    
    failed_commands = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"✓ {cmd}")
            else:
                print(f"✗ {cmd}: {result.stderr}")
                failed_commands.append(cmd)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"✗ {cmd}: {e}")
            failed_commands.append(cmd)
    
    if failed_commands:
        print(f"Failed commands: {', '.join(failed_commands)}")
        return False
    
    print("All OSTG commands working")
    return True

def test_docker_installation():
    """Test Docker installation and FRR image"""
    print("\nTesting Docker installation...")
    
    # Test Docker command
    try:
        result = subprocess.run(['docker', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✓ Docker installed: {result.stdout.strip()}")
        else:
            print("✗ Docker not working")
            return False
    except FileNotFoundError:
        print("✗ Docker not found")
        return False
    
    # Test Docker daemon
    try:
        result = subprocess.run(['docker', 'info'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ Docker daemon running")
        else:
            print("✗ Docker daemon not running")
            return False
    except subprocess.CalledProcessError:
        print("✗ Docker daemon not accessible")
        return False
    
    # Test FRR image
    try:
        result = subprocess.run(['docker', 'images', 'ostg-frr:latest'], capture_output=True, text=True)
        if 'ostg-frr' in result.stdout:
            print("✓ OSTG FRR Docker image exists")
        else:
            print("✗ OSTG FRR Docker image not found")
            return False
    except subprocess.CalledProcessError:
        print("✗ Failed to check Docker images")
        return False
    
    # Test Docker network
    try:
        result = subprocess.run(['docker', 'network', 'ls'], capture_output=True, text=True)
        if 'ostg-frr-network' in result.stdout:
            print("✓ OSTG Docker network exists")
        else:
            print("✗ OSTG Docker network not found")
            return False
    except subprocess.CalledProcessError:
        print("✗ Failed to check Docker networks")
        return False
    
    print("Docker installation test passed")
    return True

def test_file_structure():
    """Test that all required files are present"""
    print("\nTesting file structure...")
    
    required_files = [
        '/opt/ostg/ostg.conf',
        '/opt/ostg/requirements.txt',
        '/opt/ostg/install_docker.sh',
        '/opt/ostg/setup_docker.py',
        '/opt/ostg/Dockerfile.frr',
        '/opt/ostg/start-frr.sh',
        '/opt/ostg/frr.conf.template'
    ]
    
    missing_files = []
    for file_path in required_files:
        if Path(file_path).exists():
            print(f"✓ {file_path}")
        else:
            print(f"✗ {file_path}")
            missing_files.append(file_path)
    
    if missing_files:
        print(f"Missing files: {', '.join(missing_files)}")
        return False
    
    print("File structure test passed")
    return True

def test_systemd_services():
    """Test systemd services"""
    print("\nTesting systemd services...")
    
    services = [
        'ostg-server.service',
        'ostg-client.service',
        'ostg-cleanup.timer'
    ]
    
    failed_services = []
    for service in services:
        try:
            result = subprocess.run(['systemctl', 'is-enabled', service], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✓ {service} is enabled")
            else:
                print(f"✗ {service} not enabled")
                failed_services.append(service)
        except subprocess.CalledProcessError:
            print(f"✗ {service} not found")
            failed_services.append(service)
    
    if failed_services:
        print(f"Failed services: {', '.join(failed_services)}")
        return False
    
    print("Systemd services test passed")
    return True

def main():
    """Run all tests"""
    print("=== OSTG Installation Test ===")
    print("Testing complete OSTG installation with Docker + FRR support\n")
    
    tests = [
        ("Python Imports", test_python_imports),
        ("OSTG Commands", test_ostg_commands),
        ("Docker Installation", test_docker_installation),
        ("File Structure", test_file_structure),
        ("Systemd Services", test_systemd_services)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n--- {test_name} ---")
        try:
            if test_func():
                passed += 1
            else:
                print(f"❌ {test_name} FAILED")
        except Exception as e:
            print(f"❌ {test_name} ERROR: {e}")
    
    print(f"\n=== Test Results ===")
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed! OSTG installation is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the installation.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
