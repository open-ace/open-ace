#!/usr/bin/env python3
"""
Populate project_path for existing sessions in daily_messages table.

Scans qwen-code and claude project directories to find session files,
then updates the database with the corresponding project_path.
"""

import os
import sys
from pathlib import Path

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, "shared")
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

from db import get_connection, is_postgresql, _execute

def find_session_in_projects(session_id: str, tool_name: str) -> str:
    """
    Find which project directory contains the session file.
    
    Returns the encoded project name or None if not found.
    """
    if tool_name == "qwen":
        base_dir = Path.home() / ".qwen" / "projects"
        if not base_dir.exists():
            return None
            
        for subdir in base_dir.iterdir():
            if not subdir.is_dir() or subdir.name.startswith('.'):
                continue
            
            # Check in chats subdirectory
            chats_dir = subdir / "chats"
            if chats_dir.exists():
                session_file = chats_dir / f"{session_id}.jsonl"
                if session_file.exists():
                    return subdir.name
                    
    elif tool_name == "claude":
        base_dir = Path.home() / ".claude" / "projects"
        if not base_dir.exists():
            return None
            
        for subdir in base_dir.iterdir():
            if not subdir.is_dir() or subdir.name.startswith('.'):
                continue
            
            session_file = subdir / f"{session_id}.jsonl"
            if session_file.exists():
                return subdir.name
                
    elif tool_name == "openclaw":
        # For openclaw, use 'main' as default agent_name
        base_dir = Path.home() / ".openclaw" / "agents"
        if base_dir.exists():
            return "main"  # Use first/only agent
    
    return None


def main():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get sessions without project_path
    if is_postgresql():
        cursor.execute("""
            SELECT DISTINCT agent_session_id, tool_name
            FROM daily_messages
            WHERE agent_session_id IS NOT NULL
              AND (project_path IS NULL OR project_path = '')
            LIMIT 100
        """)
    else:
        cursor.execute("""
            SELECT DISTINCT agent_session_id, tool_name
            FROM daily_messages
            WHERE agent_session_id IS NOT NULL
              AND (project_path IS NULL OR project_path = '')
            LIMIT 100
        """)
    
    rows = cursor.fetchall()
    total = len(rows)
    print(f"Found {total} sessions to update")
    
    updated = 0
    not_found = 0
    
    for i, row in enumerate(rows):
        if is_postgresql():
            session_id = row["agent_session_id"]
            tool_name = row["tool_name"]
        else:
            session_id = row[0]
            tool_name = row[1]
        
        # Find project_path
        project_path = find_session_in_projects(session_id, tool_name)
        
        if project_path:
            # Update database
            if is_postgresql():
                _execute(cursor, """
                    UPDATE daily_messages
                    SET project_path = %s
                    WHERE agent_session_id = %s
                """, (project_path, session_id))
            else:
                _execute(cursor, """
                    UPDATE daily_messages
                    SET project_path = ?
                    WHERE agent_session_id = ?
                """, (project_path, session_id))
            conn.commit()
            updated += 1
            print(f"[{i+1}/{total}] Updated {session_id[:8]}... ({tool_name}) -> {project_path}")
        else:
            not_found += 1
            print(f"[{i+1}/{total}] NOT FOUND: {session_id[:8]}... ({tool_name})")
    
    conn.close()
    print(f"\nDone! Updated {updated} sessions, {not_found} not found")


if __name__ == "__main__":
    main()
