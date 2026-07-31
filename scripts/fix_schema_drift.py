#!/usr/bin/env python3
"""
Fix schema drift by removing runtime-created tables and indexes.

This script removes tables that are created at runtime (like alert_creation_failures)
from the committed schema files, since they should not be in migration-managed schemas.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "schema"

def remove_alert_creation_failures_from_postgres():
    """Remove alert_creation_failures table and related objects from PostgreSQL schema."""
    pg_schema_path = SCHEMA_DIR / "schema-postgres.sql"
    content = pg_schema_path.read_text()

    # Remove CREATE TABLE alert_creation_failures
    content = re.sub(
        r'CREATE TABLE alert_creation_failures \([^;]+\);\n\n',
        '',
        content,
        flags=re.DOTALL
    )

    # Remove CREATE SEQUENCE alert_creation_failures_id_seq
    content = re.sub(
        r'CREATE SEQUENCE alert_creation_failures_id_seq[^;]+;\n\n',
        '',
        content,
        flags=re.DOTALL
    )

    # Remove ALTER SEQUENCE ... OWNED BY alert_creation_failures.id
    content = re.sub(
        r'ALTER SEQUENCE alert_creation_failures_id_seq OWNED BY alert_creation_failures\.id;\n',
        '',
        content
    )

    # Remove ALTER TABLE ONLY alert_creation_failures ... SET DEFAULT
    content = re.sub(
        r"ALTER TABLE ONLY alert_creation_failures[^;]+;\n",
        '',
        content
    )

    # Remove ALTER TABLE ONLY alert_creation_failures ... ADD CONSTRAINT
    content = re.sub(
        r'ALTER TABLE ONLY alert_creation_failures\n\s+ADD CONSTRAINT alert_creation_failures_pkey[^;]+;\n',
        '',
        content
    )

    pg_schema_path.write_text(content)
    print(f"Updated {pg_schema_path}")

def remove_alert_creation_failures_from_sqlite():
    """Remove alert_creation_failures table from SQLite schema."""
    sqlite_schema_path = SCHEMA_DIR / "schema-sqlite.sql"
    content = sqlite_schema_path.read_text()

    # Remove CREATE TABLE alert_creation_failures
    content = re.sub(
        r'CREATE TABLE alert_creation_failures \([^;]+\);\n',
        '',
        content,
        flags=re.DOTALL
    )

    sqlite_schema_path.write_text(content)
    print(f"Updated {sqlite_schema_path}")

def main():
    """Fix schema drift."""
    print("Removing runtime-created tables from schema files...")
    remove_alert_creation_failures_from_postgres()
    remove_alert_creation_failures_from_sqlite()
    print("Done. Run 'python scripts/check_schema_sync.py' to verify.")

if __name__ == "__main__":
    main()