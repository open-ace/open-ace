#!/usr/bin/env python3
"""
Generate Database Schema from PostgreSQL Dump

This script cleans up the pg_dump output for PostgreSQL and generates
a compatible SQLite schema.

Usage:
    python3 scripts/generate_schema.py
"""

import re
import sys
from pathlib import Path

# Boolean field detection patterns
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

# Counter field patterns (should NOT be converted to boolean)
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
    """
    Check if a column is likely a boolean field based on naming patterns.

    Args:
        column_name: Column name to check.

    Returns:
        bool: True if column appears to be boolean.
    """
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


def get_project_root():
    """Get the project root directory."""
    script_dir = Path(__file__).parent
    return script_dir.parent


def clean_postgres_schema(input_sql):
    """Clean up pg_dump output for use as installation schema."""
    lines = input_sql.split("\n")
    output_lines = []

    # Header
    output_lines.append("-- Open-ACE Database Schema for PostgreSQL")
    output_lines.append("-- Auto-generated from pg_dump")
    output_lines.append("-- DO NOT EDIT MANUALLY")
    output_lines.append("")
    output_lines.append("-- Setup session")
    output_lines.append("SET client_encoding = 'UTF8';")
    output_lines.append("")

    # Simple skip patterns - just check if line contains these
    skip_contains = [
        "; Owner:",  # Comment line with Owner
        "OWNER TO",  # ALTER TABLE/SEQUENCE xxx OWNER TO yyy
        "\\restrict",
        "\\unrestrict",
        "Dumped from database",
        "Dumped by pg_dump",
        "set_config",  # SELECT pg_catalog.set_config('search_path', '', false)
    ]

    # Lines to skip if they start with certain patterns
    skip_start_patterns = [
        "-- Name:",
        "-- Type:",
        "-- Schema:",
        "-- --",
    ]

    # Tables to include (skip alembic_version as it's managed by alembic stamp)
    skip_tables = ["alembic_version"]

    current_table = None
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip lines containing certain strings
        skip = False
        for pattern in skip_contains:
            if pattern in line:
                skip = True
                break

        # Skip lines starting with certain patterns
        if not skip:
            for pattern in skip_start_patterns:
                if line.strip().startswith(pattern):
                    skip = True
                    break

        if skip:
            i += 1
            continue

        # Detect table name
        table_match = re.search(r"CREATE TABLE(?: IF NOT EXISTS)? (?:public\.)?(\w+)", line)
        if table_match:
            current_table = table_match.group(1)
            if current_table in skip_tables:
                # Skip this entire table definition
                while i < len(lines) and not lines[i].rstrip().endswith(";"):
                    i += 1
                i += 1
                current_table = None
                continue

        # Clean public. prefix
        line = line.replace("public.", "")

        # Handle CREATE SEQUENCE - keep for PostgreSQL
        if re.match(r"CREATE SEQUENCE", line):
            output_lines.append(line)
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                # Skip OWNER statements
                if not re.search(r"ALTER SEQUENCE.*OWNER", lines[i]):
                    output_lines.append(lines[i].replace("public.", ""))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace("public.", ""))
            output_lines.append("")
            i += 1
            continue

        # Handle CREATE TABLE
        if re.match(r"CREATE TABLE", line):
            output_lines.append(line.replace("public.", ""))
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                col_line = lines[i].replace("public.", "")
                if not re.search(r"ALTER TABLE.*OWNER", col_line):
                    # Convert integer DEFAULT 0/1 to boolean for boolean fields
                    # Match pattern: column_name integer DEFAULT 0, or column_name integer DEFAULT 1,
                    col_match = re.match(r"^\s+(\w+)\s+integer\s+DEFAULT\s+(0|1)", col_line)
                    if col_match:
                        col_name = col_match.group(1)
                        default_val = col_match.group(2)
                        if is_boolean_field(col_name):
                            # Convert to boolean
                            bool_default = "false" if default_val == "0" else "true"
                            col_line = re.sub(
                                r"integer\s+DEFAULT\s+[01]",
                                f"boolean DEFAULT {bool_default}",
                                col_line,
                            )
                    output_lines.append(col_line)
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace("public.", ""))
            output_lines.append("")
            i += 1
            continue

        # Handle ALTER TABLE statements
        if re.match(r"ALTER TABLE", line):
            # Check if target table is in skip_tables
            table_match = re.search(r"ALTER TABLE(?:\s+ONLY)?(?:\s+(?:public\.)?)?(\w+)", line)
            if table_match and table_match.group(1) in skip_tables:
                while i < len(lines) and not lines[i].rstrip().endswith(";"):
                    i += 1
                i += 1
                continue

            # Check if this line is ALTER TABLE SET DEFAULT (single-line format)
            # e.g., ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval(...)
            if re.search(r"ALTER COLUMN.*SET DEFAULT", line):
                output_lines.append(line.replace("public.", ""))
                output_lines.append("")
                i += 1
                continue

            # Look ahead to see if this is ADD PRIMARY KEY, ADD FOREIGN KEY, ADD CONSTRAINT
            # These are multi-line statements like:
            # ALTER TABLE ONLY public.users
            #     ADD CONSTRAINT users_pkey PRIMARY KEY (id);
            lookahead = i + 1
            while lookahead < len(lines) and lines[lookahead].strip().startswith("--"):
                lookahead += 1
            if lookahead < len(lines):
                next_line = lines[lookahead]
                if re.search(r"ADD (PRIMARY KEY|FOREIGN KEY|CONSTRAINT)", next_line):
                    output_lines.append(line.replace("public.", ""))
                    i += 1
                    while i < len(lines) and not lines[i].rstrip().endswith(";"):
                        if not re.search(r"OWNER", lines[i]):
                            output_lines.append(lines[i].replace("public.", ""))
                        i += 1
                    if i < len(lines):
                        output_lines.append(lines[i].replace("public.", ""))
                    output_lines.append("")
                elif re.search(r"ALTER COLUMN.*SET DEFAULT", next_line):
                    # Keep ALTER TABLE SET DEFAULT for id columns (sequence defaults)
                    output_lines.append(line.replace("public.", ""))
                    i += 1
                    while i < len(lines) and not lines[i].rstrip().endswith(";"):
                        if not re.search(r"OWNER", lines[i]):
                            output_lines.append(lines[i].replace("public.", ""))
                        i += 1
                    if i < len(lines):
                        output_lines.append(lines[i].replace("public.", ""))
                    output_lines.append("")
                else:
                    # Skip other ALTER TABLE
                    while i < len(lines) and not lines[i].rstrip().endswith(";"):
                        i += 1
            i += 1
            continue

        # Handle CREATE INDEX
        if re.match(r"CREATE(?: UNIQUE)? INDEX", line):
            output_lines.append(line.replace("public.", ""))
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                # Skip comment lines with Owner
                if re.search(r"; Owner:", lines[i]):
                    i += 1
                    continue
                output_lines.append(lines[i].replace("public.", ""))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace("public.", ""))
            output_lines.append("")
            i += 1
            continue

        # Handle CREATE MATERIALIZED VIEW
        if re.match(r"CREATE MATERIALIZED VIEW", line):
            output_lines.append(line.replace("public.", ""))
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                output_lines.append(lines[i].replace("public.", ""))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace("public.", ""))
            output_lines.append("")
            i += 1
            continue

        # Handle CREATE FUNCTION/TRIGGER (skip for simplicity)
        if re.match(r"CREATE (FUNCTION|TRIGGER|PROCEDURE)", line):
            while i < len(lines) and not lines[i].rstrip().endswith("$$;"):
                i += 1
            i += 1
            continue

        # Pass through other content
        if line.strip() and not line.startswith("--"):
            output_lines.append(line)

        i += 1

    return "\n".join(output_lines)


