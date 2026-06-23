"""Helpers for the post-baseline Alembic lineage."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from migrations.version_table import VERSION_NUM_LENGTH

BASELINE_REVISION = "baseline_2026_06_23"
HEAD_REVISION = BASELINE_REVISION

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_SCHEMA_DIR = _PROJECT_ROOT / "schema" / "baselines"
LEGACY_MIGRATIONS_DIR = _PROJECT_ROOT / "migrations" / "legacy_versions"


def baseline_schema_path(dialect_name: str) -> Path:
    """Return the baseline schema snapshot for a database dialect."""
    if dialect_name == "postgresql":
        return _BASELINE_SCHEMA_DIR / "baseline_2026_06_23-postgres.sql"
    if dialect_name == "sqlite":
        return _BASELINE_SCHEMA_DIR / "baseline_2026_06_23-sqlite.sql"
    raise ValueError(f"Unsupported dialect for baseline schema: {dialect_name}")


def read_baseline_schema(dialect_name: str) -> str:
    """Read the baseline schema snapshot for a database dialect."""
    return baseline_schema_path(dialect_name).read_text(encoding="utf-8")


def iter_sql_statements(sql_text: str) -> list[str]:
    """Split a SQL script into executable statements.

    This helper is intentionally limited to the current baseline snapshots,
    which only contain plain DDL/DML statements terminated by line-ending
    semicolons. It is not safe for PostgreSQL function bodies, dollar-quoted
    strings, or trigger definitions that may embed semicolons internally.
    """
    statements: list[str] = []
    buffer: list[str] = []

    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue

        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            buffer = []
            if statement:
                statements.append(statement[:-1] if statement.endswith(";") else statement)

    trailing = "\n".join(buffer).strip()
    if trailing:
        statements.append(trailing)

    return [statement for statement in statements if statement.strip()]


def execute_sql_script(connection: Connection, sql_text: str) -> None:
    """Execute a SQL script statement by statement."""
    if connection.dialect.name == "sqlite":
        raw_connection = connection.connection.driver_connection
        raw_connection.executescript(sql_text)
        return

    raw_connection = connection.connection.driver_connection
    cursor = raw_connection.cursor()
    for statement in iter_sql_statements(sql_text):
        cursor.execute(statement)
    cursor.close()


def table_exists(connection: Connection, table_name: str) -> bool:
    """Return whether a table exists in the current database."""
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        return result.scalar() is not None

    result = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return result.scalar() is not None


def version_table_exists(connection: Connection) -> bool:
    """Return whether alembic_version exists."""
    return table_exists(connection, "alembic_version")


def read_current_revision(connection: Connection) -> str | None:
    """Read the current Alembic revision directly from the database.

    The baseline lineage is intentionally single-head. We read one ordered row
    here so legacy databases with a single alembic_version entry remain cheap
    to inspect during cutover.
    """
    if not version_table_exists(connection):
        return None

    result = connection.execute(
        sa.text("SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1")
    ).fetchone()
    if not result:
        return None
    return str(result[0])


def stamp_revision(connection: Connection, revision: str) -> None:
    """Directly write the Alembic revision table."""
    if not version_table_exists(connection):
        connection.exec_driver_sql(
            f"""
            CREATE TABLE alembic_version (
                version_num VARCHAR({VERSION_NUM_LENGTH}) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
            """
        )

    connection.execute(sa.text("DELETE FROM alembic_version"))
    connection.execute(
        sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
        {"revision": revision},
    )
