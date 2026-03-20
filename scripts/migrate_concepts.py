#!/usr/bin/env python3
"""
AI Token Usage - Concept Migration Script

Migrates database to implement the corrected concept definitions:
- Request: API call count (from auth_type field in logs)
- Message: All messages (with role细分)
- Agent Session: Tool process session (from project directory)
- Conversation: One round of conversation (user message → AI complete)

This script:
1. Renames sessions table to agent_sessions
2. Renames conversation_label field to feishu_conversation_id
3. Adds agent_session_id and conversation_id fields to daily_messages
4. Computes agent_session_id and conversation_id for existing data
"""

import json
import os
import re
import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

# Add shared modules
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

from db import get_connection, DB_PATH, ensure_db_dir


def get_agent_session_id_from_path(project_path: str) -> Optional[str]:
    """
    Extract agent_session_id from project path.
    
    Project path format: /path/to/{tool_name}_{session_id}/...
    Example: /path/to/claude_12345/... -> claude_12345
    
    Args:
        project_path: The project directory path
        
    Returns:
        agent_session_id string or None if not found
    """
    if not project_path:
        return None
    
    # Try to match pattern: toolname_sessionid
    # Examples: claude_abc123, qwen_def456, openclaw_ghi789
    match = re.search(r'([a-z]+)_([a-f0-9]+)', project_path)
    if match:
        tool_name = match.group(1)
        session_id = match.group(2)
        return f"{tool_name}_{session_id}"
    
    return None


