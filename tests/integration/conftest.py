"""Fixtures for integration tests using real databases."""

import logging
import os
import uuid
from unittest.mock import patch

import pytest

logger = logging.getLogger(__name__)

import app.repositories.database as db_mod
from app.repositories.database import Database

# ---------------------------------------------------------------------------
# SQLite fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with schema initialized.

    Patches is_postgresql/adapt_sql only within this fixture's scope so that
    PostgreSQL tests are unaffected.
    """
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            db = Database(db_url=f"sqlite:///{db_path}")
            _create_sqlite_tables(db)
            yield db
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


def _create_sqlite_tables(db):
    """Create all tables needed by integration tests (SQLite DDL)."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        from app.modules.compliance.retention import get_ddl_statements as ret_ddl
        from app.modules.compliance.report import get_ddl_statements as report_ddl
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
            report_ddl,
            ps_ddl,
            auth_ddl,
        ]:
            try:
                for sql in ddl_fn():
                    cursor.execute(sql)
            except Exception as exc:
                logger.warning("DDL function %s failed: %s", ddl_fn.__module__, exc)

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
                full_entry TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT,
                sender_id TEXT,
                sender_name TEXT,
                host_name TEXT,
                message_source TEXT,
                feishu_conversation_id TEXT,
                group_subject TEXT,
                is_group_chat INTEGER DEFAULT 0,
                agent_session_id TEXT,
                conversation_id TEXT,
                created_at TEXT,
                deleted_at TEXT,
                user_id INTEGER,
                project_path TEXT,
                UNIQUE(date, tool_name, message_id, host_name)
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
                user_id INTEGER NULL,
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
        # Add UNIQUE constraint for INSERT OR REPLACE to work correctly
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_stats_date_tool_host_sender
            ON daily_stats (date, tool_name, host_name, sender_name)
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
                request_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                updated_at TEXT
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
                auto_provision_users INTEGER DEFAULT 0,
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
                deleted_at TEXT,
                tenant_id INTEGER,
                system_account TEXT
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
                role_name TEXT NOT NULL,
                permission TEXT NOT NULL,
                UNIQUE(role_name, permission)
            )
        """
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PostgreSQL fixture
# ---------------------------------------------------------------------------


def _get_pg_base_url():
    """Return the base PostgreSQL URL for creating/dropping test databases."""
    return os.environ.get("PG_TEST_URL", "postgresql://localhost:5432/ace")


def _create_pg_tables(db):
    """Create all tables needed by integration tests (PostgreSQL DDL)."""
    # First, create base tables that DDL functions depend on (tenants, users)
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_messages (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                tool_name TEXT,
                message_id TEXT,
                parent_id TEXT,
                role TEXT,
                content TEXT,
                full_entry TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT,
                sender_id TEXT,
                sender_name TEXT,
                host_name TEXT,
                message_source TEXT,
                feishu_conversation_id TEXT,
                group_subject TEXT,
                is_group_chat INTEGER DEFAULT 0,
                agent_session_id TEXT,
                conversation_id TEXT,
                created_at TEXT,
                deleted_at TEXT,
                user_id INTEGER,
                project_path TEXT,
                UNIQUE(date, tool_name, message_id, host_name)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_stats (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                tool_name TEXT,
                host_name TEXT,
                sender_name TEXT,
                user_id INTEGER NULL,
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
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                hour INTEGER,
                tool_name TEXT,
                host_name TEXT,
                tokens_used INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
                path TEXT NOT NULL,
                name TEXT,
                description TEXT,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT,
                is_active BOOLEAN DEFAULT true,
                is_shared BOOLEAN DEFAULT false,
                deleted_at TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_projects (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                first_access_at TEXT,
                last_access_at TEXT,
                total_sessions INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                total_duration_seconds REAL DEFAULT 0,
                UNIQUE(user_id, project_id)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER NOT NULL UNIQUE,
                content_filter_enabled BOOLEAN DEFAULT true,
                audit_log_enabled BOOLEAN DEFAULT true,
                audit_log_retention_days INTEGER DEFAULT 90,
                data_retention_days INTEGER DEFAULT 365,
                sso_enabled BOOLEAN DEFAULT false,
                sso_provider TEXT,
                auto_provision_users BOOLEAN DEFAULT false,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_usage (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                pattern TEXT NOT NULL,
                type TEXT DEFAULT 'keyword',
                severity TEXT DEFAULT 'medium',
                action TEXT DEFAULT 'block',
                description TEXT,
                is_enabled BOOLEAN DEFAULT true,
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
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                last_login TEXT,
                deleted_at TEXT,
                tenant_id INTEGER,
                system_account TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tool_accounts (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                role_name TEXT NOT NULL,
                permission TEXT NOT NULL,
                UNIQUE(role_name, permission)
            )
        """
        )
        conn.commit()
    finally:
        conn.close()

    # Second, execute DDL functions that depend on base tables
    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Reuse DDL functions that already emit PostgreSQL-compatible SQL
        from app.modules.compliance.retention import get_ddl_statements as ret_ddl
        from app.modules.compliance.report import get_ddl_statements as report_ddl
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
            report_ddl,
            ps_ddl,
            auth_ddl,
        ]:
            try:
                for sql in ddl_fn():
                    cursor.execute(sql)
            except Exception as exc:
                logger.warning("DDL function %s failed: %s", ddl_fn.__module__, exc)

        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def pg_db():
    """Create a temporary PostgreSQL database for integration testing.

    Creates an isolated test database (ace_test_<uuid>), initializes the schema,
    and drops it after tests complete.  Does NOT touch the production 'ace' database.
    """
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import RealDictCursor

    base_url = _get_pg_base_url()
    test_db_name = f"ace_test_{uuid.uuid4().hex[:8]}"

    # Create test database
    conn = psycopg2.connect(base_url)
    conn.autocommit = True
    try:
        conn.cursor().execute(f'CREATE DATABASE "{test_db_name}"')
    finally:
        conn.close()

    test_url = base_url.rsplit("/", 1)[0] + "/" + test_db_name

    # Create a fresh connection pool pointing to the test database
    db_mod._pg_pool = pg_pool.ThreadedConnectionPool(1, 10, test_url)

    import scripts.shared.config as config_mod

    try:
        db = Database(db_url=test_url)
        _create_pg_tables(db)

        # Patch global functions so repo code's is_postgresql() and get_database_url()
        # point to our test database instead of the production config.
        with patch.object(db_mod, "is_postgresql", return_value=True):
            with patch.object(db_mod, "get_database_url", return_value=test_url):
                with patch.object(config_mod, "get_database_url", return_value=test_url):
                    yield db
    finally:
        # Cleanup: close connections and drop test database
        db_mod._pg_pool = None

        conn = psycopg2.connect(base_url)
        conn.autocommit = True
        try:
            conn.cursor().execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (test_db_name,),
            )
            conn.cursor().execute(f'DROP DATABASE IF EXISTS "{test_db_name}"')
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Flask app fixtures for API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_db):
    """Create Flask app for testing with temporary database."""
    from flask import Flask
    from app.routes.compliance import compliance_bp

    app = Flask(__name__)
    app.register_blueprint(compliance_bp)
    app.config["TESTING"] = True

    # Patch database to use tmp_db
    with patch("app.repositories.database.Database", return_value=tmp_db):
        with patch("app.routes.compliance.report_generator.db", tmp_db):
            yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def auth_headers():
    """Headers for authenticated user (simulates login)."""
    # For admin_required decorator, we need to mock g.user_id
    from flask import g
    from unittest.mock import patch

    # In tests, we'll patch g.user_id before each request
    return {"Content-Type": "application/json"}
