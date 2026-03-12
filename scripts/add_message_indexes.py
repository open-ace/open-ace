#!/usr/bin/env python3
"""
Add indexes to daily_messages table to improve query performance.

This script addresses Issue #20: Messages page loading slowly.
Run this script once to add indexes to the existing database.

Usage:
    python3 scripts/add_message_indexes.py
"""

import os
import sys

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

import db

def add_message_indexes():
    """Add indexes to daily_messages table for better query performance."""
    print("Adding indexes to daily_messages table...")
    
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Indexes to create for Issue #20
    indexes_to_create = [
        ('idx_messages_date', 'daily_messages', 'date'),
        ('idx_messages_tool_name', 'daily_messages', 'tool_name'),
        ('idx_messages_host_name', 'daily_messages', 'host_name'),
        ('idx_messages_sender_name', 'daily_messages', 'sender_name'),
        ('idx_messages_sender_id', 'daily_messages', 'sender_id'),
        ('idx_messages_timestamp', 'daily_messages', 'timestamp'),
        ('idx_messages_role', 'daily_messages', 'role'),
        # Composite indexes for common query patterns
        ('idx_messages_date_tool', 'daily_messages', 'date, tool_name'),
        ('idx_messages_date_host', 'daily_messages', 'date, host_name'),
        ('idx_messages_date_sender', 'daily_messages', 'date, sender_name'),
        ('idx_messages_date_tool_host', 'daily_messages', 'date, tool_name, host_name'),
        # Indexes for ORDER BY optimization (Issue #20)
        ('idx_messages_date_timestamp', 'daily_messages', 'date, timestamp DESC'),
        ('idx_messages_date_role_timestamp', 'daily_messages', 'date, role, timestamp DESC'),
    ]
    
    created_count = 0
    skipped_count = 0
    
    for index_name, table_name, columns in indexes_to_create:
        try:
            # Check if index already exists (PRAGMA doesn't support parameterized queries)
            cursor.execute(f"PRAGMA index_list({table_name})")
            existing_indexes = [row[1] for row in cursor.fetchall()]
            
            if index_name in existing_indexes:
                print(f"  ✓ Index {index_name} already exists, skipping")
                skipped_count += 1
                continue
            
            # Create the index
            cursor.execute(f'CREATE INDEX {index_name} ON {table_name} ({columns})')
            print(f"  ✓ Created index: {index_name} on {table_name}({columns})")
            created_count += 1
            
        except Exception as e:
            print(f"  ✗ Error creating index {index_name}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\nDone! Created {created_count} indexes, skipped {skipped_count} existing indexes.")
    print("Messages page queries should now be significantly faster!")

if __name__ == '__main__':
    add_message_indexes()
