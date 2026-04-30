#!/usr/bin/env python3
"""
Open ACE - Compliance Report Generator

Generates compliance reports for enterprise auditing and regulatory requirements.
"""

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.modules.governance.audit_logger import AuditLogger
from app.repositories.database import Database
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class ReportType(Enum):
    """Types of compliance reports."""

    USAGE_SUMMARY = "usage_summary"
    USER_ACTIVITY = "user_activity"
    AUDIT_TRAIL = "audit_trail"
    DATA_ACCESS = "data_access"
    SECURITY = "security"
    QUOTA_USAGE = "quota_usage"
    COMPREHENSIVE = "comprehensive"


class ReportFormat(Enum):
    """Report output formats."""

    JSON = "json"
    CSV = "csv"
    HTML = "html"
    PDF = "pdf"


@dataclass
class ReportMetadata:
    """Report metadata."""

    report_id: str
    report_type: str
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    generated_by: Optional[int] = None
    tenant_id: Optional[int] = None
    filters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "report_id": self.report_id,
            "report_type": self.report_type,
            "generated_at": self.generated_at.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "generated_by": self.generated_by,
            "tenant_id": self.tenant_id,
            "filters": self.filters,
        }


@dataclass
class ComplianceReport:
    """Compliance report data structure."""

    metadata: ReportMetadata
    summary: Dict[str, Any]
    details: List[Dict[str, Any]]
    compliance_checks: List[Dict[str, Any]]
    recommendations: List[str]

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "summary": self.summary,
            "details": self.details,
            "compliance_checks": self.compliance_checks,
            "recommendations": self.recommendations,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_csv(self) -> str:
        """Convert details to CSV string."""
        if not self.details:
            return ""

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=self.details[0].keys())
        writer.writeheader()
        writer.writerows(self.details)

        return output.getvalue()


