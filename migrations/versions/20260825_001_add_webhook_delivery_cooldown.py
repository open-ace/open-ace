"""Add webhook delivery cooldown coordination columns.

Revision ID: 20260825_001
Revises: 20260824_001
Create Date: 2026-08-25

Issue: #2063
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_001"
down_revision: str | None = "20260824_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    ("receiver_identity_hash", sa.String(length=64)),
    ("cooldown_key", sa.String(length=64)),
    ("cooldown_expires_at", sa.DateTime()),
    ("delivery_claim_token", sa.String(length=64)),
    ("delivery_claim_expires_at", sa.DateTime()),
)

_INDEXES = (
    (
        "idx_webhook_deliveries_cooldown_active",
        ["cooldown_key", "status", "cooldown_expires_at"],
    ),
    ("idx_webhook_deliveries_cooldown_expiry", ["cooldown_expires_at"]),
    ("idx_webhook_deliveries_receiver_identity", ["receiver_identity_hash"]),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "webhook_deliveries" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("webhook_deliveries")}
    for column_name, column_type in _COLUMNS:
        if column_name not in existing_columns:
            op.add_column(
                "webhook_deliveries",
                sa.Column(column_name, column_type, nullable=True),
            )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("webhook_deliveries")}
    for index_name, columns in _INDEXES:
        if index_name not in existing_indexes:
            op.create_index(index_name, "webhook_deliveries", columns)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "webhook_deliveries" not in set(inspector.get_table_names()):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("webhook_deliveries")}
    for index_name, _columns in reversed(_INDEXES):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="webhook_deliveries")

    existing_columns = {col["name"] for col in inspector.get_columns("webhook_deliveries")}
    with op.batch_alter_table("webhook_deliveries") as batch_op:
        for column_name, _column_type in reversed(_COLUMNS):
            if column_name in existing_columns:
                batch_op.drop_column(column_name)
