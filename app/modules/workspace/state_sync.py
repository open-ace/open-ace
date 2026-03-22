#!/usr/bin/env python3
"""
Open ACE - State Sync Module

Provides real-time state synchronization between Workspace and Management Hub.
Supports WebSocket connections for live updates and event broadcasting.
"""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

from app.repositories.database import DB_PATH, is_postgresql

logger = logging.getLogger(__name__)


class SyncEventType(Enum):
    """Sync event types."""
    SESSION_START = 'session_start'
    SESSION_END = 'session_end'
    SESSION_UPDATE = 'session_update'
    MESSAGE_SENT = 'message_sent'
    MESSAGE_RECEIVED = 'message_received'
    TOOL_CALL = 'tool_call'
    TOOL_RESULT = 'tool_result'
    ERROR = 'error'
    ACTIVITY = 'activity'
    STATUS_CHANGE = 'status_change'
    METRICS_UPDATE = 'metrics_update'


@dataclass
class SyncEvent:
    """A synchronization event."""
    event_id: str
    event_type: str
    timestamp: datetime
    source: str  # workspace, tool, system
    session_id: Optional[str] = None
    user_id: Optional[int] = None
    tool_name: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'event_id': self.event_id,
            'event_type': self.event_type,
            'timestamp': self.timestamp.isoformat(),
            'source': self.source,
            'session_id': self.session_id,
            'user_id': self.user_id,
            'tool_name': self.tool_name,
            'data': self.data,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SyncEvent':
        """Create from dictionary."""
        return cls(
            event_id=data.get('event_id', ''),
            event_type=data.get('event_type', ''),
            timestamp=datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else datetime.utcnow(),
            source=data.get('source', ''),
            session_id=data.get('session_id'),
            user_id=data.get('user_id'),
            tool_name=data.get('tool_name'),
            data=data.get('data', {}),
            metadata=data.get('metadata', {}),
        )

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> 'SyncEvent':
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))


@dataclass
class SyncState:
    """Synchronization state for a client."""
    client_id: str
    connected_at: datetime
    last_activity: datetime
    subscriptions: Set[str] = field(default_factory=set)
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            'client_id': self.client_id,
            'connected_at': self.connected_at.isoformat(),
            'last_activity': self.last_activity.isoformat(),
            'subscriptions': list(self.subscriptions),
            'user_id': self.user_id,
            'session_id': self.session_id,
            'metadata': self.metadata,
        }

    def is_active(self, timeout_seconds: int = 60) -> bool:
        """Check if client is still active."""
        return (datetime.utcnow() - self.last_activity).total_seconds() < timeout_seconds


