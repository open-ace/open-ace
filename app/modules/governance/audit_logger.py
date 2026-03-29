#!/usr/bin/env python3
"""
Open ACE - Audit Logger Module

Provides comprehensive audit logging for enterprise compliance and security.
Records all user actions, system events, and data access for accountability.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from app.repositories.database import Database

logger = logging.getLogger(__name__)


class AuditAction(Enum):
    """Enumeration of auditable actions."""

    # Authentication actions
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    SESSION_EXPIRED = "session_expired"

    # User management actions
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_PASSWORD_CHANGE = "user_password_change"
    USER_ROLE_CHANGE = "user_role_change"
    USER_STATUS_CHANGE = "user_status_change"

    # Permission actions
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"

    # Quota actions
    QUOTA_UPDATE = "quota_update"
    QUOTA_ALERT = "quota_alert"
    QUOTA_EXCEEDED = "quota_exceeded"

    # Data access actions
    DATA_VIEW = "data_view"
    DATA_EXPORT = "data_export"
    DATA_IMPORT = "data_import"
    DATA_DELETE = "data_delete"

    # System actions
    SYSTEM_CONFIG_CHANGE = "system_config_change"
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"

    # Content filter actions
    CONTENT_BLOCKED = "content_blocked"
    CONTENT_FLAGGED = "content_flagged"


class AuditSeverity(Enum):
    """Severity levels for audit events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditLog:
    """Audit log entry data model."""

    id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str = ""
    severity: str = "info"
    resource_type: str = ""
    resource_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_id": self.user_id,
            "username": self.username,
            "action": self.action,
            "severity": self.severity,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            "success": self.success,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AuditLog":
        """Create from dictionary."""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        return cls(
            id=data.get("id"),
            timestamp=timestamp,
            user_id=data.get("user_id"),
            username=data.get("username"),
            action=data.get("action", ""),
            severity=data.get("severity", "info"),
            resource_type=data.get("resource_type", ""),
            resource_id=data.get("resource_id"),
            details=data.get("details", {}),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            session_id=data.get("session_id"),
            success=data.get("success", True),
            error_message=data.get("error_message"),
        )


