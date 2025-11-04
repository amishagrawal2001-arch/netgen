#!/usr/bin/env python3

"""
Cleanup script for OSTG device database
Removes orphaned device entries that no longer have corresponding containers
Supports both local database access and remote API access
"""

import sqlite3
import os
import sys
import json
import requests
import argparse
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_database_path():
    """Get the database path from environment or use default"""
    return os.environ.get('OSTG_DB_PATH', '/opt/OSTG/device_database.db')

def get_docker_containers():
    """Get list of running OSTG FRR containers (local only)"""
    try:
        import docker
        client = docker.from_env()
        containers = client.containers.list(filters={"name": "ostg-frr"})
        container_names = [container.name for container in containers]
        logger.info(f"Found {len(container_names)} OSTG FRR containers: {container_names}")
        return container_names
    except Exception as e:
        logger.error(f"Failed to get Docker containers: {e}")
        return []

def get_devices_from_server(server_url):
    """Get all devices from server via API"""
    try:
        response = requests.get(f"{server_url}/api/device/database/devices", timeout=10)
        if response.status_code == 200:
            data = response.json()
            devices = data.get('devices', [])
            logger.info(f"Retrieved {len(devices)} devices from server")
            return devices
        else:
            logger.error(f"Failed to get devices from server: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Failed to connect to server: {e}")
        return []

def get_containers_from_server(server_url):
    """Get running containers from server via API"""
    try:
        response = requests.get(f"{server_url}/api/docker/containers", timeout=10)
        if response.status_code == 200:
            containers = response.json()
            # Filter for OSTG FRR containers
            ostg_containers = [c for c in containers if c.get('name', '').startswith('ostg-frr')]
            logger.info(f"Found {len(ostg_containers)} OSTG FRR containers on server")
            return ostg_containers
        elif response.status_code == 404:
            logger.warning("Containers API not available, skipping container check")
            return []
        else:
            logger.error(f"Failed to get containers from server: {response.status_code}")
            return []
    except Exception as e:
        logger.warning(f"Failed to get containers from server: {e}")
        return []

def remove_device_from_server(server_url, device_id):
    """Remove a device from server via API"""
    try:
        response = requests.post(f"{server_url}/api/device/remove", 
                               json={"device_id": device_id}, 
                               timeout=10)
        if response.status_code in [200, 404]:
            return True
        else:
            logger.error(f"Failed to remove device {device_id}: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Failed to remove device {device_id}: {e}")
        return False

def extract_device_id_from_container_name(container_name):
    """Extract device ID from container name (e.g., ostg-frr-device1-abc123 -> abc123)"""
    try:
        # Container name format: ostg-frr-{device_name}-{device_id}
        parts = container_name.split('-')
        if len(parts) >= 4:
            # Join all parts after the first 3 (ostg-frr-{device_name})
            device_id = '-'.join(parts[3:])
            return device_id
        return None
    except Exception as e:
        logger.warning(f"Failed to extract device ID from container name {container_name}: {e}")
        return None

