#!/usr/bin/env python3
"""
Open ACE - Audit Analyzer

Analyzes audit logs for compliance and security insights.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.modules.governance.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


@dataclass
class AnomalyDetection:
    """Detected anomaly in audit data."""

    anomaly_type: str
    severity: str  # low, medium, high
    description: str
    affected_users: List[int]
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    details: Dict[str, Any]


class AuditAnalyzer:
    """
    Analyzer for audit logs.

    Features:
    - Pattern detection
    - Anomaly detection
    - User behavior analysis
    - Security insights
    """

    # Thresholds for anomaly detection
    FAILED_LOGIN_THRESHOLD = 5  # Failed logins before alert
    RAPID_ACTION_THRESHOLD = 50  # Actions per hour before alert
    OFF_HOURS_THRESHOLD = 0.1  # Fraction of off-hours activity before alert

    def __init__(self, audit_logger: Optional[AuditLogger] = None):
        """
        Initialize audit analyzer.

        Args:
            audit_logger: Optional AuditLogger instance.
        """
        self.audit_logger = audit_logger or AuditLogger()

    def analyze_patterns(
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Analyze patterns in audit logs.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.

        Returns:
            Dict with pattern analysis results.
        """
        if not start_time:
            start_time = datetime.utcnow() - timedelta(days=30)
        if not end_time:
            end_time = datetime.utcnow()

        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        # Analyze by hour of day
        hourly_activity = defaultdict(int)
        for log in logs:
            if log.timestamp:
                hour = log.timestamp.hour
                hourly_activity[hour] += 1

        # Analyze by day of week
        daily_activity = defaultdict(int)
        for log in logs:
            if log.timestamp:
                day = log.timestamp.weekday()
                daily_activity[day] += 1

        # Analyze by action type
        action_distribution = defaultdict(int)
        for log in logs:
            action_distribution[log.action] += 1

        # Analyze by user
        user_activity = defaultdict(int)
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
            "daily_distribution": dict(sorted(daily_activity.items())),
            "action_distribution": dict(sorted(action_distribution.items(), key=lambda x: -x[1])),
            "unique_users": len(user_activity),
            "top_users": sorted(user_activity.items(), key=lambda x: -x[1])[:10],
        }

    def detect_anomalies(
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> List[AnomalyDetection]:
        """
        Detect anomalies in audit logs.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.

        Returns:
            List[AnomalyDetection]: Detected anomalies.
        """
        if not start_time:
            start_time = datetime.utcnow() - timedelta(days=7)
        if not end_time:
            end_time = datetime.utcnow()

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

        if len(failed_logins) < self.FAILED_LOGIN_THRESHOLD:
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
            if len(logs) >= self.FAILED_LOGIN_THRESHOLD
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
                "threshold": self.FAILED_LOGIN_THRESHOLD,
                "user_breakdown": {str(k): len(v) for k, v in user_failures.items()},
            },
        )

    def _detect_rapid_activity_anomaly(
        self, start_time: datetime, end_time: datetime
    ) -> List[AnomalyDetection]:
        """Detect rapid activity anomalies."""
        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        anomalies = []

        # Group by user and hour
        user_hourly_activity = defaultdict(lambda: defaultdict(int))
        for log in logs:
            if log.user_id and log.timestamp:
                hour_key = log.timestamp.strftime("%Y-%m-%d %H")
                user_hourly_activity[log.user_id][hour_key] += 1

        # Find users with rapid activity
        for user_id, hourly in user_hourly_activity.items():
            for hour, count in hourly.items():
                if count > self.RAPID_ACTION_THRESHOLD:
                    anomalies.append(
                        AnomalyDetection(
                            anomaly_type="rapid_activity",
                            severity="medium",
                            description=f"User {user_id} had {count} actions in one hour",
                            affected_users=[user_id],
                            occurrences=count,
                            first_seen=datetime.strptime(hour, "%Y-%m-%d %H"),
                            last_seen=datetime.strptime(hour, "%Y-%m-%d %H") + timedelta(hours=1),
                            details={
                                "hour": hour,
                                "action_count": count,
                                "threshold": self.RAPID_ACTION_THRESHOLD,
                            },
                        )
                    )

        return anomalies

    def _detect_off_hours_anomaly(
        self, start_time: datetime, end_time: datetime
    ) -> List[AnomalyDetection]:
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
            if len(logs_list) > 10:  # Threshold for off-hours activity
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
    ) -> List[AnomalyDetection]:
        """Detect unusual action patterns."""
        logs = self.audit_logger.query(
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        anomalies = []

        # Check for role changes
        role_changes = [l for l in logs if l.action == "user_role_change"]
        if len(role_changes) > 5:
            anomalies.append(
                AnomalyDetection(
                    anomaly_type="frequent_role_changes",
                    severity="high",
                    description=f"{len(role_changes)} role changes detected",
                    affected_users=list(set(l.user_id for l in role_changes if l.user_id)),
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
        if len(permission_changes) > 10:
            anomalies.append(
                AnomalyDetection(
                    anomaly_type="frequent_permission_changes",
                    severity="medium",
                    description=f"{len(permission_changes)} permission changes detected",
                    affected_users=list(set(l.user_id for l in permission_changes if l.user_id)),
                    occurrences=len(permission_changes),
                    first_seen=min(l.timestamp for l in permission_changes if l.timestamp),
                    last_seen=max(l.timestamp for l in permission_changes if l.timestamp),
                    details={},
                )
            )

        return anomalies

    def get_user_behavior_profile(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """
        Get behavior profile for a user.

        Args:
            user_id: User ID.
            days: Number of days to analyze.

        Returns:
            Dict with user behavior profile.
        """
        start_time = datetime.utcnow() - timedelta(days=days)
        end_time = datetime.utcnow()

        logs = self.audit_logger.query(
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            limit=1000,
        )

        if not logs:
            return {
                "user_id": user_id,
                "period_days": days,
                "total_actions": 0,
                "message": "No activity found for this user",
            }

        # Analyze patterns
        action_counts = defaultdict(int)
        hourly_activity = defaultdict(int)
        daily_activity = defaultdict(int)

        for log in logs:
            action_counts[log.action] += 1
            if log.timestamp:
                hourly_activity[log.timestamp.hour] += 1
                daily_activity[log.timestamp.weekday()] += 1

        # Calculate typical session time
        peak_hour = max(hourly_activity.items(), key=lambda x: x[1])[0] if hourly_activity else 0
        peak_day = max(daily_activity.items(), key=lambda x: x[1])[0] if daily_activity else 0

        return {
            "user_id": user_id,
            "period_days": days,
            "total_actions": len(logs),
            "actions_per_day": len(logs) / days,
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
            "first_activity": min(l.timestamp for l in logs if l.timestamp).isoformat(),
            "last_activity": max(l.timestamp for l in logs if l.timestamp).isoformat(),
        }

    def generate_security_score(
        self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Generate a security score based on audit analysis.

        Args:
            start_time: Start of analysis period.
            end_time: End of analysis period.

        Returns:
            Dict with security score and breakdown.
        """
        if not start_time:
            start_time = datetime.utcnow() - timedelta(days=30)
        if not end_time:
            end_time = datetime.utcnow()

        # Get anomalies
        anomalies = self.detect_anomalies(start_time, end_time)

        # Calculate score (100 = best, 0 = worst)
        score = 100

        # Deduct points for anomalies
        for anomaly in anomalies:
            if anomaly.severity == "high":
                score -= 20
            elif anomaly.severity == "medium":
                score -= 10
            else:
                score -= 5

        # Ensure score is in range
        score = max(0, min(100, score))

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
            "score": score,
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

    def _generate_security_recommendations(self, anomalies: List[AnomalyDetection]) -> List[str]:
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
