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
    """Create all tables from the authoritative schema.sql (#1273 follow-up).

    Previously aggregated 10 modules' get_ddl_statements() plus ~17 hand-written
    CREATE TABLE statements — the same shadow-schema drift #1276 fixed for
    production. Now a single call to load_schema_from_file builds the full
    authoritative schema (68 tables), so integration tests exercise the SAME
    schema the app starts with, and drift can't silently re-emerge here.
    """
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(db_url=db.db_url, dialect="sqlite")


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
    from unittest.mock import patch

    from flask import g

    # In tests, we'll patch g.user_id before each request
    return {"Content-Type": "application/json"}
