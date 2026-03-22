"""Add security settings table

Revision ID: 009_add_security_settings_table
Revises: 008_add_soft_delete
Create Date: 2026-03-22

This migration creates a security_settings table to store security configuration
in the database instead of JSON file, enabling:
- Audit trail for configuration changes
- Multi-instance deployment support
- Complete backup coverage

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '009_add_security_settings_table'
down_revision: Union[str, None] = '008_add_soft_delete'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Create security_settings table
    op.create_table(
        'security_settings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('setting_key', sa.String(100), nullable=False, unique=True),
        sa.Column('setting_value', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_security_settings_key', 'security_settings', ['setting_key'])

    # Insert default security settings
    op.execute("""
        INSERT INTO security_settings (setting_key, setting_value, description) VALUES
        ('session_timeout', '30', 'Session timeout in minutes'),
        ('max_login_attempts', '5', 'Maximum failed login attempts before lockout'),
        ('password_min_length', '8', 'Minimum password length'),
        ('password_require_uppercase', 'true', 'Require uppercase letter in password'),
        ('password_require_lowercase', 'true', 'Require lowercase letter in password'),
        ('password_require_number', 'true', 'Require number in password'),
        ('password_require_special', 'false', 'Require special character in password'),
        ('two_factor_enabled', 'false', 'Enable two-factor authentication'),
        ('ip_whitelist', '[]', 'JSON array of allowed IP addresses')
    """)


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index('idx_security_settings_key', 'security_settings')
    op.drop_table('security_settings')