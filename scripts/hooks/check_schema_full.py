#!/usr/bin/env python3
"""
Check if schema-init-*.sql contains all tables created by alembic migrations.

This script scans all migration files to extract table names from op.create_table()
calls, then verifies that schema-init-*.sql contains CREATE TABLE statements for
all these tables.

Usage:
    python3 scripts/hooks/check_schema_full.py

Exit codes:
    0 - All tables present
    1 - Missing tables detected
"""

import re
import sys
from pathlib import Path


def extract_tables_from_migrations(migrations_dir: Path) -> set[str]:
    """Extract all table names from migration files."""
    tables = set()

    # Tables to exclude (temporary tables used in migrations)
    EXCLUDE_TABLES = {
        # Temporary tables created during data migration
        "sessions_new",
        "sessions_old",
        "quota_alerts_new",
        "quota_alerts_old",
        "quota_usage_new",
        "quota_usage_old",
        "tenant_usage_new",
        "tenant_usage_old",
    }

    for migration_file in migrations_dir.glob("*.py"):
        content = migration_file.read_text()

        # Find op.create_table("table_name", ...) patterns
        # Handle both single string and function call formats
        matches = re.findall(r'op\.create_table\(\s*["\'](\w+)["\']', content)
        tables.update(matches)

        # Also handle op.create_table(table_name, ...) where table_name is a variable
        # But we need to be careful - usually it's a direct string

    # Remove temporary tables
    tables -= EXCLUDE_TABLES

    return tables


def extract_tables_from_schema(schema_file: Path) -> set[str]:
    """Extract all table names from schema.sql file."""
    tables = set()

    content = schema_file.read_text()

    # Find CREATE TABLE table_name patterns
    matches = re.findall(r'CREATE TABLE\s+(\w+)\s*\(', content, re.IGNORECASE)
    tables.update(matches)

    return tables


def main():
    project_root = Path(__file__).parent.parent.parent
    migrations_dir = project_root / "migrations" / "versions"
    schema_dir = project_root / "schema"

    # Check schema-init files (for fresh installation)
    schema_init_pg = schema_dir / "schema-init-postgres.sql"
    schema_init_sqlite = schema_dir / "schema-init-sqlite.sql"

    # Check if schema-init files exist
    if not schema_init_pg.exists():
        print("ERROR: schema-init-postgres.sql not found")
        print("Please run: python3 scripts/generate_schema.py or package.sh")
        return 1

    # Extract tables from migrations
    migration_tables = extract_tables_from_migrations(migrations_dir)

    # Extract tables from schema-init
    schema_tables = extract_tables_from_schema(schema_init_pg)

    # Find missing tables
    missing_tables = migration_tables - schema_tables

    if missing_tables:
        print("=" * 60)
        print("ERROR: schema-init-postgres.sql is NOT complete!")
        print("=" * 60)
        print(f"Migration files define {len(migration_tables)} tables")
        print(f"schema-init-postgres.sql has {len(schema_tables)} tables")
        print(f"Missing {len(missing_tables)} tables:")
        for table in sorted(missing_tables):
            print(f"  - {table}")
        print()
        print("Please regenerate schema:")
        print("  python3 scripts/generate_schema.py")
        print("  or")
        print("  ./scripts/install-central/package-method/package.sh --generate-schema")
        return 1

    print(f"OK: schema-init-postgres.sql contains all {len(schema_tables)} tables")

    # Also check schema-init-sqlite if exists
    if schema_init_sqlite.exists():
        sqlite_tables = extract_tables_from_schema(schema_init_sqlite)
        sqlite_missing = migration_tables - sqlite_tables
        if sqlite_missing:
            print(f"WARNING: schema-init-sqlite.sql missing {len(sqlite_missing)} tables")
            for table in sorted(sqlite_missing):
                print(f"  - {table}")
        else:
            print(f"OK: schema-init-sqlite.sql contains all {len(sqlite_tables)} tables")

    return 0


if __name__ == "__main__":
    sys.exit(main())