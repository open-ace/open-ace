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

    # Initialize authentication tables
    init_auth_database()

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


# ==========================================
# Authentication Functions
# ==========================================

def init_auth_database() -> None:
    """Initialize the authentication database with required tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            quota_tokens INTEGER DEFAULT 1000000,
            quota_requests INTEGER DEFAULT 1000,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create sessions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_token TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # Create quota_usage table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quota_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            tool_name TEXT,
            tokens_used INTEGER DEFAULT 0,
            requests_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Authentication database initialized")


def create_user(username: str, password_hash: str, email: str = None,
                role: str = 'user', quota_tokens: int = 1000000,
                quota_requests: int = 1000) -> bool:
    """Create a new user."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT INTO users (username, password_hash, email, role, quota_tokens, quota_requests)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (username, password_hash, email, role, quota_tokens, quota_requests))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def create_user_with_is_active(username: str, password_hash: str, email: str = None,
                               role: str = 'user', quota_tokens: int = 1000000,
                               quota_requests: int = 1000, is_active: int = 1) -> bool:
    """Create a new user with is_active flag."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT INTO users (username, password_hash, email, role, quota_tokens, quota_requests, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (username, password_hash, email, role, quota_tokens, quota_requests, is_active))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[Dict]:
    """Get user by username."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Get user by ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def get_all_users() -> List[Dict]:
    """Get all users (admin only)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, username, email, role, quota_tokens, quota_requests,
               is_active, created_at
        FROM users
        ORDER BY id DESC
    ''')
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_global_quota_summary(start_date: str, end_date: str) -> Dict:
    """Get global quota summary within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    # Get total quota allocated to all users
    cursor.execute('''
        SELECT COALESCE(SUM(quota_tokens), 0) as total_quota
        FROM users
    ''')
    total_quota = cursor.fetchone()[0] or 0

    # Get total usage within date range
    cursor.execute('''
        SELECT COALESCE(SUM(tokens_used), 0) as total_used
        FROM quota_usage
        WHERE date >= ? AND date <= ?
    ''', (start_date, end_date))
    total_used = cursor.fetchone()[0] or 0

    conn.close()

    return {
        'total_quota': total_quota,
        'total_used': total_used,
        'remaining': total_quota - total_used
    }


