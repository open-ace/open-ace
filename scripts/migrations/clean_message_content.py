#!/usr/bin/env python3
"""
AI Token Usage - Clean Message Content

Cleans existing message content by removing metadata blocks.
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


def clean_content(content: str) -> str:
    """Clean message content by removing metadata blocks."""
    if not content:
        return content
    
    # Check for Queued messages with Chat history
    if '[Queued messages while agent was busy]' in content:
        body_match = re.search(r'"body":\s*"([^"]+)"', content)
        if body_match:
            body_content = body_match.group(1)
            try:
                body_content = body_content.encode().decode('unicode_escape')
            except:
                pass
            prefix_match = re.match(r'^(ou_[a-f0-9]+|U[A-Z0-9]+):\s*', body_content)
            if prefix_match:
                return body_content[prefix_match.end():]
            return body_content
    
    # Handle "System: [...] Feishu[default] message in group XXX: ACTUAL_CONTENT" format
    system_match = re.match(r'^System:\s*\[[^\]]+\]\s*Feishu\[[^\]]+\]\s*message\s+in\s+group\s+\w+:\s*(.+)$', content, re.DOTALL)
    if system_match:
        return system_match.group(1).strip()
    
    # Remove ```json``` and ``` ``` code blocks
    content = re.sub(r'```json\s*\n?\s*```', '', content)
    content = re.sub(r'```\s*\n?\s*```', '', content)
    
    # If content starts with JSON metadata, find actual content
    if content.strip().startswith('"message_id"'):
        lines = content.split('\n')
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('"') and not stripped.startswith('{') and not stripped.startswith('}'):
                if stripped.startswith('[Replying to:'):
                    continue
                if stripped.endswith('"]') or stripped.endswith('"}'):
                    continue
                return stripped
        return ""
    
    # Remove JSON metadata lines
    lines = content.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        if re.match(r'^"(message_id|sender_id|reply_to_id|conversation_label|sender|timestamp|group_subject|is_group_chat|was_mentioned|has_reply_context|label|id|name)"\s*:', stripped):
            continue
        if stripped.startswith('{') or stripped.startswith('}'):
            continue
        if stripped.startswith('Conversation info'):
            continue
        if stripped.startswith('Sender (untrusted'):
            continue
        if stripped.startswith('[Replying to:'):
            continue
        if stripped.startswith('[message_id:'):
            continue
        if stripped.startswith('[Feishu') or stripped.startswith('[Slack'):
            continue
        if stripped.startswith('[Queued messages'):
            continue
        if stripped.startswith('---'):
            continue
        if stripped.startswith('Queued #'):
            continue
        if stripped.endswith('"]') or stripped.endswith('"}'):
            continue
        
        # Remove "Sender: " prefix
        sender_match = re.match(r'^[\u4e00-\u9fa5a-zA-Z]+:\s*(.+)$', stripped)
        if sender_match:
            stripped = sender_match.group(1)
        
        if stripped:
            cleaned_lines.append(stripped)
    
    result = '\n'.join(cleaned_lines).strip()
    return result if result else content


def clean_messages():
    """Clean message content in database."""
    print("Cleaning message content...")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get messages with metadata
    cursor.execute('''
        SELECT id, content FROM daily_messages 
        WHERE content LIKE '%"message_id":%' 
           OR content LIKE '%Conversation info%'
           OR content LIKE '%```%'
        ORDER BY id DESC
        LIMIT 1000
    ''')
    
    messages = cursor.fetchall()
    print(f"Found {len(messages)} messages to clean")
    
    updated = 0
    for msg_id, content in messages:
        cleaned = clean_content(content)
        if cleaned and cleaned != content:
            cursor.execute('''
                UPDATE daily_messages 
                SET content = ? 
                WHERE id = ?
            ''', (cleaned, msg_id))
            updated += 1
            print(f"  Cleaned message {msg_id}: {cleaned[:60]}...")
    
    conn.commit()
    conn.close()
    
    print(f"\nUpdated {updated} messages")


if __name__ == '__main__':
    clean_messages()
