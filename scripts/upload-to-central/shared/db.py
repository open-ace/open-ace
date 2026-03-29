#!/usr/bin/env python3
"""
AI Token Usage - Database Module

Provides database operations for the ai_token_usage project.
Supports both SQLite (default) and PostgreSQL databases.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union, Any

# Ensure scripts directory is in path for standalone script execution
_script_dir = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_script_dir)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Use standard import after path setup
from shared import config

DB_DIR = config.DB_DIR
DB_PATH = config.DB_PATH

# Cache for database URL
_db_url_cache = None


def _get_db_url() -> str:
    """Get database URL from config."""
    global _db_url_cache
    if _db_url_cache is None:
        _db_url_cache = config.get_database_url()
    return _db_url_cache


def is_postgresql() -> bool:
    """Check if using PostgreSQL database."""
    return _get_db_url().startswith("postgresql")


def _placeholder() -> str:
    """Get the appropriate placeholder for the current database."""
    return "%s" if is_postgresql() else "?"


def _convert_sql(sql: str) -> str:
    """Convert SQL placeholders from ? to %s for PostgreSQL."""
    if is_postgresql():
        return sql.replace("?", "%s")
    return sql


def _execute(cursor, sql: str, params: tuple = ()) -> None:
    """Execute SQL with automatic placeholder conversion for PostgreSQL."""
    cursor.execute(_convert_sql(sql), params)


def _executemany(cursor, sql: str, params_list: list) -> None:
    """Execute many SQL statements with automatic placeholder conversion for PostgreSQL."""
    cursor.executemany(_convert_sql(sql), params_list)


def ensure_db_dir() -> None:
    """Ensure the database directory exists (for SQLite)."""
    os.makedirs(DB_DIR, exist_ok=True)


def get_connection() -> Union[sqlite3.Connection, Any]:
    """Get a database connection (SQLite or PostgreSQL)."""
    url = _get_db_url()
    if url.startswith("postgresql"):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(url)
            conn.cursor_factory = RealDictCursor
            return conn
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL. "
                "Install it with: pip install psycopg2-binary"
            )
    else:
        ensure_db_dir()
        # Extract path from sqlite:/// URL or use default
        if url.startswith("sqlite:///"):
            db_path = url[10:]
        else:
            db_path = DB_PATH
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _get_id_type() -> str:
    """Get the appropriate ID type for the current database."""
    if is_postgresql():
        return "SERIAL PRIMARY KEY"
    else:
        return "INTEGER PRIMARY KEY AUTOINCREMENT"


def _table_exists(cursor, table_name: str) -> bool:
    """Check if a table exists."""
    if is_postgresql():
        _execute(
            cursor,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
            (table_name,),
        )
        return cursor.fetchone()[0]
    else:
        _execute(
            cursor, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        )
        return cursor.fetchone() is not None


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if is_postgresql():
        _execute(
            cursor,
            "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = %s AND column_name = %s)",
            (table_name, column_name),
        )
        result = cursor.fetchone()
        # Handle both dict-like (RealDictRow) and tuple results
        if isinstance(result, dict):
            return result["exists"]
        return result[0]
    else:
        _execute(cursor, f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        return column_name in columns


def init_database() -> None:
    """Initialize the database with the required schema."""
    if not is_postgresql():
        ensure_db_dir()

    conn = get_connection()
    cursor = conn.cursor()

    id_type = _get_id_type()

    # Create daily_usage table
    _execute(
        cursor,
        f"""
        CREATE TABLE IF NOT EXISTS daily_usage (
            id {id_type},
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
    """,
    )

    # Create daily_messages table first (before checking for full_entry)
    _execute(
        cursor,
        f"""
        CREATE TABLE IF NOT EXISTS daily_messages (
            id {id_type},
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
    """,
    )

    # Check if host_name column exists in daily_usage, add it if not (for old databases)
    if not _column_exists(cursor, "daily_usage", "host_name"):
        print("Adding host_name column to existing daily_usage table...")
        _execute(cursor, "ALTER TABLE daily_usage ADD COLUMN host_name TEXT DEFAULT 'localhost'")
        # Update existing records with 'localhost'
        _execute(cursor, "UPDATE daily_usage SET host_name = 'localhost' WHERE host_name IS NULL")
        conn.commit()

    # Check if host_name column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "host_name"):
        print("Adding host_name column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN host_name TEXT DEFAULT 'localhost'")
        # Update existing records with 'localhost'
        _execute(
            cursor, "UPDATE daily_messages SET host_name = 'localhost' WHERE host_name IS NULL"
        )
        conn.commit()

    # Check if request_count column exists, add it if not (for old databases)
    if not _column_exists(cursor, "daily_usage", "request_count"):
        print("Adding request_count column to existing database...")
        _execute(cursor, "ALTER TABLE daily_usage ADD COLUMN request_count INTEGER DEFAULT 0")
        conn.commit()

    # Check if full_entry column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "full_entry"):
        print("Adding full_entry column to existing database...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN full_entry TEXT")
        conn.commit()

    # Check if sender_id column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "sender_id"):
        print("Adding sender_id column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN sender_id TEXT")
        conn.commit()

    # Check if sender_name column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "sender_name"):
        print("Adding sender_name column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN sender_name TEXT")
        conn.commit()

    # Check if message_source column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "message_source"):
        print("Adding message_source column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN message_source TEXT")
        conn.commit()

    # Check if feishu_conversation_id column exists in daily_messages, add it if not (for old databases)
    # conversation_label was renamed to feishu_conversation_id (Issue #94)
    if not _column_exists(cursor, "daily_messages", "feishu_conversation_id"):
        print("Adding feishu_conversation_id column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN feishu_conversation_id TEXT")
        conn.commit()

    # Check if group_subject column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "group_subject"):
        print("Adding group_subject column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN group_subject TEXT")
        conn.commit()

    # Check if is_group_chat column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "is_group_chat"):
        print("Adding is_group_chat column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN is_group_chat INTEGER")
        conn.commit()

    # Check if agent_session_id column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "agent_session_id"):
        print("Adding agent_session_id column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN agent_session_id TEXT")
        conn.commit()

    # Check if conversation_id column exists in daily_messages, add it if not (for old databases)
    if not _column_exists(cursor, "daily_messages", "conversation_id"):
        print("Adding conversation_id column to existing daily_messages table...")
        _execute(cursor, "ALTER TABLE daily_messages ADD COLUMN conversation_id TEXT")
        conn.commit()

    conn.commit()

    # Create indexes for daily_messages table to improve query performance
    # Optimized indexes (migration 014): removed redundant single-column indexes
    # Keep only essential composite indexes for common query patterns
    indexes_to_create = [
        # Essential composite indexes for common queries
        ("idx_messages_date_tool_host", "daily_messages", "date, tool_name, host_name"),
        ("idx_messages_date_role_timestamp", "daily_messages", "date, role, timestamp DESC"),
        # Single-column indexes for specific queries
        ("idx_messages_sender_id", "daily_messages", "sender_id"),
        ("idx_messages_timestamp", "daily_messages", "timestamp"),
        # New composite indexes for better coverage
        ("idx_messages_conversation", "daily_messages", "date, conversation_id, agent_session_id"),
        ("idx_messages_date_sender_id", "daily_messages", "date, sender_id"),
    ]

    for index_name, table_name, columns in indexes_to_create:
        try:
            _execute(cursor, f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})")
            print(f"Index created: {index_name} on {table_name}({columns})")
        except Exception as e:
            print(f"Warning: Could not create index {index_name}: {e}")

    conn.commit()

    # Initialize authentication tables
    init_auth_database()

    conn.close()
    if is_postgresql():
        print("Database initialized (PostgreSQL)")
    else:
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
    host_name: str = "localhost",
) -> bool:
    """Save or update usage data for a specific date and tool."""
    conn = get_connection()
    cursor = conn.cursor()

    models_json = json.dumps(models_used) if models_used else None

    if is_postgresql():
        _execute(
            cursor,
            """
            INSERT INTO daily_usage
            (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, models_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, tool_name, host_name) DO UPDATE SET
                tokens_used = EXCLUDED.tokens_used,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                cache_tokens = EXCLUDED.cache_tokens,
                request_count = EXCLUDED.request_count,
                models_used = EXCLUDED.models_used
        """,
            (
                date,
                tool_name,
                host_name,
                tokens_used,
                input_tokens,
                output_tokens,
                cache_tokens,
                request_count,
                models_json,
            ),
        )
    else:
        _execute(
            cursor,
            """
            INSERT OR REPLACE INTO daily_usage
            (date, tool_name, host_name, tokens_used, input_tokens, output_tokens, cache_tokens, request_count, models_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                date,
                tool_name,
                host_name,
                tokens_used,
                input_tokens,
                output_tokens,
                cache_tokens,
                request_count,
                models_json,
            ),
        )

    conn.commit()
    conn.close()
    return True


def get_usage_by_date(
    date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> List[Dict]:
    """Get usage data for a specific date, optionally filtered by tool and host."""
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["date = ?"]
    params = [date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    _execute(
        cursor,
        f"""
        SELECT * FROM daily_usage
        WHERE {' AND '.join(conditions)}
        ORDER BY date DESC
    """,
        params,
    )

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get("models_used"):
            result["models_used"] = json.loads(result["models_used"])
        # Ensure request_count exists with default value
        if "request_count" not in result:
            result["request_count"] = 0
        results.append(result)

    return results


def get_usage_by_tool(
    tool_name: str, days: int = 7, end_date: Optional[str] = None, host_name: Optional[str] = None
) -> List[Dict]:
    """Get usage data for a specific tool over a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    start_date = datetime.now()
    if isinstance(days, int):
        start_date = datetime.now() - timedelta(days=days - 1)
    start_date = start_date.strftime("%Y-%m-%d")

    conditions = ["tool_name = ?", "date >= ?", "date <= ?"]
    params = [tool_name, start_date, end_date]

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    _execute(
        cursor,
        f"""
        SELECT * FROM daily_usage
        WHERE {' AND '.join(conditions)}
        ORDER BY date DESC
    """,
        params,
    )

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get("models_used"):
            result["models_used"] = json.loads(result["models_used"])
        # Ensure request_count exists with default value
        if "request_count" not in result:
            result["request_count"] = 0
        results.append(result)

    return results


def get_all_tools() -> List[str]:
    """Get list of all tools in the database."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT DISTINCT tool_name FROM daily_usage
        ORDER BY tool_name
    """,
    )

    rows = cursor.fetchall()
    conn.close()

    return [row["tool_name"] for row in rows]


def get_all_hosts(active_only: bool = True) -> List[str]:
    """Get list of all hosts in the database.

    Args:
        active_only: If True, only return hosts with data in the last 7 days
    """
    conn = get_connection()
    cursor = conn.cursor()

    if active_only:
        # Query hosts from both daily_usage and daily_messages tables
        _execute(
            cursor,
            """
            SELECT DISTINCT host_name FROM daily_usage
            WHERE date >= date('now', '-7 days')
              AND host_name != 'localhost'
            UNION
            SELECT DISTINCT host_name FROM daily_messages
            WHERE date >= date('now', '-7 days')
              AND host_name != 'localhost'
            ORDER BY host_name
        """,
        )
    else:
        _execute(
            cursor,
            """
            SELECT DISTINCT host_name FROM daily_usage
            WHERE host_name != 'localhost' OR host_name IS NULL
            UNION
            SELECT DISTINCT host_name FROM daily_messages
            WHERE host_name != 'localhost' OR host_name IS NULL
            ORDER BY host_name
        """,
        )

    rows = cursor.fetchall()
    conn.close()

    return [row["host_name"] for row in rows]


def get_summary_by_tool(host_name: Optional[str] = None) -> Dict[str, Dict]:
    """Get summary statistics grouped by tool, optionally filtered by host."""
    conn = get_connection()
    cursor = conn.cursor()

    if host_name:
        _execute(
            cursor,
            """
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
        """,
            (host_name,),
        )
    else:
        _execute(
            cursor,
            """
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
        """,
        )

    rows = cursor.fetchall()
    conn.close()

    results = {}
    for row in rows:
        results[row["tool_name"]] = {
            "days_count": row["days_count"],
            "total_tokens": row["total_tokens"],
            "avg_tokens": round(row["avg_tokens"], 2) if row["avg_tokens"] else 0,
            "total_requests": row["total_requests"] if row["total_requests"] else 0,
            "avg_requests": round(row["avg_requests"], 2) if row["avg_requests"] else 0,
            "first_date": row["first_date"],
            "last_date": row["last_date"],
        }

    return results


def get_daily_range(
    start_date: str, end_date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> List[Dict]:
    """Get usage data within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["date >= ?", "date <= ?"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    _execute(
        cursor,
        f"""
        SELECT * FROM daily_usage
        WHERE {' AND '.join(conditions)}
        ORDER BY date DESC
    """,
        params,
    )

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get("models_used"):
            result["models_used"] = json.loads(result["models_used"])
        # Ensure request_count exists with default value
        if "request_count" not in result:
            result["request_count"] = 0
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
    host_name: str = "localhost",
    sender_id: Optional[str] = None,
    sender_name: Optional[str] = None,
    message_source: Optional[str] = None,
    feishu_conversation_id: Optional[str] = None,
    group_subject: Optional[str] = None,
    is_group_chat: Optional[bool] = None,
    agent_session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> bool:
    """Save an individual message to the database.

    Uses INSERT OR REPLACE with smart update logic:
    - If message exists and new tokens_used > 0, update all fields
    - If message exists and new tokens_used == 0, preserve existing token values
    - For other fields (sender_id, sender_name, etc.), always update if new value is not None

    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Tool name (claude, qwen, openclaw)
        message_id: Unique message identifier
        role: Message role (user, assistant, toolResult, error)
        content: Message content
        full_entry: Full JSON entry for reference
        tokens_used: Total tokens used
        input_tokens: Input token count
        output_tokens: Output token count
        model: Model name used
        timestamp: Message timestamp
        parent_id: Parent message ID for threading
        host_name: Host machine name
        sender_id: Sender identifier
        sender_name: Sender name
        message_source: Source (openclaw, feishu, slack)
        feishu_conversation_id: Feishu conversation identifier (renamed from conversation_label)
        group_subject: Group subject (for group chats)
        is_group_chat: Whether this is a group chat
        agent_session_id: Agent session identifier (tool process session)
        conversation_id: Conversation identifier (one round of conversation)
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Check if message already exists
    _execute(
        cursor,
        """
        SELECT tokens_used, input_tokens, output_tokens, sender_id, sender_name, model
        FROM daily_messages
        WHERE date = ? AND tool_name = ? AND message_id = ? AND host_name = ?
    """,
        (date, tool_name, message_id, host_name),
    )

    existing = cursor.fetchone()

    if existing:
        # Preserve existing token values if new values are 0
        if tokens_used == 0 and existing["tokens_used"] > 0:
            tokens_used = existing["tokens_used"]
        if input_tokens == 0 and existing["input_tokens"] > 0:
            input_tokens = existing["input_tokens"]
        if output_tokens == 0 and existing["output_tokens"] > 0:
            output_tokens = existing["output_tokens"]
        # Preserve sender info if new values are None
        if sender_id is None and existing["sender_id"]:
            sender_id = existing["sender_id"]
        if sender_name is None and existing["sender_name"]:
            sender_name = existing["sender_name"]
        # Preserve model if new value is None and existing value exists
        if model is None and existing["model"]:
            model = existing["model"]

    if is_postgresql():
        _execute(
            cursor,
            """
            INSERT INTO daily_messages
            (date, tool_name, host_name, message_id, parent_id, role, content, full_entry, tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name, message_source, feishu_conversation_id, group_subject, is_group_chat, agent_session_id, conversation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, tool_name, host_name, message_id) DO UPDATE SET
                parent_id = EXCLUDED.parent_id,
                role = EXCLUDED.role,
                content = EXCLUDED.content,
                full_entry = EXCLUDED.full_entry,
                tokens_used = EXCLUDED.tokens_used,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                model = EXCLUDED.model,
                timestamp = EXCLUDED.timestamp,
                sender_id = EXCLUDED.sender_id,
                sender_name = EXCLUDED.sender_name,
                message_source = EXCLUDED.message_source,
                feishu_conversation_id = EXCLUDED.feishu_conversation_id,
                group_subject = EXCLUDED.group_subject,
                is_group_chat = EXCLUDED.is_group_chat,
                agent_session_id = EXCLUDED.agent_session_id,
                conversation_id = EXCLUDED.conversation_id
        """,
            (
                date,
                tool_name,
                host_name,
                message_id,
                parent_id,
                role,
                content,
                full_entry,
                tokens_used,
                input_tokens,
                output_tokens,
                model,
                timestamp,
                sender_id,
                sender_name,
                message_source,
                feishu_conversation_id,
                group_subject,
                is_group_chat,
                agent_session_id,
                conversation_id,
            ),
        )
    else:
        # Use INSERT ... ON CONFLICT to preserve created_at on updates
        _execute(
            cursor,
            """
            INSERT INTO daily_messages
            (date, tool_name, host_name, message_id, parent_id, role, content, full_entry, tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name, message_source, feishu_conversation_id, group_subject, is_group_chat, agent_session_id, conversation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, tool_name, host_name, message_id) DO UPDATE SET
                parent_id = excluded.parent_id,
                role = excluded.role,
                content = excluded.content,
                full_entry = excluded.full_entry,
                tokens_used = excluded.tokens_used,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                model = excluded.model,
                timestamp = excluded.timestamp,
                sender_id = excluded.sender_id,
                sender_name = excluded.sender_name,
                message_source = excluded.message_source,
                feishu_conversation_id = excluded.feishu_conversation_id,
                group_subject = excluded.group_subject,
                is_group_chat = excluded.is_group_chat,
                agent_session_id = excluded.agent_session_id,
                conversation_id = excluded.conversation_id
        """,
            (
                date,
                tool_name,
                host_name,
                message_id,
                parent_id,
                role,
                content,
                full_entry,
                tokens_used,
                input_tokens,
                output_tokens,
                model,
                timestamp,
                sender_id,
                sender_name,
                message_source,
                feishu_conversation_id,
                group_subject,
                is_group_chat,
                agent_session_id,
                conversation_id,
            ),
        )

    conn.commit()
    conn.close()
    return True


def save_messages_batch(messages: List[Dict], batch_size: int = 1000) -> int:
    """Save multiple messages to the database using batch insert with transaction.

    This is much faster than calling save_message() for each message individually
    because it uses a single transaction and batch inserts.

    Args:
        messages: List of message dictionaries with keys:
            - date: Date in YYYY-MM-DD format
            - tool_name: Tool name (claude, qwen, openclaw)
            - message_id: Unique message identifier
            - role: Message role (user, assistant, system, tool_result)
            - content: Message content
            - full_entry: Full JSON entry for reference (optional)
            - tokens_used: Total tokens used (default 0)
            - input_tokens: Input token count (default 0)
            - output_tokens: Output token count (default 0)
            - model: Model name used (optional)
            - timestamp: Message timestamp (optional)
            - parent_id: Parent message ID (optional)
            - host_name: Host machine name (default 'localhost')
            - sender_id: Sender identifier (optional)
            - sender_name: Sender name (optional)
            - message_source: Source (optional)
            - feishu_conversation_id: Feishu conversation identifier (optional)
            - group_subject: Group subject (optional)
            - is_group_chat: Whether this is a group chat (optional)
            - agent_session_id: Agent session identifier (optional)
            - conversation_id: Conversation identifier (optional)
        batch_size: Number of messages to insert per batch (default 1000)

    Returns:
        Number of messages saved
    """
    if not messages:
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    saved = 0

    try:
        # Build a map of existing messages for this batch
        # Get unique (date, tool_name, host_name) combinations
        keys = set()
        for msg in messages:
            key = (msg.get("date"), msg.get("tool_name"), msg.get("host_name", "localhost"))
            keys.add(key)

        # Query existing messages in one go
        existing_map = {}
        for date, tool_name, host_name in keys:
            if is_postgresql():
                _execute(
                    cursor,
                    """
                    SELECT message_id, tokens_used, input_tokens, output_tokens, sender_id, sender_name, model
                    FROM daily_messages
                    WHERE date = %s AND tool_name = %s AND host_name = %s
                """,
                    (date, tool_name, host_name),
                )
            else:
                _execute(
                    cursor,
                    """
                    SELECT message_id, tokens_used, input_tokens, output_tokens, sender_id, sender_name, model
                    FROM daily_messages
                    WHERE date = ? AND tool_name = ? AND host_name = ?
                """,
                    (date, tool_name, host_name),
                )

            for row in cursor.fetchall():
                if isinstance(row, dict):
                    msg_id = row["message_id"]
                    existing_map[(date, tool_name, host_name, msg_id)] = row
                else:
                    msg_id = row[0]
                    existing_map[(date, tool_name, host_name, msg_id)] = {
                        "tokens_used": row[1],
                        "input_tokens": row[2],
                        "output_tokens": row[3],
                        "sender_id": row[4],
                        "sender_name": row[5],
                        "model": row[6],
                    }

        # Process messages in batches
        for i in range(0, len(messages), batch_size):
            batch = messages[i : i + batch_size]

            for msg in batch:
                date = msg.get("date")
                tool_name = msg.get("tool_name")
                host_name = msg.get("host_name", "localhost")
                message_id = msg.get("message_id")

                tokens_used = msg.get("tokens_used", 0)
                input_tokens = msg.get("input_tokens", 0)
                output_tokens = msg.get("output_tokens", 0)
                sender_id = msg.get("sender_id")
                sender_name = msg.get("sender_name")
                model = msg.get("model")

                # Check for existing message
                key = (date, tool_name, host_name, message_id)
                if key in existing_map:
                    existing = existing_map[key]
                    # Preserve existing token values if new values are 0
                    if tokens_used == 0 and existing.get("tokens_used", 0) > 0:
                        tokens_used = existing["tokens_used"]
                    if input_tokens == 0 and existing.get("input_tokens", 0) > 0:
                        input_tokens = existing["input_tokens"]
                    if output_tokens == 0 and existing.get("output_tokens", 0) > 0:
                        output_tokens = existing["output_tokens"]
                    # Preserve sender info if new values are None
                    if sender_id is None and existing.get("sender_id"):
                        sender_id = existing["sender_id"]
                    if sender_name is None and existing.get("sender_name"):
                        sender_name = existing["sender_name"]
                    # Preserve model if new value is None and existing value exists
                    if model is None and existing.get("model"):
                        model = existing["model"]

                if is_postgresql():
                    _execute(
                        cursor,
                        """
                        INSERT INTO daily_messages
                        (date, tool_name, host_name, message_id, parent_id, role, content, full_entry, tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name, message_source, feishu_conversation_id, group_subject, is_group_chat, agent_session_id, conversation_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (date, tool_name, host_name, message_id) DO UPDATE SET
                            parent_id = EXCLUDED.parent_id,
                            role = EXCLUDED.role,
                            content = EXCLUDED.content,
                            full_entry = EXCLUDED.full_entry,
                            tokens_used = EXCLUDED.tokens_used,
                            input_tokens = EXCLUDED.input_tokens,
                            output_tokens = EXCLUDED.output_tokens,
                            model = EXCLUDED.model,
                            timestamp = EXCLUDED.timestamp,
                            sender_id = EXCLUDED.sender_id,
                            sender_name = EXCLUDED.sender_name,
                            message_source = EXCLUDED.message_source,
                            feishu_conversation_id = EXCLUDED.feishu_conversation_id,
                            group_subject = EXCLUDED.group_subject,
                            is_group_chat = EXCLUDED.is_group_chat,
                            agent_session_id = EXCLUDED.agent_session_id,
                            conversation_id = EXCLUDED.conversation_id
                    """,
                        (
                            date,
                            tool_name,
                            host_name,
                            message_id,
                            msg.get("parent_id"),
                            msg.get("role"),
                            msg.get("content"),
                            msg.get("full_entry"),
                            tokens_used,
                            input_tokens,
                            output_tokens,
                            model,
                            msg.get("timestamp"),
                            sender_id,
                            sender_name,
                            msg.get("message_source"),
                            msg.get("feishu_conversation_id"),
                            msg.get("group_subject"),
                            msg.get("is_group_chat"),
                            msg.get("agent_session_id"),
                            msg.get("conversation_id"),
                        ),
                    )
                else:
                    # Use INSERT ... ON CONFLICT to preserve created_at on updates
                    _execute(
                        cursor,
                        """
                        INSERT INTO daily_messages
                        (date, tool_name, host_name, message_id, parent_id, role, content, full_entry, tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name, message_source, feishu_conversation_id, group_subject, is_group_chat, agent_session_id, conversation_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date, tool_name, host_name, message_id) DO UPDATE SET
                            parent_id = excluded.parent_id,
                            role = excluded.role,
                            content = excluded.content,
                            full_entry = excluded.full_entry,
                            tokens_used = excluded.tokens_used,
                            input_tokens = excluded.input_tokens,
                            output_tokens = excluded.output_tokens,
                            model = excluded.model,
                            timestamp = excluded.timestamp,
                            sender_id = excluded.sender_id,
                            sender_name = excluded.sender_name,
                            message_source = excluded.message_source,
                            feishu_conversation_id = excluded.feishu_conversation_id,
                            group_subject = excluded.group_subject,
                            is_group_chat = excluded.is_group_chat,
                            agent_session_id = excluded.agent_session_id,
                            conversation_id = excluded.conversation_id
                    """,
                        (
                            date,
                            tool_name,
                            host_name,
                            message_id,
                            msg.get("parent_id"),
                            msg.get("role"),
                            msg.get("content"),
                            msg.get("full_entry"),
                            tokens_used,
                            input_tokens,
                            output_tokens,
                            model,
                            msg.get("timestamp"),
                            sender_id,
                            sender_name,
                            msg.get("message_source"),
                            msg.get("feishu_conversation_id"),
                            msg.get("group_subject"),
                            msg.get("is_group_chat"),
                            msg.get("agent_session_id"),
                            msg.get("conversation_id"),
                        ),
                    )

                saved += 1

            # Commit after each batch
            conn.commit()

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    return saved


def get_messages_by_date(
    date: str,
    tool_name: Optional[str] = None,
    roles: Optional[List[str]] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    host_name: Optional[str] = None,
    sender: Optional[str] = None,
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
    conditions = ["date = ?"]
    params = [date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    if sender:
        conditions.append("(sender_name = ? OR sender_id = ?)")
        params.extend([sender, sender])

    if roles:
        placeholders = ",".join(["?" for _ in roles])
        conditions.append(f"role IN ({placeholders})")
        params.extend(roles)

    if search:
        conditions.append("content LIKE ?")
        params.append(f"%{search}%")

    # Get total count
    where_clause = " AND ".join(conditions)
    _execute(
        cursor,
        f"""
        SELECT COUNT(*) as count FROM daily_messages
        WHERE {where_clause}
    """,
        params,
    )

    total = cursor.fetchone()["count"]
    total_pages = (total + limit - 1) // limit if total > 0 else 1

    # Get paginated messages
    offset = (page - 1) * limit
    _execute(
        cursor,
        f"""
        SELECT * FROM daily_messages
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """,
        params + [limit, offset],
    )

    rows = cursor.fetchall()
    conn.close()

    messages = []
    for row in rows:
        msg = dict(row)
        # Store original content first
        original_content = msg.get("content")
        # Parse content as JSON if possible (for backend processing)
        # Note: Frontend also handles JSON parsing for display
        if original_content:
            try:
                msg["content_parsed"] = json.loads(original_content)
            except (json.JSONDecodeError, TypeError):
                msg["content_parsed"] = original_content
        messages.append(msg)

    return {
        "messages": messages,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_hosts_by_tool(tool_name: str) -> List[str]:
    """Get list of hosts for a specific tool."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT DISTINCT host_name FROM daily_usage
        WHERE tool_name = ?
        ORDER BY host_name
    """,
        (tool_name,),
    )

    rows = cursor.fetchall()
    conn.close()

    return [row["host_name"] for row in rows]


def get_unique_senders(
    date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> List[str]:
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

    conditions = ["date = ?"]
    params = [date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    # Get unique sender_name values, falling back to sender_id if sender_name is null
    # Include records where either sender_name or sender_id is not null
    _execute(
        cursor,
        f"""
        SELECT DISTINCT
            CASE
                WHEN sender_name IS NOT NULL AND sender_name != '' THEN sender_name
                ELSE sender_id
            END as sender
        FROM daily_messages
        WHERE {' AND '.join(conditions)}
          AND (sender_name IS NOT NULL OR sender_id IS NOT NULL)
        ORDER BY sender
    """,
        params,
    )

    rows = cursor.fetchall()
    conn.close()

    # Filter out None values and return unique senders
    senders = [row["sender"] for row in rows if row["sender"]]
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
            dt = datetime.strptime(
                ts.strip(), "%Y-%m-%d %H:%M:%S.%f" if "." in ts else "%Y-%m-%d %H:%M:%S"
            )
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

    id_type = _get_id_type()

    # Create users table
    _execute(
        cursor,
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id {id_type},
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            daily_token_quota INTEGER DEFAULT 1000000,
            monthly_token_quota INTEGER DEFAULT 30000000,
            daily_request_quota INTEGER DEFAULT 1000,
            monthly_request_quota INTEGER DEFAULT 30000,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    )

    # Create web_user_auth_sessions table
    _execute(
        cursor,
        f"""
        CREATE TABLE IF NOT EXISTS web_user_auth_sessions (
            id {id_type},
            user_id INTEGER NOT NULL,
            session_token TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """,
    )

    # Create sessions table for user authentication
    _execute(
        cursor,
        f"""
        CREATE TABLE IF NOT EXISTS sessions (
            id {id_type},
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """,
    )

    # Create quota_usage table
    _execute(
        cursor,
        f"""
        CREATE TABLE IF NOT EXISTS quota_usage (
            id {id_type},
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            tool_name TEXT,
            tokens_used INTEGER DEFAULT 0,
            requests_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """,
    )

    conn.commit()

    # Add linux_account column if not exists (migration for existing databases)
    if not _column_exists(cursor, "users", "linux_account"):
        print("Adding linux_account column to users table...")
        _execute(cursor, "ALTER TABLE users ADD COLUMN linux_account TEXT")
        conn.commit()

    conn.close()
    print("Authentication database initialized")


def create_user(
    username: str,
    password_hash: str,
    email: str = None,
    role: str = "user",
    daily_token_quota: int = 1000000,
    daily_request_quota: int = 1000,
) -> bool:
    """Create a new user."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        _execute(
            cursor,
            """
            INSERT INTO users (username, password_hash, email, role, daily_token_quota, daily_request_quota)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (username, password_hash, email, role, daily_token_quota, daily_request_quota),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def create_user_with_is_active(
    username: str,
    password_hash: str,
    email: str = None,
    role: str = "user",
    daily_token_quota: int = 1000000,
    daily_request_quota: int = 1000,
    is_active: int = 1,
    linux_account: str = None,
) -> bool:
    """Create a new user with is_active flag."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        _execute(
            cursor,
            """
            INSERT INTO users (username, password_hash, email, role, daily_token_quota, daily_request_quota, is_active, linux_account)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                username,
                password_hash,
                email,
                role,
                daily_token_quota,
                daily_request_quota,
                is_active,
                linux_account,
            ),
        )
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

    _execute(cursor, "SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Get user by ID."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(cursor, "SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def is_default_admin_password() -> bool:
    """Check if admin user is using the default password 'admin123'.

    Returns:
        True if admin user exists and has default password, False otherwise.
    """
    import hashlib

    admin_user = get_user_by_username("admin")
    if not admin_user:
        return False

    # Calculate the default password hash
    default_password_hash = hashlib.sha256(b"admin123").hexdigest()

    return admin_user["password_hash"] == default_password_hash


def get_all_users() -> List[Dict]:
    """Get all users (admin only)."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT id, username, email, role, daily_token_quota, daily_request_quota,
               is_active, created_at
        FROM users
        ORDER BY id DESC
    """,
    )
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_global_quota_summary(start_date: str, end_date: str) -> Dict:
    """Get global quota summary within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    # Get total quota allocated to all users
    _execute(
        cursor,
        """
        SELECT COALESCE(SUM(daily_token_quota), 0) as total_quota
        FROM users
    """,
    )
    total_quota = cursor.fetchone()[0] or 0

    # Get total usage within date range
    _execute(
        cursor,
        """
        SELECT COALESCE(SUM(tokens_used), 0) as total_used
        FROM quota_usage
        WHERE date >= ? AND date <= ?
    """,
        (start_date, end_date),
    )
    total_used = cursor.fetchone()[0] or 0

    conn.close()

    return {
        "total_quota": total_quota,
        "total_used": total_used,
        "remaining": total_quota - total_used,
    }


def get_user_quota_breakdown(start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage breakdown by user."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT
            u.id as user_id,
            u.username,
            u.email,
            u.daily_token_quota as quota,
            COALESCE(SUM(q.tokens_used), 0) as used
        FROM users u
        LEFT JOIN quota_usage q ON u.id = q.user_id
            AND q.date >= ? AND q.date <= ?
        GROUP BY u.id, u.username, u.email, u.daily_token_quota
        ORDER BY used DESC
    """,
        (start_date, end_date),
    )

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict["remaining"] = row_dict["quota"] - row_dict["used"]
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
    if password_hash == user["password_hash"]:
        return user
    return None


def create_session(user_id: int, session_token: str, expires_at: datetime) -> bool:
    """Create a new session for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        _execute(
            cursor,
            """
            INSERT INTO web_user_auth_sessions (user_id, session_token, expires_at)
            VALUES (?, ?, ?)
        """,
            (user_id, session_token, expires_at),
        )
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

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _execute(
        cursor,
        """
        SELECT s.*, u.* FROM web_user_auth_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_token = ? AND s.expires_at > ?
    """,
        (session_token, now),
    )

    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def delete_session(session_token: str) -> bool:
    """Delete a session."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(cursor, "DELETE FROM web_user_auth_sessions WHERE session_token = ?", (session_token,))
    conn.commit()
    conn.close()
    return True


def get_all_users() -> List[Dict]:
    """Get all users (for admin)."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(cursor, "SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def update_user(user_id: int, **kwargs) -> bool:
    """Update user information."""
    allowed_fields = [
        "email",
        "role",
        "daily_token_quota",
        "monthly_token_quota",
        "daily_request_quota",
        "monthly_request_quota",
        "is_active",
        "linux_account",
    ]
    updates = []
    params = []

    for field, value in kwargs.items():
        if field in allowed_fields:
            updates.append(f"{field} = ?")
            params.append(value)

    if not updates:
        return False

    params.append(user_id)

    conn = get_connection()
    cursor = conn.cursor()
    _execute(
        cursor,
        f"""
        UPDATE users SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?
    """,
        params,
    )
    conn.commit()
    conn.close()
    return True


def update_user_password(user_id: int, password_hash: str) -> bool:
    """Update user password hash."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
    """,
        (password_hash, user_id),
    )
    conn.commit()
    conn.close()
    return True


def delete_user(user_id: int) -> bool:
    """Delete a user (admin only)."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(cursor, "DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True


def save_quota_usage(
    user_id: int, date: str, tool_name: str = None, tokens_used: int = 0, requests_used: int = 0
) -> bool:
    """Save quota usage for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        INSERT INTO quota_usage (user_id, date, tool_name, tokens_used, requests_used)
        VALUES (?, ?, ?, ?, ?)
    """,
        (user_id, date, tool_name, tokens_used, requests_used),
    )
    conn.commit()
    conn.close()
    return True


def aggregate_quota_usage_from_messages(start_date: str = None, end_date: str = None) -> int:
    """Aggregate quota usage from daily_messages table.

    This function aggregates token and request usage from daily_messages
    and populates the quota_usage table. It matches sender_name to users
    by username.

    The aggregation strategy:
    1. Count user messages (role='user') as requests
    2. Sum tokens from assistant messages that are responses to user messages
       (linked via parent_id chain)

    Args:
        start_date: Optional start date in YYYY-MM-DD format. If None, processes all data.
        end_date: Optional end date in YYYY-MM-DD format. If None, processes all data.

    Returns:
        Number of quota_usage records created.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Build date filter conditions
    date_conditions = []
    params = []

    if start_date:
        date_conditions.append("date >= ?")
        params.append(start_date)

    if end_date:
        date_conditions.append("date <= ?")
        params.append(end_date)

    date_clause = " AND ".join(date_conditions) if date_conditions else "1=1"

    # Step 1: Get all user messages with their sender info
    _execute(
        cursor,
        f"""
        SELECT
            date,
            tool_name,
            message_id,
            COALESCE(sender_name, sender_id) as sender
        FROM daily_messages
        WHERE role = 'user'
          AND (sender_name IS NOT NULL OR sender_id IS NOT NULL)
          AND {date_clause}
    """,
        params,
    )

    user_messages = cursor.fetchall()

    # Build a map: user_message_id -> (date, tool_name, sender)
    user_msg_map = {}
    for msg in user_messages:
        user_msg_map[msg["message_id"]] = {
            "date": msg["date"],
            "tool_name": msg["tool_name"],
            "sender": msg["sender"],
        }

    # Step 2: Get all assistant messages and their tokens
    _execute(
        cursor,
        f"""
        SELECT
            date,
            tool_name,
            message_id,
            parent_id,
            tokens_used,
            input_tokens,
            output_tokens
        FROM daily_messages
        WHERE role = 'assistant'
          AND {date_clause}
    """,
        params,
    )

    assistant_messages = cursor.fetchall()

    # Build a map: assistant_message_id -> (parent_id, tokens)
    # And track which user each assistant message belongs to
    assistant_map = {}
    for msg in assistant_messages:
        assistant_map[msg["message_id"]] = {
            "parent_id": msg["parent_id"],
            "tokens_used": msg["tokens_used"] or 0,
            "input_tokens": msg["input_tokens"] or 0,
            "output_tokens": msg["output_tokens"] or 0,
            "date": msg["date"],
            "tool_name": msg["tool_name"],
        }

    # Step 2.5: Get all messages (including toolResult) for parent_id chain lookup
    _execute(
        cursor,
        f"""
        SELECT message_id, parent_id
        FROM daily_messages
        WHERE {date_clause}
    """,
        params,
    )

    all_messages = cursor.fetchall()
    # Build a map: message_id -> parent_id for chain lookup
    parent_map = {msg["message_id"]: msg["parent_id"] for msg in all_messages}

    # Step 3: For each assistant message, find the user it belongs to
    # by following the parent_id chain
    def find_user_for_assistant(assistant_msg_id):
        """Find the user message that this assistant message is responding to.

        Follows the parent_id chain through any message type (assistant, toolResult, etc.)
        to find the originating user message.
        """
        if assistant_msg_id not in assistant_map:
            return None

        assistant = assistant_map[assistant_msg_id]
        parent_id = assistant["parent_id"]

        if not parent_id:
            return None

        # Check if parent is a user message
        if parent_id in user_msg_map:
            return user_msg_map[parent_id]

        # Follow the parent_id chain through any message type (assistant, toolResult, etc.)
        # Max depth of 30 to prevent infinite loops
        current_id = parent_id
        for _ in range(30):
            if current_id in user_msg_map:
                return user_msg_map[current_id]
            if current_id not in parent_map:
                return None
            current_id = parent_map[current_id]
            if not current_id:
                return None

        return None

    # Step 4: Aggregate tokens by (date, tool_name, sender)
    usage_data = {}  # (date, tool_name, sender) -> {tokens, requests}

    # Count requests from user messages
    for msg_id, msg_info in user_msg_map.items():
        key = (msg_info["date"], msg_info["tool_name"], msg_info["sender"])
        if key not in usage_data:
            usage_data[key] = {"tokens": 0, "requests": 0}
        usage_data[key]["requests"] += 1

    # Sum tokens from assistant messages
    for assistant_msg_id in assistant_map:
        user_msg = find_user_for_assistant(assistant_msg_id)
        if user_msg:
            key = (user_msg["date"], user_msg["tool_name"], user_msg["sender"])
            if key not in usage_data:
                usage_data[key] = {"tokens": 0, "requests": 0}
            # Use tokens_used for user quota (total tokens including input + output + cache)
            assistant = assistant_map[assistant_msg_id]
            usage_data[key]["tokens"] += assistant["tokens_used"]

    # Step 5: Get all users for matching
    _execute(cursor, "SELECT id, username FROM users")
    users = {row["username"]: row["id"] for row in cursor.fetchall()}

    # Step 6: Clear existing quota_usage data for the date range
    if start_date and end_date:
        _execute(
            cursor, "DELETE FROM quota_usage WHERE date >= ? AND date <= ?", (start_date, end_date)
        )
    elif start_date:
        _execute(cursor, "DELETE FROM quota_usage WHERE date >= ?", (start_date,))
    elif end_date:
        _execute(cursor, "DELETE FROM quota_usage WHERE date <= ?", (end_date,))
    else:
        _execute(cursor, "DELETE FROM quota_usage")

    # Step 7: Insert aggregated data into quota_usage
    records_created = 0
    for (date, tool_name, sender), data in usage_data.items():
        # Try to match sender to a user
        user_id = users.get(sender)

        if user_id:
            _execute(
                cursor,
                """
                INSERT INTO quota_usage (user_id, date, tool_name, tokens_used, requests_used)
                VALUES (?, ?, ?, ?, ?)
            """,
                (user_id, date, tool_name, data["tokens"], data["requests"]),
            )
            records_created += 1

    conn.commit()
    conn.close()

    return records_created


def get_quota_usage(user_id: int, start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage for a user within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT * FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ?
        ORDER BY date DESC
    """,
        (user_id, start_date, end_date),
    )

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_total_quota_usage(user_id: int, start_date: str, end_date: str) -> Dict:
    """Get total quota usage for a user within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT
            COALESCE(SUM(tokens_used), 0) as total_tokens,
            COALESCE(SUM(requests_used), 0) as total_requests
        FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ?
    """,
        (user_id, start_date, end_date),
    )

    row = cursor.fetchone()
    conn.close()

    return (
        {"total_tokens": row["total_tokens"], "total_requests": row["total_requests"]}
        if row
        else {"total_tokens": 0, "total_requests": 0}
    )


def get_quota_usage_by_tool(user_id: int, start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage grouped by tool for a user."""
    conn = get_connection()
    cursor = conn.cursor()

    _execute(
        cursor,
        """
        SELECT
            tool_name,
            SUM(tokens_used) as total_tokens,
            SUM(requests_used) as total_requests,
            COUNT(*) as days_used
        FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ? AND tool_name IS NOT NULL
        GROUP BY tool_name
        ORDER BY total_tokens DESC
    """,
        (user_id, start_date, end_date),
    )

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_quota_usage_by_day(user_id: int, start_date: str, end_date: str) -> List[Dict]:
    """Get quota usage grouped by day for a user.

    Returns data from the first day with usage to the end_date,
    filling in zero values for days with no usage.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # First, get the earliest date with usage for this user
    _execute(
        cursor,
        """
        SELECT MIN(date) as first_date
        FROM quota_usage
        WHERE user_id = ? AND tokens_used > 0
    """,
        (user_id,),
    )

    row = cursor.fetchone()
    first_usage_date = row["first_date"] if row else None

    # If no usage data, return empty list
    if not first_usage_date:
        conn.close()
        return []

    # Get daily usage data
    _execute(
        cursor,
        """
        SELECT
            date,
            SUM(tokens_used) as total_tokens,
            SUM(requests_used) as total_requests
        FROM quota_usage
        WHERE user_id = ? AND date >= ? AND date <= ?
        GROUP BY date
        ORDER BY date ASC
    """,
        (user_id, first_usage_date, end_date),
    )

    rows = cursor.fetchall()
    conn.close()

    # Create a dictionary for quick lookup
    usage_by_date = {
        row["date"]: {"total_tokens": row["total_tokens"], "total_requests": row["total_requests"]}
        for row in rows
    }

    # Fill in all dates from first_usage_date to end_date
    result = []
    current_date = datetime.strptime(first_usage_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    while current_date <= end:
        date_str = current_date.strftime("%Y-%m-%d")
        usage = usage_by_date.get(date_str, {"total_tokens": 0, "total_requests": 0})
        result.append(
            {
                "date": date_str,
                "total_tokens": usage["total_tokens"],
                "total_requests": usage["total_requests"],
            }
        )
        current_date += timedelta(days=1)

    return result


# =============================================================================
# Analysis Module - 深度分析查询函数
# =============================================================================


def get_hourly_usage_from_messages(
    start_date: str, end_date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> List[Dict]:
    """Get hourly usage statistics from daily_messages table.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter

    Returns:
        List of dicts with hour (0-23 in UTC+8/CST), day_of_week (0-6), tokens_used, message_count
        Note: day_of_week uses SQLite strftime('%w'): 0=Sunday, 1=Monday, ..., 6=Saturday
        Times are converted from UTC to CST (UTC+8).
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["date >= ?", "date <= ?", "timestamp IS NOT NULL"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    # Extract hour from timestamp and calculate day of week
    # SQLite doesn't have native day of week, use strftime('%w')
    _execute(
        cursor,
        f"""
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
    """,
        params,
    )

    rows = cursor.fetchall()
    conn.close()

    # Convert UTC hour to CST (UTC+8) and aggregate by new (day_of_week, hour)
    # Use a dict to aggregate duplicates created by timezone conversion
    aggregated = {}
    for row in rows:
        utc_hour = row["hour"]
        cst_hour = (utc_hour + 8) % 24
        day_of_week = row["day_of_week"]

        # If hour overflowed to next day (UTC hour >= 16), adjust day_of_week
        if utc_hour >= 16:
            day_of_week = (day_of_week + 1) % 7

        key = (day_of_week, cst_hour)
        if key not in aggregated:
            aggregated[key] = {
                "hour": cst_hour,
                "day_of_week": day_of_week,
                "tokens_used": 0,
                "message_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }

        aggregated[key]["tokens_used"] += row["tokens_used"]
        aggregated[key]["message_count"] += row["message_count"]
        aggregated[key]["input_tokens"] += row["input_tokens"]
        aggregated[key]["output_tokens"] += row["output_tokens"]

    # Convert to list and sort
    results = list(aggregated.values())
    results.sort(key=lambda x: (x["day_of_week"], x["hour"]))

    return results


def get_daily_hourly_usage(
    start_date: str, end_date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> List[Dict]:
    """Get hourly usage statistics grouped by date (not day_of_week).

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter

    Returns:
        List of dicts with date, hour (0-23 in CST), tokens_used, message_count
        Times are converted from UTC to CST (UTC+8).
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["date >= ?", "date <= ?", "timestamp IS NOT NULL"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    # Extract hour from timestamp and keep date
    _execute(
        cursor,
        f"""
        SELECT
            date,
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            SUM(tokens_used) as tokens_used,
            COUNT(*) as message_count,
            SUM(input_tokens) as input_tokens,
            SUM(output_tokens) as output_tokens
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY date, hour
        ORDER BY date, hour
    """,
        params,
    )

    rows = cursor.fetchall()
    conn.close()

    # Convert UTC hour to CST (UTC+8) and aggregate by new (date, hour)
    aggregated = {}
    for row in rows:
        utc_hour = row["hour"]
        cst_hour = (utc_hour + 8) % 24
        date = row["date"]

        # If hour overflowed to next day (UTC hour >= 16), adjust date
        if utc_hour >= 16:
            from datetime import datetime, timedelta

            date_obj = datetime.strptime(date, "%Y-%m-%d")
            date_obj = date_obj + timedelta(days=1)
            date = date_obj.strftime("%Y-%m-%d")

        key = (date, cst_hour)
        if key not in aggregated:
            aggregated[key] = {
                "date": date,
                "hour": cst_hour,
                "tokens_used": 0,
                "message_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }

        aggregated[key]["tokens_used"] += row["tokens_used"]
        aggregated[key]["message_count"] += row["message_count"]
        aggregated[key]["input_tokens"] += row["input_tokens"]
        aggregated[key]["output_tokens"] += row["output_tokens"]

    # Convert to list and sort
    results = list(aggregated.values())
    results.sort(key=lambda x: (x["date"], x["hour"]))

    return results


def get_user_activity_ranking(
    start_date: str,
    end_date: str,
    limit: int = 10,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None,
) -> List[Dict]:
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

    conditions = ["date >= ?", "date <= ?", "(sender_id IS NOT NULL OR sender_name IS NOT NULL)"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    _execute(
        cursor,
        f"""
        SELECT
            COALESCE(sender_name, sender_id) as sender_name,
            MAX(sender_id) as sender_id,
            COUNT(*) as message_count,
            SUM(tokens_used) as tokens_used,
            COUNT(DISTINCT date) as active_days,
            MIN(date) as first_active_date,
            MAX(date) as last_active_date
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY COALESCE(sender_name, sender_id)
        ORDER BY tokens_used DESC
        LIMIT ?
    """,
        params + [limit],
    )

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_conversation_statistics(
    start_date: str, end_date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> Dict:
    """Get conversation statistics.

    Analyzes conversation patterns based on conversation_id field.
    A conversation is one round of dialogue (user message → AI complete).

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter

    Returns:
        Dict with conversation statistics including:
        - total_messages: Total message count
        - unique_conversations: Count of unique conversations
        - single_turn: Single-turn conversations count
        - multi_turn_ratio: Ratio of multi-turn conversations
        - message_stats: Message counts by role (user, assistant, toolResult, error)
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["date >= ?", "date <= ?"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    # Get total messages and messages with parent_id (replies)
    _execute(
        cursor,
        f"""
        SELECT
            COUNT(*) as total_messages,
            SUM(CASE WHEN parent_id IS NOT NULL THEN 1 ELSE 0 END) as reply_messages,
            COUNT(DISTINCT conversation_id) as unique_conversations
        FROM daily_messages
        WHERE {where_clause}
    """,
        params,
    )

    row = cursor.fetchone()

    # Calculate conversation length distribution
    _execute(
        cursor,
        f"""
        SELECT
            COUNT(*) as conversation_count,
            AVG(conv_length) as avg_length,
            MIN(conv_length) as min_length,
            MAX(conv_length) as max_length
        FROM (
            SELECT conversation_id, COUNT(*) as conv_length
            FROM daily_messages
            WHERE {where_clause} AND conversation_id IS NOT NULL
            GROUP BY conversation_id
        )
    """,
        params,
    )

    conv_row = cursor.fetchone()

    # Get message counts by role
    _execute(
        cursor,
        f"""
        SELECT
            role,
            COUNT(*) as count
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY role
    """,
        params,
    )

    role_rows = cursor.fetchall()
    message_stats = {}
    for role_row in role_rows:
        message_stats[role_row["role"]] = role_row["count"]

    conn.close()

    total = row["total_messages"] if row else 0
    replies = row["reply_messages"] if row else 0
    conversations = row["unique_conversations"] if row else 0

    return {
        "total_messages": total,
        "reply_messages": replies,
        "unique_conversations": conversations,
        "single_turn": total - replies,
        "multi_turn_ratio": round(replies / total, 3) if total > 0 else 0,
        "conversation_stats": {
            "count": conv_row["conversation_count"] if conv_row else 0,
            "avg_length": (
                round(conv_row["avg_length"], 2) if conv_row and conv_row["avg_length"] else 0
            ),
            "min_length": conv_row["min_length"] if conv_row else 0,
            "max_length": conv_row["max_length"] if conv_row else 0,
        },
        "message_stats": message_stats,
    }


def get_peak_usage_periods(
    start_date: str,
    end_date: str,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None,
    limit: int = 10,
) -> List[Dict]:
    """Get peak usage periods.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter
        limit: Number of peak periods to return

    Returns:
        List of dicts with date, hour (in UTC+8/CST), tokens_used, message_count
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["date >= ?", "date <= ?", "timestamp IS NOT NULL"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    _execute(
        cursor,
        f"""
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
    """,
        params + [limit],
    )

    rows = cursor.fetchall()
    conn.close()

    # Convert UTC hour to CST (UTC+8)
    results = []
    for row in rows:
        result = dict(row)
        utc_hour = result["hour"]
        cst_hour = (utc_hour + 8) % 24
        result["hour"] = cst_hour

        # If hour overflowed to next day (UTC hour >= 16), adjust date
        if utc_hour >= 16:
            from datetime import datetime, timedelta

            try:
                dt = datetime.strptime(result["date"], "%Y-%m-%d")
                result["date"] = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                pass  # Keep original date if parsing fails

        results.append(result)

    return results


def get_user_segmentation(
    start_date: str, end_date: str, tool_name: Optional[str] = None, host_name: Optional[str] = None
) -> Dict:
    """Get user segmentation by activity level.

    Segments:
    - high: > 10K tokens
    - medium: 1K - 10K tokens
    - low: < 1K tokens
    - dormant: no activity in last 7 days

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter

    Returns:
        Dict with user counts for each segment
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get user usage for the specified date range
    conditions = ["date >= ?", "date <= ?", "(sender_id IS NOT NULL OR sender_name IS NOT NULL)"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    _execute(
        cursor,
        f"""
        SELECT
            COALESCE(sender_name, sender_id) as sender,
            SUM(tokens_used) as tokens_used
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY COALESCE(sender_name, sender_id)
    """,
        params,
    )

    rows = cursor.fetchall()

    # Calculate segments
    high_users = 0
    medium_users = 0
    low_users = 0

    for row in rows:
        tokens = row["tokens_used"] or 0
        if tokens > 10000:
            high_users += 1
        elif tokens >= 1000:
            medium_users += 1
        else:
            low_users += 1

    # Get dormant users (active in past 30 days but not in selected period)
    from datetime import datetime, timedelta

    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    period_days = (end_date_obj - start_date_obj).days + 1
    thirty_days_ago = (end_date_obj - timedelta(days=30)).strftime("%Y-%m-%d")

    _execute(
        cursor,
        """
        SELECT COUNT(DISTINCT COALESCE(sender_name, sender_id)) as dormant_count
        FROM daily_messages
        WHERE date >= ? AND date < ? AND (sender_id IS NOT NULL OR sender_name IS NOT NULL)
    """,
        [thirty_days_ago, start_date],
    )

    dormant_row = cursor.fetchone()
    dormant_users = dormant_row["dormant_count"] if dormant_row else 0

    conn.close()

    return {
        "high": high_users,
        "medium": medium_users,
        "low": low_users,
        "dormant": dormant_users,
        "total_active": high_users + medium_users + low_users,
    }


def get_tool_comparison_metrics(
    start_date: str, end_date: str, host_name: Optional[str] = None
) -> List[Dict]:
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

    conditions = ["date >= ?", "date <= ?"]
    params = [start_date, end_date]

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    # Get usage metrics from daily_usage
    _execute(
        cursor,
        f"""
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
    """,
        params,
    )

    usage_rows = cursor.fetchall()

    # Get message metrics from daily_messages
    _execute(
        cursor,
        f"""
        SELECT 
            tool_name,
            COUNT(*) as total_messages,
            COUNT(DISTINCT COALESCE(sender_name, sender_id)) as unique_users,
            AVG(tokens_used) as avg_tokens_per_message
        FROM daily_messages
        WHERE {where_clause}
        GROUP BY tool_name
    """,
        params,
    )

    message_rows = cursor.fetchall()
    message_data = {row["tool_name"]: dict(row) for row in message_rows}

    conn.close()

    results = []
    for row in usage_rows:
        result = dict(row)
        msg_data = message_data.get(row["tool_name"], {})
        result["total_messages"] = msg_data.get("total_messages", 0)
        result["unique_users"] = msg_data.get("unique_users", 0)
        result["avg_tokens_per_message"] = round(msg_data.get("avg_tokens_per_message", 0), 2)
        results.append(result)

    return results


def detect_usage_anomalies(
    start_date: str,
    end_date: str,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None,
    threshold_std: float = 3.0,
) -> List[Dict]:
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

    conditions = ["date >= ?", "date <= ?"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    # Get daily usage statistics
    _execute(
        cursor,
        f"""
        SELECT 
            date,
            tool_name,
            tokens_used,
            request_count
        FROM daily_usage
        WHERE {where_clause}
        ORDER BY date
    """,
        params,
    )

    rows = cursor.fetchall()

    if not rows:
        conn.close()
        return []

    # Calculate mean and std for each tool
    import math
    from collections import defaultdict

    tool_data = defaultdict(list)
    for row in rows:
        tool_data[row["tool_name"]].append(
            {
                "date": row["date"],
                "tokens_used": row["tokens_used"],
                "request_count": row["request_count"] or 0,
            }
        )

    anomalies = []

    for tool_name, data in tool_data.items():
        if len(data) < 3:  # Need at least 3 data points
            continue

        tokens = [d["tokens_used"] for d in data]
        mean = sum(tokens) / len(tokens)
        variance = sum((x - mean) ** 2 for x in tokens) / len(tokens)
        std = math.sqrt(variance)

        if std == 0:
            continue

        # Detect anomalies
        for d in data:
            z_score = (d["tokens_used"] - mean) / std
            if abs(z_score) > threshold_std:
                anomalies.append(
                    {
                        "date": d["date"],
                        "tool_name": tool_name,
                        "tokens_used": d["tokens_used"],
                        "mean": round(mean, 2),
                        "std": round(std, 2),
                        "z_score": round(z_score, 2),
                        "anomaly_type": "spike" if z_score > 0 else "drop",
                        "severity": "high" if abs(z_score) > 4 else "medium",
                    }
                )

    conn.close()

    # Sort by severity and z_score magnitude
    anomalies.sort(key=lambda x: (0 if x["severity"] == "high" else 1, -abs(x["z_score"])))

    return anomalies


def get_key_metrics(
    start_date: str,
    end_date: Optional[str] = None,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None,
) -> Dict:
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
    usage_conditions = ["date >= ?", "date <= ?"]
    usage_params = [start_date, end_date]

    if tool_name:
        usage_conditions.append("tool_name = ?")
        usage_params.append(tool_name)

    if host_name:
        usage_conditions.append("host_name = ?")
        usage_params.append(host_name)

    usage_where = " AND ".join(usage_conditions)

    # Get usage metrics for the date range
    _execute(
        cursor,
        f"""
        SELECT
            SUM(tokens_used) as total_tokens,
            SUM(request_count) as total_requests,
            COUNT(DISTINCT tool_name) as active_tools,
            COUNT(DISTINCT date) as active_days
        FROM daily_usage
        WHERE {usage_where}
    """,
        usage_params,
    )

    usage_row = cursor.fetchone()

    # Get message metrics for the date range
    _execute(
        cursor,
        f"""
        SELECT
            COUNT(*) as total_messages,
            COUNT(DISTINCT COALESCE(sender_name, sender_id)) as active_users
        FROM daily_messages
        WHERE {usage_where} AND (sender_id IS NOT NULL OR sender_name IS NOT NULL)
    """,
        usage_params,
    )

    message_row = cursor.fetchone()

    # Get previous period usage for comparison (same length as current period)
    from datetime import datetime, timedelta

    start_obj = datetime.strptime(start_date, "%Y-%m-%d")
    end_obj = datetime.strptime(end_date, "%Y-%m-%d")
    period_days = (end_obj - start_obj).days + 1

    prev_end = (start_obj - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_start = (start_obj - timedelta(days=period_days)).strftime("%Y-%m-%d")

    prev_conditions = ["date >= ?", "date <= ?"]
    prev_params = [prev_start, prev_end]

    if tool_name:
        prev_conditions.append("tool_name = ?")
        prev_params.append(tool_name)

    if host_name:
        prev_conditions.append("host_name = ?")
        prev_params.append(host_name)

    _execute(
        cursor,
        f"""
        SELECT SUM(tokens_used) as prev_tokens
        FROM daily_usage
        WHERE {' AND '.join(prev_conditions)}
    """,
        prev_params,
    )

    prev_row = cursor.fetchone()
    prev_tokens = prev_row["prev_tokens"] if prev_row and prev_row["prev_tokens"] else 0

    conn.close()

    current_tokens = usage_row["total_tokens"] or 0
    period_change = ((current_tokens - prev_tokens) / prev_tokens * 100) if prev_tokens > 0 else 0

    return {
        "total_tokens": current_tokens,
        "total_requests": usage_row["total_requests"] or 0,
        "active_tools": usage_row["active_tools"] or 0,
        "active_days": usage_row["active_days"] or 0,
        "total_messages": message_row["total_messages"] or 0,
        "active_users": message_row["active_users"] or 0,
        "period_change": round(period_change, 2),
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
    _execute(
        cursor,
        """
        SELECT MAX(created_at) as last_updated
        FROM daily_usage
        WHERE host_name = ?
    """,
        [host_name],
    )
    usage_row = cursor.fetchone()
    last_updated_usage = usage_row["last_updated"] if usage_row else None

    # Get last update time from daily_messages
    _execute(
        cursor,
        """
        SELECT MAX(created_at) as last_updated
        FROM daily_messages
        WHERE host_name = ?
    """,
        [host_name],
    )
    message_row = cursor.fetchone()
    last_updated_messages = message_row["last_updated"] if message_row else None

    # Use the most recent update time
    last_updated = last_updated_usage
    if last_updated_messages:
        if not last_updated or last_updated_messages > last_updated:
            last_updated = last_updated_messages

    # Get record counts
    _execute(
        cursor,
        """
        SELECT COUNT(*) as count
        FROM daily_usage
        WHERE host_name = ?
    """,
        [host_name],
    )
    usage_count = cursor.fetchone()["count"] or 0

    _execute(
        cursor,
        """
        SELECT COUNT(*) as count
        FROM daily_messages
        WHERE host_name = ?
    """,
        [host_name],
    )
    message_count = cursor.fetchone()["count"] or 0

    # Get date range
    _execute(
        cursor,
        """
        SELECT MIN(date) as min_date, MAX(date) as max_date
        FROM daily_usage
        WHERE host_name = ?
    """,
        [host_name],
    )
    date_row = cursor.fetchone()

    _execute(
        cursor,
        """
        SELECT MIN(date) as min_date, MAX(date) as max_date
        FROM daily_messages
        WHERE host_name = ?
    """,
        [host_name],
    )
    msg_date_row = cursor.fetchone()

    # Combine date ranges
    min_dates = [date_row["min_date"], msg_date_row["min_date"]]
    max_dates = [date_row["max_date"], msg_date_row["max_date"]]
    min_dates = [d for d in min_dates if d]
    max_dates = [d for d in max_dates if d]

    conn.close()

    return {
        "host_name": host_name,
        "last_updated": last_updated,
        "usage_records": usage_count,
        "message_records": message_count,
        "date_range": {
            "start": min(min_dates) if min_dates else None,
            "end": max(max_dates) if max_dates else None,
        },
    }


def get_all_hosts_with_status() -> List[Dict]:
    """Get data status for all hosts in the database.

    Returns:
        List of dicts with host status information
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get all unique host names from both tables
    _execute(
        cursor,
        """
        SELECT DISTINCT host_name FROM daily_usage
        UNION
        SELECT DISTINCT host_name FROM daily_messages
    """,
    )
    hosts = [row["host_name"] for row in cursor.fetchall()]

    results = []

    for host_name in hosts:
        # Get last update time from daily_usage
        _execute(
            cursor,
            """
            SELECT MAX(created_at) as last_updated
            FROM daily_usage
            WHERE host_name = ?
        """,
            [host_name],
        )
        usage_row = cursor.fetchone()
        last_updated_usage = usage_row["last_updated"] if usage_row else None

        # Get last update time from daily_messages
        _execute(
            cursor,
            """
            SELECT MAX(created_at) as last_updated
            FROM daily_messages
            WHERE host_name = ?
        """,
            [host_name],
        )
        message_row = cursor.fetchone()
        last_updated_messages = message_row["last_updated"] if message_row else None

        # Use the most recent update time
        last_updated = last_updated_usage
        if last_updated_messages:
            if not last_updated or last_updated_messages > last_updated:
                last_updated = last_updated_messages

        # Get record counts
        _execute(
            cursor,
            """
            SELECT COUNT(*) as count
            FROM daily_usage
            WHERE host_name = ?
        """,
            [host_name],
        )
        usage_count = cursor.fetchone()["count"] or 0

        _execute(
            cursor,
            """
            SELECT COUNT(*) as count
            FROM daily_messages
            WHERE host_name = ?
        """,
            [host_name],
        )
        message_count = cursor.fetchone()["count"] or 0

        results.append(
            {
                "host_name": host_name,
                "last_updated": last_updated,
                "usage_records": usage_count,
                "message_records": message_count,
            }
        )

    conn.close()

    # Sort by last_updated (most recent first)
    results.sort(key=lambda x: x.get("last_updated") or "", reverse=True)

    return results


# =============================================================================
# Session History Module - 会话历史查询函数
# =============================================================================


def get_conversation_history(
    start_date: str,
    end_date: str,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    sort_by: str = "start_time",
    sort_order: str = "desc",
) -> Dict:
    """Get agent session history with pagination and sorting.

    An agent session is identified by agent_session_id field (tool process session).
    For messages without agent_session_id, we group by (sender_id/sender_name, date)
    and order by timestamp to form sessions.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        tool_name: Optional tool name filter
        host_name: Optional host name filter
        page: Page number (1-indexed)
        limit: Number of results per page
        sort_by: Field to sort by (session_id, user, model, start_time, end_time,
                 user_messages, ai_messages, avg_latency)
        sort_order: Sort order (asc or desc)

    Returns:
        Dict with 'sessions' (list), 'total' (int), 'page', 'limit', 'total_pages'
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Build WHERE conditions
    conditions = ["date >= ?", "date <= ?"]
    params = [start_date, end_date]

    if tool_name:
        conditions.append("tool_name = ?")
        params.append(tool_name)

    if host_name:
        conditions.append("host_name = ?")
        params.append(host_name)

    where_clause = " AND ".join(conditions)

    # First, get all messages ordered by timestamp to identify sessions
    # We use agent_session_id as primary session identifier, fallback to sender+date grouping
    _execute(
        cursor,
        f"""
        SELECT
            id,
            date,
            tool_name,
            host_name,
            message_id,
            parent_id,
            role,
            content,
            tokens_used,
            model,
            timestamp,
            sender_id,
            sender_name,
            agent_session_id,
            conversation_id
        FROM daily_messages
        WHERE {where_clause}
        ORDER BY timestamp ASC
    """,
        params,
    )

    rows = cursor.fetchall()

    # Group messages into sessions
    # Strategy:
    # - Group by agent_session_id if available
    # - Fallback to (sender, date, tool_name) for older data
    sessions = {}
    session_counter = 0

    for row in rows:
        row_dict = dict(row)
        sender = row_dict.get("sender_name") or row_dict.get("sender_id") or "Unknown"
        timestamp = row_dict.get("timestamp")
        agent_session_id = row_dict.get("agent_session_id")

        # Use agent_session_id as primary session identifier
        # Fallback to sender + date + tool_name for older data without agent_session_id
        if agent_session_id:
            session_id = agent_session_id
        else:
            session_key = f"{sender}_{row_dict['date']}_{row_dict['tool_name']}"
            session_id = session_key

        if session_id not in sessions:
            session_counter += 1
            sessions[session_id] = {
                "session_id": session_id,
                "session_index": session_counter,
                "user": sender,
                "sender_id": row_dict.get("sender_id"),
                "sender_name": row_dict.get("sender_name"),
                "models": set(),
                "start_time": None,
                "end_time": None,
                "user_messages": 0,
                "ai_messages": 0,
                "total_tokens": 0,
                "messages": [],  # Store all messages for timeline/latency calculation
                "tool_name": row_dict.get("tool_name"),
                "host_name": row_dict.get("host_name"),
                "date": row_dict.get("date"),
            }

        session = sessions[session_id]

        # Update session info
        if row_dict.get("model"):
            session["models"].add(row_dict["model"])

        # Track timestamps
        if timestamp:
            if session["start_time"] is None or timestamp < session["start_time"]:
                session["start_time"] = timestamp
            if session["end_time"] is None or timestamp > session["end_time"]:
                session["end_time"] = timestamp

        # Count messages by role
        role = row_dict.get("role", "").lower()
        if role == "user":
            session["user_messages"] += 1
        elif role == "assistant":
            session["ai_messages"] += 1

        # Sum tokens
        session["total_tokens"] += row_dict.get("tokens_used") or 0

        # Store message for timeline/latency calculation
        session["messages"].append(
            {
                "timestamp": timestamp,
                "role": role,
                "tokens": row_dict.get("tokens_used") or 0,
                "conversation_id": row_dict.get("conversation_id"),
            }
        )

    # Calculate average and max latency for each session
    for session_id, session in sessions.items():
        session["models"] = list(session["models"])
        session["avg_latency"] = _calculate_avg_latency(session["messages"])
        session["max_latency"] = _calculate_max_latency(session["messages"])
        # Remove messages list from final output (too much data)
        del session["messages"]

    # Convert to list for sorting and pagination
    sessions_list = list(sessions.values())

    # Sort sessions
    sort_field_map = {
        "session_id": "session_id",
        "user": "user",
        "model": "models",
        "start_time": "start_time",
        "end_time": "end_time",
        "user_messages": "user_messages",
        "ai_messages": "ai_messages",
        "avg_latency": "avg_latency",
        "max_latency": "max_latency",
    }

    sort_field = sort_field_map.get(sort_by, "start_time")

    def get_sort_key(session):
        val = session.get(sort_field)
        if val is None:
            # Handle None values
            if sort_field in ["user_messages", "ai_messages", "avg_latency", "max_latency"]:
                return 0
            return ""
        # For models field, use first model or empty string
        if sort_field == "models":
            return val[0] if val else ""
        return val

    sessions_list.sort(key=get_sort_key, reverse=(sort_order == "desc"))

    # Paginate
    total = len(sessions_list)
    total_pages = (total + limit - 1) // limit if total > 0 else 1
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_sessions = sessions_list[start_idx:end_idx]

    conn.close()

    return {
        "sessions": paginated_sessions,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def _calculate_avg_latency(messages: List[Dict]) -> float:
    """Calculate average AI response latency from messages.

    Latency is calculated as the time difference between a user message
    and the following assistant message.

    Args:
        messages: List of message dicts with 'timestamp' and 'role' keys

    Returns:
        Average latency in seconds, or 0 if cannot be calculated
    """
    if not messages or len(messages) < 2:
        return 0.0

    # Sort messages by timestamp
    sorted_msgs = sorted([m for m in messages if m.get("timestamp")], key=lambda x: x["timestamp"])

    latencies = []
    prev_user_time = None

    for msg in sorted_msgs:
        timestamp_str = msg.get("timestamp")
        role = msg.get("role", "")

        if not timestamp_str:
            continue

        try:
            # Parse timestamp
            if "T" in timestamp_str:
                ts = timestamp_str.replace("Z", "")
                if "." in ts:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            else:
                if "." in timestamp_str:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue

        if role == "user":
            prev_user_time = msg_time
        elif role == "assistant" and prev_user_time is not None:
            latency = (msg_time - prev_user_time).total_seconds()
            if latency > 0:  # Only count positive latencies
                latencies.append(latency)
            prev_user_time = None  # Reset after calculating

    if not latencies:
        return 0.0

    return round(sum(latencies) / len(latencies), 2)


def _calculate_max_latency(messages: List[Dict]) -> float:
    """Calculate maximum AI response latency from messages.

    Latency is calculated as the time difference between a user message
    and the following assistant message.

    Args:
        messages: List of message dicts with 'timestamp' and 'role' keys

    Returns:
        Maximum latency in seconds, or 0 if cannot be calculated
    """
    if not messages or len(messages) < 2:
        return 0.0

    # Sort messages by timestamp
    sorted_msgs = sorted([m for m in messages if m.get("timestamp")], key=lambda x: x["timestamp"])

    latencies = []
    prev_user_time = None

    for msg in sorted_msgs:
        timestamp_str = msg.get("timestamp")
        role = msg.get("role", "")

        if not timestamp_str:
            continue

        try:
            # Parse timestamp
            if "T" in timestamp_str:
                ts = timestamp_str.replace("Z", "")
                if "." in ts:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            else:
                if "." in timestamp_str:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue

        if role == "user":
            prev_user_time = msg_time
        elif role == "assistant" and prev_user_time is not None:
            latency = (msg_time - prev_user_time).total_seconds()
            if latency > 0:  # Only count positive latencies
                latencies.append(latency)
            prev_user_time = None  # Reset after calculating

    if not latencies:
        return 0.0

    return round(max(latencies), 2)


def get_conversation_timeline(session_id: str) -> Dict:
    """Get detailed timeline data for a specific conversation.

    Args:
        session_id: The session identifier (agent_session_id or conversation_id)

    Returns:
        Dict with timeline data for rendering charts
    """
    # GMT+8 timezone
    gmt_plus_8 = timezone(timedelta(hours=8))

    def convert_to_gmt8(dt: datetime) -> datetime:
        """Convert datetime to GMT+8 timezone."""
        # If the datetime is naive (no timezone), assume it's UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(gmt_plus_8)

    conn = get_connection()
    cursor = conn.cursor()

    # Try to find messages by agent_session_id first
    _execute(
        cursor,
        """
        SELECT
            timestamp,
            role,
            tokens_used,
            model,
            sender_name,
            sender_id,
            parent_id,
            conversation_id
        FROM daily_messages
        WHERE agent_session_id = ?
        ORDER BY timestamp ASC
    """,
        [session_id],
    )

    rows = cursor.fetchall()

    # If no results, try conversation_id
    if not rows:
        _execute(
            cursor,
            """
            SELECT
                timestamp,
                role,
                tokens_used,
                model,
                sender_name,
                sender_id,
                parent_id,
                conversation_id
            FROM daily_messages
            WHERE conversation_id = ?
            ORDER BY timestamp ASC
        """,
            [session_id],
        )
        rows = cursor.fetchall()

    # If still no results, try parsing the session_id (format: sender_date_tool)
    if not rows:
        parts = session_id.rsplit("_", 2)
        if len(parts) >= 3:
            sender = parts[0]
            date = parts[1]
            tool = parts[2]

            # Handle 'Unknown' sender (sender_name and sender_id are both NULL)
            if sender == "Unknown":
                _execute(
                    cursor,
                    """
                    SELECT
                        timestamp,
                        role,
                        tokens_used,
                        model,
                        sender_name,
                        sender_id,
                        parent_id,
                        conversation_id
                    FROM daily_messages
                    WHERE date = ? AND tool_name = ?
                      AND sender_name IS NULL AND sender_id IS NULL
                    ORDER BY timestamp ASC
                """,
                    [date, tool],
                )
            else:
                _execute(
                    cursor,
                    """
                    SELECT
                        timestamp,
                        role,
                        tokens_used,
                        model,
                        sender_name,
                        sender_id,
                        parent_id,
                        conversation_id
                    FROM daily_messages
                    WHERE date = ? AND tool_name = ?
                      AND (sender_name = ? OR sender_id = ?)
                    ORDER BY timestamp ASC
                """,
                    [date, tool, sender, sender],
                )
            rows = cursor.fetchall()

    conn.close()

    if not rows:
        return {"timeline": [], "latency_curve": []}

    timeline = []
    latency_data = []

    # Build a map of message_id -> message_time for user messages
    # and track user message times by their IDs for parent_id lookup
    user_message_times = {}  # message_id -> datetime
    message_times = {}  # For looking up any message by some identifier

    # First pass: collect all user message times
    for row in rows:
        timestamp_str = row["timestamp"]
        if not timestamp_str:
            continue

        try:
            # Parse timestamp
            if "T" in timestamp_str:
                ts = timestamp_str.replace("Z", "")
                if "." in ts:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            else:
                if "." in timestamp_str:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

            # Convert to GMT+8
            msg_time = convert_to_gmt8(msg_time)
        except (ValueError, TypeError):
            continue

        role = row["role"] or "unknown"
        parent_id = row["parent_id"]

        # Store user message times for latency calculation
        if role == "user":
            # Use a combination of timestamp and parent_id as key
            key = f"{timestamp_str}_{parent_id or ''}"
            user_message_times[key] = msg_time

    # Second pass: build timeline and calculate latency
    last_user_time = None
    last_assistant_time = None
    for row in rows:
        timestamp_str = row["timestamp"]
        if not timestamp_str:
            continue

        try:
            # Parse timestamp
            if "T" in timestamp_str:
                ts = timestamp_str.replace("Z", "")
                if "." in ts:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            else:
                if "." in timestamp_str:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                else:
                    msg_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

            # Convert to GMT+8
            msg_time = convert_to_gmt8(msg_time)
        except (ValueError, TypeError):
            continue

        role = row["role"] or "unknown"
        parent_id = row["parent_id"]

        # Skip toolResult messages - only show user and assistant in timeline
        if role == "toolResult":
            continue

        timeline.append(
            {
                "timestamp": timestamp_str,
                "time": msg_time.strftime("%H:%M:%S"),
                "role": role,
                "tokens": row["tokens_used"] or 0,
            }
        )

        if role == "user":
            # Calculate user latency (thinking time): time from last assistant message
            if last_assistant_time is not None:
                latency = (msg_time - last_assistant_time).total_seconds()
                if latency > 0:
                    latency_data.append(
                        {
                            "timestamp": timestamp_str,
                            "time": msg_time.strftime("%H:%M:%S"),
                            "latency": round(latency, 2),
                            "role": "user",
                        }
                    )
            last_user_time = msg_time
            last_assistant_time = None
        elif role == "assistant":
            user_time = None

            # Try to find corresponding user message by parent_id
            if parent_id:
                # Look for user message with matching parent_id or timestamp
                for key, utime in user_message_times.items():
                    if parent_id in key or key.startswith(parent_id):
                        user_time = utime
                        break

            # Fallback to last user message if no parent_id match
            if user_time is None and last_user_time is not None:
                user_time = last_user_time

            # Calculate latency if we found a user message
            if user_time is not None:
                latency = (msg_time - user_time).total_seconds()
                if latency > 0:
                    latency_data.append(
                        {
                            "timestamp": timestamp_str,
                            "time": msg_time.strftime("%H:%M:%S"),
                            "latency": round(latency, 2),
                            "role": "assistant",
                        }
                    )
            last_assistant_time = msg_time

    return {"timeline": timeline, "latency_curve": latency_data}


def get_conversation_details(session_id: str) -> Dict:
    """Get complete conversation details for a specific conversation.

    Args:
        session_id: The session identifier (agent_session_id, conversation_id, or format: sender_date_tool)

    Returns:
        Dict with conversation info and list of messages with full content
    """
    conn = get_connection()
    cursor = conn.cursor()

    rows = []

    # Try to find messages by agent_session_id first
    _execute(
        cursor,
        """
        SELECT
            message_id,
            parent_id,
            role,
            content,
            tokens_used,
            input_tokens,
            output_tokens,
            model,
            timestamp,
            sender_name,
            sender_id,
            conversation_id
        FROM daily_messages
        WHERE agent_session_id = ?
        ORDER BY timestamp ASC
    """,
        [session_id],
    )
    rows = cursor.fetchall()

    # If no results, try conversation_id
    if not rows:
        _execute(
            cursor,
            """
            SELECT
                message_id,
                parent_id,
                role,
                content,
                tokens_used,
                input_tokens,
                output_tokens,
                model,
                timestamp,
                sender_name,
                sender_id,
                conversation_id
            FROM daily_messages
            WHERE conversation_id = ?
            ORDER BY timestamp ASC
        """,
            [session_id],
        )
        rows = cursor.fetchall()

    # If still no results, try parsing the session_id (format: sender_date_tool)
    if not rows:
        parts = session_id.rsplit("_", 2)
        if len(parts) >= 3:
            sender = parts[0]
            date = parts[1]
            tool = parts[2]

            # Handle 'Unknown' sender (sender_name and sender_id are both NULL)
            if sender == "Unknown":
                _execute(
                    cursor,
                    """
                    SELECT
                        message_id,
                        parent_id,
                        role,
                        content,
                        tokens_used,
                        input_tokens,
                        output_tokens,
                        model,
                        timestamp,
                        sender_name,
                        sender_id,
                        conversation_id
                    FROM daily_messages
                    WHERE date = ? AND tool_name = ?
                      AND sender_name IS NULL AND sender_id IS NULL
                    ORDER BY timestamp ASC
                """,
                    [date, tool],
                )
            else:
                _execute(
                    cursor,
                    """
                    SELECT
                        message_id,
                        parent_id,
                        role,
                        content,
                        tokens_used,
                        input_tokens,
                        output_tokens,
                        model,
                        timestamp,
                        sender_name,
                        sender_id,
                        conversation_id
                    FROM daily_messages
                    WHERE date = ? AND tool_name = ?
                      AND (sender_name = ? OR sender_id = ?)
                    ORDER BY timestamp ASC
                """,
                    [date, tool, sender, sender],
                )
            rows = cursor.fetchall()

    conn.close()

    if not rows:
        return {"session_id": session_id, "messages": [], "total_messages": 0, "total_tokens": 0}

    messages = []
    total_tokens = 0

    for row in rows:
        row_dict = dict(row)
        tokens = row_dict.get("tokens_used") or 0
        total_tokens += tokens

        # Format timestamp to CST
        timestamp_str = row_dict.get("timestamp")
        formatted_time = format_timestamp_to_cst(timestamp_str) if timestamp_str else None

        # Parse content if it's JSON
        content = row_dict.get("content")
        content_parsed = None
        if content:
            try:
                content_parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content_parsed = content

        messages.append(
            {
                "message_id": row_dict.get("message_id"),
                "parent_id": row_dict.get("parent_id"),
                "role": row_dict.get("role"),
                "content": content,
                "content_parsed": content_parsed,
                "tokens_used": tokens,
                "input_tokens": row_dict.get("input_tokens") or 0,
                "output_tokens": row_dict.get("output_tokens") or 0,
                "model": row_dict.get("model"),
                "timestamp": timestamp_str,
                "formatted_time": formatted_time,
                "sender_name": row_dict.get("sender_name"),
                "sender_id": row_dict.get("sender_id"),
            }
        )

    # Get session metadata from first and last messages
    first_msg = messages[0] if messages else {}
    last_msg = messages[-1] if messages else {}

    user_messages = [m for m in messages if m.get("role") == "user"]
    ai_messages = [m for m in messages if m.get("role") == "assistant"]

    return {
        "session_id": session_id,
        "messages": messages,
        "total_messages": len(messages),
        "total_tokens": total_tokens,
        "user_messages": len(user_messages),
        "ai_messages": len(ai_messages),
        "start_time": first_msg.get("formatted_time"),
        "end_time": last_msg.get("formatted_time"),
        "models": list(set(m.get("model") for m in messages if m.get("model"))),
        "sender_name": first_msg.get("sender_name") or first_msg.get("sender_id") or "Unknown",
        "conversation_id": first_msg.get("conversation_id"),
    }
