"""Add tool_account_mapping_rules table for automatic mapping.

This migration:
1. Creates tool_account_mapping_rules table for storing mapping rules
2. Supports multiple match types: exact, prefix, suffix, contains, regex
3. Enables automatic tool account mapping based on rules

Benefits:
- Reduce manual mapping work
- Auto-match by username/email in sender_name
- Custom rules for organizational patterns

Revision ID: 026
Revises: 025
Create Date: 2026-06-22
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "026_add_tool_account_mapping_rules"
down_revision = (
    "040_normalize_tool_names_case_insensitive",
    "20260618_064_fix_quota_unit_inconsistency",
)
branch_labels = None
depends_on = None


def upgrade():
    """Add tool_account_mapping_rules table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # Create tool_account_mapping_rules table
    table_args = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pattern", sa.String(255), nullable=False),
        sa.Column("match_type", sa.String(20), nullable=False, server_default="exact"),
        sa.Column("tool_type", sa.String(50), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_auto", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    ]

    # Add unique constraint for (user_id, pattern, match_type)
    if is_postgresql:
        table_args.append(
            sa.UniqueConstraint("user_id", "pattern", "match_type", name="uq_mapping_rule_user_pattern")
        )

    op.create_table("tool_account_mapping_rules", *table_args)

    if not is_postgresql:
        # SQLite: create unique constraint separately
        op.create_unique_constraint(
            "uq_mapping_rule_user_pattern", "tool_account_mapping_rules", ["user_id", "pattern", "match_type"]
        )

    # Create indexes for efficient querying
    op.create_index("idx_mapping_rules_user_id", "tool_account_mapping_rules", ["user_id"])
    op.create_index("idx_mapping_rules_active", "tool_account_mapping_rules", ["is_active", "priority"])

    # Add auto_mapping_enabled column to users table (optional per-user setting)
    op.add_column(
        "users",
        sa.Column("auto_mapping_enabled", sa.Boolean(), nullable=True, server_default="1")
    )


def downgrade():
    """Remove tool_account_mapping_rules table."""
    op.drop_index("idx_mapping_rules_active", "tool_account_mapping_rules")
    op.drop_index("idx_mapping_rules_user_id", "tool_account_mapping_rules")
    op.drop_table("tool_account_mapping_rules")
    op.drop_column("users", "auto_mapping_enabled")