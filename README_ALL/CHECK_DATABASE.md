# How to Check Stream Database

## Option 1: Run Script on Server (Recommended)

The database is located at `/opt/OSTG/device_database.db` on the server.

1. **Copy the script to the server:**
   ```bash
   scp check_stream_database.py root@svl-hp-ai-srv04:/opt/OSTG/
   ```

2. **SSH to the server and run:**
   ```bash
   ssh root@svl-hp-ai-srv04
   cd /opt/OSTG
   python3 check_stream_database.py
   ```

This will show:
- All stream records
- TX/RX counts
- TX/RX rates (stored in database)
- `last_tx_count` and `last_rx_count` (used for rate calculation)
- `last_update` timestamp (used for time delta calculation)
- Expected rates based on count deltas

## Option 2: Query via SQLite directly on server

```bash
ssh root@svl-hp-ai-srv04
sqlite3 /opt/OSTG/device_database.db "SELECT stream_id, stream_name, tx_count, rx_count, tx_rate, rx_rate, last_tx_count, last_rx_count, last_update, updated_at FROM streams WHERE status='Running';"
```

## Option 3: Check Server Logs

The server logs should show rate calculation details if logging is enabled:
```bash
ssh root@svl-hp-ai-srv04
journalctl -u ostg-server -f | grep "STREAM DB"
```

Look for messages like:
- `[STREAM DB] Updating stats for {stream_id}: tx_count=...`
- `[STREAM DB] ✅ Calculated tx_rate for {stream_id}: ...`

## What to Look For

When checking the database, verify:

1. **`last_tx_count` and `last_rx_count`**:
   - Should be set to the previous count values
   - Should be different from current `tx_count`/`rx_count` if stream is active

2. **`last_update`**:
   - Should be set (not NULL)
   - Should be different from `updated_at` (time difference should be ~2 seconds for polling)

3. **`tx_rate` and `rx_rate`**:
   - Should be calculated as: `(current_count - last_count) / time_diff`
   - Should be > 0 if stream is actively sending/receiving packets

4. **Time difference**:
   - Should be >= 1.0 seconds for accurate rate calculation
   - If < 1.0 seconds, rates will be preserved from previous update

## Expected Values for Running Stream

For a stream sending at ~1000 pps:
- `tx_count`: Increasing (e.g., 1900)
- `last_tx_count`: Previous count (e.g., 1560)
- `last_update`: Timestamp from last update (e.g., 2 seconds ago)
- `tx_rate`: Should be ~170 pps (if 340 packets in 2 seconds)
- `updated_at`: Current timestamp

If `tx_rate` is 0.00 but counts are changing, check:
- Is `last_tx_count` equal to `tx_count`? (shouldn't be)
- Is `last_update` NULL? (shouldn't be)
- Is time difference < 1.0 seconds? (might preserve old rate)


