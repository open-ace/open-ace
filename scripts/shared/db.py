#!/usr/bin/env python3
"""
AI Token Usage - Database Module

Provides database operations for the ai_token_usage project.
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple


DB_DIR = os.path.expanduser("~/.ai_token_usage")
DB_PATH = os.path.join(DB_DIR, "usage.db")


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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tokens_used INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_tokens INTEGER DEFAULT 0,
            models_used TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, tool_name)
        )
    ''')

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
    models_used: Optional[List[str]] = None
) -> bool:
    """Save or update usage data for a specific date and tool."""
    conn = get_connection()
    cursor = conn.cursor()

    models_json = json.dumps(models_used) if models_used else None

    cursor.execute('''
        INSERT OR REPLACE INTO daily_usage
        (date, tool_name, tokens_used, input_tokens, output_tokens, cache_tokens, models_used)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (date, tool_name, tokens_used, input_tokens, output_tokens, cache_tokens, models_json))

    conn.commit()
    conn.close()
    return True


def get_usage_by_date(date: str, tool_name: Optional[str] = None) -> List[Dict]:
    """Get usage data for a specific date, optionally filtered by tool."""
    conn = get_connection()
    cursor = conn.cursor()

    if tool_name:
        cursor.execute('''
            SELECT * FROM daily_usage
            WHERE date = ? AND tool_name = ?
            ORDER BY date DESC
        ''', (date, tool_name))
    else:
        cursor.execute('''
            SELECT * FROM daily_usage
            WHERE date = ?
            ORDER BY date DESC
        ''', (date,))

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get('models_used'):
            result['models_used'] = json.loads(result['models_used'])
        results.append(result)

    return results


def get_usage_by_tool(
    tool_name: str,
    days: int = 7,
    end_date: Optional[str] = None
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

    cursor.execute('''
        SELECT * FROM daily_usage
        WHERE tool_name = ? AND date >= ? AND date <= ?
        ORDER BY date DESC
    ''', (tool_name, start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get('models_used'):
            result['models_used'] = json.loads(result['models_used'])
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


def get_summary_by_tool() -> Dict[str, Dict]:
    """Get summary statistics grouped by tool."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            tool_name,
            COUNT(*) as days_count,
            SUM(tokens_used) as total_tokens,
            AVG(tokens_used) as avg_tokens,
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
            'first_date': row['first_date'],
            'last_date': row['last_date']
        }

    return results


def get_daily_range(
    start_date: str,
    end_date: str,
    tool_name: Optional[str] = None
) -> List[Dict]:
    """Get usage data within a date range."""
    conn = get_connection()
    cursor = conn.cursor()

    if tool_name:
        cursor.execute('''
            SELECT * FROM daily_usage
            WHERE date >= ? AND date <= ? AND tool_name = ?
            ORDER BY date DESC
        ''', (start_date, end_date, tool_name))
    else:
        cursor.execute('''
            SELECT * FROM daily_usage
            WHERE date >= ? AND date <= ?
            ORDER BY date DESC
        ''', (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        if result.get('models_used'):
            result['models_used'] = json.loads(result['models_used'])
        results.append(result)

    return results
