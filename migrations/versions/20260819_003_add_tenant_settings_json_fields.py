"""Add allowed_tools and roi_assumptions columns to tenant_settings

Revision ID: 20260819_003
Revises: 20260819_002
Create Date: 2026-08-19

Issue: #2788

This migration adds JSON columns for allowed_tools and roi_assumptions
to the tenant_settings table, completing the schema alignment with
the TenantSettings model.

Columns added to tenant_settings:
- allowed_tools: JSONB/TEXT with default tool list
- roi_assumptions: JSONB/TEXT nullable for ROI configuration
"""

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260819_003"
down_revision: str | None = "20260819_002"
branch_labels: str | None = None
depends_on: str | None = None

# Default allowed_tools matching TenantSettings model
DEFAULT_ALLOWED_TOOLS = ["claude", "qwen", "openclaw", "codex", "zcode"]


def _is_postgresql() -> bool:
    """Check if the database is PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Add allowed_tools and roi_assumptions columns to tenant_settings."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Get existing columns
    ts_columns = {col["name"] for col in inspector.get_columns("tenant_settings")}

    # =========================================================================
    # 1. Add allowed_tools column
    # =========================================================================
    if "allowed_tools" not in ts_columns:
        log.info("Adding allowed_tools column to tenant_settings")
        if _is_postgresql():
            op.add_column(
                "tenant_settings",
                sa.Column(
                    "allowed_tools",
                    sa.dialects.postgresql.JSONB,
                    nullable=True,
                    server_default=sa.text(f"'{json.dumps(DEFAULT_ALLOWED_TOOLS)}'::jsonb"),
                ),
            )
        else:
            op.add_column(
                "tenant_settings",
                sa.Column(
                    "allowed_tools",
                    sa.Text,
                    nullable=True,
                    server_default=json.dumps(DEFAULT_ALLOWED_TOOLS),
                ),
            )

    # =========================================================================
    # 2. Add roi_assumptions column
    # =========================================================================
    if "roi_assumptions" not in ts_columns:
        log.info("Adding roi_assumptions column to tenant_settings")
        if _is_postgresql():
            op.add_column(
                "tenant_settings",
                sa.Column(
                    "roi_assumptions",
                    sa.dialects.postgresql.JSONB,
                    nullable=True,
                    server_default=sa.text("NULL"),
                ),
            )
        else:
            op.add_column(
                "tenant_settings",
                sa.Column(
                    "roi_assumptions",
                    sa.Text,
                    nullable=True,
                    server_default=None,
                ),
            )

    # =========================================================================
    # 3. Backfill data from tenants.settings JSON column
    # =========================================================================
    log.info("Backfilling allowed_tools and roi_assumptions from tenants.settings")

    if _is_postgresql():
        # PostgreSQL: Use JSONB operators to extract fields
        op.execute("""
            UPDATE tenant_settings ts
            SET allowed_tools = COALESCE(
                t.settings::jsonb->'allowed_tools',
                to_jsonb(ARRAY['claude', 'qwen', 'openclaw', 'codex', 'zcode']::text[])
            ),
            roi_assumptions = t.settings::jsonb->'roi_assumptions'
            FROM tenants t
            WHERE t.id = ts.tenant_id
              AND t.settings IS NOT NULL
              AND (ts.allowed_tools IS NULL OR ts.roi_assumptions IS NULL)
        """)
    else:
        # SQLite: Use Python script to process each row
        connection = op.get_bind()

        # Get all tenant settings rows with their JSON settings
        result = connection.execute(sa.text("""
                SELECT ts.id, ts.tenant_id, t.settings
                FROM tenant_settings ts
                JOIN tenants t ON t.id = ts.tenant_id
                WHERE t.settings IS NOT NULL
                  AND (ts.allowed_tools IS NULL OR ts.roi_assumptions IS NULL)
            """))

        for row in result:
            settings_id = row[0]
            settings_json = row[2]

            if settings_json:
                try:
                    settings_dict = (
                        json.loads(settings_json)
                        if isinstance(settings_json, str)
                        else settings_json
                    )

                    allowed_tools = settings_dict.get("allowed_tools", DEFAULT_ALLOWED_TOOLS)
                    roi_assumptions = settings_dict.get("roi_assumptions")

                    connection.execute(
                        sa.text("""
                            UPDATE tenant_settings
                            SET allowed_tools = :allowed_tools,
                                roi_assumptions = :roi_assumptions
                            WHERE id = :id
                        """),
                        {
                            "id": settings_id,
                            "allowed_tools": json.dumps(allowed_tools),
                            "roi_assumptions": (
                                json.dumps(roi_assumptions) if roi_assumptions else None
                            ),
                        },
                    )
                except (json.JSONDecodeError, TypeError) as e:
                    log.warning(
                        f"Failed to parse settings JSON for tenant_settings.id={settings_id}: {e}"
                    )

    log.info("Migration completed successfully")


def downgrade() -> None:
    """Remove allowed_tools and roi_assumptions columns from tenant_settings."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    ts_columns = {col["name"] for col in inspector.get_columns("tenant_settings")}

    if _is_postgresql():
        if "roi_assumptions" in ts_columns:
            log.info("Dropping roi_assumptions column from tenant_settings")
            op.drop_column("tenant_settings", "roi_assumptions")

        if "allowed_tools" in ts_columns:
            log.info("Dropping allowed_tools column from tenant_settings")
            op.drop_column("tenant_settings", "allowed_tools")
    else:
        # SQLite requires batch_alter_table for DROP COLUMN
        with op.batch_alter_table("tenant_settings") as batch_op:
            if "roi_assumptions" in ts_columns:
                log.info("Dropping roi_assumptions column from tenant_settings")
                batch_op.drop_column("roi_assumptions")

            if "allowed_tools" in ts_columns:
                log.info("Dropping allowed_tools column from tenant_settings")
                batch_op.drop_column("allowed_tools")