def get_user_quota_breakdown(start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage breakdown by user."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            u.id as user_id,
            u.username,
            u.email,
            u.quota_tokens as quota,
            COALESCE(SUM(q.tokens_used), 0) as used
        FROM users u
        LEFT JOIN quota_usage q ON u.id = q.user_id
            AND q.date >= ? AND q.date <= ?
        GROUP BY u.id, u.username, u.email, u.quota_tokens
        ORDER BY used DESC
    ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict['remaining'] = row_dict['quota'] - row_dict['used']
        result.append(row_dict)

    return result


def verify_password(username: str, password: str) -> Optional[Dict]:
    """Verify user password and return user info if valid."""
    import hashlib

    user = get_user_by_username(username)
    if not user:
        return None

    # For now, do a simple hash comparison
    # In production, use bcrypt: bcrypt.checkpw(password.encode(), user['password_hash'])
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if password_hash == user['password_hash']:
        return user
    return None


def create_session(user_id: int, session_token: str, expires_at: datetime) -> bool:
    """Create a new session for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT INTO sessions (user_id, session_token, expires_at)
            VALUES (?, ?, ?)
        ''', (user_id, session_token, expires_at))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_session_by_token(session_token: str) -> Optional[Dict]:
    """Get session by token if not expired."""
    conn = get_connection()
    cursor = conn.cursor()

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        SELECT s.*, u.* FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_token = ? AND s.expires_at > ?
    ''', (session_token, now))

    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def delete_session(session_token: str) -> bool:
    """Delete a session."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM sessions WHERE session_token = ?', (session_token,))
    conn.commit()
    conn.close()
    return True


def get_all_users() -> List[Dict]:
    """Get all users (for admin)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM users ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def update_user(user_id: int, **kwargs) -> bool:
    """Update user information."""
    allowed_fields = ['email', 'role', 'quota_tokens', 'quota_requests', 'is_active']
    updates = []
    params = []

    for field, value in kwargs.items():
        if field in allowed_fields:
            updates.append(f'{field} = ?')
            params.append(value)

    if not updates:
        return False

    params.append(user_id)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f'''
        UPDATE users SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?
    ''', params)
    conn.commit()
    conn.close()
    return True


def delete_user(user_id: int) -> bool:
    """Delete a user (admin only)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    return True


def save_quota_usage(user_id: int, date: str, tool_name: str = None,
                     tokens_used: int = 0, requests_used: int = 0) -> bool:
    """Save quota usage for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO quota_usage (user_id, date, tool_name, tokens_used, requests_used)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, date, tool_name, tokens_used, requests_used))
    conn.commit()
    conn.close()
    return True


def get_quota_usage(user_id: int, start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage for a user within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ?
        ORDER BY date DESC
    ''', (user_id, start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_total_quota_usage(user_id: int, start_date: str, end_date: str) -> Dict:
    """Get total quota usage for a user within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            COALESCE(SUM(tokens_used), 0) as total_tokens,
            COALESCE(SUM(requests_used), 0) as total_requests
        FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ?
    ''', (user_id, start_date, end_date))

    row = cursor.fetchone()
    conn.close()

    return {
        'total_tokens': row['total_tokens'],
        'total_requests': row['total_requests']
    } if row else {'total_tokens': 0, 'total_requests': 0}


def get_quota_usage_by_tool(user_id: int, start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage grouped by tool for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            tool_name,
            SUM(tokens_used) as total_tokens,
            SUM(requests_used) as total_requests,
            COUNT(*) as days_used
        FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ? AND tool_name IS NOT NULL
        GROUP BY tool_name
        ORDER BY total_tokens DESC
    ''', (user_id, start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


# =============================================================================
# Analysis Module - 深度分析查询函数
# =============================================================================

def get_hourly_usage_from_messages(start_date: str, end_date: str,
                                    tool_name: Optional[str] = None,
                                    host_name: Optional[str] = None) -> List[Dict]:
    """Get hourly usage statistics from daily_messages table.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter

    Returns:
        List of dicts with hour (0-23), day_of_week (0-6), tokens_used, message_count
        Note: day_of_week uses SQLite strftime('%w'): 0=Sunday, 1=Monday, ..., 6=Saturday
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    conditions = ['date >= ?', 'date <= ?', 'timestamp IS NOT NULL']
    params = [start_date, end_date]
    
    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)
    
    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)
    
    where_clause = ' AND '.join(conditions)
    
    # Extract hour from timestamp and calculate day of week
    # SQLite doesn't have native day of week, use strftime('%w')
    cursor.execute(f'''
        SELECT 
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            CAST(strftime('%w', date) AS INTEGER) as day_of_week,
            SUM(tokens_used) as tokens_used,
            COUNT(*) as message_count,
            SUM(input_tokens) as input_tokens,
            SUM(output_tokens) as output_tokens
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY hour, day_of_week
        ORDER BY day_of_week, hour
    ''', params)
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_user_activity_ranking(start_date: str, end_date: str, 
                               limit: int = 10,
                               tool_name: Optional[str] = None,
                               host_name: Optional[str] = None) -> List[Dict]:
    """Get user activity ranking.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        limit: Number of users to return
        tool_name: Optional tool name filter
        host_name: Optional host name filter
    
    Returns:
        List of dicts with sender_id, sender_name, message_count, tokens_used, active_days
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    conditions = ['date >= ?', 'date <= ?', '(sender_id IS NOT NULL OR sender_name IS NOT NULL)']
    params = [start_date, end_date]
    
    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)
    
    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)
    
    where_clause = ' AND '.join(conditions)
    
    cursor.execute(f'''
        SELECT 
            COALESCE(sender_name, sender_id) as sender_name,
            sender_id,
            COUNT(*) as message_count,
            SUM(tokens_used) as tokens_used,
            COUNT(DISTINCT date) as active_days,
            MIN(date) as first_active_date,
            MAX(date) as last_active_date
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY COALESCE(sender_name, sender_id), sender_id
        ORDER BY message_count DESC
        LIMIT ?
    ''', params + [limit])
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_session_statistics(start_date: str, end_date: str,
                           tool_name: Optional[str] = None,
                           host_name: Optional[str] = None) -> Dict:
    """Get session/conversation statistics.
    
    Analyzes conversation patterns based on parent_id relationships.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter
    
    Returns:
        Dict with session statistics
    """
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
    
    where_clause = ' AND '.join(conditions)
    
    # Get total messages and messages with parent_id (replies)
    cursor.execute(f'''
        SELECT 
            COUNT(*) as total_messages,
            SUM(CASE WHEN parent_id IS NOT NULL THEN 1 ELSE 0 END) as reply_messages,
            COUNT(DISTINCT parent_id) as unique_conversations
        FROM daily_messages
        WHERE {where_clause}
    ''', params)
    
    row = cursor.fetchone()
    
    # Calculate conversation length distribution
    cursor.execute(f'''
        SELECT 
            COUNT(*) as conversation_count,
            AVG(conv_length) as avg_length,
            MIN(conv_length) as min_length,
            MAX(conv_length) as max_length
        FROM (
            SELECT parent_id, COUNT(*) as conv_length
            FROM daily_messages
            WHERE {where_clause} AND parent_id IS NOT NULL
            GROUP BY parent_id
        )
    ''', params)
    
    conv_row = cursor.fetchone()
    
    conn.close()
    
    total = row['total_messages'] or 0
    replies = row['reply_messages'] or 0
    conversations = row['unique_conversations'] or 0
    
    return {
        'total_messages': total,
        'reply_messages': replies,
        'unique_conversations': conversations,
        'single_turn': total - replies,
        'multi_turn_ratio': round(replies / total, 3) if total > 0 else 0,
        'conversation_stats': {
            'count': conv_row['conversation_count'] if conv_row else 0,
            'avg_length': round(conv_row['avg_length'], 2) if conv_row and conv_row['avg_length'] else 0,
            'min_length': conv_row['min_length'] if conv_row else 0,
            'max_length': conv_row['max_length'] if conv_row else 0
        }
    }


