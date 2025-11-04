#!/bin/bash
# migrate_server_database.sh
# Script to migrate the database on the remote server to fix the missing column error

HOST="svl-hp-ai-srv04"
USER="root"
PASSWORD="Embe1mpls"

echo "🔧 Migrating database on $HOST to fix missing column error..."

# Create SSH command with password
ssh_command() {
    /opt/homebrew/bin/sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$USER@$HOST" "$@"
}

# Test SSH connection
echo "🔍 Testing SSH connection..."
ssh_command "echo 'SSH connection successful'"

if [ $? -ne 0 ]; then
    echo "❌ SSH connection failed. Please check credentials and server availability."
    exit 1
fi

# Check current database schema
echo ""
echo "📋 Checking current database schema..."
ssh_command "python3 -c \"
import sqlite3
import os
db_path = '/opt/OSTG/device_database.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('PRAGMA table_info(device_stats)')
    columns = cursor.fetchall()
    print('Current device_stats columns:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
    conn.close()
else:
    print('Database file not found at:', db_path)
\""

# Copy migration script to server
echo ""
echo "📤 Copying migration script to server..."
/opt/homebrew/bin/sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null migrate_database.py "$USER@$HOST:/tmp/"

# Run migration script on server
echo ""
echo "🚀 Running database migration on server..."
ssh_command "cd /tmp && python3 migrate_database.py /opt/OSTG/device_database.db"

# Verify the migration
echo ""
echo "✅ Verifying migration results..."
ssh_command "python3 -c \"
import sqlite3
import os
db_path = '/opt/OSTG/device_database.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('PRAGMA table_info(device_stats)')
    columns = cursor.fetchall()
    print('Updated device_stats columns:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
    conn.close()
else:
    print('Database file not found at:', db_path)
\""

# Clean up migration script
echo ""
echo "🧹 Cleaning up migration script..."
ssh_command "rm -f /tmp/migrate_database.py"

echo ""
echo "✅ Database migration completed!"
echo ""
echo "The missing 'last_bgp_check' column should now be available."
echo "The OSTG server should no longer show the column error."
