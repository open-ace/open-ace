#!/usr/bin/env python3
"""Utilities for validating and rebuilding schema snapshots."""

from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_schema import clean_postgres_schema, convert_to_sqlite


@dataclass
class SQLiteSchemaSnapshot:
    """Canonical SQLite schema representation for drift comparison."""

    tables: dict[str, list[dict[str, Any]]]
    indexes: dict[str, dict[str, Any]]


@dataclass
class SQLiteSchemaDiff:
    """Structured SQLite drift report."""

    tables_only_in_actual: list[str]
    tables_only_in_expected: list[str]
    column_diffs: dict[str, dict[str, Any]]
    indexes_only_in_actual: list[str]
    indexes_only_in_expected: list[str]
    index_definition_diffs: dict[str, dict[str, str]]

    def has_drift(self) -> bool:
        """Return whether any schema drift was detected."""
        return any(
            (
                self.tables_only_in_actual,
                self.tables_only_in_expected,
                self.column_diffs,
                self.indexes_only_in_actual,
                self.indexes_only_in_expected,
                self.index_definition_diffs,
            )
        )


def _project_root() -> Path:
    """Return the repository root."""
    return PROJECT_ROOT


def _python_executable() -> str:
    """Prefer the project's virtualenv Python when available."""
    venv_python = _project_root() / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def normalize_sql(sql: str | None) -> str:
    """Collapse SQL to a canonical single-line representation."""
    if not sql:
        return ""
    return " ".join(str(sql).split())


def _normalize_default(value: str | None) -> str:
    """Normalize SQLite default literals for comparison."""
    if value is None:
        return ""
    normalized = normalize_sql(value)
    lowered = normalized.lower()
    if lowered in {"false", "(false)"}:
        return "0"
    if lowered in {"true", "(true)"}:
        return "1"
    if len(normalized) >= 2 and normalized[0] == "'" and normalized[-1] == "'":
        inner = normalized[1:-1]
        if inner.isdigit():
            return inner
    return normalized


def _normalize_identifier(value: str | None) -> str:
    """Normalize quoted SQLite identifiers."""
    return str(value or "").replace('"', "").strip()


def _normalize_where_clause(value: str | None) -> str:
    """Normalize SQLite partial-index predicates."""
    normalized = normalize_sql(value)
    if not normalized:
        return ""
    normalized = normalized.replace('"', "")
    normalized = normalized.replace("((", "(").replace("))", ")")
    normalized = normalized.replace(") AND (", " AND ")
    normalized = normalized.replace(") OR (", " OR ")
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = normalized.replace(" IS FALSE", " = 0")
    normalized = normalized.replace(" IS TRUE", " = 1")
    normalized = normalized.replace("<>", "!=")
    normalized = re.sub(
        r"\blength\s*\(\(([^)]+)\)\)", r"LENGTH(\1)", normalized, flags=re.IGNORECASE
    )
    normalized = re.sub(r"\blength\s*\(", "LENGTH(", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"(?<![A-Za-z0-9_])\((\w+)\)(?=\s*(?:!=|=|NOT LIKE|LIKE|IS\b))",
        r"\1",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(\w+)\)(?=\s*(?:!=|=|NOT LIKE|LIKE|IS\b))",
        r"\1",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"LENGTH\(([^)]+)\)\s*>=\s*1\s+AND\s+LENGTH\(\1\)\s*<=\s*253",
        r"LENGTH(\1) BETWEEN 1 AND 253",
        normalized,
        flags=re.IGNORECASE,
    )
    while normalized.count(")") > normalized.count("(") and normalized.endswith(")"):
        normalized = normalized[:-1].rstrip()
    parts = re.split(r"\s+(AND|OR)\s+", normalized)
    for index, part in enumerate(parts):
        if part in {"AND", "OR"}:
            continue
        stripped = part.strip()
        while stripped.startswith("(") and stripped.endswith(")"):
            stripped = stripped[1:-1].strip()
        parts[index] = stripped
    normalized = " ".join(part for part in parts if part)
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    return normalized.strip()


