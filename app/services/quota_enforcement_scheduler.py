"""
Open ACE - Quota Enforcement Scheduler

Background scheduler that periodically checks all users' quotas
and enforces limits (terminates sessions, generates alerts).

Supports multiple implementation backends:
- threading: Default Python threading (may not work with gevent)
- gevent: Greenlet-based scheduling for gevent environments
- apscheduler: APScheduler-based scheduling (recommended for stability)
"""

from __future__ import annotations
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Configuration - can be overridden via environment variables
SCHEDULER_IMPLEMENTATION = os.environ.get(
    "SCHEDULER_IMPLEMENTATION", "threading"
).lower()  # threading, gevent, apscheduler

# Try to import APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("APScheduler not available, falling back to threading")


class QuotaEnforcementScheduler:
    """Background scheduler for proactive quota enforcement."""

    _instance: QuotaEnforcementScheduler | None = None
    _lock = threading.Lock()
    _initialized: bool

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

        self._thread = None
        self._stop_event = threading.Event()
        self._interval = 60
        self._enabled = True
        self._running = False
        self._last_run = None
        self._next_run = None
        self._initialized = True
        self._enforced_users = set()
        self._implementation = SCHEDULER_IMPLEMENTATION
        self._scheduler = None  # APScheduler instance
        self._job = None  # APScheduler job
        self._heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)
        logger.info(
            f"QuotaEnforcementScheduler initialized (implementation: {self._implementation})"
        )

    def configure(
        self,
        interval: int | None = None,
        enabled: bool | None = None,
        implementation: str | None = None,
    ):
        """Configure the scheduler.

        Args:
            interval: Check interval in seconds.
            enabled: Whether the scheduler is enabled.
            implementation: Implementation backend ('threading', 'gevent', 'apscheduler').
        """
        if interval is not None:
            self._interval = max(30, interval)
            logger.info(f"Quota enforcement interval set to {self._interval} seconds")

        if enabled is not None:
            self._enabled = enabled

        if implementation is not None:
            self._implementation = implementation.lower()
            logger.info(f"Scheduler implementation set to {self._implementation}")

    def start(self):
        """Start the scheduler."""
        if self._running:
            logger.warning("QuotaEnforcementScheduler is already running")
            return

        if not self._enabled:
            logger.info("QuotaEnforcementScheduler is disabled, not starting")
            return

        self._stop_event.clear()

        # Choose implementation
        if self._implementation == "apscheduler" and APSCHEDULER_AVAILABLE:
            self._start_apscheduler()
        elif self._implementation == "gevent":
            self._start_gevent()
        else:
            self._start_threading()

        self._running = True
        self._heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)
        logger.info(
            f"QuotaEnforcementScheduler started with interval {self._interval} seconds "
            f"(implementation: {self._implementation})"
        )

    def _start_threading(self):
        """Start using standard threading."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _start_gevent(self):
        """Start using gevent greenlets."""
        try:
            import gevent
            import gevent.event

            self._gevent_stop_event = gevent.event.Event()

            def gevent_loop():
                while not self._gevent_stop_event.is_set():
                    self._run_enforcement()
                    self._heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)
                    gevent.sleep(self._interval)

            self._greenlet = gevent.spawn(gevent_loop)
            logger.info("Started gevent-based scheduler")

        except ImportError:
            logger.warning("gevent not available, falling back to threading")
            self._start_threading()

    def _start_apscheduler(self):
        """Start using APScheduler."""
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler not available, falling back to threading")
            self._start_threading()
            return

        self._scheduler = BackgroundScheduler()
        self._job = self._scheduler.add_job(
            self._run_enforcement_with_heartbeat,
            IntervalTrigger(seconds=self._interval),
            id="quota_enforcement",
            name="Quota Enforcement Check",
            replace_existing=True,
        )
        self._scheduler.start()

    def _run_enforcement_with_heartbeat(self):
        """Run enforcement and update heartbeat."""
        self._run_enforcement()
        self._heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)

    def stop(self):
        """Stop the scheduler."""
        if not self._running:
            return

        if self._implementation == "apscheduler" and self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            self._job = None
        elif self._implementation == "gevent" and hasattr(self, "_greenlet"):
            self._gevent_stop_event.set()
            self._greenlet.kill()
        elif self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=5)

        self._running = False
        logger.info("QuotaEnforcementScheduler stopped")

    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        if self._implementation == "apscheduler" and self._scheduler:
            return bool(self._scheduler.running)
        elif self._implementation == "gevent" and hasattr(self, "_greenlet"):
            return not self._greenlet.dead
        else:
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

        # Check heartbeat freshness (should be within interval + 60 seconds)
        heartbeat_age = None
        if self._heartbeat:
            heartbeat_age = (
                datetime.now(timezone.utc).replace(tzinfo=None) - self._heartbeat
            ).total_seconds()

        heartbeat_ok = heartbeat_age is not None and heartbeat_age < self._interval + 60

        return {
            "running": self.is_running(),
            "enabled": self._enabled,
            "interval": self._interval,
            "implementation": self._implementation,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "next_run": next_run_str,
            "heartbeat": self._heartbeat.isoformat() if self._heartbeat else None,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_ok": heartbeat_ok,
        }

    def _run_loop(self):
        """Main run loop for threading implementation."""
        self._next_run = datetime.now().timestamp() + self._interval

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self._interval):
                break

            self._run_enforcement()
            self._heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)
            self._next_run = datetime.now().timestamp() + self._interval

    def _run_enforcement(self):
        """Run quota enforcement check for all users."""
        from app.repositories.database import (
            Database,
            adapt_boolean_condition,
            adapt_sql,
            is_postgresql,
        )

        bigint_cast = "::bigint" if is_postgresql() else ""
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        month_start = (
            datetime.now(timezone.utc).replace(tzinfo=None).replace(day=1).strftime("%Y-%m-%d")
        )
        self._last_run = datetime.now()

        exceeded_users = set()

        try:
            db = Database()

            # Check daily quotas
            daily_rows = db.fetch_all(
                adapt_sql(
                    f"""
                    SELECT uds.user_id, uds.requests AS today_requests,
                           uds.tokens AS today_tokens, u.username,
                           u.daily_request_quota, u.daily_token_quota
                    FROM user_daily_stats uds
                    JOIN users u ON uds.user_id = u.id
                    WHERE uds.date = ?
                      AND {adapt_boolean_condition("u.is_active", True)}
                      AND (
                        uds.requests >= COALESCE(u.daily_request_quota, 999999)
                        OR uds.tokens >= COALESCE(u.daily_token_quota, 999999){bigint_cast} * 1000000
                      )
                """
                ),
                (today,),
            )

            for row in daily_rows:
                exceeded_users.add(row["user_id"])
                self._enforce_user(row, today, "daily")

            # Check monthly quotas
            monthly_rows = db.fetch_all(
                adapt_sql(
                    f"""
                    SELECT u.id AS user_id, SUM(uds.requests) AS month_requests,
                           SUM(uds.tokens) AS month_tokens, u.username,
                           u.monthly_request_quota, u.monthly_token_quota
                    FROM user_daily_stats uds
                    JOIN users u ON uds.user_id = u.id
                    WHERE uds.date >= ? AND uds.date <= ?
                      AND {adapt_boolean_condition("u.is_active", True)}
                    GROUP BY u.id, u.username, u.monthly_request_quota, u.monthly_token_quota
                    HAVING SUM(uds.requests) >= COALESCE(u.monthly_request_quota, 999999)
                        OR SUM(uds.tokens) >= COALESCE(u.monthly_token_quota, 999999){bigint_cast} * 1000000
                """
                ),
                (month_start, today),
            )

            for row in monthly_rows:
                if row["user_id"] not in exceeded_users:
                    self._enforce_user(row, today, "monthly", month_prefix="month_")

        except Exception as e:
            logger.error(f"Quota enforcement check failed: {e}")

    def _enforce_user(self, row, today, period, month_prefix=""):
        """Enforce quota for a single exceeded user."""
        user_id = row["user_id"]
        username = row.get("username", "")
        action_key = f"{user_id}:quota_exceeded:{today}:{period}"

        if action_key in self._enforced_users:
            return

        # Create alert using transactional dual-write
        try:
            req_key = f"{month_prefix}requests" if month_prefix else "today_requests"
            tok_key = f"{month_prefix}tokens" if month_prefix else "today_tokens"
            req_quota_key = (
                f"{month_prefix}request_quota" if month_prefix else "daily_request_quota"
            )
            tok_quota_key = f"{month_prefix}token_quota" if month_prefix else "daily_token_quota"

            tokens_pct = (
                row[tok_key] / (row[tok_quota_key] * 1_000_000) * 100
                if row.get(tok_quota_key)
                else 0
            )
            requests_pct = row[req_key] / row[req_quota_key] * 100 if row.get(req_quota_key) else 0
            max_pct = max(tokens_pct, requests_pct)

            # Use transactional alert creation
            from app.modules.governance.alert_transaction_manager import (
                create_quota_alert_transactional,
            )

            success, alert_id = create_quota_alert_transactional(
                user_id=user_id,
                username=username,
                usage_percent=max_pct,
                quota_type=(
                    f"{period}_requests" if requests_pct >= tokens_pct else f"{period}_tokens"
                ),
            )

            if success:
                logger.info(
                    f"Created quota alert for user {user_id} "
                    f"({period}, {max_pct:.1f}%, alert_id={alert_id})"
                )
            else:
                logger.warning(f"Failed to create quota alert for user {user_id}")

        except Exception as e:
            logger.warning(f"Failed to create quota alert for user {user_id}: {e}")

        # Terminate active sessions
        try:
            from app.modules.workspace.session_manager import SessionManager

            sm = SessionManager()
            active_sessions = sm.get_active_sessions(user_id)
            for session in active_sessions:
                try:
                    sm.complete_session(session.session_id)
                    logger.info(
                        f"Completed session {session.session_id[:8]} for user {user_id} ({period} quota exceeded)"
                    )
                except Exception as e:
                    logger.warning(f"Failed to complete session {session.session_id[:8]}: {e}")
        except Exception as e:
            logger.warning(f"Failed to terminate sessions for user {user_id}: {e}")

        self._enforced_users.add(action_key)
        self._enforced_users = {k for k in self._enforced_users if today in k}


# Global instance
enforcement_scheduler = QuotaEnforcementScheduler()


def init_quota_enforcement():
    """Initialize and start the quota enforcement scheduler."""
    import sys

    scripts_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts", "shared"
    )
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)

    # Get configuration from environment or config file
    implementation = os.environ.get("SCHEDULER_IMPLEMENTATION", "threading")
    interval = int(os.environ.get("QUOTA_ENFORCEMENT_INTERVAL", "60"))
    enabled = os.environ.get("QUOTA_ENFORCEMENT_ENABLED", "true").lower() == "true"

    try:
        from config import get_quota_enforcement_config  # type: ignore[attr-defined]

        config = get_quota_enforcement_config()
        interval = config.get("interval", interval)
        enabled = config.get("enabled", enabled)
        implementation = config.get("implementation", implementation)
    except Exception:
        pass

    enforcement_scheduler.configure(
        interval=interval,
        enabled=enabled,
        implementation=implementation,
    )

    if enforcement_scheduler._enabled:
        enforcement_scheduler.start()
        logger.info(f"Quota enforcement scheduler started (implementation: {implementation})")
    else:
        logger.info("Quota enforcement scheduler is disabled")
