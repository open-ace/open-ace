"""Add audit anomaly detection threshold defaults to security_settings

Revision ID: 041_add_audit_threshold_defaults
Revises: 040_api_key_bool
Create Date: 2026-05-08

Adds configurable audit anomaly detection thresholds to the security_settings
table, replacing hardcoded constants in AuditAnalyzer.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "041_add_audit_threshold_defaults"
down_revision: Union[str, None] = "040_api_key_bool"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

THRESHOLDS = [
    ("audit_failed_login_threshold", "5", "Failed login count before anomaly alert"),
    ("audit_rapid_action_threshold", "50", "Actions per hour before rapid activity alert"),
    ("audit_off_hours_threshold", "10", "Off-hours actions before anomaly alert"),
    ("audit_role_change_threshold", "5", "Role changes before frequent change alert"),
    ("audit_permission_change_threshold", "10", "Permission changes before anomaly alert"),
]


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    for key, value, description in THRESHOLDS:
        if dialect == "postgresql":
            conn.execute(
                sa.text(
                    "INSERT INTO security_settings (setting_key, setting_value, description, updated_at) "
                    "VALUES (:key, :val, :desc, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (setting_key) DO NOTHING"
                ),
                {"key": key, "val": value, "desc": description},
            )
        else:
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO security_settings (setting_key, setting_value, description) "
                    "VALUES (:key, :val, :desc)"
                ),
                {"key": key, "val": value, "desc": description},
            )


def downgrade() -> None:
    conn = op.get_bind()
    keys = [t[0] for t in THRESHOLDS]
    placeholders = ", ".join([f":k{i}" for i in range(len(keys))])
    params = {f"k{i}": k for i, k in enumerate(keys)}
    conn.execute(
        sa.text(f"DELETE FROM security_settings WHERE setting_key IN ({placeholders})"),
        params,
    )
