"""PostgreSQL integration tests for the audit_logs.success BOOLEAN repair.

The unit tests next to this file assert the shape of the SQL string. That
cannot catch a wrong ``USING`` expression, a lost default or a broken
``DO`` block, because the only place the conversion branch ever executes is a
database whose column is still INTEGER — and ``schema-sync``'s
``alembic upgrade head`` runs against a correct one, where ``IF EXISTS`` is
false. These tests drift a throwaway database back to the legacy shape and run
the migration's own ``upgrade()`` through a real alembic operations context.
"""

import importlib.util
from pathlib import Path

import pytest

# Marks every test in this module as requiring a live PostgreSQL server.
# CI runs these in the dedicated `postgres-test` lane; locally they auto-skip
# via the pg_db fixture when no server is reachable.
pytestmark = [pytest.mark.postgres, pytest.mark.regression]

from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = (
    Path(__file__).parents[2] / "migrations/versions/20260918_002_normalize_audit_log_success.py"
)


def _load_migration():
    """Import the migration as a standalone module (it is not on sys.path)."""
    spec = importlib.util.spec_from_file_location("audit_log_success_migration_pg", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(pg_db):
    """SQLAlchemy engine on the per-test throwaway database."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    eng = sqlalchemy.create_engine(pg_db.db_url)
    yield eng
    eng.dispose()


def _run_upgrade(engine):
    """Run the migration's real upgrade() against *engine*."""
    migration = _load_migration()
    with engine.begin() as conn:
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()


def _success_column(engine):
    """Return (data_type, column_default) for audit_logs.success."""
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(text("""
                SELECT data_type, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'audit_logs'
                  AND column_name = 'success'
            """)).fetchone()
    return row[0], row[1]


def _drift_to_integer(engine):
    """Recreate the legacy SQLite-shaped column this migration repairs."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit_logs ALTER COLUMN success DROP DEFAULT"))
        conn.execute(text("""
                ALTER TABLE audit_logs
                ALTER COLUMN success TYPE INTEGER
                USING CASE WHEN success THEN 1 ELSE 0 END
            """))
        conn.execute(text("ALTER TABLE audit_logs ALTER COLUMN success SET DEFAULT 1"))


def test_legacy_integer_column_is_converted_with_values_preserved(engine):
    """0 becomes FALSE, non-zero becomes TRUE, NULL stays NULL, default restored."""
    from sqlalchemy import text

    _drift_to_integer(engine)
    assert _success_column(engine)[0] == "integer"

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_logs (action, success) VALUES "
                "('zero', 0), ('one', 1), ('seven', 7), ('null', NULL)"
            )
        )

    _run_upgrade(engine)

    data_type, column_default = _success_column(engine)
    assert data_type == "boolean"
    assert column_default == "true"

    with engine.connect() as conn:
        stored = dict(
            conn.execute(text("SELECT action, success FROM audit_logs")).fetchall()  # noqa: C408
        )
    assert stored == {"zero": False, "one": True, "seven": True, "null": None}


def test_upgrade_is_a_noop_on_an_already_boolean_column(engine):
    """A correct database keeps its type, default and rows — and can re-run."""
    from sqlalchemy import text

    assert _success_column(engine)[0] == "boolean"

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_logs (action, success) VALUES "
                "('kept_true', TRUE), ('kept_false', FALSE)"
            )
        )

    _run_upgrade(engine)
    _run_upgrade(engine)  # idempotent

    assert _success_column(engine) == ("boolean", "true")
    with engine.connect() as conn:
        stored = dict(
            conn.execute(text("SELECT action, success FROM audit_logs")).fetchall()  # noqa: C408
        )
    assert stored == {"kept_true": True, "kept_false": False}


def test_repaired_column_accepts_the_boolean_the_writers_bind(engine):
    """The repair fixes what actually broke: bool params against audit_logs.success.

    ``AuditLogger`` binds ``adapt_boolean_value(success)`` — a real bool on
    PostgreSQL — both when inserting (audit_logger.py) and when filtering
    ``success = ?`` in get_logs. Against the drifted INTEGER column that is a
    DatatypeMismatch.
    """
    from sqlalchemy import text

    _drift_to_integer(engine)

    insert = text("INSERT INTO audit_logs (action, success) VALUES ('probe', :flag)")
    select = text("SELECT count(*) FROM audit_logs WHERE success = :flag")

    with engine.begin() as conn:
        with pytest.raises(Exception):  # noqa: B017 - psycopg2 DatatypeMismatch, wrapped by SA
            conn.execute(insert, {"flag": True})

    _run_upgrade(engine)

    with engine.begin() as conn:
        conn.execute(insert, {"flag": True})
        assert conn.execute(select, {"flag": True}).scalar() == 1
