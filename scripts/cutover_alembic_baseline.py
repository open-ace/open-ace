#!/usr/bin/env python3
"""Cut over a legacy database onto the baseline_2026_06_23 Alembic lineage."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from migrations.baseline import (
    BASELINE_REVISION,
    LEGACY_MIGRATIONS_DIR,
    read_current_revision,
    stamp_revision,
    table_exists,
)
from scripts.shared.db import _get_db_url

FORMAL_PRODUCT_TABLES = (
    "agent_tokens",
    "ai_agent_settings",
    "autonomous_workflows",
    "compliance_reports",
    "email_notification_logs",
    "registration_tokens",
    "smtp_settings",
    "tool_account_mapping_rules",
    "workflow_events",
    "workflow_milestones",
)
SENTINEL_TABLES = ("users", "agent_sessions", "session_messages")
_REVISION_RE = re.compile(r"^revision\s*:\s*str\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_REVISION_RE_FALLBACK = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override the configured DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="report actions without writing")
    return parser


def collect_legacy_revision_ids() -> set[str]:
    """Collect archived revision identifiers that are eligible for cutover."""
    revision_ids: set[str] = set()
    if not LEGACY_MIGRATIONS_DIR.exists():
        return revision_ids

    for path in LEGACY_MIGRATIONS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = _REVISION_RE.search(text) or _REVISION_RE_FALLBACK.search(text)
        if match:
            revision_ids.add(match.group(1))

    return revision_ids


def _schema_present(connection: sa.Connection) -> bool:
    return any(table_exists(connection, table_name) for table_name in SENTINEL_TABLES)


def _missing_formal_tables(connection: sa.Connection) -> list[str]:
    if connection.dialect.name != "postgresql":
        return []
    return [
        table_name
        for table_name in FORMAL_PRODUCT_TABLES
        if not table_exists(connection, table_name)
    ]


def _column_exists(connection: sa.Connection, table_name: str, column_name: str) -> bool:
    if connection.dialect.name == "postgresql":
        result = connection.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.scalar() is not None

    result = connection.execute(sa.text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(row[1] == column_name for row in result)


def ensure_system_account_column(connection: sa.Connection) -> bool:
    """Backfill users.system_account for databases that missed migration 025."""
    if not table_exists(connection, "users") or _column_exists(
        connection, "users", "system_account"
    ):
        return False

    if _column_exists(connection, "users", "linux_account"):
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(
                "ALTER TABLE users RENAME COLUMN linux_account TO system_account"
            )
        else:
            try:
                connection.exec_driver_sql(
                    "ALTER TABLE users RENAME COLUMN linux_account TO system_account"
                )
            except Exception:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN system_account TEXT")
                connection.exec_driver_sql(
                    "UPDATE users SET system_account = linux_account WHERE system_account IS NULL"
                )
        return True

    connection.exec_driver_sql("ALTER TABLE users ADD COLUMN system_account TEXT")
    return True


def ensure_session_messages_source_column(connection: sa.Connection) -> bool:
    """Backfill session_messages.source when the DB predates 20260622_001."""
    if not table_exists(connection, "session_messages"):
        return False
    if _column_exists(connection, "session_messages", "source"):
        return False

    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "ALTER TABLE session_messages ADD COLUMN source TEXT DEFAULT ''::text NOT NULL"
        )
    else:
        connection.exec_driver_sql(
            "ALTER TABLE session_messages ADD COLUMN source TEXT DEFAULT '' NOT NULL"
        )
    return True


def ensure_session_messages_transcript_columns(connection: sa.Connection) -> bool:
    """Add #1125/#1128 transcript columns to session_messages for legacy DBs.

    Backfills source_timestamp, external_message_id and content_blocks plus the
    supporting indexes when the database predates the baseline snapshot.
    """
    if not table_exists(connection, "session_messages"):
        return False

    changed = False
    is_postgres = connection.dialect.name == "postgresql"

    if not _column_exists(connection, "session_messages", "source_timestamp"):
        if is_postgres:
            connection.exec_driver_sql(
                "ALTER TABLE session_messages ADD COLUMN source_timestamp timestamp without time zone"
            )
        else:
            connection.exec_driver_sql(
                "ALTER TABLE session_messages ADD COLUMN source_timestamp TIMESTAMP"
            )
        changed = True

    if not _column_exists(connection, "session_messages", "external_message_id"):
        if is_postgres:
            connection.exec_driver_sql(
                "ALTER TABLE session_messages ADD COLUMN external_message_id TEXT DEFAULT ''::text NOT NULL"
            )
        else:
            connection.exec_driver_sql(
                "ALTER TABLE session_messages ADD COLUMN external_message_id TEXT DEFAULT '' NOT NULL"
            )
        changed = True

    if not _column_exists(connection, "session_messages", "content_blocks"):
        connection.exec_driver_sql("ALTER TABLE session_messages ADD COLUMN content_blocks TEXT")
        changed = True

    if is_postgres:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_session_messages_external_message_id "
            "ON session_messages USING btree (session_id, external_message_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_session_messages_source "
            "ON session_messages USING btree (session_id, source)"
        )
    else:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_session_messages_external_message_id "
            "ON session_messages (session_id, external_message_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_session_messages_source "
            "ON session_messages (session_id, source)"
        )
    return changed


def ensure_users_auto_mapping_enabled(connection: sa.Connection) -> bool:
    """Backfill users.auto_mapping_enabled introduced by mapping rules support."""
    if not table_exists(connection, "users") or _column_exists(
        connection, "users", "auto_mapping_enabled"
    ):
        return False

    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN auto_mapping_enabled BOOLEAN DEFAULT true"
        )
    else:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN auto_mapping_enabled INTEGER DEFAULT 1"
        )
    return True


def ensure_tool_account_mapping_rules_table(connection: sa.Connection) -> bool:
    """Create tool_account_mapping_rules if it is missing from a legacy schema."""
    if table_exists(connection, "tool_account_mapping_rules"):
        return False

    if connection.dialect.name == "postgresql":
        statements = [
            """
            CREATE TABLE tool_account_mapping_rules (
                id integer NOT NULL,
                user_id integer NOT NULL,
                pattern character varying(255) NOT NULL,
                match_type character varying(20) DEFAULT 'exact'::character varying NOT NULL,
                tool_type character varying(50),
                priority integer DEFAULT 0 NOT NULL,
                is_auto boolean DEFAULT true NOT NULL,
                is_active boolean DEFAULT true NOT NULL,
                description character varying(255),
                created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE SEQUENCE tool_account_mapping_rules_id_seq
                START WITH 1
                INCREMENT BY 1
                NO MINVALUE
                NO MAXVALUE
                CACHE 1
            """,
            "ALTER SEQUENCE tool_account_mapping_rules_id_seq OWNED BY tool_account_mapping_rules.id",
            """
            ALTER TABLE ONLY tool_account_mapping_rules
            ALTER COLUMN id SET DEFAULT nextval('tool_account_mapping_rules_id_seq'::regclass)
            """,
            """
            ALTER TABLE ONLY tool_account_mapping_rules
            ADD CONSTRAINT tool_account_mapping_rules_pkey PRIMARY KEY (id)
            """,
            """
            ALTER TABLE ONLY tool_account_mapping_rules
            ADD CONSTRAINT uq_mapping_rule_user_pattern UNIQUE (user_id, pattern, match_type)
            """,
            """
            ALTER TABLE ONLY tool_account_mapping_rules
            ADD CONSTRAINT tool_account_mapping_rules_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            """,
            "CREATE INDEX idx_mapping_rules_user_id ON tool_account_mapping_rules USING btree (user_id)",
            """
            CREATE INDEX idx_mapping_rules_active
            ON tool_account_mapping_rules USING btree (is_active, priority)
            """,
        ]
    else:
        statements = [
            """
            CREATE TABLE tool_account_mapping_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pattern TEXT NOT NULL,
                match_type TEXT DEFAULT 'exact' NOT NULL,
                tool_type TEXT,
                priority INTEGER DEFAULT 0 NOT NULL,
                is_auto INTEGER DEFAULT 1 NOT NULL,
                is_active INTEGER DEFAULT 1 NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_mapping_rule_user_pattern UNIQUE (user_id, pattern, match_type),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX idx_mapping_rules_user_id ON tool_account_mapping_rules (user_id)",
            "CREATE INDEX idx_mapping_rules_active ON tool_account_mapping_rules (is_active, priority)",
        ]

    for statement in statements:
        connection.exec_driver_sql(statement)
    return True


