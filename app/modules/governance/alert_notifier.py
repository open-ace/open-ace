#!/usr/bin/env python3
"""
Open ACE - Alert Notifier Module

Provides real-time alert notification system for:
- Quota alerts (approaching limits)
- System alerts (errors, warnings)
- Security alerts (suspicious activity)

Supports WebSocket push, email, and webhook notifications.
"""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

from app.repositories.database import DB_PATH, is_postgresql, get_database_url, adapt_sql

logger = logging.getLogger(__name__)


class AlertType(Enum):
    """Alert types."""

    QUOTA = "quota"
    SYSTEM = "system"
    SECURITY = "security"
    PERFORMANCE = "performance"


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Alert data structure."""

    alert_id: str
    alert_type: str
    severity: str
    title: str
    message: str
    user_id: Optional[int] = None
    username: Optional[str] = None
    tool_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    read: bool = False
    action_url: Optional[str] = None
    action_text: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "user_id": self.user_id,
            "username": self.username,
            "tool_name": self.tool_name,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "read": self.read,
            "action_url": self.action_url,
            "action_text": self.action_text,
        }


@dataclass
class NotificationPreference:
    """User notification preferences."""

    user_id: int
    email_enabled: bool = True
    push_enabled: bool = True
    webhook_url: Optional[str] = None
    alert_types: List[str] = field(default_factory=lambda: ["quota", "system", "security"])
    min_severity: str = "warning"  # info, warning, critical


class AlertNotifier:
    """Real-time alert notification manager."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the alert notifier.

        Args:
            db_path: Optional custom database path.
        """
        self.db_path = db_path or str(DB_PATH)
        self._subscribers: List[Callable] = []
        self._websocket_clients: Dict[str, Any] = {}  # client_id -> websocket
        self._user_clients: Dict[int, Set[str]] = {}  # user_id -> set of client_ids
        self._email_config: Dict[str, Any] = {}
        self._webhooks: Dict[str, str] = {}
        self._ensure_tables()

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection (SQLite or PostgreSQL)."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor

                url = get_database_url()
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

        # Create alerts table
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS alerts (
                id {id_type},
                alert_id TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                user_id INTEGER,
                username TEXT,
                tool_name TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read INTEGER DEFAULT 0,
                action_url TEXT,
                action_text TEXT
            )
        """
        )

        # Create notification_preferences table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id INTEGER PRIMARY KEY,
                email_enabled INTEGER DEFAULT 1,
                push_enabled INTEGER DEFAULT 1,
                webhook_url TEXT,
                alert_types TEXT,
                min_severity TEXT DEFAULT 'warning'
            )
        """
        )

        # Create indexes
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_user_id
            ON alerts(user_id)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_created_at
            ON alerts(created_at)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_read
            ON alerts(read)
        """
        )

        conn.commit()
        conn.close()

    def subscribe(self, callback: Callable) -> None:
        """
        Subscribe to alert events.

        Args:
            callback: Function to call when an alert is created.
        """
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """
        Unsubscribe from alert events.

        Args:
            callback: Function to remove from subscribers.
        """
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def register_websocket(
        self, client_id: str, websocket: Any, user_id: Optional[int] = None
    ) -> None:
        """
        Register a WebSocket client.

        Args:
            client_id: Unique client identifier.
            websocket: WebSocket connection object.
            user_id: Optional user ID for targeted notifications.
        """
        self._websocket_clients[client_id] = websocket
        if user_id is not None:
            if user_id not in self._user_clients:
                self._user_clients[user_id] = set()
            self._user_clients[user_id].add(client_id)
        logger.info(f"Registered WebSocket client: {client_id} for user: {user_id}")

    def unregister_websocket(self, client_id: str) -> None:
        """
        Unregister a WebSocket client.

        Args:
            client_id: Client identifier to remove.
        """
        self._websocket_clients.pop(client_id, None)
        # Remove from user_clients
        for user_id, clients in list(self._user_clients.items()):
            clients.discard(client_id)
            if not clients:
                del self._user_clients[user_id]
        logger.info(f"Unregistered WebSocket client: {client_id}")

    def create_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        tool_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        action_url: Optional[str] = None,
        action_text: Optional[str] = None,
    ) -> Alert:
        """
        Create a new alert.

        Args:
            alert_type: Type of alert (quota, system, security, performance).
            severity: Severity level (info, warning, critical).
            title: Alert title.
            message: Detailed message.
            user_id: Optional user ID for targeted alert.
            username: Optional username.
            tool_name: Optional tool name.
            metadata: Optional additional metadata.
            action_url: Optional URL for action button.
            action_text: Optional text for action button.

        Returns:
            Alert: The created alert.
        """
        alert = Alert(
            alert_id=str(uuid.uuid4()),
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            user_id=user_id,
            username=username,
            tool_name=tool_name,
            metadata=metadata or {},
            action_url=action_url,
            action_text=action_text,
        )

        # Save to database
        self._save_alert(alert)

        # Notify subscribers
        for callback in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(alert))
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")

        logger.info(f"Created alert: [{severity}] {title}")
        return alert

    def _save_alert(self, alert: Alert) -> int:
        """Save alert to database."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
            INSERT INTO alerts
            (alert_id, alert_type, severity, title, message, user_id, username,
             tool_name, metadata, created_at, read, action_url, action_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
            ),
            (
                alert.alert_id,
                alert.alert_type,
                alert.severity,
                alert.title,
                alert.message,
                alert.user_id,
                alert.username,
                alert.tool_name,
                json.dumps(alert.metadata),
                alert.created_at.isoformat(),
                1 if alert.read else 0,
                alert.action_url,
                alert.action_text,
            ),
        )

        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return alert_id

    async def broadcast(self, alert: Alert, target_user_id: Optional[int] = None) -> None:
        """
        Broadcast alert to WebSocket clients.

        Args:
            alert: Alert to broadcast.
            target_user_id: Optional specific user to target.
        """
        alert_dict = alert.to_dict()
        message = json.dumps({"type": "alert", "data": alert_dict})

        if target_user_id is not None:
            # Send to specific user's clients
            client_ids = self._user_clients.get(target_user_id, set())
            for client_id in list(client_ids):
                await self._send_to_client(client_id, message)
        else:
            # Broadcast to all clients
            for client_id in list(self._websocket_clients.keys()):
                await self._send_to_client(client_id, message)

    async def _send_to_client(self, client_id: str, message: str) -> bool:
        """Send message to a specific client."""
        ws = self._websocket_clients.get(client_id)
        if ws is None:
            return False

        try:
            await ws.send(message)
            return True
        except Exception as e:
            logger.error(f"Error sending to client {client_id}: {e}")
            self.unregister_websocket(client_id)
            return False

    def get_alerts(
        self,
        user_id: Optional[int] = None,
        alert_type: Optional[str] = None,
        severity: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Alert]:
        """
        Get alerts with filters.

        Args:
            user_id: Filter by user ID.
            alert_type: Filter by alert type.
            severity: Filter by severity.
            unread_only: Only return unread alerts.
            limit: Maximum number of alerts to return.
            offset: Offset for pagination.

        Returns:
            List of Alert objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = []
        params = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if alert_type:
            conditions.append("alert_type = ?")
            params.append(alert_type)

        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if unread_only:
            conditions.append("read = 0")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        cursor.execute(
            adapt_sql(
                f"""
            SELECT * FROM alerts
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
            ),
            params + [limit, offset],
        )

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_alert(row) for row in rows]

    def get_unread_count(self, user_id: Optional[int] = None) -> int:
        """
        Get count of unread alerts.

        Args:
            user_id: Optional user ID to filter by.

        Returns:
            Number of unread alerts.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id is not None:
            cursor.execute(
                adapt_sql("SELECT COUNT(*) as count FROM alerts WHERE user_id = ? AND read = 0"),
                (user_id,),
            )
        else:
            cursor.execute("SELECT COUNT(*) as count FROM alerts WHERE read = 0")

        count = cursor.fetchone()["count"]
        conn.close()

        return count or 0

    def mark_as_read(self, alert_id: str) -> bool:
        """
        Mark an alert as read.

        Args:
            alert_id: Alert ID to mark as read.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(adapt_sql("UPDATE alerts SET read = 1 WHERE alert_id = ?"), (alert_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def mark_all_as_read(self, user_id: Optional[int] = None) -> int:
        """
        Mark all alerts as read.

        Args:
            user_id: Optional user ID to filter by.

        Returns:
            Number of alerts marked as read.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id is not None:
            cursor.execute(
                adapt_sql("UPDATE alerts SET read = 1 WHERE user_id = ? AND read = 0"), (user_id,)
            )
        else:
            cursor.execute("UPDATE alerts SET read = 1 WHERE read = 0")

        count = cursor.rowcount
        conn.commit()
        conn.close()

        return count

    def delete_alert(self, alert_id: str) -> bool:
        """
        Delete an alert.

        Args:
            alert_id: Alert ID to delete.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(adapt_sql("DELETE FROM alerts WHERE alert_id = ?"), (alert_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def cleanup_old_alerts(self, days: int = 30) -> int:
        """
        Delete alerts older than specified days.

        Args:
            days: Number of days to keep.

        Returns:
            Number of deleted alerts.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute(adapt_sql("DELETE FROM alerts WHERE created_at < ? AND read = 1"), (cutoff,))

        count = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"Cleaned up {count} old alerts")
        return count

    def get_notification_preferences(self, user_id: int) -> NotificationPreference:
        """
        Get notification preferences for a user.

        Args:
            user_id: User ID.

        Returns:
            NotificationPreference object.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql("SELECT * FROM notification_preferences WHERE user_id = ?"), (user_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return NotificationPreference(
                user_id=row["user_id"],
                email_enabled=bool(row["email_enabled"]),
                push_enabled=bool(row["push_enabled"]),
                webhook_url=row["webhook_url"],
                alert_types=(
                    json.loads(row["alert_types"])
                    if row["alert_types"]
                    else ["quota", "system", "security"]
                ),
                min_severity=row["min_severity"] or "warning",
            )

        # Return default preferences
        return NotificationPreference(user_id=user_id)

    def set_notification_preferences(self, preferences: NotificationPreference) -> bool:
        """
        Set notification preferences for a user.

        Args:
            preferences: NotificationPreference object.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if is_postgresql():
            cursor.execute(
                """
                INSERT INTO notification_preferences
                (user_id, email_enabled, push_enabled, webhook_url, alert_types, min_severity)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    email_enabled = EXCLUDED.email_enabled,
                    push_enabled = EXCLUDED.push_enabled,
                    webhook_url = EXCLUDED.webhook_url,
                    alert_types = EXCLUDED.alert_types,
                    min_severity = EXCLUDED.min_severity
            """,
                (
                    preferences.user_id,
                    preferences.email_enabled,
                    preferences.push_enabled,
                    preferences.webhook_url,
                    json.dumps(preferences.alert_types),
                    preferences.min_severity,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT OR REPLACE INTO notification_preferences
                (user_id, email_enabled, push_enabled, webhook_url, alert_types, min_severity)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    preferences.user_id,
                    1 if preferences.email_enabled else 0,
                    1 if preferences.push_enabled else 0,
                    preferences.webhook_url,
                    json.dumps(preferences.alert_types),
                    preferences.min_severity,
                ),
            )

        conn.commit()
        conn.close()

        return True

    def _row_to_alert(self, row: sqlite3.Row) -> Alert:
        """Convert a database row to Alert."""
        return Alert(
            alert_id=row["alert_id"],
            alert_type=row["alert_type"],
            severity=row["severity"],
            title=row["title"],
            message=row["message"] or "",
            user_id=row["user_id"],
            username=row["username"],
            tool_name=row["tool_name"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else datetime.utcnow()
            ),
            read=bool(row["read"]),
            action_url=row["action_url"],
            action_text=row["action_text"],
        )


# Global alert notifier instance
_alert_notifier: Optional[AlertNotifier] = None


def get_alert_notifier(db_path: Optional[str] = None) -> AlertNotifier:
    """
    Get the global alert notifier instance.

    Args:
        db_path: Optional custom database path.

    Returns:
        AlertNotifier instance.
    """
    global _alert_notifier
    if _alert_notifier is None:
        _alert_notifier = AlertNotifier(db_path)
    return _alert_notifier


def create_quota_alert(
    user_id: int,
    username: str,
    usage_percent: float,
    quota_type: str = "tokens",
) -> Alert:
    """
    Create a quota alert.

    Args:
        user_id: User ID.
        username: Username.
        usage_percent: Usage percentage.
        quota_type: Type of quota (tokens or requests).

    Returns:
        Created Alert.
    """
    notifier = get_alert_notifier()

    if usage_percent >= 100:
        severity = AlertSeverity.CRITICAL.value
        title = f"Quota Exceeded: {quota_type.title()}"
        message = f"Your {quota_type} quota has been fully used. Please contact administrator."
    elif usage_percent >= 95:
        severity = AlertSeverity.CRITICAL.value
        title = f"Quota Critical: {quota_type.title()}"
        message = f"You have used {usage_percent:.1f}% of your {quota_type} quota."
    elif usage_percent >= 80:
        severity = AlertSeverity.WARNING.value
        title = f"Quota Warning: {quota_type.title()}"
        message = f"You have used {usage_percent:.1f}% of your {quota_type} quota."
    else:
        severity = AlertSeverity.INFO.value
        title = f"Quota Notice: {quota_type.title()}"
        message = f"You have used {usage_percent:.1f}% of your {quota_type} quota."

    return notifier.create_alert(
        alert_type=AlertType.QUOTA.value,
        severity=severity,
        title=title,
        message=message,
        user_id=user_id,
        username=username,
        metadata={
            "usage_percent": usage_percent,
            "quota_type": quota_type,
        },
        action_url="/report",
        action_text="View Usage",
    )


def create_system_alert(
    title: str,
    message: str,
    severity: str = AlertSeverity.WARNING.value,
    tool_name: Optional[str] = None,
) -> Alert:
    """
    Create a system alert.

    Args:
        title: Alert title.
        message: Alert message.
        severity: Severity level.
        tool_name: Optional tool name.

    Returns:
        Created Alert.
    """
    notifier = get_alert_notifier()
    return notifier.create_alert(
        alert_type=AlertType.SYSTEM.value,
        severity=severity,
        title=title,
        message=message,
        tool_name=tool_name,
    )


def create_security_alert(
    title: str,
    message: str,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    severity: str = AlertSeverity.CRITICAL.value,
) -> Alert:
    """
    Create a security alert.

    Args:
        title: Alert title.
        message: Alert message.
        user_id: Optional user ID.
        username: Optional username.
        severity: Severity level.

    Returns:
        Created Alert.
    """
    notifier = get_alert_notifier()
    return notifier.create_alert(
        alert_type=AlertType.SECURITY.value,
        severity=severity,
        title=title,
        message=message,
        user_id=user_id,
        username=username,
    )