def _sqlite_convert_column(col_line):
    """Convert a single column definition from PostgreSQL to SQLite syntax."""
    # Remove ::type casting (e.g., 'active'::text, DEFAULT 'localhost'::character varying, ::text[])
    col_line = re.sub(r"::[a-z_]+(\([^)]*\))?(\[\])?", "", col_line)

    # Convert data types — be careful not to replace column names like 'date'
    # Must replace character varying BEFORE anything that could split it
    col_line = re.sub(r"character varying\([^)]*\)", "TEXT", col_line)
    col_line = re.sub(r"character varying", "TEXT", col_line)
    # Clean up orphaned 'varying' keyword (left after partial :: removal)
    col_line = re.sub(r"\s+varying\b", "", col_line)
    col_line = re.sub(r"timestamp without time zone", "TIMESTAMP", col_line)
    col_line = re.sub(r"timestamp with time zone", "TIMESTAMP", col_line)
    col_line = re.sub(r"double precision", "REAL", col_line)
    col_line = re.sub(r"\bbigint\b", "INTEGER", col_line)
    col_line = re.sub(r"\bsmallint\b", "INTEGER", col_line)
    col_line = re.sub(r"numeric\([^)]*\)", "REAL", col_line)
    col_line = re.sub(r"decimal\([^)]*\)", "REAL", col_line)
    # Convert 'date' type only when it appears as a type (after column name)
    col_line = re.sub(r"^(\s+\w+)\s+date\b", r"\1 TEXT", col_line)

    # Convert boolean to INTEGER
    col_line = re.sub(r"\bboolean\b", "INTEGER", col_line)
    col_line = re.sub(r"DEFAULT true", "DEFAULT 1", col_line)
    col_line = re.sub(r"DEFAULT false", "DEFAULT 0", col_line)

    # Convert DEFAULT now() to DEFAULT CURRENT_TIMESTAMP
    col_line = re.sub(r"DEFAULT now\(\)", "DEFAULT CURRENT_TIMESTAMP", col_line)

    # Handle id column with nextval sequence -> PRIMARY KEY AUTOINCREMENT
    if re.search(r"id\s+integer\s+NOT\s+NULL\s+DEFAULT\s+nextval", col_line):
        col_line = re.sub(
            r"id\s+integer\s+NOT\s+NULL\s+DEFAULT\s+nextval\([^)]+\)",
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            col_line,
        )
        return col_line

    # Remove any remaining nextval defaults
    if "nextval" in col_line:
        col_line = re.sub(r"DEFAULT\s+nextval\([^)]+\)", "", col_line)

    # Clean up double spaces
    col_line = re.sub(r"  +", " ", col_line)

    return col_line


