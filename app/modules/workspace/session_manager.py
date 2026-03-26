#!/usr/bin/env python3
"""
Open ACE - Session Manager Module

Provides session persistence and recovery for AI interactions.
Manages conversation history, state, and context across sessions.
"""

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from app.repositories.database import DB_PATH, is_postgresql, get_database_url

logger = logging.getLogger(__name__)

# Parameter placeholder for SQL queries
def _param() -> str:
    """Get the correct parameter placeholder for the current database."""
    return '?' if not is_postgresql() else '%s'

def _params(count: int) -> str:
    """Get comma-separated placeholders for multiple parameters."""
    p = _param()
    return ', '.join([p] * count)


class SessionStatus(Enum):
    """Session status enumeration."""
    ACTIVE = 'active'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    ARCHIVED = 'archived'
    ERROR = 'error'


class SessionType(Enum):
    """Session type enumeration."""
    CHAT = 'chat'
    TASK = 'task'
    WORKFLOW = 'workflow'
    AGENT = 'agent'


@dataclass
class SessionMessage:
    """A message within a session."""
    id: Optional[int] = None
    session_id: str = ''
    role: str = ''  # user, assistant, system, tool
    content: str = ''
    tokens_used: int = 0
    model: Optional[str] = None
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'tokens_used': self.tokens_used,
            'model': self.model,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionMessage':
        """Create from dictionary."""
        return cls(
            id=data.get('id'),
            session_id=data.get('session_id', ''),
            role=data.get('role', ''),
            content=data.get('content', ''),
            tokens_used=data.get('tokens_used', 0),
            model=data.get('model'),
            timestamp=datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else None,
            metadata=data.get('metadata', {}),
        )


