#!/usr/bin/env python3
"""
Generate Database Schema from PostgreSQL Dump

This script cleans up the pg_dump output for PostgreSQL and generates
a compatible SQLite schema.

Usage:
    python3 scripts/generate_schema.py
"""

import os
import sys
import re
from pathlib import Path


def get_project_root():
    """Get the project root directory."""
    script_dir = Path(__file__).parent
    return script_dir.parent


def clean_postgres_schema(input_sql):
    """Clean up pg_dump output for use as installation schema."""
    lines = input_sql.split('\n')
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
        'Owner:',
        'OWNER TO',
        'OWNER',
        '\\restrict',
        'Dumped from database',
        'Dumped by pg_dump',
    ]
    
    # Lines to skip if they start with certain patterns
    skip_start_patterns = [
        '-- Name:',
        '-- Type:',
        '-- Schema:',
    ]
    
    # Tables to include (skip alembic_version as it's managed by alembic stamp)
    skip_tables = ['alembic_version']
    
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
        table_match = re.search(r'CREATE TABLE(?: IF NOT EXISTS)? (?:public\.)?(\w+)', line)
        if table_match:
            current_table = table_match.group(1)
            if current_table in skip_tables:
                # Skip this entire table definition
                while i < len(lines) and not lines[i].rstrip().endswith(';'):
                    i += 1
                i += 1
                current_table = None
                continue
        
        # Clean public. prefix
        line = line.replace('public.', '')
        
        # Handle CREATE SEQUENCE - keep for PostgreSQL
        if re.match(r'CREATE SEQUENCE', line):
            output_lines.append(line)
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                # Skip OWNER statements
                if not re.search(r'ALTER SEQUENCE.*OWNER', lines[i]):
                    output_lines.append(lines[i].replace('public.', ''))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace('public.', ''))
            output_lines.append("")
            i += 1
            continue
        
        # Handle CREATE TABLE
        if re.match(r'CREATE TABLE', line):
            output_lines.append(line.replace('public.', ''))
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                if not re.search(r'ALTER TABLE.*OWNER', lines[i]):
                    output_lines.append(lines[i].replace('public.', ''))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace('public.', ''))
            output_lines.append("")
            i += 1
            continue
        
        # Handle ALTER TABLE statements
        if re.match(r'ALTER TABLE', line):
            # Only keep ADD PRIMARY KEY, ADD FOREIGN KEY, ADD CONSTRAINT
            if re.search(r'ADD (PRIMARY KEY|FOREIGN KEY|CONSTRAINT)', line):
                output_lines.append(line.replace('public.', ''))
                i += 1
                while i < len(lines) and not lines[i].rstrip().endswith(';'):
                    output_lines.append(lines[i].replace('public.', ''))
                    i += 1
                if i < len(lines):
                    output_lines.append(lines[i].replace('public.', ''))
                output_lines.append("")
            else:
                # Skip other ALTER TABLE
                while i < len(lines) and not lines[i].rstrip().endswith(';'):
                    i += 1
            i += 1
            continue
        
        # Handle CREATE INDEX
        if re.match(r'CREATE(?: UNIQUE)? INDEX', line):
            output_lines.append(line.replace('public.', ''))
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                # Skip comment lines with Owner
                if re.search(r'; Owner:', lines[i]):
                    i += 1
                    continue
                output_lines.append(lines[i].replace('public.', ''))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace('public.', ''))
            output_lines.append("")
            i += 1
            continue
        
        # Handle CREATE MATERIALIZED VIEW
        if re.match(r'CREATE MATERIALIZED VIEW', line):
            output_lines.append(line.replace('public.', ''))
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                output_lines.append(lines[i].replace('public.', ''))
                i += 1
            if i < len(lines):
                output_lines.append(lines[i].replace('public.', ''))
            output_lines.append("")
            i += 1
            continue
        
        # Handle CREATE FUNCTION/TRIGGER (skip for simplicity)
        if re.match(r'CREATE (FUNCTION|TRIGGER|PROCEDURE)', line):
            while i < len(lines) and not lines[i].rstrip().endswith('$$;'):
                i += 1
            i += 1
            continue
        
        # Pass through other content
        if line.strip() and not line.startswith('--'):
            output_lines.append(line)
        
        i += 1
    
    return '\n'.join(output_lines)


