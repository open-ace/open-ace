"""
Open ACE - Audit Analyzer

Analyzes audit logs for compliance and security insights.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.modules.governance.audit_logger import AuditLogger
from app.repositories.database import adapt_sql, get_db_connection  # noqa: E402

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
        audit_logger: Optional[AuditLogger] = None,
        settings: Optional[dict[str, Any]] = None,
    ):
        self.audit_logger = audit_logger or AuditLogger()
        settings = settings or {}
        self.failed_login_threshold = settings.get("audit_failed_login_threshold", 5)
        self.rapid_action_threshold = settings.get("audit_rapid_action_threshold", 50)
        self.off_hours_threshold = settings.get("audit_off_hours_threshold", 10)
        self.role_change_threshold = settings.get("audit_role_change_threshold", 5)
        self.permission_change_threshold = settings.get("audit_permission_change_threshold", 10)

    def analyze_patterns(
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> dict[str, Any]:
        """
        Analyze patterns in audit logs.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.

        Returns:
            Dict with pattern analysis results.
        """
        if not start_time:
            start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        # Analyze by hour of day
        hourly_activity: defaultdict[int, int] = defaultdict(int)
        for log in logs:
            if log.timestamp:
                hour = log.timestamp.hour
                hourly_activity[hour] += 1

        # Analyze login by hour of day (for "Login Pattern" chart)
        login_hourly_activity: defaultdict[int, int] = defaultdict(int)
        for log in logs:
            if log.timestamp and log.action == "login":
                hour = log.timestamp.hour
                login_hourly_activity[hour] += 1

        # Analyze by day of week
        daily_activity: defaultdict[int, int] = defaultdict(int)
        for log in logs:
            if log.timestamp:
                day = log.timestamp.weekday()
                daily_activity[day] += 1

        # Analyze by action type
        action_distribution: defaultdict[str, int] = defaultdict(int)
        for log in logs:
            action_distribution[log.action] += 1

        # Analyze by user
        user_activity: defaultdict[int, int] = defaultdict(int)
        for log in logs:
            if log.user_id:
                user_activity[log.user_id] += 1

        return {
            "period": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
            "total_events": len(logs),
            "hourly_distribution": dict(sorted(hourly_activity.items())),
            "login_hourly_distribution": dict(sorted(login_hourly_activity.items())),
            "daily_distribution": dict(sorted(daily_activity.items())),
            "action_distribution": dict(sorted(action_distribution.items(), key=lambda x: -x[1])),
            "unique_users": len(user_activity),
            "top_users": sorted(user_activity.items(), key=lambda x: -x[1])[:10],
        }

    def detect_anomalies(
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> list[AnomalyDetection]:
        """
        Detect anomalies in audit logs.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.

        Returns:
            List[AnomalyDetection]: Detected anomalies.
        """
        if not start_time:
            start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        if not end_time:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        anomalies = []

        # Detect failed login anomalies
        failed_login_anomaly = self._detect_failed_login_anomaly(start_time, end_time)
        if failed_login_anomaly:
            anomalies.append(failed_login_anomaly)

        # Detect rapid activity anomalies
        rapid_activity_anomalies = self._detect_rapid_activity_anomaly(start_time, end_time)
        anomalies.extend(rapid_activity_anomalies)

        # Detect off-hours activity anomalies
        off_hours_anomalies = self._detect_off_hours_anomaly(start_time, end_time)
        anomalies.extend(off_hours_anomalies)

        # Detect unusual action patterns
        action_anomalies = self._detect_action_pattern_anomaly(start_time, end_time)
        anomalies.extend(action_anomalies)

        return anomalies

    def _detect_failed_login_anomaly(
        self, start_time: datetime, end_time: datetime
    ) -> Optional[AnomalyDetection]:
        """Detect failed login anomalies."""
        failed_logins = self.audit_logger.query(
            action="login_failed",
            start_time=start_time,
            end_time=end_time,
            limit=1000,
        )

        if len(failed_logins) < self.failed_login_threshold:
            return None

        # Group by user
        user_failures = defaultdict(list)
        for log in failed_logins:
            if log.user_id:
                user_failures[log.user_id].append(log)

        # Find users with excessive failures
        affected_users = [
            user_id
            for user_id, logs in user_failures.items()
            if len(logs) >= self.failed_login_threshold
        ]

        if not affected_users:
            return None

        return AnomalyDetection(
            anomaly_type="excessive_failed_logins",
            severity="high" if len(affected_users) > 3 else "medium",
            description=f"{len(affected_users)} user(s) with excessive failed login attempts",
            affected_users=affected_users,
            occurrences=len(failed_logins),
            first_seen=min(l.timestamp for l in failed_logins if l.timestamp),
            last_seen=max(l.timestamp for l in failed_logins if l.timestamp),
            details={
                "threshold": self.failed_login_threshold,
                "user_breakdown": {str(k): len(v) for k, v in user_failures.items()},
            },
        )

    def _detect_rapid_activity_anomaly(
        self, start_time: datetime, end_time: datetime
    ) -> list[AnomalyDetection]:
        """Detect rapid activity anomalies."""
        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        anomalies = []

        # Group by user and hour
        user_hourly_activity: defaultdict[int, defaultdict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for log in logs:
            if log.user_id and log.timestamp:
                hour_key = log.timestamp.strftime("%Y-%m-%d %H")
                user_hourly_activity[log.user_id][hour_key] += 1

        # Find users with rapid activity
        for user_id, hourly in user_hourly_activity.items():
            for hour, count in hourly.items():
                if count > self.rapid_action_threshold:
                    anomalies.append(
                        AnomalyDetection(
                            anomaly_type="rapid_activity",
                            severity="medium",
                            description=f"User {user_id} had {count} actions in one hour",
                            affected_users=[user_id],
                            occurrences=count,
                            first_seen=datetime.strptime(str(hour), "%Y-%m-%d %H"),
                            last_seen=datetime.strptime(str(hour), "%Y-%m-%d %H")
                            + timedelta(hours=1),
                            details={
                                "hour": hour,
                                "action_count": count,
                                "threshold": self.rapid_action_threshold,
                            },
                        )
                    )

        return anomalies

    def _detect_off_hours_anomaly(
        self, start_time: datetime, end_time: datetime
    ) -> list[AnomalyDetection]:
        """Detect off-hours activity anomalies."""
        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        # Define off-hours (10 PM - 6 AM)
        OFF_HOURS_START = 22
        OFF_HOURS_END = 6

        off_hours_logs = [
            log
            for log in logs
            if log.timestamp
            and (log.timestamp.hour >= OFF_HOURS_START or log.timestamp.hour < OFF_HOURS_END)
        ]

        if not off_hours_logs:
            return []

        # Group by user
        user_off_hours = defaultdict(list)
        for log in off_hours_logs:
            if log.user_id:
                user_off_hours[log.user_id].append(log)

        anomalies = []
        for user_id, logs_list in user_off_hours.items():
            if len(logs_list) > self.off_hours_threshold:
                anomalies.append(
                    AnomalyDetection(
                        anomaly_type="off_hours_activity",
                        severity="low",
                        description=f"User {user_id} active during off-hours",
                        affected_users=[user_id],
                        occurrences=len(logs_list),
                        first_seen=min(l.timestamp for l in logs_list if l.timestamp),
                        last_seen=max(l.timestamp for l in logs_list if l.timestamp),
                        details={
                            "activity_count": len(logs_list),
                        },
                    )
                )

        return anomalies

    def _detect_action_pattern_anomaly(
        self, start_time: datetime, end_time: datetime
    ) -> list[AnomalyDetection]:
        """Detect unusual action patterns."""
        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        anomalies = []

        # Check for role changes
        role_changes = [l for l in logs if l.action == "user_role_change"]
        if len(role_changes) > self.role_change_threshold:
            anomalies.append(
                AnomalyDetection(
                    anomaly_type="frequent_role_changes",
                    severity="high",
                    description=f"{len(role_changes)} role changes detected",
                    affected_users=list({l.user_id for l in role_changes if l.user_id}),
                    occurrences=len(role_changes),
                    first_seen=min(l.timestamp for l in role_changes if l.timestamp),
                    last_seen=max(l.timestamp for l in role_changes if l.timestamp),
                    details={},
                )
            )

        # Check for permission changes
        permission_changes = [
            l for l in logs if l.action in ("permission_grant", "permission_revoke")
        ]
        if len(permission_changes) > self.permission_change_threshold:
            anomalies.append(
                AnomalyDetection(
                    anomaly_type="frequent_permission_changes",
                    severity="medium",
                    description=f"{len(permission_changes)} permission changes detected",
                    affected_users=list({l.user_id for l in permission_changes if l.user_id}),
                    occurrences=len(permission_changes),
                    first_seen=min(l.timestamp for l in permission_changes if l.timestamp),
                    last_seen=max(l.timestamp for l in permission_changes if l.timestamp),
                    details={},
                )
            )

        return anomalies

    def get_user_behavior_profile(self, user_id: int, days: int = 30) -> dict[str, Any]:
        """
        Get behavior profile for a user.

        Args:
            user_id: User ID.
            days: Number of days to analyze.

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
            limit=1000,
        )

        # Query agent_sessions for user work sessions
        sessions_data = []
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # Query agent_sessions for this user
                cursor.execute(
                    adapt_sql(
                        """
                    SELECT session_id, created_at, message_count, total_tokens, tool_name
                    FROM agent_sessions
                    WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                    ORDER BY created_at DESC
                    """
                    ),
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
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> dict[str, Any]:
        """
        Generate a security score based on audit analysis.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.

        Returns:
            Dict with security score and breakdown.
        """
        if not start_time:
            start_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc).replace(tzinfo=None)

        # Get anomalies
        anomalies = self.detect_anomalies(start_time, end_time)

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
