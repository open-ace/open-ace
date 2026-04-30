"""PostgreSQL-specific optimizations

Revision ID: 021_postgresql_optimization
Revises: 020_stats_date_tool_idx
Create Date: 2026-03-30

This migration implements PostgreSQL-specific optimizations:
1. ENUM types for role/status fields (storage reduction, faster comparison)
2. JSONB type for quota/settings fields (query optimization, GIN index)
3. Partial indexes for common queries (index size reduction)
4. Automatic updated_at trigger (simplify application code)
5. Foreign key indexes (cascade operation performance)
6. Database comments (documentation)

These optimizations are PostgreSQL-only and will be skipped for SQLite.

Expected benefits:
- Storage: 30-50% reduction for ENUM fields
- JSONB queries: 10x+ faster with GIN index
- Index size: 50%+ reduction with partial indexes
- Code simplification: automatic updated_at management

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "021_postgresql_optimization"
down_revision: Union[str, None] = "020_stats_date_tool_idx"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_name = :table_name AND column_name = :column_name
                )
                """),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(f"PRAGMA table_info({table_name})"),
        )
        columns = [row[1] for row in result.fetchall()]
        return column_name in columns


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
    else:
        result = conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
    return result.fetchone() is not None


def _constraint_exists(conn, table_name: str, constraint_name: str) -> bool:
    """Check if a constraint exists (PostgreSQL only)."""
    if conn.dialect.name == "postgresql":
        # Use string formatting for regclass cast, as parameter binding doesn't work with type casts
        result = conn.execute(
            sa.text(f"""
                SELECT EXISTS (
                    SELECT FROM pg_constraint
                    WHERE conname = :constraint_name
                    AND conrelid = '{table_name}'::regclass
                )
                """),
            {"constraint_name": constraint_name},
        )
        return result.fetchone()[0]
    return False


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    if not is_postgresql:
        # SQLite: Skip PostgreSQL-specific optimizations
        # But we can still add some indexes and comments
        _upgrade_sqlite_indexes(conn)
        return

    # ============================================
    # 1. Create ENUM types
    # ============================================
    # Drop existing CHECK constraints first
    if _constraint_exists(conn, "users", "chk_users_role"):
        op.execute("ALTER TABLE users DROP CONSTRAINT chk_users_role")

    if _constraint_exists(conn, "tenants", "chk_tenants_status"):
        op.execute("ALTER TABLE tenants DROP CONSTRAINT chk_tenants_status")

    if _constraint_exists(conn, "tenants", "chk_tenants_plan"):
        op.execute("ALTER TABLE tenants DROP CONSTRAINT chk_tenants_plan")

    # Drop partial indexes that use role field (must be recreated after ENUM conversion)
    # These indexes prevent ALTER COLUMN due to IMMUTABLE function requirement
    indexes_to_drop = [
        "idx_messages_session_list_covering",
        "idx_messages_usage_trend_covering",
        "idx_messages_sender_date_role_covering",
    ]
    for idx_name in indexes_to_drop:
        if _index_exists(conn, "daily_messages", idx_name):
            op.execute(f"DROP INDEX {idx_name}")

    # Create ENUM types
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE user_role AS ENUM ('admin', 'manager', 'user');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tenant_status AS ENUM ('active', 'suspended', 'trial', 'inactive');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tenant_plan AS ENUM ('free', 'standard', 'premium', 'enterprise');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE audit_severity AS ENUM ('info', 'warning', 'error', 'critical');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Convert columns to ENUM types
    # Note: Must drop default before changing type, then set new default
    if _column_exists(conn, "users", "role"):
        # Drop default first
        op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
        op.execute("""
            ALTER TABLE users
            ALTER COLUMN role TYPE user_role
            USING CASE
                WHEN role IN ('admin', 'manager', 'user') THEN role::user_role
                ELSE 'user'::user_role
            END
        """)
        # Set new default
        op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'user'::user_role")

    if _column_exists(conn, "tenants", "status"):
        # Drop default first
        op.execute("ALTER TABLE tenants ALTER COLUMN status DROP DEFAULT")
        op.execute("""
            ALTER TABLE tenants
            ALTER COLUMN status TYPE tenant_status
            USING CASE
                WHEN status IN ('active', 'suspended', 'trial', 'inactive') THEN status::tenant_status
                ELSE 'active'::tenant_status
            END
        """)
        # Set new default
        op.execute("ALTER TABLE tenants ALTER COLUMN status SET DEFAULT 'active'::tenant_status")

    if _column_exists(conn, "tenants", "plan"):
        # Drop default first
        op.execute("ALTER TABLE tenants ALTER COLUMN plan DROP DEFAULT")
        op.execute("""
            ALTER TABLE tenants
            ALTER COLUMN plan TYPE tenant_plan
            USING CASE
                WHEN plan IN ('free', 'standard', 'premium', 'enterprise') THEN plan::tenant_plan
                ELSE 'standard'::tenant_plan
            END
        """)
        # Set new default
        op.execute("ALTER TABLE tenants ALTER COLUMN plan SET DEFAULT 'standard'::tenant_plan")

    if _column_exists(conn, "daily_messages", "role"):
        # Skip if role is already message_role
        result = conn.execute(sa.text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'daily_messages' AND column_name = 'role'
                """))
        data_type = result.fetchone()
        if not data_type or data_type[0] != "message_role":
            op.execute("""
                ALTER TABLE daily_messages
                ALTER COLUMN role TYPE message_role
                USING CASE
                    WHEN role IN ('user', 'assistant', 'system') THEN role::message_role
                    ELSE 'user'::message_role
                END
            """)

    if _column_exists(conn, "audit_logs", "severity"):
        # Drop default first
        op.execute("ALTER TABLE audit_logs ALTER COLUMN severity DROP DEFAULT")
        op.execute("""
            ALTER TABLE audit_logs
            ALTER COLUMN severity TYPE audit_severity
            USING CASE
                WHEN severity IN ('info', 'warning', 'error', 'critical') THEN severity::audit_severity
                ELSE 'info'::audit_severity
            END
        """)
        # Set new default
        op.execute(
            "ALTER TABLE audit_logs ALTER COLUMN severity SET DEFAULT 'info'::audit_severity"
        )

    # ============================================
    # 2. Convert JSON fields to JSONB
    # ============================================
    if _column_exists(conn, "tenants", "quota"):
        # Check if already JSONB
        result = conn.execute(sa.text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'tenants' AND column_name = 'quota'
                """))
        data_type = result.fetchone()
        if data_type and data_type[0] not in ("jsonb", "JSONB"):
            op.execute(r"""
                ALTER TABLE tenants
                ALTER COLUMN quota TYPE JSONB
                USING CASE
                    WHEN quota IS NULL THEN NULL
                    WHEN quota::text ~ '^\s*\{' THEN quota::JSONB
                    ELSE NULL
                END
            """)

    if _column_exists(conn, "tenants", "settings"):
        result = conn.execute(sa.text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'tenants' AND column_name = 'settings'
                """))
        data_type = result.fetchone()
        if data_type and data_type[0] not in ("jsonb", "JSONB"):
            op.execute(r"""
                ALTER TABLE tenants
                ALTER COLUMN settings TYPE JSONB
                USING CASE
                    WHEN settings IS NULL THEN NULL
                    WHEN settings::text ~ '^\s*\{' THEN settings::JSONB
                    ELSE NULL
                END
            """)

    # Add GIN indexes for JSONB fields
    if not _index_exists(conn, "tenants", "idx_tenants_quota_gin"):
        op.execute("CREATE INDEX idx_tenants_quota_gin ON tenants USING gin(quota)")

    if not _index_exists(conn, "tenants", "idx_tenants_settings_gin"):
        op.execute("CREATE INDEX idx_tenants_settings_gin ON tenants USING gin(settings)")

    # ============================================
    # 3. Create partial indexes
    # ============================================
    # Recreate indexes on daily_messages.role that were dropped earlier
    # Now that role is converted to message_role ENUM
    if not _index_exists(conn, "daily_messages", "idx_messages_sender_date_role_covering"):
        op.execute("""
            CREATE INDEX idx_messages_sender_date_role_covering ON daily_messages
            USING btree (sender_id, date, role)
            INCLUDE (tokens_used)
            WHERE ((sender_id IS NOT NULL) AND (role = 'assistant'::message_role))
        """)

    if not _index_exists(conn, "daily_messages", "idx_messages_session_list_covering"):
        op.execute("""
            CREATE INDEX idx_messages_session_list_covering ON daily_messages
            USING btree (agent_session_id, tool_name, host_name, sender_name)
            INCLUDE (timestamp, tokens_used, input_tokens, output_tokens, sender_id, date)
            WHERE (agent_session_id IS NOT NULL)
        """)

    if not _index_exists(conn, "daily_messages", "idx_messages_usage_trend_covering"):
        op.execute("""
            CREATE INDEX idx_messages_usage_trend_covering ON daily_messages
            USING btree (date, role, sender_name)
            INCLUDE (tokens_used)
            WHERE (role = 'assistant'::message_role)
        """)

    # Active users only (is_active is BOOLEAN in PostgreSQL)
    if not _index_exists(conn, "users", "idx_users_active_partial"):
        op.execute("""
            CREATE INDEX idx_users_active_partial ON users (username, email, role)
            WHERE is_active IS TRUE AND deleted_at IS NULL
        """)

    # Unacknowledged alerts (acknowledged is INTEGER: 0=unacknowledged, 1=acknowledged)
    if not _index_exists(conn, "quota_alerts", "idx_alerts_unacked_partial"):
        op.execute("""
            CREATE INDEX idx_alerts_unacked_partial ON quota_alerts (created_at, user_id)
            WHERE acknowledged = 0
        """)

    # Recent audit logs - skip partial index with CURRENT_TIMESTAMP (not IMMUTABLE)
    # Use regular index instead
    if not _index_exists(conn, "audit_logs", "idx_audit_recent"):
        op.execute("""
            CREATE INDEX idx_audit_recent ON audit_logs (timestamp, user_id, action)
        """)

    # ============================================
    # 4. Create updated_at trigger function
    # ============================================
    op.execute("""
        CREATE OR REPLACE FUNCTION update_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Apply trigger to tables with updated_at column
    for table in ["tenants", "tenant_usage", "quota_usage", "tenant_quotas", "tenant_settings"]:
        if _column_exists(conn, table, "updated_at"):
            trigger_name = f"{table}_updated_at_trigger"
            # Drop existing trigger if exists
            op.execute(f"""
                DROP TRIGGER IF EXISTS {trigger_name} ON {table}
            """)
            # Create new trigger with condition
            op.execute(f"""
                CREATE TRIGGER {trigger_name}
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW
                    WHEN (OLD.* IS DISTINCT FROM NEW.*)
                    EXECUTE FUNCTION update_timestamp()
            """)

    # ============================================
    # 5. Add foreign key indexes
    # ============================================
    fk_indexes = [
        ("sessions", "user_id", "idx_sessions_user_fk"),
        ("tenant_usage", "tenant_id", "idx_tenant_usage_fk"),
        ("quota_usage", "user_id", "idx_quota_usage_user_fk"),
        ("quota_alerts", "user_id", "idx_quota_alerts_user_fk"),
        ("quota_alerts", "acknowledged_by", "idx_quota_alerts_ack_by_fk"),
        ("audit_logs", "user_id", "idx_audit_logs_user_fk"),
        ("users", "tenant_id", "idx_users_tenant_fk"),
    ]

    for table, column, index_name in fk_indexes:
        if _column_exists(conn, table, column):
            if not _index_exists(conn, table, index_name):
                op.execute(f"CREATE INDEX {index_name} ON {table} ({column})")

    # ============================================
    # 6. Add database comments
    # ============================================
    comments = [
        ("TABLE", "daily_messages", "AI tool message records, stored by date"),
        ("TABLE", "daily_usage", "AI tool usage statistics, aggregated by date/tool/host"),
        ("TABLE", "users", "System users with multi-tenant support"),
        ("TABLE", "tenants", "Multi-tenant organizations"),
        ("TABLE", "sessions", "User session management"),
        ("TABLE", "audit_logs", "System audit trail for security compliance"),
        ("COLUMN", "daily_messages.tokens_used", "Total tokens consumed for this message"),
        ("COLUMN", "daily_messages.role", "Message sender role: user/assistant/system"),
        ("COLUMN", "users.role", "User role: admin(manager)/manager/user"),
        ("COLUMN", "tenants.status", "Tenant status: active/suspended/trial/inactive"),
        ("COLUMN", "tenants.plan", "Subscription plan: free/standard/premium/enterprise"),
        ("COLUMN", "tenants.quota", "Tenant quota configuration (JSONB)"),
        ("COLUMN", "tenants.settings", "Tenant-specific settings (JSONB)"),
    ]

    for target_type, target, comment in comments:
        try:
            if target_type == "TABLE":
                op.execute(f"COMMENT ON TABLE {target} IS '{comment}'")
            else:
                op.execute(f"COMMENT ON COLUMN {target} IS '{comment}'")
        except Exception:
            pass  # Ignore comment errors


def _upgrade_sqlite_indexes(conn) -> None:
    """Add SQLite-compatible indexes."""
    # Add foreign key indexes for SQLite
    fk_indexes = [
        ("sessions", "user_id", "idx_sessions_user_fk"),
        ("tenant_usage", "tenant_id", "idx_tenant_usage_fk"),
        ("quota_usage", "user_id", "idx_quota_usage_user_fk"),
        ("quota_alerts", "user_id", "idx_quota_alerts_user_fk"),
        ("audit_logs", "user_id", "idx_audit_logs_user_fk"),
        ("users", "tenant_id", "idx_users_tenant_fk"),
    ]

    for table, column, index_name in fk_indexes:
        try:
            result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
            columns = [row[1] for row in result.fetchall()]
            if column in columns:
                result = conn.execute(
                    sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :name"),
                    {"name": index_name},
                )
                if not result.fetchone():
                    conn.execute(sa.text(f"CREATE INDEX {index_name} ON {table} ({column})"))
        except Exception:
            pass

    conn.commit()


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    if not is_postgresql:
        # SQLite: Drop indexes
        _downgrade_sqlite_indexes(conn)
        return

    # ============================================
    # Drop partial indexes that use role field
    # ============================================
    # Must drop before converting role from ENUM back to TEXT
    indexes_to_drop = [
        "idx_messages_session_list_covering",
        "idx_messages_usage_trend_covering",
        "idx_messages_sender_date_role_covering",
    ]
    for idx_name in indexes_to_drop:
        if _index_exists(conn, "daily_messages", idx_name):
            op.execute(f"DROP INDEX {idx_name}")

    # ============================================
    # 6. Remove comments
    # ============================================
    comments = [
        ("TABLE", "daily_messages"),
        ("TABLE", "daily_usage"),
        ("TABLE", "users"),
        ("TABLE", "tenants"),
        ("TABLE", "sessions"),
        ("TABLE", "audit_logs"),
        ("COLUMN", "daily_messages.tokens_used"),
        ("COLUMN", "daily_messages.role"),
        ("COLUMN", "users.role"),
        ("COLUMN", "tenants.status"),
        ("COLUMN", "tenants.plan"),
        ("COLUMN", "tenants.quota"),
        ("COLUMN", "tenants.settings"),
    ]

    for target_type, target in comments:
        try:
            if target_type == "TABLE":
                op.execute(f"COMMENT ON TABLE {target} IS NULL")
            else:
                op.execute(f"COMMENT ON COLUMN {target} IS NULL")
        except Exception:
            pass

    # ============================================
    # 5. Drop foreign key indexes
    # ============================================
    fk_indexes = [
        ("sessions", "idx_sessions_user_fk"),
        ("tenant_usage", "idx_tenant_usage_fk"),
        ("quota_usage", "idx_quota_usage_user_fk"),
        ("quota_alerts", "idx_quota_alerts_user_fk"),
        ("quota_alerts", "idx_quota_alerts_ack_by_fk"),
        ("audit_logs", "idx_audit_logs_user_fk"),
        ("users", "idx_users_tenant_fk"),
    ]

    for table, index_name in fk_indexes:
        if _index_exists(conn, table, index_name):
            op.execute(f"DROP INDEX {index_name}")

    # ============================================
    # 4. Drop triggers
    # ============================================
    for table in ["tenants", "tenant_usage", "quota_usage", "tenant_quotas", "tenant_settings"]:
        trigger_name = f"{table}_updated_at_trigger"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table}")

    # Drop trigger function
    op.execute("DROP FUNCTION IF EXISTS update_timestamp()")

    # ============================================
    # 3. Drop partial indexes
    # ============================================
    if _index_exists(conn, "users", "idx_users_active_partial"):
        op.execute("DROP INDEX idx_users_active_partial")

    if _index_exists(conn, "quota_alerts", "idx_alerts_unacked_partial"):
        op.execute("DROP INDEX idx_alerts_unacked_partial")

    if _index_exists(conn, "audit_logs", "idx_audit_recent"):
        op.execute("DROP INDEX idx_audit_recent")

    # ============================================
    # 2. Convert JSONB back to TEXT
    # ============================================
    if _column_exists(conn, "tenants", "quota"):
        result = conn.execute(sa.text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'tenants' AND column_name = 'quota'
                """))
        data_type = result.fetchone()
        if data_type and data_type[0] in ("jsonb", "JSONB"):
            op.execute("""
                ALTER TABLE tenants
                ALTER COLUMN quota TYPE TEXT
                USING quota::TEXT
            """)

    if _column_exists(conn, "tenants", "settings"):
        result = conn.execute(sa.text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'tenants' AND column_name = 'settings'
                """))
        data_type = result.fetchone()
        if data_type and data_type[0] in ("jsonb", "JSONB"):
            op.execute("""
                ALTER TABLE tenants
                ALTER COLUMN settings TYPE TEXT
                USING settings::TEXT
            """)

    # Drop GIN indexes
    if _index_exists(conn, "tenants", "idx_tenants_quota_gin"):
        op.execute("DROP INDEX idx_tenants_quota_gin")

    if _index_exists(conn, "tenants", "idx_tenants_settings_gin"):
        op.execute("DROP INDEX idx_tenants_settings_gin")

    # ============================================
    # 1. Convert ENUM back to TEXT with CHECK constraints
    # ============================================
    if _column_exists(conn, "users", "role"):
        op.execute("""
            ALTER TABLE users
            ALTER COLUMN role TYPE TEXT
            USING role::TEXT
        """)
        op.execute("""
            ALTER TABLE users
            ADD CONSTRAINT chk_users_role
            CHECK (role IN ('admin', 'manager', 'user'))
        """)

    if _column_exists(conn, "tenants", "status"):
        op.execute("""
            ALTER TABLE tenants
            ALTER COLUMN status TYPE TEXT
            USING status::TEXT
        """)
        op.execute("""
            ALTER TABLE tenants
            ADD CONSTRAINT chk_tenants_status
            CHECK (status IN ('active', 'suspended', 'trial', 'inactive'))
        """)

    if _column_exists(conn, "tenants", "plan"):
        op.execute("""
            ALTER TABLE tenants
            ALTER COLUMN plan TYPE TEXT
            USING plan::TEXT
        """)
        op.execute("""
            ALTER TABLE tenants
            ADD CONSTRAINT chk_tenants_plan
            CHECK (plan IN ('free', 'standard', 'premium', 'enterprise'))
        """)

    if _column_exists(conn, "daily_messages", "role"):
        op.execute("""
            ALTER TABLE daily_messages
            ALTER COLUMN role TYPE TEXT
            USING role::TEXT
        """)

    if _column_exists(conn, "audit_logs", "severity"):
        op.execute("""
            ALTER TABLE audit_logs
            ALTER COLUMN severity TYPE TEXT
            USING severity::TEXT
        """)

    # Drop ENUM types
    op.execute("DROP TYPE IF EXISTS audit_severity")
    op.execute("DROP TYPE IF EXISTS message_role")
    op.execute("DROP TYPE IF EXISTS tenant_plan")
    op.execute("DROP TYPE IF EXISTS tenant_status")
    op.execute("DROP TYPE IF EXISTS user_role")

    # ============================================
    # Recreate partial indexes with TEXT type
    # ============================================
    # Recreate indexes that were dropped at the beginning
    if not _index_exists(conn, "daily_messages", "idx_messages_sender_date_role_covering"):
        op.execute("""
            CREATE INDEX idx_messages_sender_date_role_covering ON daily_messages
            USING btree (sender_id, date, role)
            INCLUDE (tokens_used)
            WHERE ((sender_id IS NOT NULL) AND (role = 'assistant'))
        """)

    if not _index_exists(conn, "daily_messages", "idx_messages_session_list_covering"):
        op.execute("""
            CREATE INDEX idx_messages_session_list_covering ON daily_messages
            USING btree (agent_session_id, tool_name, host_name, sender_name)
            INCLUDE (timestamp, tokens_used, input_tokens, output_tokens, sender_id, date)
            WHERE (agent_session_id IS NOT NULL)
        """)

    if not _index_exists(conn, "daily_messages", "idx_messages_usage_trend_covering"):
        op.execute("""
            CREATE INDEX idx_messages_usage_trend_covering ON daily_messages
            USING btree (date, role, sender_name)
            INCLUDE (tokens_used)
            WHERE (role = 'assistant')
        """)


def _downgrade_sqlite_indexes(conn) -> None:
    """Drop SQLite indexes."""
    fk_indexes = [
        "idx_sessions_user_fk",
        "idx_tenant_usage_fk",
        "idx_quota_usage_user_fk",
        "idx_quota_alerts_user_fk",
        "idx_audit_logs_user_fk",
        "idx_users_tenant_fk",
    ]

    for index_name in fk_indexes:
        try:
            conn.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))
        except Exception:
            pass

    conn.commit()
