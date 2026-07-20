"""
Open ACE - Alert State Synchronizer
Synchronizes alert state changes between quota_alerts and alerts tables.
Ensures consistency when users acknowledge, delete, or clean up alerts.
"""

from __future__ import annotations



import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.repositories.database import Database, adapt_boolean_value, adapt_sql, is_postgresql

logger = logging.getLogger(__name__)


class AlertStateSynchronizer:
    """
    Synchronizes alert state between quota_alerts and alerts tables.

    Features:
    - Acknowledge/read state sync
    - Delete sync
    - Cleanup sync
    - Transaction guarantees
    """

    def __init__(self, db: Database | None = None):
        """Initialize the synchronizer."""
        self.db = db or Database()

    def sync_acknowledge(
        self,
        alert_id: str,
        user_id: int | None = None,
    ) -> bool:
        """
        Sync acknowledge/read state between tables.

        When a user acknowledges an alert in the alerts table,
        this updates the corresponding quota_alerts record.

        Args:
            alert_id: UUID of the alert in alerts table.
            user_id: Optional user ID for additional matching.

        Returns:
            True if sync successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # First, get the alert details from alerts table
                if is_postgresql():
                    cursor.execute(
                        """
                        SELECT user_id, metadata, created_at FROM alerts
                        WHERE alert_id = %s
                        """,
                        (alert_id,),
                    )
                else:
                    cursor.execute(
                        adapt_sql(
                            """
                            SELECT user_id, metadata, created_at FROM alerts
                            WHERE alert_id = ?
                        """
                        ),
                        (alert_id,),
                    )

                alert_row = cursor.fetchone()
                if not alert_row:
                    logger.warning(f"Alert {alert_id} not found for sync")
                    return False

                # Extract info for matching
                alert_user_id = alert_row[0]
                metadata_str = alert_row[1]
                created_at = alert_row[2]

                # Parse metadata to get quota_type
                quota_type = None
                if metadata_str:
                    try:
                        metadata = json.loads(metadata_str)
                        quota_type = metadata.get("quota_type")
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Mark alerts table as read
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if is_postgresql():
                    cursor.execute(
                        """
                        UPDATE alerts SET read = TRUE
                        WHERE alert_id = %s
                        """,
                        (alert_id,),
                    )
                else:
                    cursor.execute(
                        adapt_sql(
                            """
                            UPDATE alerts SET read = ?
                            WHERE alert_id = ?
                        """
                        ),
                        (adapt_boolean_value(True), alert_id),
                    )

                # Find and update corresponding quota_alerts record
                # Match by user_id, quota_type, and approximate created_at
                if quota_type and alert_user_id:
                    # Get date from created_at for matching
                    created_date = None
                    if created_at:
                        if isinstance(created_at, str):
                            created_date = created_at[:10]  # YYYY-MM-DD
                        elif isinstance(created_at, datetime):
                            created_date = created_at.strftime("%Y-%m-%d")

                    if is_postgresql():
                        cursor.execute(
                            """
                            UPDATE quota_alerts
                            SET acknowledged = TRUE,
                                acknowledged_at = %s
                            WHERE user_id = %s
                            AND quota_type = %s
                            AND acknowledged = FALSE
                            AND DATE(created_at) = %s
                            """,
                            (now, alert_user_id, quota_type, created_date),
                        )
                    else:
                        cursor.execute(
                            adapt_sql(
                                """
                                UPDATE quota_alerts
                                SET acknowledged = ?,
                                    acknowledged_at = ?
                                WHERE user_id = ?
                                AND quota_type = ?
                                AND acknowledged = ?
                                AND DATE(created_at) = ?
                            """
                            ),
                            (
                                adapt_boolean_value(True),
                                now,
                                alert_user_id,
                                quota_type,
                                adapt_boolean_value(False),
                                created_date,
                            ),
                        )

                conn.commit()
                logger.info(f"Synced acknowledge state for alert {alert_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to sync acknowledge state: {e}")
            return False

    def sync_delete(
        self,
        alert_id: str,
        user_id: int | None = None,
    ) -> bool:
        """
        Sync delete between tables.

        When a user deletes an alert from alerts table,
        this deletes the corresponding quota_alerts record.

        Args:
            alert_id: UUID of the alert in alerts table.
            user_id: Optional user ID for additional matching.

        Returns:
            True if sync successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Get the alert details before deletion
                if is_postgresql():
                    cursor.execute(
                        """
                        SELECT user_id, metadata, created_at FROM alerts
                        WHERE alert_id = %s
                        """,
                        (alert_id,),
                    )
                else:
                    cursor.execute(
                        adapt_sql(
                            """
                            SELECT user_id, metadata, created_at FROM alerts
                            WHERE alert_id = ?
                        """
                        ),
                        (alert_id,),
                    )

                alert_row = cursor.fetchone()
                if not alert_row:
                    logger.warning(f"Alert {alert_id} not found for delete sync")
                    return False

                alert_user_id = alert_row[0]
                metadata_str = alert_row[1]
                created_at = alert_row[2]

                # Parse metadata
                quota_type = None
                if metadata_str:
                    try:
                        metadata = json.loads(metadata_str)
                        quota_type = metadata.get("quota_type")
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Delete from alerts table
                if is_postgresql():
                    cursor.execute(
                        """
                        DELETE FROM alerts WHERE alert_id = %s
                        """,
                        (alert_id,),
                    )
                else:
                    cursor.execute(
                        adapt_sql(
                            """
                            DELETE FROM alerts WHERE alert_id = ?
                        """
                        ),
                        (alert_id,),
                    )

                # Delete corresponding quota_alerts record
                if quota_type and alert_user_id:
                    created_date = None
                    if created_at:
                        if isinstance(created_at, str):
                            created_date = created_at[:10]
                        elif isinstance(created_at, datetime):
                            created_date = created_at.strftime("%Y-%m-%d")

                    if is_postgresql():
                        cursor.execute(
                            """
                            DELETE FROM quota_alerts
                            WHERE user_id = %s
                            AND quota_type = %s
                            AND DATE(created_at) = %s
                            """,
                            (alert_user_id, quota_type, created_date),
                        )
                    else:
                        cursor.execute(
                            adapt_sql(
                                """
                                DELETE FROM quota_alerts
                                WHERE user_id = ?
                                AND quota_type = ?
                                AND DATE(created_at) = ?
                            """
                            ),
                            (alert_user_id, quota_type, created_date),
                        )

                conn.commit()
                logger.info(f"Synced delete for alert {alert_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to sync delete: {e}")
            return False

    def sync_cleanup(
        self,
        days: int = 30,
        user_id: int | None = None,
    ) -> dict:
        """
        Sync cleanup of old alerts between tables.

        Removes old acknowledged/read alerts from both tables
        in a single transaction.

        Args:
            days: Number of days to keep.
            user_id: Optional user ID to filter.

        Returns:
            Dict with cleanup statistics.
        """
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        cutoff_str = cutoff.isoformat()

        result = {
            "alerts_deleted": 0,
            "quota_alerts_deleted": 0,
            "success": False,
        }

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Delete from alerts table
                if user_id:
                    if is_postgresql():
                        cursor.execute(
                            """
                            DELETE FROM alerts
                            WHERE user_id = %s
                            AND read = TRUE
                            AND created_at < %s
                            """,
                            (user_id, cutoff_str),
                        )
                    else:
                        cursor.execute(
                            adapt_sql(
                                """
                                DELETE FROM alerts
                                WHERE user_id = ?
                                AND read = ?
                                AND created_at < ?
                            """
                            ),
                            (user_id, adapt_boolean_value(True), cutoff_str),
                        )
                else:
                    if is_postgresql():
                        cursor.execute(
                            """
                            DELETE FROM alerts
                            WHERE read = TRUE
                            AND created_at < %s
                            """,
                            (cutoff_str,),
                        )
                    else:
                        cursor.execute(
                            adapt_sql(
                                """
                                DELETE FROM alerts
                                WHERE read = ?
                                AND created_at < ?
                            """
                            ),
                            (adapt_boolean_value(True), cutoff_str),
                        )

                result["alerts_deleted"] = cursor.rowcount

                # Delete from quota_alerts table
                if user_id:
                    if is_postgresql():
                        cursor.execute(
                            """
                            DELETE FROM quota_alerts
                            WHERE user_id = %s
                            AND acknowledged = TRUE
                            AND created_at < %s
                            """,
                            (user_id, cutoff_str),
                        )
                    else:
                        cursor.execute(
                            adapt_sql(
                                """
                                DELETE FROM quota_alerts
                                WHERE user_id = ?
                                AND acknowledged = ?
                                AND created_at < ?
                            """
                            ),
                            (user_id, adapt_boolean_value(True), cutoff_str),
                        )
                else:
                    if is_postgresql():
                        cursor.execute(
                            """
                            DELETE FROM quota_alerts
                            WHERE acknowledged = TRUE
                            AND created_at < %s
                            """,
                            (cutoff_str,),
                        )
                    else:
                        cursor.execute(
                            adapt_sql(
                                """
                                DELETE FROM quota_alerts
                                WHERE acknowledged = ?
                                AND created_at < ?
                            """
                            ),
                            (adapt_boolean_value(True), cutoff_str),
                        )

                result["quota_alerts_deleted"] = cursor.rowcount
                conn.commit()

                result["success"] = True
                logger.info(
                    f"Synced cleanup: {result['alerts_deleted']} alerts, "
                    f"{result['quota_alerts_deleted']} quota_alerts deleted"
                )

        except Exception as e:
            logger.error(f"Failed to sync cleanup: {e}")

        return result

    def check_consistency(self) -> dict:
        """
        Check consistency between quota_alerts and alerts tables.

        Returns:
            Dict with consistency statistics and mismatch details.
        """
        result: dict[str, Any] = {
            "quota_alerts_count": 0,
            "alerts_quota_count": 0,
            "mismatches": [],
            "consistent": True,
        }

        try:
            # Count quota_alerts
            quota_count = self.db.fetch_one("SELECT COUNT(*) as count FROM quota_alerts")
            result["quota_alerts_count"] = quota_count.get("count", 0) if quota_count else 0

            # Count quota-type alerts
            alerts_count = self.db.fetch_one(
                adapt_sql(
                    """
                    SELECT COUNT(*) as count FROM alerts
                    WHERE alert_type = ?
                """
                ),
                ("quota",),
            )
            result["alerts_quota_count"] = alerts_count.get("count", 0) if alerts_count else 0

            # Check for mismatches - quota_alerts without corresponding alerts
            # This is a basic check; more detailed comparison would require more complex queries

            # Find quota_alerts that might not have corresponding alerts
            if is_postgresql():
                orphan_quota_alerts = self.db.fetch_all(
                    """
                    SELECT qa.id, qa.user_id, qa.quota_type, qa.created_at
                    FROM quota_alerts qa
                    WHERE NOT EXISTS (
                        SELECT 1 FROM alerts a
                        WHERE a.user_id = qa.user_id
                        AND a.alert_type = 'quota'
                        AND a.metadata->>'quota_type' = qa.quota_type
                        AND DATE(a.created_at) = DATE(qa.created_at)
                    )
                """
                )
            else:
                orphan_quota_alerts = self.db.fetch_all(
                    adapt_sql(
                        """
                    SELECT qa.id, qa.user_id, qa.quota_type, qa.created_at
                    FROM quota_alerts qa
                    WHERE NOT EXISTS (
                        SELECT 1 FROM alerts a
                        WHERE a.user_id = qa.user_id
                        AND a.alert_type = 'quota'
                        AND json_extract(a.metadata, '$.quota_type') = qa.quota_type
                        AND DATE(a.created_at) = DATE(qa.created_at)
                    )
                """
                    )
                )

            if orphan_quota_alerts:
                result["mismatches"].extend(
                    [
                        {
                            "type": "quota_alert_without_alert",
                            "id": row.get("id"),
                            "user_id": row.get("user_id"),
                            "quota_type": row.get("quota_type"),
                        }
                        for row in orphan_quota_alerts[:20]  # Limit to 20 for response size
                    ]
                )

            # Check for alerts without corresponding quota_alerts (less critical)
            if is_postgresql():
                orphan_alerts = self.db.fetch_all(
                    """
                    SELECT a.alert_id, a.user_id, a.metadata->>'quota_type' as quota_type, a.created_at
                    FROM alerts a
                    WHERE a.alert_type = 'quota'
                    AND NOT EXISTS (
                        SELECT 1 FROM quota_alerts qa
                        WHERE qa.user_id = a.user_id
                        AND qa.quota_type = a.metadata->>'quota_type'
                        AND DATE(qa.created_at) = DATE(a.created_at)
                    )
                """
                )
            else:
                orphan_alerts = self.db.fetch_all(
                    adapt_sql(
                        """
                    SELECT a.alert_id, a.user_id, json_extract(a.metadata, '$.quota_type') as quota_type, a.created_at
                    FROM alerts a
                    WHERE a.alert_type = 'quota'
                    AND NOT EXISTS (
                        SELECT 1 FROM quota_alerts qa
                        WHERE qa.user_id = a.user_id
                        AND qa.quota_type = json_extract(a.metadata, '$.quota_type')
                        AND DATE(qa.created_at) = DATE(a.created_at)
                    )
                """
                    )
                )

            if orphan_alerts:
                result["mismatches"].extend(
                    [
                        {
                            "type": "alert_without_quota_alert",
                            "alert_id": row.get("alert_id"),
                            "user_id": row.get("user_id"),
                            "quota_type": row.get("quota_type"),
                        }
                        for row in orphan_alerts[:20]
                    ]
                )

            result["consistent"] = len(result["mismatches"]) == 0

        except Exception as e:
            logger.error(f"Failed to check consistency: {e}")
            result["error"] = str(e)

        return result

    def repair_mismatches(self) -> dict:
        """
        Attempt to repair mismatches between tables.

        For quota_alerts without alerts, creates corresponding alerts.
        For alerts without quota_alerts, creates corresponding quota_alerts.

        Returns:
            Dict with repair statistics.
        """
        result: dict[str, Any] = {
            "alerts_created": 0,
            "quota_alerts_created": 0,
            "errors": [],
        }

        try:
            consistency = self.check_consistency()

            for mismatch in consistency.get("mismatches", []):
                if mismatch["type"] == "quota_alert_without_alert":
                    # Create corresponding alert
                    # This would need more detailed implementation
                    # For now, just log the issue
                    logger.warning(f"Found orphan quota_alert: {mismatch}")

                elif mismatch["type"] == "alert_without_quota_alert":
                    # Create corresponding quota_alert
                    # This would need more detailed implementation
                    logger.warning(f"Found orphan alert: {mismatch}")

        except Exception as e:
            result["errors"].append(str(e))
            logger.error(f"Failed to repair mismatches: {e}")

        return result


# Global instance
_synchronizer: AlertStateSynchronizer | None = None


def get_synchronizer(db: Database | None = None) -> AlertStateSynchronizer:
    """Get the global synchronizer instance."""
    global _synchronizer
    if _synchronizer is None:
        _synchronizer = AlertStateSynchronizer(db)
    return _synchronizer


def sync_acknowledge(alert_id: str, user_id: int | None = None) -> bool:
    """Convenience function to sync acknowledge state."""
    return get_synchronizer().sync_acknowledge(alert_id, user_id)


def sync_delete(alert_id: str, user_id: int | None = None) -> bool:
    """Convenience function to sync delete."""
    return get_synchronizer().sync_delete(alert_id, user_id)


def sync_cleanup(days: int = 30, user_id: int | None = None) -> dict:
    """Convenience function to sync cleanup."""
    return get_synchronizer().sync_cleanup(days, user_id)
