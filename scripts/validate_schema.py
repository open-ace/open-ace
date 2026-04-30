#!/usr/bin/env python3
"""
Validate Database Schema for Boolean Field Consistency

This script checks that PostgreSQL schema uses boolean type for boolean fields
instead of integer DEFAULT 0/1.

Usage:
    python3 scripts/validate_schema.py
"""

import re
import sys
from pathlib import Path

# Boolean field detection patterns (same as generate_schema.py)
BOOLEAN_FIELD_PATTERNS = [
    r"^is_",  # is_admin, is_active, is_published, is_public, is_featured
    r"_enabled$",  # email_enabled, push_enabled, content_filter_enabled
    r"^allow_",  # allow_comments, allow_copy
    r"^must_",  # must_change_password
    r"^can_",  # can_edit, can_delete (future)
    r"^has_",  # has_permission, has_access (future)
]
BOOLEAN_SPECIAL_WORDS = [
    "read",
    "success",
    "acknowledged",
    "verified",
    "confirmed",
    "approved",
    "rejected",
    "completed",
    "active",
]

# Counter field patterns (should NOT be boolean)
COUNT_FIELD_PATTERNS = [
    r"_count$",  # view_count, use_count, message_count
    r"_used$",  # tokens_used, requests_used
    r"_made$",  # requests_made
    r"_limit$",  # daily_token_limit
    r"_quota$",  # monthly_token_quota
    r"^total_",  # total_tokens, total_requests, total_sessions
    r"_tokens$",  # input_tokens, output_tokens, cache_tokens
    r"_users$",  # active_users, new_users
    r"_seconds$",  # duration_seconds
    r"_requests$",  # total_requests
]


def is_boolean_field(column_name: str) -> bool:
    """Check if a column is likely a boolean field based on naming patterns."""
    # Check if it's a counter field (should NOT be boolean)
    for pattern in COUNT_FIELD_PATTERNS:
        if re.search(pattern, column_name):
            return False

    # Check boolean patterns
    for pattern in BOOLEAN_FIELD_PATTERNS:
        if re.search(pattern, column_name):
            return True

    # Check special words
    return column_name in BOOLEAN_SPECIAL_WORDS


def validate_schema(schema_file: Path) -> list:
    """
    Validate schema file for boolean field consistency.

    Args:
        schema_file: Path to schema SQL file.

    Returns:
        list: List of errors found.
    """
    errors = []

    if not schema_file.exists():
        errors.append(f"Schema file not found: {schema_file}")
        return errors

    content = schema_file.read_text()
    lines = content.split("\n")

    current_table = None

    for i, line in enumerate(lines, 1):
        # Detect table name
        table_match = re.search(r"CREATE TABLE(?: IF NOT EXISTS)? (?:public\.)?(\w+)", line)
        if table_match:
            current_table = table_match.group(1)
            continue

        # Check for integer DEFAULT 0/1 that should be boolean
        # Pattern: column_name integer DEFAULT 0, or column_name integer DEFAULT 1,
        col_match = re.match(r"^\s+(\w+)\s+integer\s+DEFAULT\s+(0|1)", line)
        if col_match:
            col_name = col_match.group(1)
            if is_boolean_field(col_name):
                errors.append(
                    f"Line {i}: {current_table}.{col_name} uses integer DEFAULT {col_match.group(2)} "
                    f"but should be boolean DEFAULT {('false' if col_match.group(2) == '0' else 'true')}"
                )

    return errors


def main():
    """Main function."""
    project_root = Path(__file__).parent.parent
    schema_dir = project_root / "schema"

    print("Validating PostgreSQL schema for boolean field consistency...")
    print()

    pg_schema = schema_dir / "schema-postgres.sql"
    errors = validate_schema(pg_schema)

    if errors:
        print(f"Found {len(errors)} errors in {pg_schema}:")
        print()
        for error in errors:
            print(f"  ERROR: {error}")
        print()
        print("Please fix these errors or run generate_schema.py to auto-fix.")
        return 1

    print(f"No errors found in {pg_schema}")
    print("Schema validation passed!")

    # Also check SQLite schema for consistency (just report, don't error)
    sqlite_schema = schema_dir / "schema-sqlite.sql"
    if sqlite_schema.exists():
        sqlite_errors = validate_schema(sqlite_schema)
        if sqlite_errors:
            print()
            print(f"Note: SQLite schema has {len(sqlite_errors)} integer boolean fields (expected)")
            print("SQLite uses INTEGER for boolean fields due to type affinity.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
