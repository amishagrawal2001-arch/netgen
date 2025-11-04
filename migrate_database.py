#!/usr/bin/env python3
"""
Database migration script to add missing columns to device_stats table.
This fixes the error: "table device_stats has no column named last_bgp_check"
"""

import sqlite3
import sys
import os
from datetime import datetime

def migrate_database(db_path):
    """Migrate the database to add missing columns."""
    
    if not os.path.exists(db_path):
        print(f"❌ Database file not found: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print(f"🔍 Checking database schema at: {db_path}")
        
        # Get current table schema
        cursor.execute("PRAGMA table_info(device_stats)")
        columns = cursor.fetchall()
        existing_columns = [col[1] for col in columns]
        
        print(f"📋 Current device_stats columns: {existing_columns}")
        
        # Define missing columns that need to be added
        missing_columns = [
            ('last_bgp_check', 'TIMESTAMP'),
            ('last_ospf_check', 'TIMESTAMP'),
            ('last_ping_check', 'TIMESTAMP'),
            ('ospf_ipv4_running', 'BOOLEAN'),
            ('ospf_ipv6_running', 'BOOLEAN'),
            ('ospf_ipv4_established', 'BOOLEAN'),
            ('ospf_ipv6_established', 'BOOLEAN'),
            ('ospf_ipv4_uptime', 'TEXT'),
            ('ospf_ipv6_uptime', 'TEXT'),
            ('isis_running', 'BOOLEAN'),
            ('isis_established', 'BOOLEAN'),
            ('isis_state', 'TEXT'),
            ('isis_neighbors', 'TEXT'),
            ('isis_areas', 'TEXT'),
            ('isis_system_id', 'TEXT'),
            ('isis_net', 'TEXT'),
            ('isis_uptime', 'TEXT'),
            ('last_isis_check', 'TIMESTAMP')
        ]
        
        # Add missing columns
        columns_added = []
        for col_name, col_type in missing_columns:
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE device_stats ADD COLUMN {col_name} {col_type}")
                    columns_added.append(col_name)
                    print(f"✅ Added column: {col_name} ({col_type})")
                except sqlite3.Error as e:
                    print(f"⚠️  Could not add column {col_name}: {e}")
        
        # Commit changes
        conn.commit()
        
        # Verify the changes
        cursor.execute("PRAGMA table_info(device_stats)")
        updated_columns = cursor.fetchall()
        updated_column_names = [col[1] for col in updated_columns]
        
        print(f"\n📋 Updated device_stats columns: {updated_column_names}")
        
        if columns_added:
            print(f"\n✅ Successfully added {len(columns_added)} columns: {columns_added}")
        else:
            print("\n✅ No columns needed to be added - database is up to date")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error migrating database: {e}")
        return False

def main():
    """Main function to handle command line arguments."""
    
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        # Default database paths to check
        default_paths = [
            "/opt/OSTG/device_database.db",
            "./device_database.db",
            "device_database.db"
        ]
        
        db_path = None
        for path in default_paths:
            if os.path.exists(path):
                db_path = path
                break
        
        if not db_path:
            print("❌ No database file found. Please specify the database path:")
            print("Usage: python3 migrate_database.py <database_path>")
            print("\nDefault locations checked:")
            for path in default_paths:
                print(f"  - {path}")
            return 1
    
    print(f"🚀 Starting database migration for: {db_path}")
    print(f"⏰ Migration started at: {datetime.now()}")
    
    success = migrate_database(db_path)
    
    if success:
        print(f"\n✅ Database migration completed successfully!")
        return 0
    else:
        print(f"\n❌ Database migration failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())

