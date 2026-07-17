"""Add tenant scope to workspace session tables

Revision ID: 20260717_002_add_workspace_session_tenant_scope
Revises: 20260715_001_add_last_ci_failure_head_sha
Create Date: 2026-07-17

Issue: #1760

Persist tenant attribution on workspace session data and add tenant-aware
indexes so session listings, transcript reads, and future backfills can
enforce a concrete tenant boundary in the database layer instead of relying on
user_id-only filtering.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_002_add_workspace_session_tenant_scope"
down_revision: str | None = "20260715_001_add_last_ci_failure_head_sha"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Persist tenant_id on session tables and backfill existing rows."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    agent_session_columns = _column_names(inspector, "agent_sessions")
    if "tenant_id" not in agent_session_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"),
        )

    conn.execute(
        sa.text(
            """
            UPDATE agent_sessions
            SET tenant_id = COALESCE(
                (SELECT users.tenant_id FROM users WHERE users.id = agent_sessions.user_id),
                tenant_id,
                1
            )
            """
        )
    )

    agent_session_indexes = _index_names(inspector, "agent_sessions")
    if "idx_agent_sessions_tenant_user" not in agent_session_indexes:
        op.create_index(
            "idx_agent_sessions_tenant_user",
            "agent_sessions",
            ["tenant_id", "user_id"],
            unique=False,
        )
    if "idx_agent_sessions_tenant_updated" not in agent_session_indexes:
        op.create_index(
            "idx_agent_sessions_tenant_updated",
            "agent_sessions",
            ["tenant_id", "updated_at"],
            unique=False,
        )

    session_message_columns = _column_names(inspector, "session_messages")
    if "tenant_id" not in session_message_columns:
        op.add_column(
            "session_messages",
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"),
        )

    conn.execute(
        sa.text(
            """
            UPDATE session_messages
            SET tenant_id = COALESCE(
                (
                    SELECT agent_sessions.tenant_id
                    FROM agent_sessions
                    WHERE agent_sessions.session_id = session_messages.session_id
                ),
                tenant_id,
                1
            )
            """
        )
    )

    session_message_indexes = _index_names(inspector, "session_messages")
    if "idx_session_messages_tenant_session" not in session_message_indexes:
        op.create_index(
            "idx_session_messages_tenant_session",
            "session_messages",
            ["tenant_id", "session_id"],
            unique=False,
        )
    if "idx_session_messages_tenant_session_timestamp" not in session_message_indexes:
        op.create_index(
            "idx_session_messages_tenant_session_timestamp",
            "session_messages",
            ["tenant_id", "session_id", "timestamp", "id"],
            unique=False,
        )


def downgrade() -> None:
    """Remove tenant scope columns and indexes from workspace session tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    session_message_indexes = _index_names(inspector, "session_messages")
    for index_name in (
        "idx_session_messages_tenant_session_timestamp",
        "idx_session_messages_tenant_session",
    ):
        if index_name in session_message_indexes:
            op.drop_index(index_name, table_name="session_messages")

    session_message_columns = _column_names(inspector, "session_messages")
    if "tenant_id" in session_message_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("session_messages", "tenant_id")
        else:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_column("tenant_id")

    agent_session_indexes = _index_names(inspector, "agent_sessions")
    for index_name in ("idx_agent_sessions_tenant_updated", "idx_agent_sessions_tenant_user"):
        if index_name in agent_session_indexes:
            op.drop_index(index_name, table_name="agent_sessions")

    agent_session_columns = _column_names(inspector, "agent_sessions")
    if "tenant_id" in agent_session_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "tenant_id")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("tenant_id")
