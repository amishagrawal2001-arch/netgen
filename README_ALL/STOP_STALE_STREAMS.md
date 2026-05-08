# How to Stop Stale/Running Streams on Server

This guide explains how to identify and stop stale or running streams on the OSTG server.

## Method 1: Using the `stop_stale_streams.py` Script (Recommended)

A dedicated script has been created to help manage stale streams.

### List All Running Streams

```bash
python3 stop_stale_streams.py --list
```

This will show all currently running streams with their details (ID, name, interface, TX/RX counts, etc.).

### Stop All Running Streams

```bash
python3 stop_stale_streams.py --stop-all
```

This will stop **all** running streams on the server.

### Stop Specific Stream by ID

```bash
python3 stop_stale_streams.py --stop <stream_id>
```

Example:
```bash
python3 stop_stale_streams.py --stop be58b7a9-c087-4dfa-b785-fd197abbc1f5
```

### Stop Streams on Specific Interface

```bash
python3 stop_stale_streams.py --stop-interface <interface_name>
```

Example:
```bash
python3 stop_stale_streams.py --stop-interface ens5np0
```

### Stop Streams on Specific TG ID

```bash
python3 stop_stale_streams.py --stop-tg <tg_id>
```

Example:
```bash
python3 stop_stale_streams.py --stop-tg 0
```

### Use Different Server

```bash
python3 stop_stale_streams.py --list --server http://other-server:5051
```

## Method 2: Using `query_device_database.py`

You can also use the existing query script to see running streams:

```bash
# List all streams (including running ones)
python3 query_device_database.py --streams --stream-status all

# List only running streams
python3 query_device_database.py --streams --stream-status Running
```

However, this script doesn't have a built-in stop function, so you'll need to use Method 1 or Method 3.

## Method 3: Direct API Calls

You can also use `curl` to directly call the server API:

### List Running Streams

```bash
curl -s "http://svl-hp-ai-srv04:5051/api/streams/stats?status=Running" | python3 -m json.tool
```

### Stop a Specific Stream

```bash
curl -X POST "http://svl-hp-ai-srv04:5051/api/traffic/stop" \
  -H "Content-Type: application/json" \
  -d '{
    "streams": [
      {
        "interface": "ens5np0",
        "stream_id": "be58b7a9-c087-4dfa-b785-fd197abbc1f5"
      }
    ]
  }'
```

### Stop Multiple Streams

```bash
curl -X POST "http://svl-hp-ai-srv04:5051/api/traffic/stop" \
  -H "Content-Type: application/json" \
  -d '{
    "streams": [
      {
        "interface": "ens5np0",
        "stream_id": "stream-id-1"
      },
      {
        "interface": "ens5np0",
        "stream_id": "stream-id-2"
      }
    ]
  }'
```

## Method 4: Using Python Interactively

You can also use Python directly:

```python
import requests

SERVER_URL = "http://svl-hp-ai-srv04:5051"

# Get all running streams
response = requests.get(f"{SERVER_URL}/api/streams/stats", params={"status": "Running"})
streams = response.json().get("active_streams", [])

# Stop all running streams
for stream in streams:
    stream_id = stream.get("stream_id")
    interface = stream.get("interface")
    
    stop_payload = {
        "streams": [{
            "interface": interface,
            "stream_id": stream_id
        }]
    }
    
    stop_response = requests.post(f"{SERVER_URL}/api/traffic/stop", json=stop_payload)
    print(f"Stopped {stream.get('stream_name')}: {stop_response.status_code}")
```

## Troubleshooting

### Stream Not Found Error

If you get "Stream ID 'xxx' not found on interface 'yyy'", it means:
- The stream might have already stopped
- The interface name might be incorrect
- The stream_id might be incorrect

**Solution**: Use `--list` first to see the exact stream_id and interface name.

### Server Not Responding

If the server is not responding:
- Check if the server is running: `ssh root@svl-hp-ai-srv04 'systemctl status ostg-server'`
- Check server logs: `ssh root@svl-hp-ai-srv04 'journalctl -u ostg-server -n 50'`
- Verify the server URL is correct

### Stream Still Running After Stop

If a stream appears to still be running after stopping:
1. Wait a few seconds (stop is asynchronous)
2. Check again with `--list`
3. If still running, the stream might be stuck - restart the server service:
   ```bash
   ssh root@svl-hp-ai-srv04 'systemctl restart ostg-server'
   ```

## Best Practices

1. **Always list first**: Use `--list` to see what's running before stopping
2. **Stop selectively**: Use `--stop-interface` or `--stop-tg` to stop specific streams
3. **Verify**: After stopping, use `--list` again to confirm streams are stopped
4. **Check database**: Use `query_device_database.py --streams --stream-status all` to see database status

## Example Workflow

```bash
# 1. List all running streams
python3 stop_stale_streams.py --list

# 2. Stop all streams on a specific interface
python3 stop_stale_streams.py --stop-interface ens5np0

# 3. Verify they're stopped
python3 stop_stale_streams.py --list

# 4. Check database status
python3 query_device_database.py --streams --stream-status all
```