def ensure_compliance_reports_table(connection: sa.Connection) -> bool:
    """Create compliance_reports if it is missing from a legacy schema."""
    if table_exists(connection, "compliance_reports"):
        return False

    if connection.dialect.name == "postgresql":
        statements = [
            """
            CREATE TABLE compliance_reports (
                id integer NOT NULL,
                report_id text NOT NULL,
                report_type text NOT NULL,
                generated_at timestamp without time zone NOT NULL,
                period_start timestamp without time zone NOT NULL,
                period_end timestamp without time zone NOT NULL,
                generated_by integer,
                tenant_id integer,
                report_data text NOT NULL,
                created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE SEQUENCE compliance_reports_id_seq
                START WITH 1
                INCREMENT BY 1
                NO MINVALUE
                NO MAXVALUE
                CACHE 1
            """,
            "ALTER SEQUENCE compliance_reports_id_seq OWNED BY compliance_reports.id",
            """
            ALTER TABLE ONLY compliance_reports
            ALTER COLUMN id SET DEFAULT nextval('compliance_reports_id_seq'::regclass)
            """,
            "ALTER TABLE ONLY compliance_reports ADD CONSTRAINT compliance_reports_pkey PRIMARY KEY (id)",
            """
            ALTER TABLE ONLY compliance_reports
            ADD CONSTRAINT compliance_reports_report_id_key UNIQUE (report_id)
            """,
        ]
    else:
        statements = [
            """
            CREATE TABLE compliance_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id TEXT UNIQUE NOT NULL,
                report_type TEXT NOT NULL,
                generated_at TIMESTAMP NOT NULL,
                period_start TIMESTAMP NOT NULL,
                period_end TIMESTAMP NOT NULL,
                generated_by INTEGER,
                tenant_id INTEGER,
                report_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ]

    for statement in statements:
        connection.exec_driver_sql(statement)
    return True


def cutover_database(connection: sa.Connection, *, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Cut over a legacy database to the baseline revision."""
    active_revisions = {BASELINE_REVISION}
    legacy_revisions = collect_legacy_revision_ids()
    actions: list[str] = []

    current_revision = read_current_revision(connection)
    if current_revision in active_revisions:
        actions.append(f"already on active revision {current_revision}")
        return False, actions

    if current_revision and current_revision not in legacy_revisions:
        raise RuntimeError(
            f"Refusing cutover from unknown revision {current_revision!r}; "
            "inspect the database before rewriting alembic_version."
        )

    if current_revision is None and not _schema_present(connection):
        actions.append("database has no recognizable application schema; skipping cutover")
        return False, actions

    if dry_run:
        if not _column_exists(connection, "users", "system_account"):
            actions.append("would backfill users.system_account")
        if not _column_exists(connection, "session_messages", "source"):
            actions.append("would backfill session_messages.source")
        if not _column_exists(connection, "session_messages", "external_message_id"):
            actions.append("would add session_messages transcript columns (#1125/#1128)")
        if not _column_exists(connection, "users", "auto_mapping_enabled"):
            actions.append("would backfill users.auto_mapping_enabled")
        if not table_exists(connection, "tool_account_mapping_rules"):
            actions.append("would create tool_account_mapping_rules")
        if not table_exists(connection, "compliance_reports"):
            actions.append("would create compliance_reports")
        actions.append(f"would stamp {BASELINE_REVISION}")
        return True, actions

    if ensure_system_account_column(connection):
        actions.append("backfilled users.system_account")
    if ensure_session_messages_source_column(connection):
        actions.append("backfilled session_messages.source")
    if ensure_session_messages_transcript_columns(connection):
        actions.append("added session_messages transcript columns (#1125/#1128)")
    if ensure_users_auto_mapping_enabled(connection):
        actions.append("backfilled users.auto_mapping_enabled")
    if ensure_tool_account_mapping_rules_table(connection):
        actions.append("created tool_account_mapping_rules")
    if ensure_compliance_reports_table(connection):
        actions.append("created compliance_reports")

    missing_formal = _missing_formal_tables(connection)
    if missing_formal:
        raise RuntimeError(
            "Formal baseline tables are still missing after cutover preparation: "
            + ", ".join(missing_formal)
        )

    stamp_revision(connection, BASELINE_REVISION)
    actions.append(f"stamped {BASELINE_REVISION}")
    return True, actions


def main() -> int:
    args = build_parser().parse_args()
    database_url = args.database_url or _get_db_url()
    engine = sa.create_engine(database_url)

    try:
        with engine.begin() as connection:
            changed, actions = cutover_database(connection, dry_run=args.dry_run)
    finally:
        engine.dispose()

    if changed:
        print("Baseline cutover complete")
    else:
        print("Baseline cutover not needed")
    for action in actions:
        print(f"- {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
