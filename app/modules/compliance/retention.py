#!/usr/bin/env python3
"""
Open ACE - Data Retention Manager

Manages data retention policies and cleanup for compliance.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from app.repositories.database import Database

logger = logging.getLogger(__name__)


class RetentionPolicy(Enum):
    """Data retention policies."""

    SHORT = "short"  # 30 days
    MEDIUM = "medium"  # 90 days
    LONG = "long"  # 365 days
    CUSTOM = "custom"  # Custom days


@dataclass
class RetentionRule:
    """Data retention rule."""

    data_type: str
    retention_days: int
    action: str = "delete"  # delete, archive, anonymize
    enabled: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "data_type": self.data_type,
            "retention_days": self.retention_days,
            "action": self.action,
            "enabled": self.enabled,
        }


@dataclass
class RetentionReport:
    """Report of retention cleanup."""

    timestamp: datetime
    rules_applied: List[Dict[str, Any]]
    records_deleted: int
    records_archived: int
    records_anonymized: int
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "rules_applied": self.rules_applied,
            "records_deleted": self.records_deleted,
            "records_archived": self.records_archived,
            "records_anonymized": self.records_anonymized,
            "errors": self.errors,
        }


class DataRetentionManager:
    """
    Manager for data retention policies.

    Features:
    - Configurable retention rules per data type
    - Automated cleanup
    - Archive support
    - Anonymization support
    - Compliance reporting
    """

    # Default retention rules
    DEFAULT_RULES = {
        "audit_logs": RetentionRule("audit_logs", 90, "delete"),
        "quota_alerts": RetentionRule("quota_alerts", 30, "delete"),
        "sessions": RetentionRule("sessions", 7, "delete"),
        "sso_sessions": RetentionRule("sso_sessions", 1, "delete"),
        "usage_data": RetentionRule("usage_data", 365, "archive"),
        "messages": RetentionRule("messages", 90, "anonymize"),
        "user_activity": RetentionRule("user_activity", 365, "archive"),
    }

    def __init__(
        self, db: Optional[Database] = None, custom_rules: Optional[Dict[str, RetentionRule]] = None
    ):
        """
        Initialize retention manager.

        Args:
            db: Optional Database instance.
            custom_rules: Custom retention rules to override defaults.
        """
        self.db = db or Database()
        self.rules = self.DEFAULT_RULES.copy()
        if custom_rules:
            self.rules.update(custom_rules)

    def _ensure_tables(self) -> None:
        """Ensure retention-related tables exist."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Retention history table
            # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
            if self.db.is_postgresql:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS retention_history (
                        id SERIAL PRIMARY KEY,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        report_data TEXT NOT NULL
                    )
                """
                )
            else:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS retention_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        report_data TEXT NOT NULL
                    )
                """
                )

            conn.commit()

    def set_rule(self, data_type: str, retention_days: int, action: str = "delete") -> None:
        """
        Set a retention rule.

        Args:
            data_type: Type of data.
            retention_days: Number of days to retain.
            action: Action to take (delete, archive, anonymize).
        """
        self.rules[data_type] = RetentionRule(
            data_type=data_type,
            retention_days=retention_days,
            action=action,
        )
        logger.info(f"Set retention rule: {data_type} -> {retention_days} days ({action})")

    def get_rule(self, data_type: str) -> Optional[RetentionRule]:
        """
        Get retention rule for a data type.

        Args:
            data_type: Type of data.

        Returns:
            Optional[RetentionRule]: Retention rule or None.
        """
        return self.rules.get(data_type)

    def get_all_rules(self) -> Dict[str, RetentionRule]:
        """
        Get all retention rules.

        Returns:
            Dict[str, RetentionRule]: All retention rules.
        """
        return self.rules.copy()

    def run_cleanup(self, dry_run: bool = False) -> RetentionReport:
        """
        Run data retention cleanup.

        Args:
            dry_run: If True, don't actually delete anything.

        Returns:
            RetentionReport: Report of cleanup actions.
        """
        report = RetentionReport(
            timestamp=datetime.utcnow(),
            rules_applied=[],
            records_deleted=0,
            records_archived=0,
            records_anonymized=0,
        )

        for data_type, rule in self.rules.items():
            if not rule.enabled:
                continue

            cutoff = datetime.utcnow() - timedelta(days=rule.retention_days)

            try:
                if rule.action == "delete":
                    deleted = self._delete_old_data(data_type, cutoff, dry_run)
                    report.records_deleted += deleted
                    report.rules_applied.append(
                        {
                            "data_type": data_type,
                            "action": "delete",
                            "cutoff": cutoff.isoformat(),
                            "records_affected": deleted,
                        }
                    )

                elif rule.action == "archive":
                    archived = self._archive_old_data(data_type, cutoff, dry_run)
                    report.records_archived += archived
                    report.rules_applied.append(
                        {
                            "data_type": data_type,
                            "action": "archive",
                            "cutoff": cutoff.isoformat(),
                            "records_affected": archived,
                        }
                    )

                elif rule.action == "anonymize":
                    anonymized = self._anonymize_old_data(data_type, cutoff, dry_run)
                    report.records_anonymized += anonymized
                    report.rules_applied.append(
                        {
                            "data_type": data_type,
                            "action": "anonymize",
                            "cutoff": cutoff.isoformat(),
                            "records_affected": anonymized,
                        }
                    )

            except Exception as e:
                error_msg = f"Failed to process {data_type}: {str(e)}"
                logger.error(error_msg)
                report.errors.append(error_msg)

        # Save report
        if not dry_run:
            self._save_report(report)

        return report

    # Time column name per table
    TABLE_TIME_COLUMNS = {
        "audit_logs": "timestamp",
        "quota_alerts": "created_at",
        "sessions": "created_at",
        "sso_sessions": "created_at",
        "daily_usage": "date",
        "daily_messages": "created_at",
    }

    def _get_time_column(self, table_name: str) -> Optional[str]:
        """Get the appropriate time column for a table."""
        return self.TABLE_TIME_COLUMNS.get(table_name)

    def _delete_old_data(self, data_type: str, cutoff: datetime, dry_run: bool) -> int:
        """Delete old data from a table."""
        table_mapping = {
            "audit_logs": "audit_logs",
            "quota_alerts": "quota_alerts",
            "sessions": "sessions",
            "sso_sessions": "sso_sessions",
            "usage_data": "daily_usage",
            "messages": "daily_messages",
        }

        table_name = table_mapping.get(data_type)
        if not table_name:
            logger.warning(f"Unknown data type for deletion: {data_type}")
            return 0

        time_col = self._get_time_column(table_name)
        if not time_col:
            logger.warning(f"No time column configured for table {table_name}")
            return 0

        # Count records to delete
        count_query = f"SELECT COUNT(*) as count FROM {table_name} WHERE {time_col} < ?"
        result = self.db.fetch_one(count_query, (cutoff,))
        count = result["count"] if result else 0

        if dry_run:
            logger.info(f"[DRY RUN] Would delete {count} records from {table_name}")
            return count

        # Delete records
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"DELETE FROM {table_name} WHERE {time_col} < ?", (cutoff,)
            )
            conn.commit()
            deleted = cursor.rowcount

        logger.info(f"Deleted {deleted} records from {table_name}")
        return deleted

    def _archive_old_data(self, data_type: str, cutoff: datetime, dry_run: bool) -> int:
        """Archive old data (export and delete)."""
        # For now, just delete - in production, would export to archive storage
        return self._delete_old_data(data_type, cutoff, dry_run)

    def _anonymize_old_data(self, data_type: str, cutoff: datetime, dry_run: bool) -> int:
        """Anonymize old data."""
        table_mapping = {
            "messages": "daily_messages",
            "user_activity": "audit_logs",
        }

        table_name = table_mapping.get(data_type)
        if not table_name:
            logger.warning(f"Unknown data type for anonymization: {data_type}")
            return 0

        time_col = self._get_time_column(table_name)
        if not time_col:
            logger.warning(f"No time column configured for table {table_name}")
            return 0

        # Count records to anonymize
        count_query = f"SELECT COUNT(*) as count FROM {table_name} WHERE {time_col} < ?"
        result = self.db.fetch_one(count_query, (cutoff,))
        count = result["count"] if result else 0

        if dry_run:
            logger.info(f"[DRY RUN] Would anonymize {count} records in {table_name}")
            return count

        # Anonymize by removing PII fields
        if table_name == "daily_messages":
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    UPDATE daily_messages
                    SET sender = 'ANONYMIZED',
                        recipient = 'ANONYMIZED'
                    WHERE {time_col} < ?
                """,
                    (cutoff,),
                )
                conn.commit()
                anonymized = cursor.rowcount

        elif table_name == "audit_logs":
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    UPDATE audit_logs
                    SET username = 'ANONYMIZED',
                        ip_address = NULL,
                        user_agent = NULL
                    WHERE {time_col} < ?
                """,
                    (cutoff,),
                )
                conn.commit()
                anonymized = cursor.rowcount

        else:
            anonymized = 0

        logger.info(f"Anonymized {anonymized} records in {table_name}")
        return anonymized

    def _save_report(self, report: RetentionReport) -> None:
        """Save retention report to database."""
        import json

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO retention_history (timestamp, report_data)
                    VALUES (?, ?)
                """,
                    (report.timestamp, json.dumps(report.to_dict())),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Failed to save retention report: {e}")

    def get_retention_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Get retention cleanup history.

        Args:
            limit: Maximum number of records.

        Returns:
            List[Dict]: Retention history records.
        """
        import json

        rows = self.db.fetch_all(
            """
            SELECT id, timestamp, report_data
            FROM retention_history
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (limit,),
        )

        history = []
        for row in rows:
            try:
                report_data = json.loads(row["report_data"])
                history.append(
                    {
                        "id": row["id"],
                        "timestamp": row["timestamp"],
                        "summary": {
                            "records_deleted": report_data.get("records_deleted", 0),
                            "records_archived": report_data.get("records_archived", 0),
                            "records_anonymized": report_data.get("records_anonymized", 0),
                            "errors_count": len(report_data.get("errors", [])),
                        },
                    }
                )
            except (json.JSONDecodeError, KeyError):
                pass

        return history

    def estimate_storage(self) -> Dict[str, Any]:
        """
        Estimate storage usage by data type.

        Returns:
            Dict with storage estimates.
        """
        estimates = {}

        tables = {
            "audit_logs": "audit_logs",
            "quota_alerts": "quota_alerts",
            "sessions": "sessions",
            "daily_usage": "daily_usage",
            "daily_messages": "daily_messages",
            "users": "users",
        }

        for name, table in tables.items():
            try:
                result = self.db.fetch_one(f"SELECT COUNT(*) as count FROM {table}")
                estimates[name] = {
                    "record_count": result["count"] if result else 0,
                }
            except Exception:
                estimates[name] = {"record_count": 0, "error": "Table not found"}

        return {
            "estimates": estimates,
            "timestamp": datetime.utcnow().isoformat(),
        }


def get_ddl_statements() -> list[str]:
    """Return DDL statements for retention tables."""
    from app.repositories.database import is_postgresql
    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS retention_history (
            id {id_type},
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            report_data TEXT NOT NULL
        )
        """,
    ]

    def get_compliance_status(self) -> Dict[str, Any]:
        """
        Get data retention compliance status.

        Returns:
            Dict with compliance status.
        """
        # Check if cleanup has been run recently
        last_cleanup = self.db.fetch_one(
            """
            SELECT timestamp FROM retention_history
            ORDER BY timestamp DESC LIMIT 1
        """
        )

        last_cleanup_time = None
        days_since_cleanup = None

        if last_cleanup:
            last_cleanup_time = last_cleanup["timestamp"]
            if isinstance(last_cleanup_time, str):
                last_cleanup_time = datetime.fromisoformat(last_cleanup_time)
            days_since_cleanup = (datetime.utcnow() - last_cleanup_time).days

        # Determine compliance status
        is_compliant = True
        issues = []

        if not last_cleanup_time:
            is_compliant = False
            issues.append("No retention cleanup has been run")
        elif days_since_cleanup and days_since_cleanup > 7:
            is_compliant = False
            issues.append(f"Last cleanup was {days_since_cleanup} days ago (recommended: weekly)")

        # Check rule configuration
        for data_type, rule in self.rules.items():
            if not rule.enabled:
                issues.append(f"Retention rule for {data_type} is disabled")

        return {
            "is_compliant": is_compliant,
            "last_cleanup": last_cleanup_time.isoformat() if last_cleanup_time else None,
            "days_since_cleanup": days_since_cleanup,
            "rules_configured": len(self.rules),
            "rules_enabled": len([r for r in self.rules.values() if r.enabled]),
            "issues": issues,
            "recommendations": [
                "Run retention cleanup at least weekly",
                "Review retention policies quarterly",
                "Archive important data before deletion",
            ],
        }
