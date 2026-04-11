"""Fix remaining boolean fields to proper BOOLEAN type

Revision ID: 029_boolean_fields
Revises: 028_project_path
Create Date: 2026-04-11

This migration fixes remaining boolean fields that use integer DEFAULT 0/1
to proper PostgreSQL BOOLEAN type:

Tables affected:
- users: is_admin, must_change_password
- alerts: read
- audit_logs: success
- knowledge_base: is_published
- notification_preferences: email_enabled, push_enabled
- prompt_templates: is_public, is_featured
- quota_alerts: acknowledged
- shared_sessions: allow_comments, allow_copy
- sso_providers: is_active

SQLite uses type affinity (INTEGER for boolean), so no changes needed.

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "029_boolean_fields"
down_revision: Union[str, None] = "028_project_path"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table_name AND column_name = :column_name"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    else:
        # SQLite
        result = conn.execute(
            sa.text("SELECT 1 FROM pragma_table_info(:table_name) WHERE name = :column_name"),
            {"table_name": table_name, "column_name": column_name},
        )
    return result.fetchone() is not None


def _is_postgresql() -> bool:
    """Check if using PostgreSQL."""
    conn = op.get_bind()
    return conn.dialect.name == "postgresql"


def upgrade() -> None:
    """Convert remaining integer boolean fields to proper BOOLEAN type."""
    if not _is_postgresql():
        # SQLite uses type affinity, no changes needed
        return

    # ============================================
    # users: is_admin -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "users", "is_admin"):
        op.execute("ALTER TABLE users ALTER COLUMN is_admin DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE users
            ALTER COLUMN is_admin TYPE BOOLEAN
            USING CASE WHEN is_admin = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT FALSE")

    # ============================================
    # users: must_change_password -> BOOLEAN
    # Note: migration 015 added this as BOOLEAN, but schema.sql may have integer
    # ============================================
    if _column_exists(op.get_bind(), "users", "must_change_password"):
        # Check current type
        result = op.get_bind().execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'must_change_password'"
            )
        )
        row = result.fetchone()
        if row and row[0] == "integer":
            op.execute("ALTER TABLE users ALTER COLUMN must_change_password DROP DEFAULT")
            op.execute(
                """
                ALTER TABLE users
                ALTER COLUMN must_change_password TYPE BOOLEAN
                USING CASE WHEN must_change_password = 1 THEN TRUE ELSE FALSE END
            """
            )
            op.execute("ALTER TABLE users ALTER COLUMN must_change_password SET DEFAULT FALSE")

    # ============================================
    # alerts: read -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "alerts", "read"):
        op.execute("ALTER TABLE alerts ALTER COLUMN read DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE alerts
            ALTER COLUMN read TYPE BOOLEAN
            USING CASE WHEN read = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE alerts ALTER COLUMN read SET DEFAULT FALSE")

    # ============================================
    # audit_logs: success -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "audit_logs", "success"):
        op.execute("ALTER TABLE audit_logs ALTER COLUMN success DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE audit_logs
            ALTER COLUMN success TYPE BOOLEAN
            USING CASE WHEN success = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE audit_logs ALTER COLUMN success SET DEFAULT TRUE")

    # ============================================
    # knowledge_base: is_published -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "knowledge_base", "is_published"):
        op.execute("ALTER TABLE knowledge_base ALTER COLUMN is_published DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE knowledge_base
            ALTER COLUMN is_published TYPE BOOLEAN
            USING CASE WHEN is_published = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE knowledge_base ALTER COLUMN is_published SET DEFAULT FALSE")

    # ============================================
    # notification_preferences: email_enabled, push_enabled -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "notification_preferences", "email_enabled"):
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN email_enabled DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE notification_preferences
            ALTER COLUMN email_enabled TYPE BOOLEAN
            USING CASE WHEN email_enabled = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN email_enabled SET DEFAULT TRUE")

    if _column_exists(op.get_bind(), "notification_preferences", "push_enabled"):
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN push_enabled DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE notification_preferences
            ALTER COLUMN push_enabled TYPE BOOLEAN
            USING CASE WHEN push_enabled = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN push_enabled SET DEFAULT TRUE")

    # ============================================
    # prompt_templates: is_public, is_featured -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "prompt_templates", "is_public"):
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_public DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE prompt_templates
            ALTER COLUMN is_public TYPE BOOLEAN
            USING CASE WHEN is_public = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_public SET DEFAULT FALSE")

    if _column_exists(op.get_bind(), "prompt_templates", "is_featured"):
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_featured DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE prompt_templates
            ALTER COLUMN is_featured TYPE BOOLEAN
            USING CASE WHEN is_featured = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_featured SET DEFAULT FALSE")

    # ============================================
    # quota_alerts: acknowledged -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "quota_alerts", "acknowledged"):
        op.execute("ALTER TABLE quota_alerts ALTER COLUMN acknowledged DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE quota_alerts
            ALTER COLUMN acknowledged TYPE BOOLEAN
            USING CASE WHEN acknowledged = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE quota_alerts ALTER COLUMN acknowledged SET DEFAULT FALSE")

    # ============================================
    # shared_sessions: allow_comments, allow_copy -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "shared_sessions", "allow_comments"):
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_comments DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE shared_sessions
            ALTER COLUMN allow_comments TYPE BOOLEAN
            USING CASE WHEN allow_comments = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_comments SET DEFAULT TRUE")

    if _column_exists(op.get_bind(), "shared_sessions", "allow_copy"):
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_copy DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE shared_sessions
            ALTER COLUMN allow_copy TYPE BOOLEAN
            USING CASE WHEN allow_copy = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_copy SET DEFAULT TRUE")

    # ============================================
    # sso_providers: is_active -> BOOLEAN
    # ============================================
    if _column_exists(op.get_bind(), "sso_providers", "is_active"):
        op.execute("ALTER TABLE sso_providers ALTER COLUMN is_active DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE sso_providers
            ALTER COLUMN is_active TYPE BOOLEAN
            USING CASE WHEN is_active = 1 THEN TRUE ELSE FALSE END
        """
        )
        op.execute("ALTER TABLE sso_providers ALTER COLUMN is_active SET DEFAULT TRUE")


