"""
Add tenant usage aggregation infrastructure

Revision ID: 20260709_003_add_tenant_usage_aggregation
Revises: 20260709_001_add_base_commit_sha
Create Date: 2026-07-09

This migration adds the infrastructure for tenant usage aggregation including:
- aggregation_history table for tracking aggregation runs
- tenant_period_history table for archiving period usage
- tenant_plans table for plan definitions
- alerts_history table for alert tracking
- consistency_violations table for tracking data consistency issues
- New fields on tenants table for billing cycle management
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '20260709_003_add_tenant_usage_aggregation'
down_revision = '20260709_001_add_base_commit_sha'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply migration."""
    # Get database connection
    conn = op.get_bind()

    # Check if we're using PostgreSQL
    is_postgres = bool(conn.dialect.name == 'postgresql')

    # 1. Create aggregation_history table
    op.create_table(
        'aggregation_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('type', sa.String(50), nullable=False, comment='Aggregation type'),
        sa.Column('start_date', sa.Date(), nullable=False, comment='Start date of aggregation'),
        sa.Column('end_date', sa.Date(), nullable=False, comment='End date of aggregation'),
        sa.Column('status', sa.String(20), nullable=False, default='pending',
                  comment='Status: pending, running, completed, failed'),
        sa.Column('records_count', sa.Integer(), default=0, comment='Number of records processed'),
        sa.Column('quality_report', sa.Text(), nullable=True, comment='Data quality report in JSON'),
        sa.Column('error_message', sa.Text(), nullable=True, comment='Error message if failed'),
        sa.Column('started_at', sa.DateTime(), nullable=True, comment='Start timestamp'),
        sa.Column('completed_at', sa.DateTime(), nullable=True, comment='Completion timestamp'),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow, comment='Creation timestamp'),
    )

    # Create indexes for aggregation_history
    op.create_index('idx_aggregation_history_type_date', 'aggregation_history', ['type', 'start_date', 'end_date'])
    op.create_index('idx_aggregation_history_status', 'aggregation_history', ['status'])

    # 2. Create tenant_period_history table
    op.create_table(
        'tenant_period_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False, comment='Period start date'),
        sa.Column('period_end', sa.Date(), nullable=False, comment='Period end date'),
        sa.Column('tokens_used', sa.BigInteger(), default=0, comment='Total tokens used in period'),
        sa.Column('requests_made', sa.BigInteger(), default=0, comment='Total requests made in period'),
        sa.Column('reset_at', sa.DateTime(), nullable=False, comment='Reset timestamp'),
        sa.Column('reset_by', sa.String(100), nullable=True, comment='User who triggered reset'),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow),
    )

    # Create indexes for tenant_period_history
    op.create_index('idx_tenant_period_history_tenant', 'tenant_period_history', ['tenant_id'])
    op.create_index('idx_tenant_period_history_dates', 'tenant_period_history', ['period_start', 'period_end'])

    # 3. Create tenant_plans table
    op.create_table(
        'tenant_plans',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, comment='Plan name'),
        sa.Column('slug', sa.String(50), nullable=False, unique=True, comment='Plan slug'),
        sa.Column('quota_defaults', sa.Text() if not is_postgres else JSONB(),
                  nullable=True, comment='Default quota configuration (JSON)'),
        sa.Column('price_monthly', sa.Numeric(10, 2), default=0, comment='Monthly price'),
        sa.Column('price_quarterly', sa.Numeric(10, 2), default=0, comment='Quarterly price'),
        sa.Column('price_yearly', sa.Numeric(10, 2), default=0, comment='Yearly price'),
        sa.Column('features', sa.Text() if not is_postgres else JSONB(),
                  nullable=True, comment='Plan features (JSON)'),
        sa.Column('is_active', sa.Boolean(), default=True, comment='Whether plan is active'),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow),
    )

    # Create indexes for tenant_plans
    op.create_index('idx_tenant_plans_slug', 'tenant_plans', ['slug'])
    op.create_index('idx_tenant_plans_active', 'tenant_plans', ['is_active'])

    # 4. Create alerts_history table
    op.create_table(
        'alerts_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('alert_type', sa.String(50), nullable=False, comment='Alert type'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=True, comment='Tenant ID if tenant-specific'),
        sa.Column('severity', sa.String(20), default='warning', comment='Alert severity: info, warning, critical'),
        sa.Column('message', sa.Text(), nullable=False, comment='Alert message'),
        sa.Column('details', sa.Text() if not is_postgres else JSONB(),
                  nullable=True, comment='Additional details (JSON)'),
        sa.Column('recipients', sa.Text(), nullable=True, comment='Alert recipients'),
        sa.Column('channels', sa.String(100), nullable=True, comment='Notification channels used'),
        sa.Column('status', sa.String(20), default='sent', comment='Alert status: sent, failed'),
        sa.Column('sent_at', sa.DateTime(), default=datetime.utcnow, comment='Sent timestamp'),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow),
    )

    # Create indexes for alerts_history
    op.create_index('idx_alerts_history_type', 'alerts_history', ['alert_type'])
    op.create_index('idx_alerts_history_tenant', 'alerts_history', ['tenant_id'])
    op.create_index('idx_alerts_history_sent_at', 'alerts_history', ['sent_at'])

    # 5. Create consistency_violations table
    op.create_table(
        'consistency_violations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=True, comment='Tenant ID'),
        sa.Column('violation_type', sa.String(50), nullable=False, comment='Violation type'),
        sa.Column('expected_value', sa.BigInteger(), nullable=True, comment='Expected value'),
        sa.Column('actual_value', sa.BigInteger(), nullable=True, comment='Actual value'),
        sa.Column('difference', sa.BigInteger(), nullable=True, comment='Difference'),
        sa.Column('details', sa.Text(), nullable=True, comment='Additional details'),
        sa.Column('status', sa.String(20), default='detected',
                  comment='Status: detected, repaired, ignored'),
        sa.Column('detected_at', sa.DateTime(), default=datetime.utcnow, comment='Detection timestamp'),
        sa.Column('repaired_at', sa.DateTime(), nullable=True, comment='Repair timestamp'),
        sa.Column('created_at', sa.DateTime(), default=datetime.utcnow),
    )

    # Create indexes for consistency_violations
    op.create_index('idx_consistency_violations_tenant', 'consistency_violations', ['tenant_id'])
    op.create_index('idx_consistency_violations_status', 'consistency_violations', ['status'])
    op.create_index('idx_consistency_violations_detected', 'consistency_violations', ['detected_at'])

    # 6. Add new fields to tenants table
    # Note: SQLite doesn't support COMMENT, so we use comment parameter only for PostgreSQL

    # Helper function to add column if not exists
    def add_column_if_not_exists(table_name, column_name, column_obj):
        """Add column to table if it doesn't already exist."""
        try:
            op.add_column(table_name, column_obj)
            print(f"  Added column: {column_name}")
        except Exception as e:
            if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                print(f"  Column already exists: {column_name}")
            else:
                raise

    # billing_day: 1-31
    add_column_if_not_exists('tenants', 'billing_day',
        sa.Column('billing_day', sa.Integer(), default=1, comment='Billing day of month (1-31)'))

    # billing_cycle_type: monthly, quarterly, yearly
    add_column_if_not_exists('tenants', 'billing_cycle_type',
        sa.Column('billing_cycle_type', sa.String(20), default='monthly', comment='Billing cycle type'))

    # billing_cycle_start: Current billing cycle start date
    add_column_if_not_exists('tenants', 'billing_cycle_start',
        sa.Column('billing_cycle_start', sa.Date(), nullable=True, comment='Current billing cycle start date'))

    # billing_cycle_end: Current billing cycle end date
    add_column_if_not_exists('tenants', 'billing_cycle_end',
        sa.Column('billing_cycle_end', sa.Date(), nullable=True, comment='Current billing cycle end date'))

    # current_cycle_tokens: Tokens used in current billing cycle
    add_column_if_not_exists('tenants', 'current_cycle_tokens',
        sa.Column('current_cycle_tokens', sa.BigInteger(), default=0, comment='Tokens used in current billing cycle'))

    # over_limit_strategy: soft, hard, billable
    add_column_if_not_exists('tenants', 'over_limit_strategy',
        sa.Column('over_limit_strategy', sa.String(20), default='soft', comment='Over limit strategy: soft, hard, billable'))

    # over_limit_price_per_token: Price per token when over limit
    add_column_if_not_exists('tenants', 'over_limit_price_per_token',
        sa.Column('over_limit_price_per_token', sa.Numeric(10, 6), nullable=True, comment='Price per token when over limit'))

    # usage_alert_threshold: Alert threshold percentage (default 80%)
    add_column_if_not_exists('tenants', 'usage_alert_threshold',
        sa.Column('usage_alert_threshold', sa.Integer(), default=80, comment='Usage alert threshold percentage'))

    # usage_critical_threshold: Critical threshold percentage (default 95%)
    add_column_if_not_exists('tenants', 'usage_critical_threshold',
        sa.Column('usage_critical_threshold', sa.Integer(), default=95, comment='Usage critical threshold percentage'))

    # alert_silence_hours: Alert silence period in hours (default 24)
    add_column_if_not_exists('tenants', 'alert_silence_hours',
        sa.Column('alert_silence_hours', sa.Integer(), default=24, comment='Alert silence period in hours'))

    # Create indexes for new tenants fields
    op.create_index('idx_tenants_billing_cycle', 'tenants', ['billing_cycle_end'])

    print("Migration 20260709_002_add_tenant_usage_aggregation completed successfully")


