#!/usr/bin/env python3
"""Fix schema files by removing pollution and applying only necessary changes."""

import re


def fix_sqlite_schema():
    """Fix SQLite schema file."""
    # Read main branch schema
    with open("/tmp/main-schema-sqlite.sql") as f:
        content = f.read()

    # Find content_filter_rules table definition
    # Add new fields after updated_at
    pattern = r"(CREATE TABLE content_filter_rules \([^)]*updated_at TIMESTAMP)(\s*\);)"
    replacement = r"""\1,
 tenant_id integer,
 source text DEFAULT 'user',
 category text DEFAULT 'custom',
 status text DEFAULT 'active',
 approved_by integer,
 approved_at TIMESTAMP,
 created_by integer,
 metadata text,
 urgency_reason text
);"""

    content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    # Add filter_rule_approvals table after content_filter_rules
    # Find the position after content_filter_rules sequence
    pattern = r"(CREATE SEQUENCE content_filter_rules_id_seq[^;]*;\s*ALTER SEQUENCE content_filter_rules_id_seq OWNED BY content_filter_rules\.id;)"
    replacement = r"""\1
CREATE TABLE filter_rule_approvals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 rule_id integer,
 approver_id integer,
 action text NOT NULL,
 comment text,
 tenant_id integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

    content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    return content


def fix_postgres_schema():
    """Fix PostgreSQL schema file."""
    # Read main branch schema
    with open("/tmp/main-schema-postgres.sql") as f:
        content = f.read()

    # Find content_filter_rules table definition
    # Add new fields after updated_at
    pattern = (
        r"(CREATE TABLE content_filter_rules \([^)]*updated_at timestamp without time zone)(\s*\);)"
    )
    replacement = r"""\1,
    tenant_id integer,
    source character varying(20) DEFAULT 'user'::character varying,
    category character varying(50) DEFAULT 'custom'::character varying,
    status character varying(20) DEFAULT 'active'::character varying,
    approved_by integer,
    approved_at timestamp without time zone,
    created_by integer,
    metadata json,
    urgency_reason text
);"""

    content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    # Add filter_rule_approvals table after content_filter_rules
    # Find the position after content_filter_rules sequence
    pattern = r"(ALTER SEQUENCE content_filter_rules_id_seq OWNED BY content_filter_rules\.id;)"
    replacement = r"""\1
CREATE TABLE filter_rule_approvals (
    id integer NOT NULL,
    rule_id integer,
    approver_id integer,
    action character varying(20) NOT NULL,
    comment text,
    tenant_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE filter_rule_approvals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE filter_rule_approvals_id_seq OWNED BY filter_rule_approvals.id;
"""

    content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    return content


if __name__ == "__main__":
    # Fix SQLite schema
    sqlite_content = fix_sqlite_schema()
    with open("schema/schema-sqlite.sql", "w") as f:
        f.write(sqlite_content)
    print("Fixed schema/schema-sqlite.sql")

    # Fix PostgreSQL schema
    postgres_content = fix_postgres_schema()
    with open("schema/schema-postgres.sql", "w") as f:
        f.write(postgres_content)
    print("Fixed schema/schema-postgres.sql")

    print("Schema files fixed successfully")
