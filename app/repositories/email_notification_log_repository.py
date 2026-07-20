"""
Open ACE - Email Notification Log Repository

Provides database access for email notification audit logs.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from app.repositories.database import adapt_sql, get_database_url, is_postgresql

logger = logging.getLogger(__name__)


class EmailNotificationLogRepository:
    """Repository for email notification logs."""

    def _get_connection(self) -> Any | Any:
        """Get database connection."""
        if is_postgresql():
            url = get_database_url()
            conn = psycopg2.connect(url)
            conn.cursor_factory = RealDictCursor
            return conn
        else:
            import sqlite3

            conn = sqlite3.connect("app.db")
            conn.row_factory = sqlite3.Row
            return conn

    def create_log(
        self,
        user_id: int,
        recipient_email: str,
        subject: str,
        alert_id: str | None = None,
        email_body: str | None = None,
        status: str = "pending",
        error_message: str | None = None,
    ) -> int:
        """
        Create an email notification log entry.

        Args:
            user_id: Recipient user ID.
            recipient_email: Recipient email address.
            subject: Email subject.
            alert_id: Related alert ID.
            email_body: Email body content.
            status: Initial status (pending, sent, failed).
            error_message: Error message if failed.

        Returns:
            Log entry ID.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if is_postgresql():
            cursor.execute(
                """
                INSERT INTO email_notification_logs
                (user_id, alert_id, recipient_email, subject, email_body,
                 sent_at, status, error_message, retry_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id,
                    alert_id,
                    recipient_email,
                    subject,
                    email_body,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    status,
                    error_message,
                    0,
                ),
            )
            log_id = cursor.fetchone()["id"]
        else:
            cursor.execute(
                """
                INSERT INTO email_notification_logs
                (user_id, alert_id, recipient_email, subject, email_body,
                 sent_at, status, error_message, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    alert_id,
                    recipient_email,
                    subject,
                    email_body,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    status,
                    error_message,
                    0,
                ),
            )
            log_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return int(log_id or 0)

    def update_status(
        self,
        log_id: int,
        status: str,
        error_message: str | None = None,
        increment_retry: bool = False,
        next_retry_at: datetime | None = None,
    ) -> bool:
        """
        Update email log status.

        Args:
            log_id: Log entry ID.
            status: New status (sent, failed, retrying).
            error_message: Error message if failed.
            increment_retry: Whether to increment retry count.
            next_retry_at: Next retry time if retrying.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        retry_count_update = ""
        if increment_retry:
            retry_count_update = ", retry_count = retry_count + 1"

        next_retry_update = ""
        if next_retry_at:
            next_retry_update = f", next_retry_at = {adapt_sql('?')}"

        cursor.execute(
            adapt_sql(
                f"""
                UPDATE email_notification_logs
                SET status = ?, error_message = ? {retry_count_update} {next_retry_update}
                WHERE id = ?
            """
            ),
            [status, error_message] + ([next_retry_at] if next_retry_at else []) + [log_id],
        )

        success = bool(cursor.rowcount > 0)
        conn.commit()
        conn.close()

        return success

    def get_pending_retries(self, max_retry_count: int = 3) -> list[dict[str, Any]]:
        """
        Get logs pending retry (failed and retry_count < max).

        Args:
            max_retry_count: Maximum retry count.

        Returns:
            List of log entries pending retry.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
                SELECT * FROM email_notification_logs
                WHERE status = 'failed'
                  AND retry_count < ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY sent_at ASC
                LIMIT 50
            """
            ),
            (max_retry_count, datetime.now(timezone.utc).replace(tzinfo=None)),
        )

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_user_logs(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get email logs for a user.

        Args:
            user_id: User ID.
            limit: Maximum results.
            offset: Offset for pagination.

        Returns:
            List of log entries.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
                SELECT * FROM email_notification_logs
                WHERE user_id = ?
                ORDER BY sent_at DESC
                LIMIT ? OFFSET ?
            """
            ),
            (user_id, limit, offset),
        )

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_statistics(
        self,
        days: int = 7,
    ) -> dict[str, Any]:
        """
        Get email sending statistics.

        Args:
            days: Number of days to analyze.

        Returns:
            Statistics dict.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        # Total counts by status
        cursor.execute(
            adapt_sql(
                """
                SELECT status, COUNT(*) as count
                FROM email_notification_logs
                WHERE sent_at >= ?
                GROUP BY status
            """
            ),
            (cutoff,),
        )

        status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

        # Success rate
        total = sum(status_counts.values())
        sent = status_counts.get("sent", 0)
        success_rate = (sent / total * 100) if total > 0 else 0

        # Average retry count for failed emails
        cursor.execute(
            adapt_sql(
                """
                SELECT AVG(retry_count) as avg_retry
                FROM email_notification_logs
                WHERE status = 'failed' AND sent_at >= ?
            """
            ),
            (cutoff,),
        )

        avg_retry_row = cursor.fetchone()
        avg_retry = (
            avg_retry_row["avg_retry"] if avg_retry_row and avg_retry_row["avg_retry"] else 0
        )

        conn.close()

        return {
            "total_sent": total,
            "successful": sent,
            "failed": status_counts.get("failed", 0),
            "pending": status_counts.get("pending", 0),
            "success_rate": round(success_rate, 2),
            "average_retries": round(avg_retry, 2),
            "period_days": days,
        }

    def cleanup_old_logs(self, days: int = 90) -> int:
        """
        Delete logs older than specified days.

        Args:
            days: Number of days to keep.

        Returns:
            Number of deleted logs.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        cursor.execute(
            adapt_sql("DELETE FROM email_notification_logs WHERE sent_at < ?"),
            (cutoff,),
        )

        count = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"Cleaned up {count} old email notification logs")
        return int(count or 0)


# Global repository instance
_email_log_repo: EmailNotificationLogRepository | None = None


def get_email_log_repository() -> EmailNotificationLogRepository:
    """Get the global email log repository instance."""
    global _email_log_repo
    if _email_log_repo is None:
        _email_log_repo = EmailNotificationLogRepository()
    return _email_log_repo
