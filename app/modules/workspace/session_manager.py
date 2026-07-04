"""
Open ACE - Session Manager Module

Provides session persistence and recovery for AI interactions.
Manages conversation history, state, and context across sessions.
"""

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional, Union

from app.repositories.database import DB_PATH, escape_like, get_database_url, is_postgresql
from app.utils.tool_names import normalize_tool_name

logger = logging.getLogger(__name__)


def _sanitize_text_value(text: Optional[str]) -> Optional[str]:
    """Remove NUL / invalid UTF-8 surrogate data before persistence."""
    if text is None:
        return None
    if "\x00" in text:
        text = text.replace("\x00", "")
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


# Parameter placeholder for SQL queries
def _param() -> str:
    """Get the correct parameter placeholder for the current database."""
    return "?" if not is_postgresql() else "%s"


def _params(count: int) -> str:
    """Get comma-separated placeholders for multiple parameters."""
    p = _param()
    return ", ".join([p] * count)


class SessionStatus(Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class SessionType(Enum):
    """Session type enumeration."""

    CHAT = "chat"
    AGENT = "agent"
    WORKFLOW = "workflow"
    TERMINAL = "terminal"


@dataclass
class SessionMessage:
    """A message within a session."""

    id: Optional[int] = None
    session_id: str = ""
    role: str = ""  # user, assistant, system, tool
    content: str = ""
    tokens_used: int = 0
    model: Optional[str] = None
    timestamp: Optional[datetime] = None
    source_timestamp: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    milestone_id: str = ""
    source: str = ""
    external_message_id: str = ""
    content_blocks: list[Any] = field(default_factory=list)
    _was_inserted: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tokens_used": self.tokens_used,
            "model": self.model,
            "timestamp": _format_dt(self.timestamp),
            "source_timestamp": _format_dt(self.source_timestamp),
            "metadata": self.metadata,
            "milestone_id": self.milestone_id,
            "source": self.source,
            "external_message_id": self.external_message_id,
            "content_blocks": self.content_blocks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMessage":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            session_id=data.get("session_id", ""),
            role=data.get("role", ""),
            content=data.get("content", ""),
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model"),
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else None,
            source_timestamp=(
                datetime.fromisoformat(data["source_timestamp"])
                if data.get("source_timestamp")
                else None
            ),
            metadata=data.get("metadata", {}),
            milestone_id=data.get("milestone_id", ""),
            source=data.get("source", ""),
            external_message_id=data.get("external_message_id", ""),
            content_blocks=data.get("content_blocks", []) or [],
        )


def _format_dt(dt):
    """Format datetime as ISO 8601 string.

    - Timezone-aware datetimes preserve their timezone info (e.g., +00:00 for UTC).
    - Naive datetimes get +00:00 appended because this codebase stores UTC values
      as naive datetimes (via datetime.now(timezone.utc).replace(tzinfo=None)).
    """
    if dt is None:
        return None
    iso_str = dt.isoformat()
    if "+" not in iso_str and "Z" not in iso_str and "-" not in iso_str[-6:]:
        iso_str += "+00:00"
    return iso_str


