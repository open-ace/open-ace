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
from __future__ import annotations


import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, cast

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
    USER_PASSWORD_CHANGE_FAILED = "user_password_change_failed"
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

    id: int | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    user_id: int | None = None
    username: str | None = None
    action: str = ""
    severity: str = "info"
    resource_type: str = ""
    resource_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None
    session_id: str | None = None
    success: bool = True
    error_message: str | None = None
    tenant_id: int | None = None

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
            "tenant_id": self.tenant_id,
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
            tenant_id=data.get("tenant_id"),
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

    def __init__(self, db: Database | None = None):
        """
        Initialize audit logger.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()
        # Table structure managed by Alembic migrations

    @staticmethod
    def _normalize_tenant_id(value: Any) -> int | None:
        """Normalize a tenant identifier to a positive integer."""
        if value in (None, "", 0, "0"):
            return None
        try:
            tenant_id = int(value)
        except (TypeError, ValueError):
            return None
        return tenant_id if tenant_id > 0 else None

    def _resolve_tenant_id(
        self, tenant_id: int | None = None, user_id: int | None = None
    ) -> int | None:
        """Resolve tenant scope from an explicit tenant_id or a user record."""
        normalized = self._normalize_tenant_id(tenant_id)
        if normalized is not None or not user_id:
            return normalized

        try:
            row = self.db.fetch_one("SELECT tenant_id FROM users WHERE id = ?", (user_id,))
        except Exception:
            return None

        if not row:
            return None
        return self._normalize_tenant_id(row.get("tenant_id"))

    def log(
        self,
        action: str,
        user_id: int | None = None,
        username: str | None = None,
        severity: str = "info",
        resource_type: str = "",
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        session_id: str | None = None,
        success: bool = True,
        error_message: str | None = None,
        resource_name: str | None = None,
        tenant_id: int | None = None,
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
            effective_tenant_id = self._resolve_tenant_id(tenant_id=tenant_id, user_id=user_id)

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql(
                        """
                    INSERT INTO audit_logs
                    (timestamp, user_id, username, action, severity, resource_type,
                     resource_id, details, ip_address, user_agent, session_id,
                     success, error_message, tenant_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        effective_tenant_id,
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
        user_id: int | None = None,
        username: str | None = None,
        resource_name: str | None = None,
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
        user_id: int | None = None,
        username: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        severity: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        success: bool | None = None,
        tenant_id: int | None = None,
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

        normalized_tenant_id = self._normalize_tenant_id(tenant_id)
        if normalized_tenant_id is not None:
            conditions.append(
                "(tenant_id = ? OR (tenant_id IS NULL AND user_id IN "
                "(SELECT id FROM users WHERE tenant_id = ?)))"
            )
            params.extend([normalized_tenant_id, normalized_tenant_id])

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
        user_id: int | None = None,
        username: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        severity: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: int | None = None,
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

        normalized_tenant_id = self._normalize_tenant_id(tenant_id)
        if normalized_tenant_id is not None:
            conditions.append(
                "(tenant_id = ? OR (tenant_id IS NULL AND user_id IN "
                "(SELECT id FROM users WHERE tenant_id = ?)))"
            )
            params.extend([normalized_tenant_id, normalized_tenant_id])

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"SELECT COUNT(*) as count FROM audit_logs WHERE {where_clause}"
        try:
            result = self.db.fetch_one(query, tuple(params))
            return int(result["count"]) if result else 0
        except Exception as e:
            logger.error(f"Failed to count audit logs: {e}")
            return 0

    def get_user_activity(
        self, user_id: int, days: int = 30, tenant_id: int | None = None
    ) -> dict[str, Any]:
        """
        Get activity summary for a user.

        Args:
            user_id: User ID.
            days: Number of days to look back.

        Returns:
            Dict with activity summary.
        """
        start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        logs = self.query(
            user_id=user_id,
            start_time=start_time,
            tenant_id=tenant_id,
            limit=1000,
        )

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
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        format: str = "json",
        tenant_id: int | None = None,
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
        logs = self.query(
            start_time=start_time,
            end_time=end_time,
            tenant_id=tenant_id,
            limit=10000,
        )

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


def get_action_categories() -> dict[str, dict[str, Any]]:
    """Return audit action types organized by category.

    This function provides a structured mapping of all AuditAction enum values
    organized by category, including i18n keys for internationalization.

    Returns:
        Dict with category keys, each containing:
        - label: English label for the category
        - i18n_key: Translation key for the category
        - actions: List of action dicts with value, label, i18n_key
    """
    return {
        "auth": {
            "label": "Authentication",
            "i18n_key": "categoryAuth",
            "resource_types": ["session"],
            "actions": [
                {
                    "value": "login",
                    "label": "Login",
                    "i18n_key": "actionLogin",
                },
                {
                    "value": "logout",
                    "label": "Logout",
                    "i18n_key": "actionLogout",
                },
                {
                    "value": "login_failed",
                    "label": "Login Failed",
                    "i18n_key": "actionLoginFailed",
                },
                {
                    "value": "session_expired",
                    "label": "Session Expired",
                    "i18n_key": "actionSessionExpired",
                },
            ],
        },
        "user_management": {
            "label": "User Management",
            "i18n_key": "categoryUserManagement",
            "resource_types": ["user"],
            "actions": [
                {
                    "value": "user_create",
                    "label": "User Create",
                    "i18n_key": "actionUserCreate",
                },
                {
                    "value": "user_update",
                    "label": "User Update",
                    "i18n_key": "actionUserUpdate",
                },
                {
                    "value": "user_delete",
                    "label": "User Delete",
                    "i18n_key": "actionUserDelete",
                },
                {
                    "value": "user_password_change",
                    "label": "Password Change",
                    "i18n_key": "actionUserPasswordChange",
                },
                {
                    "value": "user_password_change_failed",
                    "label": "Password Change Failed",
                    "i18n_key": "actionUserPasswordChangeFailed",
                },
                {
                    "value": "user_role_change",
                    "label": "Role Change",
                    "i18n_key": "actionUserRoleChange",
                },
                {
                    "value": "user_status_change",
                    "label": "Status Change",
                    "i18n_key": "actionUserStatusChange",
                },
            ],
        },
        "permission": {
            "label": "Permission",
            "i18n_key": "categoryPermission",
            "resource_types": ["user"],
            "actions": [
                {
                    "value": "permission_grant",
                    "label": "Permission Grant",
                    "i18n_key": "actionPermissionGrant",
                },
                {
                    "value": "permission_revoke",
                    "label": "Permission Revoke",
                    "i18n_key": "actionPermissionRevoke",
                },
            ],
        },
        "quota": {
            "label": "Quota",
            "i18n_key": "categoryQuota",
            "resource_types": ["quota_alert"],
            "actions": [
                {
                    "value": "quota_update",
                    "label": "Quota Update",
                    "i18n_key": "actionQuotaUpdate",
                },
                {
                    "value": "quota_alert",
                    "label": "Quota Alert",
                    "i18n_key": "actionQuotaAlert",
                },
                {
                    "value": "quota_exceeded",
                    "label": "Quota Exceeded",
                    "i18n_key": "actionQuotaExceeded",
                },
            ],
        },
        "data": {
            "label": "Data",
            "i18n_key": "categoryData",
            "resource_types": ["analytics_report", "analytics", "data"],
            "actions": [
                {
                    "value": "data_view",
                    "label": "Data View",
                    "i18n_key": "actionDataView",
                },
                {
                    "value": "data_export",
                    "label": "Data Export",
                    "i18n_key": "actionDataExport",
                },
                {
                    "value": "data_import",
                    "label": "Data Import",
                    "i18n_key": "actionDataImport",
                },
                {
                    "value": "data_delete",
                    "label": "Data Delete",
                    "i18n_key": "actionDataDelete",
                },
            ],
        },
        "system": {
            "label": "System",
            "i18n_key": "categorySystem",
            "resource_types": [
                "content_filter",
                "filter_rule",
                "security_settings",
                "ai_agent_settings",
            ],
            "actions": [
                {
                    "value": "system_config_change",
                    "label": "Config Change",
                    "i18n_key": "actionSystemConfigChange",
                },
                {
                    "value": "system_start",
                    "label": "System Start",
                    "i18n_key": "actionSystemStart",
                },
                {
                    "value": "system_stop",
                    "label": "System Stop",
                    "i18n_key": "actionSystemStop",
                },
            ],
        },
        "content": {
            "label": "Content",
            "i18n_key": "categoryContent",
            "resource_types": ["content"],
            "actions": [
                {
                    "value": "content_blocked",
                    "label": "Content Blocked",
                    "i18n_key": "actionContentBlocked",
                },
                {
                    "value": "content_flagged",
                    "label": "Content Flagged",
                    "i18n_key": "actionContentFlagged",
                },
                {
                    "value": "content_warned",
                    "label": "Content Warned",
                    "i18n_key": "actionContentWarned",
                },
                {
                    "value": "content_redacted",
                    "label": "Content Redacted",
                    "i18n_key": "actionContentRedacted",
                },
            ],
        },
        "agent": {
            "label": "Agent",
            "i18n_key": "categoryAgent",
            "resource_types": ["remote_machine", "agent_token"],
            "actions": [
                {
                    "value": "agent_register",
                    "label": "Agent Register",
                    "i18n_key": "actionAgentRegister",
                },
                {
                    "value": "agent_token_rotate",
                    "label": "Token Rotate",
                    "i18n_key": "actionAgentTokenRotate",
                },
                {
                    "value": "agent_token_revoke",
                    "label": "Token Revoke",
                    "i18n_key": "actionAgentTokenRevoke",
                },
                {
                    "value": "agent_auth_failure",
                    "label": "Auth Failure",
                    "i18n_key": "actionAgentAuthFailure",
                },
                {
                    "value": "agent_reconnect",
                    "label": "Agent Reconnect",
                    "i18n_key": "actionAgentReconnect",
                },
            ],
        },
    }