def get_peak_usage_periods(start_date: str, end_date: str,
                           tool_name: Optional[str] = None,
                           host_name: Optional[str] = None,
                           limit: int = 10) -> List[Dict]:
    """Get peak usage periods.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter
        limit: Number of peak periods to return
    
    Returns:
        List of dicts with date, hour, tokens_used, message_count
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    conditions = ['date >= ?', 'date <= ?', 'timestamp IS NOT NULL']
    params = [start_date, end_date]
    
    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)
    
    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)
    
    where_clause = ' AND '.join(conditions)
    
    cursor.execute(f'''
        SELECT 
            date,
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            SUM(tokens_used) as tokens_used,
            COUNT(*) as message_count
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY date, hour
        ORDER BY tokens_used DESC
        LIMIT ?
    ''', params + [limit])
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_user_segmentation(date: str, 
                          tool_name: Optional[str] = None,
                          host_name: Optional[str] = None) -> Dict:
    """Get user segmentation by activity level.
    
    Segments:
    - high: > 10K tokens
    - medium: 1K - 10K tokens
    - low: < 1K tokens
    - dormant: no activity in last 7 days
    
    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter
    
    Returns:
        Dict with user counts for each segment
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get user usage for the specified date
    conditions = ['date = ?', '(sender_id IS NOT NULL OR sender_name IS NOT NULL)']
    params = [date]
    
    if tool_name:
        conditions.append('tool_name = ?')
        params.append(tool_name)
    
    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)
    
    where_clause = ' AND '.join(conditions)
    
    cursor.execute(f'''
        SELECT 
            COALESCE(sender_name, sender_id) as sender,
            SUM(tokens_used) as tokens_used
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY COALESCE(sender_name, sender_id), sender_id
    ''', params)
    
    rows = cursor.fetchall()
    
    # Calculate segments
    high_users = 0
    medium_users = 0
    low_users = 0
    
    for row in rows:
        tokens = row['tokens_used'] or 0
        if tokens > 10000:
            high_users += 1
        elif tokens >= 1000:
            medium_users += 1
        else:
            low_users += 1
    
    # Get dormant users (active in past 30 days but not in last 7 days)
    from datetime import datetime, timedelta
    date_obj = datetime.strptime(date, '%Y-%m-%d')
    seven_days_ago = (date_obj - timedelta(days=7)).strftime('%Y-%m-%d')
    thirty_days_ago = (date_obj - timedelta(days=30)).strftime('%Y-%m-%d')
    
    cursor.execute(f'''
        SELECT COUNT(DISTINCT COALESCE(sender_name, sender_id)) as dormant_count
        FROM daily_messages
        WHERE date >= ? AND date < ? AND (sender_id IS NOT NULL OR sender_name IS NOT NULL)
    ''', [thirty_days_ago, seven_days_ago])
    
    dormant_row = cursor.fetchone()
    dormant_users = dormant_row['dormant_count'] if dormant_row else 0
    
    conn.close()
    
    return {
        'high': high_users,
        'medium': medium_users,
        'low': low_users,
        'dormant': dormant_users,
        'total_active': high_users + medium_users + low_users
    }