def downgrade() -> None:
    """Revert migration."""
    # Get database connection
    conn = op.get_bind()

    # Check if we're using PostgreSQL
    is_postgres = bool(conn.dialect.name == 'postgresql')

    # Drop indexes
    op.drop_index('idx_tenants_billing_cycle', 'tenants')
    op.drop_index('idx_consistency_violations_detected', 'consistency_violations')
    op.drop_index('idx_consistency_violations_status', 'consistency_violations')
    op.drop_index('idx_consistency_violations_tenant', 'consistency_violations')
    op.drop_index('idx_alerts_history_sent_at', 'alerts_history')
    op.drop_index('idx_alerts_history_tenant', 'alerts_history')
    op.drop_index('idx_alerts_history_type', 'alerts_history')
    op.drop_index('idx_tenant_plans_active', 'tenant_plans')
    op.drop_index('idx_tenant_plans_slug', 'tenant_plans')
    op.drop_index('idx_tenant_period_history_dates', 'tenant_period_history')
    op.drop_index('idx_tenant_period_history_tenant', 'tenant_period_history')
    op.drop_index('idx_aggregation_history_status', 'aggregation_history')
    op.drop_index('idx_aggregation_history_type_date', 'aggregation_history')

    # Drop columns from tenants table (only for PostgreSQL)
    # SQLite doesn't support DROP COLUMN, so we skip this step for SQLite
    # In production, this means SQLite databases will have extra columns, but they won't be used
    if is_postgres:
        op.drop_column('tenants', 'alert_silence_hours')
        op.drop_column('tenants', 'usage_critical_threshold')
        op.drop_column('tenants', 'usage_alert_threshold')
        op.drop_column('tenants', 'over_limit_price_per_token')
        op.drop_column('tenants', 'over_limit_strategy')
        op.drop_column('tenants', 'current_cycle_tokens')
        op.drop_column('tenants', 'billing_cycle_end')
        op.drop_column('tenants', 'billing_cycle_start')
        op.drop_column('tenants', 'billing_cycle_type')
        op.drop_column('tenants', 'billing_day')

    # Drop tables
    op.drop_table('consistency_violations')
    op.drop_table('alerts_history')
    op.drop_table('tenant_plans')
    op.drop_table('tenant_period_history')
    op.drop_table('aggregation_history')

    print("Migration 20260709_002_add_tenant_usage_aggregation reverted successfully")