@dataclass
class AgentSession:
    """Agent session data model for persistent AI conversations."""
    id: Optional[int] = None
    session_id: str = ''
    session_type: str = SessionType.CHAT.value
    title: str = ''
    tool_name: str = ''
    host_name: str = 'localhost'
    user_id: Optional[int] = None
    status: str = SessionStatus.ACTIVE.value
    context: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    message_count: int = 0
    model: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    messages: List[SessionMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'session_type': self.session_type,
            'title': self.title,
            'tool_name': self.tool_name,
            'host_name': self.host_name,
            'user_id': self.user_id,
            'status': self.status,
            'context': self.context,
            'settings': self.settings,
            'total_tokens': self.total_tokens,
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'message_count': self.message_count,
            'model': self.model,
            'tags': self.tags,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'messages': [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AgentSession':
        """Create from dictionary."""
        return cls(
            id=data.get('id'),
            session_id=data.get('session_id', ''),
            session_type=data.get('session_type', SessionType.CHAT.value),
            title=data.get('title', ''),
            tool_name=data.get('tool_name', ''),
            host_name=data.get('host_name', 'localhost'),
            user_id=data.get('user_id'),
            status=data.get('status', SessionStatus.ACTIVE.value),
            context=data.get('context', {}),
            settings=data.get('settings', {}),
            total_tokens=data.get('total_tokens', 0),
            total_input_tokens=data.get('total_input_tokens', 0),
            total_output_tokens=data.get('total_output_tokens', 0),
            message_count=data.get('message_count', 0),
            model=data.get('model'),
            tags=data.get('tags', []),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            updated_at=datetime.fromisoformat(data['updated_at']) if data.get('updated_at') else None,
            completed_at=datetime.fromisoformat(data['completed_at']) if data.get('completed_at') else None,
            expires_at=datetime.fromisoformat(data['expires_at']) if data.get('expires_at') else None,
            messages=[SessionMessage.from_dict(m) for m in data.get('messages', [])],
        )

    def is_expired(self) -> bool:
        """Check if session is expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def is_active(self) -> bool:
        """Check if session is active."""
        return self.status == SessionStatus.ACTIVE.value and not self.is_expired()

    def generate_title(self) -> str:
        """Generate a title from the first user message."""
        if self.title:
            return self.title
        for msg in self.messages:
            if msg.role == 'user':
                # Use first 50 chars of content as title
                title = msg.content[:50]
                if len(msg.content) > 50:
                    title += '...'
                return title
        return f"Session {self.session_id[:8]}"


class SessionManager:
    """Manager for agent sessions with persistence and recovery."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the session manager.

        Args:
            db_path: Optional custom database path.
        """
        self.db_path = db_path or str(DB_PATH)
        self._ensure_tables()

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection (SQLite or PostgreSQL)."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                url = get_database_url()
                conn = psycopg2.connect(url)
                return conn
            except ImportError:
                raise ImportError(
                    "psycopg2 is required for PostgreSQL. "
                    "Install it with: pip install psycopg2-binary"
                )
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _ensure_tables(self) -> None:
        """Ensure required tables exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
        id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"

        # Create agent_sessions table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id {id_type},
                session_id TEXT NOT NULL UNIQUE,
                session_type TEXT DEFAULT 'chat',
                title TEXT,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                user_id INTEGER,
                status TEXT DEFAULT 'active',
                context TEXT,
                settings TEXT,
                total_tokens INTEGER DEFAULT 0,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                model TEXT,
                tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')

        # Create session_messages table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS session_messages (
                id {id_type},
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tokens_used INTEGER DEFAULT 0,
                model TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
            )
        ''')

        # Create indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_agent_sessions_session_id
            ON agent_sessions(session_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_id
            ON agent_sessions(user_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_agent_sessions_status
            ON agent_sessions(status)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_agent_sessions_tool_name
            ON agent_sessions(tool_name)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_session_messages_session_id
            ON session_messages(session_id)
        ''')

        conn.commit()
        conn.close()

    def create_session(
        self,
        tool_name: str,
        user_id: Optional[int] = None,
        session_type: str = SessionType.CHAT.value,
        title: str = '',
        host_name: str = 'localhost',
        context: Optional[Dict[str, Any]] = None,
        settings: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        expires_in_hours: Optional[int] = None
    ) -> AgentSession:
        """
        Create a new agent session.

        Args:
            tool_name: Name of the AI tool.
            user_id: Optional user ID.
            session_type: Type of session.
            title: Optional session title.
            host_name: Host machine name.
            context: Optional context data.
            settings: Optional session settings.
            model: Optional model name.
            expires_in_hours: Optional expiration time in hours.

        Returns:
            AgentSession: The created session.
        """
        session_id = str(uuid.uuid4())
        now = datetime.utcnow()

        expires_at = None
        if expires_in_hours:
            expires_at = now + timedelta(hours=expires_in_hours)

        session = AgentSession(
            session_id=session_id,
            session_type=session_type,
            title=title,
            tool_name=tool_name,
            host_name=host_name,
            user_id=user_id,
            status=SessionStatus.ACTIVE.value,
            context=context or {},
            settings=settings or {},
            model=model,
            expires_at=expires_at,
            created_at=now,
            updated_at=now
        )

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f'''
            INSERT INTO agent_sessions
            (session_id, session_type, title, tool_name, host_name, user_id, status,
             context, settings, model, expires_at, created_at, updated_at)
            VALUES ({_params(13)})
        ''', (
            session.session_id,
            session.session_type,
            session.title,
            session.tool_name,
            session.host_name,
            session.user_id,
            session.status,
            json.dumps(session.context),
            json.dumps(session.settings),
            session.model,
            session.expires_at.isoformat() if session.expires_at else None,
            session.created_at.isoformat(),
            session.updated_at.isoformat()
        ))

        session.id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"Created session: {session_id} for tool: {tool_name}")
        return session

    def get_session(self, session_id: str, include_messages: bool = False) -> Optional[AgentSession]:
        """
        Get a session by ID.

        Args:
            session_id: Session ID to retrieve.
            include_messages: Whether to include messages.

        Returns:
            AgentSession or None if not found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f'SELECT * FROM agent_sessions WHERE session_id = {_param()}', (session_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        session = self._row_to_session(row)

        if include_messages:
            cursor.execute(f'''
                SELECT * FROM session_messages
                WHERE session_id = {_param()}
                ORDER BY timestamp ASC
            ''', (session_id,))
            message_rows = cursor.fetchall()
            session.messages = [self._row_to_message(msg_row) for msg_row in message_rows]

        conn.close()
        return session

    def update_session(self, session: AgentSession) -> bool:
        """
        Update a session.

        Args:
            session: AgentSession with updated data.

        Returns:
            bool: True if update was successful.
        """
        if not session.session_id:
            return False

        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow()
        session.updated_at = now

        cursor.execute(f'''
            UPDATE agent_sessions
            SET title = {_param()}, status = {_param()}, context = {_param()}, settings = {_param()},
                total_tokens = {_param()}, total_input_tokens = {_param()}, total_output_tokens = {_param()},
                message_count = {_param()}, model = {_param()}, tags = {_param()}, updated_at = {_param()}, completed_at = {_param()}
            WHERE session_id = {_param()}
        ''', (
            session.title,
            session.status,
            json.dumps(session.context),
            json.dumps(session.settings),
            session.total_tokens,
            session.total_input_tokens,
            session.total_output_tokens,
            session.message_count,
            session.model,
            json.dumps(session.tags),
            session.updated_at.isoformat(),
            session.completed_at.isoformat() if session.completed_at else None,
            session.session_id
        ))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens_used: int = 0,
        model: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Add a message to a session.

        Args:
            session_id: Session ID.
            role: Message role (user, assistant, system, tool).
            content: Message content.
            tokens_used: Tokens used for this message.
            model: Model used.
            metadata: Optional metadata.

        Returns:
            int: Message ID.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()

        cursor.execute(f'''
            INSERT INTO session_messages
            (session_id, role, content, tokens_used, model, timestamp, metadata)
            VALUES ({_params(7)})
        ''', (
            session_id,
            role,
            content,
            tokens_used,
            model,
            now,
            json.dumps(metadata) if metadata else None
        ))

        message_id = cursor.lastrowid

        # Update session stats
        cursor.execute(f'''
            UPDATE agent_sessions
            SET message_count = message_count + 1,
                total_tokens = total_tokens + {_param()},
                updated_at = {_param()}
            WHERE session_id = {_param()}
        ''', (tokens_used, now, session_id))

        conn.commit()
        conn.close()

        logger.debug(f"Added message to session {session_id}: role={role}")
        return message_id

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_id: Optional[int] = None
    ) -> List[SessionMessage]:
        """
        Get messages for a session.

        Args:
            session_id: Session ID.
            limit: Optional limit on number of messages.
            before_id: Get messages before this ID.

        Returns:
            List of SessionMessage objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = f'SELECT * FROM session_messages WHERE session_id = {_param()}'
        params = [session_id]

        if before_id:
            query += f' AND id < {_param()}'
            params.append(before_id)

        query += ' ORDER BY timestamp ASC'

        if limit:
            query += f' LIMIT {_param()}'
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_message(row) for row in rows]

    def complete_session(self, session_id: str) -> bool:
        """
        Mark a session as completed.

        Args:
            session_id: Session ID to complete.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()

        cursor.execute(f'''
            UPDATE agent_sessions
            SET status = {_param()}, completed_at = {_param()}, updated_at = {_param()}
            WHERE session_id = {_param()}
        ''', (SessionStatus.COMPLETED.value, now, now, session_id))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if success:
            logger.info(f"Completed session: {session_id}")
        return success

    def archive_session(self, session_id: str) -> bool:
        """
        Archive a session.

        Args:
            session_id: Session ID to archive.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.utcnow().isoformat()

        cursor.execute(f'''
            UPDATE agent_sessions
            SET status = {_param()}, updated_at = {_param()}
            WHERE session_id = {_param()}
        ''', (SessionStatus.ARCHIVED.value, now, session_id))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if success:
            logger.info(f"Archived session: {session_id}")
        return success

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and its messages.

        Args:
            session_id: Session ID to delete.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Delete messages first
        cursor.execute(f'DELETE FROM session_messages WHERE session_id = {_param()}', (session_id,))

        # Delete session
        cursor.execute(f'DELETE FROM agent_sessions WHERE session_id = {_param()}', (session_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if success:
            logger.info(f"Deleted session: {session_id}")
        return success

    def list_sessions(
        self,
        user_id: Optional[int] = None,
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
        session_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        List sessions with filters.

        Args:
            user_id: Filter by user ID.
            tool_name: Filter by tool name.
            status: Filter by status.
            session_type: Filter by session type.
            search: Search in title.
            page: Page number.
            limit: Results per page.

        Returns:
            Dict with sessions, total, page, limit, total_pages.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = []
        params = []

        if user_id is not None:
            conditions.append(f'user_id = {_param()}')
            params.append(user_id)

        if tool_name:
            conditions.append(f'tool_name = {_param()}')
            params.append(tool_name)

        if status:
            conditions.append(f'status = {_param()}')
            params.append(status)

        if session_type:
            conditions.append(f'session_type = {_param()}')
            params.append(session_type)

        if search:
            conditions.append(f'title LIKE {_param()}')
            params.append(f'%{search}%')

        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        # Get total count
        cursor.execute(f'SELECT COUNT(*) as count FROM agent_sessions WHERE {where_clause}', params)
        total = cursor.fetchone()['count']
        total_pages = (total + limit - 1) // limit if total > 0 else 1

        # Get paginated results
        offset = (page - 1) * limit
        cursor.execute(f'''
            SELECT * FROM agent_sessions
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT {_param()} OFFSET {_param()}
        ''', params + [limit, offset])

        rows = cursor.fetchall()
        conn.close()

        sessions = [self._row_to_session(row) for row in rows]

        return {
            'sessions': sessions,
            'total': total,
            'page': page,
            'limit': limit,
            'total_pages': total_pages
        }

    def get_active_sessions(self, user_id: Optional[int] = None) -> List[AgentSession]:
        """
        Get all active sessions.

        Args:
            user_id: Optional user ID filter.

        Returns:
            List of active AgentSession objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id:
            cursor.execute(f'''
                SELECT * FROM agent_sessions
                WHERE user_id = {_param()} AND status = {_param()}
                ORDER BY updated_at DESC
            ''', (user_id, SessionStatus.ACTIVE.value))
        else:
            cursor.execute(f'''
                SELECT * FROM agent_sessions
                WHERE status = {_param()}
                ORDER BY updated_at DESC
            ''', (SessionStatus.ACTIVE.value,))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_session(row) for row in rows]

    def recover_session(self, session_id: str) -> Optional[AgentSession]:
        """
        Recover a paused or interrupted session.

        Args:
            session_id: Session ID to recover.

        Returns:
            AgentSession if recovery successful, None otherwise.
        """
        session = self.get_session(session_id, include_messages=True)

        if not session:
            return None

        if session.is_expired():
            logger.warning(f"Cannot recover expired session: {session_id}")
            return None

        if session.status == SessionStatus.ARCHIVED.value:
            logger.warning(f"Cannot recover archived session: {session_id}")
            return None

        # Update status to active
        session.status = SessionStatus.ACTIVE.value
        self.update_session(session)

        logger.info(f"Recovered session: {session_id}")
        return session

    def cleanup_expired_sessions(self, days_old: int = 30) -> int:
        """
        Clean up expired sessions older than specified days.

        Args:
            days_old: Delete sessions older than this many days.

        Returns:
            int: Number of sessions deleted.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = datetime.utcnow() - timedelta(days=days_old)

        # Get expired session IDs
        cursor.execute(f'''
            SELECT session_id FROM agent_sessions
            WHERE (expires_at < {_param()} OR updated_at < {_param()})
            AND status != {_param()}
        ''', (cutoff.isoformat(), cutoff.isoformat(), SessionStatus.ARCHIVED.value))

        session_ids = [row['session_id'] for row in cursor.fetchall()]

        # Delete messages and sessions
        for session_id in session_ids:
            cursor.execute(f'DELETE FROM session_messages WHERE session_id = {_param()}', (session_id,))
            cursor.execute(f'DELETE FROM agent_sessions WHERE session_id = {_param()}', (session_id,))

        conn.commit()
        conn.close()

        if session_ids:
            logger.info(f"Cleaned up {len(session_ids)} expired sessions")

        return len(session_ids)

    def get_session_stats(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get session statistics.

        Args:
            user_id: Optional user ID filter.

        Returns:
            Dict with session statistics.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id:
            cursor.execute(f'''
                SELECT
                    COUNT(*) as total_sessions,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
                    SUM(total_tokens) as total_tokens,
                    SUM(message_count) as total_messages
                FROM agent_sessions
                WHERE user_id = {_param()}
            ''', (user_id,))
        else:
            cursor.execute('''
                SELECT
                    COUNT(*) as total_sessions,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
                    SUM(total_tokens) as total_tokens,
                    SUM(message_count) as total_messages
                FROM agent_sessions
            ''')

        row = cursor.fetchone()
        conn.close()

        return {
            'total_sessions': row['total_sessions'] or 0,
            'active_sessions': row['active_sessions'] or 0,
            'completed_sessions': row['completed_sessions'] or 0,
            'total_tokens': row['total_tokens'] or 0,
            'total_messages': row['total_messages'] or 0,
        }

    def _row_to_session(self, row: Union[sqlite3.Row, Dict]) -> AgentSession:
        """Convert a database row to AgentSession."""
        # Handle both sqlite3.Row and dict (PostgreSQL)
        def get_value(key: str):
            if isinstance(row, dict):
                return row.get(key)
            return row[key]

        return AgentSession(
            id=get_value('id'),
            session_id=get_value('session_id'),
            session_type=get_value('session_type') or SessionType.CHAT.value,
            title=get_value('title') or '',
            tool_name=get_value('tool_name'),
            host_name=get_value('host_name') or 'localhost',
            user_id=get_value('user_id'),
            status=get_value('status') or SessionStatus.ACTIVE.value,
            context=json.loads(get_value('context')) if get_value('context') else {},
            settings=json.loads(get_value('settings')) if get_value('settings') else {},
            total_tokens=get_value('total_tokens') or 0,
            total_input_tokens=get_value('total_input_tokens') or 0,
            total_output_tokens=get_value('total_output_tokens') or 0,
            message_count=get_value('message_count') or 0,
            model=get_value('model'),
            tags=json.loads(get_value('tags')) if get_value('tags') else [],
            created_at=datetime.fromisoformat(get_value('created_at')) if get_value('created_at') else None,
            updated_at=datetime.fromisoformat(get_value('updated_at')) if get_value('updated_at') else None,
            completed_at=datetime.fromisoformat(get_value('completed_at')) if get_value('completed_at') else None,
            expires_at=datetime.fromisoformat(get_value('expires_at')) if get_value('expires_at') else None,
        )

    def _row_to_message(self, row: Union[sqlite3.Row, Dict]) -> SessionMessage:
        """Convert a database row to SessionMessage."""
        # Handle both sqlite3.Row and dict (PostgreSQL)
        def get_value(key: str):
            if isinstance(row, dict):
                return row.get(key)
            return row[key]

        return SessionMessage(
            id=get_value('id'),
            session_id=get_value('session_id'),
            role=get_value('role'),
            content=get_value('content') or '',
            tokens_used=get_value('tokens_used') or 0,
            model=get_value('model'),
            timestamp=datetime.fromisoformat(get_value('timestamp')) if get_value('timestamp') else None,
            metadata=json.loads(get_value('metadata')) if get_value('metadata') else {},
        )
