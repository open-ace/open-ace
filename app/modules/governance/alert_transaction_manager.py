"""
Open ACE - Alert Transaction Manager

Provides transactional alert creation for quota alerts with:
- Dual-write to both quota_alerts and alerts tables
- Retry mechanism (up to 3 times)
- Failure compensation queue
- Timeout control

Ensures data consistency across both tables.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.repositories.database import Database, adapt_sql, is_postgresql

logger = logging.getLogger(__name__)

# Configuration
ALERT_TRANSACTION_TIMEOUT_SEC = 30
ALERT_TRANSACTION_MAX_RETRIES = 3
ALERT_COMPENSATION_MAX_RETRIES = 10


@dataclass
class QuotaAlertData:
    """Data for creating a quota alert."""

    user_id: int
    username: str
    quota_type: str  # tokens, requests, daily_tokens, daily_requests, etc.
    usage_percent: float
    current_usage: int = 0
    quota_limit: int = 0
    threshold: float = 0.0
    original_alert_type: str = "warning"  # warning, critical, exceeded
    language: str = "en"

    def to_quota_alerts_dict(self) -> dict:
        """Convert to quota_alerts table format."""
        return {
            "user_id": self.user_id,
            "alert_type": self.original_alert_type,
            "quota_type": self.quota_type,
            "period": "daily",
            "threshold": self.threshold,
            "current_usage": self.current_usage,
            "quota_limit": self.quota_limit,
            "percentage": self.usage_percent / 100,
            "message": self._generate_message(),
        }

    def to_alerts_dict(self) -> dict:
        """Convert to alerts table format."""
        # Map alert_type to severity
        if self.original_alert_type == "exceeded":
            severity = "critical"
        else:
            severity = self.original_alert_type

        return {
            "alert_id": str(uuid.uuid4()),
            "alert_type": "quota",
            "severity": severity,
            "title": self._generate_title(),
            "message": self._generate_message(),
            "user_id": self.user_id,
            "username": self.username,
            "metadata": json.dumps({
                "quota_type": self.quota_type,
                "usage_percent": self.usage_percent,
                "current_usage": self.current_usage,
                "quota_limit": self.quota_limit,
                "threshold": self.threshold,
                "original_alert_type": self.original_alert_type,
            }),
            "action_url": "/report",
            "action_text": "View Usage",
        }

    def _generate_title(self) -> str:
        """Generate alert title."""
        if self.original_alert_type == "exceeded":
            return f"Quota Exceeded: {self.quota_type.title()}"
        elif self.original_alert_type == "critical":
            return f"Quota Critical: {self.quota_type.title()}"
        else:
            return f"Quota Warning: {self.quota_type.title()}"

    def _generate_message(self) -> str:
        """Generate alert message."""
        if self.original_alert_type == "exceeded":
            return f"Your {self.quota_type} quota has been fully used. Please contact administrator."
        elif self.original_alert_type == "critical":
            return f"You have used {self.usage_percent:.1f}% of your {self.quota_type} quota."
        else:
            return f"You have used {self.usage_percent:.1f}% of your {self.quota_type} quota."


@dataclass
class AlertCreationFailure:
    """Record of a failed alert creation."""

    id: Optional[int] = None
    alert_data: str = ""  # JSON string of QuotaAlertData
    retry_count: int = 0
    last_retry_at: Optional[datetime] = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    status: str = "pending"  # pending, retrying, failed, success

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "alert_data": self.alert_data,
            "retry_count": self.retry_count,
            "last_retry_at": self.last_retry_at.isoformat() if self.last_retry_at else None,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
        }


class AlertTransactionManager:
    """
    Manages transactional alert creation for quota alerts.

    Features:
    - Dual-write to quota_alerts and alerts tables
    - Retry mechanism with exponential backoff
    - Failure compensation queue
    - Deduplication across both tables
    """

    def __init__(self, db: Optional[Database] = None):
        """Initialize the transaction manager."""
        self.db = db or Database()
        self._ensure_failure_table()

    def _ensure_failure_table(self) -> None:
        """Ensure the alert_creation_failures table exists."""
        if is_postgresql():
            create_sql = """
                CREATE TABLE IF NOT EXISTS alert_creation_failures (
                    id SERIAL PRIMARY KEY,
                    alert_data TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    last_retry_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """
        else:
            create_sql = """
                CREATE TABLE IF NOT EXISTS alert_creation_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_data TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    last_retry_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            """

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(create_sql)
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not create alert_creation_failures table: {e}")

    def create_quota_alert_transactional(
        self,
        alert_data: QuotaAlertData,
    ) -> tuple[bool, Optional[str]]:
        """
        Create a quota alert in both tables atomically.

        Args:
            alert_data: Data for the alert.

        Returns:
            Tuple of (success, alert_id). alert_id is None on failure.
        """
        # Check for recent alerts (deduplication)
        if self._has_recent_alert(alert_data.user_id, alert_data.quota_type):
            logger.debug(
                f"Skipping duplicate alert for user {alert_data.user_id}, "
                f"quota_type {alert_data.quota_type}"
            )
            return True, None

        alert_id = str(uuid.uuid4())
        retry_intervals = [1, 2, 4]  # Exponential backoff

        for attempt in range(ALERT_TRANSACTION_MAX_RETRIES):
            try:
                success = self._execute_transaction(alert_data, alert_id)
                if success:
                    return True, alert_id
            except Exception as e:
                logger.warning(
                    f"Alert creation attempt {attempt + 1} failed: {e}"
                )
                if attempt < len(retry_intervals):
                    time.sleep(retry_intervals[attempt])

        # All retries failed, add to compensation queue
        self._add_to_failure_queue(alert_data)
        logger.error(
            f"Failed to create alert after {ALERT_TRANSACTION_MAX_RETRIES} attempts, "
            f"added to compensation queue"
        )
        return False, None

    def _execute_transaction(
        self,
        alert_data: QuotaAlertData,
        alert_id: str,
    ) -> bool:
        """Execute the dual-write transaction."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            try:
                # Write to quota_alerts table
                quota_data = alert_data.to_quota_alerts_dict()
                cursor.execute(
                    adapt_sql("""
                        INSERT INTO quota_alerts
                        (user_id, alert_type, quota_type, period, threshold,
                         current_usage, quota_limit, percentage, message)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """),
                    (
                        quota_data["user_id"],
                        quota_data["alert_type"],
                        quota_data["quota_type"],
                        quota_data["period"],
                        quota_data["threshold"],
                        quota_data["current_usage"],
                        quota_data["quota_limit"],
                        quota_data["percentage"],
                        quota_data["message"],
                    ),
                )

                # Write to alerts table
                alerts_data = alert_data.to_alerts_dict()
                cursor.execute(
                    adapt_sql("""
                        INSERT INTO alerts
                        (alert_id, alert_type, severity, title, message, user_id,
                         username, metadata, created_at, read, action_url, action_text)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """),
                    (
                        alert_id,
                        alerts_data["alert_type"],
                        alerts_data["severity"],
                        alerts_data["title"],
                        alerts_data["message"],
                        alerts_data["user_id"],
                        alerts_data["username"],
                        alerts_data["metadata"],
                        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        0,
                        alerts_data.get("action_url"),
                        alerts_data.get("action_text"),
                    ),
                )

                conn.commit()
                logger.info(
                    f"Created quota alert: user={alert_data.user_id}, "
                    f"type={alert_data.original_alert_type}, "
                    f"quota_type={alert_data.quota_type}, "
                    f"alert_id={alert_id}"
                )
                return True

            except Exception as e:
                conn.rollback()
                raise e

    def _has_recent_alert(self, user_id: int, quota_type: str, hours: int = 1) -> bool:
        """Check if a recent alert exists in either table."""
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        threshold_str = threshold.isoformat()

        # Check quota_alerts
        quota_result = self.db.fetch_one(
            adapt_sql("""
                SELECT COUNT(*) as count FROM quota_alerts
                WHERE user_id = ? AND quota_type = ? AND created_at >= ?
            """),
            (user_id, quota_type, threshold_str),
        )

        if quota_result and quota_result.get("count", 0) > 0:
            return True

        # Check alerts (via metadata)
        if is_postgresql():
            alerts_result = self.db.fetch_one(
                """
                    SELECT COUNT(*) as count FROM alerts
                    WHERE user_id = %s AND alert_type = %s AND created_at >= %s
                    AND metadata->>'quota_type' = %s
                """,
                (user_id, "quota", threshold_str, quota_type),
            )
        else:
            alerts_result = self.db.fetch_one(
                """
                    SELECT COUNT(*) as count FROM alerts
                    WHERE user_id = ? AND alert_type = ? AND created_at >= ?
                    AND json_extract(metadata, '$.quota_type') = ?
                """,
                (user_id, "quota", threshold_str, quota_type),
            )

        return alerts_result is not None and alerts_result.get("count", 0) > 0

    def _add_to_failure_queue(self, alert_data: QuotaAlertData) -> None:
        """Add failed alert to compensation queue."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql("""
                        INSERT INTO alert_creation_failures (alert_data, retry_count, status)
                        VALUES (?, 0, 'pending')
                    """),
                    (json.dumps(alert_data.__dict__),),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to add to failure queue: {e}")

    def get_pending_failures(self, limit: int = 100) -> list[AlertCreationFailure]:
        """Get pending failures from the queue."""
        rows = self.db.fetch_all(
            adapt_sql("""
                SELECT * FROM alert_creation_failures
                WHERE status IN ('pending', 'retrying')
                AND retry_count < ?
                ORDER BY created_at ASC
                LIMIT ?
            """),
            (ALERT_COMPENSATION_MAX_RETRIES, limit),
        )

        failures = []
        for row in rows:
            created_at_val = row.get("created_at")
            if created_at_val is None:
                created_at_val = datetime.now(timezone.utc).replace(tzinfo=None)

            failures.append(AlertCreationFailure(
                id=row.get("id"),
                alert_data=row.get("alert_data", ""),
                retry_count=row.get("retry_count", 0),
                last_retry_at=row.get("last_retry_at"),
                created_at=created_at_val,
                status=row.get("status", "pending"),
            ))

        return failures

    def retry_failure(self, failure: AlertCreationFailure) -> bool:
        """Retry a failed alert creation."""
        try:
            data = json.loads(failure.alert_data)
            alert_data = QuotaAlertData(**data)

            success, _ = self.create_quota_alert_transactional(alert_data)

            # Update failure record
            new_status = "success" if success else "retrying"
            new_retry_count = failure.retry_count + 1 if not success else failure.retry_count

            if not success and new_retry_count >= ALERT_COMPENSATION_MAX_RETRIES:
                new_status = "failed"

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql("""
                        UPDATE alert_creation_failures
                        SET retry_count = ?, last_retry_at = ?, status = ?
                        WHERE id = ?
                    """),
                    (
                        new_retry_count,
                        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        new_status,
                        failure.id,
                    ),
                )
                conn.commit()

            return success

        except Exception as e:
            logger.error(f"Failed to retry alert creation: {e}")
            return False

    def get_failure_stats(self) -> dict:
        """Get statistics about the failure queue."""
        result = self.db.fetch_one(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'retrying' THEN 1 ELSE 0 END) as retrying,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success
            FROM alert_creation_failures
            """
        )

        return dict(result) if result else {
            "total": 0,
            "pending": 0,
            "retrying": 0,
            "failed": 0,
            "success": 0,
        }


