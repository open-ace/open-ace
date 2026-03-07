#!/usr/bin/env python3
"""
AI Token Usage - Database Module

Provides database operations for the ai_token_usage project.
"""

import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

# Import shared configuration - support both relative and absolute imports
def _get_config():
    """Get config module, trying relative then absolute imports."""
    try:
        from . import config
        return config
    except ImportError:
        try:
            import config
            return config
        except ImportError:
            # Try adding shared_dir to path
            script_dir = os.path.dirname(os.path.abspath(__file__))
            shared_dir = os.path.dirname(script_dir)
            if shared_dir not in sys.path:
                sys.path.insert(0, shared_dir)
            import config
            return config

config = _get_config()
DB_DIR = config.DB_DIR
DB_PATH = config.DB_PATH


def ensure_db_dir() -> None:
    """Ensure the database directory exists."""
    os.makedirs(DB_DIR, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    """Initialize the database with the required schema."""
    ensure_db_dir()

    conn = get_connection()
    cursor = conn.cursor()

    # Create daily_usage table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            host_name TEXT NOT NULL DEFAULT 'localhost',
            tokens_used INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_tokens INTEGER DEFAULT 0,
            request_count INTEGER DEFAULT 0,
            models_used TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, tool_name, host_name)
        )
    ''')

    # Create daily_messages table first (before checking for full_entry)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_messages (
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, tool_name, message_id, host_name)
        )
    ''')

    # Check if host_name column exists in daily_usage, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_usage)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'host_name' not in columns:
        print("Adding host_name column to existing daily_usage table...")
        cursor.execute("ALTER TABLE daily_usage ADD COLUMN host_name TEXT DEFAULT 'localhost'")
        # Update existing records with 'localhost'
        cursor.execute("UPDATE daily_usage SET host_name = 'localhost' WHERE host_name IS NULL")
        conn.commit()

    # Check if host_name column exists in daily_messages, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'host_name' not in columns:
        print("Adding host_name column to existing daily_messages table...")
        cursor.execute("ALTER TABLE daily_messages ADD COLUMN host_name TEXT DEFAULT 'localhost'")
        # Update existing records with 'localhost'
        cursor.execute("UPDATE daily_messages SET host_name = 'localhost' WHERE host_name IS NULL")
        conn.commit()

    # Check if request_count column exists, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_usage)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'request_count' not in columns:
        print("Adding request_count column to existing database...")
        cursor.execute("ALTER TABLE daily_usage ADD COLUMN request_count INTEGER DEFAULT 0")
        conn.commit()

    # Check if full_entry column exists in daily_messages, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'full_entry' not in columns:
        print("Adding full_entry column to existing database...")
        cursor.execute("ALTER TABLE daily_messages ADD COLUMN full_entry TEXT")
        conn.commit()

    # Check if sender_id column exists in daily_messages, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'sender_id' not in columns:
        print("Adding sender_id column to existing daily_messages table...")
        cursor.execute("ALTER TABLE daily_messages ADD COLUMN sender_id TEXT")
        conn.commit()

    # Check if sender_name column exists in daily_messages, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'sender_name' not in columns:
        print("Adding sender_name column to existing daily_messages table...")
        cursor.execute("ALTER TABLE daily_messages ADD COLUMN sender_name TEXT")
        conn.commit()

    # Check if message_source column exists in daily_messages, add it if not (for old databases)
    cursor.execute("PRAGMA table_info(daily_messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'message_source' not in columns:
        print("Adding message_source column to existing daily_messages table...")
        cursor.execute("ALTER TABLE daily_messages ADD COLUMN message_source TEXT")
        conn.commit()

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def save_usage(
    date: str,
    tool_name: str,
    tokens_used: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_tokens: int = 0,
    request_count: int = 0,
    models_used: Optional[List[str]] = None,
    host_name: str = 'localhost'
) -> bool:
    """Save or update usage data for a specific date and tool."""
    conn = get_connection()
    cursor = conn.cursor()

    models_json = json.dumps(models_used) if models_used else None

    cursor.execute('''
        INSERT OR REPLACE INTO daily_usage
        (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, models_used)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, models_json))

    conn.commit()
    conn.close()
    return True


def get_usage_by_date(date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None) -> List[Dict]:
    """Get usage data for a specific date, optionally filtered by tool and host."""
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ['date = ?']
    params = [date]

    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)

    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)

    cursor.execute(f'''
        SELECT * FROM daily_usage
        WHERE {' AND '.join(conditions)}
        ORDER BY date DESC
    ''', params)

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get('models_used'):
            result['models_used'] = json.loads(result['models_used'])
        # Ensure request_count exists with default value
        if 'request_count' not in result:
            result['request_count'] = 0
        results.append(result)

    return results


def get_usage_by_tool(
    tool_name: str,
    days: int = 7,
    end_date: Optional[str] = None,
    host_name: Optional[str] = None
) -> List[Dict]:
    """Get usage data for a specific tool over a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    start_date = datetime.now()
    if isinstance(days, int):
        start_date = datetime.now() - timedelta(days=days-1)
    start_date = start_date.strftime("%Y-%m-%d")

    conditions = ['tool_name = ?', 'date >= ?', 'date <= ?']
    params = [tool_name, start_date, end_date]

    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)

    cursor.execute(f'''
        SELECT * FROM daily_usage
        WHERE {' AND '.join(conditions)}
        ORDER BY date DESC
    ''', params)

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get('models_used'):
            result['models_used'] = json.loads(result['models_used'])
        # Ensure request_count exists with default value
        if 'request_count' not in result:
            result['request_count'] = 0
        results.append(result)

    return results


def get_all_tools() -> List[str]:
    """Get list of all tools in the database."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT DISTINCT tool_name FROM daily_usage
        ORDER BY tool_name
    ''')

    rows = cursor.fetchall()
    conn.close()

    return [row['tool_name'] for row in rows]


def get_all_hosts(active_only: bool = True) -> List[str]:
    """Get list of all hosts in the database.

    Args:
        active_only: If True, only return hosts with data in the last 7 days
    """
    conn = get_connection()
    cursor = conn.cursor()

    if active_only:
        cursor.execute('''
            SELECT DISTINCT host_name FROM daily_usage
            WHERE date >= date('now', '-7 days')
              AND host_name != 'localhost'
            ORDER BY host_name
        ''')
    else:
        cursor.execute('''
            SELECT DISTINCT host_name FROM daily_usage
            WHERE host_name != 'localhost' OR host_name IS NULL
            ORDER BY host_name
        ''')

    rows = cursor.fetchall()
    conn.close()

    return [row['host_name'] for row in rows]


def get_summary_by_tool(host_name: Optional[str] = None) -> Dict[str, Dict]:
    """Get summary statistics grouped by tool, optionally filtered by host."""
    conn = get_connection()
    cursor = conn.cursor()

    if host_name:
        cursor.execute('''
            SELECT
                tool_name,
                COUNT(*) as days_count,
                SUM(tokens_used) as total_tokens,
                AVG(tokens_used) as avg_tokens,
                SUM(request_count) as total_requests,
                AVG(request_count) as avg_requests,
                MIN(date) as first_date,
                MAX(date) as last_date
            FROM daily_usage
            WHERE host_name = ?
            GROUP BY tool_name
            ORDER BY total_tokens DESC
        ''', (host_name,))
    else:
        cursor.execute('''
            SELECT
                tool_name,
                COUNT(*) as days_count,
                SUM(tokens_used) as total_tokens,
                AVG(tokens_used) as avg_tokens,
                SUM(request_count) as total_requests,
                AVG(request_count) as avg_requests,
                MIN(date) as first_date,
                MAX(date) as last_date
            FROM daily_usage
            GROUP BY tool_name
            ORDER BY total_tokens DESC
        ''')

    rows = cursor.fetchall()
    conn.close()

    results = {}
    for row in rows:
        results[row['tool_name']] = {
            'days_count': row['days_count'],
            'total_tokens': row['total_tokens'],
            'avg_tokens': round(row['avg_tokens'], 2) if row['avg_tokens'] else 0,
            'total_requests': row['total_requests'] if row['total_requests'] else 0,
            'avg_requests': round(row['avg_requests'], 2) if row['avg_requests'] else 0,
            'first_date': row['first_date'],
            'last_date': row['last_date']
        }

    return results


def get_daily_range(
    start_date: str,
    end_date: str,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None
) -> List[Dict]:
    """Get usage data within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ['date >= ?', 'date <= ?']
    params = [start_date, end_date]

    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)

    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)

    cursor.execute(f'''
        SELECT * FROM daily_usage
        WHERE {' AND '.join(conditions)}
        ORDER BY date DESC
    ''', params)

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get('models_used'):
            result['models_used'] = json.loads(result['models_used'])
        # Ensure request_count exists with default value
        if 'request_count' not in result:
            result['request_count'] = 0
        results.append(result)

    return results


def save_message(
    date: str,
    tool_name: str,
    message_id: str,
    role: str,
    content: str,
    full_entry: Optional[str] = None,
    tokens_used: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: Optional[str] = None,
    timestamp: Optional[str] = None,
    parent_id: Optional[str] = None,
    host_name: str = 'localhost',
    sender_id: Optional[str] = None,
    sender_name: Optional[str] = None,
    message_source: Optional[str] = None,
    conversation_label: Optional[str] = None,
    group_subject: Optional[str] = None,
    is_group_chat: Optional[bool] = None
) -> bool:
    """Save an individual message to the database."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR REPLACE INTO daily_messages
        (date, tool_name, host_name, message_id, parent_id, role, content, full_entry, tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name, message_source, conversation_label, group_subject, is_group_chat)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (date, tool_name, host_name, message_id, parent_id, role, content, full_entry, tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name, message_source, conversation_label, group_subject, is_group_chat))

    conn.commit()
    conn.close()
    return True