def _sqlite_convert_check(constraint_line):
    """Convert PostgreSQL CHECK constraint to SQLite-compatible form."""
    # First remove all ::type casts (including array suffixes like ::text[])
    constraint_line = re.sub(r"::[a-z_]+(\[\])?", "", constraint_line)

    # Remove orphaned 'varying' keyword
    constraint_line = re.sub(r"\s+varying\b", "", constraint_line)

    # Replace ANY (ARRAY[...]) and ANY ((ARRAY[...])) with IN (...)
    def _any_array_to_in(match):
        return f"IN ({match.group(1)})"

    # Handle: col = ANY ((ARRAY[...]))  (users_role style with double parens)
    constraint_line = re.sub(
        r"=\s*ANY\s*\(\(ARRAY\[([^\]]+)\]\)\)",
        _any_array_to_in,
        constraint_line,
    )
    # Handle: col = ANY (ARRAY[...])  (tenants style with single parens)
    constraint_line = re.sub(
        r"=\s*ANY\s*\(ARRAY\[([^\]]+)\]\)",
        _any_array_to_in,
        constraint_line,
    )

    # Clean up extra parens around the IN clause
    constraint_line = re.sub(r"\(\((\w+)\s+IN\s*\(", r"(\1 IN (", constraint_line)
    constraint_line = re.sub(r"\(\((\w+)\)\s+IN\s*\(", r"(\1 IN (", constraint_line)

    # Balance parens — PG wraps CHECK expressions in ((...)) which may leave extras
    open_count = constraint_line.count("(")
    close_count = constraint_line.count(")")
    while close_count > open_count:
        # Remove the rightmost extra )
        idx = constraint_line.rfind(")")
        constraint_line = constraint_line[:idx] + constraint_line[idx + 1 :]
        close_count -= 1

    return constraint_line


