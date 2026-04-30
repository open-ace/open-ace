"""Add CHECK constraints for data integrity

Revision ID: 013_add_check_constraints
Revises: 012_fix_data_types
Create Date: 2026-03-28

This migration adds CHECK constraints to ensure data integrity:
- users.role: must be one of 'admin', 'manager', 'user'
- tenants.status: must be one of 'active', 'suspended', 'trial', 'inactive'
- tenants.plan: must be one of 'free', 'standard', 'premium', 'enterprise'
- quota_usage.tokens_used: must be >= 0
- quota_usage.requests_used: must be >= 0
- daily_messages.tokens_used: must be >= 0
- daily_usage.tokens_used: must be >= 0

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "013_add_check_constraints"
down_revision: Union[str, None] = "012_fix_data_types"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # ============================================
    # Validate and clean existing data before adding constraints
    # ============================================
    # Clean invalid role values in users
    op.execute(
        """
        UPDATE users SET role = 'user'
        WHERE role IS NOT NULL AND role NOT IN ('admin', 'manager', 'user')
    """
    )

    # Clean invalid status values in tenants
    op.execute(
        """
        UPDATE tenants SET status = 'active'
        WHERE status IS NOT NULL AND status NOT IN ('active', 'suspended', 'trial', 'inactive')
    """
    )

    # Clean invalid plan values in tenants
    op.execute(
        """
        UPDATE tenants SET plan = 'standard'
        WHERE plan IS NOT NULL AND plan NOT IN ('free', 'standard', 'premium', 'enterprise')
    """
    )

    # Clean negative token values in quota_usage
    op.execute(
        """
        UPDATE quota_usage SET tokens_used = 0 WHERE tokens_used < 0
    """
    )
    op.execute(
        """
        UPDATE quota_usage SET requests_used = 0 WHERE requests_used < 0
    """
    )

    # Clean negative token values in daily_messages
    op.execute(
        """
        UPDATE daily_messages SET tokens_used = 0 WHERE tokens_used < 0
    """
    )
    op.execute(
        """
        UPDATE daily_messages SET input_tokens = 0 WHERE input_tokens < 0
    """
    )
    op.execute(
        """
        UPDATE daily_messages SET output_tokens = 0 WHERE output_tokens < 0
    """
    )

    # Clean negative token values in daily_usage
    op.execute(
        """
        UPDATE daily_usage SET tokens_used = 0 WHERE tokens_used < 0
    """
    )
    op.execute(
        """
        UPDATE daily_usage SET input_tokens = 0 WHERE input_tokens < 0
    """
    )
    op.execute(
        """
        UPDATE daily_usage SET output_tokens = 0 WHERE output_tokens < 0
    """
    )
    op.execute(
        """
        UPDATE daily_usage SET cache_tokens = 0 WHERE cache_tokens < 0
    """
    )
    op.execute(
        """
        UPDATE daily_usage SET request_count = 0 WHERE request_count < 0
    """
    )

    # ============================================
    # users table constraints
    # ============================================
    if is_postgresql:
        op.execute(
            """
            ALTER TABLE users
            ADD CONSTRAINT chk_users_role
            CHECK (role IN ('admin', 'manager', 'user'))
        """
        )
    else:
        # SQLite doesn't support ALTER TABLE ADD CONSTRAINT
        # We need to recreate the table
        op.execute(
            """
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'manager', 'user')),
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                daily_token_quota INTEGER,
                monthly_token_quota INTEGER,
                daily_request_quota INTEGER,
                monthly_request_quota INTEGER,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL
            )
        """
        )
        op.execute(
            """
            INSERT INTO users_new
            SELECT id, username, password_hash, email, role, is_active, created_at, last_login,
                   daily_token_quota, monthly_token_quota, daily_request_quota, monthly_request_quota, tenant_id
            FROM users
        """
        )
        # Recreate indexes
        op.execute("CREATE INDEX idx_users_role ON users_new(role)")
        op.execute("CREATE INDEX idx_users_email ON users_new(email)")
        op.execute("CREATE INDEX idx_users_active ON users_new(is_active)")
        op.execute("CREATE INDEX idx_users_tenant ON users_new(tenant_id)")
        # Drop old table and rename
        op.drop_table("users")
        op.rename_table("users_new", "users")

    # ============================================
    # tenants table constraints
    # ============================================
    if is_postgresql:
        op.execute(
            """
            ALTER TABLE tenants
            ADD CONSTRAINT chk_tenants_status
            CHECK (status IN ('active', 'suspended', 'trial', 'inactive'))
        """
        )
        op.execute(
            """
            ALTER TABLE tenants
            ADD CONSTRAINT chk_tenants_plan
            CHECK (plan IN ('free', 'standard', 'premium', 'enterprise'))
        """
        )
    else:
        # SQLite: recreate table with CHECK constraints
        op.execute(
            """
            CREATE TABLE tenants_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'trial', 'inactive')),
                plan TEXT DEFAULT 'standard' CHECK (plan IN ('free', 'standard', 'premium', 'enterprise')),
                contact_email TEXT,
                contact_phone TEXT,
                contact_name TEXT,
                quota TEXT,
                settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trial_ends_at TIMESTAMP,
                subscription_ends_at TIMESTAMP,
                user_count INTEGER DEFAULT 0,
                total_tokens_used INTEGER DEFAULT 0,
                total_requests_made INTEGER DEFAULT 0
            )
        """
        )
        op.execute(
            """
            INSERT INTO tenants_new
            SELECT id, name, slug, status, plan, contact_email, contact_phone, contact_name,
                   quota, settings, created_at, updated_at, trial_ends_at, subscription_ends_at,
                   user_count, total_tokens_used, total_requests_made
            FROM tenants
        """
        )
        op.execute("CREATE INDEX idx_tenants_slug ON tenants_new(slug)")
        op.execute("CREATE INDEX idx_tenants_status ON tenants_new(status)")
        op.drop_table("tenants")
        op.rename_table("tenants_new", "tenants")

    # ============================================
    # quota_usage table constraints
    # ============================================
    if is_postgresql:
        op.execute(
            """
            ALTER TABLE quota_usage
            ADD CONSTRAINT chk_quota_usage_tokens_positive
            CHECK (tokens_used >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE quota_usage
            ADD CONSTRAINT chk_quota_usage_requests_positive
            CHECK (requests_used >= 0)
        """
        )
    else:
        # SQLite: recreate table with CHECK constraints
        op.execute(
            """
            CREATE TABLE quota_usage_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                period TEXT DEFAULT 'daily',
                tokens_used INTEGER DEFAULT 0 CHECK (tokens_used >= 0),
                requests_used INTEGER DEFAULT 0 CHECK (requests_used >= 0),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date, period)
            )
        """
        )
        op.execute(
            """
            INSERT INTO quota_usage_new
            SELECT id, user_id, date, period, tokens_used, requests_used, created_at, updated_at
            FROM quota_usage
        """
        )
        op.execute("CREATE INDEX idx_quota_usage_user ON quota_usage_new(user_id)")
        op.execute("CREATE INDEX idx_quota_usage_date ON quota_usage_new(date)")
        op.drop_table("quota_usage")
        op.rename_table("quota_usage_new", "quota_usage")

    # ============================================
    # daily_messages table constraints
    # ============================================
    if is_postgresql:
        op.execute(
            """
            ALTER TABLE daily_messages
            ADD CONSTRAINT chk_daily_messages_tokens_positive
            CHECK (tokens_used >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE daily_messages
            ADD CONSTRAINT chk_daily_messages_input_tokens_positive
            CHECK (input_tokens >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE daily_messages
            ADD CONSTRAINT chk_daily_messages_output_tokens_positive
            CHECK (output_tokens >= 0)
        """
        )
    else:
        # SQLite: recreate table with CHECK constraints
        # Note: This is complex due to many columns, but we need to add constraints
        op.execute(
            """
            CREATE TABLE daily_messages_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                message_id TEXT NOT NULL,
                parent_id TEXT,
                role TEXT NOT NULL,
                content TEXT,
                full_entry TEXT,
                tokens_used INTEGER DEFAULT 0 CHECK (tokens_used >= 0),
                input_tokens INTEGER DEFAULT 0 CHECK (input_tokens >= 0),
                output_tokens INTEGER DEFAULT 0 CHECK (output_tokens >= 0),
                model TEXT,
                timestamp TEXT,
                sender_id TEXT,
                sender_name TEXT,
                message_source TEXT,
                feishu_conversation_id TEXT,
                group_subject TEXT,
                is_group_chat INTEGER,
                conversation_id TEXT,
                agent_session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (tokens_used >= 0),
                CHECK (input_tokens >= 0),
                CHECK (output_tokens >= 0)
            )
        """
        )
        op.execute(
            """
            INSERT INTO daily_messages_new
            SELECT id, date, tool_name, host_name, message_id, parent_id, role, content, full_entry,
                   tokens_used, input_tokens, output_tokens, model, timestamp, sender_id, sender_name,
                   message_source, feishu_conversation_id, group_subject, is_group_chat,
                   conversation_id, agent_session_id, created_at
            FROM daily_messages
        """
        )
        # Recreate essential indexes
        op.execute(
            "CREATE INDEX idx_messages_date_tool_host ON daily_messages_new(date, tool_name, host_name)"
        )
        op.execute("CREATE INDEX idx_messages_sender_id ON daily_messages_new(sender_id)")
        op.execute(
            "CREATE INDEX idx_messages_date_role_timestamp ON daily_messages_new(date, role, timestamp)"
        )
        op.drop_table("daily_messages")
        op.rename_table("daily_messages_new", "daily_messages")

    # ============================================
    # daily_usage table constraints
    # ============================================
    if is_postgresql:
        op.execute(
            """
            ALTER TABLE daily_usage
            ADD CONSTRAINT chk_daily_usage_tokens_positive
            CHECK (tokens_used >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE daily_usage
            ADD CONSTRAINT chk_daily_usage_input_tokens_positive
            CHECK (input_tokens >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE daily_usage
            ADD CONSTRAINT chk_daily_usage_output_tokens_positive
            CHECK (output_tokens >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE daily_usage
            ADD CONSTRAINT chk_daily_usage_cache_tokens_positive
            CHECK (cache_tokens >= 0)
        """
        )
        op.execute(
            """
            ALTER TABLE daily_usage
            ADD CONSTRAINT chk_daily_usage_request_count_positive
            CHECK (request_count >= 0)
        """
        )
    else:
        # SQLite: recreate table with CHECK constraints
        op.execute(
            """
            CREATE TABLE daily_usage_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                tokens_used INTEGER DEFAULT 0 CHECK (tokens_used >= 0),
                input_tokens INTEGER DEFAULT 0 CHECK (input_tokens >= 0),
                output_tokens INTEGER DEFAULT 0 CHECK (output_tokens >= 0),
                cache_tokens INTEGER DEFAULT 0 CHECK (cache_tokens >= 0),
                request_count INTEGER DEFAULT 0 CHECK (request_count >= 0),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, tool_name, host_name)
            )
        """
        )
        op.execute(
            """
            INSERT INTO daily_usage_new
            SELECT id, date, tool_name, host_name, tokens_used, input_tokens, output_tokens,
                   cache_tokens, request_count, created_at
            FROM daily_usage
        """
        )
        # Recreate essential indexes
        op.execute("CREATE INDEX idx_daily_usage_date ON daily_usage_new(date)")
        op.execute("CREATE INDEX idx_daily_usage_tool ON daily_usage_new(tool_name)")
        op.drop_table("daily_usage")
        op.rename_table("daily_usage_new", "daily_usage")


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    if is_postgresql:
        # Drop PostgreSQL constraints
        op.execute(
            "ALTER TABLE daily_usage DROP CONSTRAINT IF EXISTS chk_daily_usage_request_count_positive"
        )
        op.execute(
            "ALTER TABLE daily_usage DROP CONSTRAINT IF EXISTS chk_daily_usage_cache_tokens_positive"
        )
        op.execute(
            "ALTER TABLE daily_usage DROP CONSTRAINT IF EXISTS chk_daily_usage_output_tokens_positive"
        )
        op.execute(
            "ALTER TABLE daily_usage DROP CONSTRAINT IF EXISTS chk_daily_usage_input_tokens_positive"
        )
        op.execute(
            "ALTER TABLE daily_usage DROP CONSTRAINT IF EXISTS chk_daily_usage_tokens_positive"
        )

        op.execute(
            "ALTER TABLE daily_messages DROP CONSTRAINT IF EXISTS chk_daily_messages_output_tokens_positive"
        )
        op.execute(
            "ALTER TABLE daily_messages DROP CONSTRAINT IF EXISTS chk_daily_messages_input_tokens_positive"
        )
        op.execute(
            "ALTER TABLE daily_messages DROP CONSTRAINT IF EXISTS chk_daily_messages_tokens_positive"
        )

        op.execute(
            "ALTER TABLE quota_usage DROP CONSTRAINT IF EXISTS chk_quota_usage_requests_positive"
        )
        op.execute(
            "ALTER TABLE quota_usage DROP CONSTRAINT IF EXISTS chk_quota_usage_tokens_positive"
        )

        op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS chk_tenants_plan")
        op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS chk_tenants_status")

        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_role")
    else:
        # SQLite: recreate tables without CHECK constraints
        # This is complex, so we'll provide simplified version
        # Recreate users without CHECK (but keep foreign key)
        op.execute(
            """
            CREATE TABLE users_old (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                daily_token_quota INTEGER,
                monthly_token_quota INTEGER,
                daily_request_quota INTEGER,
                monthly_request_quota INTEGER,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL
            )
        """
        )
        op.execute(
            """
            INSERT INTO users_old
            SELECT id, username, password_hash, email, role, is_active, created_at, last_login,
                   daily_token_quota, monthly_token_quota, daily_request_quota, monthly_token_quota, tenant_id
            FROM users
        """
        )
        op.execute("CREATE INDEX idx_users_role ON users_old(role)")
        op.execute("CREATE INDEX idx_users_email ON users_old(email)")
        op.execute("CREATE INDEX idx_users_active ON users_old(is_active)")
        op.execute("CREATE INDEX idx_users_tenant ON users_old(tenant_id)")
        op.drop_table("users")
        op.rename_table("users_old", "users")

        # Recreate tenants without CHECK
        op.execute(
            """
            CREATE TABLE tenants_old (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'active',
                plan TEXT DEFAULT 'standard',
                contact_email TEXT,
                contact_phone TEXT,
                contact_name TEXT,
                quota TEXT,
                settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trial_ends_at TIMESTAMP,
                subscription_ends_at TIMESTAMP,
                user_count INTEGER DEFAULT 0,
                total_tokens_used INTEGER DEFAULT 0,
                total_requests_made INTEGER DEFAULT 0
            )
        """
        )
        op.execute(
            """
            INSERT INTO tenants_old
            SELECT id, name, slug, status, plan, contact_email, contact_phone, contact_name,
                   quota, settings, created_at, updated_at, trial_ends_at, subscription_ends_at,
                   user_count, total_tokens_used, total_requests_made
            FROM tenants
        """
        )
        op.execute("CREATE INDEX idx_tenants_slug ON tenants_old(slug)")
        op.execute("CREATE INDEX idx_tenants_status ON tenants_old(status)")
        op.drop_table("tenants")
        op.rename_table("tenants_old", "tenants")

        # Recreate quota_usage without CHECK (but keep foreign key)
        op.execute(
            """
            CREATE TABLE quota_usage_old (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                period TEXT DEFAULT 'daily',
                tokens_used INTEGER DEFAULT 0,
                requests_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date, period)
            )
        """
        )
        op.execute(
            """
            INSERT INTO quota_usage_old
            SELECT id, user_id, date, period, tokens_used, requests_used, created_at, updated_at
            FROM quota_usage
        """
        )
        op.execute("CREATE INDEX idx_quota_usage_user ON quota_usage_old(user_id)")
        op.execute("CREATE INDEX idx_quota_usage_date ON quota_usage_old(date)")
        op.drop_table("quota_usage")
        op.rename_table("quota_usage_old", "quota_usage")
