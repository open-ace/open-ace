"""
Open ACE - Audit Logger Module

Provides comprehensive audit logging for enterprise compliance and security.
Records all user actions, system events, and data access for accountability.

Resource conventions (governance audit logging):
- resource_id is ALWAYS a real entity primary key (user_id, rule_id, alert_id,
  machine_id, report_id, ...) or NULL. Never synthesize token prefixes, date
  hashes, or config keys — synthesized values cannot be traced back to a row.
- resource_name (human-readable) is injected into details["resource_name"] by
  log()/log_action(); caller-supplied details take precedence over it. Reuse
  an already-fetched entity for the name rather than issuing a second query
  (audit logging must stay lightweight).
- Operations with no single entity (login/logout/data_view/config singletons)
  leave resource_id NULL; the UI renders "-". That is correct, not a defect.
- details is persisted as JSON TEXT and always read back as a dict via
  AuditLog._parse_details (the single parse point, called from from_dict). Do
  NOT add audit_logs queries that bypass query()/from_dict — they would skip
  parsing and surface details as a raw string.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional, cast

from app.repositories.database import Database, adapt_boolean_value, adapt_sql

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
    CONTENT_WARNED = "content_warned"
    CONTENT_REDACTED = "content_redacted"

    # Remote agent actions
    AGENT_REGISTER = "agent_register"
    AGENT_TOKEN_ROTATE = "agent_token_rotate"
    AGENT_TOKEN_REVOKE = "agent_token_revoke"
    AGENT_AUTH_FAILURE = "agent_auth_failure"
    AGENT_RECONNECT = "agent_reconnect"


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
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str = ""
    severity: str = "info"
    resource_type: str = ""
    resource_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
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
            timestamp=(
                timestamp
                if isinstance(timestamp, datetime)
                else datetime.now(timezone.utc).replace(tzinfo=None)
            ),
            user_id=data.get("user_id"),
            username=data.get("username"),
            action=data.get("action", ""),
            severity=data.get("severity", "info"),
            resource_type=data.get("resource_type", ""),
            resource_id=data.get("resource_id"),
            details=cls._parse_details(data.get("details")),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            session_id=data.get("session_id"),
            success=data.get("success", True),
            error_message=data.get("error_message"),
        )

    @staticmethod
    def _parse_details(value: Any) -> dict[str, Any]:
        """Normalize the ``details`` column value to a dict.

        ``details`` is persisted as JSON TEXT (written via ``json.dumps``).
        Rows read back from the database may surface as ``None`` (SQL NULL),
        an empty string, an already-parsed dict, a valid JSON string, or a
        corrupted/non-JSON string (legacy seed data). Every form is coerced to
        a dict without raising, so reads of historical data never break.

        This is the single parse point for the column — all ``AuditLog``
        objects are constructed via ``from_dict`` (called only by ``query``),
        so every read path (governance API, exports, compliance reports)
        benefits. Do not add ``audit_logs`` queries that bypass ``query``.
        """
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                logger.warning("Unparseable audit log details, ignoring: %r", value)
                return {}
            if isinstance(parsed, dict):
                return parsed
            logger.warning("Audit log details JSON is not an object, ignoring: %r", value)
            return {}
        logger.warning("Unexpected audit log details type %s, ignoring", type(value).__name__)
        return {}


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
        details: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        resource_name: Optional[str] = None,
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
            resource_name: Human-readable name of the resource. Injected into
                details["resource_name"] only when the caller has not already
                set that key (caller-supplied details take precedence).

        Returns:
            bool: True if log was saved successfully.
        """
        try:
            # Inject resource_name without clobbering caller-supplied details:
            # shallow-merge with caller keys winning (setdefault).
            if resource_name:
                details = dict(details or {})
                details.setdefault("resource_name", resource_name)
            details_json = json.dumps(details) if details else None

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql(
                        """
                    INSERT INTO audit_logs
                    (timestamp, user_id, username, action, severity, resource_type,
                     resource_id, details, ip_address, user_agent, session_id,
                     success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                    ),
                    (
                        datetime.now(timezone.utc).replace(tzinfo=None),
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
                        adapt_boolean_value(success),
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
        resource_name: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """
        Log an audit action using the AuditAction enum.

        Args:
            action: AuditAction enum value.
            user_id: ID of the user.
            username: Username.
            **kwargs: Additional arguments passed to log().
            resource_name: Human-readable resource name, injected into details.

        Returns:
            bool: True if successful.
        """
        return self.log(
            action=action.value,
            user_id=user_id,
            username=username,
            resource_name=resource_name,
            **kwargs,
        )

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
    ) -> list[AuditLog]:
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
        params: list[Any] = []

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
            params.append(adapt_boolean_value(success))

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM audit_logs
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """

        try:
            rows = self.db.fetch_all(query, tuple(params + [limit, offset]))
        except Exception as e:
            logger.error(f"Failed to query audit logs: {e}")
            return []

        return [AuditLog.from_dict(row) for row in rows]

    def count(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        severity: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> int:
        """
        Count audit logs matching filters.

        Args:
            user_id: Filter by user ID.
            username: Filter by username.
            action: Filter by action type.
            resource_type: Filter by resource type.
            severity: Filter by severity.
            start_time: Filter logs after this time.
            end_time: Filter logs before this time.

        Returns:
            int: Count of matching logs.
        """
        conditions = []
        params: list[Any] = []

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

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"SELECT COUNT(*) as count FROM audit_logs WHERE {where_clause}"
        try:
            result = self.db.fetch_one(query, tuple(params))
            return int(result["count"]) if result else 0
        except Exception as e:
            logger.error(f"Failed to count audit logs: {e}")
            return 0

    def get_user_activity(self, user_id: int, days: int = 30) -> dict[str, Any]:
        """
        Get activity summary for a user.

        Args:
            user_id: User ID.
            days: Number of days to look back.

        Returns:
            Dict with activity summary.
        """
        start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        logs = self.query(user_id=user_id, start_time=start_time, limit=1000)

        # Group by action
        action_counts: dict[str, int] = {}
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
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM audit_logs WHERE timestamp < ?", (cutoff,))
                deleted = cursor.rowcount
                conn.commit()

            logger.info(f"Cleaned up {deleted} audit logs older than {days} days")
            return cast("int", deleted)

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
                    "resource_name",
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
                        (
                            log.details.get("resource_name", "")
                            if isinstance(log.details, dict)
                            else ""
                        ),
                    ]
                )

            return output.getvalue()
        else:
            raise ValueError(f"Unsupported format: {format}")


def get_ddl_statements() -> list[str]:
    """Return DDL statements for audit_logs table.

    This function is called by schema_init.ensure_all_tables() at app startup
    to ensure the audit_logs table exists, regardless of whether Alembic
    migrations have been run.
    """
    from app.repositories.database import is_postgresql

    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        f"""CREATE TABLE IF NOT EXISTS audit_logs (
            id {id_type},
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            resource_type TEXT,
            resource_id TEXT,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            session_id TEXT,
            success INTEGER DEFAULT 1,
            error_message TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs (timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs (action)",
        "CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs (resource_type, resource_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_logs (severity)",
    ]
