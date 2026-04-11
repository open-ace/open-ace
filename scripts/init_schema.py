#!/usr/bin/env python3
"""
Initialize database schema for fresh installation.

This script creates the complete database schema directly,
without running through alembic migrations.

For upgrades from existing databases, use alembic migrations instead:
    python -m alembic upgrade head
"""

import os
import sys

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.shared.db import (
    get_connection,
    is_postgresql,
    _execute,
)


def _table_exists(cursor, table_name: str) -> bool:
    """Check if a table exists."""
    if is_postgresql():
        _execute(
            cursor,
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
            (table_name,),
        )
        result = cursor.fetchone()
        # Handle both dict-like (RealDictRow) and tuple results
        if isinstance(result, dict):
            return result.get("exists", False)
        else:
            return result[0] if result else False
    else:
        _execute(
            cursor, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        )
        return cursor.fetchone() is not None


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if is_postgresql():
        _execute(
            cursor,
            "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = %s AND column_name = %s)",
            (table_name, column_name),
        )
        result = cursor.fetchone()
        # Handle both dict-like (RealDictRow) and tuple results
        if isinstance(result, dict):
            return result.get("exists", False)
        else:
            return result[0] if result else False
    else:
        _execute(
            cursor,
            "SELECT name FROM pragma_table_info(?) WHERE name=?",
            (table_name, column_name),
        )
        return cursor.fetchone() is not None