def convert_to_sqlite(postgres_sql):
    """Convert PostgreSQL schema to SQLite-compatible format."""
    lines = postgres_sql.split("\n")
    output_lines = []

    # Pre-scan: find PRIMARY KEY constraints from ALTER TABLE statements
    # pg_dump puts PKs in ALTER TABLE, but SQLite needs them inline in CREATE TABLE
    pk_map = {}  # table_name -> pk_column_name
    for j, ln in enumerate(lines):
        pk_match = re.search(
            r"ALTER TABLE(?:\s+ONLY)?(?:\s+(?:public\.)?)?(\w+)\s+.*ADD CONSTRAINT\s+\w+\s+PRIMARY KEY\s*\((\w+)\)",
            ln,
        )
        if pk_match:
            pk_map[pk_match.group(1)] = pk_match.group(2)
        else:
            # Multi-line ALTER TABLE — check next line too
            if re.match(r"ALTER TABLE", ln):
                lookahead = j + 1
                while lookahead < len(lines) and lines[lookahead].strip().startswith("--"):
                    lookahead += 1
                if lookahead < len(lines):
                    pk_match2 = re.search(
                        r"ADD CONSTRAINT\s+\w+\s+PRIMARY KEY\s*\((\w+)\)",
                        lines[lookahead],
                    )
                    if pk_match2:
                        tbl = re.search(r"ALTER TABLE(?:\s+ONLY)?(?:\s+(?:public\.)?)?(\w+)", ln)
                        if tbl:
                            pk_map[tbl.group(1)] = pk_match2.group(1)

    # Header
    output_lines.append("-- Open-ACE Database Schema for SQLite")
    output_lines.append("-- Converted from schema-postgres.sql")
    output_lines.append("-- DO NOT EDIT MANUALLY")
    output_lines.append("")

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip input file header comments (PG header — we write our own SQLite header)
        if (
            i < 10
            and line.startswith("--")
            and ("PostgreSQL" in line or "Auto-generated" in line or "DO NOT EDIT" in line)
        ):
            i += 1
            continue

        # Skip PostgreSQL-specific SET statements
        if re.match(r"SET ", line):
            i += 1
            continue

        # Skip blank comment separators
        if line.strip() == "--" or line.strip() == "-- --":
            i += 1
            continue

        # Skip CREATE SEQUENCE (SQLite uses AUTOINCREMENT)
        if re.match(r"CREATE SEQUENCE", line):
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                i += 1
            i += 1
            continue

        # Skip ALTER SEQUENCE ... OWNED BY (part of sequence block, but may appear standalone)
        if re.match(r"ALTER SEQUENCE", line):
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                i += 1
            i += 1
            continue

        # Skip CREATE MATERIALIZED VIEW
        if re.match(r"CREATE MATERIALIZED VIEW", line):
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                i += 1
            i += 1
            continue

        # Handle CREATE TABLE
        if re.match(r"CREATE TABLE", line):
            table_lines = []
            i += 1  # skip the CREATE TABLE line, we'll rewrite it

            # Collect full table definition (columns + constraints until closing );)
            body_lines = []
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                body_lines.append(lines[i])
                i += 1
            # Skip closing ");"

            # Extract table name from original line
            table_match = re.search(
                r"CREATE TABLE(?:\s+IF NOT EXISTS)?(?:\s+(?:public\.)?)?(\w+)", line
            )
            table_name = table_match.group(1) if table_match else "unknown"

            converted_columns = []
            for bl in body_lines:
                stripped = bl.strip()

                # Skip empty lines
                if not stripped:
                    continue

                # Handle CHECK constraints
                if stripped.startswith("CONSTRAINT") and "CHECK" in stripped:
                    converted = _sqlite_convert_check(stripped)
                    converted_columns.append(f"    {converted}")
                    continue

                # Handle UNIQUE constraints inline
                if stripped.startswith("UNIQUE") or (
                    stripped.startswith("CONSTRAINT") and "UNIQUE" in stripped
                ):
                    # Extract UNIQUE(...) part
                    unique_match = re.search(r"UNIQUE\s*\([^)]+\)", stripped)
                    if unique_match:
                        converted_columns.append(f"    {unique_match.group(0)}")
                    continue

                # Convert column definitions
                converted = _sqlite_convert_column(bl)

                converted_columns.append(converted)

            # Apply PRIMARY KEY from pk_map
            pk_col = pk_map.get(table_name)
            if pk_col:
                for idx_c, col in enumerate(converted_columns):
                    # Match: "    pk_col <type> ..." — add PRIMARY KEY to the column
                    if re.match(rf"^\s+{re.escape(pk_col)}\s+", col):
                        if "integer" in col.lower():
                            # integer PK: use AUTOINCREMENT for id-like columns
                            converted_columns[idx_c] = re.sub(
                                rf"^(\s+{re.escape(pk_col)})\s+integer\s+NOT\s+NULL",
                                r"\1 INTEGER PRIMARY KEY AUTOINCREMENT",
                                col,
                            )
                            # If NOT NULL wasn't there (e.g. user_id PK)
                            if "AUTOINCREMENT" not in converted_columns[idx_c]:
                                converted_columns[idx_c] = re.sub(
                                    rf"^(\s+{re.escape(pk_col)})\s+integer",
                                    r"\1 INTEGER PRIMARY KEY",
                                    col,
                                )
                        else:
                            # Non-integer PK (e.g. login_attempts.username): just add PRIMARY KEY
                            converted_columns[idx_c] = re.sub(
                                rf"^(\s+{re.escape(pk_col)})\s+(\w+)",
                                r"\1 \2 PRIMARY KEY",
                                col,
                            )
                        break

            # Build clean CREATE TABLE statement
            table_lines.append(f"CREATE TABLE {table_name} (")
            for cl in converted_columns:
                table_lines.append(cl)
            table_lines.append(");")

            output_lines.extend(table_lines)
            output_lines.append("")
            i += 1
            continue

        # Handle CREATE INDEX
        if re.match(r"CREATE(?: UNIQUE)? INDEX", line):
            idx_parts = [line]

            # If the CREATE INDEX line already ends with ;, it's a single-line statement
            if not line.rstrip().endswith(";"):
                i += 1
                while i < len(lines) and not lines[i].rstrip().endswith(";"):
                    idx_parts.append(lines[i])
                    i += 1
                if i < len(lines):
                    idx_parts.append(lines[i])

            # Join the full index statement, then clean it
            full_idx = " ".join(p.strip() for p in idx_parts)
            # Remove trailing semicolon for processing
            full_idx = full_idx.rstrip(";")

            # Remove PostgreSQL-specific syntax
            full_idx = re.sub(r" USING [a-z]+", "", full_idx)
            full_idx = re.sub(r" INCLUDE \([^)]+\)", "", full_idx)
            # Remove ::type casts in WHERE clauses
            full_idx = re.sub(r"::[a-z_\[\]]+", "", full_idx)
            # Remove varchar_pattern_ops
            full_idx = re.sub(r"\s+varchar_pattern_ops", "", full_idx)
            # Convert boolean comparisons in WHERE: (role)::text = 'assistant'::text -> role = 'assistant'
            full_idx = re.sub(r"\((\w+)\)::text\s*=\s*'([^']*)'::text", r"\1 = '\2'", full_idx)
            # Convert (user_id IS NOT NULL) AND ... in WHERE
            full_idx = re.sub(r"\((\w+)\)::text\s*=\s*'([^']*)'", r"\1 = '\2'", full_idx)

            # Skip indexes on materialized views (not supported in SQLite)
            if re.match(r"CREATE(?: UNIQUE)? INDEX\s+\w+\s+ON\s+session_stats\b", full_idx):
                i += 1
                continue

            output_lines.append(f"{full_idx};")
            output_lines.append("")
            i += 1
            continue

        # Handle ALTER TABLE statements
        if re.match(r"ALTER TABLE", line):
            # Collect full statement
            alter_parts = [line]
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                alter_parts.append(lines[i])
                i += 1
            if i < len(lines):
                alter_parts.append(lines[i])

            full_alter = " ".join(p.strip() for p in alter_parts)

            # Skip: FOREIGN KEY, PRIMARY KEY, SET DEFAULT nextval, OWNER
            if any(kw in full_alter for kw in ["FOREIGN KEY", "OWNER TO", "nextval"]):
                i += 1
                continue
            if re.search(r"ALTER COLUMN.*SET DEFAULT", full_alter):
                i += 1
                continue
            if re.match(r"ALTER TABLE(?:\s+ONLY)?(?:\s+(?:public\.)?)?(\w+)", line):
                table_m = re.search(r"ALTER TABLE(?:\s+ONLY)?(?:\s+(?:public\.)?)?(\w+)", line)
                if table_m and table_m.group(1) == "alembic_version":
                    i += 1
                    continue

            # Keep: ADD CONSTRAINT UNIQUE, ADD CONSTRAINT PRIMARY KEY
            if "ADD CONSTRAINT" in full_alter and (
                "UNIQUE" in full_alter or "PRIMARY KEY" in full_alter
            ):
                # Extract constraint name and columns
                constraint_match = re.search(
                    r"ADD CONSTRAINT\s+(\w+)\s+(UNIQUE|PRIMARY KEY)\s*\(([^)]+)\)",
                    full_alter,
                )
                if constraint_match:
                    cname = constraint_match.group(1)
                    ctype = constraint_match.group(2)
                    cols = constraint_match.group(3)
                    if ctype == "UNIQUE":
                        output_lines.append(
                            f"CREATE UNIQUE INDEX {cname} ON {table_m.group(1)} ({cols});"
                        )
                        output_lines.append("")
                    # PRIMARY KEY handled in CREATE TABLE
                i += 1
                continue

            # Skip other ALTER TABLE
            i += 1
            continue

        # Pass through comments and empty lines
        if line.startswith("--") or not line.strip():
            output_lines.append(line)
            i += 1
            continue

        # Skip anything else that doesn't match
        i += 1

    # Post-processing: clean up alembic_version references
    result = "\n".join(output_lines)

    # Remove alembic_version table (managed by alembic stamp)
    result = re.sub(r"CREATE TABLE alembic_version[^;]+;", "", result, flags=re.DOTALL)

    # Remove consecutive blank lines (more than 1)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result