def get_tool_comparison_metrics(start_date: str, end_date: str,
                                host_name: Optional[str] = None) -> List[Dict]:
    """Get comparison metrics for different tools.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        host_name: Optional host name filter
    
    Returns:
        List of dicts with tool metrics
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    conditions = ['date >= ?', 'date <= ?']
    params = [start_date, end_date]
    
    if host_name:
        conditions.append('host_name = ?')
        params.append(host_name)
    
    where_clause = ' AND '.join(conditions)
    
    # Get usage metrics from daily_usage
    cursor.execute(f'''
        SELECT 
            tool_name,
            SUM(tokens_used) as total_tokens,
            SUM(input_tokens) as input_tokens,
            SUM(output_tokens) as output_tokens,
            SUM(request_count) as total_requests,
            COUNT(*) as days_active,
            AVG(tokens_used) as avg_daily_tokens
        FROM daily_usage
        WHERE {where_clause}
        GROUP BY tool_name
        ORDER BY total_tokens DESC
    ''', params)
    
    usage_rows = cursor.fetchall()
    
    # Get message metrics from daily_messages
    cursor.execute(f'''
        SELECT 
            tool_name,
            COUNT(*) as total_messages,
            COUNT(DISTINCT COALESCE(sender_name, sender_id)) as unique_users,
            AVG(tokens_used) as avg_tokens_per_message
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY tool_name
    ''', params)
    
    message_rows = cursor.fetchall()
    message_data = {row['tool_name']: dict(row) for row in message_rows}
    
    conn.close()
    
    results = []
    for row in usage_rows:
        result = dict(row)
        msg_data = message_data.get(row['tool_name'], {})
        result['total_messages'] = msg_data.get('total_messages', 0)
        result['unique_users'] = msg_data.get('unique_users', 0)
        result['avg_tokens_per_message'] = round(msg_data.get('avg_tokens_per_message', 0), 2)
        results.append(result)
    
    return results


def detect_usage_anomalies(start_date: str, end_date: str,
                           tool_name: Optional[str] = None,
                           host_name: Optional[str] = None,
                           threshold_std: float = 3.0) -> List[Dict]:
    """Detect usage anomalies using statistical methods (3-sigma rule).
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter
        threshold_std: Number of standard deviations for anomaly detection
    
    Returns:
        List of dicts with anomaly details
    """
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
    
    where_clause = ' AND '.join(conditions)
    
    # Get daily usage statistics
    cursor.execute(f'''
        SELECT 
            date,
            tool_name,
            tokens_used,
            request_count
        FROM daily_usage
        WHERE {where_clause}
        ORDER BY date
    ''', params)
    
    rows = cursor.fetchall()
    
    if not rows:
        conn.close()
        return []
    
    # Calculate mean and std for each tool
    from collections import defaultdict
    import math
    
    tool_data = defaultdict(list)
    for row in rows:
        tool_data[row['tool_name']].append({
            'date': row['date'],
            'tokens_used': row['tokens_used'],
            'request_count': row['request_count'] or 0
        })
    
    anomalies = []
    
    for tool_name, data in tool_data.items():
        if len(data) < 3:  # Need at least 3 data points
            continue
        
        tokens = [d['tokens_used'] for d in data]
        mean = sum(tokens) / len(tokens)
        variance = sum((x - mean) ** 2 for x in tokens) / len(tokens)
        std = math.sqrt(variance)
        
        if std == 0:
            continue
        
        # Detect anomalies
        for d in data:
            z_score = (d['tokens_used'] - mean) / std
            if abs(z_score) > threshold_std:
                anomalies.append({
                    'date': d['date'],
                    'tool_name': tool_name,
                    'tokens_used': d['tokens_used'],
                    'mean': round(mean, 2),
                    'std': round(std, 2),
                    'z_score': round(z_score, 2),
                    'anomaly_type': 'spike' if z_score > 0 else 'drop',
                    'severity': 'high' if abs(z_score) > 4 else 'medium'
                })
    
    conn.close()
    
    # Sort by severity and z_score magnitude
    anomalies.sort(key=lambda x: (0 if x['severity'] == 'high' else 1, -abs(x['z_score'])))
    
    return anomalies


def get_key_metrics(start_date: str,
                    end_date: Optional[str] = None,
                    tool_name: Optional[str] = None,
                    host_name: Optional[str] = None) -> Dict:
    """Get key metrics for dashboard.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format (optional, defaults to start_date for single day)
        tool_name: Optional tool name filter
        host_name: Optional host name filter

    Returns:
        Dict with key metrics
    """
    # If end_date not provided, use start_date for single day query
    if end_date is None:
        end_date = start_date

    conn = get_connection()
    cursor = conn.cursor()

    # Build WHERE conditions for date range
    usage_conditions = ['date >= ?', 'date <= ?']
    usage_params = [start_date, end_date]

    if tool_name:
        usage_conditions.append('tool_name = ?')
        usage_params.append(tool_name)

    if host_name:
        usage_conditions.append('host_name = ?')
        usage_params.append(host_name)

    usage_where = ' AND '.join(usage_conditions)

    # Get usage metrics for the date range
    cursor.execute(f'''
        SELECT
            SUM(tokens_used) as total_tokens,
            SUM(request_count) as total_requests,
            COUNT(DISTINCT tool_name) as active_tools,
            COUNT(DISTINCT date) as active_days
        FROM daily_usage
        WHERE {usage_where}
    ''', usage_params)

    usage_row = cursor.fetchone()

    # Get message metrics for the date range
    cursor.execute(f'''
        SELECT
            COUNT(*) as total_messages,
            COUNT(DISTINCT COALESCE(sender_name, sender_id)) as active_users
        FROM daily_messages
        WHERE {usage_where} AND (sender_id IS NOT NULL OR sender_name IS NOT NULL)
    ''', usage_params)

    message_row = cursor.fetchone()

    # Get previous period usage for comparison (same length as current period)
    from datetime import datetime, timedelta
    start_obj = datetime.strptime(start_date, '%Y-%m-%d')
    end_obj = datetime.strptime(end_date, '%Y-%m-%d')
    period_days = (end_obj - start_obj).days + 1

    prev_end = (start_obj - timedelta(days=1)).strftime('%Y-%m-%d')
    prev_start = (start_obj - timedelta(days=period_days)).strftime('%Y-%m-%d')

    prev_conditions = ['date >= ?', 'date <= ?']
    prev_params = [prev_start, prev_end]

    if tool_name:
        prev_conditions.append('tool_name = ?')
        prev_params.append(tool_name)

    if host_name:
        prev_conditions.append('host_name = ?')
        prev_params.append(host_name)

    cursor.execute(f'''
        SELECT SUM(tokens_used) as prev_tokens
        FROM daily_usage
        WHERE {' AND '.join(prev_conditions)}
    ''', prev_params)

    prev_row = cursor.fetchone()
    prev_tokens = prev_row['prev_tokens'] if prev_row and prev_row['prev_tokens'] else 0

    conn.close()

    current_tokens = usage_row['total_tokens'] or 0
    period_change = ((current_tokens - prev_tokens) / prev_tokens * 100) if prev_tokens > 0 else 0

    return {
        'total_tokens': current_tokens,
        'total_requests': usage_row['total_requests'] or 0,
        'active_tools': usage_row['active_tools'] or 0,
        'active_days': usage_row['active_days'] or 0,
        'total_messages': message_row['total_messages'] or 0,
        'active_users': message_row['active_users'] or 0,
        'period_change': round(period_change, 2)
    }


def get_data_status_by_host(host_name: str) -> Dict:
    """Get data status for a specific host.

    Args:
        host_name: Name of the host to get status for

    Returns:
        Dict with host status information
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get last update time from daily_usage
    cursor.execute('''
        SELECT MAX(created_at) as last_updated
        FROM daily_usage
        WHERE host_name = ?
    ''', [host_name])
    usage_row = cursor.fetchone()
    last_updated_usage = usage_row['last_updated'] if usage_row else None

    # Get last update time from daily_messages
    cursor.execute('''
        SELECT MAX(created_at) as last_updated
        FROM daily_messages
        WHERE host_name = ?
    ''', [host_name])
    message_row = cursor.fetchone()
    last_updated_messages = message_row['last_updated'] if message_row else None

    # Use the most recent update time
    last_updated = last_updated_usage
    if last_updated_messages:
        if not last_updated or last_updated_messages > last_updated:
            last_updated = last_updated_messages

    # Get record counts
    cursor.execute('''
        SELECT COUNT(*) as count
        FROM daily_usage
        WHERE host_name = ?
    ''', [host_name])
    usage_count = cursor.fetchone()['count'] or 0

    cursor.execute('''
        SELECT COUNT(*) as count
        FROM daily_messages
        WHERE host_name = ?
    ''', [host_name])
    message_count = cursor.fetchone()['count'] or 0

    # Get date range
    cursor.execute('''
        SELECT MIN(date) as min_date, MAX(date) as max_date
        FROM daily_usage
        WHERE host_name = ?
    ''', [host_name])
    date_row = cursor.fetchone()

    cursor.execute('''
        SELECT MIN(date) as min_date, MAX(date) as max_date
        FROM daily_messages
        WHERE host_name = ?
    ''', [host_name])
    msg_date_row = cursor.fetchone()

    # Combine date ranges
    min_dates = [date_row['min_date'], msg_date_row['min_date']]
    max_dates = [date_row['max_date'], msg_date_row['max_date']]
    min_dates = [d for d in min_dates if d]
    max_dates = [d for d in max_dates if d]

    conn.close()

    return {
        'host_name': host_name,
        'last_updated': last_updated,
        'usage_records': usage_count,
        'message_records': message_count,
        'date_range': {
            'start': min(min_dates) if min_dates else None,
            'end': max(max_dates) if max_dates else None
        }
    }