def downgrade() -> None:
    """Revert boolean fields to integer type."""
    if not _is_postgresql():
        return

    # Revert sso_providers: is_active
    if _column_exists(op.get_bind(), "sso_providers", "is_active"):
        op.execute("ALTER TABLE sso_providers ALTER COLUMN is_active DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE sso_providers
            ALTER COLUMN is_active TYPE INTEGER
            USING CASE WHEN is_active THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE sso_providers ALTER COLUMN is_active SET DEFAULT 1")

    # Revert shared_sessions
    if _column_exists(op.get_bind(), "shared_sessions", "allow_copy"):
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_copy DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE shared_sessions
            ALTER COLUMN allow_copy TYPE INTEGER
            USING CASE WHEN allow_copy THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_copy SET DEFAULT 1")

    if _column_exists(op.get_bind(), "shared_sessions", "allow_comments"):
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_comments DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE shared_sessions
            ALTER COLUMN allow_comments TYPE INTEGER
            USING CASE WHEN allow_comments THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE shared_sessions ALTER COLUMN allow_comments SET DEFAULT 1")

    # Revert quota_alerts: acknowledged
    if _column_exists(op.get_bind(), "quota_alerts", "acknowledged"):
        op.execute("ALTER TABLE quota_alerts ALTER COLUMN acknowledged DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE quota_alerts
            ALTER COLUMN acknowledged TYPE INTEGER
            USING CASE WHEN acknowledged THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE quota_alerts ALTER COLUMN acknowledged SET DEFAULT 0")

    # Revert prompt_templates
    if _column_exists(op.get_bind(), "prompt_templates", "is_featured"):
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_featured DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE prompt_templates
            ALTER COLUMN is_featured TYPE INTEGER
            USING CASE WHEN is_featured THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_featured SET DEFAULT 0")

    if _column_exists(op.get_bind(), "prompt_templates", "is_public"):
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_public DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE prompt_templates
            ALTER COLUMN is_public TYPE INTEGER
            USING CASE WHEN is_public THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE prompt_templates ALTER COLUMN is_public SET DEFAULT 0")

    # Revert notification_preferences
    if _column_exists(op.get_bind(), "notification_preferences", "push_enabled"):
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN push_enabled DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE notification_preferences
            ALTER COLUMN push_enabled TYPE INTEGER
            USING CASE WHEN push_enabled THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN push_enabled SET DEFAULT 1")

    if _column_exists(op.get_bind(), "notification_preferences", "email_enabled"):
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN email_enabled DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE notification_preferences
            ALTER COLUMN email_enabled TYPE INTEGER
            USING CASE WHEN email_enabled THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE notification_preferences ALTER COLUMN email_enabled SET DEFAULT 1")

    # Revert knowledge_base: is_published
    if _column_exists(op.get_bind(), "knowledge_base", "is_published"):
        op.execute("ALTER TABLE knowledge_base ALTER COLUMN is_published DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE knowledge_base
            ALTER COLUMN is_published TYPE INTEGER
            USING CASE WHEN is_published THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE knowledge_base ALTER COLUMN is_published SET DEFAULT 0")

    # Revert audit_logs: success
    if _column_exists(op.get_bind(), "audit_logs", "success"):
        op.execute("ALTER TABLE audit_logs ALTER COLUMN success DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE audit_logs
            ALTER COLUMN success TYPE INTEGER
            USING CASE WHEN success THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE audit_logs ALTER COLUMN success SET DEFAULT 1")

    # Revert alerts: read
    if _column_exists(op.get_bind(), "alerts", "read"):
        op.execute("ALTER TABLE alerts ALTER COLUMN read DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE alerts
            ALTER COLUMN read TYPE INTEGER
            USING CASE WHEN read THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE alerts ALTER COLUMN read SET DEFAULT 0")

    # Revert users: must_change_password (only if was integer before)
    # Skip - migration 015 originally added as BOOLEAN

    # Revert users: is_admin
    if _column_exists(op.get_bind(), "users", "is_admin"):
        op.execute("ALTER TABLE users ALTER COLUMN is_admin DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE users
            ALTER COLUMN is_admin TYPE INTEGER
            USING CASE WHEN is_admin THEN 1 ELSE 0 END
        """
        )
        op.execute("ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT 0")