def main():
    """Main function."""
    project_root = get_project_root()
    schema_dir = project_root / "schema"

    # Run pg_dump to get the schema
    print("Running pg_dump...")
    import subprocess
    from urllib.parse import urlparse

    # Get database URL from config
    sys.path.insert(0, str(project_root / "scripts"))
    from shared import config

    db_url = config.get_database_url()

    # Parse database URL to get database name
    # Format: postgresql://user:pass@host:port/dbname or sqlite:///path/to/db
    if db_url.startswith("postgresql://"):
        parsed = urlparse(db_url)
        db_name = parsed.path.lstrip("/")
        if not db_name:
            print("Error: Could not parse database name from URL")
            return 1
    else:
        print("Error: generate_schema.py only supports PostgreSQL databases")
        return 1

    result = subprocess.run(
        ["pg_dump", "-d", db_name, "--schema-only"], capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error: pg_dump failed: {result.stderr}")
        return 1
    raw_sql = result.stdout

    print("Cleaning PostgreSQL schema...")
    clean_sql = clean_postgres_schema(raw_sql)

    # Save cleaned PostgreSQL schema
    pg_file = schema_dir / "schema-postgres.sql"
    pg_file.write_text(clean_sql)
    print(f"  Saved: {pg_file}")

    print("Converting to SQLite schema...")
    sqlite_sql = convert_to_sqlite(clean_sql)

    # Save SQLite schema
    sqlite_file = schema_dir / "schema-sqlite.sql"
    sqlite_file.write_text(sqlite_sql)
    print(f"  Saved: {sqlite_file}")

    # Stats
    print("\nStats:")
    print(f"  PostgreSQL: {len(clean_sql.splitlines())} lines")
    print(f"  SQLite: {len(sqlite_sql.splitlines())} lines")

    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
