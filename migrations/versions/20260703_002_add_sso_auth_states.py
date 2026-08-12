"""add sso_auth_states table

Revision ID: 20260703_002_add_sso_auth_states
Revises: 20260703_001_add_require_full_review_rounds
Create Date: 2026-07-03

Promotes the "wild" ``sso_auth_states`` table into the migration lineage. Until
now this table only existed because ``SSOManager._store_auth_state`` ran a
runtime ``CREATE TABLE IF NOT EXISTS`` on every OAuth/OIDC callback — it was
never in the baseline nor any migration, so a pure-Alembic upgrade (deployments
that don't run ``ensure_all_tables()`` at startup) never got it, and the Schema
Sync CI treated it as drift once the runtime DDL was removed (Issue #237 item 4).

Columns mirror the former runtime DDL exactly so existing rows keep working.
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260703_002_add_sso_auth_states"
down_revision: str | None = "20260703_001_add_require_full_review_rounds"
branch_labels: str | None = None
depends_on: str | None = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists (PostgreSQL or SQLite)."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM information_schema.tables"
                "  WHERE table_name = :table_name"
                ")"
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    result = conn.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Create the sso_auth_states table."""
    conn = op.get_bind()

    if _table_exists(conn, "sso_auth_states"):
        log.info("sso_auth_states table already exists, skipping")
        return

    log.info("Creating sso_auth_states table")
    # The DDL is dialect-neutral (plain TEXT columns + TIMESTAMP default), so a
    # single statement works for both PostgreSQL and SQLite.
    # Note: expires_at and its index are defined here (not in a later ALTER) so
    # that schema-sync's column-dict comparison matches schema-sqlite.sql on all
    # SQLite versions. ALTER ADD COLUMN can produce a different internal default
    # representation depending on the SQLite library version (#1815 CI drift).
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TABLE sso_auth_states (
                state TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                nonce TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '600 seconds')
            )
            """
        )
    else:
        op.execute(
            """
            CREATE TABLE sso_auth_states (
                state TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                nonce TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL DEFAULT (datetime('now', '+600 seconds'))
            )
            """
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sso_auth_states_expires ON sso_auth_states(expires_at)"
    )


def downgrade() -> None:
    """Drop the sso_auth_states table."""
    conn = op.get_bind()
    if _table_exists(conn, "sso_auth_states"):
        log.info("Dropping sso_auth_states table")
        op.execute("DROP TABLE IF EXISTS sso_auth_states")
    else:
        log.info("sso_auth_states table does not exist, skipping")