# Global instance
_transaction_manager: Optional[AlertTransactionManager] = None


def get_transaction_manager(db: Optional[Database] = None) -> AlertTransactionManager:
    """Get the global transaction manager instance."""
    global _transaction_manager
    if _transaction_manager is None:
        _transaction_manager = AlertTransactionManager(db)
    return _transaction_manager


def create_quota_alert_transactional(
    user_id: int,
    username: str,
    usage_percent: float,
    quota_type: str = "tokens",
    current_usage: int = 0,
    quota_limit: int = 0,
    threshold: float = 0.0,
    language: str = "en",
) -> tuple[bool, Optional[str]]:
    """
    Create a quota alert in both tables atomically.

    Convenience function that wraps AlertTransactionManager.

    Args:
        user_id: User ID.
        username: Username.
        usage_percent: Usage percentage.
        quota_type: Type of quota (tokens, requests, etc.).
        current_usage: Current usage value.
        quota_limit: Quota limit.
        threshold: Threshold that was crossed.
        language: Language for notifications.

    Returns:
        Tuple of (success, alert_id).
    """
    # Determine alert type based on usage percent
    if usage_percent >= 100:
        original_alert_type = "exceeded"
    elif usage_percent >= 95:
        original_alert_type = "critical"
    elif usage_percent >= 80:
        original_alert_type = "warning"
    else:
        original_alert_type = "warning"

    alert_data = QuotaAlertData(
        user_id=user_id,
        username=username,
        quota_type=quota_type,
        usage_percent=usage_percent,
        current_usage=current_usage,
        quota_limit=quota_limit,
        threshold=threshold,
        original_alert_type=original_alert_type,
        language=language,
    )

    manager = get_transaction_manager()
    return manager.create_quota_alert_transactional(alert_data)