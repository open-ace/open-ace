"""
Open ACE - Quota Manager Module

Provides quota management and alerting for enterprise usage control.
Tracks user quotas, generates alerts, and enforces limits.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from app.repositories.database import (
    Database,
    adapt_boolean_condition,
    adapt_boolean_value,
    adapt_sql,
)
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Token quotas are stored in M (millions) units
# Convert to actual tokens when comparing with usage
TOKEN_QUOTA_MULTIPLIER = 1_000_000


class AlertType(Enum):
    """Types of quota alerts."""

    WARNING = "warning"  # 80% of quota
    CRITICAL = "critical"  # 95% of quota
    EXCEEDED = "exceeded"  # Over quota
    RESET = "reset"  # Quota reset


class QuotaPeriod(Enum):
    """Quota period types."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class QuotaAlert:
    """Quota alert data model."""

    id: Optional[int] = None
    user_id: int = 0
    alert_type: str = "warning"
    quota_type: str = "tokens"  # tokens or requests
    period: str = "daily"
    threshold: float = 0.0
    current_usage: int = 0
    quota_limit: int = 0
    percentage: float = 0.0
    message: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "alert_type": self.alert_type,
            "quota_type": self.quota_type,
            "period": self.period,
            "threshold": self.threshold,
            "current_usage": self.current_usage,
            "quota_limit": self.quota_limit,
            "percentage": self.percentage,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "acknowledged_by": self.acknowledged_by,
        }


@dataclass
class QuotaStatus:
    """Current quota status for a user."""

    user_id: int
    username: str = ""
    period: str = "daily"

    # Token quotas
    token_limit: int = 0
    tokens_used: int = 0
    token_percentage: float = 0.0

    # Request quotas
    request_limit: int = 0
    requests_used: int = 0
    request_percentage: float = 0.0

    # Status
    is_over_token_quota: bool = False
    is_over_request_quota: bool = False
    alerts: list[QuotaAlert] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "period": self.period,
            "tokens": {
                "limit": self.token_limit,
                "used": self.tokens_used,
                "percentage": self.token_percentage,
                "over_quota": self.is_over_token_quota,
            },
            "requests": {
                "limit": self.request_limit,
                "used": self.requests_used,
                "percentage": self.request_percentage,
                "over_quota": self.is_over_request_quota,
            },
            "alerts": [a.to_dict() for a in self.alerts],
        }


