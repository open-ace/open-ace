#!/usr/bin/env python3
"""
Open ACE - Database Optimization

Provides database indexing and optimization utilities.
"""

import logging
from typing import Any, Optional

from app.repositories.database import Database

logger = logging.getLogger(__name__)


# Recommended indexes for performance
# Optimized indexes (migration 014): removed redundant single-column indexes
RECOMMENDED_INDEXES = {
    "daily_usage": [
        ("idx_usage_date", ["date"]),
        ("idx_usage_tool", ["tool_name"]),
        ("idx_usage_host", ["host_name"]),
        ("idx_usage_date_tool", ["date", "tool_name"]),
        ("idx_usage_date_tool_host", ["date", "tool_name", "host_name"]),
    ],
    "daily_messages": [
        # Essential composite indexes for common queries
        ("idx_messages_date_tool_host", ["date", "tool_name", "host_name"]),
        ("idx_messages_date_role_timestamp", ["date", "role", "timestamp"]),
        # Single-column indexes for specific queries
        ("idx_messages_sender_id", ["sender_id"]),
        ("idx_messages_timestamp", ["timestamp"]),
        # New composite indexes for better coverage
        ("idx_messages_conversation", ["date", "conversation_id", "agent_session_id"]),
        ("idx_messages_date_sender_id", ["date", "sender_id"]),
    ],
    "users": [
        ("idx_users_username", ["username"]),
        ("idx_users_email", ["email"]),
        ("idx_users_role", ["role"]),
        ("idx_users_active", ["is_active"]),
    ],
    "sessions": [
        ("idx_sessions_token", ["token"]),
        ("idx_sessions_user", ["user_id"]),
        ("idx_sessions_expires", ["expires_at"]),
        ("idx_sessions_active", ["is_active", "expires_at"]),
    ],
    "audit_logs": [
        ("idx_audit_timestamp", ["timestamp"]),
        ("idx_audit_user_id", ["user_id"]),
        ("idx_audit_action", ["action"]),
        ("idx_audit_resource", ["resource_type", "resource_id"]),
        ("idx_audit_severity", ["severity"]),
    ],
    "quota_usage": [
        ("idx_quota_user", ["user_id"]),
        ("idx_quota_date", ["date"]),
        ("idx_quota_user_date", ["user_id", "date"]),
    ],
    "quota_alerts": [
        ("idx_quota_alerts_user", ["user_id"]),
        ("idx_quota_alerts_created", ["created_at"]),
        ("idx_quota_alerts_unack", ["acknowledged", "created_at"]),
    ],
    "tenants": [
        ("idx_tenants_slug", ["slug"]),
        ("idx_tenants_status", ["status"]),
    ],
    "tenant_usage": [
        ("idx_tenant_usage_tenant", ["tenant_id"]),
        ("idx_tenant_usage_date", ["date"]),
        ("idx_tenant_usage_tenant_date", ["tenant_id", "date"]),
    ],
    "content_filter_rules": [
        ("idx_filter_rules_type", ["type"]),
        ("idx_filter_rules_enabled", ["is_enabled"]),
    ],
}


