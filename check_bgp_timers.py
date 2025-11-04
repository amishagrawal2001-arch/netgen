#!/usr/bin/env python3
"""
Check BGP timer configuration in existing FRR containers.
This script checks if the BGP timer values are correctly applied.
"""

import docker
import json

def check_bgp_timers():
    """Check BGP timer configuration in existing FRR containers."""
    
    print("🔍 BGP Timer Configuration Check")
    print("=================================")
    
    try:
        # Connect to Docker
        client = docker.from_env()
        
        # Find all OSTG FRR containers
        containers = client.containers.list(filters={"name": "ostg-frr"})
        
        if not containers:
            print("❌ No OSTG FRR containers found")
            return False
        
        print(f"Found {len(containers)} OSTG FRR containers:")
        
        for container in containers:
            container_name = container.name
            print(f"\n📋 Checking container: {container_name}")
            
            try:
                # Get BGP configuration
                result = container.exec_run("vtysh -c 'show running-config' | grep -A 20 'router bgp'")
                
                if result.exit_code == 0:
                    config_output = result.output.decode('utf-8')
                    print("BGP Configuration:")
                    print(config_output)
                    
                    # Check for timer configuration
                    if "timers" in config_output:
                        print("✅ Timer configuration found in BGP config")
                        
                        # Extract timer values
                        import re
                        timer_match = re.search(r'timers (\d+) (\d+)', config_output)
                        if timer_match:
                            keepalive = timer_match.group(1)
                            hold_time = timer_match.group(2)
                            print(f"   Keepalive: {keepalive}s")
                            print(f"   Hold-time: {hold_time}s")
                        else:
                            print("   ⚠️  Could not extract timer values")
                    else:
                        print("❌ No timer configuration found in BGP config")
                        
                else:
                    print(f"❌ Failed to get BGP configuration: {result.output.decode('utf-8')}")
                    
            except Exception as e:
                print(f"❌ Error checking container {container_name}: {e}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    check_bgp_timers()
