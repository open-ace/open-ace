"""
Open ACE - Data Fetch Scheduler

Background scheduler for automatic data fetching at configurable intervals.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Configuration - can be overridden via environment variables
SCHEDULER_IMPLEMENTATION = os.environ.get(
    "SCHEDULER_IMPLEMENTATION", "threading"
).lower()  # threading, gevent, apscheduler

# Try to import APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler  # noqa: F401
    from apscheduler.triggers.interval import IntervalTrigger  # noqa: F401

    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("APScheduler not available, falling back to threading")


class DataFetchScheduler:
    """
    Background scheduler for automatic data fetching.

    Runs data fetch scripts at configurable intervals.
    """

    _instance: DataFetchScheduler | None = None
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
        self._interval = 300  # Default: 5 minutes
        self._enabled = True
        self._running = False
        self._last_run = None
        self._next_run = None
        self._heartbeat = None
        self._initialized = True
        self._implementation = SCHEDULER_IMPLEMENTATION
        self._scheduler = None  # APScheduler instance
        logger.info(f"DataFetchScheduler initialized (implementation: {self._implementation})")

    def configure(self, interval: int | None = None, enabled: bool | None = None):
        """
        Configure the scheduler.

        Args:
            interval: Fetch interval in seconds.
            enabled: Whether auto fetch is enabled.
        """
        if interval is not None:
            self._interval = max(60, interval)  # Minimum 1 minute
            logger.info(f"Data fetch interval set to {self._interval} seconds")

        if enabled is not None:
            self._enabled = enabled
            logger.info(f"Data fetch enabled: {self._enabled}")

    def start(self):
        """Start the scheduler."""
        if self._running:
            logger.warning("DataFetchScheduler is already running")
            return

        if not self._enabled:
            logger.info("DataFetchScheduler is disabled, not starting")
            return

        self._stop_event.clear()

        if self._implementation == "apscheduler" and APSCHEDULER_AVAILABLE:
            self._start_apscheduler()
        elif self._implementation == "gevent":
            self._start_gevent()
        else:
            self._start_threading()

        self._running = True
        logger.info(
            f"DataFetchScheduler started (implementation: {self._implementation}, interval: {self._interval}s)"
        )

    def _start_threading(self):
        """Start using threading backend."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _start_gevent(self):
        """Start using gevent greenlets."""
        try:
            import gevent
            import gevent.event

            self._gevent_stop_event = gevent.event.Event()

            def gevent_loop():
                from datetime import timezone as tz

                self._next_run = datetime.now().timestamp() + self._interval
                while not self._gevent_stop_event.is_set():
                    gevent.sleep(self._interval)
                    self._run_fetch()
                    self._heartbeat = datetime.now(tz.utc).replace(tzinfo=None)
                    self._next_run = datetime.now().timestamp() + self._interval

            self._greenlet = gevent.spawn(gevent_loop)
            logger.info("Started gevent-based scheduler")

        except ImportError:
            logger.warning("gevent not available, falling back to threading")
            self._start_threading()

    def _start_apscheduler(self):
        """Start using APScheduler backend."""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._run_fetch_with_heartbeat,
            IntervalTrigger(seconds=self._interval),
            id="data_fetch",
            name="Data Fetch",
            replace_existing=True,
        )
        self._scheduler.start()

    def _run_fetch_with_heartbeat(self):
        """Run fetch and update heartbeat."""
        self._run_fetch()
        from datetime import timezone as tz

        self._heartbeat = datetime.now(tz.utc).replace(tzinfo=None)

    def stop(self):
        """Stop the scheduler."""
        if self._implementation == "apscheduler" and self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        elif self._implementation == "gevent" and hasattr(self, "_greenlet"):
            self._gevent_stop_event.set()
            self._greenlet.kill()
        elif self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=5)

        self._running = False
        logger.info("DataFetchScheduler stopped")

    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        if self._implementation == "apscheduler":
            return self._running and self._scheduler and self._scheduler.running
        elif self._implementation == "gevent":
            return self._running and hasattr(self, "_greenlet") and not self._greenlet.dead
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Get scheduler status."""
        from datetime import datetime as dt
        from datetime import timezone as tz

        next_run_str = None
        if self._next_run:
            try:
                next_run_dt = dt.fromtimestamp(self._next_run)
                next_run_str = next_run_dt.isoformat()
            except (TypeError, ValueError):
                pass

        # Check heartbeat freshness
        heartbeat_age = None
        if self._heartbeat:
            heartbeat_age = (dt.now(tz.utc).replace(tzinfo=None) - self._heartbeat).total_seconds()

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
        """Main scheduler loop."""
        from datetime import timezone as tz

        # Calculate next run time
        self._next_run = datetime.now().timestamp() + self._interval

        while not self._stop_event.is_set():
            # Wait for the interval or stop event
            if self._stop_event.wait(timeout=self._interval):
                break

            # Run fetch
            self._run_fetch()

            # Update heartbeat
            self._heartbeat = datetime.now(tz.utc).replace(tzinfo=None)

            # Update next run time
            self._next_run = datetime.now().timestamp() + self._interval

    def _run_fetch(self):
        """Run the data fetch scripts."""
        from datetime import timezone as tz

        from app.routes.fetch import run_fetch_scripts

        logger.info("Starting scheduled data fetch...")
        self._last_run = datetime.now(tz.utc).replace(tzinfo=None)

        try:
            run_fetch_scripts()
            logger.info("Scheduled data fetch completed")
        except Exception as e:
            logger.exception(f"Error in scheduled data fetch: {e}")

        # Refresh materialized views for PostgreSQL
        self._refresh_materialized_views()

        # Safety net: aggregate user_daily_stats periodically
        self._aggregate_user_stats()

        # Refresh usage_summary table
        self._refresh_usage_summary()

        # Check quotas after data is fresh
        self._check_quotas()

    def _refresh_materialized_views(self):
        """Refresh materialized views for PostgreSQL performance optimization."""
        from app.repositories.database import Database, is_postgresql

        if not is_postgresql():
            return

        try:
            db = Database()
            # Check if session_stats materialized view exists
            mv_check = db.fetch_one(
                "SELECT EXISTS (SELECT FROM pg_matviews WHERE matviewname = 'session_stats')"
            )
            if mv_check and mv_check.get("exists", False):
                db.execute("REFRESH MATERIALIZED VIEW session_stats")
                logger.info("Refreshed session_stats materialized view")
        except Exception as e:
            logger.warning(f"Error refreshing materialized views: {e}")

    def _aggregate_user_stats(self):
        """Safety net: aggregate user_daily_stats periodically."""
        try:
            from app.services.user_stats_aggregator import aggregate_user_stats_background

            aggregate_user_stats_background()
        except Exception as e:
            logger.warning(f"Scheduled user stats aggregation failed: {e}")

    def _refresh_usage_summary(self):
        """Refresh usage_summary table after new data is fetched."""
        from app.services.summary_service import SummaryService

        try:
            summary_service = SummaryService()
            summary_service.refresh_summary()
            logger.info("Usage summary refreshed")
        except Exception as e:
            logger.warning(f"Usage summary refresh failed: {e}")

    def _check_quotas(self):
        """Check all users' quotas and enforce limits after data refresh."""
        from datetime import datetime as dt
        from datetime import timezone as tz

        from app.repositories.database import (
            Database,
            adapt_boolean_condition,
            adapt_sql,
            is_postgresql,
        )

        bigint_cast = "::bigint" if is_postgresql() else ""
        today = dt.now(tz.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        month_start = dt.now(tz.utc).replace(tzinfo=None).replace(day=1).strftime("%Y-%m-%d")
        db = Database()

        exceeded_users = set()

        try:
            # Find users who exceeded their daily quota
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
                self._enforce_user_quota(row, today, "daily")

            # Find users who exceeded their monthly quota
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
                      AND u.monthly_token_quota IS NOT NULL
                    GROUP BY u.id, u.username, u.monthly_request_quota, u.monthly_token_quota
                    HAVING SUM(uds.requests) >= COALESCE(u.monthly_request_quota, 999999)
                        OR SUM(uds.tokens) >= COALESCE(u.monthly_token_quota, 999999){bigint_cast} * 1000000
                """
                ),
                (month_start, today),
            )

            for row in monthly_rows:
                if row["user_id"] not in exceeded_users:
                    self._enforce_user_quota(row, today, "monthly", month_prefix="month_")

        except Exception as e:
            logger.error(f"Quota enforcement check failed: {e}")

    def _enforce_user_quota(self, row, today, period, month_prefix=""):
        """Enforce quota for a single exceeded user."""
        user_id = row["user_id"]
        username = row.get("username", "")
        action_key = f"{user_id}:quota_exceeded:{today}:{period}"

        if not hasattr(self, "_enforced_users"):
            self._enforced_users: set[str] = set()
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

        req_key = f"{month_prefix}requests" if month_prefix else "today_requests"
        tok_key = f"{month_prefix}tokens" if month_prefix else "today_tokens"
        logger.warning(
            f"Quota enforced ({period}): user={username}({user_id}), "
            f"requests={row[req_key]}/{row.get('daily_request_quota' if not month_prefix else 'monthly_request_quota')}, "
            f"tokens={row[tok_key]}/{row.get('daily_token_quota' if not month_prefix else 'monthly_token_quota', 0) * 1_000_000}"
        )


# Global scheduler instance
scheduler = DataFetchScheduler()


def init_scheduler():
    """Initialize and start the data fetch scheduler."""
    import os
    import sys

    # Add scripts/shared to path for config import
    scripts_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts", "shared"
    )
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)

    from config import get_data_fetch_interval, is_data_fetch_enabled  # type: ignore[attr-defined]

    interval = get_data_fetch_interval()
    enabled = is_data_fetch_enabled()

    scheduler.configure(interval=interval, enabled=enabled)

    if enabled:
        scheduler.start()
        logger.info(f"Data fetch scheduler started with interval {interval} seconds")
    else:
        logger.info("Data fetch scheduler is disabled")
