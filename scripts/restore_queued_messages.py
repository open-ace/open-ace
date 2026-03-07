#!/usr/bin/env python3
"""
AI Token Usage - Restore Queued Messages

Restores the original message content from full_entry for queued messages.
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


def extract_queued_message_content(full_entry: str) -> str:
    """Extract actual message content from queued message full_entry."""
    try:
        entry = json.loads(full_entry)
        
        # Navigate to the message content
        message = entry.get('message', {})
        content_list = message.get('content', [])
        
        if content_list and len(content_list) > 0:
            text_content = content_list[0].get('text', '')
            
            # Look for body in Chat history
            body_match = re.search(r'"body":\s*"([^"]+)"', text_content)
            if body_match:
                body_content = body_match.group(1)
                # Decode unicode escapes
                try:
                    body_content = body_content.encode().decode('unicode_escape')
                except:
                    pass
                # Remove sender prefix
                prefix_match = re.match(r'^(ou_[a-f0-9]+|U[A-Z0-9]+):\s*', body_content)
                if prefix_match:
                    return body_content[prefix_match.end():]
                return body_content
            
            # If no body found, return the last line (actual message)
            lines = text_content.split('\n')
            for line in reversed(lines):
                stripped = line.strip()
                if stripped and not stripped.startswith('[') and not stripped.startswith('{') and not stripped.startswith('Queued') and not stripped.startswith('---'):
                    # Remove sender prefix if present
                    prefix_match = re.match(r'^(ou_[a-f0-9]+|U[A-Z0-9]+):\s*(.+)$', stripped)
                    if prefix_match:
                        return prefix_match.group(2)
                    return stripped
        
        return ""
    except Exception as e:
        print(f"Error extracting content: {e}")
        return ""


def restore_queued_messages():
    """Restore content for queued messages."""
    print("Restoring queued messages...")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get queued messages
    cursor.execute('''
        SELECT id, full_entry 
        FROM daily_messages
        WHERE content LIKE '%Queued messages while agent was busy%'
          AND full_entry IS NOT NULL
    ''')
    
    messages = cursor.fetchall()
    print(f"Found {len(messages)} queued messages")
    
    updated = 0
    for msg_id, full_entry in messages:
        content = extract_queued_message_content(full_entry)
        if content:
            cursor.execute('''
                UPDATE daily_messages 
                SET content = ? 
                WHERE id = ?
            ''', (content, msg_id))
            updated += 1
            print(f"  Updated message {msg_id}: {content[:60]}...")
    
    conn.commit()
    conn.close()
    
    print(f"\nRestored {updated} queued messages")


if __name__ == '__main__':
    restore_queued_messages()
