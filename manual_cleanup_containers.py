#!/usr/bin/env python3
"""
Manual cleanup script for orphaned OSTG FRR containers
Removes containers that don't have corresponding database entries
"""

import requests
import argparse
import sys

def remove_container_via_api(server_url, container_name):
    """Remove a container via API"""
    # Extract device_id from container name
    if not container_name.startswith('ostg-frr-'):
        print(f"❌ Invalid container name format: {container_name}")
        return False
    
    device_id = container_name[len('ostg-frr-'):]
    print(f"Removing container: {container_name}")
    print(f"  Device ID: {device_id}")
    
    try:
        response = requests.post(
            f'{server_url}/api/device/remove',
            json={'device_id': device_id, 'device_name': ''},
            timeout=15
        )
        
        if response.status_code == 200:
            result = response.json()
            container_removed = result.get('container_removed', False)
            status = result.get('status', '')
            
            if container_removed:
                print(f"  ✅ Successfully removed container via API")
                return True
            else:
                print(f"  ⚠️  API returned success but container_removed={container_removed}")
                print(f"  Status: {status}")
                return False
        else:
            print(f"  ❌ Failed with status {response.status_code}")
            print(f"  Response: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Manually cleanup orphaned OSTG FRR containers')
    parser.add_argument('--server', default='http://svl-hp-ai-srv04:5051', 
                       help='Server URL (default: http://svl-hp-ai-srv04:5051)')
    parser.add_argument('containers', nargs='+', help='Container names to remove')
    
    args = parser.parse_args()
    
    print('🧹 Cleaning up orphaned containers...\n')
    
    success_count = 0
    for container_name in args.containers:
        if remove_container_via_api(args.server, container_name):
            success_count += 1
        print()
    
    print(f'✅ Cleanup complete! Successfully removed {success_count}/{len(args.containers)} containers')
    
    if success_count < len(args.containers):
        print('\n⚠️  Some containers may need manual removal via SSH:')
        print('  docker stop <container_name> && docker rm <container_name>')

if __name__ == '__main__':
    main()