def get_messages_by_date(
    date: str,
    tool_name: Optional[str] = None,
    roles: Optional[List[str]] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    host_name: Optional[str] = None,
    sender: Optional[str] = None
) -> Dict:
    """Get messages for a specific date with filters.

    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Optional tool name filter (claude, qwen, etc.)
        roles: Optional list of roles to filter (user, assistant, system)
        search: Optional search term for message content
        page: Page number (1-indexed)
        limit: Number of results per page
        host_name: Optional host name filter
        sender: Optional sender name or ID filter

    Returns:
        Dict with 'messages' (list), 'total' (int), 'page', 'limit', 'total_pages'
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Build query with WHERE conditions
    conditions = ['date = ?']
    params = [date]

    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)

    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)

    if sender:
        conditions.append('(sender_name = ? OR sender_id = ?)')
        params.extend([sender, sender])

    if roles:
        placeholders = ','.join(['?' for _ in roles])
        conditions.append(f'role IN ({placeholders})')
        params.extend(roles)

    if search:
        conditions.append('content LIKE ?')
        params.append(f'%{search}%')

    # Get total count
    where_clause = ' AND '.join(conditions)
    cursor.execute(f'''
        SELECT COUNT(*) as count FROM daily_messages
        WHERE {where_clause}
    ''', params)

    total = cursor.fetchone()['count']
    total_pages = (total + limit - 1) // limit if total > 0 else 1

    # Get paginated messages
    offset = (page - 1) * limit
    cursor.execute(f'''
        SELECT * FROM daily_messages
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    ''', params + [limit, offset])

    rows = cursor.fetchall()
    conn.close()

    messages = []
    for row in rows:
        msg = dict(row)
        # Store original content first
        original_content = msg.get('content')
        # Parse content as JSON if possible
        if original_content:
            try:
                msg['content_parsed'] = json.loads(original_content)
            except (json.JSONDecodeError, TypeError):
                msg['content_parsed'] = original_content
        messages.append(msg)

    return {
        'messages': messages,
        'total': total,
        'page': page,
        'limit': limit,
        'total_pages': total_pages
    }


def get_hosts_by_tool(tool_name: str) -> List[str]:
    """Get list of hosts for a specific tool."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT DISTINCT host_name FROM daily_usage
        WHERE tool_name = ?
        ORDER BY host_name
    ''', (tool_name,))

    rows = cursor.fetchall()
    conn.close()

    return [row['host_name'] for row in rows]


def get_unique_senders(date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None) -> List[str]:
    """Get unique sender names for a specific date.

    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Optional tool name filter (claude, qwen, etc.)
        host_name: Optional host name filter

    Returns:
        List of unique sender names sorted alphabetically
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ['date = ?']
    params = [date]

    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)

    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)

    # Get unique sender_name values, falling back to sender_id if sender_name is null
    # Include records where either sender_name or sender_id is not null
    cursor.execute(f'''
        SELECT DISTINCT
            CASE
                WHEN sender_name IS NOT NULL AND sender_name != '' THEN sender_name
                ELSE sender_id
            END as sender
        FROM daily_messages
        WHERE {' AND '.join(conditions)}
          AND (sender_name IS NOT NULL OR sender_id IS NOT NULL)
        ORDER BY sender
    ''', params)

    rows = cursor.fetchall()
    conn.close()

    # Filter out None values and return unique senders
    senders = [row['sender'] for row in rows if row['sender']]
    return senders


def format_timestamp_to_cst(timestamp_str: str) -> str:
    """Convert UTC timestamp string to CST (Asia/Shanghai) formatted string.

    Handles various timestamp formats:
    - "2026-03-03T12:21:31.917Z" (standard ISO with Z)
    - "2026-03-03 04:21:31.917Z" (modified format with space)

    Args:
        timestamp_str: UTC timestamp in ISO format

    Returns:
        Formatted string in CST timezone (e.g., "2026-03-03 20:21:31")
    """
    if not timestamp_str:
        return ""

    try:
        # Handle modified format (space instead of T) from database
        if " " in timestamp_str:
            ts = timestamp_str.replace("Z", "")
            # Remove trailing Z if present
            dt = datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S.%f" if "." in ts else "%Y-%m-%d %H:%M:%S")
        elif timestamp_str.endswith("Z"):
            ts = timestamp_str[:-1]
            if "." in ts:
                dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
            else:
                dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", ""))

        # Convert to CST (UTC+8)
        from datetime import timedelta
        cst_dt = dt + timedelta(hours=8)

        return cst_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return timestamp_str
