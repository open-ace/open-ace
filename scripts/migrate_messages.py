#!/usr/bin/env python3
"""
AI Token Usage - Migrate Existing Messages

Updates existing messages in the database to properly set:
- message_source (slack/feishu/openclaw)
- sender_id
- sender_name
"""

import json
import os
import re
import sys
from pathlib import Path

# Add shared modules
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

from db import get_connection
import feishu_user_cache


def detect_message_source(content: str) -> tuple:
    """
    Detect message source and extract sender info from content.
    
    Returns:
        tuple: (message_source, sender_id, sender_name)
    """
    sender_id = None
    sender_name = None
    message_source = "openclaw"
    
    # Check for Slack messages
    if "Slack message in #" in content or "Slack DM from" in content:
        message_source = "slack"
        # Extract sender name from "Slack message in #channel from Name: content"
        match = re.search(r'Slack (?:message in [^#]+#?\w+|DM) from ([^:]+):', content)
        if match:
            sender_name = match.group(1).strip()
    
    # Check for Feishu messages
    # Method 1: Look for conversation_label in JSON metadata
    if '"conversation_label"' in content or "'conversation_label'" in content:
        message_source = "feishu"
        # Try to extract sender_id from JSON
        json_match = re.search(r'\{[^{}]*"sender_id"[^{}]*\}', content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                sender_id = data.get('sender_id')
            except:
                pass
    
    # Method 2: Look for sender_id with ou_ prefix
    ou_match = re.search(r'"sender_id":\s*"(ou_[a-f0-9]+)"', content)
    if ou_match:
        message_source = "feishu"
        sender_id = ou_match.group(1)
    
    # If sender_id found but no name, try to get from Feishu API
    if message_source == "feishu" and sender_id and not sender_name:
        # Try cache first
        cached_name = feishu_user_cache.get_user_name_from_cache(sender_id)
        if cached_name:
            sender_name = cached_name
        else:
            # Try API
            config_path = Path.home() / ".open-ace" / "config.json"
            if config_path.exists():
                with open(config_path) as f:
                    config = json.load(f)
                feishu_config = config.get('feishu', {})
                app_id = feishu_config.get('app_id')
                app_secret = feishu_config.get('app_secret')
                
                if app_id and app_secret:
                    api_name = feishu_user_cache.get_user_name(sender_id, app_id, app_secret)
                    if api_name:
                        sender_name = api_name
    
    return message_source, sender_id, sender_name


def migrate_messages(days: int = 30):
    """Migrate messages from the last N days."""
    print(f"Migrating messages from the last {days} days...")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get messages that need migration (no message_source set)
    # Only process openclaw tool messages
    cursor.execute('''
        SELECT id, content, sender_id, sender_name
        FROM daily_messages
        WHERE tool_name = 'openclaw'
          AND (message_source IS NULL OR message_source = '')
        ORDER BY id DESC
        LIMIT 10000
    ''')
    
    messages = cursor.fetchall()
    print(f"Found {len(messages)} messages to migrate")
    
    updated = 0
    feishu_count = 0
    slack_count = 0
    
    for msg_id, content, existing_sender_id, existing_sender_name in messages:
        # Skip if already has sender info
        if existing_sender_id and existing_sender_name:
            continue
        
        message_source, sender_id, sender_name = detect_message_source(content)
        
        # Only update if we found something new
        if message_source != 'openclaw' or sender_id:
            cursor.execute('''
                UPDATE daily_messages
                SET message_source = ?, sender_id = ?, sender_name = ?
                WHERE id = ?
            ''', (message_source, sender_id, sender_name, msg_id))
            updated += 1
            
            if message_source == 'feishu':
                feishu_count += 1
            elif message_source == 'slack':
                slack_count += 1
    
    conn.commit()
    conn.close()
    
    print(f"\nMigration completed:")
    print(f"  Updated: {updated} messages")
    print(f"  Feishu messages: {feishu_count}")
    print(f"  Slack messages: {slack_count}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Migrate existing messages to add source and sender info')
    parser.add_argument('--days', type=int, default=30, help='Number of days to migrate (default: 30)')
    args = parser.parse_args()
    
    migrate_messages(days=args.days)
