"""Fixtures for integration tests using real SQLite databases."""

import os
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database


@pytest.fixture(autouse=True)
def _force_sqlite_compat():
    """Patch is_postgresql/adapt_sql so repos use SQLite-compatible SQL.

    The production config may point to PostgreSQL, but integration tests use
    temporary SQLite databases.  Without this patch, repos generate %s
    placeholders and pass cursor_factory=RealDictCursor to sqlite3.
    """
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            yield
        finally:
            db_mod.adapt_sql = orig


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with schema initialized."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_url=f"sqlite:///{db_path}")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    _create_tables(db)

    yield db

    try:
        os.unlink(db_path)
    except OSError:
        pass


def _create_tables(db):
    """Create all tables needed by integration tests."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        from app.modules.compliance.retention import get_ddl_statements as ret_ddl
        from app.modules.sso.manager import get_ddl_statements as sso_ddl
        from app.modules.workspace.api_key_proxy import get_ddl_statements as akp_ddl
        from app.modules.workspace.collaboration import get_ddl_statements as collab_ddl
        from app.modules.workspace.prompt_library import get_ddl_statements as pl_ddl
        from app.modules.workspace.remote_agent_manager import get_ddl_statements as ram_ddl
        from app.modules.workspace.session_manager import get_ddl_statements as sm_ddl
        from app.services.auth_service import get_ddl_statements as auth_ddl
        from app.services.permission_service import get_ddl_statements as ps_ddl

        for ddl_fn in [
            sm_ddl,
            collab_ddl,
            pl_ddl,
            akp_ddl,
            ram_ddl,
            sso_ddl,
            ret_ddl,
            ps_ddl,
            auth_ddl,
        ]:
            try:
                for sql in ddl_fn():
                    cursor.execute(sql)
            except Exception:
                pass

        conn.commit()
    finally:
        conn.close()

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                tool_name TEXT,
                message_id TEXT,
                parent_id TEXT,
                role TEXT,
                content TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT,
                sender_id TEXT,
                sender_name TEXT,
                host_name TEXT,
                user_id INTEGER,
                is_group_chat INTEGER DEFAULT 0
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                tool_name TEXT,
                host_name TEXT,
                sender_name TEXT,
                total_tokens INTEGER DEFAULT 0,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                updated_at TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                models_used TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hourly_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                hour INTEGER,
                tool_name TEXT,
                host_name TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                name TEXT,
                description TEXT,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT,
                is_active INTEGER DEFAULT 1,
                is_shared INTEGER DEFAULT 0,
                deleted_at TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_projects (
                user_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                first_access_at TEXT,
                last_access_at TEXT,
                total_sessions INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                total_duration_seconds REAL DEFAULT 0,
                PRIMARY KEY (user_id, project_id)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT UNIQUE,
                status TEXT DEFAULT 'active',
                plan TEXT DEFAULT 'free',
                contact_email TEXT,
                contact_phone TEXT,
                contact_name TEXT,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT,
                trial_ends_at TEXT,
                subscription_ends_at TEXT,
                user_count INTEGER DEFAULT 0,
                total_tokens_used INTEGER DEFAULT 0,
                total_requests_made INTEGER DEFAULT 0,
                quota TEXT,
                settings TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_quotas (
                tenant_id INTEGER PRIMARY KEY,
                daily_token_limit INTEGER DEFAULT 1000000,
                monthly_token_limit INTEGER DEFAULT 30000000,
                daily_request_limit INTEGER DEFAULT 10000,
                monthly_request_limit INTEGER DEFAULT 300000,
                max_users INTEGER DEFAULT 10,
                max_sessions_per_user INTEGER DEFAULT 5,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_settings (
                tenant_id INTEGER PRIMARY KEY,
                content_filter_enabled INTEGER DEFAULT 1,
                audit_log_enabled INTEGER DEFAULT 1,
                audit_log_retention_days INTEGER DEFAULT 90,
                data_retention_days INTEGER DEFAULT 365,
                sso_enabled INTEGER DEFAULT 0,
                sso_provider TEXT,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                requests_made INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                new_users INTEGER DEFAULT 0,
                UNIQUE(tenant_id, date),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS content_filter_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                type TEXT DEFAULT 'keyword',
                severity TEXT DEFAULT 'medium',
                action TEXT DEFAULT 'block',
                description TEXT,
                is_enabled INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS security_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT,
                updated_at TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                last_login TEXT,
                tenant_id INTEGER
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tool_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tool_account TEXT NOT NULL,
                tool_type TEXT,
                description TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(user_id, tool_account)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL,
                granted_by INTEGER,
                granted_at TEXT,
                UNIQUE(user_id, permission)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS role_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                permission TEXT NOT NULL,
                UNIQUE(role, permission)
            )
        """
        )
        conn.commit()
    finally:
        conn.close()
