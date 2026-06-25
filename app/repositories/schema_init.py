"""Centralized schema initialization for all modules.

Called once at app startup via create_app() to ensure all tables exist,
replacing the per-request _ensure_tables() pattern that caused ShareLock
contention on PostgreSQL.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

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

    Duplicates migrations/baseline.iter_sql_statements by design: importing
    migrations/ (Alembic land: sqlalchemy, version_table) into the app startup
    path is undesirable, so this keeps schema_init dependency-light. The two must
    stay in sync; if the schema files ever grow dollar-quoted function bodies or
    embedded semicolons, both need a real tokenizer. The schema files currently
    contain only plain DDL terminated by line-ending semicolons, so line-based
    splitting is safe (and tested against the MATERIALIZED VIEW statement).
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
    This reads schema-{sqlite,postgres}.sql and executes them — so every table
    carries the full, authoritative column set with no parallel hand-maintained
    DDL.

    ``dialect`` lets a caller (e.g. SessionManager._ensure_tables under a
    monkeypatched is_postgresql) pin the dialect explicitly; otherwise it's
    derived from the current DATABASE_URL.

    Idempotency (re-running on a DB that already has the tables):
      * sqlite: the whole script runs via executescript; CREATE TABLE/INDEX are
        rewritten to IF NOT EXISTS in-memory so the script is safe to re-run.
      * postgres: CREATE TABLE/INDEX are likewise rewritten to IF NOT EXISTS;
        additionally the connection is switched to autocommit for the DDL loop
        so each statement is independent. The schema file also contains objects
        _not_ covered by IF NOT EXISTS (~40 CREATE SEQUENCE, a MATERIALIZED
        VIEW, ALTER TABLE ADD CONSTRAINT) — on a live DB these raise "already
        exists", which is expected and logged at DEBUG; genuinely unexpected
        errors are logged at WARNING. Without autocommit a single pre-existing
        object would abort the transaction and cascade-skip every later
        statement (#1276 review).
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
            # autocommit so each statement is independent: a pre-existing
            # SEQUENCE/VIEW/CONSTRAINT (not covered by IF NOT EXISTS) raising
            # "already exists" won't abort the transaction and cascade-skip the
            # rest of the script. NB: PgConnectionWrapper delegates attribute
            # access to its underlying psycopg2 conn, but __setattr__ lands on
            # the wrapper itself, so set autocommit on the raw _conn.
            raw: Any = getattr(conn, "_conn", conn)
            prev_autocommit = getattr(raw, "autocommit", False)
            # psycopg2 raises ProgrammingError if autocommit is flipped while a
            # transaction is open; pooled connections come back idle (the
            # connection() context manager rolls back), but rollback defensively
            # so a future caller returning a dirty conn can't break the flip.
            try:
                raw.rollback()
            except Exception:
                pass
            raw.autocommit = True
            try:
                cursor = conn.cursor()
                for stmt in _iter_pg_statements(sql_text):
                    try:
                        cursor.execute(stmt)
                    except Exception as e:  # noqa: BLE001 - "already exists" is expected
                        msg = str(e).lower()
                        # Expected when re-running on an existing DB: the object
                        # or constraint already exists. Postgres reports these in
                        # several phrasings, so match them all and log at DEBUG;
                        # anything else is a genuine DDL error → WARNING.
                        is_expected = any(
                            phrase in msg
                            for phrase in (
                                "already exists",
                                "duplicate",
                                "multiple primary keys",
                                "constraint already exists",
                                "already an object named",
                            )
                        )
                        level = logging.DEBUG if is_expected else logging.WARNING
                        logger.log(level, "Schema DDL skipped: %s — %s", stmt[:80].strip(), e)
                cursor.close()
            finally:
                raw.autocommit = prev_autocommit
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
