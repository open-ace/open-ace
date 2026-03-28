"""Fix data type inconsistencies

Revision ID: 012_fix_data_types
Revises: 011_add_tenant_id_to_users
Create Date: 2026-03-28

This migration fixes data type inconsistencies:
- Changes date fields from String/Text to DATE type (PostgreSQL only)
- Changes timestamp fields from String to TIMESTAMP type (PostgreSQL only)
- Changes boolean fields from Integer to BOOLEAN type (PostgreSQL only)

SQLite uses flexible type affinity, so these changes are only applied for PostgreSQL.

Tables affected:
- daily_messages: timestamp (String -> TIMESTAMP)
- daily_usage: date (String -> DATE)
- tenant_usage: date (Text -> DATE)
- quota_usage: date (Text -> DATE)
- content_filter_rules: created_at, updated_at (Text -> TIMESTAMP), is_enabled (Integer -> BOOLEAN)
- users: is_active (Integer -> BOOLEAN)
- sessions: is_active (Integer -> BOOLEAN)
- tenant_quotas: all boolean fields
- tenant_settings: all boolean fields

"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '012_fix_data_types'
down_revision: Union[str, None] = '011_add_tenant_id_to_users'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == 'postgresql'

    if not is_postgresql:
        # SQLite uses flexible type affinity, no changes needed
        return

    # ============================================
    # daily_messages: timestamp -> TIMESTAMP
    # ============================================
    op.execute("""
        ALTER TABLE daily_messages
        ALTER COLUMN timestamp TYPE TIMESTAMP
        USING CASE
            WHEN timestamp IS NULL THEN NULL
            WHEN timestamp ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' THEN
                timestamp::TIMESTAMP
            WHEN timestamp ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}' THEN
                REPLACE(timestamp, ' ', 'T')::TIMESTAMP
            ELSE NULL
        END
    """)

    # ============================================
    # daily_usage: date -> DATE
    # ============================================
    op.execute("""
        ALTER TABLE daily_usage
        ALTER COLUMN date TYPE DATE
        USING date::DATE
    """)

    # ============================================
    # tenant_usage: date -> DATE
    # ============================================
    op.execute("""
        ALTER TABLE tenant_usage
        ALTER COLUMN date TYPE DATE
        USING date::DATE
    """)

    # ============================================
    # quota_usage: date -> DATE
    # ============================================
    op.execute("""
        ALTER TABLE quota_usage
        ALTER COLUMN date TYPE DATE
        USING date::DATE
    """)

    # ============================================
    # content_filter_rules: fix types
    # ============================================
    op.execute("""
        ALTER TABLE content_filter_rules
        ALTER COLUMN created_at TYPE TIMESTAMP
        USING CASE
            WHEN created_at IS NULL THEN NULL
            WHEN created_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' THEN
                created_at::TIMESTAMP
            WHEN created_at ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}' THEN
                REPLACE(created_at, ' ', 'T')::TIMESTAMP
            ELSE NULL
        END
    """)

    op.execute("""
        ALTER TABLE content_filter_rules
        ALTER COLUMN updated_at TYPE TIMESTAMP
        USING CASE
            WHEN updated_at IS NULL THEN NULL
            WHEN updated_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' THEN
                updated_at::TIMESTAMP
            WHEN updated_at ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}' THEN
                REPLACE(updated_at, ' ', 'T')::TIMESTAMP
            ELSE NULL
        END
    """)

    # For is_enabled, we need to drop the default first, then change type
    op.execute("ALTER TABLE content_filter_rules ALTER COLUMN is_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE content_filter_rules
        ALTER COLUMN is_enabled TYPE BOOLEAN
        USING CASE WHEN is_enabled = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE content_filter_rules ALTER COLUMN is_enabled SET DEFAULT TRUE")

    # ============================================
    # users: is_active -> BOOLEAN
    # ============================================
    op.execute("ALTER TABLE users ALTER COLUMN is_active DROP DEFAULT")
    op.execute("""
        ALTER TABLE users
        ALTER COLUMN is_active TYPE BOOLEAN
        USING CASE WHEN is_active = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE users ALTER COLUMN is_active SET DEFAULT TRUE")

    # ============================================
    # sessions: is_active -> BOOLEAN
    # ============================================
    op.execute("ALTER TABLE sessions ALTER COLUMN is_active DROP DEFAULT")
    op.execute("""
        ALTER TABLE sessions
        ALTER COLUMN is_active TYPE BOOLEAN
        USING CASE WHEN is_active = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE sessions ALTER COLUMN is_active SET DEFAULT TRUE")

    # ============================================
    # tenant_quotas: boolean fields
    # Note: tenant_quotas doesn't have boolean fields in current schema
    # ============================================

    # ============================================
    # tenant_settings: boolean fields
    # ============================================
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN content_filter_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN content_filter_enabled TYPE BOOLEAN
        USING CASE WHEN content_filter_enabled = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN content_filter_enabled SET DEFAULT TRUE")

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN audit_log_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN audit_log_enabled TYPE BOOLEAN
        USING CASE WHEN audit_log_enabled = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN audit_log_enabled SET DEFAULT TRUE")

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN sso_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN sso_enabled TYPE BOOLEAN
        USING CASE WHEN sso_enabled = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN sso_enabled SET DEFAULT FALSE")

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN custom_branding DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN custom_branding TYPE BOOLEAN
        USING CASE WHEN custom_branding = 1 THEN TRUE ELSE FALSE END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN custom_branding SET DEFAULT FALSE")


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == 'postgresql'

    if not is_postgresql:
        return

    # Revert tenant_settings boolean fields
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN custom_branding DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN custom_branding TYPE INTEGER
        USING CASE WHEN custom_branding THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN custom_branding SET DEFAULT 0")

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN sso_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN sso_enabled TYPE INTEGER
        USING CASE WHEN sso_enabled THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN sso_enabled SET DEFAULT 0")

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN audit_log_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN audit_log_enabled TYPE INTEGER
        USING CASE WHEN audit_log_enabled THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN audit_log_enabled SET DEFAULT 1")

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN content_filter_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE tenant_settings
        ALTER COLUMN content_filter_enabled TYPE INTEGER
        USING CASE WHEN content_filter_enabled THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN content_filter_enabled SET DEFAULT 1")

    # Revert sessions is_active
    op.execute("ALTER TABLE sessions ALTER COLUMN is_active DROP DEFAULT")
    op.execute("""
        ALTER TABLE sessions
        ALTER COLUMN is_active TYPE INTEGER
        USING CASE WHEN is_active THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE sessions ALTER COLUMN is_active SET DEFAULT 1")

    # Revert users is_active
    op.execute("ALTER TABLE users ALTER COLUMN is_active DROP DEFAULT")
    op.execute("""
        ALTER TABLE users
        ALTER COLUMN is_active TYPE INTEGER
        USING CASE WHEN is_active THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE users ALTER COLUMN is_active SET DEFAULT 1")

    # Revert content_filter_rules
    op.execute("ALTER TABLE content_filter_rules ALTER COLUMN is_enabled DROP DEFAULT")
    op.execute("""
        ALTER TABLE content_filter_rules
        ALTER COLUMN is_enabled TYPE INTEGER
        USING CASE WHEN is_enabled THEN 1 ELSE 0 END
    """)
    op.execute("ALTER TABLE content_filter_rules ALTER COLUMN is_enabled SET DEFAULT 1")
    op.execute("""
        ALTER TABLE content_filter_rules
        ALTER COLUMN updated_at TYPE TEXT
        USING COALESCE(updated_at::TEXT, NULL)
    """)
    op.execute("""
        ALTER TABLE content_filter_rules
        ALTER COLUMN created_at TYPE TEXT
        USING COALESCE(created_at::TEXT, NULL)
    """)

    # Revert quota_usage date
    op.execute("""
        ALTER TABLE quota_usage
        ALTER COLUMN date TYPE TEXT
        USING date::TEXT
    """)

    # Revert tenant_usage date
    op.execute("""
        ALTER TABLE tenant_usage
        ALTER COLUMN date TYPE TEXT
        USING date::TEXT
    """)

    # Revert daily_usage date
    op.execute("""
        ALTER TABLE daily_usage
        ALTER COLUMN date TYPE VARCHAR
        USING date::VARCHAR
    """)

    # Revert daily_messages timestamp
    op.execute("""
        ALTER TABLE daily_messages
        ALTER COLUMN timestamp TYPE VARCHAR
        USING COALESCE(timestamp::TEXT, NULL)
    """)
