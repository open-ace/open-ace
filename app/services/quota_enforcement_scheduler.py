#!/usr/bin/env python3
"""
Open ACE - Quota Enforcement Scheduler

Background scheduler that periodically checks all users' quotas
and enforces limits (terminates sessions, generates alerts).
"""

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class QuotaEnforcementScheduler:
    """Background scheduler for proactive quota enforcement."""

    _instance = None
    _lock = threading.Lock()

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
        logger.info("QuotaEnforcementScheduler initialized")

    def configure(self, interval: int = None, enabled: bool = None):
        if interval is not None:
            self._interval = max(30, interval)
            logger.info(f"Quota enforcement interval set to {self._interval} seconds")

        if enabled is not None:
            self._enabled = enabled

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            logger.warning("QuotaEnforcementScheduler is already running")
            return

        if not self._enabled:
            logger.info("QuotaEnforcementScheduler is disabled, not starting")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info(f"QuotaEnforcementScheduler started with interval {self._interval} seconds")

    def stop(self):
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        self._running = False
        logger.info("QuotaEnforcementScheduler stopped")

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
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
        self._next_run = datetime.now().timestamp() + self._interval

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self._interval):
                break

            self._run_enforcement()
            self._next_run = datetime.now().timestamp() + self._interval

    def _run_enforcement(self):
        """Run quota enforcement check for all users."""
        from app.repositories.database import Database, adapt_sql

        today = datetime.utcnow().strftime("%Y-%m-%d")
        month_start = datetime.utcnow().replace(day=1).strftime("%Y-%m-%d")
        self._last_run = datetime.now()

        exceeded_users = set()

        try:
            db = Database()

            # Check daily quotas
            daily_rows = db.fetch_all(
                adapt_sql("""
                    SELECT uds.user_id, uds.requests AS today_requests,
                           uds.tokens AS today_tokens, u.username,
                           u.daily_request_quota, u.daily_token_quota
                    FROM user_daily_stats uds
                    JOIN users u ON uds.user_id = u.id
                    WHERE uds.date = ?
                      AND u.is_active = 1
                      AND (
                        uds.requests >= COALESCE(u.daily_request_quota, 999999)
                        OR uds.tokens >= COALESCE(u.daily_token_quota, 999999) * 1000000
                      )
                """),
                (today,),
            )

            for row in daily_rows:
                exceeded_users.add(row["user_id"])
                self._enforce_user(row, today, "daily")

            # Check monthly quotas
            monthly_rows = db.fetch_all(
                adapt_sql("""
                    SELECT u.user_id, SUM(uds.requests) AS month_requests,
                           SUM(uds.tokens) AS month_tokens, u.username,
                           u.monthly_request_quota, u.monthly_token_quota
                    FROM user_daily_stats uds
                    JOIN users u ON uds.user_id = u.id
                    WHERE uds.date >= ? AND uds.date <= ?
                      AND u.is_active = 1
                      AND u.monthly_token_quota IS NOT NULL
                      AND (
                        SUM(uds.requests) >= COALESCE(u.monthly_request_quota, 999999)
                        OR SUM(uds.tokens) >= COALESCE(u.monthly_token_quota, 999999) * 1000000
                      )
                    GROUP BY u.user_id, u.username, u.monthly_request_quota, u.monthly_token_quota
                """),
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

        # Create alert
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
            from app.modules.governance.alert_notifier import create_quota_alert

            create_quota_alert(
                user_id=user_id,
                username=username,
                usage_percent=max_pct,
                quota_type=(
                    f"{period}_requests" if requests_pct >= tokens_pct else f"{period}_tokens"
                ),
            )
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
    import os
    import sys

    scripts_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts", "shared"
    )
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)

    try:
        from config import get_quota_enforcement_config

        config = get_quota_enforcement_config()
        enforcement_scheduler.configure(
            interval=config.get("interval", 60),
            enabled=config.get("enabled", True),
        )
    except Exception:
        enforcement_scheduler.configure(interval=60, enabled=True)

    if enforcement_scheduler._enabled:
        enforcement_scheduler.start()
        logger.info("Quota enforcement scheduler started")
    else:
        logger.info("Quota enforcement scheduler is disabled")
