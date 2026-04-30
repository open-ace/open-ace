"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-03-21

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Create daily_usage table
    op.create_table(
        "daily_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("host_name", sa.String(), nullable=False, server_default="localhost"),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("cache_tokens", sa.Integer(), server_default="0"),
        sa.Column("request_count", sa.Integer(), server_default="0"),
        sa.Column("models_used", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("date", "tool_name", "host_name", name="uq_daily_usage_date_tool_host"),
    )

    # Create indexes for daily_usage
    op.create_index("idx_usage_date", "daily_usage", ["date"])
    op.create_index("idx_usage_tool_name", "daily_usage", ["tool_name"])
    op.create_index("idx_usage_host_name", "daily_usage", ["host_name"])

    # Create daily_messages table
    op.create_table(
        "daily_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("host_name", sa.String(), nullable=False, server_default="localhost"),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("full_entry", sa.Text(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("timestamp", sa.String(), nullable=True),
        sa.Column("sender_id", sa.String(), nullable=True),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("message_source", sa.String(), nullable=True),
        sa.Column("feishu_conversation_id", sa.String(), nullable=True),
        sa.Column("group_subject", sa.String(), nullable=True),
        sa.Column("is_group_chat", sa.Integer(), nullable=True),
        sa.Column("agent_session_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "date",
            "tool_name",
            "message_id",
            "host_name",
            name="uq_daily_messages_date_tool_msg_host",
        ),
    )

    # Create indexes for daily_messages
    op.create_index("idx_messages_date", "daily_messages", ["date"])
    op.create_index("idx_messages_tool_name", "daily_messages", ["tool_name"])
    op.create_index("idx_messages_host_name", "daily_messages", ["host_name"])
    op.create_index("idx_messages_sender_name", "daily_messages", ["sender_name"])
    op.create_index("idx_messages_sender_id", "daily_messages", ["sender_id"])
    op.create_index("idx_messages_timestamp", "daily_messages", ["timestamp"])
    op.create_index("idx_messages_role", "daily_messages", ["role"])
    op.create_index("idx_messages_date_tool", "daily_messages", ["date", "tool_name"])
    op.create_index("idx_messages_date_host", "daily_messages", ["date", "host_name"])
    op.create_index("idx_messages_date_sender", "daily_messages", ["date", "sender_name"])
    op.create_index(
        "idx_messages_date_tool_host", "daily_messages", ["date", "tool_name", "host_name"]
    )
    op.create_index(
        "idx_messages_date_timestamp", "daily_messages", ["date", sa.text("timestamp DESC")]
    )
    op.create_index(
        "idx_messages_date_role_timestamp",
        "daily_messages",
        ["date", "role", sa.text("timestamp DESC")],
    )

    # Create users table for authentication
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Integer(), server_default="0"),
        sa.Column("is_active", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_login", sa.TIMESTAMP(), nullable=True),
    )

    # Create sessions table
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("is_active", sa.Integer(), server_default="1"),
    )

    # Create index for sessions
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_session_id", "sessions", ["session_id"])


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop tables in reverse order
    op.drop_index("idx_sessions_session_id", "sessions")
    op.drop_index("idx_sessions_user_id", "sessions")
    op.drop_table("sessions")

    op.drop_table("users")

    # Drop daily_messages indexes and table
    op.drop_index("idx_messages_date_role_timestamp", "daily_messages")
    op.drop_index("idx_messages_date_timestamp", "daily_messages")
    op.drop_index("idx_messages_date_tool_host", "daily_messages")
    op.drop_index("idx_messages_date_sender", "daily_messages")
    op.drop_index("idx_messages_date_host", "daily_messages")
    op.drop_index("idx_messages_date_tool", "daily_messages")
    op.drop_index("idx_messages_role", "daily_messages")
    op.drop_index("idx_messages_timestamp", "daily_messages")
    op.drop_index("idx_messages_sender_id", "daily_messages")
    op.drop_index("idx_messages_sender_name", "daily_messages")
    op.drop_index("idx_messages_host_name", "daily_messages")
    op.drop_index("idx_messages_tool_name", "daily_messages")
    op.drop_index("idx_messages_date", "daily_messages")
    op.drop_table("daily_messages")

    # Drop daily_usage indexes and table
    op.drop_index("idx_usage_host_name", "daily_usage")
    op.drop_index("idx_usage_tool_name", "daily_usage")
    op.drop_index("idx_usage_date", "daily_usage")
    op.drop_table("daily_usage")