def get_all_hosts_with_status() -> List[Dict]:
    """Get data status for all hosts in the database.

    Returns:
        List of dicts with host status information
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get all unique host names from both tables
    cursor.execute('''
        SELECT DISTINCT host_name FROM daily_usage
        UNION
        SELECT DISTINCT host_name FROM daily_messages
    ''')
    hosts = [row['host_name'] for row in cursor.fetchall()]

    results = []

    for host_name in hosts:
        # Get last update time from daily_usage
        cursor.execute('''
            SELECT MAX(created_at) as last_updated
            FROM daily_usage
            WHERE host_name = ?
        ''', [host_name])
        usage_row = cursor.fetchone()
        last_updated_usage = usage_row['last_updated'] if usage_row else None

        # Get last update time from daily_messages
        cursor.execute('''
            SELECT MAX(created_at) as last_updated
            FROM daily_messages
            WHERE host_name = ?
        ''', [host_name])
        message_row = cursor.fetchone()
        last_updated_messages = message_row['last_updated'] if message_row else None

        # Use the most recent update time
        last_updated = last_updated_usage
        if last_updated_messages:
            if not last_updated or last_updated_messages > last_updated:
                last_updated = last_updated_messages

        # Get record counts
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM daily_usage
            WHERE host_name = ?
        ''', [host_name])
        usage_count = cursor.fetchone()['count'] or 0

        cursor.execute('''
            SELECT COUNT(*) as count
            FROM daily_messages
            WHERE host_name = ?
        ''', [host_name])
        message_count = cursor.fetchone()['count'] or 0

        results.append({
            'host_name': host_name,
            'last_updated': last_updated,
            'usage_records': usage_count,
            'message_records': message_count
        })

    conn.close()

    # Sort by last_updated (most recent first)
    results.sort(key=lambda x: x.get('last_updated') or '', reverse=True)

    return results