def _index_exists(cursor, table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
    if is_postgresql():
        _execute(
            cursor,
            "SELECT EXISTS (SELECT FROM pg_indexes WHERE tablename = %s AND indexname = %s)",
            (table_name, index_name),
        )
        result = cursor.fetchone()
        # Handle both dict-like (RealDictRow) and tuple results
        if isinstance(result, dict):
            return result.get("exists", False)
        else:
            return result[0] if result else False
    else:
        _execute(
            cursor,
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND name=?",
            (table_name, index_name),
        )
        return cursor.fetchone() is not None


def init_schema():
    """Create complete database schema for fresh installation."""
    conn = get_connection()
    cursor = conn.cursor()
    pg = is_postgresql()

    print(f"Initializing schema for {pg and 'PostgreSQL' or 'SQLite'}...")

    # Determine ID type
    id_type = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # ============================================
    # 1. Core tables
    # ============================================

    # daily_messages - AI tool message records
    if not _table_exists(cursor, "daily_messages"):
        role_type = "message_role" if pg else "TEXT"
        role_check = "" if pg else "CHECK (role IN ('user', 'assistant', 'system'))"
        _execute(
            cursor,
            f"""
            CREATE TABLE daily_messages (
                id {id_type},
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT NOT NULL DEFAULT 'localhost',
                message_id TEXT NOT NULL,
                parent_id TEXT,
                role {role_type} NOT NULL {role_check},
                content TEXT,
                full_entry TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT,
                sender_id TEXT,
                sender_name TEXT,
                message_source TEXT,
                conversation_id TEXT,
                agent_session_id TEXT,
                project_path TEXT,
                feishu_conversation_id TEXT,
                group_subject TEXT,
                is_group_chat INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, message_id, host_name)
            )
        """,
        )
        print("Created table: daily_messages")

    # daily_usage - AI tool usage statistics
    if not _table_exists(cursor, "daily_usage"):
        _execute(
            cursor,
            f"""
            CREATE TABLE daily_usage (
                id {id_type},
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT NOT NULL DEFAULT 'localhost',
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, host_name)
            )
        """,
        )
        print("Created table: daily_usage")

    # users - System users
    if not _table_exists(cursor, "users"):
        role_type = "user_role" if pg else "TEXT"
        role_check = "" if pg else "CHECK (role IN ('admin', 'manager', 'user'))"
        role_default = "'user'::user_role" if pg else "'user'"
        _execute(
            cursor,
            f"""
            CREATE TABLE users (
                id {id_type},
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT UNIQUE,
                role {role_type} NOT NULL DEFAULT {role_default} {role_check},
                daily_token_quota INTEGER DEFAULT 1000000,
                daily_request_quota INTEGER DEFAULT 10000,
                monthly_token_quota INTEGER DEFAULT 10000000,
                monthly_request_quota INTEGER DEFAULT 30000,
                is_active INTEGER DEFAULT 1,
                system_account TEXT,
                must_change_password INTEGER DEFAULT 0,
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP
            )
        """,
        )
        print("Created table: users")

    # tenants - Multi-tenant organizations
    if not _table_exists(cursor, "tenants"):
        status_type = "tenant_status" if pg else "TEXT"
        status_check = "" if pg else "CHECK (status IN ('active', 'suspended', 'trial', 'inactive'))"
        status_default = "'active'::tenant_status" if pg else "'active'"
        plan_type = "tenant_plan" if pg else "TEXT"
        plan_check = "" if pg else "CHECK (plan IN ('free', 'standard', 'premium', 'enterprise'))"
        plan_default = "'standard'::tenant_plan" if pg else "'standard'"
        quota_type = "JSONB" if pg else "TEXT"
        settings_type = "JSONB" if pg else "TEXT"

        _execute(
            cursor,
            f"""
            CREATE TABLE tenants (
                id {id_type},
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                status {status_type} NOT NULL DEFAULT {status_default} {status_check},
                plan {plan_type} NOT NULL DEFAULT {plan_default} {plan_check},
                contact_email TEXT,
                contact_phone TEXT,
                contact_name TEXT,
                quota {quota_type},
                settings {settings_type},
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at TIMESTAMP
            )
        """,
        )
        print("Created table: tenants")

    # sessions - User sessions
    if not _table_exists(cursor, "sessions"):
        _execute(
            cursor,
            f"""
            CREATE TABLE sessions (
                id {id_type},
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """,
        )
        print("Created table: sessions")

    # quota_usage - User quota tracking
    if not _table_exists(cursor, "quota_usage"):
        _execute(
            cursor,
            f"""
            CREATE TABLE quota_usage (
                id {id_type},
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                tool_name TEXT,
                tokens_used INTEGER DEFAULT 0,
                requests_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """,
        )
        print("Created table: quota_usage")

    # quota_alerts - Quota alerts
    if not _table_exists(cursor, "quota_alerts"):
        severity_type = "audit_severity" if pg else "TEXT"
        severity_check = "" if pg else "CHECK (severity IN ('info', 'warning', 'error', 'critical'))"
        _execute(
            cursor,
            f"""
            CREATE TABLE quota_alerts (
                id {id_type},
                user_id INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                current_value INTEGER NOT NULL,
                message TEXT,
                acknowledged INTEGER DEFAULT 0,
                acknowledged_by INTEGER,
                acknowledged_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (acknowledged_by) REFERENCES users(id)
            )
        """,
        )
        print("Created table: quota_alerts")

    # audit_logs - System audit trail
    if not _table_exists(cursor, "audit_logs"):
        severity_type = "audit_severity" if pg else "TEXT"
        severity_check = "" if pg else "CHECK (severity IN ('info', 'warning', 'error', 'critical'))"
        severity_default = "'info'::audit_severity" if pg else "'info'"
        _execute(
            cursor,
            f"""
            CREATE TABLE audit_logs (
                id {id_type},
                user_id INTEGER,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                details TEXT,
                severity {severity_type} DEFAULT {severity_default} {severity_check},
                ip_address TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """,
        )
        print("Created table: audit_logs")

    # tenant_usage - Tenant usage tracking
    if not _table_exists(cursor, "tenant_usage"):
        _execute(
            cursor,
            f"""
            CREATE TABLE tenant_usage (
                id {id_type},
                tenant_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                requests_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """,
        )
        print("Created table: tenant_usage")

    # web_user_auth_sessions - Web UI authentication
    if not _table_exists(cursor, "web_user_auth_sessions"):
        _execute(
            cursor,
            f"""
            CREATE TABLE web_user_auth_sessions (
                id {id_type},
                user_id INTEGER NOT NULL,
                session_token TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """,
        )
        print("Created table: web_user_auth_sessions")

    # usage_summary - Pre-aggregated dashboard data
    if not _table_exists(cursor, "usage_summary"):
        _execute(
            cursor,
            f"""
            CREATE TABLE usage_summary (
                id {id_type},
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT NOT NULL,
                total_tokens INTEGER DEFAULT 0,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                assistant_message_count INTEGER DEFAULT 0,
                user_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, host_name)
            )
        """,
        )
        print("Created table: usage_summary")

    # daily_stats - Pre-aggregated trend analysis
    if not _table_exists(cursor, "daily_stats"):
        _execute(
            cursor,
            f"""
            CREATE TABLE daily_stats (
                id {id_type},
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                requests INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, host_name)
            )
        """,
        )
        print("Created table: daily_stats")

    # hourly_stats - Pre-aggregated hourly usage
    if not _table_exists(cursor, "hourly_stats"):
        _execute(
            cursor,
            f"""
            CREATE TABLE hourly_stats (
                id {id_type},
                date TEXT NOT NULL,
                hour INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                requests INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, hour, tool_name, host_name)
            )
        """,
        )
        print("Created table: hourly_stats")

    # security_settings - Security configuration
    if not _table_exists(cursor, "security_settings"):
        _execute(
            cursor,
            f"""
            CREATE TABLE security_settings (
                id {id_type},
                setting_key TEXT NOT NULL UNIQUE,
                setting_value TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        )
        print("Created table: security_settings")

    # ============================================
    # 2. PostgreSQL-specific: Create ENUM types
    # ============================================
    if pg:
        # Create ENUM types (ignore if already exist)
        enum_types = [
            ("user_role", ["admin", "manager", "user"]),
            ("tenant_status", ["active", "suspended", "trial", "inactive"]),
            ("tenant_plan", ["free", "standard", "premium", "enterprise"]),
            ("message_role", ["user", "assistant", "system"]),
            ("audit_severity", ["info", "warning", "error", "critical"]),
        ]

        for enum_name, values in enum_types:
            try:
                values_str = ", ".join(f"'{v}'" for v in values)
                _execute(
                    cursor,
                    f"""
                    DO $$ BEGIN
                        CREATE TYPE {enum_name} AS ENUM ({values_str});
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                """,
                )
                print(f"Created ENUM type: {enum_name}")
            except Exception as e:
                print(f"Note: {enum_name} may already exist: {e}")

        # Create updated_at trigger function
        _execute(
            cursor,
            """
            CREATE OR REPLACE FUNCTION update_timestamp()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """,
        )
        print("Created trigger function: update_timestamp")

        # Apply triggers to tables with updated_at
        for table in ["tenants", "tenant_usage", "quota_usage", "users", "usage_summary", "daily_stats", "hourly_stats"]:
            if _table_exists(cursor, table) and _column_exists(cursor, table, "updated_at"):
                trigger_name = f"{table}_updated_at_trigger"
                _execute(
                    cursor,
                    f"""
                    DROP TRIGGER IF EXISTS {trigger_name} ON {table};
                    CREATE TRIGGER {trigger_name}
                        BEFORE UPDATE ON {table}
                        FOR EACH ROW
                        WHEN (OLD.* IS DISTINCT FROM NEW.*)
                        EXECUTE FUNCTION update_timestamp();
                """,
                )
                print(f"Created trigger: {trigger_name}")

        # Add GIN indexes for JSONB fields
        if _table_exists(cursor, "tenants"):
            if _column_exists(cursor, "tenants", "quota"):
                if not _index_exists(cursor, "tenants", "idx_tenants_quota_gin"):
                    _execute(cursor, "CREATE INDEX idx_tenants_quota_gin ON tenants USING gin(quota)")
                    print("Created index: idx_tenants_quota_gin")
            if _column_exists(cursor, "tenants", "settings"):
                if not _index_exists(cursor, "tenants", "idx_tenants_settings_gin"):
                    _execute(cursor, "CREATE INDEX idx_tenants_settings_gin ON tenants USING gin(settings)")
                    print("Created index: idx_tenants_settings_gin")

    # ============================================
    # 3. Create indexes
    # ============================================
    indexes = [
        # daily_messages indexes
        ("daily_messages", "idx_messages_date_tool_host", "date, tool_name, host_name"),
        ("daily_messages", "idx_messages_date_role_timestamp", "date, role, timestamp DESC"),
        ("daily_messages", "idx_messages_sender_id", "sender_id"),
        ("daily_messages", "idx_messages_timestamp", "timestamp"),
        ("daily_messages", "idx_messages_conversation", "date, conversation_id, agent_session_id"),
        ("daily_messages", "idx_messages_date_sender_id", "date, sender_id"),
        ("daily_messages", "idx_messages_project_path", "project_path"),
        ("daily_messages", "idx_messages_agent_session_project", "agent_session_id, project_path"),
        # users indexes
        ("users", "idx_users_username", "username"),
        ("users", "idx_users_email", "email"),
        ("users", "idx_users_tenant_fk", "tenant_id"),
        ("users", "idx_users_active_partial", "username, email, role", "WHERE is_active = 1 AND deleted_at IS NULL" if pg else ""),
        # sessions indexes
        ("sessions", "idx_sessions_user_fk", "user_id"),
        ("sessions", "idx_sessions_token", "token"),
        # quota_usage indexes
        ("quota_usage", "idx_quota_usage_user_fk", "user_id"),
        ("quota_usage", "idx_quota_usage_date", "date"),
        # audit_logs indexes
        ("audit_logs", "idx_audit_logs_user_fk", "user_id"),
        ("audit_logs", "idx_audit_recent", "timestamp, user_id, action"),
        # tenant_usage indexes
        ("tenant_usage", "idx_tenant_usage_fk", "tenant_id"),
        # daily_stats indexes
        ("daily_stats", "idx_daily_stats_date_tool", "date, tool_name"),
        # hourly_stats indexes
        ("hourly_stats", "idx_hourly_stats_date_hour", "date, hour"),
        # usage_summary indexes
        ("usage_summary", "idx_usage_summary_date", "date"),
    ]

    for table, idx_name, columns, *extra in indexes:
        where_clause = extra[0] if extra else ""
        if _table_exists(cursor, table):
            if not _index_exists(cursor, table, idx_name):
                try:
                    sql = f"CREATE INDEX {idx_name} ON {table} ({columns})"
                    if where_clause:
                        sql += f" {where_clause}"
                    _execute(cursor, sql)
                    print(f"Created index: {idx_name}")
                except Exception as e:
                    print(f"Note: Failed to create index {idx_name}: {e}")

    conn.commit()
    conn.close()

    print("Database schema initialized successfully!")
    return True


def main():
    """Main entry point."""
    print("=" * 50)
    print("  Open ACE - Database Schema Initialization")
    print("=" * 50)
    print()

    try:
        init_schema()
        print()
        print("Schema initialization complete.")
        print("Next step: Run scripts/init_db.py to create default admin user.")
    except Exception as e:
        print(f"Error: Failed to initialize schema: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()