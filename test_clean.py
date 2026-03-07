#!/usr/bin/env python3
import sqlite3
import sys
sys.path.insert(0, 'scripts')
from clean_message_content import clean_content

conn = sqlite3.connect('/Users/rhuang/.ai-token-analyzer/usage.db')
c = conn.cursor()

# Check ai-lab messages
c.execute("SELECT id, content FROM daily_messages WHERE host_name='ai-lab' AND content LIKE '%\"message_id\":%'")
messages = c.fetchall()

print(f'Found {len(messages)} ai-lab messages')
for msg_id, content in messages:
    lines = content.split('\n')
    print(f'\n=== Message {msg_id} ({len(lines)} lines) ===')
    # Find lines with actual content
    content_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('"') and not stripped.startswith('{') and not stripped.startswith('}'):
            content_lines.append((i, stripped))
    
    print(f'Content lines: {len(content_lines)}')
    for i, line in content_lines[:5]:
        print(f'  Line {i}: {line[:80]}')
    
    cleaned = clean_content(content)
    print(f'Cleaned: {cleaned[:100] if cleaned else "EMPTY"}')

conn.close()