def cleanup_orphaned_devices_local():
    """Remove device entries that don't have corresponding containers (local database)"""
    db_path = get_database_path()
    
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return
    
    # Get running containers
    container_names = get_docker_containers()
    container_device_ids = set()
    
    for container_name in container_names:
        device_id = extract_device_id_from_container_name(container_name)
        if device_id:
            container_device_ids.add(device_id)
    
    logger.info(f"Device IDs from containers: {container_device_ids}")
    
    # Connect to database
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all device IDs from database
        cursor.execute("SELECT device_id, device_name FROM devices")
        db_devices = cursor.fetchall()
        
        logger.info(f"Found {len(db_devices)} devices in database:")
        for device_id, device_name in db_devices:
            logger.info(f"  - {device_id}: {device_name}")
        
        # Find orphaned devices
        orphaned_devices = []
        for device_id, device_name in db_devices:
            if device_id not in container_device_ids:
                orphaned_devices.append((device_id, device_name))
        
        if not orphaned_devices:
            logger.info("No orphaned devices found")
            return
        
        logger.warning(f"Found {len(orphaned_devices)} orphaned devices:")
        for device_id, device_name in orphaned_devices:
            logger.warning(f"  - {device_id}: {device_name}")
        
        # Ask for confirmation
        response = input(f"\nRemove {len(orphaned_devices)} orphaned devices? (y/N): ")
        if response.lower() != 'y':
            logger.info("Cleanup cancelled")
            return
        
        # Remove orphaned devices
        for device_id, device_name in orphaned_devices:
            try:
                # Remove device and related data
                cursor.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
                cursor.execute("DELETE FROM device_stats WHERE device_id = ?", (device_id,))
                cursor.execute("DELETE FROM device_events WHERE device_id = ?", (device_id,))
                
                logger.info(f"Removed orphaned device: {device_id} ({device_name})")
            except Exception as e:
                logger.error(f"Failed to remove device {device_id}: {e}")
        
        # Commit changes
        conn.commit()
        logger.info(f"Successfully removed {len(orphaned_devices)} orphaned devices")
        
    except Exception as e:
        logger.error(f"Database error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def cleanup_orphaned_devices_server(server_url, force=False):
    """Remove device entries that don't have corresponding containers (via server API)"""
    logger.info(f"Connecting to server: {server_url}")
    
    # Get devices from server
    devices = get_devices_from_server(server_url)
    if not devices:
        logger.error("Failed to retrieve devices from server")
        return
    
    # Get containers from server
    containers = get_containers_from_server(server_url)
    container_device_ids = set()
    
    for container in containers:
        container_name = container.get('name', '')
        device_id = extract_device_id_from_container_name(container_name)
        if device_id:
            container_device_ids.add(device_id)
    
    logger.info(f"Device IDs from containers: {container_device_ids}")
    
    # Find orphaned devices (devices without containers)
    orphaned_devices = []
    for device in devices:
        device_id = device.get('device_id')
        device_name = device.get('device_name', 'Unknown')
        
        if device_id and device_id not in container_device_ids:
            orphaned_devices.append((device_id, device_name))
    
    # Also find duplicate devices (same device name, different IDs)
    device_names = {}
    duplicate_devices = []
    for device in devices:
        device_id = device.get('device_id')
        device_name = device.get('device_name', 'Unknown')
        
        if device_name in device_names:
            # Found duplicate - keep the older one, mark newer for removal
            existing_id, existing_created = device_names[device_name]
            current_created = device.get('created_at', '')
            
            if current_created > existing_created:
                # Current device is newer, mark it for removal
                duplicate_devices.append((device_id, device_name))
            else:
                # Existing device is newer, mark it for removal and update tracking
                duplicate_devices.append((existing_id, device_name))
                device_names[device_name] = (device_id, current_created)
        else:
            device_names[device_name] = (device_id, device.get('created_at', ''))
    
    # Combine orphaned and duplicate devices
    devices_to_remove = list(set(orphaned_devices + duplicate_devices))
    
    if not devices_to_remove:
        logger.info("No devices to remove found")
        return
    
    logger.warning(f"Found {len(devices_to_remove)} devices to remove:")
    for device_id, device_name in devices_to_remove:
        logger.warning(f"  - {device_id}: {device_name}")
    
    # Ask for confirmation unless force is enabled
    if not force:
        try:
            response = input(f"\nRemove {len(devices_to_remove)} devices? (y/N): ")
            if response.lower() != 'y':
                logger.info("Cleanup cancelled")
                return
        except EOFError:
            logger.error("Cannot get user input in non-interactive mode. Use --force to skip confirmation.")
            return
    
    # Remove devices
    removed_count = 0
    for device_id, device_name in devices_to_remove:
        if remove_device_from_server(server_url, device_id):
            logger.info(f"Removed device: {device_id} ({device_name})")
            removed_count += 1
        else:
            logger.error(f"Failed to remove device: {device_id} ({device_name})")
    
    logger.info(f"Successfully removed {removed_count} devices")

def show_database_status():
    """Show current database status"""
    db_path = get_database_path()
    
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Count devices
        cursor.execute("SELECT COUNT(*) FROM devices")
        device_count = cursor.fetchone()[0]
        
        # Count running devices
        cursor.execute("SELECT COUNT(*) FROM devices WHERE status = 'Running'")
        running_count = cursor.fetchone()[0]
        
        # Count stopped devices
        cursor.execute("SELECT COUNT(*) FROM devices WHERE status = 'Stopped'")
        stopped_count = cursor.fetchone()[0]
        
        logger.info(f"Database Status:")
        logger.info(f"  Total devices: {device_count}")
        logger.info(f"  Running devices: {running_count}")
        logger.info(f"  Stopped devices: {stopped_count}")
        
        # Show all devices
        cursor.execute("SELECT device_id, device_name, status, created_at FROM devices ORDER BY created_at DESC")
        devices = cursor.fetchall()
        
        if devices:
            logger.info(f"\nAll devices:")
            for device_id, device_name, status, created_at in devices:
                logger.info(f"  - {device_id}: {device_name} ({status}) - {created_at}")
        
    except Exception as e:
        logger.error(f"Database error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Cleanup OSTG device database')
    parser.add_argument('--server', help='Server URL for remote cleanup (e.g., http://server:5051)')
    parser.add_argument('--status', action='store_true', help='Show database status only')
    parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    
    args = parser.parse_args()
    
    if args.status:
        if args.server:
            show_database_status_server(args.server)
        else:
            show_database_status()
    else:
        if args.server:
            cleanup_orphaned_devices_server(args.server, force=args.force)
        else:
            cleanup_orphaned_devices_local()

def show_database_status_server(server_url):
    """Show current database status via server API"""
    logger.info(f"Connecting to server: {server_url}")
    
    devices = get_devices_from_server(server_url)
    if not devices:
        logger.error("Failed to retrieve devices from server")
        return
    
    # Count devices by status
    total_count = len(devices)
    running_count = len([d for d in devices if d.get('status') == 'Running'])
    stopped_count = len([d for d in devices if d.get('status') == 'Stopped'])
    
    logger.info(f"Database Status (via server):")
    logger.info(f"  Total devices: {total_count}")
    logger.info(f"  Running devices: {running_count}")
    logger.info(f"  Stopped devices: {stopped_count}")
    
    if devices:
        logger.info(f"\nAll devices:")
        for device in devices:
            device_id = device.get('device_id', 'Unknown')
            device_name = device.get('device_name', 'Unknown')
            status = device.get('status', 'Unknown')
            created_at = device.get('created_at', 'Unknown')
            logger.info(f"  - {device_id}: {device_name} ({status}) - {created_at}")

if __name__ == "__main__":
    main()
