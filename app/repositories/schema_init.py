"""Centralized schema initialization for all modules.

Called once at app startup via create_app() to ensure all tables exist,
replacing the per-request _ensure_tables() pattern that caused ShareLock
contention on PostgreSQL.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_FILES = {
    "sqlite": _PROJECT_ROOT / "schema" / "schema-sqlite.sql",
    "postgresql": _PROJECT_ROOT / "schema" / "schema-postgres.sql",
}

# Match "CREATE TABLE <name>" and "CREATE [UNIQUE] INDEX <name>", case-insensitive,
# so we can rewrite them to their IF NOT EXISTS form without editing schema.sql
# itself (the authoritative .sql files are consumed as-is by Alembic and must not
# gain IF NOT EXISTS there). This makes load_schema_from_file idempotent on a DB
# that already has the tables.
_CREATE_TABLE_RE = re.compile(r"^(\s*CREATE\s+TABLE\s+)(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE)
_CREATE_INDEX_RE = re.compile(
    r"^(\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+)(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE
)


def _make_idempotent(sql_text: str) -> str:
    """Rewrite CREATE TABLE/INDEX to their IF NOT EXISTS form.

    The authoritative schema files (schema-sqlite.sql / schema-postgres.sql) are
    written for a one-shot bootstrap of an EMPTY database (Alembic baseline), so
    their CREATE statements lack IF NOT EXISTS. Re-running them on a DB that
    already has the tables would error. This rewrites each CREATE to be safe to
    re-run, so load_schema_from_file is idempotent (production startup, tests).
    """
    out_lines = []
    for line in sql_text.splitlines(keepends=True):
        if _CREATE_TABLE_RE.match(line):
            line = _CREATE_TABLE_RE.sub(r"\1IF NOT EXISTS ", line, count=1)
        elif _CREATE_INDEX_RE.match(line):
            line = _CREATE_INDEX_RE.sub(r"\1IF NOT EXISTS ", line, count=1)
        out_lines.append(line)
    return "".join(out_lines)


def _iter_pg_statements(sql_text: str):
    """Yield top-level statements from a SQL script (split on ';'-terminated lines).

    Mirrors migrations/baseline.iter_sql_statements: the schema files only contain
    plain DDL terminated by line-ending semicolons, so line-based splitting is safe.
    """
    buffer = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            yield "\n".join(buffer)
            buffer = []
    if buffer:
        yield "\n".join(buffer)


def schema_file_for_dialect(dialect: str) -> Path:
    """Return the authoritative schema .sql path for a dialect."""
    path = _SCHEMA_FILES.get(dialect)
    if not path:
        raise ValueError(f"Unsupported dialect for schema file: {dialect}")
    return path


def load_schema_from_file(db_url: str | None = None, dialect: str | None = None) -> None:
    """Load the authoritative schema .sql as the single source of truth (#1273).

    Replaces both the per-module get_ddl_statements() aggregation and
    SessionManager._ensure_tables(): both had drifted from the authoritative
    schema files (missing columns like project_id/project_path/request_count).
    This reads schema-{sqlite,postgres}.sql, makes the CREATE statements
    idempotent (IF NOT EXISTS), and executes them — so every table carries the
    full, authoritative column set with no parallel hand-maintained DDL.

    ``dialect`` lets a caller (e.g. SessionManager._ensure_tables under a
    monkeypatched is_postgresql) pin the dialect explicitly; otherwise it's
    derived from the current DATABASE_URL.

    For sqlite the whole script runs via executescript; for postgres statements
    run one-by-one (each wrapped in try/except so a CREATE on an existing object
    is a no-op rather than aborting the whole script).
    """
    from app.repositories.database import Database, is_postgresql

    if dialect is None:
        dialect = "postgresql" if is_postgresql() else "sqlite"
    sql_text = schema_file_for_dialect(dialect).read_text(encoding="utf-8")
    sql_text = _make_idempotent(sql_text)
    db = Database(db_url=db_url)
    conn = db.get_connection()
    try:
        if db.is_postgresql:
            cursor = conn.cursor()
            for stmt in _iter_pg_statements(sql_text):
                try:
                    cursor.execute(stmt)
                except Exception as e:  # noqa: BLE001 - idempotent DDL tolerates "already exists"
                    logger.debug("Schema DDL skipped: %s — %s", stmt[:80].strip(), e)
            conn.commit()
        else:
            conn.executescript(sql_text)  # sqlite: whole-script, IF NOT EXISTS makes it safe
            conn.commit()
    finally:
        conn.close()


def ensure_all_tables() -> None:
    """Ensure all application tables and indexes exist (single source of truth).

    Loads the authoritative schema files (schema-sqlite.sql / schema-postgres.sql)
    via load_schema_from_file(). This replaces the former per-module
    get_ddl_statements() aggregation, which had drifted from the authoritative
    schema (each module hand-maintained a parallel DDL that lost columns like
    project_id/project_path/request_count — #1273).

    The per-module get_ddl_statements() functions are kept for now (deprecated;
    not called here) to avoid breaking any external references and may be
    removed in a follow-up. Schema is now driven solely by the .sql files +
    Alembic migrations.
    """
    load_schema_from_file()
    logger.info("Schema initialization complete (loaded from authoritative schema.sql)")