class StateSyncManager:
    """
    Manager for real-time state synchronization.

    Provides:
    - Event broadcasting to connected clients
    - Subscription management
    - Event persistence for replay
    - Activity tracking
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the state sync manager.

        Args:
            db_path: Optional custom database path.
        """
        self.db_path = db_path or str(DB_PATH)
        self._clients: Dict[str, SyncState] = {}
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._ensure_tables()

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection (SQLite or PostgreSQL)."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                url = os.environ['DATABASE_URL']
                conn = psycopg2.connect(url)
                conn.cursor_factory = RealDictCursor
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

        # Create sync_events table for event persistence
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS sync_events (
                id {id_type},
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT,
                session_id TEXT,
                user_id INTEGER,
                tool_name TEXT,
                data TEXT,
                metadata TEXT
            )
        ''')

        # Create indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sync_events_timestamp
            ON sync_events(timestamp)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sync_events_session_id
            ON sync_events(session_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sync_events_user_id
            ON sync_events(user_id)
        ''')

        conn.commit()
        conn.close()

    def register_client(
        self,
        client_id: Optional[str] = None,
        user_id: Optional[int] = None,
        subscriptions: Optional[List[str]] = None
    ) -> SyncState:
        """
        Register a new client connection.

        Args:
            client_id: Optional client ID (auto-generated if not provided).
            user_id: Optional user ID.
            subscriptions: Optional list of event types to subscribe to.

        Returns:
            SyncState: The client's sync state.
        """
        if client_id is None:
            client_id = str(uuid.uuid4())

        now = datetime.utcnow()
        state = SyncState(
            client_id=client_id,
            connected_at=now,
            last_activity=now,
            subscriptions=set(subscriptions) if subscriptions else set(),
            user_id=user_id,
        )

        self._clients[client_id] = state
        logger.info(f"Client registered: {client_id}")

        # Emit connection event
        self.emit_event(SyncEvent(
            event_id=str(uuid.uuid4()),
            event_type=SyncEventType.ACTIVITY.value,
            timestamp=now,
            source='system',
            user_id=user_id,
            data={'action': 'client_connected', 'client_id': client_id}
        ))

        return state

    def unregister_client(self, client_id: str) -> bool:
        """
        Unregister a client connection.

        Args:
            client_id: Client ID to unregister.

        Returns:
            bool: True if client was unregistered.
        """
        if client_id in self._clients:
            state = self._clients.pop(client_id)

            # Emit disconnection event
            self.emit_event(SyncEvent(
                event_id=str(uuid.uuid4()),
                event_type=SyncEventType.ACTIVITY.value,
                timestamp=datetime.utcnow(),
                source='system',
                user_id=state.user_id,
                data={'action': 'client_disconnected', 'client_id': client_id}
            ))

            logger.info(f"Client unregistered: {client_id}")
            return True
        return False

    def subscribe(self, client_id: str, event_types: List[str]) -> bool:
        """
        Subscribe a client to specific event types.

        Args:
            client_id: Client ID.
            event_types: List of event types to subscribe to.

        Returns:
            bool: True if subscription was successful.
        """
        if client_id not in self._clients:
            return False

        self._clients[client_id].subscriptions.update(event_types)
        self._clients[client_id].last_activity = datetime.utcnow()
        logger.debug(f"Client {client_id} subscribed to: {event_types}")
        return True

    def unsubscribe(self, client_id: str, event_types: List[str]) -> bool:
        """
        Unsubscribe a client from specific event types.

        Args:
            client_id: Client ID.
            event_types: List of event types to unsubscribe from.

        Returns:
            bool: True if unsubscription was successful.
        """
        if client_id not in self._clients:
            return False

        self._clients[client_id].subscriptions.difference_update(event_types)
        self._clients[client_id].last_activity = datetime.utcnow()
        return True

    def emit_event(self, event: SyncEvent) -> None:
        """
        Emit a sync event to all subscribed clients.

        Args:
            event: The event to emit.
        """
        # Persist event
        self._persist_event(event)

        # Queue for async processing
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event")

        # Call registered handlers
        handlers = self._handlers.get(event.event_type, []) + self._handlers.get('*', [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Handler error for {event.event_type}: {e}")

        logger.debug(f"Emitted event: {event.event_type} ({event.event_id})")

    def _persist_event(self, event: SyncEvent) -> None:
        """Persist an event to the database."""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT OR IGNORE INTO sync_events
                (event_id, event_type, timestamp, source, session_id, user_id, tool_name, data, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                event.event_id,
                event.event_type,
                event.timestamp.isoformat(),
                event.source,
                event.session_id,
                event.user_id,
                event.tool_name,
                json.dumps(event.data),
                json.dumps(event.metadata)
            ))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to persist event: {e}")
        finally:
            conn.close()

    def get_events(
        self,
        event_type: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[SyncEvent]:
        """
        Get events from the persistence store.

        Args:
            event_type: Filter by event type.
            session_id: Filter by session ID.
            user_id: Filter by user ID.
            since: Get events after this timestamp.
            limit: Maximum number of events to return.

        Returns:
            List of SyncEvent objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = []
        params = []

        if event_type:
            conditions.append('event_type = ?')
            params.append(event_type)

        if session_id:
            conditions.append('session_id = ?')
            params.append(session_id)

        if user_id:
            conditions.append('user_id = ?')
            params.append(user_id)

        if since:
            conditions.append('timestamp > ?')
            params.append(since.isoformat())

        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        cursor.execute(f'''
            SELECT * FROM sync_events
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        ''', params + [limit])

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_event(row) for row in rows]

    def register_handler(self, event_type: str, handler: Callable) -> None:
        """
        Register a handler for a specific event type.

        Args:
            event_type: Event type to handle (or '*' for all).
            handler: Handler function.
        """
        self._handlers[event_type].append(handler)
        logger.info(f"Registered handler for event type: {event_type}")

    def unregister_handler(self, event_type: str, handler: Callable) -> bool:
        """
        Unregister a handler.

        Args:
            event_type: Event type.
            handler: Handler function to remove.

        Returns:
            bool: True if handler was removed.
        """
        if event_type in self._handlers and handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
            return True
        return False

    def update_client_activity(self, client_id: str, session_id: Optional[str] = None) -> bool:
        """
        Update client activity timestamp.

        Args:
            client_id: Client ID.
            session_id: Optional session ID to associate.

        Returns:
            bool: True if update was successful.
        """
        if client_id not in self._clients:
            return False

        self._clients[client_id].last_activity = datetime.utcnow()
        if session_id:
            self._clients[client_id].session_id = session_id
        return True

    def get_client_state(self, client_id: str) -> Optional[SyncState]:
        """
        Get a client's sync state.

        Args:
            client_id: Client ID.

        Returns:
            SyncState or None if not found.
        """
        return self._clients.get(client_id)

    def get_active_clients(self, user_id: Optional[int] = None) -> List[SyncState]:
        """
        Get all active clients.

        Args:
            user_id: Optional user ID filter.

        Returns:
            List of active SyncState objects.
        """
        clients = [c for c in self._clients.values() if c.is_active()]

        if user_id:
            clients = [c for c in clients if c.user_id == user_id]

        return clients

    def broadcast_to_subscribers(self, event: SyncEvent) -> int:
        """
        Broadcast an event to all subscribed clients.

        Args:
            event: The event to broadcast.

        Returns:
            int: Number of clients that received the event.
        """
        count = 0
        for client_id, state in self._clients.items():
            if event.event_type in state.subscriptions or '*' in state.subscriptions:
                # In a real implementation, this would send via WebSocket
                # For now, we just track that the client would receive it
                count += 1
                logger.debug(f"Event {event.event_id} sent to client {client_id}")

        return count

    def cleanup_inactive_clients(self, timeout_seconds: int = 300) -> int:
        """
        Remove inactive clients.

        Args:
            timeout_seconds: Timeout for inactivity.

        Returns:
            int: Number of clients removed.
        """
        inactive = [
            client_id for client_id, state in self._clients.items()
            if not state.is_active(timeout_seconds)
        ]

        for client_id in inactive:
            self.unregister_client(client_id)

        if inactive:
            logger.info(f"Cleaned up {len(inactive)} inactive clients")

        return len(inactive)

    def cleanup_old_events(self, days_old: int = 7) -> int:
        """
        Clean up old events from the database.

        Args:
            days_old: Delete events older than this many days.

        Returns:
            int: Number of events deleted.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = datetime.utcnow() - timedelta(days=days_old)

        cursor.execute('DELETE FROM sync_events WHERE timestamp < ?', (cutoff.isoformat(),))
        deleted = cursor.rowcount

        conn.commit()
        conn.close()

        if deleted:
            logger.info(f"Cleaned up {deleted} old events")

        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """
        Get synchronization statistics.

        Returns:
            Dict with sync statistics.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) as count FROM sync_events')
        total_events = cursor.fetchone()['count']

        cursor.execute('''
            SELECT COUNT(*) as count FROM sync_events
            WHERE timestamp > ?
        ''', ((datetime.utcnow() - timedelta(hours=1)).isoformat(),))
        events_last_hour = cursor.fetchone()['count']

        conn.close()

        return {
            'connected_clients': len(self._clients),
            'active_clients': len(self.get_active_clients()),
            'total_events': total_events,
            'events_last_hour': events_last_hour,
            'event_queue_size': self._event_queue.qsize(),
            'registered_handlers': sum(len(h) for h in self._handlers.values()),
        }

    def _row_to_event(self, row: sqlite3.Row) -> SyncEvent:
        """Convert a database row to SyncEvent."""
        return SyncEvent(
            event_id=row['event_id'],
            event_type=row['event_type'],
            timestamp=datetime.fromisoformat(row['timestamp']) if row['timestamp'] else datetime.utcnow(),
            source=row['source'] or '',
            session_id=row['session_id'],
            user_id=row['user_id'],
            tool_name=row['tool_name'],
            data=json.loads(row['data']) if row['data'] else {},
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
        )


# Global state sync manager instance
_state_sync_manager: Optional[StateSyncManager] = None


def get_state_sync_manager() -> StateSyncManager:
    """
    Get the global state sync manager instance.

    Returns:
        StateSyncManager: The global instance.
    """
    global _state_sync_manager
    if _state_sync_manager is None:
        _state_sync_manager = StateSyncManager()
    return _state_sync_manager