def _normalize_column_type(column_name: str, type_name: str, default: str) -> str:
    """Normalize temporal/text affinity noise for SQLite snapshots."""
    if type_name == "TEXT":
        lowered = column_name.lower()
        if lowered == "timestamp" or lowered.endswith("_at") or lowered in {"last_login"}:
            return "TEMPORAL"
        if default in {"CURRENT_TIMESTAMP", "'(CURRENT_TIMESTAMP)'"}:
            return "TEMPORAL"
    return type_name


def _extract_where_clause(sql: str | None) -> str:
    """Extract and normalize a WHERE clause from CREATE INDEX SQL."""
    if not sql:
        return ""
    match = re.search(r"\bWHERE\b(.*)$", normalize_sql(sql), re.IGNORECASE)
    if not match:
        return ""
    return _normalize_where_clause(match.group(1))


def _index_columns(conn: sqlite3.Connection, index_name: str) -> list[str]:
    """Return normalized indexed columns, preserving DESC markers when present."""
    quoted_name = index_name.replace("'", "''")
    rows = conn.execute(f"PRAGMA index_xinfo('{quoted_name}')").fetchall()
    columns: list[str] = []
    for row in rows:
        key = row[5] if len(row) > 5 else 1
        if int(key) == 0:
            continue
        column_name = _normalize_identifier(row[2])
        if not column_name:
            continue
        desc = int(row[3]) if len(row) > 3 and row[3] is not None else 0
        columns.append(f"{column_name} DESC" if desc else column_name)
    return columns


def _index_signature(index: dict[str, Any]) -> str:
    """Build a semantic signature for a SQLite index."""
    return json.dumps(
        {
            "table": index["table"],
            "unique": int(index["unique"]),
            "columns": list(index["columns"]),
            "where": index["where"],
        },
        ensure_ascii=True,
        sort_keys=True,
    )