def convert_to_sqlite(postgres_sql):
    """Convert PostgreSQL schema to SQLite-compatible format."""
    lines = postgres_sql.split('\n')
    output_lines = []
    
    # Header
    output_lines.append("-- Open-ACE Database Schema for SQLite")
    output_lines.append("-- Auto-generated from PostgreSQL schema")
    output_lines.append("-- DO NOT EDIT MANUALLY")
    output_lines.append("")
    
    # SQLite doesn't support: SEQUENCE, MATERIALIZED VIEW, partial indexes with WHERE,
    # INCLUDE clause, FOREIGN KEY in ALTER TABLE, ::type casting
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Skip PostgreSQL-specific setup
        if re.match(r'SET', line):
            i += 1
            continue
        
        # Skip CREATE SEQUENCE (SQLite uses AUTOINCREMENT)
        if re.match(r'CREATE SEQUENCE', line):
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                i += 1
            i += 1
            continue
        
        # Skip CREATE MATERIALIZED VIEW
        if re.match(r'CREATE MATERIALIZED VIEW', line):
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                i += 1
            i += 1
            continue
        
        # Handle CREATE TABLE
        if re.match(r'CREATE TABLE', line):
            table_lines = [line]
            i += 1
            
            # Collect full table definition
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                col_line = lines[i]
                
                # Remove ::type casting
                col_line = re.sub(r"::[a-z_]+(\([^)]*\))?", '', col_line)
                
                # Convert types
                col_line = re.sub(r'character varying\([^)]*\)', 'TEXT', col_line)
                col_line = re.sub(r'timestamp without time zone', 'TIMESTAMP', col_line)
                col_line = re.sub(r'timestamp with time zone', 'TIMESTAMP', col_line)
                col_line = re.sub(r'boolean', 'INTEGER', col_line)
                col_line = re.sub(r'double precision', 'REAL', col_line)
                col_line = re.sub(r'bigint', 'INTEGER', col_line)
                col_line = re.sub(r'smallint', 'INTEGER', col_line)
                col_line = re.sub(r'numeric\([^)]*\)', 'REAL', col_line)
                col_line = re.sub(r'decimal\([^)]*\)', 'REAL', col_line)
                
                # Handle SERIAL -> INTEGER PRIMARY KEY AUTOINCREMENT
                if re.search(r'integer\s+NOT\s+NULL\s+DEFAULT\s+nextval', col_line):
                    col_line = re.sub(
                        r'integer\s+NOT\s+NULL\s+DEFAULT\s+nextval\([^)]+\)',
                        'INTEGER PRIMARY KEY AUTOINCREMENT',
                        col_line
                    )
                    # Remove the id column line if it's redundant
                    if 'id' in col_line and 'PRIMARY KEY' in col_line:
                        col_line = col_line.replace('NOT NULL', '')
                
                # Handle DEFAULT with nextval (sequence) - remove it
                if 'nextval' in col_line:
                    col_line = re.sub(r'DEFAULT\s+nextval\([^)]+\)', '', col_line)
                
                # Clean up double spaces
                col_line = re.sub(r'  +', ' ', col_line)
                
                table_lines.append(col_line)
                i += 1
            
            # Closing semicolon
            if i < len(lines):
                closing = lines[i]
                closing = re.sub(r'::[a-z_]+', '', closing)
                table_lines.append(closing)
            
            # Process table to handle PRIMARY KEY and AUTOINCREMENT
            # Find the id column and make it PRIMARY KEY AUTOINCREMENT
            processed = []
            has_pk = False
            for tl in table_lines:
                if re.search(r'id\s+integer', tl) and not has_pk:
                    # Make id column PRIMARY KEY AUTOINCREMENT
                    tl = re.sub(r'id\s+integer(?:\s+NOT\s+NULL)?(?:\s+DEFAULT\s+nextval[^,]*)?', 
                               'id INTEGER PRIMARY KEY AUTOINCREMENT', tl)
                    has_pk = True
                processed.append(tl)
            
            output_lines.extend(processed)
            output_lines.append("")
            i += 1
            continue
        
        # Handle CREATE INDEX
        if re.match(r'CREATE(?: UNIQUE)? INDEX', line):
            idx_line = line
            
            # Remove PostgreSQL-specific: USING btree, INCLUDE, WHERE
            idx_line = re.sub(r' USING [a-z]+', '', idx_line)
            idx_line = re.sub(r' INCLUDE \([^)]+\)', '', idx_line)
            
            # Keep partial index WHERE clause but warn (SQLite supports it)
            # Actually SQLite supports WHERE in indexes
            
            output_lines.append(idx_line)
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(';'):
                idx_cont = lines[i]
                idx_cont = re.sub(r' USING [a-z]+', '', idx_cont)
                idx_cont = re.sub(r' INCLUDE \([^)]+\)', '', idx_cont)
                output_lines.append(idx_cont)
                i += 1
            if i < len(lines):
                output_lines.append(lines[i])
            output_lines.append("")
            i += 1
            continue
        
        # Skip ALTER TABLE ADD FOREIGN KEY (SQLite needs it in CREATE TABLE)
        if re.match(r'ALTER TABLE', line):
            if 'FOREIGN KEY' in line:
                while i < len(lines) and not lines[i].rstrip().endswith(';'):
                    i += 1
                i += 1
                continue
            elif 'PRIMARY KEY' in line:
                # Skip ALTER TABLE ADD PRIMARY KEY (handled in CREATE TABLE)
                while i < len(lines) and not lines[i].rstrip().endswith(';'):
                    i += 1
                i += 1
                continue
            else:
                # Keep other ALTER TABLE (ADD CONSTRAINT UNIQUE)
                output_lines.append(line)
                i += 1
                while i < len(lines) and not lines[i].rstrip().endswith(';'):
                    output_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    output_lines.append(lines[i])
                output_lines.append("")
                i += 1
                continue
        
        # Pass through comments and empty lines
        output_lines.append(line)
        i += 1
    
    # Remove alembic_version table (managed by alembic stamp)
    result = '\n'.join(output_lines)
    result = re.sub(
        r'CREATE TABLE alembic_version[^;]+;',
        '',
        result,
        flags=re.DOTALL
    )
    
    return result


def main():
    """Main function."""
    project_root = get_project_root()
    schema_dir = project_root / "schema"
    
    # Read the raw PostgreSQL dump
    raw_file = schema_dir / "schema-postgres.sql"
    if not raw_file.exists():
        print(f"Error: {raw_file} not found. Run pg_dump first:")
        print("  pg_dump -d <database> --schema-only > schema/schema-postgres.sql")
        return 1
    
    print("Reading PostgreSQL schema dump...")
    raw_sql = raw_file.read_text()
    
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