class AuditLogger:
    """
    Audit logging service for enterprise compliance.

    Features:
    - Records all user actions with context
    - Supports querying and filtering
    - Automatic cleanup of old logs
    - Export capabilities for compliance reporting
    """

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize audit logger.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()
        # Table structure managed by Alembic migrations

    def log(
        self,
        action: str,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        severity: str = "info",
        resource_type: str = "",
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        Log an audit event.

        Args:
            action: Action being performed (use AuditAction enum values).
            user_id: ID of the user performing the action.
            username: Username of the user.
            severity: Severity level (info, warning, error, critical).
            resource_type: Type of resource being accessed.
            resource_id: ID of the specific resource.
            details: Additional details about the action.
            ip_address: Client IP address.
            user_agent: Client user agent.
            session_id: Session identifier.
            success: Whether the action was successful.
            error_message: Error message if action failed.

        Returns:
            bool: True if log was saved successfully.
        """
        try:
            details_json = json.dumps(details) if details else None

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_logs
                    (timestamp, user_id, username, action, severity, resource_type,
                     resource_id, details, ip_address, user_agent, session_id,
                     success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        datetime.utcnow(),
                        user_id,
                        username,
                        action,
                        severity,
                        resource_type,
                        resource_id,
                        details_json,
                        ip_address,
                        user_agent,
                        session_id,
                        1 if success else 0,
                        error_message,
                    ),
                )
                conn.commit()

            logger.debug(f"Audit log: {action} by user {username or user_id or 'system'}")
            return True

        except Exception as e:
            logger.error(f"Failed to save audit log: {e}")
            return False

    def log_action(
        self,
        action: AuditAction,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """
        Log an audit action using the AuditAction enum.

        Args:
            action: AuditAction enum value.
            user_id: ID of the user.
            username: Username.
            **kwargs: Additional arguments passed to log().

        Returns:
            bool: True if successful.
        """
        return self.log(action=action.value, user_id=user_id, username=username, **kwargs)

    def query(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        severity: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        success: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditLog]:
        """
        Query audit logs with filters.

        Args:
            user_id: Filter by user ID.
            username: Filter by username.
            action: Filter by action type.
            resource_type: Filter by resource type.
            severity: Filter by severity.
            start_time: Filter logs after this time.
            end_time: Filter logs before this time.
            success: Filter by success status.
            limit: Maximum number of results.
            offset: Offset for pagination.

        Returns:
            List[AuditLog]: List of matching audit logs.
        """
        conditions = []
        params = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if username:
            conditions.append("username = ?")
            params.append(username)

        if action:
            conditions.append("action = ?")
            params.append(action)

        if resource_type:
            conditions.append("resource_type = ?")
            params.append(resource_type)

        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)

        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        if success is not None:
            conditions.append("success = ?")
            params.append(1 if success else 0)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM audit_logs
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """

        rows = self.db.fetch_all(query, tuple(params + [limit, offset]))

        return [AuditLog.from_dict(row) for row in rows]

    def count(
        self,
        user_id: Optional[int] = None,
        action: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> int:
        """
        Count audit logs matching filters.

        Args:
            user_id: Filter by user ID.
            action: Filter by action type.
            start_time: Filter logs after this time.
            end_time: Filter logs before this time.

        Returns:
            int: Count of matching logs.
        """
        conditions = []
        params = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if action:
            conditions.append("action = ?")
            params.append(action)

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)

        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"SELECT COUNT(*) as count FROM audit_logs WHERE {where_clause}"
        result = self.db.fetch_one(query, tuple(params))

        return result["count"] if result else 0

    def get_user_activity(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """
        Get activity summary for a user.

        Args:
            user_id: User ID.
            days: Number of days to look back.

        Returns:
            Dict with activity summary.
        """
        start_time = datetime.utcnow() - timedelta(days=days)

        logs = self.query(user_id=user_id, start_time=start_time, limit=1000)

        # Group by action
        action_counts: Dict[str, int] = {}
        for log in logs:
            action_counts[log.action] = action_counts.get(log.action, 0) + 1

        return {
            "user_id": user_id,
            "period_days": days,
            "total_actions": len(logs),
            "action_breakdown": action_counts,
            "last_activity": logs[0].to_dict() if logs else None,
        }

    def cleanup_old_logs(self, days: int = 90) -> int:
        """
        Delete audit logs older than specified days.

        Args:
            days: Number of days to keep.

        Returns:
            int: Number of logs deleted.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM audit_logs WHERE timestamp < ?", (cutoff,))
                deleted = cursor.rowcount
                conn.commit()

            logger.info(f"Cleaned up {deleted} audit logs older than {days} days")
            return deleted

        except Exception as e:
            logger.error(f"Failed to cleanup audit logs: {e}")
            return 0

    def export_logs(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        format: str = "json",
    ) -> str:
        """
        Export audit logs for compliance reporting.

        Args:
            start_time: Start of date range.
            end_time: End of date range.
            format: Export format ('json' or 'csv').

        Returns:
            str: Exported data as string.
        """
        logs = self.query(start_time=start_time, end_time=end_time, limit=10000)

        if format == "json":
            return json.dumps([log.to_dict() for log in logs], indent=2, default=str)
        elif format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            # Header
            writer.writerow(
                [
                    "id",
                    "timestamp",
                    "user_id",
                    "username",
                    "action",
                    "severity",
                    "resource_type",
                    "resource_id",
                    "ip_address",
                    "success",
                    "error_message",
                ]
            )

            # Data
            for log in logs:
                writer.writerow(
                    [
                        log.id,
                        log.timestamp.isoformat() if log.timestamp else "",
                        log.user_id or "",
                        log.username or "",
                        log.action,
                        log.severity,
                        log.resource_type,
                        log.resource_id or "",
                        log.ip_address or "",
                        log.success,
                        log.error_message or "",
                    ]
                )

            return output.getvalue()
        else:
            raise ValueError(f"Unsupported format: {format}")