class DatabaseOptimizer:
    """
    Database optimization utilities.

    Features:
    - Index management
    - Query analysis
    - Performance recommendations
    """

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize database optimizer.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    def create_indexes(self, tables: Optional[list[str]] = None) -> dict[str, Any]:
        """
        Create recommended indexes.

        Args:
            tables: Specific tables to index, or all if None.

        Returns:
            Dict with results.
        """
        results = {
            "created": [],
            "skipped": [],
            "errors": [],
        }

        tables_to_index = tables or list(RECOMMENDED_INDEXES.keys())

        with self.db.connection() as conn:
            cursor = conn.cursor()

            for table in tables_to_index:
                if table not in RECOMMENDED_INDEXES:
                    results["skipped"].append(
                        {
                            "table": table,
                            "reason": "No recommended indexes",
                        }
                    )
                    continue

                for index_name, columns in RECOMMENDED_INDEXES[table]:
                    try:
                        columns_str = ", ".join(columns)
                        sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns_str})"
                        cursor.execute(sql)
                        results["created"].append(
                            {
                                "table": table,
                                "index": index_name,
                                "columns": columns,
                            }
                        )
                    except Exception as e:
                        results["errors"].append(
                            {
                                "table": table,
                                "index": index_name,
                                "error": str(e),
                            }
                        )

            conn.commit()

        logger.info(f"Created {len(results['created'])} indexes, {len(results['errors'])} errors")
        return results

    def drop_indexes(self, tables: Optional[list[str]] = None) -> dict[str, Any]:
        """
        Drop all custom indexes.

        Args:
            tables: Specific tables, or all if None.

        Returns:
            Dict with results.
        """
        results = {
            "dropped": [],
            "errors": [],
        }

        tables_to_process = tables or list(RECOMMENDED_INDEXES.keys())

        with self.db.connection() as conn:
            cursor = conn.cursor()

            for table in tables_to_process:
                if table not in RECOMMENDED_INDEXES:
                    continue

                for index_name, _ in RECOMMENDED_INDEXES[table]:
                    try:
                        cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
                        results["dropped"].append(index_name)
                    except Exception as e:
                        results["errors"].append(
                            {
                                "index": index_name,
                                "error": str(e),
                            }
                        )

            conn.commit()

        return results

    def analyze_table(self, table_name: str) -> dict[str, Any]:
        """
        Analyze a table for optimization opportunities.

        Args:
            table_name: Table to analyze.

        Returns:
            Dict with analysis results.
        """
        analysis = {
            "table": table_name,
            "row_count": 0,
            "indexes": [],
            "recommendations": [],
        }

        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Get row count
            try:
                cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
                result = cursor.fetchone()
                analysis["row_count"] = result[0] if result else 0
            except Exception:
                analysis["row_count"] = "error"

            # Get existing indexes
            try:
                cursor.execute(f"PRAGMA index_list({table_name})")
                indexes = cursor.fetchall()

                for idx in indexes:
                    idx_name = idx[1]
                    cursor.execute(f"PRAGMA index_info({idx_name})")
                    columns = [col[2] for col in cursor.fetchall()]
                    analysis["indexes"].append(
                        {
                            "name": idx_name,
                            "columns": columns,
                            "unique": bool(idx[2]),
                        }
                    )
            except Exception:
                pass

        # Generate recommendations
        if table_name in RECOMMENDED_INDEXES:
            existing_names = {idx["name"] for idx in analysis["indexes"]}
            for idx_name, columns in RECOMMENDED_INDEXES[table_name]:
                if idx_name not in existing_names:
                    analysis["recommendations"].append(
                        {
                            "type": "create_index",
                            "index": idx_name,
                            "columns": columns,
                            "reason": "Recommended index for common queries",
                        }
                    )

        return analysis

    def get_table_stats(self) -> list[dict[str, Any]]:
        """
        Get statistics for all tables.

        Returns:
            List of table statistics.
        """
        stats = []

        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Get all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            for table in tables:
                table_stat = {
                    "name": table,
                    "row_count": 0,
                    "size_estimate": 0,
                }

                try:
                    # Get row count
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    table_stat["row_count"] = cursor.fetchone()[0]

                    # Estimate size (rough)
                    cursor.execute("PRAGMA page_count")
                    page_count = cursor.fetchone()[0]
                    cursor.execute("PRAGMA page_size")
                    page_size = cursor.fetchone()[0]
                    table_stat["size_estimate"] = page_count * page_size

                except Exception:
                    pass

                stats.append(table_stat)

        return stats

    def vacuum(self) -> bool:
        """
        Run VACUUM to optimize database.

        Returns:
            bool: True if successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("VACUUM")
                conn.commit()

            logger.info("Database VACUUM completed")
            return True

        except Exception as e:
            logger.error(f"VACUUM failed: {e}")
            return False

    def analyze(self) -> bool:
        """
        Run ANALYZE to update statistics.

        Returns:
            bool: True if successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("ANALYZE")
                conn.commit()

            logger.info("Database ANALYZE completed")
            return True

        except Exception as e:
            logger.error(f"ANALYZE failed: {e}")
            return False

    def optimize(self) -> dict[str, Any]:
        """
        Run full optimization.

        Returns:
            Dict with optimization results.
        """
        results = {
            "indexes": self.create_indexes(),
            "analyze": self.analyze(),
            "vacuum": self.vacuum(),
            "stats": self.get_table_stats(),
        }

        return results

    def get_query_plan(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        """
        Get query execution plan.

        Args:
            query: SQL query.
            params: Query parameters.

        Returns:
            List of plan steps.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"EXPLAIN QUERY PLAN {query}", params)
            rows = cursor.fetchall()

        return [dict(row) if hasattr(row, "keys") else {"detail": row[0]} for row in rows]


def optimize_database() -> dict[str, Any]:
    """
    Run database optimization.

    Returns:
        Dict with optimization results.
    """
    optimizer = DatabaseOptimizer()
    return optimizer.optimize()


def create_all_indexes() -> dict[str, Any]:
    """
    Create all recommended indexes.

    Returns:
        Dict with results.
    """
    optimizer = DatabaseOptimizer()
    return optimizer.create_indexes()
