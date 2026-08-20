"""
Open ACE - Permission Task Service

Background task service for managing asynchronous permission setup
for large shared projects.

Issue: #2746
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import g

from app.models.project import Project
from app.utils.datetime_utils import ensure_utc_suffix
from app.utils.workspace import (
    SHARED_GROUP_NAME,
    _is_docker_multi_user_mode,
    ensure_shared_group,
    estimate_file_count_fast,
    setup_permissions_with_depth_limit,
    verify_setgid_support,
)

logger = logging.getLogger(__name__)

# Configuration constants (can be overridden via environment variables)
PERMISSION_SYNC_THRESHOLD = int(os.environ.get("PERMISSION_SYNC_THRESHOLD", "1000"))
PERMISSION_MAX_CONCURRENT_TASKS = int(os.environ.get("PERMISSION_MAX_CONCURRENT_TASKS", "5"))
PERMISSION_MAX_QUEUE_SIZE = int(os.environ.get("PERMISSION_MAX_QUEUE_SIZE", "20"))
PERMISSION_TASK_CLEANUP_DAYS = int(os.environ.get("PERMISSION_TASK_CLEANUP_DAYS", "7"))
PERMISSION_PRIORITY_MANUAL_FIX = int(os.environ.get("PERMISSION_PRIORITY_MANUAL_FIX", "5"))
PERMISSION_PRIORITY_AUTO_CREATE = int(os.environ.get("PERMISSION_PRIORITY_AUTO_CREATE", "10"))
PERMISSION_MAX_DEPTH = int(os.environ.get("PERMISSION_MAX_DEPTH", "10"))


class PermissionTaskService:
    """Service for managing permission setup tasks.

    Features:
    - Task submission and queue management
    - Queue saturation protection
    - Task deduplication
    - Priority scheduling
    - Checkpoint recovery
    - Progress tracking
    """

    _instance: PermissionTaskService | None = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._executor = ThreadPoolExecutor(max_workers=PERMISSION_MAX_CONCURRENT_TASKS)
        self._running_tasks: set[str] = set()
        self._stop_event = threading.Event()
        self._initialized = True

        logger.info("PermissionTaskService initialized")

    def check_queue_saturation(self, db) -> tuple[bool, int]:
        """Check if the task queue is saturated.

        Returns:
            Tuple of (is_saturated, current_queue_length).
        """
        try:
            # Count pending and running tasks
            count_result = db.execute(sa.text("""
                    SELECT COUNT(*) FROM permission_tasks
                    WHERE status IN ('pending', 'running')
                """))
            queue_length = count_result.scalar() or 0

            is_saturated = queue_length >= PERMISSION_MAX_QUEUE_SIZE
            return (is_saturated, queue_length)

        except Exception as e:
            logger.error(f"Error checking queue saturation: {e}")
            return (False, 0)

    def generate_task_checksum(self, project_id: int, path: str) -> str:
        """Generate checksum for task deduplication."""
        data = f"{project_id}:{path}:{int(time.time() / 300)}"  # 5-minute window
        return hashlib.md5(data.encode()).hexdigest()

    def check_existing_task(self, db, project_id: int) -> dict | None:
        """Check if there's an existing pending/running task for this project.

        Returns:
            Existing task dict or None.
        """
        try:
            result = db.execute(
                sa.text("""
                    SELECT task_id, status, progress, created_at
                    FROM permission_tasks
                    WHERE project_id = :project_id
                    AND status IN ('pending', 'running')
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"project_id": project_id},
            )
            row = result.fetchone()

            if row:
                return {
                    "task_id": row[0],
                    "status": row[1],
                    "progress": row[2],
                    "created_at": ensure_utc_suffix(row[3]),
                }
            return None

        except Exception as e:
            logger.error(f"Error checking existing task: {e}")
            return None

    def submit_task(
        self,
        db,
        project_id: int,
        user_id: int,
        path: str,
        priority: int | None = None,
        depth_limit: int | None = None,
    ) -> tuple[bool, str, dict | None]:
        """Submit a new permission setup task.

        Args:
            db: Database session
            project_id: Project ID
            user_id: User ID initiating the task
            path: Project path
            priority: Task priority (lower = higher priority)
            depth_limit: Maximum recursion depth

        Returns:
            Tuple of (success, error_message, task_info).
        """
        try:
            # Check queue saturation
            is_saturated, queue_length = self.check_queue_saturation(db)
            if is_saturated:
                return (
                    False,
                    f"Task queue is full ({queue_length}/{PERMISSION_MAX_QUEUE_SIZE}). Please retry later.",
                    None,
                )

            # Check for existing task (deduplication)
            existing_task = self.check_existing_task(db, project_id)
            if existing_task:
                logger.info(
                    f"Returning existing task {existing_task['task_id']} for project {project_id}"
                )
                return (True, "", existing_task)

            # Estimate file count
            file_count = estimate_file_count_fast(path)

            # Generate task ID and checksum
            task_id = str(uuid.uuid4())
            checksum = self.generate_task_checksum(project_id, path)

            # Set default priority
            if priority is None:
                priority = PERMISSION_PRIORITY_AUTO_CREATE

            # Set default depth limit
            if depth_limit is None:
                depth_limit = PERMISSION_MAX_DEPTH

            # Insert task into database
            db.execute(
                sa.text("""
                    INSERT INTO permission_tasks
                    (task_id, project_id, user_id, path, status, priority,
                     progress, files_processed, total_files, depth_limit,
                     checksum, created_at)
                    VALUES
                    (:task_id, :project_id, :user_id, :path, 'pending', :priority,
                     0, 0, :total_files, :depth_limit, :checksum, :created_at)
                """),
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "user_id": user_id,
                    "path": path,
                    "priority": priority,
                    "total_files": file_count,
                    "depth_limit": depth_limit,
                    "checksum": checksum,
                    "created_at": datetime.now(timezone.utc),
                },
            )

            # Update project status to 'setting'
            db.execute(
                sa.text("""
                    UPDATE projects
                    SET permission_status = 'setting', permission_task_id = :task_id
                    WHERE id = :project_id
                """),
                {"task_id": task_id, "project_id": project_id},
            )

            db.commit()

            task_info = {
                "task_id": task_id,
                "status": "pending",
                "priority": priority,
                "estimated_files": file_count,
                "queue_position": queue_length + 1,
            }

            logger.info(f"Submitted permission task {task_id} for project {project_id}")
            return (True, "", task_info)

        except Exception as e:
            logger.error(f"Error submitting task: {e}")
            db.rollback()
            return (False, f"Failed to submit task: {e}", None)

    def get_task_status(self, db, task_id: str) -> dict | None:
        """Get task status by task ID.

        Returns:
            Task status dict or None.
        """
        try:
            result = db.execute(
                sa.text("""
                    SELECT task_id, project_id, user_id, path, status, priority,
                           progress, files_processed, total_files, depth_limit,
                           error_message, created_at, started_at, completed_at
                    FROM permission_tasks
                    WHERE task_id = :task_id
                """),
                {"task_id": task_id},
            )
            row = result.fetchone()

            if not row:
                return None

            return {
                "task_id": row[0],
                "project_id": row[1],
                "user_id": row[2],
                "path": row[3],
                "status": row[4],
                "priority": row[5],
                "progress": row[6],
                "files_processed": row[7],
                "total_files": row[8],
                "depth_limit": row[9],
                "error_message": row[10],
                "created_at": ensure_utc_suffix(row[11]),
                "started_at": ensure_utc_suffix(row[12]),
                "completed_at": ensure_utc_suffix(row[13]),
            }

        except Exception as e:
            logger.error(f"Error getting task status: {e}")
            return None

    def cancel_task(self, db, task_id: str) -> tuple[bool, str]:
        """Cancel a pending or running task.

        Returns:
            Tuple of (success, error_message).
        """
        try:
            # Check task status
            task_status = self.get_task_status(db, task_id)
            if not task_status:
                return (False, "Task not found")

            if task_status["status"] not in ("pending", "running"):
                return (False, f"Cannot cancel task in {task_status['status']} state")

            # Update task status
            db.execute(
                sa.text("""
                    UPDATE permission_tasks
                    SET status = 'cancelled', completed_at = :completed_at
                    WHERE task_id = :task_id
                """),
                {"task_id": task_id, "completed_at": datetime.now(timezone.utc)},
            )

            # Update project status back to null
            db.execute(
                sa.text("""
                    UPDATE projects
                    SET permission_status = NULL, permission_task_id = NULL
                    WHERE id = :project_id
                """),
                {"project_id": task_status["project_id"]},
            )

            db.commit()

            logger.info(f"Cancelled permission task {task_id}")
            return (True, "")

        except Exception as e:
            logger.error(f"Error cancelling task: {e}")
            db.rollback()
            return (False, f"Failed to cancel task: {e}")

    def cleanup_old_tasks(self, db):
        """Clean up old completed/failed tasks.

        Removes tasks older than PERMISSION_TASK_CLEANUP_DAYS days.
        """
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=PERMISSION_TASK_CLEANUP_DAYS)

            db.execute(
                sa.text("""
                    DELETE FROM permission_tasks
                    WHERE status IN ('completed', 'failed', 'cancelled')
                    AND created_at < :cutoff_date
                """),
                {"cutoff_date": cutoff_date},
            )

            db.commit()
            logger.debug(f"Cleaned up old permission tasks")

        except Exception as e:
            logger.error(f"Error cleaning up old tasks: {e}")
            db.rollback()


# Singleton instance
_service: PermissionTaskService | None = None


def get_permission_task_service() -> PermissionTaskService:
    """Get the singleton PermissionTaskService instance."""
    global _service
    if _service is None:
        _service = PermissionTaskService()
    return _service