def _normalize_sqlite_type(type_name: str | None) -> str:
    """Normalize SQLite-compatible types to reduce formatting noise."""
    raw = str(type_name or "").strip().upper()
    if not raw:
        return ""
    if raw in {"DATE", "DATETIME", "TIMESTAMP"}:
        return "TEMPORAL"
    if raw == "BOOLEAN":
        return "INTEGER"
    if "INT" in raw:
        return "INTEGER"
    if any(token in raw for token in ("CHAR", "CLOB", "TEXT", "VARCHAR")):
        return "TEXT"
    if any(token in raw for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    if "BLOB" in raw:
        return "BLOB"
    return "NUMERIC"


def normalize_sql_text(sql: str) -> str:
    """Normalize SQL text for stable textual diffs."""
    lines = [line.rstrip() for line in sql.replace("\r\n", "\n").splitlines()]
    return "\n".join(lines).strip() + "\n"


def sqlite_snapshot_from_db(
    db_path: Path,
    *,
    include_alembic_version: bool = False,
) -> SQLiteSchemaSnapshot:
    """Read a SQLite DB into a canonical snapshot."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    table_filter = "" if include_alembic_version else "AND name != 'alembic_version'"

    tables: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
        f"""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%' {table_filter}
        ORDER BY name
        """
    ).fetchall():
        table_name = str(row["name"])
        columns = []
        for col in conn.execute(f"PRAGMA table_info({table_name})").fetchall():
            default = _normalize_default(str(col[4])) if col[4] is not None else ""
            columns.append(
                {
                    "name": str(col[1]),
                    "type": _normalize_column_type(
                        str(col[1]),
                        _normalize_sqlite_type(col[2]),
                        default,
                    ),
                    "notnull": 1 if int(col[5]) else int(col[3]),
                    "default": default,
                    "pk": int(col[5]),
                }
            )
        tables[table_name] = columns

    indexes: dict[str, dict[str, Any]] = {}
    for table_name in sorted(tables):
        for row in conn.execute(f"PRAGMA index_list('{table_name}')").fetchall():
            index_name = str(row[1])
            sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            sql_text = str(sql_row[0]) if sql_row and sql_row[0] is not None else ""
            indexes[index_name] = {
                "table": table_name,
                "unique": int(row[2]),
                "origin": str(row[3]) if len(row) > 3 else "",
                "partial": int(row[4]) if len(row) > 4 else 0,
                "columns": _index_columns(conn, index_name),
                "where": _extract_where_clause(sql_text),
            }

    conn.close()
    return SQLiteSchemaSnapshot(tables=tables, indexes=indexes)


def sqlite_snapshot_from_sql(sql_text: str) -> SQLiteSchemaSnapshot:
    """Build a temp SQLite DB from SQL text and snapshot it."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        temp_db = Path(handle.name)
    try:
        conn = sqlite3.connect(temp_db)
        conn.executescript(sql_text)
        conn.commit()
        conn.close()
        return sqlite_snapshot_from_db(temp_db)
    finally:
        if temp_db.exists():
            temp_db.unlink()


def sqlite_snapshot_from_sql_file(schema_path: Path) -> SQLiteSchemaSnapshot:
    """Build a SQLite snapshot from a schema SQL file."""
    return sqlite_snapshot_from_sql(schema_path.read_text(encoding="utf-8"))


def sqlite_snapshot_from_alembic() -> SQLiteSchemaSnapshot:
    """Build a SQLite snapshot by migrating a temp DB to Alembic head."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        temp_db = Path(handle.name)
    try:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{temp_db}"
        result = subprocess.run(
            [_python_executable(), "-m", "alembic", "upgrade", "head"],
            cwd=_project_root(),
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "alembic failed")
        return sqlite_snapshot_from_db(temp_db)
    finally:
        if temp_db.exists():
            temp_db.unlink()


def compare_sqlite_snapshots(
    actual: SQLiteSchemaSnapshot,
    expected: SQLiteSchemaSnapshot,
) -> SQLiteSchemaDiff:
    """Compare two SQLite schema snapshots."""
    actual_tables = set(actual.tables)
    expected_tables = set(expected.tables)
    actual_indexes = set(actual.indexes)
    expected_indexes = set(expected.indexes)

    column_diffs: dict[str, dict[str, Any]] = {}
    for table_name in sorted(actual_tables & expected_tables):
        actual_columns = {column["name"]: column for column in actual.tables[table_name]}
        expected_columns = {column["name"]: column for column in expected.tables[table_name]}
        if actual_columns != expected_columns:
            column_diffs[table_name] = {
                "actual": list(actual_columns.values()),
                "expected": list(expected_columns.values()),
            }

    index_definition_diffs: dict[str, dict[str, str]] = {}
    for index_name in sorted(actual_indexes & expected_indexes):
        if _index_signature(actual.indexes[index_name]) != _index_signature(
            expected.indexes[index_name]
        ):
            index_definition_diffs[index_name] = {
                "actual": json.dumps(actual.indexes[index_name], ensure_ascii=True, sort_keys=True),
                "expected": json.dumps(
                    expected.indexes[index_name], ensure_ascii=True, sort_keys=True
                ),
            }

    unmatched_actual = {
        name: index for name, index in actual.indexes.items() if name not in expected.indexes
    }
    unmatched_expected = {
        name: index for name, index in expected.indexes.items() if name not in actual.indexes
    }

    actual_by_signature: dict[str, list[str]] = {}
    for name, index in unmatched_actual.items():
        actual_by_signature.setdefault(_index_signature(index), []).append(name)

    expected_by_signature: dict[str, list[str]] = {}
    for name, index in unmatched_expected.items():
        expected_by_signature.setdefault(_index_signature(index), []).append(name)

    for signature in set(actual_by_signature) & set(expected_by_signature):
        matched = min(len(actual_by_signature[signature]), len(expected_by_signature[signature]))
        for _ in range(matched):
            del unmatched_actual[actual_by_signature[signature].pop()]
            del unmatched_expected[expected_by_signature[signature].pop()]

    return SQLiteSchemaDiff(
        tables_only_in_actual=sorted(actual_tables - expected_tables),
        tables_only_in_expected=sorted(expected_tables - actual_tables),
        column_diffs=column_diffs,
        indexes_only_in_actual=sorted(unmatched_actual),
        indexes_only_in_expected=sorted(unmatched_expected),
        index_definition_diffs=index_definition_diffs,
    )


def render_sqlite_diff(diff: SQLiteSchemaDiff) -> str:
    """Render a readable SQLite schema diff."""
    if not diff.has_drift():
        return "SQLite schema snapshots match."

    lines: list[str] = []
    if diff.tables_only_in_actual:
        lines.append("Tables only in actual: " + ", ".join(diff.tables_only_in_actual))
    if diff.tables_only_in_expected:
        lines.append("Tables only in expected: " + ", ".join(diff.tables_only_in_expected))
    if diff.indexes_only_in_actual:
        lines.append("Indexes only in actual: " + ", ".join(diff.indexes_only_in_actual))
    if diff.indexes_only_in_expected:
        lines.append("Indexes only in expected: " + ", ".join(diff.indexes_only_in_expected))
    for table_name, details in diff.column_diffs.items():
        actual_columns = [column["name"] for column in details["actual"]]
        expected_columns = [column["name"] for column in details["expected"]]
        lines.append(
            f"Column drift in table {table_name}: "
            f"actual={actual_columns} expected={expected_columns}"
        )
    for index_name, details in diff.index_definition_diffs.items():
        lines.append(f"Index definition drift in {index_name}")
    return "\n".join(lines)


def compare_postgres_schema_text(actual_sql: str, expected_sql: str) -> list[str]:
    """Return a unified diff between normalized PostgreSQL schema texts."""
    actual = normalize_sql_text(actual_sql).splitlines(keepends=True)
    expected = normalize_sql_text(expected_sql).splitlines(keepends=True)
    return list(
        difflib.unified_diff(
            expected,
            actual,
            fromfile="schema/schema-postgres.sql",
            tofile="pg_dump_cleaned.sql",
        )
    )


def run_alembic_upgrade(database_url: str) -> None:
    """Run Alembic migrations against the provided database URL."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [_python_executable(), "-m", "alembic", "upgrade", "head"],
        cwd=_project_root(),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "alembic failed")


