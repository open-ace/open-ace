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
    orig_adapt_sql = db_mod.adapt_sql
    db_mod.adapt_sql = lambda q: q
    try:
        db_path = str(tmp_path / "test.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = Database(db_url=f"sqlite:///{db_path}")
        _create_sqlite_tables(db)

        # Patch is_postgresql in database module and all modules that import it
        patches = [patch.object(db_mod, "is_postgresql", return_value=False)]

        # Import and patch all repositories that use is_postgresql
        try:
            import app.repositories.usage_repo as usage_repo

            patches.append(patch.object(usage_repo, "is_postgresql", return_value=False))
        except ImportError:
            pass

        try:
            import app.repositories.daily_stats_repo as daily_stats_repo

            patches.append(patch.object(daily_stats_repo, "is_postgresql", return_value=False))
        except ImportError:
            pass

        # Combine all patches
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            yield db
    finally:
        db_mod.adapt_sql = orig_adapt_sql
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _create_sqlite_tables(db):
    """Create all tables from the authoritative schema.sql (#1273 follow-up).

    Previously aggregated 10 modules' get_ddl_statements() plus ~17 hand-written
    CREATE TABLE statements — the same shadow-schema drift #1276 fixed for
    production. Now a single call to load_schema_from_file builds the full
    authoritative schema (68 tables), so integration tests exercise the SAME
    schema the app starts with, and drift can't silently re-emerge here.
    """
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(db_url=db.db_url, dialect="sqlite")

    # Issue #2789: Create tenant_sensitive_keywords and tenant_keywords_version tables
    # These tables are defined in migration 20260819_001 but may not be in schema.sql yet
    _create_tenant_keywords_tables(db, dialect="sqlite")


def _create_tenant_keywords_tables(db, dialect="sqlite"):
    """Create tenant keywords tables for Issue #2789.

    Creates tables if they don't exist (idempotent).
    """
    try:
        if dialect == "sqlite":
            db.execute("""
                CREATE TABLE IF NOT EXISTS tenant_sensitive_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    normalized_keyword TEXT NOT NULL,
                    is_enabled INTEGER DEFAULT 1 NOT NULL,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP
                )
            """)
            db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_keyword
                ON tenant_sensitive_keywords(tenant_id, normalized_keyword)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_keywords_tenant
                ON tenant_sensitive_keywords(tenant_id)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS tenant_keywords_version (
                    tenant_id INTEGER PRIMARY KEY,
                    version INTEGER DEFAULT 1 NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
            """)
        else:
            # PostgreSQL
            db.execute("""
                CREATE TABLE IF NOT EXISTS tenant_sensitive_keywords (
                    id SERIAL PRIMARY KEY,
                    tenant_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    normalized_keyword TEXT NOT NULL,
                    is_enabled BOOLEAN DEFAULT TRUE NOT NULL,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP
                )
            """)
            db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_keyword
                ON tenant_sensitive_keywords(tenant_id, normalized_keyword)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_keywords_tenant
                ON tenant_sensitive_keywords(tenant_id)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS tenant_keywords_version (
                    tenant_id INTEGER PRIMARY KEY,
                    version BIGINT DEFAULT 1 NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
            """)
    except Exception as e:
        logger.warning(f"Could not create tenant_keywords tables (may already exist): {e}")


def _get_pg_base_url():
    """Return the base PostgreSQL URL for creating/dropping test databases."""
    return os.environ.get("PG_TEST_URL", "postgresql://localhost:5432/ace")


def _create_pg_tables(db):
    """Create all tables from the authoritative schema.sql (Issue #1277).

    Replaces the previous two-phase approach (manual CREATE TABLE + get_ddl_statements())
    with a single call to load_schema_from_file(), ensuring integration tests
    use the exact same schema as production startup.
    """
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(db_url=db.db_url, dialect="postgresql")

    # Issue #2789: Create tenant_sensitive_keywords and tenant_keywords_version tables
    _create_tenant_keywords_tables(db, dialect="postgresql")


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
    try:
        conn = psycopg2.connect(base_url, connect_timeout=2)
    except psycopg2.OperationalError as exc:
        # Skip cleanly when no live PostgreSQL server is reachable instead of
        # erroring every test. These integration tests require a running Postgres;
        # environments without one (local sandbox, CI without a DB service) skip.
        pytest.skip(f"PostgreSQL server not reachable at {base_url}: {exc}")
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
    from app.routes.governance import governance_bp

    app = Flask(__name__)
    app.register_blueprint(compliance_bp)
    app.register_blueprint(governance_bp, url_prefix="/api")
    app.config["TESTING"] = True

    # Patch database to use tmp_db
    with patch("app.repositories.database.Database", return_value=tmp_db):
        with patch("app.routes.compliance.report_generator.db", tmp_db):
            # Issue #2789: Patch governance_repo to use tmp_db
            with patch("app.routes.governance.governance_repo.db", tmp_db):
                with patch("app.routes.governance.governance_repo._ensure_config_dir"):
                    # Patch GovernanceRepository constructor to always use tmp_db
                    with patch(
                        "app.repositories.governance_repo.GovernanceRepository.__init__",
                        lambda self, db=None: setattr(self, "db", tmp_db),
                    ):
                        yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def auth_headers():
    """Headers for authenticated user (simulates login)."""
    # For admin_required decorator, we need to mock g.user_id
    from unittest.mock import patch

    from flask import g

    # In tests, we'll patch g.user_id before each request
    return {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Additional fixtures for tenant keywords tests (Issue #2789)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_db):
    """Alias for tmp_db for compatibility with tenant keywords tests."""
    return tmp_db


@pytest.fixture
def admin_client(app, tmp_db):
    """Create authenticated admin test client for tenant keywords tests."""
    from unittest.mock import patch

    # Ensure necessary tenant and user records exist for foreign key constraints
    # Tenant 1 is required for tenant_id references
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (1, 'Default Tenant', 'default')"
    )
    # User 1 is required for created_by references (platform admin)
    # Note: password_hash is required, role must be valid ('platform_admin', 'tenant_admin', etc.)
    tmp_db.execute(
        """INSERT OR IGNORE INTO users
           (id, username, email, password_hash, role, tenant_id)
           VALUES (1, 'admin', 'admin@test.com', 'test_hash', 'platform_admin', NULL)"""
    )
    # User 2 for tenant admin (tenant_id must NOT be NULL for tenant_admin role)
    tmp_db.execute(
        """INSERT OR IGNORE INTO users
           (id, username, email, password_hash, role, tenant_id)
           VALUES (2, 'tenant_admin', 'tenant@test.com', 'test_hash', 'tenant_admin', 1)"""
    )

    # Mock authenticated admin user
    admin_user = {
        "id": 1,
        "username": "admin",
        "email": "admin@test.com",
        "role": "platform_admin",  # Must match valid role in users table
        "tenant_id": None,  # Platform admin
    }

    with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
        client = app.test_client()
        client.set_cookie("session_token", "test-admin-token")
        yield client


@pytest.fixture
def tenant_admin_client(app, tmp_db):
    """Create authenticated tenant admin test client for tenant keywords tests."""
    from unittest.mock import patch

    # Ensure necessary tenant and user records exist for foreign key constraints
    tmp_db.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug) VALUES (1, 'Default Tenant', 'default')"
    )
    tmp_db.execute(
        """INSERT OR IGNORE INTO users
           (id, username, email, password_hash, role, tenant_id)
           VALUES (1, 'admin', 'admin@test.com', 'test_hash', 'platform_admin', NULL)"""
    )
    tmp_db.execute(
        """INSERT OR IGNORE INTO users
           (id, username, email, password_hash, role, tenant_id)
           VALUES (2, 'tenant_admin', 'tenant@test.com', 'test_hash', 'tenant_admin', 1)"""
    )

    # Mock authenticated tenant admin user
    tenant_user = {
        "id": 2,
        "username": "tenant_admin",
        "email": "tenant@test.com",
        "role": "tenant_admin",  # Must be tenant_admin for same_tenant_or_platform_admin decorator
        "tenant_id": 1,
    }

    with patch("app.auth.decorators._load_user_from_token", return_value=tenant_user):
        client = app.test_client()
        client.set_cookie("session_token", "test-tenant-token")
        yield client