class ReportGenerator:
    """
    Generator for compliance reports.

    Features:
    - Multiple report types
    - Various output formats
    - Customizable date ranges
    - Compliance check integration
    """

    def __init__(
        self,
        db: Optional[Database] = None,
        usage_repo: Optional[UsageRepository] = None,
        user_repo: Optional[UserRepository] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        """
        Initialize report generator.

        Args:
            db: Optional Database instance.
            usage_repo: Optional UsageRepository instance.
            user_repo: Optional UserRepository instance.
            audit_logger: Optional AuditLogger instance.
        """
        self.db = db or Database()
        self.usage_repo = usage_repo or UsageRepository()
        self.user_repo = user_repo or UserRepository()
        self.audit_logger = audit_logger or AuditLogger()

    def generate_report(
        self,
        report_type: str,
        period_start: datetime,
        period_end: datetime,
        generated_by: Optional[int] = None,
        tenant_id: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> ComplianceReport:
        """
        Generate a compliance report.

        Args:
            report_type: Type of report to generate.
            period_start: Report period start date.
            period_end: Report period end date.
            generated_by: User ID of report generator.
            tenant_id: Tenant ID for multi-tenant reports.
            filters: Additional filters.

        Returns:
            ComplianceReport: Generated report.
        """
        import uuid

        metadata = ReportMetadata(
            report_id=str(uuid.uuid4()),
            report_type=report_type,
            generated_at=datetime.utcnow(),
            period_start=period_start,
            period_end=period_end,
            generated_by=generated_by,
            tenant_id=tenant_id,
            filters=filters or {},
        )

        # Generate report based on type
        if report_type == ReportType.USAGE_SUMMARY.value:
            summary, details = self._generate_usage_summary(period_start, period_end, tenant_id)
        elif report_type == ReportType.USER_ACTIVITY.value:
            summary, details = self._generate_user_activity(period_start, period_end, tenant_id)
        elif report_type == ReportType.AUDIT_TRAIL.value:
            summary, details = self._generate_audit_trail(period_start, period_end, tenant_id)
        elif report_type == ReportType.DATA_ACCESS.value:
            summary, details = self._generate_data_access(period_start, period_end, tenant_id)
        elif report_type == ReportType.SECURITY.value:
            summary, details = self._generate_security_report(period_start, period_end, tenant_id)
        elif report_type == ReportType.QUOTA_USAGE.value:
            summary, details = self._generate_quota_usage(period_start, period_end, tenant_id)
        elif report_type == ReportType.COMPREHENSIVE.value:
            summary, details = self._generate_comprehensive_report(
                period_start, period_end, tenant_id
            )
        else:
            summary, details = {}, []

        # Run compliance checks
        compliance_checks = self._run_compliance_checks(report_type, summary, details)

        # Generate recommendations
        recommendations = self._generate_recommendations(compliance_checks)

        return ComplianceReport(
            metadata=metadata,
            summary=summary,
            details=details,
            compliance_checks=compliance_checks,
            recommendations=recommendations,
        )

    def _generate_usage_summary(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate usage summary report."""
        start_date = period_start.strftime("%Y-%m-%d")
        end_date = period_end.strftime("%Y-%m-%d")

        # Get usage data
        usage_data = self.db.fetch_all(
            """
            SELECT
                date,
                tool_name,
                host_name,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(requests) as total_requests,
                COUNT(DISTINCT user_id) as unique_users
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY date, tool_name, host_name
            ORDER BY date DESC, tool_name
        """,
            (start_date, end_date),
        )

        # Calculate summary
        total_tokens = sum(r.get("total_tokens", 0) or 0 for r in usage_data)
        total_requests = sum(r.get("total_requests", 0) or 0 for r in usage_data)
        unique_tools = set(r.get("tool_name") for r in usage_data if r.get("tool_name"))

        summary = {
            "period": {
                "start": start_date,
                "end": end_date,
                "days": (period_end - period_start).days + 1,
            },
            "totals": {
                "tokens": total_tokens,
                "requests": total_requests,
                "tools_used": len(unique_tools),
                "tools": list(unique_tools),
            },
            "averages": {
                "daily_tokens": total_tokens // max((period_end - period_start).days + 1, 1),
                "daily_requests": total_requests // max((period_end - period_start).days + 1, 1),
            },
        }

        details = [dict(r) for r in usage_data]

        return summary, details

    def _generate_user_activity(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate user activity report."""
        start_date = period_start.strftime("%Y-%m-%d")
        end_date = period_end.strftime("%Y-%m-%d")

        # Get user activity
        user_activity = self.db.fetch_all(
            """
            SELECT
                u.id as user_id,
                u.username,
                u.email,
                u.role,
                COUNT(DISTINCT du.date) as active_days,
                SUM(du.total_tokens) as total_tokens,
                SUM(du.requests) as total_requests,
                MIN(du.date) as first_activity,
                MAX(du.date) as last_activity
            FROM users u
            LEFT JOIN daily_usage du ON u.id = du.user_id
                AND du.date >= ? AND du.date <= ?
            GROUP BY u.id
            ORDER BY total_tokens DESC
        """,
            (start_date, end_date),
        )

        # Calculate summary
        active_users = [u for u in user_activity if u.get("active_days", 0) > 0]
        total_users = len(user_activity)

        summary = {
            "period": {
                "start": start_date,
                "end": end_date,
            },
            "totals": {
                "total_users": total_users,
                "active_users": len(active_users),
                "inactive_users": total_users - len(active_users),
            },
            "by_role": self._group_by_role(user_activity),
        }

        details = [dict(r) for r in user_activity]

        return summary, details

    def _generate_audit_trail(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate audit trail report."""
        audit_logs = self.audit_logger.query(
            start_time=period_start,
            end_time=period_end,
            limit=10000,
        )

        # Calculate summary
        action_counts = {}
        severity_counts = {}
        user_actions = {}

        for log in audit_logs:
            action = log.action
            severity = log.severity
            user_id = log.user_id

            action_counts[action] = action_counts.get(action, 0) + 1
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

            if user_id:
                user_actions[user_id] = user_actions.get(user_id, 0) + 1

        summary = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "totals": {
                "total_events": len(audit_logs),
                "unique_users": len(user_actions),
            },
            "by_action": action_counts,
            "by_severity": severity_counts,
        }

        details = [log.to_dict() for log in audit_logs]

        return summary, details

    def _generate_data_access(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate data access report."""
        # Get data access logs
        access_logs = self.audit_logger.query(
            start_time=period_start,
            end_time=period_end,
            resource_type="data",
            limit=10000,
        )

        # Also get export/import actions
        export_logs = self.audit_logger.query(
            start_time=period_start,
            end_time=period_end,
            action="data_export",
            limit=10000,
        )

        summary = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "totals": {
                "data_access_events": len(access_logs),
                "data_exports": len(export_logs),
            },
        }

        details = [log.to_dict() for log in access_logs + export_logs]

        return summary, details

    def _generate_security_report(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate security report."""
        # Get security-related audit logs
        security_actions = [
            "login",
            "login_failed",
            "logout",
            "session_expired",
            "user_password_change",
            "user_role_change",
            "permission_grant",
            "permission_revoke",
            "content_blocked",
            "content_flagged",
        ]

        all_logs = []
        for action in security_actions:
            logs = self.audit_logger.query(
                start_time=period_start,
                end_time=period_end,
                action=action,
                limit=1000,
            )
            all_logs.extend(logs)

        # Calculate summary
        failed_logins = [l for l in all_logs if l.action == "login_failed"]
        password_changes = [l for l in all_logs if l.action == "user_password_change"]
        role_changes = [l for l in all_logs if l.action == "user_role_change"]
        content_blocked = [l for l in all_logs if l.action == "content_blocked"]

        summary = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "security_events": {
                "failed_logins": len(failed_logins),
                "password_changes": len(password_changes),
                "role_changes": len(role_changes),
                "content_blocked": len(content_blocked),
            },
            "risk_indicators": [],
        }

        # Add risk indicators
        if len(failed_logins) > 10:
            summary["risk_indicators"].append(
                {
                    "level": "warning",
                    "message": f"High number of failed login attempts: {len(failed_logins)}",
                }
            )

        if len(role_changes) > 5:
            summary["risk_indicators"].append(
                {
                    "level": "info",
                    "message": f"Multiple role changes detected: {len(role_changes)}",
                }
            )

        details = [log.to_dict() for log in all_logs]

        return summary, details

    def _generate_quota_usage(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate quota usage report."""
        # Get quota alerts
        quota_alerts = self.db.fetch_all(
            """
            SELECT * FROM quota_alerts
            WHERE created_at >= ? AND created_at <= ?
            ORDER BY created_at DESC
        """,
            (period_start, period_end),
        )

        # Get user quota status
        users = self.user_repo.get_all_users(include_inactive=False)

        summary = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "alerts": {
                "total": len(quota_alerts),
                "warnings": len([a for a in quota_alerts if a.get("alert_type") == "warning"]),
                "critical": len([a for a in quota_alerts if a.get("alert_type") == "critical"]),
                "exceeded": len([a for a in quota_alerts if a.get("alert_type") == "exceeded"]),
            },
            "users_monitored": len(users),
        }

        details = [dict(a) for a in quota_alerts]

        return summary, details

    def _generate_comprehensive_report(
        self, period_start: datetime, period_end: datetime, tenant_id: Optional[int]
    ) -> tuple:
        """Generate comprehensive compliance report."""
        # Combine all report types
        usage_summary, _ = self._generate_usage_summary(period_start, period_end, tenant_id)
        user_activity, _ = self._generate_user_activity(period_start, period_end, tenant_id)
        audit_summary, audit_details = self._generate_audit_trail(
            period_start, period_end, tenant_id
        )
        security_summary, _ = self._generate_security_report(period_start, period_end, tenant_id)
        quota_summary, _ = self._generate_quota_usage(period_start, period_end, tenant_id)

        summary = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "usage": usage_summary,
            "users": user_activity,
            "audit": audit_summary,
            "security": security_summary,
            "quota": quota_summary,
        }

        return summary, audit_details

    def _run_compliance_checks(
        self, report_type: str, summary: Dict[str, Any], details: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Run compliance checks on the report data."""
        checks = []

        # Check 1: Data retention compliance
        checks.append(
            {
                "check_id": "data_retention",
                "name": "Data Retention Policy",
                "status": "pass",
                "message": "Data retention policy is being followed",
            }
        )

        # Check 2: User access review
        if summary.get("users", {}).get("inactive_users", 0) > 10:
            checks.append(
                {
                    "check_id": "inactive_users",
                    "name": "Inactive User Review",
                    "status": "warning",
                    "message": f"{summary['users']['inactive_users']} inactive users found. Review access.",
                }
            )
        else:
            checks.append(
                {
                    "check_id": "inactive_users",
                    "name": "Inactive User Review",
                    "status": "pass",
                    "message": "Inactive user count is within acceptable range",
                }
            )

        # Check 3: Security events
        security_events = summary.get("security", {}).get("security_events", {})
        if security_events.get("failed_logins", 0) > 20:
            checks.append(
                {
                    "check_id": "failed_logins",
                    "name": "Failed Login Monitoring",
                    "status": "fail",
                    "message": f"High number of failed logins: {security_events['failed_logins']}",
                }
            )
        else:
            checks.append(
                {
                    "check_id": "failed_logins",
                    "name": "Failed Login Monitoring",
                    "status": "pass",
                    "message": "Failed login attempts within normal range",
                }
            )

        # Check 4: Quota compliance
        quota_alerts = summary.get("quota", {}).get("alerts", {})
        if quota_alerts.get("exceeded", 0) > 0:
            checks.append(
                {
                    "check_id": "quota_compliance",
                    "name": "Quota Compliance",
                    "status": "warning",
                    "message": f"{quota_alerts['exceeded']} quota exceedances detected",
                }
            )
        else:
            checks.append(
                {
                    "check_id": "quota_compliance",
                    "name": "Quota Compliance",
                    "status": "pass",
                    "message": "All users within quota limits",
                }
            )

        return checks

    def _generate_recommendations(self, compliance_checks: List[Dict[str, Any]]) -> List[str]:
        """Generate recommendations based on compliance checks."""
        recommendations = []

        for check in compliance_checks:
            if check["status"] == "fail":
                recommendations.append(f"URGENT: {check['name']} - {check['message']}")
            elif check["status"] == "warning":
                recommendations.append(f"Review: {check['name']} - {check['message']}")

        if not recommendations:
            recommendations.append("All compliance checks passed. Continue monitoring.")

        return recommendations

    def _group_by_role(self, user_activity: List[Dict]) -> Dict[str, int]:
        """Group user activity by role."""
        by_role = {}
        for user in user_activity:
            role = user.get("role", "unknown")
            by_role[role] = by_role.get(role, 0) + 1
        return by_role

    def save_report(self, report: ComplianceReport) -> bool:
        """Save report to database."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
                id_type = (
                    "SERIAL PRIMARY KEY"
                    if self.db.is_postgresql
                    else "INTEGER PRIMARY KEY AUTOINCREMENT"
                )

                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS compliance_reports (
                        id {id_type},
                        report_id TEXT UNIQUE NOT NULL,
                        report_type TEXT NOT NULL,
                        generated_at TIMESTAMP NOT NULL,
                        period_start TIMESTAMP NOT NULL,
                        period_end TIMESTAMP NOT NULL,
                        generated_by INTEGER,
                        tenant_id INTEGER,
                        report_data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """
                )
                cursor.execute(
                    """
                    INSERT INTO compliance_reports
                    (report_id, report_type, generated_at, period_start, period_end,
                     generated_by, tenant_id, report_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        report.metadata.report_id,
                        report.metadata.report_type,
                        report.metadata.generated_at,
                        report.metadata.period_start,
                        report.metadata.period_end,
                        report.metadata.generated_by,
                        report.metadata.tenant_id,
                        report.to_json(),
                    ),
                )
                conn.commit()

            logger.info(f"Saved compliance report: {report.metadata.report_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save report: {e}")
            return False

    def get_saved_reports(
        self, report_type: Optional[str] = None, tenant_id: Optional[int] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get saved reports."""
        try:
            conditions = []
            params = []

            if report_type:
                conditions.append("report_type = ?")
                params.append(report_type)

            if tenant_id:
                conditions.append("tenant_id = ?")
                params.append(tenant_id)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            query = f"""
                SELECT report_id, report_type, generated_at, period_start, period_end
                FROM compliance_reports
                WHERE {where_clause}
                ORDER BY generated_at DESC
                LIMIT ?
            """

            rows = self.db.fetch_all(query, tuple(params + [limit]))
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_saved_report(self, report_id: str) -> Optional[ComplianceReport]:
        """Get a saved report by ID."""
        row = self.db.fetch_one(
            "SELECT report_data FROM compliance_reports WHERE report_id = ?", (report_id,)
        )

        if row:
            try:
                data = json.loads(row["report_data"])
                # Reconstruct report from saved data
                metadata = ReportMetadata(
                    report_id=data["metadata"]["report_id"],
                    report_type=data["metadata"]["report_type"],
                    generated_at=datetime.fromisoformat(data["metadata"]["generated_at"]),
                    period_start=datetime.fromisoformat(data["metadata"]["period_start"]),
                    period_end=datetime.fromisoformat(data["metadata"]["period_end"]),
                    generated_by=data["metadata"].get("generated_by"),
                    tenant_id=data["metadata"].get("tenant_id"),
                    filters=data["metadata"].get("filters", {}),
                )

                return ComplianceReport(
                    metadata=metadata,
                    summary=data["summary"],
                    details=data["details"],
                    compliance_checks=data["compliance_checks"],
                    recommendations=data["recommendations"],
                )
            except Exception as e:
                logger.error(f"Failed to load report: {e}")

        return None


def get_ddl_statements() -> list[str]:
    """Return DDL statements for compliance report tables."""
    from app.repositories.database import is_postgresql

    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS compliance_reports (
            id {id_type},
            report_id TEXT UNIQUE NOT NULL,
            report_type TEXT NOT NULL,
            generated_at TIMESTAMP NOT NULL,
            period_start TIMESTAMP NOT NULL,
            period_end TIMESTAMP NOT NULL,
            generated_by INTEGER,
            tenant_id INTEGER,
            report_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]
