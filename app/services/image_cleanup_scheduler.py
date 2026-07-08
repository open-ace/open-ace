"""
Open ACE - Image Cleanup Scheduler

Background scheduler for cleaning up expired uploaded images.
Follows the DataFetchScheduler pattern (threading + daemon).
"""

import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ImageCleanupScheduler:
    """
    Background scheduler for expired image cleanup.

    Runs cleanup at configurable intervals (default: 1 hour).
    Uses singleton pattern with threading.Thread + daemon.
    """

    _instance: Optional[ImageCleanupScheduler] = None
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

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interval = 3600  # Default: 1 hour
        self._enabled = True
        self._running = False
        self._last_run: Optional[datetime] = None
        self._next_run: Optional[datetime] = None
        self._initialized = True
        logger.info("ImageCleanupScheduler initialized")

    def configure(self, interval: Optional[int] = None, enabled: Optional[bool] = None):
        """
        Configure the scheduler.

        Args:
            interval: Cleanup interval in seconds (minimum 60).
            enabled: Whether cleanup is enabled.
        """
        if interval is not None:
            self._interval = max(60, interval)
            logger.info(f"Image cleanup interval set to {self._interval} seconds")

        if enabled is not None:
            self._enabled = enabled
            logger.info(f"Image cleanup enabled: {self._enabled}")

    def start(self):
        """Start the scheduler."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("ImageCleanupScheduler is already running")
            return

        if not self._enabled:
            logger.info("ImageCleanupScheduler is disabled, not starting")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info(f"ImageCleanupScheduler started with interval {self._interval} seconds")

    def stop(self):
        """Stop the scheduler."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        self._running = False
        logger.info("ImageCleanupScheduler stopped")

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Get scheduler status."""
        next_run_str = None
        if self._next_run:
            try:
                next_run_dt = datetime.fromtimestamp(self._next_run)
                next_run_str = next_run_dt.isoformat()
            except (TypeError, ValueError):
                pass

        return {
            "running": self._running,
            "enabled": self._enabled,
            "interval": self._interval,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "next_run": next_run_str,
        }

    def _run_loop(self):
        """Main scheduler loop."""
        # Calculate next run time
        self._next_run = datetime.now().timestamp() + self._interval

        while not self._stop_event.is_set():
            # Wait for the interval or stop event
            if self._stop_event.wait(timeout=self._interval):
                break

            # Run cleanup
            self._run_cleanup()

            # Update next run time
            self._next_run = datetime.now().timestamp() + self._interval

    def _run_cleanup(self):
        """Run the cleanup process."""
        logger.info("Starting scheduled image cleanup...")
        self._last_run = datetime.now()

        try:
            from app.services.image_service import get_image_service

            image_service = get_image_service()
            deleted_count, failed_count = image_service.cleanup_expired_images()

            logger.info(
                f"Scheduled image cleanup completed: "
                f"deleted={deleted_count}, failed={failed_count}"
            )

            # Send alert if there were failures
            if failed_count > 0:
                self._send_cleanup_alert(deleted_count, failed_count)

        except Exception as e:
            logger.exception(f"Error in scheduled image cleanup: {e}")

        # Check disk space
        self._check_disk_space()

    def _send_cleanup_alert(self, deleted_count: int, failed_count: int):
        """Send alert for cleanup failures."""
        try:
            from app.modules.governance.alert_notifier import create_alert

            create_alert(
                alert_type="image_cleanup_failure",
                severity="warning",
                title="Image Cleanup Failures",
                message=f"Failed to delete {failed_count} expired images during cleanup. "
                        f"Successfully deleted: {deleted_count}",
                metadata={
                    "deleted_count": deleted_count,
                    "failed_count": failed_count,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to send cleanup alert: {e}")

    def _check_disk_space(self):
        """Check disk space and send warning if threshold exceeded."""
        try:
            from app.services.image_service import get_image_service
            from app.services.storage_quota_service import get_storage_quota_service

            image_service = get_image_service()
            quota_service = get_storage_quota_service()

            config = image_service.get_config()
            storage_path = config.storage_path
            if storage_path.startswith("~"):
                storage_path = os.path.expanduser(storage_path)

            import os
            os.makedirs(storage_path, exist_ok=True)

            disk_ok, disk_warning = quota_service.check_disk_space(
                storage_path,
                config.space_threshold_pct
            )

            if not disk_ok and disk_warning:
                logger.warning(f"Disk space warning: {disk_warning}")
                self._send_disk_space_alert(disk_warning)

        except Exception as e:
            logger.warning(f"Failed to check disk space: {e}")

    def _send_disk_space_alert(self, warning_message: str):
        """Send alert for disk space threshold."""
        try:
            from app.modules.governance.alert_notifier import create_alert

            create_alert(
                alert_type="disk_space_warning",
                severity="warning",
                title="Disk Space Threshold Exceeded",
                message=warning_message,
            )
        except Exception as e:
            logger.warning(f"Failed to send disk space alert: {e}")


# Global scheduler instance
scheduler = ImageCleanupScheduler()


def init_image_cleanup_scheduler():
    """Initialize and start the image cleanup scheduler."""
    import json
    import os

    # Try to load config
    interval = 3600  # Default: 1 hour
    enabled = True

    try:
        config_path = os.path.expanduser("~/.open-ace/config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
            upload_config = config.get("upload", {})
            image_config = upload_config.get("image", {})
            # Allow configuring cleanup interval (in seconds)
            interval = image_config.get("cleanup_interval_seconds", 3600)
            enabled = image_config.get("cleanup_enabled", True)
    except Exception as e:
        logger.warning(f"Failed to load cleanup config: {e}")

    scheduler.configure(interval=interval, enabled=enabled)

    if enabled:
        scheduler.start()
        logger.info(f"Image cleanup scheduler started with interval {interval} seconds")
    else:
        logger.info("Image cleanup scheduler is disabled")