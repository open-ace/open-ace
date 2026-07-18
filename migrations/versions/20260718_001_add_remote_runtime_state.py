"""Add persistent remote runtime state tables.

Revision ID: 20260718_001_add_remote_runtime_state
Revises: 20260717_004_scope_usage_and_audit_to_tenant
Create Date: 2026-07-18

Issue: #1782

Externalize the shareable portions of the remote workspace runtime state so
HTTP-polling agents and SSE reconnects do not require every control-plane
request to hit the same web process.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_001_add_remote_runtime_state"
down_revision: str | None = "20260717_004_scope_usage_and_audit_to_tenant"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create persistent remote-runtime command and output queues.

    The schema.sql snapshots also define these tables (so freshly-bootstrapped
    databases already have them), and the runtime can create them on first
    use. Guard each create_table/create_index against the existing schema, the
    same way 20260717_001 does, so this migration no-ops cleanly on databases
    that already have the tables (Issue #1782).
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "remote_runtime_commands" not in existing_tables:
        op.create_table(
            "remote_runtime_commands",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("command_id", sa.String(length=64), nullable=False, unique=True),
            sa.Column("machine_id", sa.Text(), nullable=False),
            sa.Column("session_id", sa.Text(), nullable=True),
            sa.Column("command_type", sa.Text(), nullable=False, server_default=""),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("response_payload", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=True,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("responded_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
        )

    commands_indexes = {idx["name"] for idx in inspector.get_indexes("remote_runtime_commands")}
    if "idx_remote_runtime_commands_machine_status" not in commands_indexes:
        op.create_index(
            "idx_remote_runtime_commands_machine_status",
            "remote_runtime_commands",
            ["machine_id", "status", "id"],
            unique=False,
        )
    if "idx_remote_runtime_commands_expires" not in commands_indexes:
        op.create_index(
            "idx_remote_runtime_commands_expires",
            "remote_runtime_commands",
            ["expires_at"],
            unique=False,
        )

    if "remote_runtime_outputs" not in existing_tables:
        op.create_table(
            "remote_runtime_outputs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("event_index", sa.Integer(), nullable=False),
            sa.Column("stream", sa.Text(), nullable=False, server_default="stdout"),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=True,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "session_id", "event_index", name="uq_remote_runtime_outputs_session_index"
            ),
        )

    outputs_indexes = {idx["name"] for idx in inspector.get_indexes("remote_runtime_outputs")}
    if "idx_remote_runtime_outputs_session_index" not in outputs_indexes:
        op.create_index(
            "idx_remote_runtime_outputs_session_index",
            "remote_runtime_outputs",
            ["session_id", "event_index"],
            unique=False,
        )
    if "idx_remote_runtime_outputs_expires" not in outputs_indexes:
        op.create_index(
            "idx_remote_runtime_outputs_expires",
            "remote_runtime_outputs",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    """Drop persistent remote-runtime state tables."""
    op.drop_index("idx_remote_runtime_outputs_expires", table_name="remote_runtime_outputs")
    op.drop_index("idx_remote_runtime_outputs_session_index", table_name="remote_runtime_outputs")
    op.drop_table("remote_runtime_outputs")
    op.drop_index("idx_remote_runtime_commands_expires", table_name="remote_runtime_commands")
    op.drop_index(
        "idx_remote_runtime_commands_machine_status", table_name="remote_runtime_commands"
    )
    op.drop_table("remote_runtime_commands")
