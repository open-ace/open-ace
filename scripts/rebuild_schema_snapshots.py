#!/usr/bin/env python3
"""Rebuild committed schema snapshots from a disposable PostgreSQL database."""

from __future__ import annotations

import argparse
import os
import sys

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
        required=True,
        help="disposable PostgreSQL database URL used for 'alembic head + pg_dump'",
    )
    parser.add_argument(
        "--skip-postgres-migrate",
        action="store_true",
        help="skip running Alembic before pg_dump",
    )
    return parser


def main() -> int:
    """Rebuild schema snapshots."""
    args = build_parser().parse_args()
    clean_pg = schema_sync.build_clean_postgres_schema(
        args.postgres_url,
        migrate=not args.skip_postgres_migrate,
    )
    pg_path, sqlite_path = schema_sync.write_schema_snapshots(clean_pg)
    print(f"Updated PostgreSQL schema: {pg_path}")
    print(f"Updated SQLite schema: {sqlite_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
