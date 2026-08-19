"""
Open ACE - Audit Analyzer

Analyzes audit logs for compliance and security insights.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.modules.governance.audit_logger import AuditLogger
from app.repositories.database import adapt_sql, get_db_connection, is_postgresql  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class AnomalyDetection:
    """Detected anomaly in audit data."""

    anomaly_type: str
    severity: str  # low, medium, high
    description: str
    affected_users: list[int]
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    details: dict[str, Any]


def _parse_timestamp(val: Any) -> datetime:
    """Parse a timestamp value from SQL into a datetime.

    Handles datetime objects, ISO-format strings, and falls back to now().
    """
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditAnalyzer:
    """
    Analyzer for audit logs.

    Features:
    - Pattern detection
    - Anomaly detection
    - User behavior analysis
    - Security insights
    """

    # Risk weights by anomaly type for scoring
    RISK_WEIGHTS = {
        "excessive_failed_logins": 1.5,
        "rapid_activity": 1.2,
        "off_hours_activity": 1.0,
        "frequent_role_changes": 1.8,
        "frequent_permission_changes": 1.6,
    }

    # Base deductions by severity level
    BASE_DEDUCTIONS = {"high": 15, "medium": 8, "low": 3}

    def __init__(
        self,
        audit_logger: AuditLogger | None = None,
        settings: dict[str, Any] | None = None,
    ):
        self.audit_logger = audit_logger or AuditLogger()
        settings = settings or {}
        self.failed_login_threshold = settings.get("audit_failed_login_threshold", 5)
        self.rapid_action_threshold = settings.get("audit_rapid_action_threshold", 50)
        self.off_hours_threshold = settings.get("audit_off_hours_threshold", 10)
        self.role_change_threshold = settings.get("audit_role_change_threshold", 5)
        self.permission_change_threshold = settings.get("audit_permission_change_threshold", 10)

    def analyze_patterns(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Analyze patterns in audit logs using SQL aggregation.

        Uses database-side GROUP BY queries instead of loading objects into
        Python, so results cover the full time range regardless of data volume.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.
            tenant_id: Tenant ID for data isolation. If provided, only analyze
                audit logs belonging to this tenant.

        Returns:
            Dict with pattern analysis results including completeness metadata.
        """
        if not start_time:
            start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # Build base WHERE clause matching audit_logger.query() filters
        conditions = ["timestamp >= ?", "timestamp <= ?"]
        params: list[Any] = [start_time, end_time]
        self._build_tenant_filter(conditions, params, tenant_id=tenant_id)

        where_clause = " AND ".join(conditions)

        # Use database-specific hour/day extraction
        if is_postgresql():
            hour_expr = "EXTRACT(HOUR FROM timestamp)"
            # PostgreSQL: EXTRACT(DOW FROM ts) returns 0=Sunday..6=Saturday
            # Python weekday(): 0=Monday..6=Sunday
            # Convert: (dow - 1 + 7) % 7 gives Python weekday
            day_expr = "CAST((EXTRACT(DOW FROM timestamp)::int - 1 + 7) % 7 AS int)"
        else:
            # SQLite: strftime('%w', ts) returns 0=Sunday..6=Saturday
            # Convert to Python weekday: (cast(strftime('%w') as int) - 1 + 7) % 7
            hour_expr = "CAST(strftime('%H', timestamp) AS integer)"
            day_expr = "(CAST(strftime('%w', timestamp) AS integer) - 1 + 7) % 7"

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # 1. Total count
                cursor.execute(
                    adapt_sql(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE {where_clause}"),
                    tuple(params),
                )
                row = cursor.fetchone()
                total_events = int(row["cnt"]) if row else 0

                # 2. Hourly distribution
                cursor.execute(
                    adapt_sql(
                        f"SELECT {hour_expr} as hour, COUNT(*) as cnt "
                        f"FROM audit_logs WHERE {where_clause} "
                        f"GROUP BY {hour_expr} ORDER BY {hour_expr}"
                    ),
                    tuple(params),
                )
                hourly_distribution = {}
                for r in cursor.fetchall():
                    hourly_distribution[int(r["hour"])] = int(r["cnt"])

                # 3. Login hourly distribution
                login_where = f"{where_clause} AND action = ?"
                login_params = list(params) + ["login"]
                cursor.execute(
                    adapt_sql(
                        f"SELECT {hour_expr} as hour, COUNT(*) as cnt "
                        f"FROM audit_logs WHERE {login_where} "
                        f"GROUP BY {hour_expr} ORDER BY {hour_expr}"
                    ),
                    tuple(login_params),
                )
                login_hourly_distribution = {}
                for r in cursor.fetchall():
                    login_hourly_distribution[int(r["hour"])] = int(r["cnt"])

                # 4. Daily distribution (by weekday)
                cursor.execute(
                    adapt_sql(
                        f"SELECT {day_expr} as day, COUNT(*) as cnt "
                        f"FROM audit_logs WHERE {where_clause} "
                        f"GROUP BY {day_expr} ORDER BY {day_expr}"
                    ),
                    tuple(params),
                )
                daily_distribution = {}
                for r in cursor.fetchall():
                    daily_distribution[int(r["day"])] = int(r["cnt"])

                # 5. Action distribution
                cursor.execute(
                    adapt_sql(
                        f"SELECT action, COUNT(*) as cnt "
                        f"FROM audit_logs WHERE {where_clause} "
                        f"GROUP BY action ORDER BY cnt DESC"
                    ),
                    tuple(params),
                )
                action_distribution = {}
                for r in cursor.fetchall():
                    action_distribution[r["action"]] = int(r["cnt"])

                # 6. Top users
                cursor.execute(
                    adapt_sql(
                        f"SELECT user_id, COUNT(*) as cnt "
                        f"FROM audit_logs WHERE {where_clause} AND user_id IS NOT NULL "
                        f"GROUP BY user_id ORDER BY cnt DESC LIMIT 10"
                    ),
                    tuple(params),
                )
                top_users = []
                for r in cursor.fetchall():
                    top_users.append((int(r["user_id"]), int(r["cnt"])))

                # 7. Unique users count
                cursor.execute(
                    adapt_sql(
                        f"SELECT COUNT(DISTINCT user_id) as cnt "
                        f"FROM audit_logs WHERE {where_clause} AND user_id IS NOT NULL"
                    ),
                    tuple(params),
                )
                unique_users_row = cursor.fetchone()
                unique_users = int(unique_users_row["cnt"]) if unique_users_row else 0

                # 8. Oldest analyzed timestamp
                cursor.execute(
                    adapt_sql(
                        f"SELECT MIN(timestamp) as oldest FROM audit_logs WHERE {where_clause}"
                    ),
                    tuple(params),
                )
                oldest_row = cursor.fetchone()
                oldest_val = oldest_row["oldest"] if oldest_row else None
                oldest_analyzed_at = None
                if oldest_val is not None:
                    if isinstance(oldest_val, str):
                        try:
                            oldest_analyzed_at = datetime.fromisoformat(oldest_val)
                        except (ValueError, TypeError):
                            oldest_analyzed_at = None
                    elif isinstance(oldest_val, datetime):
                        oldest_analyzed_at = oldest_val

        except Exception as e:
            logger.error(f"Failed to analyze patterns via SQL: {e}", exc_info=True)
            return {
                "period": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                },
                "total_events": 0,
                "matching_events": 0,
                "analyzed_events": 0,
                "truncated": True,  # Cannot confirm completeness
                "coverage_ratio": 0.0,
                "oldest_analyzed_at": None,
                "error": "Pattern analysis failed due to a database error",
                "hourly_distribution": {},
                "login_hourly_distribution": {},
                "daily_distribution": {},
                "action_distribution": {},
                "unique_users": 0,
                "top_users": [],
            }

        return {
            "period": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
            # Backward-compatible: total_events now reflects real count
            "total_events": total_events,
            # Completeness metadata (Issue #2750)
            "matching_events": total_events,
            "analyzed_events": total_events,
            "truncated": False,
            "coverage_ratio": 1.0,
            "oldest_analyzed_at": oldest_analyzed_at.isoformat() if oldest_analyzed_at else None,
            "hourly_distribution": dict(sorted(hourly_distribution.items())),
            "login_hourly_distribution": dict(sorted(login_hourly_distribution.items())),
            "daily_distribution": dict(sorted(daily_distribution.items())),
            "action_distribution": action_distribution,
            "unique_users": unique_users,
            "top_users": top_users,
        }

    def detect_anomalies(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: int | None = None,
    ) -> list[AnomalyDetection]:
        """
        Detect anomalies in audit logs using SQL-based detection.

        Each detector uses a specialized SQL query that only retrieves the
        aggregated data needed, instead of loading up to 10,000 full objects.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.
            tenant_id: Tenant ID for data isolation. If provided, only analyze
                audit logs belonging to this tenant.

        Returns:
            List[AnomalyDetection]: Detected anomalies.
        """
        if not start_time:
            start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        if not end_time:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        anomalies = []

        # Detect failed login anomalies
        failed_login_anomaly = self._detect_failed_login_anomaly(
            start_time, end_time, tenant_id=tenant_id
        )
        if failed_login_anomaly:
            anomalies.append(failed_login_anomaly)

        # Detect rapid activity anomalies
        rapid_activity_anomalies = self._detect_rapid_activity_anomaly(
            start_time, end_time, tenant_id=tenant_id
        )
        anomalies.extend(rapid_activity_anomalies)

        # Detect off-hours activity anomalies
        off_hours_anomalies = self._detect_off_hours_anomaly(
            start_time, end_time, tenant_id=tenant_id
        )
        anomalies.extend(off_hours_anomalies)

        # Detect unusual action patterns
        action_anomalies = self._detect_action_pattern_anomaly(
            start_time, end_time, tenant_id=tenant_id
        )
        anomalies.extend(action_anomalies)

        return anomalies

    def _build_tenant_filter(
        self, conditions: list[str], params: list[Any], tenant_id: int | None = None
    ) -> tuple[list[str], list[Any]]:
        """Add tenant scope filter to conditions/params if applicable.

        Args:
            conditions: SQL WHERE conditions list
            params: SQL parameters list
            tenant_id: Optional tenant ID for data isolation. If provided,
                only analyze audit logs belonging to this tenant.
        """
        normalized_tenant_id = self.audit_logger._normalize_tenant_id(tenant_id)
        if normalized_tenant_id is not None:
            conditions.append(
                "(tenant_id = ? OR (tenant_id IS NULL AND user_id IN "
                "(SELECT id FROM users WHERE tenant_id = ?)))"
            )
            params.extend([normalized_tenant_id, normalized_tenant_id])
        return conditions, params

    def _detect_failed_login_anomaly(
        self, start_time: datetime, end_time: datetime, tenant_id: int | None = None
    ) -> AnomalyDetection | None:
        """Detect failed login anomalies using SQL aggregation."""
        conditions = ["action = ?", "timestamp >= ?", "timestamp <= ?"]
        params: list[Any] = ["login_failed", start_time, end_time]
        self._build_tenant_filter(conditions, params, tenant_id=tenant_id)
        where_clause = " AND ".join(conditions)

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # Total failed logins
                cursor.execute(
                    adapt_sql(f"SELECT COUNT(*) as cnt FROM audit_logs WHERE {where_clause}"),
                    tuple(params),
                )
                row = cursor.fetchone()
                total_failed = int(row["cnt"]) if row else 0

                if total_failed < self.failed_login_threshold:
                    return None

                # Group by user with HAVING clause
                cursor.execute(
                    adapt_sql(
                        f"SELECT user_id, COUNT(*) as cnt, "
                        f"MIN(timestamp) as first_seen, MAX(timestamp) as last_seen "
                        f"FROM audit_logs WHERE {where_clause} AND user_id IS NOT NULL "
                        f"GROUP BY user_id HAVING COUNT(*) >= ? "
                        f"ORDER BY cnt DESC"
                    ),
                    tuple(params + [self.failed_login_threshold]),
                )
                user_rows = cursor.fetchall()

                if not user_rows:
                    return None

                affected_users = [int(r["user_id"]) for r in user_rows]
                user_breakdown = {str(int(r["user_id"])): int(r["cnt"]) for r in user_rows}

                # Overall first/last seen across all failed logins
                cursor.execute(
                    adapt_sql(
                        f"SELECT MIN(timestamp) as first_seen, MAX(timestamp) as last_seen "
                        f"FROM audit_logs WHERE {where_clause}"
                    ),
                    tuple(params),
                )
                range_row = cursor.fetchone()

                first_seen = (
                    _parse_timestamp(range_row["first_seen"])
                    if range_row
                    else datetime.now(timezone.utc).replace(tzinfo=None)
                )
                last_seen = _parse_timestamp(range_row["last_seen"]) if range_row else first_seen

                return AnomalyDetection(
                    anomaly_type="excessive_failed_logins",
                    severity="high" if len(affected_users) > 3 else "medium",
                    description=f"{len(affected_users)} user(s) with excessive failed login attempts",
                    affected_users=affected_users,
                    occurrences=total_failed,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    details={
                        "threshold": self.failed_login_threshold,
                        "user_breakdown": user_breakdown,
                    },
                )
        except Exception as e:
            logger.error(f"Failed to detect failed login anomalies: {e}", exc_info=True)
            return None

    def _detect_rapid_activity_anomaly(
        self, start_time: datetime, end_time: datetime, tenant_id: int | None = None
    ) -> list[AnomalyDetection]:
        """Detect rapid activity anomalies using SQL aggregation."""
        conditions = ["timestamp >= ?", "timestamp <= ?", "user_id IS NOT NULL"]
        params: list[Any] = [start_time, end_time]
        self._build_tenant_filter(conditions, params, tenant_id=tenant_id)
        where_clause = " AND ".join(conditions)

        # Database-specific hour bucket expression
        if is_postgresql():
            hour_bucket_expr = "TO_CHAR(timestamp, 'YYYY-MM-DD HH24')"
        else:
            hour_bucket_expr = "strftime('%Y-%m-%d %H', timestamp)"

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql(
                        f"SELECT user_id, {hour_bucket_expr} as hour, COUNT(*) as cnt "
                        f"FROM audit_logs WHERE {where_clause} "
                        f"GROUP BY user_id, {hour_bucket_expr} "
                        f"HAVING COUNT(*) > ? "
                        f"ORDER BY cnt DESC"
                    ),
                    tuple(params + [self.rapid_action_threshold]),
                )
                rows = cursor.fetchall()

            anomalies = []
            for r in rows:
                user_id = int(r["user_id"])
                hour_str = r["hour"]
                count = int(r["cnt"])
                anomalies.append(
                    AnomalyDetection(
                        anomaly_type="rapid_activity",
                        severity="medium",
                        description=f"User {user_id} had {count} actions in one hour",
                        affected_users=[user_id],
                        occurrences=count,
                        first_seen=datetime.strptime(str(hour_str), "%Y-%m-%d %H"),
                        last_seen=datetime.strptime(str(hour_str), "%Y-%m-%d %H")
                        + timedelta(hours=1),
                        details={
                            "hour": str(hour_str),
                            "action_count": count,
                            "threshold": self.rapid_action_threshold,
                        },
                    )
                )
            return anomalies
        except Exception as e:
            logger.error(f"Failed to detect rapid activity anomalies: {e}", exc_info=True)
            return []

    def _detect_off_hours_anomaly(
        self, start_time: datetime, end_time: datetime, tenant_id: int | None = None
    ) -> list[AnomalyDetection]:
        """Detect off-hours activity anomalies using SQL aggregation."""
        # Define off-hours (10 PM - 6 AM)
        OFF_HOURS_START = 22
        OFF_HOURS_END = 6

        # Database-specific hour extraction for WHERE clause
        if is_postgresql():
            hour_check = "(EXTRACT(HOUR FROM timestamp) >= ? OR EXTRACT(HOUR FROM timestamp) < ?)"
        else:
            hour_check = (
                "(CAST(strftime('%H', timestamp) AS integer) >= ? "
                "OR CAST(strftime('%H', timestamp) AS integer) < ?)"
            )

        conditions = [
            "timestamp >= ?",
            "timestamp <= ?",
            "user_id IS NOT NULL",
            hour_check,
        ]
        params: list[Any] = [start_time, end_time, OFF_HOURS_START, OFF_HOURS_END]
        self._build_tenant_filter(conditions, params, tenant_id=tenant_id)
        where_clause = " AND ".join(conditions)

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql(
                        f"SELECT user_id, COUNT(*) as cnt, "
                        f"MIN(timestamp) as first_seen, MAX(timestamp) as last_seen "
                        f"FROM audit_logs WHERE {where_clause} "
                        f"GROUP BY user_id HAVING COUNT(*) > ? "
                        f"ORDER BY cnt DESC"
                    ),
                    tuple(params + [self.off_hours_threshold]),
                )
                rows = cursor.fetchall()

            anomalies = []
            for r in rows:
                user_id = int(r["user_id"])
                count = int(r["cnt"])

                anomalies.append(
                    AnomalyDetection(
                        anomaly_type="off_hours_activity",
                        severity="low",
                        description=f"User {user_id} active during off-hours",
                        affected_users=[user_id],
                        occurrences=count,
                        first_seen=_parse_timestamp(r["first_seen"]),
                        last_seen=_parse_timestamp(r["last_seen"]),
                        details={
                            "activity_count": count,
                        },
                    )
                )
            return anomalies
        except Exception as e:
            logger.error(f"Failed to detect off-hours anomalies: {e}", exc_info=True)
            return []

    def _detect_action_pattern_anomaly(
        self, start_time: datetime, end_time: datetime, tenant_id: int | None = None
    ) -> list[AnomalyDetection]:
        """Detect unusual action patterns using SQL aggregation."""
        anomalies = []

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # Check for role changes
                conditions = [
                    "action = ?",
                    "timestamp >= ?",
                    "timestamp <= ?",
                ]
                params: list[Any] = ["user_role_change", start_time, end_time]
                self._build_tenant_filter(conditions, params, tenant_id=tenant_id)
                where_clause = " AND ".join(conditions)

                cursor.execute(
                    adapt_sql(
                        f"SELECT COUNT(*) as cnt, "
                        f"MIN(timestamp) as first_seen, MAX(timestamp) as last_seen "
                        f"FROM audit_logs WHERE {where_clause}"
                    ),
                    tuple(params),
                )
                row = cursor.fetchone()
                role_count = int(row["cnt"]) if row else 0

                if role_count > self.role_change_threshold:
                    # Get distinct user_ids
                    cursor.execute(
                        adapt_sql(
                            f"SELECT DISTINCT user_id FROM audit_logs "
                            f"WHERE {where_clause} AND user_id IS NOT NULL"
                        ),
                        tuple(params),
                    )
                    affected = [int(r["user_id"]) for r in cursor.fetchall()]

                    anomalies.append(
                        AnomalyDetection(
                            anomaly_type="frequent_role_changes",
                            severity="high",
                            description=f"{role_count} role changes detected",
                            affected_users=affected,
                            occurrences=role_count,
                            first_seen=(
                                _parse_timestamp(row["first_seen"])
                                if row
                                else datetime.now(timezone.utc).replace(tzinfo=None)
                            ),
                            last_seen=(
                                _parse_timestamp(row["last_seen"])
                                if row
                                else datetime.now(timezone.utc).replace(tzinfo=None)
                            ),
                            details={},
                        )
                    )

                # Check for permission changes
                conditions2 = [
                    "action IN (?, ?)",
                    "timestamp >= ?",
                    "timestamp <= ?",
                ]
                params2: list[Any] = ["permission_grant", "permission_revoke", start_time, end_time]
                self._build_tenant_filter(conditions2, params2, tenant_id=tenant_id)
                where_clause2 = " AND ".join(conditions2)

                cursor.execute(
                    adapt_sql(
                        f"SELECT COUNT(*) as cnt, "
                        f"MIN(timestamp) as first_seen, MAX(timestamp) as last_seen "
                        f"FROM audit_logs WHERE {where_clause2}"
                    ),
                    tuple(params2),
                )
                row2 = cursor.fetchone()
                perm_count = int(row2["cnt"]) if row2 else 0

                if perm_count > self.permission_change_threshold:
                    cursor.execute(
                        adapt_sql(
                            f"SELECT DISTINCT user_id FROM audit_logs "
                            f"WHERE {where_clause2} AND user_id IS NOT NULL"
                        ),
                        tuple(params2),
                    )
                    affected2 = [int(r["user_id"]) for r in cursor.fetchall()]

                    anomalies.append(
                        AnomalyDetection(
                            anomaly_type="frequent_permission_changes",
                            severity="medium",
                            description=f"{perm_count} permission changes detected",
                            affected_users=affected2,
                            occurrences=perm_count,
                            first_seen=(
                                _parse_timestamp(row2["first_seen"])
                                if row2
                                else datetime.now(timezone.utc).replace(tzinfo=None)
                            ),
                            last_seen=(
                                _parse_timestamp(row2["last_seen"])
                                if row2
                                else datetime.now(timezone.utc).replace(tzinfo=None)
                            ),
                            details={},
                        )
                    )

            return anomalies
        except Exception as e:
            logger.error(f"Failed to detect action pattern anomalies: {e}", exc_info=True)
            return []

    def get_user_behavior_profile(
        self, user_id: int, days: int = 30, tenant_id: int | None = None
    ) -> dict[str, Any]:
        """
        Get behavior profile for a user.

        Args:
            user_id: User ID.
            days: Number of days to analyze.
            tenant_id: Tenant ID for data isolation. If provided, only analyze
                audit logs belonging to this tenant.

        Returns:
            Dict with user behavior profile.
        """
        start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # Query audit logs for administrative actions
        logs = self.audit_logger.query(
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            tenant_id=tenant_id,
            limit=1000,
        )

        # Query agent_sessions for user work sessions
        sessions_data = []
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # Query agent_sessions for this user
                cursor.execute(
                    adapt_sql("""
                    SELECT session_id, created_at, message_count, total_tokens, tool_name
                    FROM agent_sessions
                    WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                    ORDER BY created_at DESC
                    """),
                    (user_id, start_time.isoformat(), end_time.isoformat()),
                )
                sessions_data = cursor.fetchall()
        except Exception as e:
            logger.warning(
                f"Failed to query agent_sessions: {type(e).__name__}: {e}", exc_info=True
            )

        # Combine audit logs and session data for analysis
        total_actions = len(logs) + len(sessions_data)

        if total_actions == 0:
            return {
                "user_id": user_id,
                "period_days": days,
                "total_actions": 0,
                "actions_per_day": 0.0,
                "action_breakdown": {},
                "hourly_distribution": {},
                "daily_distribution": {},
                "peak_activity_hour": 0,
                "peak_activity_day": "-",
                "first_activity": None,
                "last_activity": None,
                "message": "No activity found for this user",
            }

        # Analyze patterns from audit logs
        action_counts: defaultdict[str, int] = defaultdict(int)
        hourly_activity: defaultdict[int, int] = defaultdict(int)
        daily_activity: defaultdict[int, int] = defaultdict(int)

        for log in logs:
            action_counts[log.action] += 1
            if log.timestamp:
                hourly_activity[log.timestamp.hour] += 1
                daily_activity[log.timestamp.weekday()] += 1

        # Analyze patterns from agent_sessions
        for session in sessions_data:
            # Handle both dict (PostgreSQL RealDictCursor) and tuple (SQLite)
            if isinstance(session, dict):
                created_at = session.get("created_at")
                message_count = session.get("message_count")
                tool_name = session.get("tool_name")
            else:
                # SQLite returns tuple: (session_id, created_at, message_count, total_tokens, tool_name)
                created_at = session[1] if len(session) > 1 else None
                message_count = session[2] if len(session) > 2 else None
                tool_name = session[4] if len(session) > 4 else None

            # Add session action counts
            action_counts["session"] += 1
            if message_count:
                # Convert to int in case database returns string
                try:
                    action_counts["message"] += int(message_count)
                except (ValueError, TypeError):
                    pass
            if tool_name:
                action_counts[f"tool:{tool_name}"] += 1

            # Parse created_at timestamp and convert to local time
            if created_at:
                try:
                    ts = (
                        datetime.fromisoformat(created_at)
                        if isinstance(created_at, str)
                        else created_at
                    )
                    # Convert to local time for accurate hour analysis
                    # Database stores UTC time, need to convert to local
                    if ts.tzinfo is None:
                        # Assume UTC if no timezone info (database timestamp without time zone)
                        ts = ts.replace(tzinfo=timezone.utc)
                    local_ts = ts.astimezone()
                    hourly_activity[local_ts.hour] += 1
                    daily_activity[local_ts.weekday()] += 1
                except Exception:
                    pass

        # Calculate typical session time
        peak_hour = max(hourly_activity.items(), key=lambda x: x[1])[0] if hourly_activity else 0
        peak_day = max(daily_activity.items(), key=lambda x: x[1])[0] if daily_activity else 0

        # Calculate first and last activity times
        all_timestamps = []
        for log in logs:
            if log.timestamp:
                ts = log.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                all_timestamps.append(ts.astimezone())
        for session in sessions_data:
            # Handle both dict and tuple formats
            if isinstance(session, dict):
                created_at = session.get("created_at")
            else:
                created_at = session[1] if len(session) > 1 else None
            if created_at:
                try:
                    ts = (
                        datetime.fromisoformat(created_at)
                        if isinstance(created_at, str)
                        else created_at
                    )
                    # Convert to local time for consistent display
                    if ts.tzinfo is None:
                        # Assume UTC if no timezone info
                        ts = ts.replace(tzinfo=timezone.utc)
                    ts = ts.astimezone()
                    all_timestamps.append(ts)
                except Exception:
                    pass

        return {
            "user_id": user_id,
            "period_days": days,
            "total_actions": total_actions,
            "actions_per_day": total_actions / days,
            "action_breakdown": dict(action_counts),
            "peak_activity_hour": peak_hour,
            "peak_activity_day": [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ][peak_day],
            "hourly_distribution": dict(sorted(hourly_activity.items())),
            "daily_distribution": dict(sorted(daily_activity.items())),
            "first_activity": min(all_timestamps).isoformat() if all_timestamps else None,
            "last_activity": max(all_timestamps).isoformat() if all_timestamps else None,
        }

    def generate_security_score(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: int | None = None,
        precomputed_anomalies: list[AnomalyDetection] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a security score based on audit analysis.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.
            tenant_id: Tenant ID for data isolation. If provided, only analyze
                audit logs belonging to this tenant.
            precomputed_anomalies: Optional pre-computed anomalies to avoid
                re-running anomaly detection (Issue #2750).

        Returns:
            Dict with security score and breakdown.
        """
        if not start_time:
            start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # Use pre-computed anomalies if provided, otherwise compute them
        anomalies = (
            precomputed_anomalies
            if precomputed_anomalies is not None
            else self.detect_anomalies(start_time, end_time, tenant_id=tenant_id)
        )

        # Calculate score (100 = best, 0 = worst)
        score: float = 100

        # Deduct points using risk-weighted frequency-based scoring
        for anomaly in anomalies:
            base = self.BASE_DEDUCTIONS.get(anomaly.severity, 3)
            weight = self.RISK_WEIGHTS.get(anomaly.anomaly_type, 1.0)
            # Frequency factor: log2 scaling, capped at 5x
            freq_factor = min(1 + math.log2(max(anomaly.occurrences, 1)), 5)
            score -= base * weight * freq_factor

        # Ensure score is in range
        score = max(0.0, min(100.0, score))

        # Determine grade
        if score >= 90:
            grade = "A"
        elif score >= 80:
            grade = "B"
        elif score >= 70:
            grade = "C"
        elif score >= 60:
            grade = "D"
        else:
            grade = "F"

        return {
            "score": round(score),
            "grade": grade,
            "anomaly_count": len(anomalies),
            "high_severity_count": len([a for a in anomalies if a.severity == "high"]),
            "medium_severity_count": len([a for a in anomalies if a.severity == "medium"]),
            "low_severity_count": len([a for a in anomalies if a.severity == "low"]),
            "anomalies": [
                {
                    "type": a.anomaly_type,
                    "severity": a.severity,
                    "description": a.description,
                }
                for a in anomalies
            ],
            "recommendations": self._generate_security_recommendations(anomalies),
        }

    def _generate_security_recommendations(self, anomalies: list[AnomalyDetection]) -> list[str]:
        """Generate security recommendations based on anomalies."""
        recommendations = []

        for anomaly in anomalies:
            if anomaly.anomaly_type == "excessive_failed_logins":
                recommendations.append(
                    "Review failed login attempts and consider implementing "
                    "account lockout policies or MFA"
                )
            elif anomaly.anomaly_type == "rapid_activity":
                recommendations.append(
                    "Investigate rapid activity patterns for potential "
                    "automated scripts or compromised accounts"
                )
            elif anomaly.anomaly_type == "off_hours_activity":
                recommendations.append("Review off-hours activity for unauthorized access")
            elif anomaly.anomaly_type == "frequent_role_changes":
                recommendations.append("Implement approval workflow for role changes")
            elif anomaly.anomaly_type == "frequent_permission_changes":
                recommendations.append("Review permission management process")

        if not recommendations:
            recommendations.append("No security issues detected. Continue monitoring.")

        return list(set(recommendations))  # Remove duplicates