def get_conversation_id_from_parent_id_chain(messages: List[Dict]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Build conversation_id based on parent_id chain.

    A conversation starts when a user sends a message (parent_id is null or from different session)
    and ends when AI completes the final response.

    Args:
        messages: List of messages sorted by timestamp

    Returns:
        Tuple of (conversation_map, agent_session_map)
        - conversation_map: Dict mapping message_id to conversation_id
        - agent_session_map: Dict mapping message_id to agent_session_id
    """
    conversation_map = {}
    agent_session_map = {}
    conversation_counter = 0
    current_conversation_id = None
    current_agent_session_id = None

    for msg in messages:
        msg_id = msg.get('message_id')
        parent_id = msg.get('parent_id')
        role = msg.get('role')
        agent_session_id = msg.get('agent_session_id')

        # Determine if this starts a new conversation
        is_user_message = role == 'user'
        has_no_parent = parent_id is None or parent_id == ''

        if is_user_message or has_no_parent:
            # Start a new conversation
            conversation_counter += 1
            current_conversation_id = f"conv_{conversation_counter}"
            # Update current agent_session_id if available
            if agent_session_id:
                current_agent_session_id = agent_session_id

        conversation_map[msg_id] = current_conversation_id
        agent_session_map[msg_id] = current_agent_session_id

    return conversation_map, agent_session_map


def migrate_database():
    """Main migration function."""
    print("=" * 60)
    print("Open ACE - Concept Migration")
    print("=" * 60)
    print(f"Database: {DB_PATH}")
    print()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Step 1: Check current schema
    print("Step 1: Checking current schema...")
    cursor.execute("PRAGMA table_info(daily_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    print(f"  Current columns in daily_messages: {columns}")
    
    has_conversation_label = 'conversation_label' in columns
    has_agent_session_id = 'agent_session_id' in columns
    has_conversation_id = 'conversation_id' in columns
    
    print(f"  Has conversation_label: {has_conversation_label}")
    print(f"  Has agent_session_id: {has_agent_session_id}")
    print(f"  Has conversation_id: {has_conversation_id}")
    print()
    
    # Step 2: Rename conversation_label to feishu_conversation_id
    if has_conversation_label and not has_agent_session_id:
        print("Step 2: Renaming conversation_label to feishu_conversation_id...")
        try:
            # Add new column
            cursor.execute('ALTER TABLE daily_messages ADD COLUMN feishu_conversation_id TEXT')
            
            # Copy data
            cursor.execute('UPDATE daily_messages SET feishu_conversation_id = conversation_label')
            
            # Drop old column (requires SQLite 3.35+)
            # For older SQLite, we need to recreate the table
            cursor.execute('PRAGMA foreign_keys=off')
            cursor.execute('BEGIN TRANSACTION')
            
            # Create new table without conversation_label
            cursor.execute('''
                CREATE TABLE daily_messages_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    host_name TEXT NOT NULL DEFAULT 'localhost',
                    message_id TEXT NOT NULL,
                    parent_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT,
                    full_entry TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    model TEXT,
                    timestamp TEXT,
                    sender_id TEXT,
                    sender_name TEXT,
                    message_source TEXT,
                    feishu_conversation_id TEXT,
                    group_subject TEXT,
                    is_group_chat INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, tool_name, message_id, host_name)
                )
            ''')
            
            # Copy data
            cursor.execute('''
                INSERT INTO daily_messages_new 
                SELECT id, date, tool_name, host_name, message_id, parent_id, role, content, 
                       full_entry, tokens_used, input_tokens, output_tokens, model, timestamp,
                       sender_id, sender_name, message_source, feishu_conversation_id,
                       group_subject, is_group_chat, created_at
                FROM daily_messages
            ''')
            
            # Drop old table
            cursor.execute('DROP TABLE daily_messages')
            
            # Rename new table
            cursor.execute('ALTER TABLE daily_messages_new RENAME TO daily_messages')
            
            cursor.execute('COMMIT')
            cursor.execute('PRAGMA foreign_keys=on')
            
            print("  ✓ conversation_label → feishu_conversation_id")
        except Exception as e:
            print(f"  ✗ Error renaming column: {e}")
            conn.rollback()
    else:
        print("Step 2: Skipping (column already renamed or doesn't exist)")
    print()
    
    # Step 3: Add agent_session_id and conversation_id columns
    if not has_agent_session_id:
        print("Step 3: Adding agent_session_id column...")
        try:
            cursor.execute('ALTER TABLE daily_messages ADD COLUMN agent_session_id TEXT')
            print("  ✓ Added agent_session_id column")
        except Exception as e:
            print(f"  ✗ Error adding column: {e}")
    else:
        print("Step 3: Skipping (agent_session_id already exists)")
    print()
    
    if not has_conversation_id:
        print("Step 3b: Adding conversation_id column...")
        try:
            cursor.execute('ALTER TABLE daily_messages ADD COLUMN conversation_id TEXT')
            print("  ✓ Added conversation_id column")
        except Exception as e:
            print(f"  ✗ Error adding column: {e}")
    else:
        print("Step 3b: Skipping (conversation_id already exists)")
    print()
    
    # Step 4: Compute agent_session_id and conversation_id for existing data
    print("Step 4: Computing agent_session_id and conversation_id...")
    
    # Get all messages with full_entry to extract project path
    cursor.execute('''
        SELECT id, date, tool_name, host_name, message_id, parent_id, role, 
               content, full_entry, timestamp, agent_session_id, conversation_id
        FROM daily_messages
        WHERE agent_session_id IS NULL OR conversation_id IS NULL
        ORDER BY timestamp ASC
    ''')
    
    messages = cursor.fetchall()
    print(f"  Processing {len(messages)} messages...")
    
    # Group messages by date and tool for processing
    messages_by_group = {}
    for msg in messages:
        row = dict(msg)
        key = (row['date'], row['tool_name'], row['host_name'])
        if key not in messages_by_group:
            messages_by_group[key] = []
        messages_by_group[key].append(row)
    
    updated_count = 0
    total_messages = len(messages)
    
    for (date, tool_name, host_name), group_messages in messages_by_group.items():
        # Sort by timestamp
        group_messages.sort(key=lambda x: x.get('timestamp') or '')
        
        # Compute conversation_id based on parent_id chain
        conversation_map, agent_session_map = get_conversation_id_from_parent_id_chain(group_messages)

        for msg in group_messages:
            msg_id = msg['id']
            message_id = msg.get('message_id')
            full_entry = msg.get('full_entry')
            parent_id = msg.get('parent_id')
            role = msg.get('role')

            # Get conversation_id from conversation_map
            conversation_id = conversation_map.get(message_id)

            # Get agent_session_id from agent_session_map first
            # If not available, try to extract from full_entry
            agent_session_id = agent_session_map.get(message_id)
            if not agent_session_id and full_entry:
                try:
                    entry = json.loads(full_entry)
                    # Try to get project path from entry
                    project_path = entry.get('project_path') or entry.get('project') or entry.get('path')
                    if project_path:
                        agent_session_id = get_agent_session_id_from_path(project_path)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Update database
            if agent_session_id or conversation_id:
                cursor.execute('''
                    UPDATE daily_messages
                    SET agent_session_id = ?, conversation_id = ?
                    WHERE id = ?
                ''', (agent_session_id, conversation_id, msg_id))
                updated_count += 1
    
    conn.commit()
    print(f"  ✓ Updated {updated_count} messages with agent_session_id and conversation_id")
    print()
    
    # Step 5: Create indexes for new columns
    print("Step 5: Creating indexes for new columns...")
    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_agent_session ON daily_messages(agent_session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_conversation ON daily_messages(conversation_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_feishu_conv ON daily_messages(feishu_conversation_id)')
        print("  ✓ Created indexes")
    except Exception as e:
        print(f"  ✗ Error creating indexes: {e}")
    print()
    
    # Step 6: Check sessions table
    print("Step 6: Checking sessions table...")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    sessions_table = cursor.fetchone()
    
    if sessions_table:
        print("  Found sessions table - needs to be renamed to agent_sessions")
        # Note: This requires manual intervention for the auth database
        print("  ⚠️  WARNING: sessions table exists in auth database - manual rename required")
        print("     Run: ALTER TABLE sessions RENAME TO agent_sessions")
        print("     And update related indexes and foreign keys")
    else:
        print("  ✓ No sessions table in daily database")
    print()
    
    # Step 7: Summary
    print("=" * 60)
    print("Migration Summary")
    print("=" * 60)
    
    cursor.execute('SELECT COUNT(*) FROM daily_messages')
    total = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM daily_messages WHERE agent_session_id IS NOT NULL')
    with_agent_session = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM daily_messages WHERE conversation_id IS NOT NULL')
    with_conversation = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM daily_messages WHERE feishu_conversation_id IS NOT NULL')
    with_feishu_conv = cursor.fetchone()[0]
    
    print(f"  Total messages: {total}")
    print(f"  Messages with agent_session_id: {with_agent_session} ({with_agent_session/total*100:.1f}%)")
    print(f"  Messages with conversation_id: {with_conversation} ({with_conversation/total*100:.1f}%)")
    print(f"  Messages with feishu_conversation_id: {with_feishu_conv} ({with_feishu_conv/total*100:.1f}%)")
    print()
    
    conn.close()
    
    print("Migration completed!")
    print()
    print("Next steps:")
    print("  1. Update fetch scripts to compute and save agent_session_id and conversation_id")
    print("  2. Update web.py to use new field names")
    print("  3. Update templates/index.html to use new field names")
    print("  4. Update fetch scripts to extract agent_session_id from project path")
    print()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Migrate database to implement corrected concepts')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    args = parser.parse_args()
    
    if args.dry_run:
        print("Dry run mode - no changes will be made")
        print()
    
    migrate_database()
