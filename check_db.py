#!/usr/bin/env python3
import sqlite3
import sys

def check_database(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if database exists and has tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        print("Tables in database:")
        for table in tables:
            print(f"  {table[0]}")
        
        # Check device_stats table schema if it exists
        if any('device_stats' in table for table in tables):
            cursor.execute("PRAGMA table_info(device_stats)")
            columns = cursor.fetchall()
            print("\ndevice_stats table columns:")
            for col in columns:
                print(f"  {col[1]} ({col[2]})")
        else:
            print("\ndevice_stats table does not exist")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error checking database: {e}")
        return False

if __name__ == "__main__":
    db_path = "/opt/OSTG/device_database.db"
    check_database(db_path)
