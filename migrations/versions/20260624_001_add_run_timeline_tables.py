"""add run timeline tables

Revision ID: 20260624_001_add_run_timeline_tables
Revises: baseline_2026_06_23
Create Date: 2026-06-24

Adds the persisted remote-session run timeline:
- agent_runs         (1:1 with agent_sessions; run_id == session_id)
- agent_run_events   (append-only event stream; ordered by autoincrement id)
- agent_approvals    (durable permission request/response; keyed by request_id)

Note: ``agent_run_events.run_id`` is a plain indexed column, NOT a foreign key,
so an event can be recorded before/without a matching run row without violating
referential integrity. The runtime DDL mirror lives in
app/modules/workspace/run_timeline/__init__.py:get_ddl_statements().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260624_001_add_run_timeline_tables"
down_revision: str | None = "baseline_2026_06_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the run-timeline tables and indexes."""
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text, nullable=False, unique=True),
        sa.Column("session_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.Integer),
        sa.Column("tenant_id", sa.Integer),
        sa.Column("machine_id", sa.Text),
        sa.Column("tool_name", sa.Text),
        sa.Column("provider", sa.Text),
        sa.Column("cli_tool", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("status", sa.Text, server_default="active"),
        sa.Column("started_at", sa.TIMESTAMP),
        sa.Column("ended_at", sa.TIMESTAMP),
        sa.Column("total_tokens", sa.Integer, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer, server_default="0"),
        sa.Column("total_requests", sa.Integer, server_default="0"),
        sa.Column("metadata", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_agent_runs_session_id", "agent_runs", ["session_id"], unique=True)
    op.create_index("idx_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("idx_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text),
        sa.Column("session_id", sa.Text),
        sa.Column("event_type", sa.Text, nullable=False, server_default=""),
        sa.Column("event_subtype", sa.Text),
        sa.Column("role", sa.Text),
        sa.Column("content", sa.Text),
        sa.Column("tool_name", sa.Text),
        sa.Column("provider", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("key_id", sa.Text),
        sa.Column("user_id", sa.Integer),
        sa.Column("tenant_id", sa.Integer),
        sa.Column("machine_id", sa.Text),
        sa.Column("metadata", sa.Text),
        sa.Column("event_ts", sa.TIMESTAMP),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_run_events_session_id", "agent_run_events", ["session_id", "id"])
    op.create_index("idx_run_events_run_id", "agent_run_events", ["run_id"])
    op.create_index("idx_run_events_event_type", "agent_run_events", ["event_type"])
    op.create_index("idx_run_events_created_at", "agent_run_events", ["created_at"])

    op.create_table(
        "agent_approvals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.Text, nullable=False, unique=True),
        sa.Column("run_id", sa.Text),
        sa.Column("session_id", sa.Text),
        sa.Column("tool_name", sa.Text),
        sa.Column("request_subtype", sa.Text),
        sa.Column("request_details", sa.Text),
        sa.Column("status", sa.Text, server_default="pending"),
        sa.Column("decision", sa.Text),
        sa.Column("decided_by", sa.Integer),
        sa.Column("decided_by_name", sa.Text),
        sa.Column("decision_metadata", sa.Text),
        sa.Column("requested_at", sa.TIMESTAMP),
        sa.Column("decided_at", sa.TIMESTAMP),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_agent_approvals_session_id", "agent_approvals", ["session_id"])
    op.create_index("idx_agent_approvals_run_id", "agent_approvals", ["run_id"])
    op.create_index("idx_agent_approvals_status", "agent_approvals", ["status"])


def downgrade() -> None:
    """Drop the run-timeline tables."""
    op.drop_table("agent_approvals")
    op.drop_table("agent_run_events")
    op.drop_table("agent_runs")
