OSTG Device Database Query Examples
======================================

1. Show help and available options:
   python3 query_device_database.py --help

2. Show database information:
   python3 query_device_database.py --info

3. List all devices (basic):
   python3 query_device_database.py --list

4. List all devices with detailed information:
   python3 query_device_database.py --list --detailed

5. Show specific device information:
   python3 query_device_database.py --device <device_id>

6. Show specific device with events and statistics:
   python3 query_device_database.py --device <device_id> --events --stats

7. Search for devices by name or ID:
   python3 query_device_database.py --search <search_term>

8. Search with detailed information:
   python3 query_device_database.py --search <search_term> --detailed

9. Connect to different server:
   python3 query_device_database.py --server http://other-server:5051 --list

10. Default behavior (shows database info and device list):
    python3 query_device_database.py

📋 Common Use Cases:
===================

• Quick device overview:
  python3 query_device_database.py --list

• Detailed device analysis:
  python3 query_device_database.py --device my_device --detailed --events --stats

• Find devices by name:
  python3 query_device_database.py --search 'test'

• Database health check:
  python3 query_device_database.py --info

• Monitor all devices:
  python3 query_device_database.py --list --detailed