def dump_postgres_schema(database_url: str) -> str:
    """Run pg_dump against a PostgreSQL database URL."""
    result = subprocess.run(
        ["pg_dump", "-d", database_url, "--schema-only", "--no-owner", "--no-privileges"],
        cwd=_project_root(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "pg_dump failed")
    return result.stdout


def build_clean_postgres_schema(database_url: str, *, migrate: bool = True) -> str:
    """Migrate a PostgreSQL database and return the cleaned schema dump."""
    if migrate:
        run_alembic_upgrade(database_url)
    return clean_postgres_schema(dump_postgres_schema(database_url))


def write_schema_snapshots(
    clean_postgres_sql: str, schema_dir: Path | None = None
) -> tuple[Path, Path]:
    """Write PostgreSQL and SQLite schema snapshots to disk."""
    target_dir = schema_dir or (_project_root() / "schema")
    target_dir.mkdir(parents=True, exist_ok=True)

    pg_path = target_dir / "schema-postgres.sql"
    sqlite_path = target_dir / "schema-sqlite.sql"

    pg_path.write_text(normalize_sql_text(clean_postgres_sql), encoding="utf-8")
    sqlite_path.write_text(convert_to_sqlite(clean_postgres_sql), encoding="utf-8")
    return pg_path, sqlite_path


def sqlite_snapshot_from_postgres_schema_file(schema_path: Path) -> SQLiteSchemaSnapshot:
    """Convert committed PostgreSQL schema to SQLite and snapshot it."""
    postgres_sql = schema_path.read_text(encoding="utf-8")
    return sqlite_snapshot_from_sql(convert_to_sqlite(postgres_sql))


def sqlite_diff_to_dict(diff: SQLiteSchemaDiff) -> dict[str, Any]:
    """Serialize a SQLite diff for JSON output."""
    payload = asdict(diff)
    payload["has_drift"] = diff.has_drift()
    return payload