class QuotaManager:
    """
    Quota management service for enterprise usage control.

    Features:
    - Track user token and request quotas
    - Generate alerts at configurable thresholds
    - Enforce quota limits
    - Support for daily, weekly, and monthly periods
    """

    # Default alert thresholds (percentage of quota)
    DEFAULT_THRESHOLDS = [0.8, 0.95, 1.0]  # 80%, 95%, 100%

    def __init__(
        self,
        db: Optional[Database] = None,
        user_repo: Optional[UserRepository] = None,
        thresholds: Optional[list[float]] = None,
    ):
        """
        Initialize quota manager.

        Args:
            db: Optional Database instance.
            user_repo: Optional UserRepository instance.
            thresholds: Alert thresholds (default: [0.8, 0.95, 1.0]).
        """
        self.db = db or Database()
        self.user_repo = user_repo or UserRepository()
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS
        # Table structure managed by Alembic migrations

    def record_usage(
        self, user_id: int, tokens: int = 0, requests: int = 1, date: Optional[str] = None
    ) -> bool:
        """
        Record usage for a user.

        Args:
            user_id: User ID.
            tokens: Number of tokens used.
            requests: Number of requests made.
            date: Date string (YYYY-MM-DD), defaults to today.

        Returns:
            bool: True if successful.
        """
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Use db.execute with adapt_sql for cross-DB compatibility (? → %s for PostgreSQL)
                cursor.execute(
                    adapt_sql("""
                    INSERT INTO quota_usage (user_id, date, period, tokens_used, requests_used)
                    VALUES (?, ?, 'daily', ?, ?)
                    ON CONFLICT(user_id, date, period) DO UPDATE SET
                        tokens_used = quota_usage.tokens_used + ?,
                        requests_used = quota_usage.requests_used + ?,
                        updated_at = CURRENT_TIMESTAMP
                """),
                    (user_id, date, tokens, requests, tokens, requests),
                )

                conn.commit()

            # Check for alerts
            self._check_and_create_alerts(user_id, date)

            return True

        except Exception as e:
            logger.error(f"Failed to record usage: {e}")
            return False

    def get_user_quota_status(self, user_id: int, period: str = "daily") -> QuotaStatus:
        """
        Get quota status for a user.

        Args:
            user_id: User ID.
            period: Quota period (daily, weekly, monthly).

        Returns:
            QuotaStatus: Current quota status.
        """
        # Get user info
        user = self.user_repo.get_user_by_id(user_id)
        username = user.get("username", "") if user else ""

        # Get quota limits based on period
        if period == "monthly":
            token_limit = (
                user.get("monthly_token_quota") * TOKEN_QUOTA_MULTIPLIER
                if user and user.get("monthly_token_quota")
                else None
            )
            request_limit = user.get("monthly_request_quota") if user else None
        else:
            token_limit = (
                user.get("daily_token_quota") * TOKEN_QUOTA_MULTIPLIER
                if user and user.get("daily_token_quota")
                else None
            )
            request_limit = user.get("daily_request_quota") if user else None

        # Default limits if not set
        if token_limit is None:
            token_limit = 1000000  # 1M tokens default
        if request_limit is None:
            request_limit = 1000  # 1000 requests default

        # Get usage for period
        start_date, end_date = self._get_period_dates(period)
        usage = self._get_usage_in_range(user_id, start_date, end_date)

        tokens_used = usage.get("tokens", 0)
        requests_used = usage.get("requests", 0)

        # Calculate percentages
        token_pct = (tokens_used / token_limit * 100) if token_limit > 0 else 0
        request_pct = (requests_used / request_limit * 100) if request_limit > 0 else 0

        # Get recent alerts
        alerts = self._get_recent_alerts(user_id, limit=5)

        return QuotaStatus(
            user_id=user_id,
            username=username,
            period=period,
            token_limit=token_limit,
            tokens_used=tokens_used,
            token_percentage=round(token_pct, 2),
            request_limit=request_limit,
            requests_used=requests_used,
            request_percentage=round(request_pct, 2),
            is_over_token_quota=tokens_used >= token_limit,
            is_over_request_quota=requests_used >= request_limit,
            alerts=alerts,
        )

    def check_quota(self, user_id: int, tokens: int = 0, requests: int = 1) -> dict[str, Any]:
        """
        Check if user has quota available.

        Args:
            user_id: User ID.
            tokens: Tokens to check.
            requests: Requests to check.

        Returns:
            Dict with 'allowed', 'reason', and 'status' keys.
        """
        status = self.get_user_quota_status(user_id)

        # Check if would exceed after this usage
        would_exceed_tokens = (status.tokens_used + tokens) >= status.token_limit
        would_exceed_requests = (status.requests_used + requests) >= status.request_limit

        if would_exceed_tokens:
            return {
                "allowed": False,
                "reason": f"Token quota exceeded. Used: {status.tokens_used}/{status.token_limit}",
                "status": status.to_dict(),
            }

        if would_exceed_requests:
            return {
                "allowed": False,
                "reason": f"Request quota exceeded. Used: {status.requests_used}/{status.request_limit}",
                "status": status.to_dict(),
            }

        # Check monthly quotas
        user = self.user_repo.get_user_by_id(user_id)
        if user:
            monthly_token_quota = user.get("monthly_token_quota")
            monthly_request_quota = user.get("monthly_request_quota")
            if monthly_token_quota or monthly_request_quota:
                monthly_status = self.get_user_quota_status(user_id, period="monthly")
                if (
                    monthly_token_quota
                    and monthly_status.tokens_used + tokens
                    >= monthly_token_quota * TOKEN_QUOTA_MULTIPLIER
                ):
                    return {
                        "allowed": False,
                        "reason": f"Monthly token quota exceeded. Used: {monthly_status.tokens_used}/{monthly_token_quota * TOKEN_QUOTA_MULTIPLIER}",
                        "status": monthly_status.to_dict(),
                    }
                if (
                    monthly_request_quota
                    and monthly_status.requests_used + requests >= monthly_request_quota
                ):
                    return {
                        "allowed": False,
                        "reason": f"Monthly request quota exceeded. Used: {monthly_status.requests_used}/{monthly_request_quota}",
                        "status": monthly_status.to_dict(),
                    }

        return {
            "allowed": True,
            "reason": None,
            "status": status.to_dict(),
        }

    def _check_and_create_alerts(self, user_id: int, date: str) -> None:
        """Check usage and create alerts if thresholds crossed."""
        status = self.get_user_quota_status(user_id)

        # Check token percentage
        self._create_alert_if_needed(
            user_id=user_id,
            quota_type="tokens",
            current_usage=status.tokens_used,
            quota_limit=status.token_limit,
            percentage=status.token_percentage / 100,
        )

        # Check request percentage
        self._create_alert_if_needed(
            user_id=user_id,
            quota_type="requests",
            current_usage=status.requests_used,
            quota_limit=status.request_limit,
            percentage=status.request_percentage / 100,
        )

        # Also push to the main alerts table (supports WebSocket notifications)
        try:
            user = self.user_repo.get_user_by_id(user_id)
            username = user.get("username", "") if user else ""
            max_pct = max(status.token_percentage, status.request_percentage)
            if max_pct >= 80:
                from app.modules.governance.alert_notifier import create_quota_alert

                create_quota_alert(
                    user_id=user_id,
                    username=username,
                    usage_percent=max_pct,
                    quota_type=(
                        "tokens"
                        if status.token_percentage >= status.request_percentage
                        else "requests"
                    ),
                )
        except Exception as e:
            logger.warning(f"Failed to push quota alert to notifier: {e}")

    def _create_alert_if_needed(
        self, user_id: int, quota_type: str, current_usage: int, quota_limit: int, percentage: float
    ) -> None:
        """Create alert if threshold is crossed."""
        for threshold in sorted(self.thresholds, reverse=True):
            if percentage >= threshold:
                # Check if we already have an alert for this threshold today
                today = datetime.utcnow().strftime("%Y-%m-%d")

                existing = self.db.fetch_one(
                    """
                    SELECT id FROM quota_alerts
                    WHERE user_id = ? AND quota_type = ?
                    AND date(created_at) = ?
                    AND threshold >= ?
                """,
                    (user_id, quota_type, today, threshold),
                )

                if existing:
                    return

                # Determine alert type
                if percentage >= 1.0:
                    alert_type = "exceeded"
                elif percentage >= 0.95:
                    alert_type = "critical"
                else:
                    alert_type = "warning"

                # Create alert
                message = self._generate_alert_message(
                    alert_type, quota_type, current_usage, quota_limit, percentage
                )

                try:
                    with self.db.connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT INTO quota_alerts
                            (user_id, alert_type, quota_type, threshold,
                             current_usage, quota_limit, percentage, message)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                user_id,
                                alert_type,
                                quota_type,
                                threshold,
                                current_usage,
                                quota_limit,
                                percentage,
                                message,
                            ),
                        )
                        conn.commit()

                    logger.warning(
                        f"Quota alert created: user={user_id}, type={alert_type}, {quota_type}={percentage*100:.1f}%"
                    )

                except Exception as e:
                    logger.error(f"Failed to create quota alert: {e}")

                return

    def _generate_alert_message(
        self, alert_type: str, quota_type: str, current: int, limit: int, percentage: float
    ) -> str:
        """Generate alert message."""
        pct_str = f"{percentage * 100:.1f}%"

        if alert_type == "exceeded":
            return f"Quota exceeded: {quota_type} usage at {pct_str} ({current:,}/{limit:,})"
        elif alert_type == "critical":
            return f"Critical: {quota_type} usage at {pct_str} ({current:,}/{limit:,})"
        else:
            return f"Warning: {quota_type} usage at {pct_str} ({current:,}/{limit:,})"

    def _get_period_dates(self, period: str) -> tuple:
        """Get start and end dates for a period."""
        today = datetime.utcnow()

        if period == "daily":
            start = today.strftime("%Y-%m-%d")
            end = start
        elif period == "weekly":
            start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
        elif period == "monthly":
            start = today.replace(day=1).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
        else:
            start = today.strftime("%Y-%m-%d")
            end = start

        return start, end

    def _get_usage_from_daily_stats(
        self, user_id: int, start_date: str, end_date: str
    ) -> dict[str, int]:
        """Get usage from pre-aggregated user_daily_stats table (fast path)."""
        result = self.db.fetch_one(
            """
            SELECT
                COALESCE(SUM(requests), 0) as requests,
                COALESCE(SUM(tokens), 0) as tokens
            FROM user_daily_stats
            WHERE user_id = ? AND date >= ? AND date <= ?
        """,
            (user_id, start_date, end_date),
        )
        if result:
            return {
                "tokens": int(result["tokens"]),
                "requests": int(result["requests"]),
            }
        return {"tokens": 0, "requests": 0}

    def _get_usage_in_range(self, user_id: int, start_date: str, end_date: str) -> dict[str, int]:
        """Get total usage in a date range. Tries user_daily_stats first, falls back to raw queries."""
        # Fast path: use pre-aggregated user_daily_stats
        try:
            stats = self._get_usage_from_daily_stats(user_id, start_date, end_date)
            if stats["tokens"] > 0 or stats["requests"] > 0:
                return stats
        except Exception:
            pass  # Fall through to legacy query

        # Legacy path: agent_sessions (remote) + daily_messages (local)
        # Remote session usage from agent_sessions
        remote_result = self.db.fetch_one(
            """
            SELECT
                COALESCE(SUM(total_tokens), 0) as tokens,
                COALESCE(SUM(
                    (SELECT COUNT(*) FROM session_messages sm
                     WHERE sm.session_id = agent_sessions.session_id
                       AND sm.role IN ('assistant', 'toolResult'))
                ), 0) as requests
            FROM agent_sessions
            WHERE user_id = ?
              AND workspace_type = 'remote'
              AND CAST(created_at AS DATE) >= ? AND CAST(created_at AS DATE) <= ?
        """,
            (user_id, start_date, end_date),
        )

        remote_tokens = int(remote_result["tokens"]) if remote_result else 0
        remote_requests = int(remote_result["requests"]) if remote_result else 0

        # Local CLI usage from daily_messages — use sender_name LIKE since many rows lack user_id
        local_tokens = 0
        local_requests = 0
        user = self.user_repo.get_user_by_id(user_id)
        if user:
            system_account = user.get("system_account") or user.get("username", "")
            if system_account:
                local_result = self.db.fetch_one(
                    """
                    SELECT
                        COALESCE(SUM(tokens_used), 0) as tokens,
                        COUNT(*) as requests
                    FROM daily_messages
                    WHERE sender_name LIKE ? AND date >= ? AND date <= ?
                      AND role = 'assistant'
                      AND (message_source IS NULL OR message_source != 'remote_workspace')
                """,
                    (f"{system_account}%", start_date, end_date),
                )
                local_tokens = int(local_result["tokens"]) if local_result else 0
                local_requests = int(local_result["requests"]) if local_result else 0

        return {
            "tokens": remote_tokens + local_tokens,
            "requests": remote_requests + local_requests,
        }

    def _get_recent_alerts(self, user_id: int, limit: int = 10) -> list[QuotaAlert]:
        """Get recent alerts for a user."""
        rows = self.db.fetch_all(
            """
            SELECT * FROM quota_alerts
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (user_id, limit),
        )

        alerts = []
        for row in rows:
            alerts.append(
                QuotaAlert(
                    id=row.get("id"),
                    user_id=row.get("user_id", 0),
                    alert_type=row.get("alert_type", "warning"),
                    quota_type=row.get("quota_type", "tokens"),
                    period=row.get("period", "daily"),
                    threshold=row.get("threshold", 0),
                    current_usage=row.get("current_usage", 0),
                    quota_limit=row.get("quota_limit", 0),
                    percentage=row.get("percentage", 0),
                    message=row.get("message", ""),
                    created_at=(
                        datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None
                    ),
                    acknowledged=bool(row.get("acknowledged", 0)),
                    acknowledged_at=(
                        datetime.fromisoformat(row["acknowledged_at"])
                        if row.get("acknowledged_at")
                        else None
                    ),
                    acknowledged_by=row.get("acknowledged_by"),
                )
            )

        return alerts

    def acknowledge_alert(self, alert_id: int, acknowledged_by: int) -> bool:
        """Acknowledge an alert."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql("""
                    UPDATE quota_alerts
                    SET acknowledged = ?,
                        acknowledged_at = ?,
                        acknowledged_by = ?
                    WHERE id = ?
                """),
                    (adapt_boolean_value(True), datetime.utcnow(), acknowledged_by, alert_id),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to acknowledge alert: {e}")
            return False

    def get_all_quota_statuses(self) -> list[QuotaStatus]:
        """Get quota status for all users with optimized batch queries."""
        users = self.user_repo.get_all_users(include_inactive=False)

        if not users:
            return []

        # Get all user IDs
        user_ids = [u.get("id") for u in users if u.get("id")]

        # Batch query: get remote usage from agent_sessions for all users
        start_date, end_date = self._get_period_dates("daily")
        remote_usage_rows = self.db.fetch_all(
            """
            SELECT user_id,
                   COALESCE(SUM(total_tokens), 0) as tokens,
                   COALESCE(SUM(
                       (SELECT COUNT(*) FROM session_messages sm
                        WHERE sm.session_id = agent_sessions.session_id
                          AND sm.role IN ('assistant', 'toolResult'))
                   ), 0) as requests
            FROM agent_sessions
            WHERE user_id IN ({}) AND workspace_type = 'remote'
              AND CAST(created_at AS DATE) >= ? AND CAST(created_at AS DATE) <= ?
            GROUP BY user_id
        """.format(",".join(["?"] * len(user_ids))),
            tuple(user_ids) + (start_date, end_date),
        )

        # Build remote usage lookup
        remote_usage_lookup = {
            row["user_id"]: {"tokens": row["tokens"], "requests": row["requests"]}
            for row in remote_usage_rows
        }

        # Batch query: get local CLI usage from daily_messages for all users
        # Use sender_name LIKE to cover rows that lack user_id
        sender_conditions = []
        sender_params = []
        for user in users:
            system_account = user.get("system_account") or user.get("username", "")
            if system_account:
                sender_conditions.append("sender_name LIKE ?")
                sender_params.append(f"{system_account}%")

        local_usage_lookup: dict[int, int] = {}
        if sender_conditions:
            local_usage_rows = self.db.fetch_all(
                """
                SELECT sender_name,
                       COALESCE(SUM(tokens_used), 0) as tokens,
                       COUNT(*) as requests
                FROM daily_messages
                WHERE ({}) AND date >= ? AND date <= ?
                  AND role = 'assistant'
                  AND (message_source IS NULL OR message_source != 'remote_workspace')
                GROUP BY sender_name
            """.format(" OR ".join(sender_conditions)),
                tuple(sender_params) + (start_date, end_date),
            )

            # Map sender_name results back to user_id
            for row in local_usage_rows:
                sender_name = row["sender_name"] or ""
                for user in users:
                    uid = user.get("id")
                    sa = user.get("system_account") or user.get("username", "")
                    if sa and sender_name.startswith(sa):
                        existing = local_usage_lookup.get(uid, {"tokens": 0, "requests": 0})
                        local_usage_lookup[uid] = {
                            "tokens": existing["tokens"] + int(row["tokens"]),
                            "requests": existing["requests"] + int(row["requests"]),
                        }
                        break

        # Batch query: get recent alerts for all users
        alert_rows = self.db.fetch_all(
            """
            SELECT * FROM quota_alerts
            WHERE user_id IN ({})
            ORDER BY created_at DESC
        """.format(",".join(["?"] * len(user_ids))),
            tuple(user_ids),
        )

        # Build alerts lookup by user_id
        alerts_lookup: dict[int, list[dict[str, Any]]] = {}
        for row in alert_rows:
            user_id = row.get("user_id")
            if user_id not in alerts_lookup:
                alerts_lookup[user_id] = []
            if len(alerts_lookup[user_id]) < 5:  # Limit to 5 alerts per user
                alerts_lookup[user_id].append(
                    QuotaAlert(
                        id=row.get("id"),
                        user_id=row.get("user_id", 0),
                        alert_type=row.get("alert_type", "warning"),
                        quota_type=row.get("quota_type", "tokens"),
                        period=row.get("period", "daily"),
                        threshold=row.get("threshold", 0),
                        current_usage=row.get("current_usage", 0),
                        quota_limit=row.get("quota_limit", 0),
                        percentage=row.get("percentage", 0),
                        message=row.get("message", ""),
                        created_at=(
                            datetime.fromisoformat(row["created_at"])
                            if row.get("created_at")
                            else None
                        ),
                        acknowledged=bool(row.get("acknowledged", 0)),
                        acknowledged_at=(
                            datetime.fromisoformat(row["acknowledged_at"])
                            if row.get("acknowledged_at")
                            else None
                        ),
                        acknowledged_by=row.get("acknowledged_by"),
                    )
                )

        # Build statuses from batch data
        statuses = []
        for user in users:
            user_id = user.get("id")
            if not user_id:
                continue

            username = user.get("username", "")
            token_limit = (
                user.get("daily_token_quota") or 1
            ) * TOKEN_QUOTA_MULTIPLIER  # Convert M units to actual tokens
            request_limit = user.get("daily_request_quota") or 1000

            remote_usage = remote_usage_lookup.get(user_id, {"tokens": 0, "requests": 0})
            local_usage = local_usage_lookup.get(user_id, {"tokens": 0, "requests": 0})
            tokens_used = int(remote_usage.get("tokens", 0)) + int(local_usage.get("tokens", 0))
            requests_used = int(remote_usage.get("requests", 0)) + int(
                local_usage.get("requests", 0)
            )

            token_pct = (tokens_used / token_limit * 100) if token_limit > 0 else 0
            request_pct = (requests_used / request_limit * 100) if request_limit > 0 else 0

            alerts = alerts_lookup.get(user_id, [])

            statuses.append(
                QuotaStatus(
                    user_id=user_id,
                    username=username,
                    period="daily",
                    token_limit=token_limit,
                    tokens_used=tokens_used,
                    token_percentage=round(token_pct, 2),
                    request_limit=request_limit,
                    requests_used=requests_used,
                    request_percentage=round(request_pct, 2),
                    is_over_token_quota=tokens_used >= token_limit,
                    is_over_request_quota=requests_used >= request_limit,
                    alerts=alerts,
                )
            )

        return statuses

    def get_all_alerts(
        self, unacknowledged_only: bool = False, limit: int = 100
    ) -> list[QuotaAlert]:
        """Get all quota alerts."""
        if unacknowledged_only:
            rows = self.db.fetch_all(
                adapt_sql(f"""
                SELECT * FROM quota_alerts
                WHERE {adapt_boolean_condition('acknowledged', False)}
                ORDER BY created_at DESC
                LIMIT ?
            """),
                (limit,),
            )
        else:
            rows = self.db.fetch_all(
                """
                SELECT * FROM quota_alerts
                ORDER BY created_at DESC
                LIMIT ?
            """,
                (limit,),
            )

        alerts = []
        for row in rows:
            alerts.append(
                QuotaAlert(
                    id=row.get("id"),
                    user_id=row.get("user_id", 0),
                    alert_type=row.get("alert_type", "warning"),
                    quota_type=row.get("quota_type", "tokens"),
                    period=row.get("period", "daily"),
                    threshold=row.get("threshold", 0),
                    current_usage=row.get("current_usage", 0),
                    quota_limit=row.get("quota_limit", 0),
                    percentage=row.get("percentage", 0),
                    message=row.get("message", ""),
                    created_at=(
                        datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None
                    ),
                    acknowledged=bool(row.get("acknowledged", 0)),
                    acknowledged_at=(
                        datetime.fromisoformat(row["acknowledged_at"])
                        if row.get("acknowledged_at")
                        else None
                    ),
                    acknowledged_by=row.get("acknowledged_by"),
                )
            )

        return alerts

    def cleanup_old_alerts(self, days: int = 30) -> int:
        """Delete old acknowledged alerts."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql(f"""
                    DELETE FROM quota_alerts
                    WHERE {adapt_boolean_condition('acknowledged', True)} AND created_at < ?
                """),
                    (cutoff,),
                )
                deleted = cursor.rowcount
                conn.commit()

            logger.info(f"Cleaned up {deleted} old quota alerts")
            return deleted

        except Exception as e:
            logger.error(f"Failed to cleanup alerts: {e}")
            return 0
