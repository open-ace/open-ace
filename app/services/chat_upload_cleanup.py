"""Chat upload cleanup scheduler.

Issue #127: Periodically clean up old chat attachment files.

This scheduler removes chat uploads older than a configurable number of days
(default 7 days) to prevent unbounded disk usage.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from app.services.distributed_scheduler import DistributedScheduler

logger = logging.getLogger(__name__)

# Configuration: max age in days for chat uploads
CHAT_UPLOAD_MAX_AGE_DAYS = int(os.environ.get("OPENACE_CHAT_UPLOAD_MAX_AGE_DAYS", "7"))

# Chat uploads directory name (must match fs.py)
CHAT_UPLOADS_DIR = ".qwen/chat-uploads"


class ChatUploadCleanupScheduler(DistributedScheduler):
    """Scheduler to clean up old chat upload files.

    Issue #127: Implements automatic cleanup of chat attachments to prevent
    unbounded disk usage. Files older than OPENACE_CHAT_UPLOAD_MAX_AGE_DAYS
    are deleted.
    """

    def __init__(self, db):
        """Initialize the scheduler.

        Args:
            db: Database instance.
        """
        super().__init__(
            job_name="chat_upload_cleanup",
            db=db,
            strategy="heartbeat",
            lock_timeout=600,  # 10 minutes max execution time
        )
        self.db = db

    def _run_job(self) -> None:
        """Execute the cleanup job."""
        logger.info("Starting chat upload cleanup (max age: %d days)", CHAT_UPLOAD_MAX_AGE_DAYS)

        try:
            deleted_count, error_count = self._cleanup_old_uploads()
            logger.info(
                "Chat upload cleanup completed: deleted=%d, errors=%d",
                deleted_count,
                error_count,
            )
        except Exception as e:
            logger.error("Failed to cleanup chat uploads: %s", e)
            raise

    def _cleanup_old_uploads(self) -> tuple[int, int]:
        """Find and delete old chat upload files.

        Returns:
            Tuple of (deleted_count, error_count).
        """
        deleted_count = 0
        error_count = 0
        cutoff_time = time.time() - (CHAT_UPLOAD_MAX_AGE_DAYS * 24 * 60 * 60)

        # Find all user home directories
        # In multi-user mode, homes are under WORKSPACE_BASE_DIR
        # In single-user mode, home is the user's home directory
        from app.utils.workspace import get_workspace_base_dirs

        base_dirs = get_workspace_base_dirs()

        for base_dir in base_dirs:
            if not os.path.isdir(base_dir):
                continue

            # Iterate over user directories
            try:
                user_dirs = os.listdir(base_dir)
            except OSError as e:
                logger.warning("Cannot list base directory %s: %s", base_dir, e)
                continue

            for user_dir_name in user_dirs:
                user_home = os.path.join(base_dir, user_dir_name)
                chat_uploads_dir = os.path.join(user_home, CHAT_UPLOADS_DIR)

                if not os.path.isdir(chat_uploads_dir):
                    continue

                # Clean up old session directories
                d, e = self._cleanup_session_dirs(chat_uploads_dir, cutoff_time)
                deleted_count += d
                error_count += e

        return deleted_count, error_count

    def _cleanup_session_dirs(
        self, chat_uploads_dir: str, cutoff_time: float
    ) -> tuple[int, int]:
        """Clean up old session directories within a user's chat uploads folder.

        Args:
            chat_uploads_dir: Path to user's chat uploads directory.
            cutoff_time: Files older than this timestamp will be deleted.

        Returns:
            Tuple of (deleted_count, error_count).
        """
        deleted_count = 0
        error_count = 0

        try:
            session_dirs = os.listdir(chat_uploads_dir)
        except OSError as e:
            logger.warning("Cannot list chat uploads directory %s: %s", chat_uploads_dir, e)
            return 0, 1

        for session_id in session_dirs:
            session_path = os.path.join(chat_uploads_dir, session_id)

            if not os.path.isdir(session_path):
                continue

            # Check if any files in the session directory are old
            try:
                session_mtime = os.path.getmtime(session_path)
                if session_mtime < cutoff_time:
                    # Delete the entire session directory
                    shutil.rmtree(session_path)
                    deleted_count += 1
                    logger.debug("Deleted old chat upload session: %s", session_path)
            except OSError as e:
                logger.warning("Failed to delete session directory %s: %s", session_path, e)
                error_count += 1

        return deleted_count, error_count


def cleanup_chat_uploads_once() -> tuple[int, int]:
    """Run a single cleanup pass without the scheduler.

    Useful for manual cleanup or testing.

    Returns:
        Tuple of (deleted_count, error_count).
    """
    logger.info("Running manual chat upload cleanup")
    deleted_count = 0
    error_count = 0
    cutoff_time = time.time() - (CHAT_UPLOAD_MAX_AGE_DAYS * 24 * 60 * 60)

    from app.utils.workspace import get_workspace_base_dirs

    base_dirs = get_workspace_base_dirs()

    for base_dir in base_dirs:
        if not os.path.isdir(base_dir):
            continue

        try:
            user_dirs = os.listdir(base_dir)
        except OSError:
            continue

        for user_dir_name in user_dirs:
            user_home = os.path.join(base_dir, user_dir_name)
            chat_uploads_dir = os.path.join(user_home, CHAT_UPLOADS_DIR)

            if not os.path.isdir(chat_uploads_dir):
                continue

            try:
                session_dirs = os.listdir(chat_uploads_dir)
            except OSError:
                continue

            for session_id in session_dirs:
                session_path = os.path.join(chat_uploads_dir, session_id)

                if not os.path.isdir(session_path):
                    continue

                try:
                    session_mtime = os.path.getmtime(session_path)
                    if session_mtime < cutoff_time:
                        shutil.rmtree(session_path)
                        deleted_count += 1
                except OSError as e:
                    logger.warning("Failed to delete %s: %s", session_path, e)
                    error_count += 1

    logger.info("Manual cleanup completed: deleted=%d, errors=%d", deleted_count, error_count)
    return deleted_count, error_count


def start_chat_upload_cleanup() -> None:
    """Start the chat upload cleanup scheduler.

    Issue #127: Registers and starts the periodic cleanup of old chat uploads
    to prevent unbounded disk usage.
    """
    try:
        from app.repositories.database import Database

        db = Database()
        scheduler = ChatUploadCleanupScheduler(db)
        scheduler.start()
        logger.info("Chat upload cleanup scheduler started")
    except Exception as e:
        logger.warning("Failed to start chat upload cleanup scheduler: %s", e)
