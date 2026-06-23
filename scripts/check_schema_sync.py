#!/usr/bin/env python3
"""Validate committed schema snapshots against generated structures."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
SHARED_DIR = os.path.join(SCRIPT_DIR, "shared")
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

import schema_sync


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postgres-url",
        help="disposable PostgreSQL database URL used for 'alembic head + pg_dump' validation",
    )
    parser.add_argument(
        "--skip-postgres-migrate",
        action="store_true",
        help="skip running Alembic before pg_dump when --postgres-url is provided",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="always exit 0 and print drift as warnings",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON output",
    )
    return parser


def main() -> int:
    """Run schema sync checks."""
    args = build_parser().parse_args()
    project_root = Path(__file__).resolve().parent.parent
    schema_dir = project_root / "schema"
    pg_path = schema_dir / "schema-postgres.sql"
    sqlite_path = schema_dir / "schema-sqlite.sql"

    committed_sqlite = schema_sync.sqlite_snapshot_from_sql_file(sqlite_path)
    sqlite_from_pg = schema_sync.sqlite_snapshot_from_postgres_schema_file(pg_path)
    sqlite_from_alembic = schema_sync.sqlite_snapshot_from_alembic()

    sqlite_from_pg_diff = schema_sync.compare_sqlite_snapshots(committed_sqlite, sqlite_from_pg)
    sqlite_from_alembic_diff = schema_sync.compare_sqlite_snapshots(
        committed_sqlite, sqlite_from_alembic
    )

    postgres_diff_lines: list[str] = []
    postgres_status = "skipped"
    postgres_error = ""
    if args.postgres_url:
        try:
            clean_pg = schema_sync.build_clean_postgres_schema(
                args.postgres_url,
                migrate=not args.skip_postgres_migrate,
            )
            postgres_diff_lines = schema_sync.compare_postgres_schema_text(
                clean_pg,
                pg_path.read_text(encoding="utf-8"),
            )
            postgres_status = "drift" if postgres_diff_lines else "ok"
        except Exception as exc:  # pragma: no cover - depends on local pg tooling
            postgres_status = "error"
            postgres_error = str(exc)

    has_failure = any(
        (
            sqlite_from_pg_diff.has_drift(),
            sqlite_from_alembic_diff.has_drift(),
            postgres_status in {"drift", "error"},
        )
    )

    if args.json:
        print(
            json.dumps(
                {
                    "sqlite_from_committed_postgres": schema_sync.sqlite_diff_to_dict(
                        sqlite_from_pg_diff
                    ),
                    "sqlite_from_alembic_head": schema_sync.sqlite_diff_to_dict(
                        sqlite_from_alembic_diff
                    ),
                    "postgres_from_url": {
                        "status": postgres_status,
                        "error": postgres_error,
                        "diff": postgres_diff_lines,
                    },
                    "has_failure": has_failure,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    else:
        print("[sqlite] committed schema-postgres.sql -> schema-sqlite.sql")
        print(schema_sync.render_sqlite_diff(sqlite_from_pg_diff))
        print()
        print("[sqlite] alembic head -> committed schema-sqlite.sql")
        print(schema_sync.render_sqlite_diff(sqlite_from_alembic_diff))
        print()
        print("[postgres] alembic head + pg_dump -> committed schema-postgres.sql")
        if postgres_status == "skipped":
            print("Skipped: provide --postgres-url to run PostgreSQL validation.")
        elif postgres_status == "error":
            print(f"Error: {postgres_error}")
        elif postgres_diff_lines:
            print("".join(postgres_diff_lines[:200]).rstrip())
        else:
            print("PostgreSQL schema snapshot matches.")

    return 0 if args.warn_only or not has_failure else 1


if __name__ == "__main__":
    raise SystemExit(main())