@dataclass
class AgentSession:
    """Agent session data model for persistent AI conversations."""

    id: Optional[int] = None
    session_id: str = ""
    session_type: str = SessionType.CHAT.value
    title: str = ""
    tool_name: str = ""
    host_name: str = "localhost"
    user_id: Optional[int] = None
    project_id: Optional[int] = None  # Project association for statistics
    project_path: Optional[str] = None  # Project path for quick reference
    status: str = SessionStatus.ACTIVE.value
    context: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    message_count: int = 0
    request_count: int = 0  # Number of API requests (assistant messages only)
    model: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    messages: list[SessionMessage] = field(default_factory=list)
    workspace_type: str = "local"  # local or remote
    remote_machine_id: Optional[str] = None
    paused_at: Optional[datetime] = None
    # Real CLI/sidebar session id for workflow-owned lines (Claude local). The
    # workflow row stores a stable tracking id; this column is the authoritative
    # resume target. Promoted out of context JSON so a partial context write
    # can never lose it and resume never falls back to the tracking id (#1200).
    cli_session_id: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "session_type": self.session_type,
            "title": self.title,
            "tool_name": self.tool_name,
            "host_name": self.host_name,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "project_path": self.project_path,
            "status": self.status,
            "context": self.context,
            "settings": self.settings,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "message_count": self.message_count,
            "request_count": self.request_count,
            "model": self.model,
            "tags": self.tags,
            "created_at": _format_dt(self.created_at),
            "updated_at": _format_dt(self.updated_at),
            "completed_at": _format_dt(self.completed_at),
            "expires_at": _format_dt(self.expires_at),
            "messages": [m.to_dict() for m in self.messages],
            "workspace_type": self.workspace_type,
            "remote_machine_id": self.remote_machine_id,
            "paused_at": _format_dt(self.paused_at),
            "cli_session_id": self.cli_session_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentSession":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            session_id=data.get("session_id", ""),
            session_type=data.get("session_type", SessionType.CHAT.value),
            title=data.get("title", ""),
            tool_name=data.get("tool_name", ""),
            host_name=data.get("host_name", "localhost"),
            user_id=data.get("user_id"),
            project_id=data.get("project_id"),
            project_path=data.get("project_path"),
            status=data.get("status", SessionStatus.ACTIVE.value),
            context=data.get("context", {}),
            settings=data.get("settings", {}),
            total_tokens=data.get("total_tokens", 0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            message_count=data.get("message_count", 0),
            model=data.get("model"),
            tags=data.get("tags", []),
            created_at=(
                datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            expires_at=(
                datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
            ),
            messages=[SessionMessage.from_dict(m) for m in data.get("messages", [])],
            workspace_type=data.get("workspace_type", "local"),
            remote_machine_id=data.get("remote_machine_id"),
            paused_at=(
                datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None
            ),
            cli_session_id=data.get("cli_session_id", ""),
        )

    def is_expired(self) -> bool:
        """Check if session is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc).replace(tzinfo=None) > self.expires_at

    def is_active(self) -> bool:
        """Check if session is active."""
        return self.status == SessionStatus.ACTIVE.value and not self.is_expired()

    def generate_title(self) -> str:
        """Generate a title from the first user message."""
        if self.title:
            return self.title
        for msg in self.messages:
            if msg.role == "user":
                # Use first 50 chars of content as title
                title = msg.content[:50]
                if len(msg.content) > 50:
                    title += "..."
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

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection (SQLite or PostgreSQL)."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor

                url = get_database_url()
                conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
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

    @staticmethod
    def _column_exists(cursor: Any, table: str, column: str) -> bool:
        """Check whether a column exists on a table (SQLite or PostgreSQL)."""
        if is_postgresql():
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
                """,
                (table, column),
            )
        else:
            cursor.execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
            # sqlite3 with row_factory returns Row-like objects; index 1 is name
            return any(
                (row[1] if isinstance(row, (tuple, sqlite3.Row)) else row["name"]) == column
                for row in rows
            )
        return cursor.fetchone() is not None

    @staticmethod
    def _index_exists(cursor: Any, table: str, index: str) -> bool:
        """Check whether an index exists on a table (SQLite or PostgreSQL)."""
        if is_postgresql():
            cursor.execute(
                """
                SELECT 1 FROM pg_indexes
                WHERE tablename = %s AND indexname = %s
                """,
                (table, index),
            )
        else:
            cursor.execute(f"PRAGMA index_list({table})")
            rows = cursor.fetchall()
            return any(
                (row[1] if isinstance(row, (tuple, sqlite3.Row)) else row["name"]) == index
                for row in rows
            )
        return cursor.fetchone() is not None

    def _ensure_tables(self) -> None:
        """Ensure required tables exist from the authoritative schema (#1273).

        Loads schema-sqlite.sql / schema-postgres.sql via the centralized
        load_schema_from_file(), which is the single source of truth. This
        replaces the former hand-maintained CREATE TABLE + alter_columns list
        that had drifted from the authoritative schema (missing project_id,
        project_path, request_count, etc.). Idempotent (CREATE IF NOT EXISTS).
        """
        from app.repositories.schema_init import load_schema_from_file

        # Pin the dialect from THIS module's is_postgresql (tests monkeypatch it
        # here, not in schema_init.database), and convert the sqlite db_path to a
        # db_url. Postgres relies on DATABASE_URL (db_url=None).
        dialect = "postgresql" if is_postgresql() else "sqlite"
        db_url = f"sqlite:///{self.db_path}" if dialect == "sqlite" and self.db_path else None
        load_schema_from_file(db_url=db_url, dialect=dialect)

    @staticmethod
    def _extract_external_message_id(metadata: Optional[dict[str, Any]]) -> str:
        """Extract a stable external message identity from transcript metadata."""
        metadata = metadata or {}
        for identity_key in ("external_message_id", "message_id", "uuid"):
            identity_value = metadata.get(identity_key)
            if identity_value:
                return str(identity_value)
        return ""

    @staticmethod
    def _extract_source(metadata: Optional[dict[str, Any]]) -> str:
        """Extract the transcript producer identifier from metadata."""
        metadata = metadata or {}
        value = metadata.get("source")
        return str(value) if value else ""

    @staticmethod
    def _extract_content_blocks(metadata: Optional[dict[str, Any]]) -> list[Any]:
        """Extract structured content blocks from metadata."""
        metadata = metadata or {}
        value = metadata.get("content_blocks")
        return value if isinstance(value, list) else []

    def create_session(
        self,
        tool_name: str,
        user_id: Optional[int] = None,
        session_type: str = SessionType.CHAT.value,
        title: str = "",
        host_name: str = "localhost",
        context: Optional[dict[str, Any]] = None,
        settings: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        expires_in_hours: Optional[int] = None,
        project_id: Optional[int] = None,
        project_path: Optional[str] = None,
        session_id: Optional[str] = None,
        workspace_type: str = "local",
        remote_machine_id: Optional[str] = None,
    ) -> AgentSession:
        """
        Create a new agent session.

        If a session with the same session_id already exists, return the existing session.
        This handles cases where qwen-code-webui tries to create a session that was
        already created by fetch_qwen.py or previous webui instance.

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
            project_id: Optional project ID for statistics.
            project_path: Optional project path for quick reference.
            session_id: Optional session_id to use (e.g., from qwen CLI).
                        If not provided, a new UUID will be generated.

        Returns:
            AgentSession: The created or existing session.
        """
        # Use provided session_id or generate a new one
        session_id = session_id or str(uuid.uuid4())

        # Check if session already exists
        existing_session = self.get_session(session_id)
        if existing_session:
            logger.info(f"Session {session_id} already exists, returning existing session")
            return existing_session

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        expires_at = None
        if expires_in_hours:
            expires_at = now + timedelta(hours=expires_in_hours)

        # Normalize at the write boundary so agent_sessions never stores tool
        # variants that would split aggregates (combined usage, ROI breakdown).
        tool_name = normalize_tool_name(tool_name)

        session = AgentSession(
            session_id=session_id,
            session_type=session_type,
            title=title,
            tool_name=tool_name,
            host_name=host_name,
            user_id=user_id,
            project_id=project_id,
            project_path=project_path,
            status=SessionStatus.ACTIVE.value,
            context=context or {},
            settings=settings or {},
            model=model,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
            workspace_type=workspace_type,
            remote_machine_id=remote_machine_id,
        )

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            INSERT INTO agent_sessions
            (session_id, session_type, title, tool_name, host_name, user_id, project_id,
             project_path, status, context, settings, model, expires_at, created_at, updated_at,
             request_count, total_tokens, total_input_tokens, total_output_tokens, message_count,
             workspace_type, remote_machine_id, cli_session_id)
            VALUES ({_params(23)})
        """,
            (
                session.session_id,
                session.session_type,
                session.title,
                session.tool_name,
                session.host_name,
                session.user_id,
                session.project_id,
                session.project_path,
                session.status,
                json.dumps(session.context),
                json.dumps(session.settings),
                session.model,
                session.expires_at.isoformat() if session.expires_at else None,
                session.created_at.isoformat() if session.created_at else None,
                session.updated_at.isoformat() if session.updated_at else None,
                session.request_count or 0,
                session.total_tokens or 0,
                session.total_input_tokens or 0,
                session.total_output_tokens or 0,
                session.message_count or 0,
                session.workspace_type or "local",
                session.remote_machine_id,
                session.cli_session_id or "",
            ),
        )

        session.id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"Created session: {session_id} for tool: {tool_name}")
        return session

    def get_session(
        self,
        session_id: str,
        include_messages: bool = False,
        message_milestone_id: Optional[str] = None,
    ) -> Optional[AgentSession]:
        """
        Get a session by ID.

        Args:
            session_id: Session ID to retrieve.
            include_messages: Whether to include messages.
            message_milestone_id: Optional milestone filter for session messages.

        Returns:
            AgentSession or None if not found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"SELECT * FROM agent_sessions WHERE session_id = {_param()}", (session_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        session = self._row_to_session(row)

        if include_messages:
            query = f"""
                SELECT * FROM session_messages
                WHERE session_id = {_param()}
            """
            params: list[Any] = [session_id]
            if message_milestone_id is not None:
                query += f" AND milestone_id = {_param()}"
                params.append(message_milestone_id)
            query += " ORDER BY timestamp ASC"
            cursor.execute(query, params)
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

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.updated_at = now

        cursor.execute(
            f"""
            UPDATE agent_sessions
            SET title = {_param()}, status = {_param()}, context = {_param()}, settings = {_param()},
                total_tokens = {_param()}, total_input_tokens = {_param()}, total_output_tokens = {_param()},
                message_count = {_param()}, request_count = {_param()}, model = {_param()}, tags = {_param()},
                updated_at = {_param()}, completed_at = {_param()},
                workspace_type = {_param()}, remote_machine_id = {_param()}, paused_at = {_param()},
                cli_session_id = {_param()}
            WHERE session_id = {_param()}
        """,
            (
                session.title,
                session.status,
                json.dumps(session.context),
                json.dumps(session.settings),
                session.total_tokens,
                session.total_input_tokens,
                session.total_output_tokens,
                session.message_count,
                session.request_count,
                session.model,
                json.dumps(session.tags),
                session.updated_at.isoformat() if session.updated_at else None,
                session.completed_at.isoformat() if session.completed_at else None,
                session.workspace_type,
                session.remote_machine_id,
                session.paused_at.isoformat() if session.paused_at else None,
                session.cli_session_id or "",
                session.session_id,
            ),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def list_cli_session_ids_for_project(self, project_path: str) -> set[str]:
        """Return all non-empty ``cli_session_id`` values for sessions in a project.

        Used by the autonomous runner's mtime fallback to EXCLUDE sessions already
        bound to a workflow's session lines (main/review/test). Without this, a
        shared "main" session — continuously appended and thus always newest by
        mtime — gets wrongly picked for a fresh review/test line, collapsing the
        3-session topology (issue #723).
        """
        if not project_path:
            return set()
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""SELECT DISTINCT cli_session_id FROM agent_sessions
                    WHERE project_path = {_param()} AND cli_session_id IS NOT NULL
                      AND cli_session_id != ''""",
                (project_path,),
            )
            return {
                (row["cli_session_id"] if not isinstance(row, (tuple, list)) else row[0])
                for row in cursor.fetchall()
                if (row["cli_session_id"] if not isinstance(row, (tuple, list)) else row[0])
            }
        except Exception:
            logger.warning("Failed to list cli_session_ids for project", exc_info=True)
            return set()
        finally:
            conn.close()

    ALLOWED_UPDATE_FIELDS = {
        "status",
        "workspace_type",
        "remote_machine_id",
        "title",
        "context",
        "settings",
        "tags",
        "model",
        "cli_tool",
        "message_count",
        "request_count",
        "total_tokens",
        "total_input_tokens",
        "total_output_tokens",
        "total_cost",
        "duration_seconds",
        "error_message",
        "completed_at",
        "paused_at",
        "expires_at",
        "terminal_id",
        "project_path",
        "user_id",
        "cli_session_id",
    }

    def update_session_fields(self, session_id: str, fields: dict[str, Any]) -> bool:
        """
        Update specific fields of a session.

        Args:
            session_id: Session ID to update.
            fields: Dictionary of field names and values to update.

        Returns:
            bool: True if update was successful.
        """
        if not session_id or not fields:
            return False

        # Filter to allowed fields only (prevent column name injection)
        safe_fields = {k: v for k, v in fields.items() if k in self.ALLOWED_UPDATE_FIELDS}
        if not safe_fields:
            return False

        p = _param()
        # Build SET clause
        sets = ", ".join([f"{k} = {p}" for k in safe_fields])
        # Add updated_at
        sets += f", updated_at = {p}"
        values = [safe_fields[k] for k in safe_fields]
        # Handle JSON fields
        json_fields = ["context", "settings", "tags"]
        for i, k in enumerate(safe_fields.keys()):
            if k in json_fields and isinstance(values[i], (dict, list)):
                values[i] = json.dumps(values[i])
            elif k in ["updated_at", "completed_at", "paused_at", "expires_at"] and values[i]:
                if isinstance(values[i], datetime):
                    values[i] = values[i].isoformat()
        values.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        values.append(session_id)

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"UPDATE agent_sessions SET {sets} WHERE session_id = {p}",
            values,
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def increment_session_usage(
        self,
        session_id: str,
        request_delta: int = 0,
        total_tokens_delta: int = 0,
        total_input_delta: int = 0,
        total_output_delta: int = 0,
        message_delta: int = 0,
    ) -> bool:
        """Increment a session's cumulative usage counters by the given deltas.

        ``agent_sessions.{message_count,request_count,total_tokens,
        total_input_tokens,total_output_tokens}`` must be monotonically
        accumulated so that ``Σ milestone.phase_* == session.*`` holds (#1003).
        The previous per-call overwrite (update_session_fields) reset the column
        to each call's local count, breaking the invariant. Callers pass the
        per-call ``AgentTaskResult`` counters as deltas. ``message_delta`` lets
        transcript writers (which keep ``add_message`` side-effect-free via
        ``count_usage=False``) own ``message_count`` explicitly (#1128).
        """
        if not session_id:
            return False
        p = _param()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE agent_sessions
            SET message_count = COALESCE(message_count, 0) + {p},
                request_count = COALESCE(request_count, 0) + {p},
                total_tokens = COALESCE(total_tokens, 0) + {p},
                total_input_tokens = COALESCE(total_input_tokens, 0) + {p},
                total_output_tokens = COALESCE(total_output_tokens, 0) + {p},
                updated_at = {p}
            WHERE session_id = {p}
            """,
            (
                message_delta,
                request_delta,
                total_tokens_delta,
                total_input_delta,
                total_output_delta,
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                session_id,
            ),
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_id: Optional[int] = None,
        milestone_id: Optional[str] = None,
    ) -> list[SessionMessage]:
        """
        Get messages for a session.

        Args:
            session_id: Session ID.
            limit: Optional limit on number of messages.
            before_id: Get messages before this ID.
            milestone_id: Optional milestone filter.

        Returns:
            List of SessionMessage objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = f"SELECT * FROM session_messages WHERE session_id = {_param()}"
        params: list[Any] = [session_id]

        if milestone_id is not None:
            query += f" AND milestone_id = {_param()}"
            params.append(milestone_id)

        if before_id:
            query += f" AND id < {_param()}"
            params.append(before_id)

        query += " ORDER BY timestamp ASC"

        if limit:
            query += f" LIMIT {_param()}"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_message(row) for row in rows]

    # Default / max page sizes for message keyset pagination (Issue #241 #22).
    DEFAULT_MESSAGE_PAGE_SIZE = 100
    MAX_MESSAGE_PAGE_SIZE = 500

    @staticmethod
    def _raw_timestamp(value: Any) -> Optional[str]:
        """Return the canonical stored form of a timestamp column value.

        The cursor round-trips through the HTTP layer, so the timestamp must be
        a stable string that, when fed back into ``WHERE timestamp < ?``, matches
        stored rows exactly. We therefore return the naive-UTC ISO form (no
        timezone suffix) that ``add_message`` writes, rather than the
        display-formatted value produced by ``_format_dt`` (which appends
        ``+00:00`` and would break lexicographic comparison against stored
        values that lack the suffix).
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _desc_nulls_first_order() -> str:
        """ORDER BY clause that yields newest-first with NULLs at the very top.

        Reversing this yields the final oldest-first (``ASC NULLS LAST``) render
        order. Crucially, NULL timestamps land in the *first* (most-recent) page
        instead of being stranded past the cursor and silently dropped — the
        defensive net required while pre-migration NULL rows may still exist
        (Issue #241 #22, F7). After the NOT NULL migration backfills them, this
        branch is moot but harmless.
        """
        if is_postgresql():
            return "ORDER BY timestamp DESC NULLS FIRST, id DESC"
        # SQLite: synthesize NULLS FIRST via the IS NULL boolean (1 sorts first
        # under DESC). Older SQLite lacks NULLS FIRST/LAST keywords.
        return "ORDER BY (timestamp IS NULL) DESC, timestamp DESC, id DESC"

    def get_messages_page(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_timestamp: Optional[str] = None,
        before_id: Optional[int] = None,
        milestone_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return one page of session messages using composite-key keyset paging.

        Sort key is ``(timestamp ASC, id ASC)`` — ``id`` is the tiebreaker so the
        order is total even when timestamps collide or were backfilled out of
        insertion order (Issue #241 #22 review). The cursor is the *smallest*
        sort key among the retained rows, so the caller can request the next
        older page by passing it back as ``(before_timestamp, before_id)``.

        Pages are fetched newest-first (``DESC``) then reversed in-memory, which
        — unlike ``WHERE id<cursor ORDER BY timestamp ASC LIMIT n`` — returns the
        page immediately adjacent to the cursor rather than the globally oldest
        rows.

        Args:
            session_id: Session ID.
            limit: Page size (clamped to ``[1, MAX]``; defaults to
                ``DEFAULT_MESSAGE_PAGE_SIZE``).
            before_timestamp: Cursor timestamp (raw stored ISO form). When
                omitted, the most-recent page (tail) is returned.
            before_id: Cursor id, the tiebreaker paired with ``before_timestamp``.
            milestone_id: Optional milestone filter.

        Returns:
            Dict ``{messages, has_more, next_cursor}`` where ``next_cursor`` is
            ``{"timestamp": str, "id": int}`` of the oldest retained message, or
            ``None`` when ``has_more`` is False.
        """
        if limit is None or limit <= 0:
            limit = self.DEFAULT_MESSAGE_PAGE_SIZE
        limit = min(limit, self.MAX_MESSAGE_PAGE_SIZE)

        # Fetch limit+1 to detect whether an older page exists, without a COUNT.
        fetch_n = limit + 1

        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = [f"session_id = {_param()}"]
        params: list[Any] = [session_id]
        if milestone_id is not None:
            conditions.append(f"milestone_id = {_param()}")
            params.append(milestone_id)

        if before_timestamp is not None and before_id is not None:
            # Composite keyset: rows strictly less than the cursor in (ts, id)
            # total order. Expanded to the portable OR form so both PG and
            # SQLite avoid row-value syntax differences.
            conditions.append(
                f"(timestamp < {_param()} OR (timestamp = {_param()} AND id < {_param()}))"
            )
            params.extend([before_timestamp, before_timestamp, before_id])

        where_clause = " AND ".join(conditions)
        query = (
            f"SELECT * FROM session_messages WHERE {where_clause} "
            f"{self._desc_nulls_first_order()} LIMIT {_param()}"
        )
        cursor.execute(query, params + [fetch_n])
        rows = cursor.fetchall()
        conn.close()

        has_more = len(rows) > limit
        kept = rows[:limit]
        # Reverse newest-first -> oldest-first for rendering.
        kept = list(reversed(kept))
        messages = [self._row_to_message(row) for row in kept]

        next_cursor = None
        if has_more and messages:
            oldest = messages[0]
            oldest_ts = self._raw_timestamp(
                oldest.timestamp if oldest.timestamp is not None else oldest.source_timestamp
            )
            if oldest_ts is not None and oldest.id is not None:
                next_cursor = {"timestamp": oldest_ts, "id": oldest.id}

        return {"messages": messages, "has_more": has_more, "next_cursor": next_cursor}

    def count_messages(self, session_id: str, milestone_id: Optional[str] = None) -> int:
        """Count messages in a session, optionally scoped to a milestone.

        Used for the pagination ``total`` indicator. ``agent_sessions.message_count``
        is session-level (not milestone-aware), so the milestone case needs a
        real conditional COUNT (Issue #241 #22 review).
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        if milestone_id is None:
            cursor.execute(
                f"SELECT COUNT(*) AS c FROM session_messages WHERE session_id = {_param()}",
                (session_id,),
            )
        else:
            cursor.execute(
                f"SELECT COUNT(*) AS c FROM session_messages "
                f"WHERE session_id = {_param()} AND milestone_id = {_param()}",
                (session_id, milestone_id),
            )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return 0
        value = row["c"] if isinstance(row, dict) else row[0]
        return int(value or 0)

    @staticmethod
    def _normalize_message_timestamp(value: Optional[Union[datetime, str]]) -> Optional[datetime]:
        """Normalize message timestamps to naive UTC datetimes for storage."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = f"{raw[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                return None
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        return None

    @staticmethod
    def _merge_message_metadata(
        existing: Optional[dict[str, Any]], incoming: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        """Merge transcript metadata without clobbering richer existing values."""
        merged = dict(existing or {})
        for key, value in (incoming or {}).items():
            if value in (None, "", [], {}):
                continue
            if merged.get(key) in (None, "", [], {}):
                merged[key] = value
        return merged

    @staticmethod
    def _serialize_json(value: Any) -> Optional[str]:
        """Serialize JSON payloads, preserving NULL for empty structured fields."""
        if value in (None, "", [], {}):
            return None
        return json.dumps(value)

    def _find_existing_message(
        self, cursor: Any, session_id: str, role: str, metadata: Optional[dict[str, Any]]
    ) -> Optional[SessionMessage]:
        """Find an existing transcript row for a stable external message identity."""
        metadata = metadata or {}
        external_message_id = self._extract_external_message_id(metadata)
        if external_message_id:
            cursor.execute(
                f"""
                SELECT *
                FROM session_messages
                WHERE session_id = {_param()}
                  AND role = {_param()}
                  AND external_message_id = {_param()}
                ORDER BY id ASC
                LIMIT 1
                """,
                (session_id, role, external_message_id),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_message(row)

        for identity_key in ("external_message_id", "message_id", "uuid"):
            identity_value = metadata.get(identity_key)
            if not identity_value:
                continue
            escaped = escape_like(str(identity_value))
            cursor.execute(
                f"""
                SELECT *
                FROM session_messages
                WHERE session_id = {_param()}
                  AND role = {_param()}
                  AND metadata LIKE {_param()}
                  ESCAPE '\\'
                ORDER BY id ASC
                LIMIT 1
                """,
                (session_id, role, f'%"{identity_key}": "{escaped}"%'),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_message(row)

        return None

    def append_transcript_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens_used: int = 0,
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        milestone_id: str = "",
        timestamp: Optional[Union[datetime, str]] = None,
        source: str = "",
        external_message_id: str = "",
    ) -> Optional[SessionMessage]:
        """Append a transcript row without incrementing request/token summary.

        This is the preferred writer for transcript-producing code paths
        (autonomous runner, remote streaming, proxy transcript sync, history
        import). Those paths should update usage summary separately via
        ``increment_session_usage`` or an equivalent owner.
        """
        merged_metadata = dict(metadata or {})
        if source and not merged_metadata.get("source"):
            merged_metadata["source"] = source
        if external_message_id and not merged_metadata.get("external_message_id"):
            merged_metadata["external_message_id"] = external_message_id
        return self.add_message(
            session_id=session_id,
            role=role,
            content=content,
            tokens_used=tokens_used,
            model=model,
            metadata=merged_metadata or None,
            milestone_id=milestone_id,
            count_usage=False,
            timestamp=timestamp,
        )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens_used: int = 0,
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        milestone_id: str = "",
        source: str = "",
        count_usage: bool = True,
        timestamp: Optional[Union[datetime, str]] = None,
    ) -> Optional[SessionMessage]:
        """
        Add a message to a session.

        Args:
            session_id: Session ID.
            role: Message role (user, assistant, system, tool).
            content: Message content.
            tokens_used: Tokens consumed by this message.
            model: Optional model name.
            metadata: Optional metadata dict.
            source: Message source tag for downstream filtering.
            count_usage: When True (default), accumulate request_count (assistant
                messages) and total_tokens on the session. Autonomous local runs
                pass False because the agent runner owns those counters via
                increment_session_usage (avoids double-count); non-autonomous
                callers (remote, session_sync) keep True as they rely on this.
            timestamp: Optional source timestamp. When omitted, store the current
                UTC time. Historical sync/import paths should pass the original
                message timestamp so transcript order remains stable.

        Returns:
            SessionMessage or None if session not found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute(
            f"SELECT session_id FROM agent_sessions WHERE session_id = {_param()}",
            (session_id,),
        )
        if not cursor.fetchone():
            conn.close()
            return None

        now = self._normalize_message_timestamp(timestamp) or datetime.now(timezone.utc).replace(
            tzinfo=None
        )
        metadata = metadata or {}
        extracted_source = source or self._extract_source(metadata)
        external_message_id = self._extract_external_message_id(metadata)
        content_blocks = self._extract_content_blocks(metadata)

        existing = self._find_existing_message(cursor, session_id, role, metadata)
        if existing:
            merged_metadata = self._merge_message_metadata(existing.metadata, metadata)
            merged_content = existing.content or _sanitize_text_value(content) or ""
            candidate_content = _sanitize_text_value(content) or ""
            if candidate_content and len(candidate_content) > len(merged_content or ""):
                merged_content = candidate_content
            merged_tokens = max(existing.tokens_used or 0, tokens_used or 0)
            merged_model = existing.model or model
            merged_milestone = existing.milestone_id or milestone_id
            merged_timestamp = existing.source_timestamp or now
            merged_source = existing.source or extracted_source
            merged_external_message_id = (
                existing.external_message_id
                or external_message_id
                or self._extract_external_message_id(merged_metadata)
            )
            merged_content_blocks = (
                existing.content_blocks
                or content_blocks
                or self._extract_content_blocks(merged_metadata)
            )

            cursor.execute(
                f"""
                UPDATE session_messages
                SET content = {_param()},
                    tokens_used = {_param()},
                    model = {_param()},
                    source_timestamp = {_param()},
                    metadata = {_param()},
                    milestone_id = {_param()},
                    source = {_param()},
                    external_message_id = {_param()},
                    content_blocks = {_param()}
                WHERE id = {_param()}
                """,
                (
                    merged_content,
                    merged_tokens,
                    merged_model,
                    merged_timestamp.isoformat() if merged_timestamp else None,
                    _sanitize_text_value(json.dumps(merged_metadata)),
                    merged_milestone,
                    merged_source,
                    merged_external_message_id,
                    self._serialize_json(merged_content_blocks),
                    existing.id,
                ),
            )
            conn.commit()
            conn.close()

            existing.content = merged_content
            existing.tokens_used = merged_tokens
            existing.model = merged_model
            existing.source_timestamp = merged_timestamp
            existing.metadata = merged_metadata
            existing.milestone_id = merged_milestone
            existing.source = merged_source
            existing.external_message_id = merged_external_message_id
            existing.content_blocks = merged_content_blocks
            existing._was_inserted = False
            return existing

        message = SessionMessage(
            session_id=session_id,
            role=role,
            content=_sanitize_text_value(content) or "",
            tokens_used=tokens_used,
            model=model,
            timestamp=now,
            source_timestamp=now,
            metadata=metadata,
            milestone_id=milestone_id,
            source=extracted_source,
            external_message_id=external_message_id,
            content_blocks=content_blocks,
        )

        cursor.execute(
            f"""
            INSERT INTO session_messages (
                session_id, role, content, tokens_used, model, timestamp,
                source_timestamp, metadata, milestone_id, source,
                external_message_id, content_blocks
            )
            VALUES ({_params(12)})
        """,
            (
                message.session_id,
                message.role,
                message.content,
                message.tokens_used,
                message.model,
                message.timestamp.isoformat() if message.timestamp else None,
                message.source_timestamp.isoformat() if message.source_timestamp else None,
                _sanitize_text_value(json.dumps(message.metadata)),
                milestone_id,
                message.source,
                message.external_message_id,
                self._serialize_json(message.content_blocks),
            ),
        )

        # When count_usage=True, accumulate message_count/request_count/
        # total_tokens on the session — remote_session_manager and session_sync
        # rely on add_message for these summaries. When count_usage=False (the
        # transcript path used by append_transcript_message), leave the session
        # summary untouched: those callers own their counters via
        # increment_session_usage (per-call delta), so counting here too would
        # double-count (#1003 / #1007 review) and break the side-effect-free
        # transcript contract (#1128).
        request_count_increment = 1 if (count_usage and role == "assistant") else 0
        if count_usage:
            cursor.execute(
                f"""
                UPDATE agent_sessions
                SET message_count = message_count + 1,
                    request_count = COALESCE(request_count, 0) + {_param()},
                    total_tokens = total_tokens + {_param()},
                    updated_at = {_param()}
                WHERE session_id = {_param()}
            """,
                (request_count_increment, tokens_used, now.isoformat(), session_id),
            )

        message.id = cursor.lastrowid
        message._was_inserted = True
        conn.commit()
        conn.close()

        return message

    def complete_session(self, session_id: str) -> bool:
        """
        Mark a session as completed and update project statistics.

        Args:
            session_id: Session ID to complete.

        Returns:
            bool: True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        now_iso = now.isoformat()

        # First, get session info for statistics update
        cursor.execute(
            f"""
            SELECT user_id, project_id, project_path, created_at, total_tokens
            FROM agent_sessions
            WHERE session_id = {_param()}
        """,
            (session_id,),
        )
        session_row = cursor.fetchone()

        # Update session status
        cursor.execute(
            f"""
            UPDATE agent_sessions
            SET status = {_param()}, completed_at = {_param()}, updated_at = {_param()}
            WHERE session_id = {_param()}
        """,
            (SessionStatus.COMPLETED.value, now_iso, now_iso, session_id),
        )

        success = cursor.rowcount > 0
        conn.commit()

        # Update project statistics if session has project_id and user_id
        if success and session_row:
            user_id = session_row["user_id"]
            project_id = session_row["project_id"]
            created_at_str = session_row["created_at"]
            total_tokens = session_row["total_tokens"] or 0

            if user_id and project_id:
                try:
                    # Calculate session duration in seconds
                    duration_seconds = 0
                    if created_at_str:
                        try:
                            # PostgreSQL returns datetime objects, SQLite returns strings
                            if isinstance(created_at_str, datetime):
                                created_at = created_at_str
                            else:
                                created_at = datetime.fromisoformat(created_at_str)
                            duration_seconds = int((now - created_at).total_seconds())
                            # Cap at reasonable maximum (24 hours)
                            duration_seconds = min(duration_seconds, 24 * 3600)
                        except Exception as e:
                            logger.warning(f"Failed to parse created_at: {e}")

                    # Update user_projects statistics
                    cursor.execute(
                        f"""
                        INSERT INTO user_projects (user_id, project_id, first_access_at, last_access_at,
                                                   total_sessions, total_tokens, total_requests, total_duration_seconds)
                        VALUES ({_params(2)}, {_param()}, {_param()}, 1, {_param()}, 1, {_param()})
                        ON CONFLICT (user_id, project_id) DO UPDATE SET
                            last_access_at = {_param()},
                            total_sessions = user_projects.total_sessions + 1,
                            total_tokens = user_projects.total_tokens + {_param()},
                            total_requests = user_projects.total_requests + 1,
                            total_duration_seconds = user_projects.total_duration_seconds + {_param()}
                    """,
                        (
                            user_id,
                            project_id,
                            now_iso,
                            now_iso,
                            total_tokens,
                            duration_seconds,
                            now_iso,
                            total_tokens,
                            duration_seconds,
                        ),
                    )
                    conn.commit()
                    logger.info(
                        f"Updated project stats for user {user_id}, project {project_id}: "
                        f"+{duration_seconds}s, +{total_tokens} tokens"
                    )
                except Exception as e:
                    logger.error(f"Failed to update project statistics: {e}")

        conn.close()

        if success:
            logger.info(f"Completed session: {session_id}")
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
        cursor.execute(f"DELETE FROM session_messages WHERE session_id = {_param()}", (session_id,))

        # Delete session
        cursor.execute(f"DELETE FROM agent_sessions WHERE session_id = {_param()}", (session_id,))

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
        limit: int = 20,
    ) -> dict[str, Any]:
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
        params: list[Any] = []

        if user_id is not None:
            conditions.append(f"user_id = {_param()}")
            params.append(user_id)

        if tool_name:
            conditions.append(f"tool_name = {_param()}")
            params.append(tool_name)

        if status:
            conditions.append(f"status = {_param()}")
            params.append(status)

        if session_type:
            conditions.append(f"session_type = {_param()}")
            params.append(session_type)

        if search:
            conditions.append(f"title LIKE {_param()} ESCAPE '\\'")
            params.append(f"%{escape_like(search)}%")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        cursor.execute(f"SELECT COUNT(*) as count FROM agent_sessions WHERE {where_clause}", params)
        total = cursor.fetchone()["count"]
        total_pages = (total + limit - 1) // limit if total > 0 else 1

        # Get paginated results
        offset = (page - 1) * limit
        cursor.execute(
            f"""
            SELECT * FROM agent_sessions
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT {_param()} OFFSET {_param()}
        """,
            params + [limit, offset],
        )

        rows = cursor.fetchall()
        conn.close()

        sessions = [self._row_to_session(row) for row in rows]

        return {
            "sessions": sessions,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }

    def get_active_sessions(self, user_id: Optional[int] = None) -> list[AgentSession]:
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
            cursor.execute(
                f"""
                SELECT * FROM agent_sessions
                WHERE user_id = {_param()} AND status = {_param()}
                ORDER BY updated_at DESC
            """,
                (user_id, SessionStatus.ACTIVE.value),
            )
        else:
            cursor.execute(
                f"""
                SELECT * FROM agent_sessions
                WHERE status = {_param()}
                ORDER BY updated_at DESC
            """,
                (SessionStatus.ACTIVE.value,),
            )

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

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_old)

        # Get expired session IDs
        cursor.execute(
            f"""
            SELECT session_id FROM agent_sessions
            WHERE expires_at < {_param()} OR updated_at < {_param()}
        """,
            (cutoff.isoformat(), cutoff.isoformat()),
        )

        session_ids = [row["session_id"] for row in cursor.fetchall()]

        # Delete messages and sessions
        for session_id in session_ids:
            cursor.execute(
                f"DELETE FROM session_messages WHERE session_id = {_param()}", (session_id,)
            )
            cursor.execute(
                f"DELETE FROM agent_sessions WHERE session_id = {_param()}", (session_id,)
            )

        conn.commit()
        conn.close()

        if session_ids:
            logger.info(f"Cleaned up {len(session_ids)} expired sessions")

        return len(session_ids)

    def get_session_stats(self, user_id: Optional[int] = None) -> dict[str, Any]:
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
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_sessions,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
                    SUM(total_tokens) as total_tokens,
                    SUM(message_count) as total_messages
                FROM agent_sessions
                WHERE user_id = {_param()}
            """,
                (user_id,),
            )
        else:
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_sessions,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
                    SUM(total_tokens) as total_tokens,
                    SUM(message_count) as total_messages
                FROM agent_sessions
            """
            )

        row = cursor.fetchone()
        conn.close()

        return {
            "total_sessions": row["total_sessions"] or 0,
            "active_sessions": row["active_sessions"] or 0,
            "completed_sessions": row["completed_sessions"] or 0,
            "total_tokens": row["total_tokens"] or 0,
            "total_messages": row["total_messages"] or 0,
        }

    def _row_to_session(self, row: Union[sqlite3.Row, dict]) -> AgentSession:
        """Convert a database row to AgentSession."""

        # Handle both sqlite3.Row and dict (PostgreSQL)
        def get_value(key: str):
            if isinstance(row, dict):
                return row.get(key)
            try:
                return row[key]
            except (KeyError, IndexError):
                return None

        # Helper to convert datetime values (PostgreSQL returns datetime objects)
        def parse_datetime(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value  # Already a datetime object (PostgreSQL)
            return datetime.fromisoformat(value)  # String (SQLite)

        return AgentSession(
            id=get_value("id"),
            session_id=get_value("session_id"),
            session_type=get_value("session_type") or SessionType.CHAT.value,
            title=get_value("title") or "",
            tool_name=get_value("tool_name"),
            host_name=get_value("host_name") or "localhost",
            user_id=get_value("user_id"),
            status=get_value("status") or SessionStatus.ACTIVE.value,
            context=json.loads(get_value("context")) if get_value("context") else {},
            settings=json.loads(get_value("settings")) if get_value("settings") else {},
            total_tokens=get_value("total_tokens") or 0,
            total_input_tokens=get_value("total_input_tokens") or 0,
            total_output_tokens=get_value("total_output_tokens") or 0,
            message_count=get_value("message_count") or 0,
            request_count=get_value("request_count") or 0,
            model=get_value("model"),
            tags=json.loads(get_value("tags")) if get_value("tags") else [],
            created_at=parse_datetime(get_value("created_at")),
            updated_at=parse_datetime(get_value("updated_at")),
            completed_at=parse_datetime(get_value("completed_at")),
            expires_at=parse_datetime(get_value("expires_at")),
            project_id=get_value("project_id"),
            project_path=get_value("project_path"),
            workspace_type=get_value("workspace_type") or "local",
            remote_machine_id=get_value("remote_machine_id"),
            paused_at=parse_datetime(get_value("paused_at")),
            cli_session_id=get_value("cli_session_id") or "",
        )

    def _row_to_message(self, row: Union[sqlite3.Row, dict]) -> SessionMessage:
        """Convert a database row to SessionMessage."""

        # Handle both sqlite3.Row and dict (PostgreSQL)
        def get_value(key: str):
            if isinstance(row, dict):
                return row.get(key)
            try:
                return row[key]
            except (KeyError, IndexError):
                return None

        def parse_datetime(value):
            """Parse datetime from string or return datetime object (PostgreSQL)."""
            if value is None:
                return None
            if isinstance(value, datetime):
                return value  # PostgreSQL returns datetime objects
            return datetime.fromisoformat(value)  # String (SQLite)

        return SessionMessage(
            id=get_value("id"),
            session_id=get_value("session_id"),
            role=get_value("role"),
            content=get_value("content") or "",
            tokens_used=get_value("tokens_used") or 0,
            model=get_value("model"),
            timestamp=parse_datetime(get_value("timestamp")),
            source_timestamp=parse_datetime(get_value("source_timestamp")),
            metadata=self._decode_message_metadata(row),
            milestone_id=get_value("milestone_id") or "",
            source=get_value("source") or "",
            external_message_id=get_value("external_message_id") or "",
            content_blocks=self._decode_content_blocks(get_value("content_blocks")),
        )

    @staticmethod
    def _decode_content_blocks(raw_value: Any) -> list[Any]:
        """Decode structured content blocks from a JSON column."""
        if not raw_value:
            return []
        if isinstance(raw_value, list):
            return raw_value
        try:
            parsed = json.loads(raw_value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _decode_message_metadata(self, row: Union[sqlite3.Row, dict]) -> dict[str, Any]:
        """Decode metadata and rehydrate structured columns for API callers."""

        def get_value(key: str):
            if isinstance(row, dict):
                return row.get(key)
            try:
                return row[key]
            except (KeyError, IndexError):
                return None

        metadata: dict[str, Any] = (
            json.loads(get_value("metadata")) if get_value("metadata") else {}
        )
        content_blocks = self._decode_content_blocks(get_value("content_blocks"))
        source = get_value("source") or ""
        external_message_id = get_value("external_message_id") or ""

        if content_blocks and not metadata.get("content_blocks"):
            metadata["content_blocks"] = content_blocks
        if source and not metadata.get("source"):
            metadata["source"] = source
        if external_message_id and not metadata.get("external_message_id"):
            metadata["external_message_id"] = external_message_id

        return metadata


def get_ddl_statements() -> list[str]:
    """Return DDL statements for session manager tables."""
    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        f"""
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
            expires_at TIMESTAMP,
            cli_session_id TEXT DEFAULT ''
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS session_messages (
            id {id_type},
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tokens_used INTEGER DEFAULT 0,
            model TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            milestone_id TEXT DEFAULT '',
            source TEXT DEFAULT '',
            source_timestamp TIMESTAMP,
            external_message_id TEXT DEFAULT '',
            content_blocks TEXT,
            FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_session_id
        ON agent_sessions(session_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_id
        ON agent_sessions(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_status
        ON agent_sessions(status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_tool_name
        ON agent_sessions(tool_name)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_session_messages_session_id
        ON session_messages(session_id)
        """,
        "ALTER TABLE agent_sessions ADD COLUMN workspace_type TEXT DEFAULT 'local'",
        "ALTER TABLE agent_sessions ADD COLUMN remote_machine_id TEXT",
        "ALTER TABLE agent_sessions ADD COLUMN request_count INTEGER DEFAULT 0",
        "ALTER TABLE agent_sessions ADD COLUMN paused_at TIMESTAMP",
        "ALTER TABLE agent_sessions ADD COLUMN cli_session_id TEXT DEFAULT ''",
        "ALTER TABLE session_messages ADD COLUMN source_timestamp TIMESTAMP",
        "ALTER TABLE session_messages ADD COLUMN external_message_id TEXT DEFAULT ''",
        "ALTER TABLE session_messages ADD COLUMN content_blocks TEXT",
        """
        CREATE INDEX IF NOT EXISTS idx_session_messages_external_message_id
        ON session_messages(session_id, external_message_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_session_messages_source
        ON session_messages(session_id, source)
        """,
    ]


# Module-level singleton
_instance: Optional[SessionManager] = None
_instance_lock = threading.Lock()


def get_session_manager() -> SessionManager:
    """Get the module-level SessionManager singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = SessionManager()
    return _instance
