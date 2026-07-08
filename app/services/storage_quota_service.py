"""
Open ACE - Storage Quota Service

Service for managing user storage quotas for image uploads.
"""

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.repositories.database import Database, adapt_boolean_condition, adapt_sql, is_postgresql

logger = logging.getLogger(__name__)

# Default quota: 100MB
DEFAULT_STORAGE_QUOTA_BYTES = 100 * 1024 * 1024


class StorageQuotaService:
    """Service for storage quota management."""

    def __init__(self):
        self.db = Database()

    def get_user_quota(self, user_id: int) -> int:
        """
        Get user's storage quota in bytes.

        Args:
            user_id: User ID.

        Returns:
            int: Storage quota in bytes.
        """
        try:
            query = adapt_sql(
                """
                SELECT storage_quota_bytes FROM users
                WHERE id = ? AND {condition}
                """
            )
            condition = adapt_boolean_condition("deleted_at IS NULL", is_postgresql())
            row = self.db.fetch_one(query.format(condition=condition), (user_id,))
            if row and row.get("storage_quota_bytes"):
                return row["storage_quota_bytes"]
            return DEFAULT_STORAGE_QUOTA_BYTES
        except Exception as e:
            logger.error(f"Failed to get user quota: {e}")
            return DEFAULT_STORAGE_QUOTA_BYTES

    def get_user_storage_used(self, user_id: int) -> int:
        """
        Get user's current storage usage in bytes.

        Args:
            user_id: User ID.

        Returns:
            int: Storage used in bytes.
        """
        try:
            query = adapt_sql(
                """
                SELECT storage_used_bytes FROM users
                WHERE id = ? AND {condition}
                """
            )
            condition = adapt_boolean_condition("deleted_at IS NULL", is_postgresql())
            row = self.db.fetch_one(query.format(condition=condition), (user_id,))
            if row and row.get("storage_used_bytes"):
                return row["storage_used_bytes"]
            return 0
        except Exception as e:
            logger.error(f"Failed to get user storage used: {e}")
            return 0

    def calculate_user_storage_used(self, user_id: int) -> int:
        """
        Calculate user's actual storage usage from uploaded_images table.

        This is used to verify/sync the storage_used_bytes field.

        Args:
            user_id: User ID.

        Returns:
            int: Total storage used in bytes.
        """
        try:
            query = adapt_sql(
                """
                SELECT SUM(file_size) as total_size FROM uploaded_images
                WHERE user_id = ?
                """
            )
            row = self.db.fetch_one(query, (user_id,))
            if row and row.get("total_size"):
                return row["total_size"]
            return 0
        except Exception as e:
            logger.error(f"Failed to calculate user storage: {e}")
            return 0

    def check_quota_available(self, user_id: int, file_size: int) -> tuple[bool, Optional[str]]:
        """
        Check if user has enough quota for file upload.

        Args:
            user_id: User ID.
            file_size: Size of file to upload in bytes.

        Returns:
            tuple: (is_available, error_message)
        """
        quota = self.get_user_quota(user_id)
        used = self.get_user_storage_used(user_id)
        remaining = quota - used

        if file_size > remaining:
            quota_mb = quota / (1024 * 1024)
            used_mb = used / (1024 * 1024)
            remaining_mb = remaining / (1024 * 1024)
            file_mb = file_size / (1024 * 1024)
            return False, (
                f"Storage quota exceeded. "
                f"Quota: {quota_mb:.1f}MB, Used: {used_mb:.1f}MB, "
                f"Remaining: {remaining_mb:.1f}MB, File: {file_mb:.1f}MB"
            )

        return True, None

    def update_storage_used(self, user_id: int, delta_bytes: int) -> bool:
        """
        Update user's storage_used_bytes by delta.

        Args:
            user_id: User ID.
            delta_bytes: Bytes to add (positive) or remove (negative).

        Returns:
            bool: True if update successful.
        """
        try:
            # Get current value
            current = self.get_user_storage_used(user_id)
            new_value = max(0, current + delta_bytes)

            query = adapt_sql(
                """
                UPDATE users SET storage_used_bytes = ?, updated_at = ?
                WHERE id = ? AND {condition}
                """
            )
            condition = adapt_boolean_condition("deleted_at IS NULL", is_postgresql())
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            self.db.execute(
                query.format(condition=condition),
                (new_value, now, user_id)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update storage used: {e}")
            return False

    def sync_storage_used(self, user_id: int) -> bool:
        """
        Sync storage_used_bytes with actual uploaded_images sum.

        Args:
            user_id: User ID.

        Returns:
            bool: True if sync successful.
        """
        actual_used = self.calculate_user_storage_used(user_id)
        current_used = self.get_user_storage_used(user_id)

        if actual_used != current_used:
            delta = actual_used - current_used
            return self.update_storage_used(user_id, delta)

        return True

    def get_storage_status(self) -> dict:
        """
        Get overall storage status for admin monitoring.

        Returns:
            dict: Storage status with total, used, and threshold info.
        """
        try:
            # Get total storage used across all users
            query = adapt_sql(
                """
                SELECT SUM(file_size) as total_used, COUNT(*) as total_files
                FROM uploaded_images
                """
            )
            row = self.db.fetch_one(query)

            total_used = row.get("total_used", 0) if row else 0
            total_files = row.get("total_files", 0) if row else 0

            # Get per-user stats
            user_query = adapt_sql(
                """
                SELECT u.id, u.username, u.storage_used_bytes, u.storage_quota_bytes,
                       COUNT(ui.id) as file_count
                FROM users u
                LEFT JOIN uploaded_images ui ON u.id = ui.user_id
                WHERE {condition}
                GROUP BY u.id, u.username, u.storage_used_bytes, u.storage_quota_bytes
                ORDER BY u.storage_used_bytes DESC
                """
            )
            condition = adapt_boolean_condition("u.deleted_at IS NULL", is_postgresql())
            user_rows = self.db.fetch_all(user_query.format(condition=condition))

            user_stats = []
            for row in user_rows:
                used = row.get("storage_used_bytes", 0) or 0
                quota = row.get("storage_quota_bytes", DEFAULT_STORAGE_QUOTA_BYTES) or DEFAULT_STORAGE_QUOTA_BYTES
                quota_mb = quota / (1024 * 1024)
                used_mb = used / (1024 * 1024)
                percentage = (used / quota * 100) if quota > 0 else 0
                user_stats.append({
                    "user_id": row["id"],
                    "username": row["username"],
                    "storage_used_bytes": used,
                    "storage_quota_bytes": quota,
                    "storage_used_mb": round(used_mb, 2),
                    "storage_quota_mb": round(quota_mb, 2),
                    "usage_percentage": round(percentage, 1),
                    "file_count": row.get("file_count", 0) or 0,
                })

            return {
                "total_used_bytes": total_used,
                "total_used_mb": round(total_used / (1024 * 1024), 2),
                "total_files": total_files,
                "user_stats": user_stats[:50],  # Top 50 users
            }
        except Exception as e:
            logger.error(f"Failed to get storage status: {e}")
            return {
                "total_used_bytes": 0,
                "total_used_mb": 0,
                "total_files": 0,
                "user_stats": [],
            }

    def check_disk_space(self, storage_path: str, threshold_pct: int = 80) -> tuple[bool, Optional[str]]:
        """
        Check disk space at storage path against threshold.

        Args:
            storage_path: Path to check.
            threshold_pct: Threshold percentage (default 80%).

        Returns:
            tuple: (is_ok, warning_message)
        """
        try:
            # Ensure path exists
            if not os.path.exists(storage_path):
                os.makedirs(storage_path, exist_ok=True)

            # Get disk usage
            total, used, free = shutil.disk_usage(storage_path)
            usage_pct = (used / total) * 100

            if usage_pct >= threshold_pct:
                total_gb = total / (1024 * 1024 * 1024)
                used_gb = used / (1024 * 1024 * 1024)
                free_gb = free / (1024 * 1024 * 1024)
                return False, (
                    f"Disk space warning: {usage_pct:.1f}% used "
                    f"(Total: {total_gb:.1f}GB, Used: {used_gb:.1f}GB, Free: {free_gb:.1f}GB)"
                )

            return True, None
        except Exception as e:
            logger.error(f"Failed to check disk space: {e}")
            return True, None  # Allow upload if check fails


# Singleton instance
_storage_quota_service: Optional[StorageQuotaService] = None


def get_storage_quota_service() -> StorageQuotaService:
    """Get singleton StorageQuotaService instance."""
    global _storage_quota_service
    if _storage_quota_service is None:
        _storage_quota_service = StorageQuotaService()
    return _storage_quota_service